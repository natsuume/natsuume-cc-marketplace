"""ui-discipline: 常時注入するルールと ui-patterns skill に置くルールの分担の契約テスト。

常時注入 (`hooks/prompts/ui-rules.md`) に置くルールは visibility-taxonomy・layout-stability・
async-states の 3 つで、残りの 7 ルール (component-layers・composition・component-search・
design-tokens・a11y-basics・robustness・visual-direction) の意図・指示・境界は ui-patterns skill
(`skills/ui-patterns/SKILL.md`) に置く。

文章全体の一致は検査しない。rule ID マーカー・見出し・語句の有無で検査し、言い回しは実装側で
選べる。語の照合は、行の折り返しで分断された出現も拾うため、空白を除去した文字列で行う。

各テストクラスが検査する規則:

- ``AlwaysInjectedRulesTest`` (ui-rules.md): rule ID マーカーは常時注入する 3 ルールだけで、
  各ルールの節に意図・指示・境界 (`**なぜ**`・`**指示**`・`**境界**`) があり、各ルールの要の
  語句が残る。`**なぜ**`・`**指示**`・`**境界**` はファイル全体で 3 回ずつしか現れない
  (skill に置くルールの本文を持たない)。冒頭の説明 (表題から最初の rule マーカーまで) は
  ui-patterns skill に触れる。
- ``SkillRuleSummariesTest`` (ui-rules.md): skill に置く 7 ルールのそれぞれについて、rule ID
  (`rule:<id>` または `` `<id>` `` の形) と要約を書いた行があり、その行を含む節 (見出しを含む)
  が ui-patterns skill に触れる。
- ``SkillRuleBodiesTest`` (ui-patterns の SKILL.md): 10 ルールのそれぞれに見出しに
  `rule:<id>` を含む節がちょうど 1 つあり、skill に置く 7 ルールの節には、そのルールの意図・
  指示・境界の要の語句がある。
- ``SkillDescriptionTest`` (ui-patterns の SKILL.md): frontmatter の description が、skill に
  置くルールが必要になる作業 (共通 component の新設・API の設計、ダイアログ・フォームの実装、
  スタイル値の指定、新規の視覚デザイン) を挙げる。
- ``SubagentPreambleTest`` (ui-rules-subagent-preamble.md): rule:visual-direction の読み替えを
  書いた箇条書きの項目が、そのルールの所在 (ui-patterns skill) とエスカレーションを書き、
  視覚方向の候補の形 (3〜4 案、配色・タイポグラフィ・トーン) と返し方 (最終報告) を保つ。
- ``ReadmeRuleTablesTest`` (plugin README): rule ID の表が、常時注入する 3 ルールの表と、
  skill に置く 7 ルールの表に分かれている。
- ``StaleTenRuleClaimsTest`` (plugin README と ui-patterns の SKILL.md): 10 ルールを常時注入
  するという文 (「10 ルールとして常時配送」「常時注入される 10 ルール」の形) を持たない。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "ui-discipline"
UI_RULES = PLUGIN_DIR / "hooks" / "prompts" / "ui-rules.md"
SUBAGENT_PREAMBLE = PLUGIN_DIR / "hooks" / "prompts" / "ui-rules-subagent-preamble.md"
UI_PATTERNS_SKILL = PLUGIN_DIR / "skills" / "ui-patterns" / "SKILL.md"
PLUGIN_README = PLUGIN_DIR / "README.md"

# 常時注入するルール。
ALWAYS_INJECTED_RULE_IDS = ("visibility-taxonomy", "layout-stability", "async-states")

# ui-patterns skill に置くルール。
SKILL_RULE_IDS = (
    "component-layers",
    "composition",
    "component-search",
    "design-tokens",
    "a11y-basics",
    "robustness",
    "visual-direction",
)

ALL_RULE_IDS = ALWAYS_INJECTED_RULE_IDS + SKILL_RULE_IDS

# ルールの節の意図・指示・境界を示すラベル。
RULE_PART_LABELS = ("**なぜ**", "**指示**", "**境界**")

# 常時注入するルールの要の語句 (現在の内容を保つことの検査)。
ALWAYS_INJECTED_RULE_PHRASES: dict[str, tuple[str, ...]] = {
    "visibility-taxonomy": (
        "aria-disabled",
        "常時有効",
        "決定表に無い状況",
        "progressive disclosure",
    ),
    "layout-stability": (
        "Cumulative Layout Shift",
        "aspect-ratio",
        "line-clamp",
        "truncation",
    ),
    "async-states": ("白画面", "loading / empty / error", "skeleton", "静的コンテンツ"),
}

# skill に置くルールの意図 (なぜ)・指示・境界の要の語句。
SKILL_RULE_PHRASES: dict[str, dict[str, tuple[str, ...]]] = {
    "component-layers": {
        "なぜ": ("誤った抽象化",),
        "指示": ("共通化必須", "rule of three"),
        "境界": ("shadcn/ui",),
    },
    "composition": {
        "なぜ": ("boolean trap",),
        "指示": ("slot 追加", "inline 化"),
        "境界": ("fork コピー",),
    },
    "component-search": {
        "なぜ": ("最も頻発する失敗",),
        "指示": ("インベントリ", "プロジェクト規約の置き場所"),
        "境界": ("同一セッションの直前の作業",),
    },
    "design-tokens": {
        "なぜ": ("ダークモード",),
        "指示": ("token 経由", "未整備"),
        "境界": ("色は例外なく token 経由",),
    },
    "a11y-basics": {
        "なぜ": ("後付け",),
        "指示": ("キーボードで完結", "focus trap", "WCAG"),
        "境界": ("自前で再実装しない",),
    },
    "robustness": {
        "なぜ": ("ウィンドウ分割",),
        "指示": ("rem 基準", "固定高さ"),
        "境界": ("レスポンシブ",),
    },
    "visual-direction": {
        "なぜ": ("AI slop",),
        "指示": ("3〜4 案", "配色・タイポグラフィ・トーン", "避ける既定"),
        "境界": ("提案を挟まず",),
    },
}

# skill の description に挙げる作業と、それを識別する語 (空白を除去した description に照合する)。
SKILL_DESCRIPTION_TASKS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("共通 component の新設", re.compile(r"共通(?:component|コンポーネント)")),
    ("component の API の設計", re.compile(r"API")),
    ("ダイアログの実装", re.compile(r"ダイアログ|(?i:dialog)")),
    ("フォームの実装", re.compile(r"フォーム|(?i:form)")),
    ("スタイル値の指定", re.compile(r"スタイル")),
    ("新規の視覚デザイン", re.compile(r"視覚")),
)

RULE_MARKER_PATTERN = re.compile(r"<!--\s*rule:([A-Za-z0-9_-]+)\s*-->")
HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)
HEADING_PATTERN = re.compile(r"^(#{1,6}) ")
FENCE_PATTERN = re.compile(r"^\s*(?:```|~~~)")
TOP_LEVEL_LIST_ITEM_PATTERN = re.compile(r"^(?:[-*+]|\d+\.) ")
TABLE_RULE_ID_PATTERN = re.compile(r"^\|\s*`(?:rule:)?([A-Za-z0-9_-]+)`\s*\|")

# 10 ルールを常時注入・配送するという文 (空白を除去した文に照合する)。「10 ルールのうち
# 3 ルールを常時注入する」のような文には一致しない。
STALE_TEN_RULE_CLAIM = re.compile(r"10ルール(?:として|を)常時|常時(?:注入|配送)(?:する|される)10ルール")

# rule ID の要約の行に、rule ID の他に求める文字数 (空白を除く)。
SUMMARY_MIN_EXTRA_CHARS = 10


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def display_path(path: Path) -> str:
    """失敗メッセージ用のパス (リポジトリ直下からの相対パス)。"""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def strip_whitespace(text: str) -> str:
    """空白文字をすべて除去する (行の折り返しで分断された出現も照合するため)。"""
    return "".join(text.split())


def contains_phrase(text: str, phrase: str) -> bool:
    return strip_whitespace(phrase) in strip_whitespace(text)


def without_html_comments(text: str) -> str:
    """HTML コメント (保守者向けメモと rule マーカー) を除いた本文。"""
    return HTML_COMMENT_PATTERN.sub("", text)


def rule_markers(text: str) -> list[str]:
    return RULE_MARKER_PATTERN.findall(text)


def rule_block(text: str, rule_id: str) -> str:
    """`<!-- rule:<rule_id> -->` から次の rule マーカーの手前までを返す。"""
    marker = re.search(rf"<!--\s*rule:{re.escape(rule_id)}\s*-->", text)
    if marker is None:
        return ""
    following = RULE_MARKER_PATTERN.search(text, marker.end())
    return text[marker.end() :] if following is None else text[marker.end() : following.start()]


def heading_sections(text: str) -> list[tuple[str, str]]:
    """見出しごとの節を (見出し行, 見出し行を含む節の本文) の列で返す。

    節は見出し行から、同じか上位レベルの次の見出しの手前まで。fence 内の `#` 始まりの行は
    見出しとして扱わない。
    """
    lines = text.splitlines(keepends=True)
    headings: list[tuple[int, int]] = []
    in_fence = False
    for index, line in enumerate(lines):
        if FENCE_PATTERN.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_PATTERN.match(line)
        if match:
            headings.append((index, len(match.group(1))))
    sections = []
    for position, (start, level) in enumerate(headings):
        end = len(lines)
        for next_start, next_level in headings[position + 1 :]:
            if next_level <= level:
                end = next_start
                break
        sections.append((lines[start].rstrip("\n"), "".join(lines[start:end])))
    return sections


def innermost_section_containing(text: str, line_index: int) -> str:
    """`line_index` 行目を含む最も内側の節 (直前の見出し行を含む) を返す。

    見出しが無い位置ではファイル全体を返す。
    """
    lines = text.splitlines(keepends=True)
    in_fence = False
    start = 0
    start_level: int | None = None
    for index, line in enumerate(lines[: line_index + 1]):
        if FENCE_PATTERN.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_PATTERN.match(line)
        if match:
            start = index
            start_level = len(match.group(1))
    if start_level is None:
        return text
    end = len(lines)
    in_fence = False
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if FENCE_PATTERN.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_PATTERN.match(line)
        if match and index > line_index:
            end = index
            break
    return "".join(lines[start:end])


def skill_rule_sections(rule_id: str) -> list[str]:
    """SKILL.md のうち、見出しに `rule:<rule_id>` を含む節 (見出しを含む) の列。"""
    pattern = re.compile(rf"rule:{re.escape(rule_id)}(?![A-Za-z0-9_-])")
    return [
        section
        for heading, section in heading_sections(read(UI_PATTERNS_SKILL))
        if pattern.search(heading)
    ]


def frontmatter_description(text: str) -> str:
    """SKILL.md の frontmatter (先頭の `---` で囲んだ部分) の description の値。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("description:"):
            return line[len("description:") :].strip()
    return ""


