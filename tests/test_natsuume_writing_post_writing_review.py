"""natsuume-writing: 書いた後のレビュー (契約テスト)。

文書ファイルと PR・issue の本文を書いたときは、完了とする前に review skill を 1 回実行し、
指摘を反映してから完了とする。

検査する契約:
- general-writing.md セクション 5 が、対象 (文書ファイル・PR・issue の本文の地の文)、対象外
  (コードコメント・コミットメッセージ・地の文を変えない編集・作業用のファイル)、
  review の 1 回実行と指摘の反映、PR・issue の本文を投稿前の下書きファイルでレビューすること、
  Codex による独立調査をリポジトリの外の事実を述べた主張がある場合に限ることを定める
- core-summary.md (SessionStart 注入) の文章作成一般の層が同じ指示を持ち、
  general-writing.md セクション 5 を参照する
- review skill が PR・issue の本文の下書きファイルを対象に含め、セクション 5 に従った
  レビューでは書いた側が指摘を反映し、それ以外ではユーザーの指示を待つ。セクション 5 に
  従ったレビューでは、観点 3 の独立調査をリポジトリの外の事実を述べた主張がある場合に限る
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "plugins" / "natsuume-writing"
RULES_DIR = PLUGIN_DIR / "rules"
GENERAL_RULES = RULES_DIR / "general-writing.md"
CORE_SUMMARY = RULES_DIR / "core-summary.md"
REVIEW_SKILL = PLUGIN_DIR / "skills" / "review" / "SKILL.md"

REVIEW_SECTION_HEADING = "## 5. 書いた後のレビュー"
REVIEW_COMMAND = "/natsuume-writing:review"
GENERAL_LAYER_HEADING = "## 文章作成一般のルール"
TECH_LAYER_HEADING = "## natsuume 名義の技術文書のルール"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class GeneralRulesPostWritingReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        text = read(GENERAL_RULES)
        self.assertIn(REVIEW_SECTION_HEADING, text)
        self.section = text[text.index(REVIEW_SECTION_HEADING) :]

    def test_section_is_last_and_follows_vocabulary_section(self) -> None:
        text = read(GENERAL_RULES)
        self.assertLess(
            text.index("## 4. 強調と語彙"), text.index(REVIEW_SECTION_HEADING)
        )
        self.assertNotIn("\n## ", self.section[len(REVIEW_SECTION_HEADING) :])

    def test_section_runs_review_once_and_applies_findings(self) -> None:
        self.assertIn(REVIEW_COMMAND, self.section)
        self.assertIn("1 回", self.section)
        self.assertIn("反映してから完了", self.section)

    def test_section_covers_document_files_and_pr_issue_bodies(self) -> None:
        for target in ("文書ファイル", "PR・issue の本文"):
            with self.subTest(target=target):
                self.assertIn(target, self.section)

    def test_section_reviews_pr_issue_bodies_before_posting(self) -> None:
        self.assertIn("投稿する前の下書き", self.section)

    def test_section_excludes_code_comments_and_commit_messages(self) -> None:
        self.assertIn("コードコメントとコミットメッセージ", self.section)
        self.assertIn("対象外", self.section)

    def test_section_reports_unapplied_findings(self) -> None:
        self.assertIn("反映しなかった指摘", self.section)

    def test_section_targets_body_text(self) -> None:
        self.assertIn("地の文を書いたとき", self.section)

    def test_section_excludes_mechanical_edits_and_working_files(self) -> None:
        for excluded in ("地の文を変えない編集", "作業用のファイル"):
            with self.subTest(excluded=excluded):
                self.assertIn(excluded, self.section)

    def test_section_limits_codex_investigation_to_external_facts(self) -> None:
        self.assertIn("Codex による独立調査", self.section)
        self.assertIn("リポジトリの外の事実", self.section)


class CoreSummaryPostWritingReviewTest(unittest.TestCase):
    def test_general_layer_instructs_review_after_writing(self) -> None:
        text = read(CORE_SUMMARY)
        general = text[
            text.index(GENERAL_LAYER_HEADING) : text.index(TECH_LAYER_HEADING)
        ]
        self.assertIn(REVIEW_COMMAND, general)
        self.assertIn("1 回", general)
        self.assertIn("general-writing.md` セクション 5", general)
        self.assertIn("PR・issue の本文", general)
        for excluded in (
            "コードコメントとコミットメッセージ",
            "地の文を変えない編集",
            "作業用のファイル",
        ):
            with self.subTest(excluded=excluded):
                self.assertIn(excluded, general)
        self.assertIn("は対象外です", general)


class ReviewSkillPostWritingReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(REVIEW_SKILL)

    def test_review_accepts_pr_issue_body_drafts(self) -> None:
        self.assertIn("PR・issue の本文は、投稿前の下書き", self.text)

    def test_review_still_does_not_modify_target_file(self) -> None:
        self.assertIn("対象ファイルは**一切変更しません**", self.text)

    def test_writer_applies_findings_under_section_five(self) -> None:
        self.assertIn("general-writing.md` セクション 5", self.text)
        self.assertIn("書いた側の作業として指摘を反映", self.text)

    def test_other_reviews_wait_for_user_instruction(self) -> None:
        self.assertIn("ユーザーから明示的な指示があってから修正", self.text)

    def test_section_five_review_runs_codex_only_for_external_facts(self) -> None:
        factual = self.text[self.text.index("### 2-3.") : self.text.index("### 2-4.")]
        self.assertIn("general-writing.md` セクション 5", factual)
        self.assertIn("リポジトリの外の事実", factual)
        self.assertIn("メインセッションの調査だけで行ったこと", factual)

    def test_codex_condition_gates_step_before_request_items(self) -> None:
        factual = self.text[self.text.index("### 2-3.") : self.text.index("### 2-4.")]
        condition = factual.index("リポジトリの外の事実")
        request_items = factual.index("runner に渡す request には次を含めます")
        self.assertLess(condition, request_items)
        self.assertNotIn("\n   - **境界: `general-writing.md` セクション 5", factual)


if __name__ == "__main__":
    unittest.main()
