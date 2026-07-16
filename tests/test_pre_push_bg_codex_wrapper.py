"""block-bg-codex-wrapper.sh の agent_type 検証 gate (fail-closed) 契約を先に固定する
TDD Phase A (red) テスト (issue #267)。

契約: agent_type 検証 gate (fail-closed): command に run-codex-review.sh を含む Bash
実行は、hook payload のトップレベル agent_type が pre-push-review:codex-reviewer に
完全一致する場合のみ許可。欠落 (= メインセッション or agent_type 未対応の旧 Claude
Code)・不一致はいずれも deny。既存の bg / pipeline deny は agent_type 一致時も維持。
jq 不在等の環境失敗は既存どおり fail-open (本テストの対象外)。

この gate はまだ実装されていない (issue #267 で後続 commit として実装予定)。その
ため、agent_type 欠落・不一致で deny を期待するケースは現行実装では FAIL する
(= red で正しい)。gate 実装後にそれらのケースは green になる想定。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = (
    ROOT
    / "plugins"
    / "pre-push-review"
    / "hooks"
    / "scripts"
    / "block-bg-codex-wrapper.sh"
)

WRAPPER_COMMAND = (
    "bash /opt/claude/plugins/pre-push-review/hooks/scripts/run-codex-review.sh"
)
CODEX_REVIEWER_AGENT_TYPE = "pre-push-review:codex-reviewer"


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class BlockBgCodexWrapperAgentTypeGateTest(unittest.TestCase):
    def run_hook(
        self, payload: dict[str, object], cwd: Path
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps(payload).encode("utf-8"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
        )

    def assert_denied(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotEqual(result.stdout, b"")
        response = json.loads(result.stdout)
        self.assertEqual(
            response["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def assert_allowed(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_agent_type_missing_foreground_wrapper_is_denied_with_reviewer_reason(
        self,
    ) -> None:
        # gate 実装後 (issue #267) に green になる想定: 現行実装は agent_type を検証
        # しないため、agent_type 欠落でも foreground wrapper 起動を allow してしまう。
        # そのためこのテストは現行実装では FAIL する (= red で正しい)。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": WRAPPER_COMMAND},
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)
            response = json.loads(result.stdout)
            reason = response["hookSpecificOutput"]["permissionDecisionReason"]
            self.assertIn(CODEX_REVIEWER_AGENT_TYPE, reason)

    def test_agent_type_mismatch_foreground_wrapper_is_denied(self) -> None:
        # gate 実装後 (issue #267) に green になる想定: 現行実装は agent_type を検証
        # しないため、不一致の agent_type でも foreground wrapper 起動を allow して
        # しまう。そのためこのテストは現行実装では FAIL する (= red で正しい)。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": WRAPPER_COMMAND},
                "agent_type": "general-purpose",
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_agent_type_match_foreground_wrapper_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": WRAPPER_COMMAND},
                "agent_type": CODEX_REVIEWER_AGENT_TYPE,
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_agent_type_match_background_flag_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": WRAPPER_COMMAND,
                    "run_in_background": True,
                },
                "agent_type": CODEX_REVIEWER_AGENT_TYPE,
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_agent_type_match_pipeline_command_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"{WRAPPER_COMMAND} | tee /tmp/log.txt",
                },
                "agent_type": CODEX_REVIEWER_AGENT_TYPE,
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_unrelated_command_with_missing_agent_type_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "echo hello"},
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)


if __name__ == "__main__":
    unittest.main()