def top_level_list_items(text: str) -> list[str]:
    """最上位の箇条書きの項目 (続きの行を含む) の列を返す。"""
    items: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if TOP_LEVEL_LIST_ITEM_PATTERN.match(line):
            if current is not None:
                items.append("\n".join(current))
            current = [line]
        elif current is not None and line.strip() and line[:1].isspace():
            current.append(line)
        else:
            if current is not None:
                items.append("\n".join(current))
            current = None
    if current is not None:
        items.append("\n".join(current))
    return items


def rule_id_tables(text: str) -> list[tuple[int, list[str]]]:
    """1 列目が `` `<rule ID>` `` の行を持つ表を、(表の先頭の行番号, rule ID の列) で返す。"""
    tables: list[tuple[int, list[str]]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].startswith("|"):
            index += 1
            continue
        start = index
        ids: list[str] = []
        while index < len(lines) and lines[index].startswith("|"):
            match = TABLE_RULE_ID_PATTERN.match(lines[index])
            if match and match.group(1) in ALL_RULE_IDS:
                ids.append(match.group(1))
            index += 1
        if ids:
            tables.append((start, ids))
    return tables


def japanese_sentences(text: str) -> list[str]:
    """「。」・行の境目で区切った文を返す。"""
    return [part for part in re.split(r"。|\n", text) if part.strip()]


