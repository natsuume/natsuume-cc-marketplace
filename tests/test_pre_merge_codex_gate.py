"""pre-merge-codex-review の軽量 merge gate 契約テスト (Phase A: spec-first, red)。

gate (`plugins/pre-merge-codex-review/hooks/scripts/block-pre-merge.sh`) は
Phase A 時点でまだ存在しない。全テストは gate が実装されるまで意図した失敗で
red になる。

固定する契約:

- gate は Bash コマンド文字列に `gh pr merge` の連続列を含む場合のみ関与する。
  含まないコマンドには関与しない (無出力)。連続列の検出は粗い文字列判定で
  よく、フラグ文法の解析や invocation の厳密な分類は行わない (quoted な言及
  等で誤爆した場合はコマンドの言い換えで回避できる、cooperative 利用前提)。
- 関与したコマンドに `--auto` または `--admin` の文字列を含む場合は deny する
  (遅延 merge 予約・保護 bypass はサポート外。粗い文字列検出でよく、
  レビューコメントの有無に依らない)。
- それ以外の関与コマンドでは、merge 対象 PR の現在の head SHA を gh で取得し、
  PR 上のレビューコメントに機械可読 header
  `<!-- codex-review: head=<full head SHA> status=pass|findings -->`
  を持ち、head SHA が現在の head と完全一致するものが存在するかを確認する:
  - 存在すれば無出力で終了する (gate は decision を出さず、既定の許可フロー
    に委ねる)
  - 存在しなければ deny し、`pre-merge-codex-review:codex-reviewer` subagent
    の実行を案内する
- status は pass / findings のどちらでも「レビュー済み」として成立する
  (merge の approve や findings 0 件の証明ではない)。
- 対象 PR の解決と head SHA の取得は gh に委ねる。gh / jq が見つからない・
  PR の解決や取得に失敗した・head SHA が得られない場合はすべて deny する
  (fail-closed)。merge と無関係な Bash 呼び出しには関与しない。
- gate は permissionDecision として deny 以外を出さない (allow / updatedInput
  を出さない。通過時は無出力で既定の許可フローに委ねる)。

テストは一時ディレクトリに fake `gh` を置き PATH の先頭に通す。fake は呼び出し
引数に依らず、設定された head SHA と PR レビュー一覧を含む JSON を返す寛容な
実装であり、gate がどの gh 呼び出し形を採るかは Phase B の実装裁量とする
(検証するのは「gh から得た実状態と照合する」という契約のみ)。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "plugins" / "pre-merge-codex-review" / "hooks" / "scripts"
GATE = SCRIPTS_DIR / "block-pre-merge.sh"
WRAPPER_GUARD = SCRIPTS_DIR / "block-bg-codex-wrapper.sh"

CODEX_SUBAGENT_NAME = "pre-merge-codex-review:codex-reviewer"

PR_NUMBER = 123
HEAD_SHA = "1234567890abcdef1234567890abcdef12345678"
OTHER_SHA = "fedcba0987654321fedcba0987654321fedcba09"

MERGE_COMMAND = f"gh pr merge {PR_NUMBER} --squash"

# 寛容 fake gh: 引数に依らず gh-config.json の payload (headRefOid + reviews) を
# 返す。graphql_fail 相当の失敗注入は fail フラグで行う。
FAKE_GH_SCRIPT = """#!/usr/bin/env python3
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "gh-config.json"), encoding="utf-8") as fp:
    config = json.load(fp)

with open(os.path.join(HERE, "gh-calls.log"), "a", encoding="utf-8") as fp:
    fp.write(json.dumps(sys.argv[1:]) + "\\n")

if config.get("fail"):
    print("fake-gh: injected failure", file=sys.stderr)
    sys.exit(1)

