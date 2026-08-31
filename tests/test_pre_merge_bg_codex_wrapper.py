"""pre-merge-codex-review の wrapper 起動検証 hook (block-bg-codex-wrapper.sh) の契約テスト。

固定する契約:

- codex review wrapper (`run-pre-merge-codex-review.sh`) を含む Bash 実行は、hook payload
  のトップレベル `agent_type` が `pre-merge-codex-review:codex-reviewer` に完全一致する
  場合のみ許可する。欠落 (= メインセッション or agent_type 未対応の Claude Code) と
  不一致はいずれも deny する。
- agent_type が一致していても、background 起動 (`run_in_background: true`) と
  pipeline / background separator 経由 (`|` / `&`) は deny する。
- wrapper と無関係な Bash 呼び出しには関与しない (無出力)。
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
    / "pre-merge-codex-review"
    / "hooks"
    / "scripts"
    / "block-bg-codex-wrapper.sh"
)

WRAPPER_COMMAND = (
    "bash /opt/claude/plugins/pre-merge-codex-review/hooks/scripts/"
    "run-pre-merge-codex-review.sh"
)
CODEX_REVIEWER_AGENT_TYPE = "pre-merge-codex-review:codex-reviewer"


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

    def test_hook_script_exists(self) -> None:
        self.assertTrue(HOOK.is_file(), f"missing wrapper guard: {HOOK}")

    def test_agent_type_missing_foreground_wrapper_is_denied(self) -> None:
        # メインセッションでは agent_type が欠落するため、foreground でも deny する。
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
        # built-in subagent 等の別 agent_type からの起動も deny する。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": WRAPPER_COMMAND},
                "agent_type": "general-purpose",
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_pre_push_reviewer_agent_type_is_denied(self) -> None:
        # 併存する pre-push 側の reviewer namespace も完全一致しないため deny する。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": WRAPPER_COMMAND},
                "agent_type": "pre-push-codex-review:codex-reviewer",
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

    def test_agent_type_match_with_output_redirection_is_allowed(self) -> None:
        # `2>&1` の `&` は redirection であり background separator ではない。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"{WRAPPER_COMMAND} > codex.log 2>&1",
                },
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

    def test_agent_type_match_background_separator_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"{WRAPPER_COMMAND} &",
                },
                "agent_type": CODEX_REVIEWER_AGENT_TYPE,
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_line_continuation_split_wrapper_name_is_denied(self) -> None:
        # 行継続で basename を分断しても検出を素通りしない。
        with tempfile.TemporaryDirectory() as name:
            split_command = (
                "bash /opt/claude/plugins/pre-merge-codex-review/hooks/scripts/"
                "run-pre-merge-codex-\\\nreview.sh"
            )
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": split_command},
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_agent_definition_fallback_listing_is_allowed(self) -> None:
        """agents/codex-reviewer.md の fallback step 1 (候補列挙) が deny されない。"""
        listing_command = (
            'for candidate in "$HOME"/.claude/plugins/cache/*/'
            "pre-merge-codex-review/*/hooks/scripts/"
            "run-pre-merge-codex-review.sh "
            '"$HOME"/.claude/plugins/cache/*/pre-merge-codex-review/hooks/'
            "scripts/run-pre-merge-codex-review.sh; do "
            'if [ -f "$candidate" ]; then echo "$candidate"; fi; done'
        )
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": listing_command},
                "agent_type": CODEX_REVIEWER_AGENT_TYPE,
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_agent_definition_fallback_launch_is_allowed(self) -> None:
        """agents/codex-reviewer.md の fallback step 2 (実 path 起動) が deny されない。"""
        launch_command = (
            'bash "/home/user/.claude/plugins/cache/natsuume-plugins/'
            "pre-merge-codex-review/1.0.0/hooks/scripts/"
            'run-pre-merge-codex-review.sh"'
        )
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": launch_command},
                "agent_type": CODEX_REVIEWER_AGENT_TYPE,
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_unrelated_command_with_missing_agent_type_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "echo hello"},
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_pre_push_wrapper_command_is_not_touched(self) -> None:
        # 併存環境での basename 干渉を避けるため、pre-push 側の wrapper には関与しない。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "bash /opt/claude/plugins/pre-push-codex-review/hooks/"
                        "scripts/run-pre-push-codex-review.sh"
                    )
                },
                "agent_type": "pre-push-codex-review:codex-reviewer",
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)


if __name__ == "__main__":
    unittest.main()