class AlwaysInjectedRulesTest(unittest.TestCase):
    """ui-rules.md は常時注入する 3 ルールの本文だけを持つ。"""

    def test_rule_markers_are_the_always_injected_rules_only(self) -> None:
        markers = rule_markers(read(UI_RULES))
        self.assertEqual(
            sorted(ALWAYS_INJECTED_RULE_IDS),
            sorted(markers),
            f"{display_path(UI_RULES)}: rule ID マーカーが常時注入する 3 ルール "
            f"{list(ALWAYS_INJECTED_RULE_IDS)} だけでない (マーカー: {markers})",
        )

    def test_always_injected_rules_keep_intent_instruction_and_boundary(self) -> None:
        text = read(UI_RULES)
        for rule_id in ALWAYS_INJECTED_RULE_IDS:
            block = rule_block(text, rule_id)
            with self.subTest(rule=rule_id):
                self.assertTrue(
                    block.strip(), f"{display_path(UI_RULES)}: `<!-- rule:{rule_id} -->` が無い"
                )
                for label in RULE_PART_LABELS:
                    self.assertIn(
                        label, block, f"{display_path(UI_RULES)}: rule:{rule_id} の節に {label} が無い"
                    )

    def test_always_injected_rules_keep_their_key_phrases(self) -> None:
        text = read(UI_RULES)
        for rule_id, phrases in ALWAYS_INJECTED_RULE_PHRASES.items():
            block = rule_block(text, rule_id)
            for phrase in phrases:
                with self.subTest(rule=rule_id, phrase=phrase):
                    self.assertTrue(
                        contains_phrase(block, phrase),
                        f"{display_path(UI_RULES)}: rule:{rule_id} の節に「{phrase}」が無い",
                    )

    def test_rule_part_labels_appear_only_for_the_always_injected_rules(self) -> None:
        """skill に置くルールの意図・指示・境界の節は ui-rules.md に無い。"""
        text = read(UI_RULES)
        for label in RULE_PART_LABELS:
            with self.subTest(label=label):
                self.assertEqual(
                    len(ALWAYS_INJECTED_RULE_IDS),
                    text.count(label),
                    f"{display_path(UI_RULES)}: {label} が常時注入するルールの数 "
                    f"({len(ALWAYS_INJECTED_RULE_IDS)}) と違う回数現れる",
                )

    def test_intro_mentions_the_ui_patterns_skill(self) -> None:
        """冒頭の説明 (表題から最初の rule マーカーまで) が ui-patterns skill に触れる。"""
        text = read(UI_RULES)
        title = re.search(r"^# ", text, re.MULTILINE)
        self.assertIsNotNone(title, f"{display_path(UI_RULES)}: 表題 (`# `) が無い")
        assert title is not None
        first_marker = RULE_MARKER_PATTERN.search(text, title.start())
        intro = text[title.start() : first_marker.start() if first_marker else len(text)]
        self.assertIn(
            "ui-patterns",
            intro,
            f"{display_path(UI_RULES)}: 冒頭の説明が ui-patterns skill に置くルールに触れない",
        )


