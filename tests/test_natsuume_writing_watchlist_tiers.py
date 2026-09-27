"""natsuume-writing: 見直し候補の語の区分 (契約テスト)。

見直し候補の語の一覧 (rules/expression-watchlist.md) を、人間の文章 (AI 以前の技術記事と小説) での
使われ方と、AI 以後の上乗せの大きさにもとづいて区分し、区分ごとに指示の強さを変える。

検査する契約:
- expression-watchlist.md の最上位の見出しが 4 区分 (強く抑制 / 適度に抑制 / 物語では自然 /
  技術文書では自然) で、各区分の直下に区分ごとの扱いが書かれている
- 各区分の中で、直訳調・比喩的な語と抽象的な漢語・評価語の分類を保つ
- 上乗せが実質的でない語 (除外区分) を一覧に載せない
- 強く抑制する区分に、言い回し単位の項目 (### 言い回し) がある
- 冒頭の説明が、人間の文章の基準として小説を使う現在の計測方法を述べる
- core-summary.md (SessionStart 注入) が強く抑制する語をすべて列挙する
- general-writing.md・review skill・draft skill が区分ごとの扱いの違いを参照する
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "plugins" / "natsuume-writing"
RULES_DIR = PLUGIN_DIR / "rules"
WATCHLIST = RULES_DIR / "expression-watchlist.md"
GENERAL_RULES = RULES_DIR / "general-writing.md"
CORE_SUMMARY = RULES_DIR / "core-summary.md"
DRAFT_SKILL = PLUGIN_DIR / "skills" / "draft" / "SKILL.md"
REVIEW_SKILL = PLUGIN_DIR / "skills" / "review" / "SKILL.md"

STRONG = "## 強く抑制する語"
MODERATE = "## 適度に抑制する語"
NARRATIVE = "## 物語では自然な語"
TECHNICAL = "## 技術文書では自然な語"
TIER_HEADINGS = [STRONG, MODERATE, NARRATIVE, TECHNICAL]

TRANSLATIONESE = "### 直訳調・比喩的な語"
ABSTRACT = "### 抽象的な漢語・評価語"
PHRASES = "### 言い回し"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def block(text: str, heading: str) -> str:
    """`heading` 行から、同じ深さ以上の次の見出しまでを返す。"""
    level = len(heading.split(" ", 1)[0])
    start = text.index(heading + "\n")
    rest = text[start + len(heading) :]
    match = re.search(rf"^#{{1,{level}}} ", rest, flags=re.MULTILINE)
    return rest[: match.start()] if match else rest


def entries(text: str) -> list[str]:
    return [line[2:] for line in text.splitlines() if line.startswith("- ")]


class WatchlistTierStructureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(WATCHLIST)

    def test_top_level_headings_are_the_four_tiers(self) -> None:
        headings = [line for line in self.text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, TIER_HEADINGS)

    def test_each_tier_states_its_treatment(self) -> None:
        treatments = {
            STRONG: "原則として使わない",
            MODERATE: "近い位置に重ねない",
            NARRATIVE: "字義どおり",
            TECHNICAL: "用語として",
        }
        for heading, treatment in treatments.items():
            with self.subTest(tier=heading):
                intro = block(self.text, heading).split("\n### ", 1)[0]
                self.assertIn(treatment, intro)

    def test_tiers_keep_word_categories(self) -> None:
        for heading in (STRONG, MODERATE):
            tier = block(self.text, heading)
            with self.subTest(tier=heading):
                self.assertIn(TRANSLATIONESE, tier)
                self.assertIn(ABSTRACT, tier)

    def test_words_are_placed_in_their_tiers(self) -> None:
        placements = {
            STRONG: ("正本", "線引き", "暗黙知"),
            MODERATE: ("効く", "壊れる", "罠", "壁打ち", "判断材料"),
            NARRATIVE: ("〜した瞬間", "黙って〜する"),
            TECHNICAL: ("実務", "土台"),
        }
        for heading, words in placements.items():
            tier_entries = entries(block(self.text, heading))
            for word in words:
                with self.subTest(tier=heading, word=word):
                    self.assertIn(word, tier_entries)

    def test_excluded_words_are_not_listed(self) -> None:
        listed = entries(self.text)
        for word in ("静かに", "壁にぶつかる", "バランスを取る", "踏み出す", "潜む"):
            with self.subTest(word=word):
                self.assertNotIn(word, listed)

    def test_word_counts_match_the_classification(self) -> None:
        strong = block(self.text, STRONG)
        strong_words = entries(block(strong, TRANSLATIONESE)) + entries(
            block(strong, ABSTRACT)
        )
        self.assertEqual(len(strong_words), 35)
        self.assertEqual(len(entries(block(self.text, MODERATE))), 220)
        self.assertEqual(len(entries(block(self.text, NARRATIVE))), 42)
        self.assertEqual(len(entries(block(self.text, TECHNICAL))), 56)


class WatchlistPhraseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.phrases = entries(block(block(read(WATCHLIST), STRONG), PHRASES))

    def test_strong_tier_lists_phrases(self) -> None:
        self.assertEqual(len(self.phrases), 31)
        for phrase in ("〜に効く", "効いてくる", "〜しない設計", "〜を前提にした"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.phrases)

    def test_fragments_are_not_listed_as_phrases(self) -> None:
        for fragment in ("を自然", "が現場", "は決定", "壊さぬ", "効いたの"):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, self.phrases)


class WatchlistHeaderTest(unittest.TestCase):
    def test_header_describes_current_measurement(self) -> None:
        header = read(WATCHLIST).split("\n## ", 1)[0]
        for keyword in ("小説", "2019〜2022 年", "2026 年", "上乗せ"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, header)


class TierReferenceTest(unittest.TestCase):
    def test_core_summary_lists_every_strong_word(self) -> None:
        strong = block(read(WATCHLIST), STRONG)
        summary = read(CORE_SUMMARY)
        self.assertIn("原則として使わない", summary)
        for word in entries(block(strong, TRANSLATIONESE)) + entries(
            block(strong, ABSTRACT)
        ):
            with self.subTest(word=word):
                self.assertIn(word, summary)

    def test_general_rules_describe_tier_treatment(self) -> None:
        vocabulary = block(read(GENERAL_RULES), "## 4. 強調と語彙")
        for keyword in ("強く抑制する語", "適度に抑制する語", "区分"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, vocabulary)

    def test_review_reports_strong_words_one_by_one(self) -> None:
        watchlist_line = next(
            line
            for line in read(REVIEW_SKILL).splitlines()
            if "expression-watchlist.md" in line
        )
        self.assertIn("強く抑制する語", watchlist_line)
        self.assertIn("1 件ずつ", watchlist_line)
        self.assertIn("適度に抑制する語", watchlist_line)

    def test_draft_avoids_strong_words(self) -> None:
        self.assertIn("強く抑制する語", read(DRAFT_SKILL))

    def test_technical_tier_exempts_only_term_usage(self) -> None:
        technical = block(read(WATCHLIST), TECHNICAL).split("\n### ", 1)[0]
        self.assertIn("比喩的な用法", technical)
        for path in (REVIEW_SKILL, DRAFT_SKILL):
            with self.subTest(file=path.name):
                self.assertIn("技術文書での用語としての用法", read(path))


class PluginDocumentsAvoidStrongWordsTest(unittest.TestCase):
    def test_readmes_do_not_use_seihon(self) -> None:
        for path in (PLUGIN_DIR / "README.md", REPO_ROOT / "README.md"):
            with self.subTest(file=str(path.relative_to(REPO_ROOT))):
                self.assertNotIn("正本", read(path))


if __name__ == "__main__":
    unittest.main()
