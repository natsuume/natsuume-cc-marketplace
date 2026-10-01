"""agent-discipline: part 3 の配送で、claim 用スクリプトのパスのプレースホルダを置き換える。

always-3.md の `rule:issue-claim` は claim 用スクリプトを
``'{{CLAIM_ISSUE_SCRIPT_PATH}}' <N> '<branch>'`` の形で 1 回だけ参照する。
inject-rules-part.sh は part 3 を配送するとき、プレースホルダを
``skills/issue-start/scripts/claim-issue.sh`` の絶対パスに置き換える。

- ``ClaimScriptPlaceholderTest``: always-3.md にプレースホルダがちょうど 1 回、上の形で
  `rule:issue-claim` の節の中に現れる。
- ``ClaimScriptPathSubstitutionTest``: part 3 の配送はプレースホルダを claim-issue.sh の絶対
  パス (realpath で比べる) に置き換え、出力にプレースホルダを残さない。プレースホルダの行の
  ほかは always-3.md の本文 (先頭の保守者向け HTML コメントを除いたもの) と同じ。part 2 の
  配送は always-2.md の本文のまま。part 3 の配送本文は 7,800 字 (Unicode code point 数) 以下。
- ``ClaimScriptMissingTest``: plugin ディレクトリを一時ディレクトリにコピーし、コピー側の
  claim-issue.sh を削除して実行すると、プレースホルダを含む行全体が「claim 用のスクリプトが
  見つからないため、issue への着手をせずユーザーに報告する」趣旨の 1 行に置き換わる。ほかの行は
  変わらず、part 3 の他の rule (`rule:ask-user-question`・`rule:tdd-two-phase`) も届く。part 2
  の配送は変わらない。

inject-rules-part.sh は、一時ディレクトリを ``TMPDIR`` / ``HOME`` として env で渡して実行する
(実システムの配送済みマーカーのディレクトリに触れない)。置き換えた文の言い回しは検査せず、
文に含める要素だけを検査する。
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
PLUGIN_DIR = ROOT / "plugins" / "agent-discipline"

INJECT_SCRIPT_RELATIVE = Path("hooks") / "scripts" / "inject-rules-part.sh"
PROMPTS_RELATIVE = Path("hooks") / "prompts"
CLAIM_SCRIPT_RELATIVE = Path("skills") / "issue-start" / "scripts" / "claim-issue.sh"

ALWAYS_2 = PLUGIN_DIR / PROMPTS_RELATIVE / "always-2.md"
ALWAYS_3 = PLUGIN_DIR / PROMPTS_RELATIVE / "always-3.md"
CLAIM_SCRIPT = PLUGIN_DIR / CLAIM_SCRIPT_RELATIVE

PLACEHOLDER = "{{CLAIM_ISSUE_SCRIPT_PATH}}"
INVOCATION = f"'{PLACEHOLDER}' <N> '<branch>'"
ISSUE_CLAIM_MARKER = "<!-- rule:issue-claim -->"
RULE_MARKER_PREFIX = "<!-- rule:"
OTHER_PART_3_MARKERS = ("<!-- rule:ask-user-question -->", "<!-- rule:tdd-two-phase -->")

SESSION_ID = "claim-script-injection-session"

# part 3 の配送本文 (additionalContext) の字数の上限 (Unicode code point 数)。
PART_3_SIZE_LIMIT = 7800

# スクリプトが無いときに、プレースホルダの行の代わりに置く文の要素 (空白を除去した行に照合する)。
MISSING_SCRIPT_REQUIREMENTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("claim 用のスクリプト", re.compile(r"claim[^。]*スクリプト")),
    ("見つからない", re.compile(r"見つから(?:ない|ず|なかった)")),
    ("issue への着手をしない", re.compile(r"着手(?:を)?(?:せず|しない|しません)")),
    ("ユーザーに報告する", re.compile(r"ユーザー?に[^。]*報告")),
)

LEADING_RULE_MARKER = re.compile(r"<!-- (?:rule|subagent-rule):")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def body_lines(text: str) -> list[str]:
    """prompt ファイルの配送本文の行 (先頭の保守者向け HTML コメントと直後の空行を除く)。

    1 行目が `<!--` で始まり、rule マーカーではなく、`-->` で閉じている場合だけ先頭の
    コメントを除く。hook は本文を `$(...)` で読むため、末尾の空行も除く。
    """
    lines = text.splitlines()
    while lines and lines[-1] == "":
        lines.pop()
    if not lines or not lines[0].startswith("<!--") or LEADING_RULE_MARKER.match(lines[0]):
        return lines
    for index, line in enumerate(lines):
        searched = line[4:] if index == 0 else line
        position = searched.find("-->")
        if position < 0:
            continue
        rest = searched[position + 3 :]
        remaining = ([rest] if rest.strip() else []) + lines[index + 1 :]
        while remaining and not remaining[0].strip():
            remaining.pop(0)
        return remaining
    return lines


def rule_block(text: str, marker: str) -> str:
    """`marker` から次の `<!-- rule:` マーカーの手前までを返す (マーカー行を除く)。"""
    start = text.find(marker)
    if start < 0:
        return ""
    body_start = start + len(marker)
    end = text.find(RULE_MARKER_PREFIX, body_start)
    return text[body_start:] if end < 0 else text[body_start:end]


def strip_whitespace(text: str) -> str:
    return "".join(text.split())


class InjectionTestCase(unittest.TestCase):
    """inject-rules-part.sh を隔離した TMPDIR で実行する helper。"""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def run_part(self, plugin_dir: Path, part: str, run_name: str) -> str:
        """plugin_dir の inject-rules-part.sh で part を配送し、additionalContext を返す。

        実行ごとに新しい TMPDIR を使う (配送済みマーカーで 2 回目が無音になるのを避ける)。
        """
        state_root = self.work / f"state-{run_name}"
        home = self.work / f"home-{run_name}"
        state_root.mkdir()
        home.mkdir()
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(home),
            "TMPDIR": str(state_root),
        }
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": SESSION_ID}
        completed = subprocess.run(
            ["/bin/bash", str(plugin_dir / INJECT_SCRIPT_RELATIVE), part],
            cwd=self.work,
            env=env,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(
            completed.stdout.strip(), f"part {part} の配送が無い: stderr={completed.stderr!r}"
        )
        output = json.loads(completed.stdout)
        return output["hookSpecificOutput"]["additionalContext"]

    def placeholder_line_index(self, lines: list[str], source: Path) -> int:
        """配送本文の行のうち、プレースホルダを含む唯一の行の位置。"""
        indexes = [index for index, line in enumerate(lines) if PLACEHOLDER in line]
        self.assertEqual(
            1,
            len(indexes),
            f"{source} の配送本文でプレースホルダ {PLACEHOLDER} を含む行の数",
        )
        return indexes[0]

    def assert_only_line_differs(
        self, expected: list[str], actual: list[str], index: int, what: str
    ) -> None:
        """index の行のほかは expected と actual が同じ。"""
        self.assertEqual(len(expected), len(actual), f"{what}: 配送本文の行数が変わった")
        for number, (want, got) in enumerate(zip(expected, actual)):
            if number == index:
                continue
            self.assertEqual(want, got, f"{what}: {number + 1} 行目が変わった")


class ClaimScriptPlaceholderTest(unittest.TestCase):
    """always-3.md のプレースホルダ。"""

    def test_placeholder_appears_once_as_the_invocation(self) -> None:
        """プレースホルダはちょうど 1 回、`'{{CLAIM_ISSUE_SCRIPT_PATH}}' <N> '<branch>'` の形で
        現れる。"""
        text = read(ALWAYS_3)
        self.assertEqual(1, text.count(PLACEHOLDER), f"{PLACEHOLDER} の出現回数")
        self.assertIn(INVOCATION, text)

    def test_placeholder_is_in_the_issue_claim_rule(self) -> None:
        """プレースホルダは `rule:issue-claim` の節の中にある。"""
        self.assertTrue(
            INVOCATION in rule_block(read(ALWAYS_3), ISSUE_CLAIM_MARKER),
            f"`{ISSUE_CLAIM_MARKER}` の節に「{INVOCATION}」が無い",
        )


class ClaimScriptPathSubstitutionTest(InjectionTestCase):
    """claim-issue.sh があるときの配送。"""

    def test_part_3_replaces_the_placeholder_with_the_script_path(self) -> None:
        """part 3 の配送はプレースホルダを claim-issue.sh の絶対パスに置き換え、ほかの行は
        always-3.md の本文のまま配送する。"""
        expected = body_lines(read(ALWAYS_3))
        index = self.placeholder_line_index(expected, ALWAYS_3)
        context = self.run_part(PLUGIN_DIR, "3", "part3")
        self.assertNotIn(PLACEHOLDER, context)
        actual = context.splitlines()
        self.assert_only_line_differs(expected, actual, index, "part 3")

        prefix, suffix = expected[index].split(PLACEHOLDER)
        line = actual[index]
        self.assertTrue(
            line.startswith(prefix) and line.endswith(suffix) and len(line) > len(prefix + suffix),
            f"プレースホルダの前後が変わった: {line!r}",
        )
        substituted = line[len(prefix) : len(line) - len(suffix)]
        self.assertTrue(os.path.isabs(substituted), f"絶対パスではない: {substituted!r}")
        self.assertEqual(
            os.path.realpath(CLAIM_SCRIPT),
            os.path.realpath(substituted),
            "置き換えたパスが claim-issue.sh を指さない",
        )

    def test_part_3_fits_the_size_limit(self) -> None:
        """part 3 の配送本文 (置き換えた後の additionalContext) は 7,800 字 (Unicode code
        point 数) 以下。"""
        context = self.run_part(PLUGIN_DIR, "3", "part3-size")
        self.assertLessEqual(
            len(context), PART_3_SIZE_LIMIT, f"part 3 の配送本文の字数 ({len(context)} 字)"
        )

    def test_part_2_is_delivered_unchanged(self) -> None:
        """part 2 の配送は always-2.md の本文のまま。"""
        context = self.run_part(PLUGIN_DIR, "2", "part2")
        self.assertEqual(body_lines(read(ALWAYS_2)), context.splitlines())


class ClaimScriptMissingTest(InjectionTestCase):
    """claim-issue.sh が無いときの配送 (plugin のコピーから削除して実行する)。"""

    def setUp(self) -> None:
        super().setUp()
        self.plugin_copy = self.work / "agent-discipline"
        shutil.copytree(PLUGIN_DIR, self.plugin_copy)
        copied_script = self.plugin_copy / CLAIM_SCRIPT_RELATIVE
        if copied_script.exists():
            copied_script.unlink()
        self.assertFalse(copied_script.exists())

    def test_placeholder_line_becomes_a_stop_instruction(self) -> None:
        """プレースホルダを含む行全体が、claim 用のスクリプトが見つからないため issue への
        着手をせずユーザーに報告する、という 1 行に置き換わる。ほかの行は変わらない。"""
        always_3 = self.plugin_copy / PROMPTS_RELATIVE / "always-3.md"
        expected = body_lines(read(always_3))
        index = self.placeholder_line_index(expected, always_3)
        context = self.run_part(self.plugin_copy, "3", "part3")
        self.assertNotIn(PLACEHOLDER, context)
        actual = context.splitlines()
        self.assert_only_line_differs(expected, actual, index, "part 3 (スクリプトなし)")

        replaced = strip_whitespace(actual[index])
        for name, requirement in MISSING_SCRIPT_REQUIREMENTS:
            with self.subTest(requirement=name):
                self.assertRegex(replaced, requirement, f"置き換えた行: {actual[index]!r}")
        # 行全体を置き換える (プレースホルダの部分だけを置き換えて、前後を残さない)。
        prefix, suffix = (strip_whitespace(part) for part in expected[index].split(PLACEHOLDER))
        if prefix:
            self.assertFalse(
                replaced.startswith(prefix),
                f"プレースホルダの前の部分が残っている: {actual[index]!r}",
            )
        if suffix:
            self.assertNotIn(
                suffix, replaced, f"プレースホルダの後の部分が残っている: {actual[index]!r}"
            )

    def test_other_part_3_rules_are_delivered(self) -> None:
        """スクリプトが無くても、part 3 の他の rule は届く。"""
        context = self.run_part(self.plugin_copy, "3", "part3")
        for marker in (ISSUE_CLAIM_MARKER, *OTHER_PART_3_MARKERS):
            with self.subTest(marker=marker):
                self.assertIn(marker, context)

    def test_part_2_is_delivered_unchanged(self) -> None:
        """スクリプトが無くても、part 2 の配送は always-2.md の本文のまま。"""
        context = self.run_part(self.plugin_copy, "2", "part2")
        always_2 = self.plugin_copy / PROMPTS_RELATIVE / "always-2.md"
        self.assertEqual(body_lines(read(always_2)), context.splitlines())


if __name__ == "__main__":
    unittest.main()
