"""pre-merge-cross-review merge gate の Fable review 投稿契約テスト。

gate (`block-pre-merge.sh`) は codex review の判定・投稿・deny を従来どおり行い、codex 側の
通過が確定した後にだけ、ローカルの Fable review 記録を PR に投稿する。

- 記録: git-dir 直下の attestation (`.claude-pre-merge-fable-reviewed`、内容 `head=<SHA>`
  の 1 行) と投稿用本文 (`.claude-pre-merge-fable-comment.md`、先頭行が
  `<!-- fable-review: head=<SHA> status=pass|findings -->`)
- 投稿: `gh pr review <PR> --comment --body-file <本文>`。codex 側の投稿がある場合は
  codex → Fable の順
- PR に現在の head SHA の Fable review コメントが既にあれば投稿せず、記録を掃除する
- 記録が無い・attestation が symlink / head 不一致・本文 header 不一致の場合は投稿も削除も
  せず merge を通す
- Fable の投稿に失敗しても merge は通す (stdout 無出力)。記録は残し、stderr に手動投稿の
  手順を案内する
- codex 側で deny する経路 (記録なし・投稿失敗・Fable コメントのみ) では Fable を投稿しない

fake gh は `pr view` に設定 JSON を返し、`pr review` の呼び出しを calls.jsonl に記録する。
`--body-file` の basename に `fable` を含む呼び出しだけを失敗させる設定を持つ。
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


def _load_gate_tests():
    """codex 側の gate テストが持つ定数を読み込む。

    `import` 文で書くと「sys.path 操作より前に import 文が来る」という lint 制約
    (E402) に抵触するため、既存テストと同じ importlib 経由の明示 import にする。
    """
    return importlib.import_module("test_pre_merge_codex_gate")


_gate_tests = _load_gate_tests()

GATE = _gate_tests.GATE
PR_NUMBER = _gate_tests.PR_NUMBER
HEAD_SHA = _gate_tests.HEAD_SHA
OTHER_SHA = _gate_tests.OTHER_SHA
MERGE_COMMAND = _gate_tests.MERGE_COMMAND
codex_review_comment = _gate_tests.codex_review_comment

CODEX_FINAL = _gate_tests.FINAL_ATTESTATION
CODEX_PENDING = _gate_tests.PENDING_ATTESTATION
CODEX_BODY = _gate_tests.COMMENT_BODY
FABLE_FINAL = ".claude-pre-merge-fable-reviewed"
FABLE_BODY = ".claude-pre-merge-fable-comment.md"

NOTICE_PREFIX = "[pre-merge-cross-review]"

FAKE_GH_SCRIPT = """#!/usr/bin/env python3
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "gh-config.json"), encoding="utf-8") as fp:
    config = json.load(fp)

args = sys.argv[1:]
if args[:2] == ["pr", "view"]:
    print(json.dumps(config["payload"]))
    sys.exit(0)

if args[:2] == ["pr", "review"]:
    body_file = ""
    if "--body-file" in args and args.index("--body-file") + 1 < len(args):
        body_file = args[args.index("--body-file") + 1]
    body = ""
    if body_file and os.path.isfile(body_file):
        with open(body_file, encoding="utf-8") as fp:
            body = fp.read()
    with open(os.path.join(HERE, "calls.jsonl"), "a", encoding="utf-8") as fp:
        fp.write(json.dumps({"args": args, "body_file": body_file, "body": body}) + "\\n")
    if config.get("review_fail"):
        print("fake-gh: injected review failure", file=sys.stderr)
        sys.exit(1)
    if config.get("fable_review_fail") and "fable" in os.path.basename(body_file):
        print("fake-gh: injected fable review failure", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)

