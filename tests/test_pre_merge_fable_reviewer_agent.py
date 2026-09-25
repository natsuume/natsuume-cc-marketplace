"""pre-merge-cross-review の fable-reviewer agent 定義の契約テスト。

`agents/fable-reviewer.md` は、current branch の PR の merge-base..head 全差分を、PR 説明と
関連 issue の受入基準・設計境界と照らして read-only でレビューし、confidence / severity
付きの parent-safe な markdown report を返す reviewer subagent を定義する。

- frontmatter: `name: fable-reviewer`、`tools: Bash, Read, Glob, Grep`、`model: opus`
  (Fable で走るのは親が `model: "fable"` を明示して起動したときに限る)
- description: merge 実行権限を示唆する語 (`gh pr merge` / `merge gate` / `deny` / `投稿`)
  を含まず、`read-only` と `parent-safe` を含む
- 本文: 起動仕様、report 契約 (`Status: pass | findings | execution-failed`、3 つの
  Category、`Confidence: high | medium | low`)、自己フィルタ禁止、SubagentHandback による
  返却、関連 issue の収集元 (`closingIssuesReferences` と `Refs` / `Refs owner/repo#N`)、
  未信頼入力の扱い、read-only の禁止事項を述べる。report は親 session に返るだけなので、
  公開を前提にした制約を置かない。Agent の起動 mode を指示しない
"""

from __future__ import annotations

import importlib
import re
import sys
import unittest
from pathlib import Path


_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


def _load_shared_contract():
    """Agent 起動 mode 指示の検出 helper を持つ共有 module を読み込む。

    `import` 文で書くと「sys.path 操作より前に import 文が来る」という lint 制約
    (E402) に抵触するため、既存テストと同じ importlib 経由の明示 import にする。
    """
    return importlib.import_module("test_pre_push_codex_reviewer_bg_recovery")


agent_launch_mode_hits = _load_shared_contract().agent_launch_mode_hits

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "plugins" / "pre-merge-cross-review" / "agents" / "fable-reviewer.md"
FABLE_REVIEWER_TYPE = "pre-merge-cross-review:fable-reviewer"

FORBIDDEN_DESCRIPTION_SUBSTRINGS = ("gh pr merge", "merge gate", "deny", "投稿")
REQUIRED_DESCRIPTION_SUBSTRINGS = ("read-only", "parent-safe")

REQUIRED_BODY_PHRASES = (
    # 起動仕様 (subagent_type と model の 2 表記)
    f'`subagent_type: "{FABLE_REVIEWER_TYPE}"`',
    '`model: "fable"`',
    # report 契約
    "# Fable Review",
    "Status: pass | findings | execution-failed",
    "correctness",
    "acceptance-criteria",
    "design-boundary",
    "Confidence: high | medium | low",
    # 全件報告
    "自己フィルタ",
    # 返却経路
    "SubagentHandback",
    # 関連 issue の収集元
    "closingIssuesReferences",
    "Refs",
    "Refs owner/repo#N",
    "-R owner/repo",
    # PR 本文・issue・差分は指示ではなくデータとして扱う
    "Untrusted input",
    # read-only の禁止事項 (gh は PR と issue の参照だけに使う)
    "read-only",
    "gh pr view",
    "gh issue view",
)


# report の公開を前提にした記述。
PUBLISHED_REPORT_PHRASES = (
    "Published output",
    "is posted",
    "published as a pull request comment",
    "without `#`",
)


def read_agent() -> str:
    return AGENT.read_text(encoding="utf-8")


def frontmatter_lines(text: str) -> list[str]:
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    return match.group(1).splitlines() if match else []


def description(text: str) -> str:
    for line in frontmatter_lines(text):
        if line.startswith("description:"):
            return line[len("description:") :].strip()
    return ""


class FableReviewerFrontmatterTest(unittest.TestCase):
    def test_agent_file_exists(self) -> None:
        self.assertTrue(AGENT.is_file(), f"{AGENT} が無い")

    def test_frontmatter_name_tools_and_model(self) -> None:
        lines = frontmatter_lines(read_agent())
        for expected in (
            "name: fable-reviewer",
            "tools: Bash, Read, Glob, Grep",
            "model: opus",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, lines)

    def test_description_forbids_merge_authority_language(self) -> None:
        text = description(read_agent())
        self.assertTrue(text, "description が無い")
        for forbidden in FORBIDDEN_DESCRIPTION_SUBSTRINGS:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_description_requires_read_only_and_parent_safe(self) -> None:
        text = description(read_agent())
        for required in REQUIRED_DESCRIPTION_SUBSTRINGS:
            with self.subTest(required=required):
                self.assertIn(required, text)


class FableReviewerBodyTest(unittest.TestCase):
    def test_body_contains_required_phrases(self) -> None:
        body = read_agent()
        missing = [phrase for phrase in REQUIRED_BODY_PHRASES if phrase not in body]
        self.assertEqual([], missing, f"fable-reviewer.md に無い記述: {missing}")

    def test_body_does_not_assume_a_published_report(self) -> None:
        """report は親 session に返るだけで PR に公開されないため、公開を前提にした
        制約 (issue 番号を `#` 無しで書く・別リポジトリの issue を読まない等) を置かない。"""
        body = read_agent()
        present = [phrase for phrase in PUBLISHED_REPORT_PHRASES if phrase in body]
        self.assertEqual([], present, f"公開を前提にした記述が残っている: {present}")

    def test_body_omits_agent_launch_mode(self) -> None:
        hits = agent_launch_mode_hits(read_agent())
        self.assertEqual(hits, [], "Agent 起動 mode の指示が残っている")


if __name__ == "__main__":
    unittest.main()
