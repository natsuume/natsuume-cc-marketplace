"""pre-merge-codex-review の merge gate 契約テスト (Phase A: spec-first, red)。

`GATE` (`plugins/pre-merge-codex-review/hooks/scripts/block-pre-merge.sh`) は
Phase A 時点でまだ存在しない。すべてのテストは、この gate script が実装される
までは意図した失敗 (assert failure、または gate 不在に由来する応答不一致) で
red になる。

固定する契約:

- gate は `gh pr merge` を検出した Bash コマンドに対する PreToolUse hook である。
- marker (`.claude-pre-merge-codex-reviewed`) は改行区切りの key=value で 5 key を
  束縛する: `repo` (owner/name)・`pr` (PR 番号)・`merge_base` (base branch との
  merge-base commit OID)・`head` (レビュー時の PR head commit OID)・`diff_hash`
  (merge-base..head 全差分の sha256。計算式は
  `plugins/pre-push-review/hooks/scripts/lib/diff-hash.sh` の
  `compute_review_hash_in` と同一)。
- marker が無い、または 5 key のいずれかが実 PR metadata・現在のローカル HEAD と
  一致しなければ gate は deny する。
- `--auto` を含む `gh pr merge` は marker の状態に関わらず常に deny する。
- 5 key すべてが実 PR metadata・現在のローカル HEAD と一致する場合のみ gate は
  allow し、merge コマンドへ `--match-head-commit <レビュー済み head OID>` を
  自動付与した `updatedInput` を返す。

gate は実の GitHub API を叩けないため、テストは一時ディレクトリに fake `gh`
実行ファイルを作り PATH の先頭に置く。fake `gh` は呼び出し引数の形に依らず、
テスト側で一時 repo の実状態から生成した固定の PR metadata JSON を返す寛容な
実装であり、gate がどの `gh` 呼び出し形 (`gh pr view` / `gh api` 等) を採るかは
検証しない (検証するのは「実 PR metadata と照合すること」という契約のみ)。
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = (
    ROOT
    / "plugins"
    / "pre-merge-codex-review"
    / "hooks"
    / "scripts"
    / "block-pre-merge.sh"
)

MARKER_NAME = ".claude-pre-merge-codex-reviewed"
CODEX_SUBAGENT_NAME = "pre-merge-codex-review:codex-reviewer"

PR_NUMBER = 123
REPO_NAME_WITH_OWNER = "test-owner/test-repo"

MERGE_COMMAND = f"gh pr merge {PR_NUMBER} --merge"
MERGE_AUTO_COMMAND = f"gh pr merge {PR_NUMBER} --auto --merge"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _git_dir(work: Path) -> Path:
    value = subprocess.check_output(
        ["git", "rev-parse", "--absolute-git-dir"], cwd=work
    )
    return Path(value.decode().strip())


def _rev_parse(work: Path, rev: str) -> str:
    return (
        subprocess.check_output(["git", "rev-parse", rev], cwd=work)
        .decode()
        .strip()
    )


def _create_feature_repository(temporary: Path) -> Path:
    """merge 対象 PR の head 相当のブランチを持つ一時 repo を作る。

    bare origin を用意し、master へ base commit を push、origin/HEAD を設定した
    うえで feature branch に変更 commit を積んで push する (= すでに remote に
    到達した「レビュー対象 PR」を模す)。
    """
    origin = temporary / "origin.git"
    work = temporary / "work"
    _git(temporary, "init", "--bare", str(origin))
    _git(temporary, "init", str(work))
    _git(work, "config", "user.name", "Marketplace Test")
    _git(work, "config", "user.email", "marketplace@example.invalid")
    (work / "example.txt").write_text("base\n", encoding="utf-8")
    _git(work, "add", "example.txt")
    _git(work, "commit", "-m", "base")
    _git(work, "branch", "-M", "master")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-u", "origin", "master")
    _git(work, "remote", "set-head", "origin", "master")
    _git(work, "checkout", "-b", "feature/test")
    (work / "example.txt").write_text("changed\n", encoding="utf-8")
    _git(work, "add", "example.txt")
    _git(work, "commit", "-m", "change")
    _git(work, "push", "-u", "origin", "feature/test")
    return work


def _expected_review_hash(work: Path) -> str:
    """`compute_review_hash_in` (base=master, target_cwd=work) と同じ計算式を
    独立に再実装する。working tree は常に clean な状態で呼ぶため staged /
    unstaged 差分は空になり、実質 `head` / `mbase` 束縛行 + merge-base..HEAD の
    全差分がハッシュ入力になる。
    """
    head = _rev_parse(work, "HEAD^{commit}")
    merge_base = (
        subprocess.check_output(
            ["git", "merge-base", "origin/master", "HEAD"], cwd=work
        )
        .decode()
        .strip()
    )
    chunks = [
        f"head {head}\n".encode(),
        f"mbase {merge_base}\n".encode(),
    ]
    for args in (
        ("diff", "--no-ext-diff", "--no-textconv", merge_base, "HEAD"),
        ("diff", "--no-ext-diff", "--no-textconv", "--cached"),
        ("diff", "--no-ext-diff", "--no-textconv"),
    ):
        chunks.append(subprocess.check_output(["git", *args], cwd=work).rstrip(b"\n"))
    return hashlib.sha256(b"".join(chunks)).hexdigest()


def _write_fake_gh(bin_dir: Path, *, pr_number: int, head_oid: str, repo: str) -> Path:
    """gh の実 API を叩けない環境向けの fake 実行ファイルを作る。

    gate がどの gh 呼び出し形を採るかは Phase B の実装裁量のため、本 fake は
    引数を問わず同じ固定 PR metadata JSON を返す寛容な実装にする。呼び出し
    引数は診断用にログへ記録するのみで、テストの assertion はログ内容に
    依存しない。
    """
    gh_path = bin_dir / "gh"
    log_path = bin_dir / "gh-calls.log"
    payload = json.dumps(
        {
            "number": pr_number,
            "headRefOid": head_oid,
            "headRefName": "feature/test",
            "baseRefName": "master",
            "state": "OPEN",
            "isDraft": False,
            "url": f"https://github.com/{repo}/pull/{pr_number}",
            "nameWithOwner": repo,
            "headRepository": {"nameWithOwner": repo},
            "baseRepository": {"nameWithOwner": repo},
        },
        ensure_ascii=False,
    )
    script_lines = [
        "#!/bin/bash",
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(log_path))}",
        "cat <<'GH_FAKE_JSON'",
        payload,
        "GH_FAKE_JSON",
        "",
    ]
    gh_path.write_text("\n".join(script_lines), encoding="utf-8")
    mode = gh_path.stat().st_mode
    gh_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return gh_path


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("git") and shutil.which("jq"),
    "gate integration requires bash, git, and jq",
)
class PreMergeCodexGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary_path = Path(self.temporary.name)
        self.work = _create_feature_repository(temporary_path)

        self.head_oid = _rev_parse(self.work, "HEAD^{commit}")
        self.base_oid = _rev_parse(self.work, "origin/master^{commit}")
        self.merge_base_oid = (
            subprocess.check_output(
                ["git", "merge-base", "origin/master", "HEAD"], cwd=self.work
            )
            .decode()
            .strip()
        )
        self.diff_hash = _expected_review_hash(self.work)

        self.fake_bin_dir = temporary_path / "fake-bin"
        self.fake_bin_dir.mkdir()
        _write_fake_gh(
            self.fake_bin_dir,
            pr_number=PR_NUMBER,
            head_oid=self.head_oid,
            repo=REPO_NAME_WITH_OWNER,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

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

    def updated_command(self, result: subprocess.CompletedProcess[bytes]) -> str:
        response = json.loads(result.stdout)
        return response["hookSpecificOutput"]["updatedInput"]["command"]

    def write_marker(self, content: str) -> None:
        (_git_dir(self.work) / MARKER_NAME).write_text(content, encoding="utf-8")

    def build_marker(
        self,
        *,
        repo: str = REPO_NAME_WITH_OWNER,
        pr: int = PR_NUMBER,
        merge_base: str | None = None,
        head: str | None = None,
        diff_hash: str | None = None,
    ) -> str:
        if merge_base is None:
            merge_base = self.merge_base_oid
        if head is None:
            head = self.head_oid
        if diff_hash is None:
            diff_hash = self.diff_hash
        return (
            f"repo={repo}\n"
            f"pr={pr}\n"
            f"merge_base={merge_base}\n"
            f"head={head}\n"
            f"diff_hash={diff_hash}\n"
        )

    # ------------------------------------------------------------------
    # (a) gate script の存在
    # ------------------------------------------------------------------

    def test_gate_script_exists(self) -> None:
        self.assertTrue(GATE.is_file(), f"missing gate script: {GATE}")

    # ------------------------------------------------------------------
    # (b) marker 無しで deny。deny 文は subagent namespace と codex への言及を持つ
    # ------------------------------------------------------------------

    def test_denies_merge_without_marker(self) -> None:
        result = self.run_gate(MERGE_COMMAND)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "deny")

        reason = self.deny_reason(result)
        self.assertIn(CODEX_SUBAGENT_NAME, reason)
        self.assertIn("codex", reason.lower())

    # ------------------------------------------------------------------
    # (c) --auto は marker の有無に依らず常に deny
    # ------------------------------------------------------------------

    def test_denies_auto_merge_regardless_of_marker(self) -> None:
        with self.subTest(marker="absent"):
            result = self.run_gate(MERGE_AUTO_COMMAND)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(self.decision(result), "deny")

        with self.subTest(marker="present_and_valid"):
            self.write_marker(self.build_marker())
            result = self.run_gate(MERGE_AUTO_COMMAND)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(
                self.decision(result),
                "deny",
                "--auto はサポート外のため有効な marker があっても deny する契約",
            )

    # ------------------------------------------------------------------
    # (d) 正しい 5 key marker + PR metadata 一致 → allow + --match-head-commit 付与
    # ------------------------------------------------------------------

    def test_allows_merge_with_valid_marker_and_injects_match_head_commit(
        self,
    ) -> None:
        self.write_marker(self.build_marker())

        result = self.run_gate(MERGE_COMMAND)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "allow")

        updated = self.updated_command(result)
        self.assertIn(f"--match-head-commit {self.head_oid}", updated)

    # ------------------------------------------------------------------
    # (e) marker の pr / head が実状態と食い違う場合は deny (2 ケース)
    # ------------------------------------------------------------------

    def test_denies_merge_when_marker_binds_wrong_pr_or_head(self) -> None:
        cases = {
            "pr_mismatch": self.build_marker(pr=PR_NUMBER + 1),
            # head_mismatch: 実在するが現在の branch HEAD とは異なる commit
            # (base commit の OID) を束縛する。
            "head_mismatch": self.build_marker(head=self.base_oid),
        }
        for label, marker in cases.items():
            with self.subTest(case=label):
                self.write_marker(marker)
                result = self.run_gate(MERGE_COMMAND)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.decision(result), "deny", f"case={label}")


if __name__ == "__main__":
    unittest.main()
