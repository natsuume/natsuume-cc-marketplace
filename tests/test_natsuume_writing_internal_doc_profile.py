"""natsuume-writing: 社内技術文書プロファイル (契約テスト)。

natsuume が社内向けに書く技術文書を、書籍・ブログと並ぶ媒体として扱う。

検査する契約:
- writing-rules.md の適用範囲と構成が社内技術文書を含み、社内技術文書プロファイルが
  ブログプロファイルの後にある
- プロファイルが、文末 (常体と体言止め、です・ます調と「〜である」を使わない)、
  箇条書き中心、人称と読者への働きかけを使わないこと、構成・コードの定型を求めないこと、
  シグネチャ表現を使わないことを定め、表記の商業版基準 (共通コア 5) は上書きしない
- core-summary.md (SessionStart 注入) の技術文書の層が、社内技術文書の文末と
  プロファイルの優先を伝える
- outline / draft / review の各 skill が媒体の選択肢に社内技術文書を含め、
  記事タイプを省略できる媒体に社内技術文書を含める
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "plugins" / "natsuume-writing"
RULES_DIR = PLUGIN_DIR / "rules"
WRITING_RULES = RULES_DIR / "writing-rules.md"
GENERAL_RULES = RULES_DIR / "general-writing.md"
CORE_SUMMARY = RULES_DIR / "core-summary.md"
SKILLS_DIR = PLUGIN_DIR / "skills"
OUTLINE_SKILL = SKILLS_DIR / "outline" / "SKILL.md"
DRAFT_SKILL = SKILLS_DIR / "draft" / "SKILL.md"
REVIEW_SKILL = SKILLS_DIR / "review" / "SKILL.md"

PROFILE_HEADING = "## 社内技術文書プロファイル (共通コアへの差分)"
MEDIUM = "社内技術文書"
MEDIA_CHOICES = "書籍 / 企業ブログ / 個人ブログ / 社内技術文書"
TYPE_OPTIONAL_MEDIA = "媒体 = 書籍 / 社内技術文書 のとき"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class WritingRulesScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        text = read(WRITING_RULES)
        self.scope = text[: text.index("## 1. 通底原則")]

    def test_scope_covers_internal_documents(self) -> None:
        self.assertIn("社内技術文書の**地の文**", self.scope)

    def test_structure_lists_three_profiles(self) -> None:
        self.assertIn("(書籍 / ブログ / 社内技術文書)", self.scope)


class InternalDocProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        text = read(WRITING_RULES)
        self.assertIn(PROFILE_HEADING, text)
        self.text = text
        self.profile = text[text.index(PROFILE_HEADING) :]

    def test_profile_follows_blog_profile(self) -> None:
        self.assertLess(
            self.text.index("## ブログプロファイル"),
            self.text.index(PROFILE_HEADING),
        )
        self.assertNotIn("\n## ", self.profile[len(PROFILE_HEADING) :])

    def test_profile_uses_plain_form_and_noun_endings(self) -> None:
        for phrase in ("「〜する」「〜した」「〜だった」", "体言止め"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.profile)

    def test_profile_rejects_desu_masu_and_dearu(self) -> None:
        self.assertIn("です・ます調は使わない", self.profile)
        self.assertIn("「〜である」「〜のである」は使わず", self.profile)

    def test_profile_prefers_bullet_points(self) -> None:
        self.assertIn("**箇条書き中心**", self.profile)
        self.assertIn("1 項目に 1 つの事実", self.profile)
        self.assertIn(
            "`general-writing.md` セクション 2 の「分類・対称化」は箇条書きの項目に、"
            "「演出的な断片文」は文末の規則による体言止めの文に適用しない",
            self.profile,
        )

    def test_profile_drops_person_and_reader_address(self) -> None:
        self.assertIn("**人称と読者への働きかけ**", self.profile)
        self.assertIn("一人称 (「筆者」を含む)", self.profile)
        self.assertIn("「〜してみましょう」", self.profile)
        self.assertIn("で確認済み", self.profile)

    def test_profile_drops_structure_and_code_templates(self) -> None:
        self.assertIn("**構成・コードの定型を求めない**", self.profile)
        for template in ("順番に見ていきましょう", "予告文と回収文"):
            with self.subTest(template=template):
                self.assertIn(template, self.profile)

    def test_profile_drops_signature_expressions(self) -> None:
        self.assertIn("**シグネチャ表現を使わない**", self.profile)

    def test_existing_conventions_override_profile_style(self) -> None:
        self.assertIn(
            "規約が定めていない事項はこのプロファイルの規則に従う", self.profile
        )
        self.assertIn("規約が無いときの既定", self.profile)
        self.assertIn("outline コメントの `文体=`", self.profile)

    def test_qa_questions_may_use_question_mark(self) -> None:
        self.assertIn("Q&A の問いの項目では「？」を使ってよい", self.profile)

    def test_profile_keeps_notation_rules(self) -> None:
        self.assertNotIn("共通コア 5", self.profile)
        self.assertNotIn("表記", self.profile)

    def test_general_rules_name_internal_documents(self) -> None:
        self.assertIn("テックブログ・技術書・社内技術文書", read(GENERAL_RULES))


class CoreSummaryInternalDocTest(unittest.TestCase):
    def test_tech_layer_mentions_internal_doc_profile(self) -> None:
        text = read(CORE_SUMMARY)
        tech = text[text.index("## natsuume 名義の技術文書のルール") :]
        self.assertIn("テックブログ・技術書・社内技術文書", tech)
        self.assertIn("社内技術文書プロファイル", tech)
        self.assertIn("常体", tech)
        self.assertIn("通底原則 1・2 と文末表現の要点より優先", tech)
        self.assertIn("構成とコードの定型", tech)
        self.assertIn("は求めません", tech)
        self.assertNotIn("構成とコードの定型・シグネチャ表現は使いません", tech)
        self.assertIn("Q&A の問いには「？」を使ってよい", tech)
        self.assertIn(
            "「何でも分類・列挙する構成を重ねない」は箇条書きの項目には適用しません",
            tech,
        )
        self.assertIn("「筆者」ではなく「＜環境・バージョン・日付＞で確認済み」", tech)
        self.assertIn(
            "社内の規約が定めている事項はそちらに従い、定めていない事項は", tech
        )
        self.assertIn("書籍/ブログ/社内技術文書の媒体プロファイル", tech)


class SkillsInternalDocMediumTest(unittest.TestCase):
    def test_outline_offers_internal_doc_medium(self) -> None:
        text = read(OUTLINE_SKILL)
        self.assertIn(f"媒体: {MEDIA_CHOICES}", text)
        self.assertIn("媒体 = 書籍 / 社内技術文書 のときは省略可", text)
        self.assertIn("媒体=社内技術文書 / 文体=プロファイル / 想定読者=", text)
        self.assertIn("文体 (媒体 = 社内技術文書 のときのみ)", text)
        self.assertIn(
            "文体 = 社内規約 で規約やテンプレートが構成を定めているときは、それに合わせた構成案",
            text,
        )
        description = next(
            line for line in text.splitlines() if line.startswith("description:")
        )
        self.assertIn(MEDIUM, description)

    def test_draft_offers_internal_doc_medium(self) -> None:
        text = read(DRAFT_SKILL)
        self.assertIn(f"媒体 ({MEDIA_CHOICES})", text)
        self.assertIn("社内技術文書プロファイル", text)
        self.assertIn("で確認済み", text)
        self.assertIn("同じく TODO コメントとプレースホルダーを伴う形で残します", text)
        self.assertIn("文体 (媒体 = 社内技術文書 のときのみ。", text)
        self.assertIn("判断できない場合にだけユーザーに確認します", text)
        self.assertIn(
            "媒体 = 社内技術文書 で、規約が箇条書きの使い方を定めていないときは、社内技術文書プロファイルに従って箇条書きの項目で書き",
            text,
        )
        self.assertIn("箇条書き中心に従い、この項目を適用しない", text)
        description = next(
            line for line in text.splitlines() if line.startswith("description:")
        )
        self.assertIn(MEDIUM, description)

    def test_review_offers_internal_doc_medium(self) -> None:
        text = read(REVIEW_SKILL)
        self.assertIn(f"媒体 ({MEDIA_CHOICES})", text)
        self.assertIn(TYPE_OPTIONAL_MEDIA, text)
        self.assertIn("(書籍 / ブログ / 社内技術文書、", text)
        self.assertIn(MEDIUM, text[: text.index("## 1. 入力の確認")])
        self.assertIn(
            "規約が箇条書きの使い方を定めていなければ箇条書きの項目を「分類・対称化」として、"
            "規約が文末を定めていなければ体言止めの文を「演出的な断片文」として指摘しない",
            text,
        )
        outline = read(OUTLINE_SKILL)
        self.assertIn(
            "(構成・文末・表記・箇条書きの使い方など) が 1 つでもあれば", outline
        )
        self.assertIn("文体 (媒体 = 社内技術文書 のときのみ。", text)
        notation = text[text.index("### 2-4.") : text.index("## 3. 指摘一覧の提示")]
        self.assertIn(
            "媒体 = 社内技術文書 で文体 = 社内規約 のときは、規約が表記を定めていればその規約で判定します",
            notation,
        )
        style_rules = text[text.index("### 2-1.") : text.index("### 2-2.")]
        self.assertIn(
            "媒体 = 社内技術文書 で文体 = 社内規約 のときは、規約が定める事項はその規約",
            style_rules,
        )

    def test_draft_applies_convention_when_loading_rules(self) -> None:
        text = read(DRAFT_SKILL)
        rules_step = text[
            text.index("## 2. 執筆ルールの読み込み") : text.index("## 3.")
        ]
        self.assertIn(
            "媒体 = 社内技術文書 で文体 = 社内規約 のときは、規約が定める事項はその規約",
            rules_step,
        )


if __name__ == "__main__":
    unittest.main()
