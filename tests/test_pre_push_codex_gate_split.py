"""codex review gate 分離の挙動契約テスト (issue #378 Phase A)。

pre-push-review core (3 マーカー gate) を「core (code + security の 2 マーカー)」と
「pre-push-codex-review (codex マーカー単独)」へ分離する契約を、実際の PreToolUse
hook 入出力で固定する。

- pre-push-codex-review の push gate (`block-pre-push-codex.sh`) は、codex マーカー
  (`.claude-pre-push-codex-reviewed`) が「commit 列 (HEAD / merge-base の OID) +
  ブランチ全差分 + 未コミット差分」のハッシュと一致しない限り `git push` を deny する。
  単独 install でも自立動作するため、push 検出・target 解決・dirty-tree gate 等の
  共通ロジックは pre-push-review core と独立に持つ。
- pre-push-review core (`block-pre-push.sh`) は Phase B で code-reviewed /
  security-reviewed の 2 マーカーのみを検証するよう縮小される。現行 core は引き続き
  codex マーカーも要求するため、Phase A 時点では 2 マーカーのみでの allow は red
  (意図した失敗) になる。
- 両 plugin を併用する場合の deny は AND 合成であり、どちらか一方のマーカーが欠落
  していれば push は成立しない。

ハッシュ計算は既存の `tests/test_pre_push_auto_mark.py` の `expected_review_hash`
と同じ手法 (HEAD OID / merge-base OID を束縛し、branch 全差分 + staged + unstaged
diff を連結した sha256) を独立に再実装して用いる。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPLIT_GATE = (
    ROOT
    / "plugins"
    / "pre-push-codex-review"
    / "hooks"
    / "scripts"
    / "block-pre-push-codex.sh"
)
CORE_GATE = (
    ROOT / "plugins" / "pre-push-review" / "hooks" / "scripts" / "block-pre-push.sh"
)

CODE_REVIEWED_MARKER_NAME = ".claude-pre-push-code-reviewed"
CODEX_MARKER_NAME = ".claude-pre-push-codex-reviewed"
SECURITY_MARKER_NAME = ".claude-pre-push-security-reviewed"

CODEX_SUBAGENT_NAME = "pre-push-codex-review:codex-reviewer"

PUSH_COMMAND = "git push origin HEAD"


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


def _create_feature_repository(temporary: Path) -> Path:
    """レビュー対象の差分 commit を 1 つ持つ feature branch の一時 repo を作る。

    tests/test_pre_push_auto_mark.py の create_feature_repository と同じ手順:
    bare origin を用意し、master へ base commit を push、origin/HEAD を設定した
    うえで feature branch に未 push の change commit を積む。
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
    return work


def _expected_review_hash(work: Path) -> str:
    """block-pre-push.sh / block-pre-push-codex.sh が検証するハッシュを独立に
    再計算する (lib/diff-hash.sh の compute_review_hash_in と同じ計算式)。
    """
    head = (
        subprocess.check_output(["git", "rev-parse", "HEAD^{commit}"], cwd=work)
        .decode()
        .strip()
    )
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


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("git") and shutil.which("jq"),
    "gate integration requires bash, git, and jq",
)
class PrePushCodexGateSplitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.work = _create_feature_repository(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_gate(self, gate: Path) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": PUSH_COMMAND},
        }
        return subprocess.run(
            ["bash", str(gate)],
            cwd=self.work,
            input=json.dumps(payload).encode("utf-8"),
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

    def write_marker(self, name: str, value: str) -> None:
        (_git_dir(self.work) / name).write_text(value, encoding="utf-8")

    # (a) 新 gate script の存在
    def test_split_gate_script_exists(self) -> None:
        self.assertTrue(
            SPLIT_GATE.is_file(), f"missing split gate script: {SPLIT_GATE}"
        )

    # (b) 新 gate: codex マーカーが無ければ deny し、deny 文は自 plugin の marker /
    # subagent への言及を持つ
    def test_split_gate_denies_push_without_codex_marker(self) -> None:
        result = self.run_gate(SPLIT_GATE)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.decision(result), "deny")

        reason = self.deny_reason(result)
        self.assertIn(CODEX_SUBAGENT_NAME, reason)
        self.assertIn("codex", reason.lower())

    # (c) 新 gate: 現在の branch 差分 hash と一致する codex マーカーがあれば allow
    def test_split_gate_allows_push_with_fresh_codex_marker(self) -> None:
        review_hash = _expected_review_hash(self.work)
        self.write_marker(CODEX_MARKER_NAME, review_hash)

        result = self.run_gate(SPLIT_GATE)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIsNone(self.decision(result))
        self.assertEqual(result.stdout, b"")

    # (d) core gate: Phase B の契約として code-reviewed / security-reviewed の
    # 2 マーカーのみで allow するはずだが、現行 core は codex マーカーも要求する
    # ため現時点では red (意図した失敗)。
    def test_core_gate_allows_push_with_only_code_and_security_markers(self) -> None:
        review_hash = _expected_review_hash(self.work)
        self.write_marker(CODE_REVIEWED_MARKER_NAME, review_hash)
        self.write_marker(SECURITY_MARKER_NAME, review_hash)

        result = self.run_gate(CORE_GATE)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIsNone(
            self.decision(result),
            "core は code + security の 2 マーカーのみで allow する契約 (Phase B)",
        )

    # (e) 併用 AND 統合: (d) と同じ 2 マーカー状態でも、新 gate は codex マーカー
    # 欠落で deny し、両 plugin 併用時の push はいずれか一方の marker 不足で
    # 止まる。
    def test_combined_gates_block_push_when_codex_marker_missing(self) -> None:
        review_hash = _expected_review_hash(self.work)
        self.write_marker(CODE_REVIEWED_MARKER_NAME, review_hash)
        self.write_marker(SECURITY_MARKER_NAME, review_hash)

        core_result = self.run_gate(CORE_GATE)
        split_result = self.run_gate(SPLIT_GATE)

        self.assertEqual(
            self.decision(split_result),
            "deny",
            "codex マーカーが無い限り新 gate は deny を維持する",
        )

        push_allowed = (
            self.decision(core_result) != "deny"
            and self.decision(split_result) != "deny"
        )
        self.assertFalse(
            push_allowed,
            "併用時は core / 新 gate のいずれかが deny すれば push は成立しない",
        )


if __name__ == "__main__":
    unittest.main()