class SkillRuleSummariesTest(unittest.TestCase):
    """ui-rules.md は skill に置く 7 ルールを rule ID と 1 行の要約で示す。"""

    @staticmethod
    def summary_line_index(lines: list[str], rule_id: str) -> int | None:
        """rule ID (`rule:<id>` または `` `<id>` ``) と要約を書いた行の番号。"""
        pattern = re.compile(rf"rule:{re.escape(rule_id)}(?![A-Za-z0-9_-])|`{re.escape(rule_id)}`")
        for index, line in enumerate(lines):
            if not pattern.search(line):
                continue
            extra = strip_whitespace(pattern.sub("", line)).lstrip("-*+")
            if len(extra) >= SUMMARY_MIN_EXTRA_CHARS:
                return index
        return None

    def test_each_skill_rule_has_a_summary_line(self) -> None:
        lines = without_html_comments(read(UI_RULES)).splitlines()
        for rule_id in SKILL_RULE_IDS:
            with self.subTest(rule=rule_id):
                self.assertIsNotNone(
                    self.summary_line_index(lines, rule_id),
                    f"{display_path(UI_RULES)}: rule:{rule_id} の rule ID と要約を書いた行が無い",
                )

    def test_summary_section_points_to_the_ui_patterns_skill(self) -> None:
        text = without_html_comments(read(UI_RULES))
        lines = text.splitlines()
        for rule_id in SKILL_RULE_IDS:
            with self.subTest(rule=rule_id):
                index = self.summary_line_index(lines, rule_id)
                if index is None:
                    self.fail(f"{display_path(UI_RULES)}: rule:{rule_id} の要約の行が無い")
                section = innermost_section_containing(text, index)
                self.assertIn(
                    "ui-patterns",
                    section,
                    f"{display_path(UI_RULES)}: rule:{rule_id} の要約の節に、詳細が ui-patterns "
                    "skill にあることが書かれていない",
                )


