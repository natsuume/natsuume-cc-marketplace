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


class BlockBgCodexWrapperExecFormClassificationTest(unittest.TestCase):
    """実行形 segment 分類 (codex review P2 指摘の regression 修正) のテスト。

    substring `run-codex-review.sh` を含んでいても、 wrapper を実行せず言及する
    だけの read-only コマンド (cat / git diff / grep 等) は agent_type gate の対象外
    (allow) とし、 interpreter 起動・コマンド置換を含む形・不明コマンドのみを実行形
    として gate する契約を固定する。
    """

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

    def test_cat_mention_with_missing_agent_type_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "cat plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_git_diff_mention_with_missing_agent_type_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "git diff -- plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_grep_piped_to_head_mention_with_missing_agent_type_is_allowed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "grep -n marker plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh | head -5"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_sed_mention_with_missing_agent_type_is_denied(self) -> None:
        # sed は allowlist 外 (GNU sed の e コマンドという子プロセス実行面を持つため)。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "sed -n '1,50p' plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_find_exec_with_missing_agent_type_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        r"find . -name run-codex-review.sh -exec bash {} \;"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_git_global_option_with_missing_agent_type_is_denied(self) -> None:
        # 直後 token が `-c` (global option) のため git 特例の言及扱いにならない。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "git -c core.pager=x diff run-codex-review.sh"
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_command_substitution_fallback_missing_agent_type_is_denied(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        'WRAPPER=$(find "$HOME/.claude/plugins/cache" '
                        "-path '*run-codex-review.sh' -type f | head -1) "
                        '&& bash "$WRAPPER"'
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_command_substitution_fallback_agent_type_match_is_allowed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        'WRAPPER=$(find "$HOME/.claude/plugins/cache" '
                        "-path '*run-codex-review.sh' -type f | head -1) "
                        '&& bash "$WRAPPER"'
                    )
                },
                "agent_type": CODEX_REVIEWER_AGENT_TYPE,
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_command_substitution_fallback_with_trailing_pipe_is_denied(
        self,
    ) -> None:
        # INDIRECTION フラグ + 位置を問わない単独 `|` の存在で、 agent_type 一致でも
        # deny する (末尾の `| tee` は wrapper 起動 segment に直接隣接していない)。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        'WRAPPER=$(find "$HOME/.claude/plugins/cache" '
                        "-path '*run-codex-review.sh' -type f | head -1) "
                        '&& bash "$WRAPPER" | tee /tmp/log.txt'
                    )
                },
                "agent_type": CODEX_REVIEWER_AGENT_TYPE,
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_allowlist_command_with_command_substitution_is_denied(self) -> None:
        # allowlist コマンド (cat) でもコマンド置換を含む場合は規則 1 が優先し実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "cat $(bash run-codex-review.sh)"},
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)


if __name__ == "__main__":
    unittest.main()