print(json.dumps(config["payload"]))
"""


def codex_review_comment(head_sha: str, status: str = "pass") -> str:
    """wrapper が投稿する PR レビュー本文の形 (機械可読 header + report)。"""
    return (
        f"<!-- codex-review: head={head_sha} status={status} -->\n"
        "# Codex Review\n\nStatus: "
        f"{status}\n"
    )


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("jq"),
    "gate integration requires bash and jq",
)
class PreMergeCodexGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary_path = Path(self.temporary.name)
        self.work = temporary_path / "work"
        self.work.mkdir()

        self.fake_bin_dir = temporary_path / "fake-bin"
        self.fake_bin_dir.mkdir()
        self.gh_log = self.fake_bin_dir / "gh-calls.log"
        gh_path = self.fake_bin_dir / "gh"
        gh_path.write_text(FAKE_GH_SCRIPT, encoding="utf-8")
        gh_path.chmod(
            gh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        self.configure_fake_gh(review_bodies=[])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def configure_fake_gh(
        self,
        *,
        review_bodies: list[str],
        head_oid: str | None = HEAD_SHA,
        fail: bool = False,
    ) -> None:
        payload: dict[str, object] = {
            "reviews": [{"body": body} for body in review_bodies],
        }
        if head_oid is not None:
            payload["headRefOid"] = head_oid
        config = {"payload": payload, "fail": fail}
        (self.fake_bin_dir / "gh-config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )

    def run_gate(self, command: str) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        env = os.environ.copy()
        env["PATH"] = f"{self.fake_bin_dir}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(
            ["bash", str(GATE)],
            cwd=self.work,
            input=json.dumps(payload).encode("utf-8"),
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def decision(self, result: subprocess.CompletedProcess[bytes]) -> str | None:
        if not result.stdout.strip():
            return None
        response = json.loads(result.stdout)
        return response["hookSpecificOutput"]["permissionDecision"]

    def deny_reason(self, result: subprocess.CompletedProcess[bytes]) -> str:
        response = json.loads(result.stdout)
        return response["hookSpecificOutput"]["permissionDecisionReason"]

    # ------------------------------------------------------------------
    # (a) hook script の存在 (Phase A 骨格契約)
    # ------------------------------------------------------------------

    def test_gate_script_exists(self) -> None:
        self.assertTrue(GATE.is_file(), f"missing gate script: {GATE}")

    def test_wrapper_guard_script_exists(self) -> None:
        self.assertTrue(
            WRAPPER_GUARD.is_file(), f"missing wrapper guard: {WRAPPER_GUARD}"
        )

    # ------------------------------------------------------------------
    # (b) 関与条件: `gh pr merge` の連続列を含む場合のみ
    # ------------------------------------------------------------------

    def test_ignores_commands_without_merge_sequence(self) -> None:
        commands = {
            "unrelated": "git status",
            "gh_readonly": "gh pr view 123 --json state",
            "merge_named_field": "gh pr list --json mergeable,mergeStateStatus",
        }
        for label, command in commands.items():
            with self.subTest(case=label):
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertIsNone(self.decision(result), f"case={label}")

    # ------------------------------------------------------------------
    # (c) --auto / --admin はレビューコメントの有無に依らず deny
    # ------------------------------------------------------------------

    def test_denies_auto_and_admin_regardless_of_review_comment(self) -> None:
        self.configure_fake_gh(review_bodies=[codex_review_comment(HEAD_SHA)])
        commands = {
            "auto": f"gh pr merge {PR_NUMBER} --auto --squash",
            "auto_equals": f"gh pr merge {PR_NUMBER} --auto=true --squash",
            "admin": f"gh pr merge {PR_NUMBER} --squash --admin",
        }
        for label, command in commands.items():
            with self.subTest(case=label):
                result = self.run_gate(command)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")

    # ------------------------------------------------------------------
    # (d) 現 head SHA に一致する codex-review コメントが無ければ deny。
    #     deny 文は subagent namespace と codex への言及を持つ
    # ------------------------------------------------------------------

    def test_denies_merge_without_matching_review_comment(self) -> None:
        cases = {
            "no_comments": [],
            "different_head": [codex_review_comment(OTHER_SHA)],
            "plain_comment_without_header": [
                "LGTM! (通常のレビューコメント。codex-review header なし)"
            ],
        }
        for label, review_bodies in cases.items():
            with self.subTest(case=label):
                self.configure_fake_gh(review_bodies=review_bodies)
                result = self.run_gate(MERGE_COMMAND)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")
                reason = self.deny_reason(result)
                self.assertIn(CODEX_SUBAGENT_NAME, reason)
                self.assertIn("codex", reason.lower())

    # ------------------------------------------------------------------
    # (e) 一致するコメントが在れば無出力 (既定の許可フローへ)。
    #     status は pass / findings のどちらでも成立する
    # ------------------------------------------------------------------

    def test_passes_through_merge_with_matching_review_comment(self) -> None:
        cases = {
            "status_pass": [codex_review_comment(HEAD_SHA, status="pass")],
            "status_findings": [
                codex_review_comment(HEAD_SHA, status="findings")
            ],
            "mixed_with_other_comments": [
                "先行する通常コメント",
                codex_review_comment(OTHER_SHA),
                codex_review_comment(HEAD_SHA),
            ],
        }
        for label, review_bodies in cases.items():
            with self.subTest(case=label):
                self.configure_fake_gh(review_bodies=review_bodies)
                result = self.run_gate(MERGE_COMMAND)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertIsNone(
                    self.decision(result),
                    f"一致コメントがあれば decision を出さない契約 (case={label})",
                )

    # ------------------------------------------------------------------
    # (f) fail-closed: gh の失敗・head SHA の欠落は deny
    # ------------------------------------------------------------------

    def test_denies_merge_when_gh_fails(self) -> None:
        self.configure_fake_gh(review_bodies=[], fail=True)
        result = self.run_gate(MERGE_COMMAND)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "deny")

    def test_denies_merge_when_head_sha_is_unavailable(self) -> None:
        self.configure_fake_gh(
            review_bodies=[codex_review_comment(HEAD_SHA)], head_oid=None
        )
        result = self.run_gate(MERGE_COMMAND)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "deny")


class PreMergeGateMissingDependencyTest(unittest.TestCase):
    """必須依存 (jq / gh) が見つからない環境では merge コマンドを fail-closed に
    deny し、無関係な Bash 呼び出しには関与しない契約。"""

    def _run_gate(
        self, path_value: str, command: str
    ) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        bash = shutil.which("bash")
        assert bash is not None
        env = os.environ.copy()
        env["PATH"] = path_value
        return subprocess.run(
            [bash, str(GATE)],
            input=json.dumps(payload).encode("utf-8"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
            env=env,
        )

    def _minimal_shims(self, work: Path) -> Path | None:
        cat = shutil.which("cat")
        dirname = shutil.which("dirname")
        if shutil.which("bash") is None or cat is None or dirname is None:
            return None
        shims = work / "bin"
        shims.mkdir()
        (shims / "cat").symlink_to(cat)
        (shims / "dirname").symlink_to(dirname)
        return shims

    def test_merge_is_denied_without_jq(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            shims = self._minimal_shims(Path(name))
            if shims is None:
                self.skipTest("requires bash, cat, and dirname")
            result = self._run_gate(str(shims), MERGE_COMMAND)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            response = json.loads(result.stdout)
            output = response["hookSpecificOutput"]
            self.assertEqual(output["permissionDecision"], "deny")
            self.assertIn("jq", output["permissionDecisionReason"])

    def test_unrelated_command_passes_without_jq(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            shims = self._minimal_shims(Path(name))
            if shims is None:
                self.skipTest("requires bash, cat, and dirname")
            result = self._run_gate(str(shims), "printf hello")
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout, b"")

    def test_merge_is_denied_without_gh(self) -> None:
        """gh 以外の全コマンドが見える PATH (symlink farm) でも、gh が無ければ
        merge は deny される。"""
        if not (
            shutil.which("bash") and shutil.which("git") and shutil.which("jq")
        ):
            self.skipTest("requires bash, git, and jq")
        with tempfile.TemporaryDirectory() as name:
            shims = Path(name) / "bin"
            shims.mkdir()
            seen: set[str] = set()
            for directory in os.environ.get("PATH", "").split(os.pathsep):
                candidate = Path(directory)
                if not candidate.is_dir():
                    continue
                for entry in candidate.iterdir():
                    if entry.name in seen or entry.name == "gh":
                        continue
                    try:
                        if entry.is_file() and os.access(entry, os.X_OK):
                            (shims / entry.name).symlink_to(entry)
                            seen.add(entry.name)
                    except OSError:
                        continue
            result = self._run_gate(str(shims), MERGE_COMMAND)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            response = json.loads(result.stdout)
            output = response["hookSpecificOutput"]
            self.assertEqual(output["permissionDecision"], "deny")
            self.assertIn("gh", output["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