class SkillRuleBodiesTest(unittest.TestCase):
    """ui-patterns の SKILL.md は skill に置く 7 ルールの意図・指示・境界を持つ。"""

    def test_each_rule_has_exactly_one_section(self) -> None:
        for rule_id in ALL_RULE_IDS:
            with self.subTest(rule=rule_id):
                sections = skill_rule_sections(rule_id)
                self.assertEqual(
                    1,
                    len(sections),
                    f"{display_path(UI_PATTERNS_SKILL)}: 見出しに rule:{rule_id} を含む節が "
                    f"1 つでない ({len(sections)} 個)",
                )

    def test_skill_rule_sections_carry_intent_instruction_and_boundary(self) -> None:
        for rule_id, parts in SKILL_RULE_PHRASES.items():
            sections = skill_rule_sections(rule_id)
            section = sections[0] if sections else ""
            for part, phrases in parts.items():
                for phrase in phrases:
                    with self.subTest(rule=rule_id, part=part, phrase=phrase):
                        self.assertTrue(
                            contains_phrase(section, phrase),
                            f"{display_path(UI_PATTERNS_SKILL)}: rule:{rule_id} の節に{part}の"
                            f"「{phrase}」が無い",
                        )


class SkillDescriptionTest(unittest.TestCase):
    """ui-patterns の description が、skill に置くルールが必要になる作業を挙げる。"""

    def test_description_lists_the_tasks_that_need_the_skill_rules(self) -> None:
        description = frontmatter_description(read(UI_PATTERNS_SKILL))
        self.assertTrue(
            description, f"{display_path(UI_PATTERNS_SKILL)}: frontmatter の description が無い"
        )
        stripped = strip_whitespace(description)
        for task, pattern in SKILL_DESCRIPTION_TASKS:
            with self.subTest(task=task):
                self.assertIsNotNone(
                    pattern.search(stripped),
                    f"{display_path(UI_PATTERNS_SKILL)}: description に「{task}」の作業が無い "
                    f"(description: {description})",
                )


