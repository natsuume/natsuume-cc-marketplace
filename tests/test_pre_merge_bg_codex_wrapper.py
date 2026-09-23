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
import os
import re
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
SCRIPTS_DIR = HOOK.parent
MERGE_GATE = SCRIPTS_DIR / "block-pre-merge.sh"
CMD_PARSER_LIB = SCRIPTS_DIR / "lib" / "cmd-parser.sh"

# パターン部に ANSI-C quoting (`$'...'`) を含む bash のパターン置換
# (`${VAR//...$'...'...}`)。macOS 既定の bash 3.2 はパターン部の `$'...'` を展開
# しないため、この形の置換は bash のバージョンによって結果が変わる。
ANSI_C_PATTERN_SUBSTITUTION = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*//[^}]*\$'")

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


def code_lines(path: Path) -> list[str]:
    """コメント行 (先頭の空白を除いて `#` で始まる行) を除いた本文の行。"""
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


def copy_scripts_without_cmd_parser(destination: Path) -> Path:
    """hook scripts ディレクトリを複製し、複製先から `lib/cmd-parser.sh` を除く。"""
    copied = destination / "scripts"
    shutil.copytree(SCRIPTS_DIR, copied)
    (copied / "lib" / "cmd-parser.sh").unlink(missing_ok=True)
    return copied


class LineContinuationNormalizationTest(unittest.TestCase):
    """hook script の行継続 `\\<改行>` 正規化の契約。

    - block-bg-codex-wrapper.sh と block-pre-merge.sh は、行継続の正規化を
      `lib/cmd-parser.sh` の `normalize_line_continuations` で行う。パターン部に
      ANSI-C quoting を使う bash のパターン置換は bash 3.2 と 4 以降で結果が変わる
      ため使わない。
    - helper の入出力は、テストを実行する bash のバージョンに依らない形で固定する。
    - `lib/cmd-parser.sh` を読み込めない場合、block-bg-codex-wrapper.sh は生の
      コマンドが wrapper basename か行継続を含むときだけ deny し (reason に
      `cmd-parser.sh` を含める)、それ以外のコマンドには関与しない (無出力)。
    """

    def run_hook_without_cmd_parser(
        self, payload: dict[str, object]
    ) -> subprocess.CompletedProcess[bytes]:
        with tempfile.TemporaryDirectory() as name:
            scripts = copy_scripts_without_cmd_parser(Path(name))
            return subprocess.run(
                ["bash", str(scripts / HOOK.name)],
                input=json.dumps(payload).encode("utf-8"),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=name,
            )

    def assert_denied_for_missing_cmd_parser(
        self, result: subprocess.CompletedProcess[bytes]
    ) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotEqual(
            result.stdout,
            b"",
            "cmd-parser.sh を読み込めない状態で deny されませんでした",
        )
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("cmd-parser.sh", output["permissionDecisionReason"])

    def test_scripts_do_not_use_ansi_c_quoting_in_pattern_substitution(
        self,
    ) -> None:
        for script in (HOOK, MERGE_GATE):
            with self.subTest(script=script.name):
                offending = [
                    line
                    for line in code_lines(script)
                    if ANSI_C_PATTERN_SUBSTITUTION.search(line)
                ]
                self.assertEqual(
                    offending,
                    [],
                    f"{script.name} がパターン部に ANSI-C quoting を使う"
                    "パターン置換を含みます (bash 3.2 では展開されません)",
                )

    def test_scripts_normalize_with_cmd_parser_helper(self) -> None:
        for script in (HOOK, MERGE_GATE):
            with self.subTest(script=script.name):
                code = "\n".join(code_lines(script))
                self.assertTrue(
                    "lib/cmd-parser.sh" in code,
                    f"{script.name} が lib/cmd-parser.sh を source していません",
                )
                self.assertTrue(
                    'normalize_line_continuations "$COMMAND"' in code,
                    f"{script.name} が normalize_line_continuations で"
                    "行継続を正規化していません",
                )

    @unittest.skipUnless(shutil.which("bash"), "helper test requires bash")
    def test_helper_normalizes_line_continuations(self) -> None:
        self.assertTrue(
            CMD_PARSER_LIB.is_file(), f"lib が見つかりません: {CMD_PARSER_LIB}"
        )
        cases = {
            "split_word": ("gh pr me\\\nrge 1", "gh pr merge 1"),
            "inside_single_quotes": ("echo 'a\\\nb'", "echo 'ab'"),
            "inside_double_quotes": ('echo "a\\\nb"', 'echo "ab"'),
            "trailing_backslash_without_newline": ("echo a\\", "echo a\\"),
            "trailing_line_continuation": ("git push\\\n", "git push"),
            "no_line_continuation": (
                "gh pr merge 1 --squash",
                "gh pr merge 1 --squash",
            ),
        }
        for label, (command, expected) in cases.items():
            with self.subTest(case=label):
                env = os.environ.copy()
                env["LIB"] = str(CMD_PARSER_LIB)
                env["INPUT"] = command
                result = subprocess.run(
                    [
                        "bash",
                        "-c",
                        'source "$LIB"; normalize_line_continuations "$INPUT"',
                    ],
                    env=env,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(
                    result.stdout.decode("utf-8"), expected, f"case={label}"
                )

    @unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
    def test_wrapper_launch_is_denied_without_cmd_parser(self) -> None:
        # lib があれば allow される正規の起動でも、正規化できない状態では通さない。
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": WRAPPER_COMMAND},
            "agent_type": CODEX_REVIEWER_AGENT_TYPE,
        }
        result = self.run_hook_without_cmd_parser(payload)
        self.assert_denied_for_missing_cmd_parser(result)

    @unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
    def test_line_continuation_command_is_denied_without_cmd_parser(
        self,
    ) -> None:
        # 行継続を含むコマンドは正規化しないと wrapper 起動かを判定できない。
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "echo a\\\nb"},
        }
        result = self.run_hook_without_cmd_parser(payload)
        self.assert_denied_for_missing_cmd_parser(result)

    @unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
    def test_unrelated_command_passes_without_cmd_parser(self) -> None:
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
        }
        result = self.run_hook_without_cmd_parser(payload)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
