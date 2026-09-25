"""natsuume-writing: 傾向情報 (構造化・レトリックの密度と見直し候補の語) の一般化 (契約テスト)。

生成 AI の普及前後の比較から得た傾向情報を、natsuume 名義の技術文書に限らず、
成果物として書く日本語の文章すべてに適用する。natsuume 固有の文体
(です・ます調・「筆者」・表記基準・媒体プロファイル) は技術文書だけに適用する。

検査する契約:
- rules/general-writing.md が文章作成一般のルールとして存在し、適用対象 (成果物の文章) と
  対象外 (チャットでの応答) を定め、natsuume 固有の文体を定めない
- writing-rules.md が general-writing.md を前提として適用し、競合時は writing-rules.md を優先する
- core-summary.md (SessionStart 注入) が文章作成一般と natsuume 名義の技術文書の 2 層で書かれ、
  一般ルールの全文と見直し候補の一覧への参照を持つ
- inject-core.sh が rules ディレクトリの絶対パスを `(参照パス)` 行として注入本文に付け足す
- expression-watchlist.md が技術記事に限らない見直し候補として general-writing.md を基準に扱われる
- review skill が技術文書以外の文章を受け付け、文章の種類によって適用ルールを切り替え、
  観点 3 を事実の正確さとして扱う
- draft skill が general-writing.md を読み込む
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "plugins" / "natsuume-writing"
RULES_DIR = PLUGIN_DIR / "rules"
GENERAL_RULES = RULES_DIR / "general-writing.md"
WRITING_RULES = RULES_DIR / "writing-rules.md"
CORE_SUMMARY = RULES_DIR / "core-summary.md"
WATCHLIST = RULES_DIR / "expression-watchlist.md"
INJECT_CORE = PLUGIN_DIR / "hooks" / "scripts" / "inject-core.sh"
DRAFT_SKILL = PLUGIN_DIR / "skills" / "draft" / "SKILL.md"
REVIEW_SKILL = PLUGIN_DIR / "skills" / "review" / "SKILL.md"

GENERAL_LAYER_HEADING = "## 文章作成一般のルール"
TECH_LAYER_HEADING = "## natsuume 名義の技術文書のルール"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class GeneralRulesScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(GENERAL_RULES)

    def test_scope_covers_written_artifacts(self) -> None:
        for artifact in ("ドキュメント", "PR", "コミットメッセージ", "メール"):
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, self.text)

    def test_scope_excludes_chat_responses(self) -> None:
        self.assertIn("チャット", self.text)
        self.assertIn("対象外", self.text)

    def test_general_rules_do_not_define_natsuume_style(self) -> None:
        for natsuume_style in ("筆者", "だ・である"):
            with self.subTest(natsuume_style=natsuume_style):
                self.assertNotIn(natsuume_style, self.text)

    def test_general_rules_defer_to_project_conventions(self) -> None:
        self.assertIn("規約", self.text)

    def test_general_rules_point_to_writing_rules_for_tech_documents(self) -> None:
        self.assertIn("writing-rules.md", self.text)


class WritingRulesLayeringTest(unittest.TestCase):
    def test_scope_section_applies_general_rules_as_base(self) -> None:
        text = read(WRITING_RULES)
        scope = text[: text.index("## 1. 通底原則")]
        self.assertIn("general-writing.md", scope)
        self.assertIn("優先", scope)


class CoreSummaryLayeringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(CORE_SUMMARY)

    def test_core_summary_has_general_and_tech_layers(self) -> None:
        self.assertIn(GENERAL_LAYER_HEADING, self.text)
        self.assertIn(TECH_LAYER_HEADING, self.text)
        self.assertLess(
            self.text.index(GENERAL_LAYER_HEADING),
            self.text.index(TECH_LAYER_HEADING),
        )

    def test_core_summary_general_layer_excludes_chat(self) -> None:
        general = self.text[
            self.text.index(GENERAL_LAYER_HEADING) : self.text.index(TECH_LAYER_HEADING)
        ]
        self.assertIn("チャット", general)
        self.assertIn("general-writing.md", general)
        self.assertIn("expression-watchlist.md", general)

    def test_core_summary_no_longer_limits_whole_summary_to_tech_documents(
        self,
    ) -> None:
        self.assertNotIn("執筆と無関係な作業では無視してよい", self.text)


class InjectCoreReferencePathTest(unittest.TestCase):
    def test_injected_context_ends_with_rules_dir_path(self) -> None:
        result = subprocess.run(
            ["sh", str(INJECT_CORE)],
            input="{}",
            capture_output=True,
            text=True,
            check=True,
        )
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.startswith(read(CORE_SUMMARY).rstrip("\n")))
        self.assertEqual(
            context.splitlines()[-1], f"(参照パス) {RULES_DIR.resolve()}"
        )


class WatchlistGeneralScopeTest(unittest.TestCase):
    def test_watchlist_is_judged_by_general_rules(self) -> None:
        header = read(WATCHLIST).split("\n## ", 1)[0]
        self.assertIn("general-writing.md", header)
        self.assertIn("文章作成一般", header)


class ReviewSkillGeneralDocumentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(REVIEW_SKILL)

    def test_review_reads_general_rules(self) -> None:
        self.assertIn("general-writing.md", self.text)

    def test_review_distinguishes_document_kind(self) -> None:
        self.assertIn("natsuume 名義の技術文書", self.text)
        self.assertIn("それ以外の文章", self.text)

    def test_review_treats_viewpoint_three_as_factual_accuracy(self) -> None:
        self.assertIn("事実の正確さ", self.text)

    def test_review_description_is_not_limited_to_tech_articles(self) -> None:
        description = next(
            line for line in self.text.splitlines() if line.startswith("description:")
        )
        self.assertNotIn("技術記事・技術書の原稿を", description)


class DraftSkillGeneralRulesTest(unittest.TestCase):
    def test_draft_reads_general_rules(self) -> None:
        self.assertIn("general-writing.md", read(DRAFT_SKILL))


if __name__ == "__main__":
    unittest.main()