class SubagentPreambleTest(unittest.TestCase):
    """前置き注記の rule:visual-direction の読み替えは、ルールが skill にあっても成り立つ。"""

    def visual_direction_item(self) -> str:
        body = without_html_comments(read(SUBAGENT_PREAMBLE))
        items = [item for item in top_level_list_items(body) if "visual-direction" in item]
        self.assertEqual(
            1,
            len(items),
            f"{display_path(SUBAGENT_PREAMBLE)}: rule:visual-direction の読み替えを書いた"
            f"箇条書きの項目が 1 つでない ({len(items)} 個)",
        )
        return items[0]

    def test_item_names_the_location_and_the_escalation(self) -> None:
        item = self.visual_direction_item()
        for phrase, meaning in (
            ("ui-patterns", "rule:visual-direction の所在 (ui-patterns skill)"),
            ("エスカレーション", "視覚方向の候補をエスカレーションで返すこと"),
        ):
            with self.subTest(phrase=phrase):
                self.assertTrue(
                    contains_phrase(item, phrase),
                    f"{display_path(SUBAGENT_PREAMBLE)}: rule:visual-direction の読み替えに"
                    f"{meaning}が書かれていない",
                )

    def test_item_keeps_the_shape_of_the_candidates_and_the_report(self) -> None:
        item = self.visual_direction_item()
        for phrase in ("3〜4 案", "配色・タイポグラフィ・トーン", "最終報告"):
            with self.subTest(phrase=phrase):
                self.assertTrue(
                    contains_phrase(item, phrase),
                    f"{display_path(SUBAGENT_PREAMBLE)}: rule:visual-direction の読み替えに"
                    f"「{phrase}」が無い",
                )

    def test_skill_path_placeholder_is_kept(self) -> None:
        self.assertIn(
            "{{UI_PATTERNS_SKILL_PATH}}",
            without_html_comments(read(SUBAGENT_PREAMBLE)),
            f"{display_path(SUBAGENT_PREAMBLE)}: ui-patterns の SKILL.md を Read で参照する"
            "ためのプレースホルダが無い",
        )


class ReadmeRuleTablesTest(unittest.TestCase):
    """README の rule ID の表は、常時注入する 3 ルールと skill に置く 7 ルールに分かれる。"""

    def test_rule_tables_are_split(self) -> None:
        text = read(PLUGIN_README)
        tables = rule_id_tables(text)
        id_sets = [sorted(ids) for _, ids in tables]
        for label, expected in (
            ("常時注入する 3 ルール", sorted(ALWAYS_INJECTED_RULE_IDS)),
            ("ui-patterns skill に置く 7 ルール", sorted(SKILL_RULE_IDS)),
        ):
            with self.subTest(table=label):
                self.assertIn(
                    expected,
                    id_sets,
                    f"{display_path(PLUGIN_README)}: {label}だけを並べた rule ID の表が無い "
                    f"(表ごとの rule ID: {id_sets})",
                )

    def test_tables_are_introduced_by_their_delivery(self) -> None:
        """各表を含む節 (見出しを含む) が、配送の仕方 (常時注入 / ui-patterns skill) を書く。"""
        text = read(PLUGIN_README)
        for start, ids in rule_id_tables(text):
            section = innermost_section_containing(text, start)
            if sorted(ids) == sorted(ALWAYS_INJECTED_RULE_IDS):
                with self.subTest(table="常時注入する 3 ルール"):
                    self.assertIn("常時", section)
            elif sorted(ids) == sorted(SKILL_RULE_IDS):
                with self.subTest(table="ui-patterns skill に置く 7 ルール"):
                    self.assertIn("ui-patterns", section)


class StaleTenRuleClaimsTest(unittest.TestCase):
    """README と SKILL.md は、10 ルールを常時注入するとは書かない。"""

    def test_no_sentence_says_ten_rules_are_always_injected(self) -> None:
        for path in (PLUGIN_README, UI_PATTERNS_SKILL):
            offenders = [
                sentence.strip()
                for sentence in japanese_sentences(read(path))
                if STALE_TEN_RULE_CLAIM.search(strip_whitespace(sentence))
            ]
            with self.subTest(file=display_path(path)):
                self.assertEqual(
                    [],
                    offenders,
                    f"{display_path(path)}: 10 ルールを常時注入するという文が残っている",
                )


if __name__ == "__main__":
    unittest.main()
