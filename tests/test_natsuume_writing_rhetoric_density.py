"""natsuume-writing: 構造化・レトリックの密度制御と読解体験の過剰設計の回避 (契約テスト)。

writing-rules.md の共通コアに、文章全体でのレトリック・論証パターン・構造化の密度を
制御する上位原則と、読解体験を過剰に設計しない規則を置き、draft / review skill が
それを全文スコープで適用することを固定する。

検査する契約:
- 通底原則に密度制御の原則 (5 番目) があり、通底原則 2 の「疑問の先回り」が
  網羅的な反論処理を求めない形に限定されている
- 共通コアのセクション 10 が密度制御を扱い、典型例 (二項対立・概念命名・過剰な構造化・
  決め台詞・比喩の使い回し・網羅的な反論処理・抽象概念の主語・断片文・予告・構造実況・
  発見や転回の演出・先回りの抽象命題・自己ヘッジ・太字の多用・直訳的な言い回し) を含む
- たたき台生成時の特則はセクション 11 に移り、skill からの節番号参照が一致している
- セクション 7 の構造化規則が無条件適用ではなく、読者の理解に必要な場合に限定されている
- core-summary.md (SessionStart 注入) に密度制御の原則の要点がある
- review skill が全文スコープの密度チェックと、削っても情報・論理が失われないメタ文の確認と、
  見直しを促す目安を持つ
- draft skill が同じ論証テンプレートの反復と、予告・本説明・再要約の重複を避ける
- rules/expression-watchlist.md が、生成 AI の普及後に増えた直訳調・比喩的な語と
  抽象的な漢語・評価語を見直し候補 (使用禁止ではない) として列挙し、話題語を含まない。
  writing-rules.md と draft / review skill がこの一覧を参照する
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "plugins" / "natsuume-writing"
WRITING_RULES = PLUGIN_DIR / "rules" / "writing-rules.md"
CORE_SUMMARY = PLUGIN_DIR / "rules" / "core-summary.md"
DRAFT_SKILL = PLUGIN_DIR / "skills" / "draft" / "SKILL.md"
REVIEW_SKILL = PLUGIN_DIR / "skills" / "review" / "SKILL.md"
WATCHLIST = PLUGIN_DIR / "rules" / "expression-watchlist.md"

DENSITY_PRINCIPLE = "読者の理解に必要な分だけ整える"
DENSITY_SECTION_HEADING = "## 10. 構造化・レトリックの密度"
DRAFT_SPECIAL_SECTION_HEADING = "## 11. たたき台生成時の特則"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    """`heading` 行から次の同レベル以上の見出し (または区切り線) までを返す。"""
    start = text.index(heading)
    rest = text[start + len(heading) :]
    match = re.search(r"^(## |---$)", rest, flags=re.MULTILINE)
    return rest[: match.start()] if match else rest


class WritingRulesPrincipleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(WRITING_RULES)
        self.principles = section(self.text, "## 1. 通底原則")

    def test_density_principle_is_fifth_principle(self) -> None:
        self.assertRegex(
            self.principles, r"(?m)^5\. \*\*" + re.escape(DENSITY_PRINCIPLE)
        )

    def test_principle_two_does_not_require_exhaustive_rebuttal(self) -> None:
        principle_two = next(
            line for line in self.principles.splitlines() if line.startswith("2. ")
        )
        self.assertIn("網羅的", principle_two)


class WritingRulesDensitySectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(WRITING_RULES)

    def test_density_section_exists_before_draft_special_section(self) -> None:
        self.assertIn(DENSITY_SECTION_HEADING, self.text)
        self.assertIn(DRAFT_SPECIAL_SECTION_HEADING, self.text)
        self.assertLess(
            self.text.index(DENSITY_SECTION_HEADING),
            self.text.index(DRAFT_SPECIAL_SECTION_HEADING),
        )
        self.assertNotIn("## 10. たたき台生成時の特則", self.text)

    def test_density_section_lists_typical_patterns(self) -> None:
        body = section(self.text, DENSITY_SECTION_HEADING)
        for keyword in (
            "二項対立",
            "命名",
            "分類",
            "決め台詞",
            "比喩",
            "想定反論",
            "抽象概念を主語",
            "断片文",
            "予告",
            "構造の実況",
            "意外性",
            "抽象命題",
            "ヘッジ",
            "太字",
            "直訳",
        ):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, body)

    def test_density_section_is_about_repetition_not_single_use(self) -> None:
        body = section(self.text, DENSITY_SECTION_HEADING)
        self.assertIn("記事全体", body)
        self.assertIn("反復", body)

    def test_density_section_names_translationese_examples(self) -> None:
        body = section(self.text, DENSITY_SECTION_HEADING)
        for example in ("刺さる", "効く", "倒れる"):
            with self.subTest(example=example):
                self.assertIn(example, body)

    def test_density_section_exempts_code_and_list_introductions(self) -> None:
        body = section(self.text, DENSITY_SECTION_HEADING)
        self.assertIn("セクション 8", body)

    def test_density_section_rationale_has_no_statistics(self) -> None:
        body = section(self.text, DENSITY_SECTION_HEADING)
        self.assertNotRegex(body, r"\d+(\.\d+)?\s*(倍|%)")


class WritingRulesStructuringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.structure = section(read(WRITING_RULES), "## 7. 構成・列挙の型")

    def test_mechanical_structuring_check_is_removed(self) -> None:
        self.assertNotIn("3 段落以上構造化要素なしに続いたら", self.structure)
        self.assertNotIn("地の文で流さない", self.structure)

    def test_structuring_is_conditional_on_reader_understanding(self) -> None:
        self.assertIn("理解が速くなる", self.structure)
        self.assertIn("論理のつながり", self.structure)


class CoreSummaryTest(unittest.TestCase):
    def test_core_summary_has_density_principle(self) -> None:
        self.assertIn(DENSITY_PRINCIPLE, read(CORE_SUMMARY))


class SkillSectionReferenceTest(unittest.TestCase):
    def test_skills_reference_common_core_zero_to_ten(self) -> None:
        for path in (DRAFT_SKILL, REVIEW_SKILL):
            with self.subTest(skill=path.parent.name):
                text = read(path)
                self.assertIn("セクション 0〜10", text)
                self.assertNotIn("セクション 0〜9", text)

    def test_draft_skill_references_special_section_eleven(self) -> None:
        text = read(DRAFT_SKILL)
        self.assertIn("セクション 11", text)
        self.assertNotIn("セクション 10 (たたき台生成時の特則)", text)


class ReviewSkillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(REVIEW_SKILL)

    def test_review_checks_density_over_whole_text(self) -> None:
        self.assertIn("全文スコープ", self.text)
        self.assertIn("1 回の使用だけでは指摘しない", self.text)

    def test_review_checks_removable_meta_sentences(self) -> None:
        self.assertIn("削っても情報・論理が失われない", self.text)

    def test_review_has_rereading_thresholds_not_violations(self) -> None:
        self.assertIn("見直しを促す目安", self.text)
        self.assertIn("太字", self.text)


class ExpressionWatchlistTest(unittest.TestCase):
    """見直し候補の語の一覧 (直訳調・比喩的な語と、抽象的な漢語・評価語)。"""

    def setUp(self) -> None:
        self.text = read(WATCHLIST)

    def test_watchlist_has_translationese_and_abstract_sections(self) -> None:
        self.assertIn("## 直訳調・比喩的な語", self.text)
        self.assertIn("## 抽象的な漢語・評価語", self.text)

    def test_watchlist_contains_data_backed_examples(self) -> None:
        translationese = section(self.text, "## 直訳調・比喩的な語")
        for example in ("効く", "壊れる", "瞬間"):
            with self.subTest(example=example):
                self.assertIn(example, translationese)

    def test_watchlist_entries_exclude_topic_words_and_digits(self) -> None:
        entries = [line[2:] for line in self.text.splitlines() if line.startswith("- ")]
        self.assertTrue(entries)
        for entry in entries:
            with self.subTest(entry=entry):
                for topic_word in ("エージェント", "プロンプト", "LLM", "AI"):
                    self.assertNotIn(topic_word, entry)
                self.assertNotRegex(entry, r"[0-9０-９A-Za-zＡ-Ｚａ-ｚ]")

    def test_watchlist_is_not_a_ban_list(self) -> None:
        self.assertIn("使用禁止ではない", self.text)

    def test_rules_and_skills_reference_watchlist(self) -> None:
        for path in (WRITING_RULES, DRAFT_SKILL, REVIEW_SKILL):
            with self.subTest(file=str(path.relative_to(PLUGIN_DIR))):
                self.assertIn("expression-watchlist.md", read(path))

    def test_density_section_names_data_backed_translationese(self) -> None:
        body = section(read(WRITING_RULES), DENSITY_SECTION_HEADING)
        self.assertIn("壊れる", body)


class DraftSkillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(DRAFT_SKILL)

    def test_draft_avoids_repeating_argument_template(self) -> None:
        self.assertIn("同じ論証テンプレート", self.text)

    def test_draft_avoids_preview_explain_recap_duplication(self) -> None:
        self.assertIn("予告・本説明・再要約", self.text)

    def test_draft_keeps_volume_calibration(self) -> None:
        self.assertIn("分量はコメントの指示量に比例", self.text)


if __name__ == "__main__":
    unittest.main()
