"""ui-discipline: SubagentStart の注入範囲と、注入範囲を利用者に伝える文書。

ui-discipline は UI 実装の規律を SessionStart (inject-ui-rules.sh) と SubagentStart
(inject-ui-rules-subagent.sh) で注入する。SubagentStart では、UI を実装しない agent
(``EXCLUDED_AGENT_TYPES``) を除外する。

inject-ui-rules-subagent.sh の I/O 契約:

- stdin は SubagentStart の hook 入力 (JSON)
- ``agent_type`` が ``EXCLUDED_AGENT_TYPES`` のどれかと完全に一致するときは、何も出力せず
  exit 0 する
- それ以外は ``{"hookSpecificOutput": {"hookEventName": "SubagentStart",
  "additionalContext": <文字列>}}`` を出力して exit 0 する。``agent_type`` が無い・空・null の
  入力と、JSON として読めない入力 (除外の判定ができない入力) もこちらに含む

各テストクラスが検査する規則:

- ``ExcludedAgentTest``: 除外リストの各 agent_type では何も出力せず exit 0 する。
- ``IncludedAgentTest``: 除外リストに無い agent_type (除外リストの名前に似た名前を含む) と、
  agent_type を判定できない入力では、これまでどおり注入する。
- ``InjectionScriptCommentTest``: inject-ui-rules.sh のコメントに issue 番号 (``#<数字>``) が
  無い。
- ``ReadmeInjectionScopeTest``: plugin README に、除外リストの agent_type がすべて書かれ、
  UI のある project の ``.claude/settings.json`` の ``enabledPlugins`` で有効にする手順が
  書かれている。

hook は hooks.json と同じく shebang で直接実行し、tempfile で作った一時ディレクトリを
``TMPDIR`` / ``HOME`` / ``XDG_CACHE_HOME`` として env で渡す (親プロセスの env を継承しない)。
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
PLUGIN_DIR = ROOT / "plugins" / "ui-discipline"
SCRIPTS_DIR = PLUGIN_DIR / "hooks" / "scripts"
SESSION_START_SCRIPT = SCRIPTS_DIR / "inject-ui-rules.sh"
SUBAGENT_START_SCRIPT = SCRIPTS_DIR / "inject-ui-rules-subagent.sh"
PLUGIN_README = PLUGIN_DIR / "README.md"

EVENT = "SubagentStart"
SESSION_ID = "ui-discipline-injection-scope-session"

# SubagentStart で注入しない agent_type。UI を実装しない agent である。
EXCLUDED_AGENT_TYPES = (
    # このリポジトリの plugin が定義する agent
    "pre-push-review:code-reviewer",
    "pre-push-review:security-reviewer",
    "pre-push-codex-review:codex-reviewer",
    "pre-merge-cross-review:codex-reviewer",
    "cross-model-advisor:codex-advisor-runner",
    "cross-model-advisor:codex-rescue-runner",
    "cross-model-advisor:codex-review-runner",
    # Claude Code 組み込みの agent
    "Explore",
    "claude-code-guide",
    "statusline-setup",
)

# 除外リストに無い agent_type。除外リストの名前と完全には一致しない名前を含む
# (除外の判定は完全一致で行う)。
INCLUDED_AGENT_TYPES = (
    "general-purpose",
    "Plan",
    "other-plugin:implementer",
    "Explore-v2",
    "my-plugin:Explore",
    "explore",
    "pre-push-review:code-reviewer-v2",
    "other-plugin:codex-reviewer",
)

# agent_type を判定できない入力 (stdin にそのまま渡す文字列)。
UNDECIDABLE_INPUTS = (
    ("agent_type が無い", json.dumps({"hook_event_name": EVENT, "session_id": SESSION_ID})),
    (
        "agent_type が空",
        json.dumps({"hook_event_name": EVENT, "session_id": SESSION_ID, "agent_type": ""}),
    ),
    (
        "agent_type が null",
        json.dumps({"hook_event_name": EVENT, "session_id": SESSION_ID, "agent_type": None}),
    ),
    ("JSON として読めない", "not json {"),
    ("空の入力", ""),
)

ISSUE_NUMBER_PATTERN = re.compile(r"#\d+")
PROJECT_SETTINGS_PATH = ".claude/settings.json"
ENABLED_PLUGINS_KEY = "enabledPlugins"


def subagent_input(agent_type: str) -> str:
    return json.dumps(
        {
            "hook_event_name": EVENT,
            "session_id": SESSION_ID,
            "agent_id": "ui-discipline-injection-scope-agent",
            "agent_type": agent_type,
        }
    )


def run_subagent_hook(stdin: str) -> subprocess.CompletedProcess[str]:
    """inject-ui-rules-subagent.sh を shebang で直接実行する。"""
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        env = {"PATH": os.environ["PATH"]}
        for key, name in (("HOME", "home"), ("TMPDIR", "tmp"), ("XDG_CACHE_HOME", "cache")):
            directory = temp / name
            directory.mkdir()
            env[key] = str(directory)
        return subprocess.run(
            [str(SUBAGENT_START_SCRIPT)],
            cwd=ROOT,
            env=env,
            input=stdin,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=15,
            check=False,
        )


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@unittest.skipUnless(shutil.which("jq"), "hook の実行には jq が必要")
class ExcludedAgentTest(unittest.TestCase):
    """除外リストの agent_type では何も出力せず exit 0 する。"""

    def test_excluded_agent_types_receive_no_output(self) -> None:
        for agent_type in EXCLUDED_AGENT_TYPES:
            with self.subTest(agent_type=agent_type):
                result = run_subagent_hook(subagent_input(agent_type))
                self.assertEqual(
                    0,
                    result.returncode,
                    f"{agent_type}: exit code が 0 でない (stderr: {result.stderr})",
                )
                self.assertEqual(
                    "", result.stdout, f"{agent_type}: 除外する agent_type に出力がある"
                )


@unittest.skipUnless(shutil.which("jq"), "hook の実行には jq が必要")
class IncludedAgentTest(unittest.TestCase):
    """除外リストに無い agent_type と、agent_type を判定できない入力では注入する。"""

    def assert_injected(self, label: str, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(
            0, result.returncode, f"{label}: exit code が 0 でない (stderr: {result.stderr})"
        )
        self.assertTrue(result.stdout.strip(), f"{label}: 注入の出力が無い")
        output = json.loads(result.stdout)
        self.assertEqual({"hookSpecificOutput"}, set(output), f"{label}: 最上位のキーが違う")
        hook_output = output["hookSpecificOutput"]
        self.assertEqual(
            {"hookEventName", "additionalContext"},
            set(hook_output),
            f"{label}: hookSpecificOutput のキーが違う",
        )
        self.assertEqual(EVENT, hook_output["hookEventName"], f"{label}: hookEventName が違う")
        context = hook_output["additionalContext"]
        self.assertIsInstance(context, str, f"{label}: additionalContext が文字列でない")
        self.assertIn(
            "<!-- rule:component-layers -->",
            context,
            f"{label}: additionalContext に UI 実装規律の本文が無い",
        )

    def test_agent_types_outside_the_list_are_injected(self) -> None:
        for agent_type in INCLUDED_AGENT_TYPES:
            with self.subTest(agent_type=agent_type):
                self.assert_injected(agent_type, run_subagent_hook(subagent_input(agent_type)))

    def test_input_without_a_decidable_agent_type_is_injected(self) -> None:
        for label, stdin in UNDECIDABLE_INPUTS:
            with self.subTest(input=label):
                self.assert_injected(label, run_subagent_hook(stdin))


class InjectionScriptCommentTest(unittest.TestCase):
    """inject-ui-rules.sh のコメントは issue 番号を書かず、現在の契約だけを書く。"""

    def test_session_start_script_has_no_issue_number(self) -> None:
        found = ISSUE_NUMBER_PATTERN.findall(read(SESSION_START_SCRIPT))
        self.assertEqual(
            [],
            found,
            f"{SESSION_START_SCRIPT.relative_to(ROOT)}: issue 番号の記述がある",
        )


class ReadmeInjectionScopeTest(unittest.TestCase):
    """plugin README が注入範囲 (除外リストと project 単位の有効化) を書く。"""

    def test_readme_lists_every_excluded_agent_type(self) -> None:
        readme = read(PLUGIN_README)
        for agent_type in EXCLUDED_AGENT_TYPES:
            with self.subTest(agent_type=agent_type):
                # 前後が名前の一部 (ASCII 英数字・`_`・`-`・`:`) でない位置に、名前全体が現れる。
                pattern = re.compile(
                    rf"(?<![\w:-]){re.escape(agent_type)}(?![\w:-])", re.ASCII
                )
                self.assertRegex(
                    readme,
                    pattern,
                    f"README に除外する agent_type `{agent_type}` が書かれていない",
                )

    def test_readme_describes_enabling_per_project(self) -> None:
        readme = read(PLUGIN_README)
        for phrase in (PROJECT_SETTINGS_PATH, ENABLED_PLUGINS_KEY):
            with self.subTest(phrase=phrase):
                self.assertIn(
                    phrase,
                    readme,
                    f"README に project 単位で有効にする手順 ({phrase}) が書かれていない",
                )


if __name__ == "__main__":
    unittest.main()
