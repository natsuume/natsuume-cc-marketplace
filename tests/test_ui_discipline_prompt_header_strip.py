"""ui-discipline: 注入スクリプトは prompt ファイル先頭の保守者向け HTML コメントを除いて配送する。

対象の配送は次の 2 スクリプトである。

- inject-ui-rules.sh (SessionStart): ui-rules.md
- inject-ui-rules-subagent.sh (SubagentStart): ui-rules-subagent-preamble.md と ui-rules.md

「先頭コメントを除いた本文」(``body_without_leading_comment``) は次のとおりに定める。
agent-discipline の hooks/scripts/lib/prompt-body.sh (``read_agent_discipline_prompt``) と
同じ規則である。

- ファイルが ``<!--`` で始まり、その後ろに ``-->`` がある場合は、最初の ``-->`` までの
  コメントと、その直後に続く空行を除く
- ファイルが ``<!--`` で始まらない場合は、内容をそのまま使う (先頭の空行も残す)
- ``-->`` が無い (先頭コメントが閉じていない) 場合は、内容をそのまま使う
- 先頭のコメントが rule マーカー (``<!-- rule:<id> -->`` / ``<!-- subagent-rule:<id> -->``)
  の場合は、内容をそのまま使う
- 先頭コメントより後ろにある HTML コメント (rule マーカーを含む) と、先頭の空行以外の本文は
  変えない
- hook は prompt ファイルを ``$(...)`` で読むため、末尾の改行は落ちる

SubagentStart の additionalContext は、前置き注記の本文のプレースホルダ
``{{UI_PATTERNS_SKILL_PATH}}`` を ui-patterns skill の SKILL.md の絶対パスに置き換えたものと、
空行と、ui-rules.md の本文を並べたものである。

各テストクラスが検査する規則:

- ``BodyHelperTest``: ui-discipline の helper ``read_ui_discipline_prompt`` が、境界ケースの
  fixture (``BOUNDARY_CASES``) それぞれで期待する本文を書く。読めないファイルでは何も書かず
  1 を返す。
- ``HelperParityTest``: ui-discipline の helper と agent-discipline の helper が、境界ケースの
  fixture と ui-discipline の実際の prompt ファイルで、同じ出力と終了コードを返す。
- ``RealPromptDeliveryTest``: 実際の prompt ファイルで各スクリプトを実行すると、
  (1) additionalContext に prompt ファイルの先頭コメントが含まれず、(2) additionalContext は
  先頭コメントを除いた本文を各スクリプトの組み立て方で並べたものに一致し、(3) prompt
  ファイルの rule マーカーが同じ順序ですべて残る。
- ``PreambleHeaderTest``: 前置き注記の先頭コメントは、先頭コメントが注入本文に含まれると
  書かない。

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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "ui-discipline"
HOOKS_DIR = PLUGIN_DIR / "hooks"
SCRIPTS_DIR = HOOKS_DIR / "scripts"
PROMPTS_DIR = HOOKS_DIR / "prompts"
UI_RULES = PROMPTS_DIR / "ui-rules.md"
SUBAGENT_PREAMBLE = PROMPTS_DIR / "ui-rules-subagent-preamble.md"
SKILL_FILE = PLUGIN_DIR / "skills" / "ui-patterns" / "SKILL.md"

UI_HELPER = SCRIPTS_DIR / "lib" / "prompt-body.sh"
UI_HELPER_FUNCTION = "read_ui_discipline_prompt"
AGENT_DISCIPLINE_HELPER = (
    ROOT / "plugins" / "agent-discipline" / "hooks" / "scripts" / "lib" / "prompt-body.sh"
)
AGENT_DISCIPLINE_HELPER_FUNCTION = "read_agent_discipline_prompt"

SKILL_PATH_PLACEHOLDER = "{{UI_PATTERNS_SKILL_PATH}}"
SESSION_ID = "ui-discipline-prompt-header-strip-session"

# 前置き注記の先頭コメントに書かない記述 (空白を除いて照合する)。
DELIVERED_HEADER_PHRASE = "注入本文に含まれて配送される"

LEADING_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)
LEADING_BLANK_LINES_PATTERN = re.compile(r"\A(?:[ \t]*\n)+")
RULE_MARKER_PATTERN = re.compile(r"<!--\s*(?:subagent-)?rule:[A-Za-z0-9_-]+\s*-->")


def body_without_leading_comment(text: str) -> str:
    """prompt ファイルの内容から、配送される本文 (先頭の保守者向けコメントを除いたもの) を求める。

    規則は module docstring の「先頭コメントを除いた本文」のとおり。
    """
    match = LEADING_COMMENT_PATTERN.match(text)
    if match is None or RULE_MARKER_PATTERN.fullmatch(match.group(0)):
        return text.rstrip("\n")
    rest = LEADING_BLANK_LINES_PATTERN.sub("", text[match.end() :], count=1)
    return rest.rstrip("\n")


def leading_comment(text: str) -> str | None:
    """ファイル先頭の閉じた HTML コメント (保守者向けメモ)。無い場合と、先頭が rule マーカーの
    場合は None。"""
    match = LEADING_COMMENT_PATTERN.match(text)
    if match is None or RULE_MARKER_PATTERN.fullmatch(match.group(0)):
        return None
    return match.group(0)


def rule_markers(text: str) -> list[str]:
    return RULE_MARKER_PATTERN.findall(text)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 境界ケースの fixture
# ---------------------------------------------------------------------------

HEADER_SENTINEL = "UI-HEADER-SENTINEL"
HEADER = (
    "<!--\n"
    f"  保守者向けメモ: この行は配送しない ({HEADER_SENTINEL})\n"
    "  2 行目のメモ\n"
    "-->\n"
)
BODY_WITH_COMMENTS = (
    "# 見出し\n"
    "\n"
    "本文の 1 段落目。\n"
    "\n"
    "<!-- rule:sample-rule -->\n"
    "## 1. サンプル規則\n"
    "\n"
    "行の途中のコメント <!-- 本文途中の 1 行コメント --> を含む段落。\n"
    "\n"
    "<!--\n"
    "  本文途中の複数行コメント\n"
    "-->\n"
    "\n"
    "<!-- subagent-rule:sample-subagent-rule -->\n"
    "末尾の段落。\n"
)
BODY_STARTING_WITH_RULE_MARKER = (
    "<!-- rule:first-rule -->\n"
    "## 1. 先頭の規則\n"
    "\n"
    "本文。\n"
)
BODY_WITH_BLANK_LINE_RUNS = (
    "# 見出し\n"
    "\n"
    "\n"
    "\n"
    "空行 3 行の後の段落。\n"
    "\n"
    "\n"
)
UNCLOSED_TEXT = (
    "<!--\n"
    "  閉じていない保守者向けメモ\n"
    "\n"
    "# 見出し\n"
    "\n"
    "本文。\n"
)


@dataclass(frozen=True)
class BoundaryCase:
    """prompt ファイルの内容 (``text``) と、配送される本文の期待値 (``expected_body``)。"""

    label: str
    text: str
    expected_body: str


BOUNDARY_CASES = (
    BoundaryCase(
        label="先頭にコメントが無い",
        text=BODY_WITH_COMMENTS,
        expected_body=BODY_WITH_COMMENTS.rstrip("\n"),
    ),
    BoundaryCase(
        label="先頭にコメントが無く、先頭に空行がある",
        text="\n\n" + BODY_WITH_COMMENTS,
        expected_body=("\n\n" + BODY_WITH_COMMENTS).rstrip("\n"),
    ),
    BoundaryCase(
        label="先頭コメントが閉じていない",
        text=UNCLOSED_TEXT,
        expected_body=UNCLOSED_TEXT.rstrip("\n"),
    ),
    BoundaryCase(
        label="本文の途中に HTML コメントがある",
        text=HEADER + "\n" + BODY_WITH_COMMENTS,
        expected_body=BODY_WITH_COMMENTS.rstrip("\n"),
    ),
    BoundaryCase(
        label="先頭コメントの直後の行に rule マーカーがある",
        text=HEADER + BODY_STARTING_WITH_RULE_MARKER,
        expected_body=BODY_STARTING_WITH_RULE_MARKER.rstrip("\n"),
    ),
    BoundaryCase(
        label="先頭コメントと rule マーカーの間に空行がある",
        text=HEADER + "\n" + BODY_STARTING_WITH_RULE_MARKER,
        expected_body=BODY_STARTING_WITH_RULE_MARKER.rstrip("\n"),
    ),
    BoundaryCase(
        label="ファイルの 1 行目が rule マーカー",
        text=BODY_STARTING_WITH_RULE_MARKER,
        expected_body=BODY_STARTING_WITH_RULE_MARKER.rstrip("\n"),
    ),
    BoundaryCase(
        label="先頭コメントの後に空行が 3 行ある",
        text=HEADER + "\n\n\n" + BODY_WITH_BLANK_LINE_RUNS,
        expected_body=BODY_WITH_BLANK_LINE_RUNS.rstrip("\n"),
    ),
    BoundaryCase(
        label="先頭コメントの直後に本文がある",
        text=HEADER + BODY_WITH_BLANK_LINE_RUNS,
        expected_body=BODY_WITH_BLANK_LINE_RUNS.rstrip("\n"),
    ),
    BoundaryCase(
        label="先頭コメントと同じ行に本文が続く",
        text="<!-- 保守者向けメモ -->本文が同じ行に続く。\n\n次の段落。\n",
        expected_body="本文が同じ行に続く。\n\n次の段落。",
    ),
)


# ---------------------------------------------------------------------------
# helper と hook の実行
# ---------------------------------------------------------------------------


def isolated_env(temp: Path) -> dict[str, str]:
    """親プロセスの env を継承せず、PATH と隔離ディレクトリだけを渡す env を作る。"""
    env = {"PATH": os.environ["PATH"]}
    for key, name in (("HOME", "home"), ("TMPDIR", "tmp"), ("XDG_CACHE_HOME", "cache")):
        directory = temp / name
        directory.mkdir(exist_ok=True)
        env[key] = str(directory)
    return env


def run_helper(helper: Path, function: str, prompt_file: Path) -> subprocess.CompletedProcess[str]:
    """helper を /bin/sh で読み込み、function に prompt_file を渡して実行する。"""
    with tempfile.TemporaryDirectory() as temporary:
        return subprocess.run(
            ["/bin/sh", "-c", f'. "$1" && {function} "$2"', "sh", str(helper), str(prompt_file)],
            env=isolated_env(Path(temporary)),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=15,
            check=False,
        )


@dataclass(frozen=True)
class Delivery:
    """1 つの配送経路: 実行するスクリプト・入力・読む prompt ファイル・組み立て方。

    ``assemble`` は、各 prompt ファイルの先頭コメントを除いた本文 (``targets`` の順) から、
    期待する additionalContext を組み立てる。
    """

    label: str
    script: str
    event: str
    targets: tuple[Path, ...]
    assemble: Callable[[list[str]], str]
    extra_input: tuple[tuple[str, str], ...] = ()


def assemble_session_start(bodies: list[str]) -> str:
    (rules,) = bodies
    return rules


def assemble_subagent_start(bodies: list[str]) -> str:
    preamble, rules = bodies
    return preamble.replace(SKILL_PATH_PLACEHOLDER, str(SKILL_FILE)) + "\n\n" + rules


DELIVERIES = (
    Delivery(
        label="inject-ui-rules.sh (ui-rules.md)",
        script="inject-ui-rules.sh",
        event="SessionStart",
        targets=(UI_RULES,),
        assemble=assemble_session_start,
    ),
    Delivery(
        label="inject-ui-rules-subagent.sh (ui-rules-subagent-preamble.md + ui-rules.md)",
        script="inject-ui-rules-subagent.sh",
        event="SubagentStart",
        targets=(SUBAGENT_PREAMBLE, UI_RULES),
        assemble=assemble_subagent_start,
        extra_input=(
            ("agent_id", "ui-discipline-prompt-header-strip-agent"),
            ("agent_type", "general-purpose"),
        ),
    ),
)


def run_delivery(delivery: Delivery) -> subprocess.CompletedProcess[str]:
    """スクリプトを shebang で直接実行する。"""
    body = {"hook_event_name": delivery.event, "session_id": SESSION_ID}
    body.update(dict(delivery.extra_input))
    with tempfile.TemporaryDirectory() as temporary:
        return subprocess.run(
            [str(SCRIPTS_DIR / delivery.script)],
            cwd=ROOT,
            env=isolated_env(Path(temporary)),
            input=json.dumps(body, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=15,
            check=False,
        )


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------


class BodyHelperTest(unittest.TestCase):
    """ui-discipline の helper は境界ケースの fixture で期待する本文を書く。"""

    def test_helper_writes_the_expected_body_for_each_boundary_case(self) -> None:
        for case in BOUNDARY_CASES:
            with self.subTest(case=case.label):
                with tempfile.TemporaryDirectory() as temporary:
                    prompt_file = Path(temporary) / "prompt.md"
                    prompt_file.write_text(case.text, encoding="utf-8")
                    result = run_helper(UI_HELPER, UI_HELPER_FUNCTION, prompt_file)
                self.assertEqual(
                    0, result.returncode, f"{case.label}: 終了コードが 0 でない ({result.stderr})"
                )
                self.assertEqual(
                    case.expected_body,
                    result.stdout.rstrip("\n"),
                    f"{case.label}: helper の本文が fixture の期待値と違う",
                )
                if HEADER_SENTINEL in case.text:
                    self.assertNotIn(
                        HEADER_SENTINEL, result.stdout, f"{case.label}: 先頭コメントが残っている"
                    )

    def test_helper_returns_1_without_output_for_an_unreadable_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.md"
            result = run_helper(UI_HELPER, UI_HELPER_FUNCTION, missing)
        self.assertEqual(1, result.returncode, "読めないファイルで 1 を返さない")
        self.assertEqual("", result.stdout, "読めないファイルで出力がある")


class HelperParityTest(unittest.TestCase):
    """ui-discipline の helper は agent-discipline の helper と同じ出力・終了コードを返す。"""

    def assert_same_result(self, label: str, prompt_file: Path) -> None:
        expected = run_helper(
            AGENT_DISCIPLINE_HELPER, AGENT_DISCIPLINE_HELPER_FUNCTION, prompt_file
        )
        actual = run_helper(UI_HELPER, UI_HELPER_FUNCTION, prompt_file)
        self.assertEqual(
            (expected.returncode, expected.stdout),
            (actual.returncode, actual.stdout),
            f"{label}: ui-discipline の helper の結果が agent-discipline の helper と違う",
        )

    def test_boundary_cases_give_the_same_result(self) -> None:
        for case in BOUNDARY_CASES:
            with self.subTest(case=case.label):
                with tempfile.TemporaryDirectory() as temporary:
                    prompt_file = Path(temporary) / "prompt.md"
                    prompt_file.write_text(case.text, encoding="utf-8")
                    self.assert_same_result(case.label, prompt_file)

    def test_real_prompt_files_give_the_same_result(self) -> None:
        for prompt_file in (UI_RULES, SUBAGENT_PREAMBLE):
            with self.subTest(prompt=prompt_file.name):
                self.assert_same_result(prompt_file.name, prompt_file)

    def test_unreadable_file_gives_the_same_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assert_same_result("読めないファイル", Path(temporary) / "missing.md")


@unittest.skipUnless(shutil.which("jq"), "hook の実行には jq が必要")
class RealPromptDeliveryTest(unittest.TestCase):
    """実際の prompt ファイルを読む各スクリプトの出力。"""

    def context_of(self, delivery: Delivery) -> str:
        """スクリプトを実行し、出力 JSON の形を検査して additionalContext を返す。"""
        result = run_delivery(delivery)
        label = delivery.label
        self.assertEqual(
            0, result.returncode, f"{label}: exit code が 0 でない (stderr: {result.stderr})"
        )
        self.assertTrue(result.stdout.strip(), f"{label}: 出力が無い (stderr: {result.stderr})")
        output = json.loads(result.stdout)
        self.assertEqual({"hookSpecificOutput"}, set(output), f"{label}: 最上位のキーが違う")
        hook_output = output["hookSpecificOutput"]
        self.assertEqual(
            {"hookEventName", "additionalContext"},
            set(hook_output),
            f"{label}: hookSpecificOutput のキーが違う",
        )
        self.assertEqual(
            delivery.event, hook_output["hookEventName"], f"{label}: hookEventName が違う"
        )
        self.assertIsInstance(
            hook_output["additionalContext"], str, f"{label}: additionalContext が文字列でない"
        )
        return hook_output["additionalContext"]

    def test_leading_comment_of_the_prompt_is_not_delivered(self) -> None:
        for delivery in DELIVERIES:
            with self.subTest(delivery=delivery.label):
                context = self.context_of(delivery)
                for path in delivery.targets:
                    header = leading_comment(read(path))
                    if header is None:
                        continue
                    self.assertNotIn(
                        header,
                        context,
                        f"{delivery.label}: {path.name} の先頭コメントが additionalContext に"
                        "含まれる",
                    )

    def test_body_without_the_leading_comment_is_delivered_unchanged(self) -> None:
        for delivery in DELIVERIES:
            with self.subTest(delivery=delivery.label):
                context = self.context_of(delivery)
                bodies = [body_without_leading_comment(read(path)) for path in delivery.targets]
                self.assertEqual(
                    delivery.assemble(bodies),
                    context,
                    f"{delivery.label}: additionalContext が先頭コメントを除いた本文の"
                    "組み立てと一致しない",
                )

    def test_rule_markers_of_the_prompt_are_all_delivered(self) -> None:
        for delivery in DELIVERIES:
            with self.subTest(delivery=delivery.label):
                context = self.context_of(delivery)
                expected = [
                    marker for path in delivery.targets for marker in rule_markers(read(path))
                ]
                self.assertEqual(
                    expected,
                    rule_markers(context),
                    f"{delivery.label}: prompt ファイルの rule マーカーが additionalContext に"
                    "同じ順序で残っていない",
                )


class PreambleHeaderTest(unittest.TestCase):
    """前置き注記の先頭コメントは、先頭コメントが注入本文に含まれると書かない。"""

    def test_preamble_header_does_not_say_it_is_delivered(self) -> None:
        header = leading_comment(read(SUBAGENT_PREAMBLE)) or ""
        self.assertNotIn(
            DELIVERED_HEADER_PHRASE,
            re.sub(r"\s+", "", header),
            f"{SUBAGENT_PREAMBLE.name}: 先頭コメントが注入本文に含まれるという記述が残っている",
        )


if __name__ == "__main__":
    unittest.main()