sys.exit(0)
"""


def fable_header(head: str, status: str = "pass") -> str:
    return f"<!-- fable-review: head={head} status={status} -->"


def fable_review_comment(head: str, status: str = "pass") -> str:
    return f"{fable_header(head, status)}\n# Fable Review\n\nStatus: {status}\n"


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
    "gate integration requires bash, jq, and git",
)
class PreMergeGateFablePostTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        temporary_path = Path(self.temporary.name)

        self.work = temporary_path / "work"
        self.work.mkdir()
        self.git("init")
        self.git("config", "user.name", "Marketplace Test")
        self.git("config", "user.email", "marketplace@example.invalid")
        (self.work / "example.txt").write_text("base\n", encoding="utf-8")
        self.git("add", "example.txt")
        self.git("commit", "-m", "base")
        self.git_dir = Path(
            subprocess.check_output(
                ["git", "rev-parse", "--absolute-git-dir"], cwd=self.work
            )
            .decode()
            .strip()
        )

        self.fake_bin_dir = temporary_path / "fake-bin"
        self.fake_bin_dir.mkdir()
        gh_path = self.fake_bin_dir / "gh"
        gh_path.write_text(FAKE_GH_SCRIPT, encoding="utf-8")
        gh_path.chmod(
            gh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        self.configure_fake_gh(review_bodies=[])

        isolated_tmp = temporary_path / "tmp"
        isolated_cache = temporary_path / "cache"
        isolated_tmp.mkdir()
        isolated_cache.mkdir()
        self.gate_env = os.environ.copy()
        self.gate_env["PATH"] = (
            f"{self.fake_bin_dir}{os.pathsep}{self.gate_env.get('PATH', '')}"
        )
        self.gate_env["TMPDIR"] = str(isolated_tmp)
        self.gate_env["XDG_CACHE_HOME"] = str(isolated_cache)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def git(self, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(self.work), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def configure_fake_gh(
        self,
        *,
        review_bodies: list[str],
        review_fail: bool = False,
        fable_review_fail: bool = False,
    ) -> None:
        config = {
            "payload": {
                "number": PR_NUMBER,
                "headRefOid": HEAD_SHA,
                "reviews": [{"body": body} for body in review_bodies],
            },
            "review_fail": review_fail,
            "fable_review_fail": fable_review_fail,
        }
        (self.fake_bin_dir / "gh-config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )

    def codex_final_path(self) -> Path:
        return self.git_dir / CODEX_FINAL

    def codex_pending_path(self) -> Path:
        return self.git_dir / CODEX_PENDING

    def codex_body_path(self) -> Path:
        return self.git_dir / CODEX_BODY

    def fable_final_path(self) -> Path:
        return self.git_dir / FABLE_FINAL

    def fable_body_path(self) -> Path:
        return self.git_dir / FABLE_BODY

    def write_codex_record(self) -> None:
        self.codex_final_path().write_text(
            f"pr={PR_NUMBER}\nhead={HEAD_SHA}\n", encoding="utf-8"
        )
        self.codex_body_path().write_text(
            codex_review_comment(HEAD_SHA), encoding="utf-8"
        )

    def write_fable_record(
        self,
        *,
        head: str = HEAD_SHA,
        body_first_line: str | None = None,
    ) -> None:
        self.fable_final_path().write_text(f"head={head}\n", encoding="utf-8")
        first_line = (
            body_first_line if body_first_line is not None else fable_header(head)
        )
        self.fable_body_path().write_text(
            f"{first_line}\n# Fable Review\n\nStatus: pass\n", encoding="utf-8"
        )

    def fable_record_snapshot(self) -> dict[str, bytes | None]:
        return {
            path.name: path.read_bytes() if os.path.lexists(path) else None
            for path in (self.fable_final_path(), self.fable_body_path())
        }

    def run_gate(self) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": MERGE_COMMAND},
            "cwd": str(self.work),
        }
        result = subprocess.run(
            ["bash", str(GATE)],
            cwd=self.work,
            input=json.dumps(payload).encode("utf-8"),
            env=self.gate_env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result

    def decision(self, result: subprocess.CompletedProcess[bytes]) -> str | None:
        if not result.stdout.strip():
            return None
        response = json.loads(result.stdout)
        return response["hookSpecificOutput"]["permissionDecision"]

    def review_calls(self) -> list[dict[str, object]]:
        log = self.fake_bin_dir / "calls.jsonl"
        if not log.exists():
            return []
        return [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def posted_kinds(self) -> list[str]:
        """投稿呼び出しを本文ファイルの種類 (codex / fable) の列で返す。"""
        kinds = []
        for call in self.review_calls():
            args = call["args"]
            assert isinstance(args, list)
            self.assertEqual(
                args[:4], ["pr", "review", str(PR_NUMBER), "--comment"], args
            )
            body_file = Path(str(call["body_file"]))
            if body_file == self.fable_body_path():
                kinds.append("fable")
            elif body_file == self.codex_body_path():
                kinds.append("codex")
            else:
                kinds.append(f"unknown:{body_file}")
        return kinds

    def assert_allowed_silently(
        self, result: subprocess.CompletedProcess[bytes]
    ) -> None:
        self.assertEqual(result.stdout, b"", result.stdout.decode())

    def assert_codex_record_cleaned(self) -> None:
        self.assertFalse(self.codex_final_path().exists())
        self.assertFalse(self.codex_body_path().exists())
        self.assertFalse(self.codex_pending_path().exists())

    def assert_fable_record_cleaned(self) -> None:
        self.assertFalse(os.path.lexists(self.fable_final_path()))
        self.assertFalse(os.path.lexists(self.fable_body_path()))

    # ------------------------------------------------------------------
    # 投稿経路
    # ------------------------------------------------------------------

    def test_posts_codex_then_fable_and_cleans_both_records(self) -> None:
        self.write_codex_record()
        self.write_fable_record()
        result = self.run_gate()
        self.assert_allowed_silently(result)
        self.assertEqual(self.posted_kinds(), ["codex", "fable"])
        fable_call = self.review_calls()[1]
        self.assertEqual(
            str(fable_call["body"]).split("\n", 1)[0], fable_header(HEAD_SHA)
        )
        self.assert_codex_record_cleaned()
        self.assert_fable_record_cleaned()

    def test_existing_codex_comment_posts_only_fable(self) -> None:
        self.configure_fake_gh(review_bodies=[codex_review_comment(HEAD_SHA)])
        self.write_fable_record()
        result = self.run_gate()
        self.assert_allowed_silently(result)
        self.assertEqual(self.posted_kinds(), ["fable"])
        self.assert_fable_record_cleaned()

    def test_existing_fable_comment_skips_posting_and_cleans_record(self) -> None:
        cases = {
            "codex_comment_exists": [
                codex_review_comment(HEAD_SHA),
                fable_review_comment(HEAD_SHA),
            ],
            "codex_record_posted": [fable_review_comment(HEAD_SHA)],
        }
        for label, review_bodies in cases.items():
            with self.subTest(case=label):
                log = self.fake_bin_dir / "calls.jsonl"
                log.unlink(missing_ok=True)
                self.configure_fake_gh(review_bodies=review_bodies)
                if label == "codex_record_posted":
                    self.write_codex_record()
                self.write_fable_record()
                result = self.run_gate()
                self.assert_allowed_silently(result)
                expected = ["codex"] if label == "codex_record_posted" else []
                self.assertEqual(self.posted_kinds(), expected)
                self.assert_fable_record_cleaned()

    def test_without_fable_record_keeps_codex_behavior(self) -> None:
        cases = {
            "codex_record_posted": [],
            "codex_comment_exists": [codex_review_comment(HEAD_SHA)],
        }
        for label, review_bodies in cases.items():
            with self.subTest(case=label):
                log = self.fake_bin_dir / "calls.jsonl"
                log.unlink(missing_ok=True)
                self.configure_fake_gh(review_bodies=review_bodies)
                if label == "codex_record_posted":
                    self.write_codex_record()
                result = self.run_gate()
                self.assert_allowed_silently(result)
                expected = ["codex"] if label == "codex_record_posted" else []
                self.assertEqual(self.posted_kinds(), expected)
                self.assertNotIn(NOTICE_PREFIX, result.stderr.decode())

    def test_fable_post_failure_allows_merge_and_keeps_record(self) -> None:
        self.configure_fake_gh(review_bodies=[], fable_review_fail=True)
        self.write_codex_record()
        self.write_fable_record()
        before = self.fable_record_snapshot()
        result = self.run_gate()
        self.assert_allowed_silently(result)
        self.assertEqual(self.posted_kinds(), ["codex", "fable"])
        self.assert_codex_record_cleaned()
        self.assertEqual(
            self.fable_record_snapshot(), before, "投稿に失敗した Fable 記録は残す"
        )
        stderr = result.stderr.decode()
        self.assertIn(NOTICE_PREFIX, stderr)
        self.assertIn(str(self.fable_body_path()), stderr)
        self.assertIn("gh pr review", stderr)
        self.assertIn("--body-file", stderr)

    # ------------------------------------------------------------------
    # 投稿しない経路 (merge は通し、Fable 記録は残す)
    # ------------------------------------------------------------------

    def test_invalid_fable_record_is_neither_posted_nor_removed(self) -> None:
        target = Path(self.temporary.name) / "fable-attestation-target"
        target.write_text(f"head={HEAD_SHA}\n", encoding="utf-8")
        cases = ("head_mismatch", "attestation_symlink", "body_header_mismatch",
                 "body_header_other_head")
        for label in cases:
            with self.subTest(case=label):
                log = self.fake_bin_dir / "calls.jsonl"
                log.unlink(missing_ok=True)
                for path in (self.fable_final_path(), self.fable_body_path()):
                    if os.path.lexists(path):
                        path.unlink()
                self.write_codex_record()
                if label == "head_mismatch":
                    self.write_fable_record(head=OTHER_SHA)
                elif label == "attestation_symlink":
                    self.write_fable_record()
                    self.fable_final_path().unlink()
                    self.fable_final_path().symlink_to(target)
                elif label == "body_header_mismatch":
                    self.write_fable_record(body_first_line="# Fable Review")
                else:
                    self.write_fable_record(
                        body_first_line=fable_header(OTHER_SHA)
                    )
                before = self.fable_record_snapshot()
                result = self.run_gate()
                self.assert_allowed_silently(result)
                self.assertEqual(self.posted_kinds(), ["codex"])
                self.assertEqual(self.fable_record_snapshot(), before)
                self.assertEqual(
                    target.read_text(encoding="utf-8"), f"head={HEAD_SHA}\n"
                )

    # ------------------------------------------------------------------
    # codex 側の deny 経路では Fable を投稿しない
    # ------------------------------------------------------------------

    def test_missing_codex_record_denies_without_posting_fable(self) -> None:
        self.write_fable_record()
        result = self.run_gate()
        self.assertEqual(self.decision(result), "deny")
        self.assertEqual(self.posted_kinds(), [])

    def test_codex_post_failure_denies_without_posting_fable(self) -> None:
        self.configure_fake_gh(review_bodies=[], review_fail=True)
        self.write_codex_record()
        self.write_fable_record()
        result = self.run_gate()
        self.assertEqual(self.decision(result), "deny")
        self.assertNotIn("fable", self.posted_kinds())

    def test_fable_comment_without_codex_review_denies(self) -> None:
        self.configure_fake_gh(review_bodies=[fable_review_comment(HEAD_SHA)])
        result = self.run_gate()
        self.assertEqual(self.decision(result), "deny")
        self.assertEqual(self.posted_kinds(), [])


if __name__ == "__main__":
    unittest.main()
