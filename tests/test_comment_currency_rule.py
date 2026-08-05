"""説明文書の経緯記述禁止ルール (rule:comment-currency) の配送契約を検証する。

このルールは、コードコメント・docstring・README 等の説明文書には現在の内容に
対する説明のみを書き、過去の経緯・変更履歴の解説を書かないことを規定する。
配送 3 面 (fable 向け常時適用ルール / sonnet 向け常時適用ルール / subagent 向け
常時適用ルール) それぞれについて、次を検証する:

1. ルールマーカーが単独行 (行頭から行末までがマーカーのみの行) として規定
   回数だけ存在すること。prose や inline code 内への偶発的な部分文字列一致は
   出現として数えない
2. マーカーを保持するファイルを面ごとに一意に解決したうえで、そのルール本文
   ブロックが「## 」見出し行 (「説明は常に最新の内容のみ」を含む) で始まり、
   ブロックを段落分割 → 文分割 (「。」区切り) → 対称正規化 (ASCII 空白・
   タブ・改行の除去) した文要素の集合に対し、canonical 文セットの各文が
   完全一致で存在すること (部分文字列包含ではない)
3. 各配送経路 — SessionStart (inject-always.sh の fable 配送・sonnet part 1
   self-gate 配送)、UserPromptSubmit (inject-rules-part.sh の sonnet part 2/3
   self-gate 配送、resolve-model-on-prompt.sh の Fable one-shot 補正配送)、
   SubagentStart (inject-subagent-rules.sh の配送) — が実際に生成する
   additionalContext の最大構成が UTF-16 code units 8,000 以下 (inject-always.sh
   の `-gt 8000` 縮退条件と整合する境界) に収まること (判定は単一の共有述語
   within_size_budget を使う)
4. ルール本文ブロック自身、および面ごとに解決した冒頭 HTML コメントヘッダが、
   面固有の静的自己準拠述語 (static_surface_*、定義直前のコメント参照) が
   定める禁止対象の経緯記述 (issue/PR 番号・年月日。大文字小文字や年月日単位
   前の空白の表記ゆらぎを含む) を含まないこと。ヘッダはさらに既知ナラティブ
   語彙のブロックリストでも検査する (ルール本文は禁止形式を引用言及するため
   対象外)

面の解決 (resolve_face) は失敗原因 (マーカー 0 件 / 複数 part / ブロック抽出
不能 / ヘッダ欠落) を判別可能な形で返し、全検査の失敗メッセージに実際の原因が
表示される。共有ロジックは、実ファイルに依存しない synthetic な入力での
自己テストも別途持つ。
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

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS = REPO_ROOT / "plugins" / "agent-discipline" / "hooks" / "prompts"
SCRIPTS = REPO_ROOT / "plugins" / "agent-discipline" / "hooks" / "scripts"

FABLE_MD = PROMPTS / "always-fable.md"
SONNET_MD = {
    "always-sonnet-1.md": PROMPTS / "always-sonnet-1.md",
    "always-sonnet-2.md": PROMPTS / "always-sonnet-2.md",
    "always-sonnet-3.md": PROMPTS / "always-sonnet-3.md",
}
SUBAGENT_MD = PROMPTS / "subagent-rules.md"

INJECT_ALWAYS_SH = SCRIPTS / "inject-always.sh"
INJECT_RULES_PART_SH = SCRIPTS / "inject-rules-part.sh"
INJECT_SUBAGENT_RULES_SH = SCRIPTS / "inject-subagent-rules.sh"
RESOLVE_MODEL_ON_PROMPT_SH = SCRIPTS / "resolve-model-on-prompt.sh"

MARKER = "<!-- rule:comment-currency -->"
SIZE_BUDGET_UNITS = 8000
EXPECTED_HEADING_TEXT = "説明は常に最新の内容のみ"
# Markdown の ATX 見出し規則 (先頭インデントは 3 個以下の ASCII 空白まで) に
# 合わせた見出し行判定。block_heading_line が返す改行を含まない単一行に対して
# 使うため DOTALL 等は不要。
HEADING_INDENT_PATTERN = re.compile(r"^( {0,3})(#.*)$")

# 配送面のラベル。fable / subagent は固定ファイル、sonnet はマーカーを保持する
# part を動的に解決する (resolve_face_path 参照)。
FACES = (
    "always-fable.md",
    "always-sonnet-{1,2,3}.md",
    "subagent-rules.md",
)

# 各面のルールブロックに存在すべき canonical 文 (rule:comment-currency の配送
# 本文からの逐語抜粋)。文単位の完全一致で照合する (部分文字列包含ではない)。
# 段落順序・同一段落性は固定しない。太字見出し接頭辞 (`**指示**: ` 等) は、
# 実ファイルで同じ文に地続きで付いている場合のみ含める。
CANONICAL_SENTENCES = {
    "always-fable.md": (
        # 中核指示文
        "**指示**: コードコメント・docstring・README 等の説明文書には現在の内容に対する説明のみを書き、過去の経緯・変更履歴の解説 (版数・日付・issue/PR 番号による過去の変更の記述、旧実装の説明、移設・置換・廃止の記録、不採用案の経緯記録、出典としての issue/PR 番号参照) を書かない。",
        # 履歴置き場文
        "履歴と検討経緯は commit message・PR 説明・issue に置く。",
        # 契約・制約文
        "契約・制約は issue 参照に頼らずその場で完結して書き、コード変更で説明が古くなる場合は同時に更新する。",
        # touch-time 文 (fable は 1 文)
        "適用は touch-time — 新規作成・意味変更した説明ブロックに適用し、指示のない一括清掃や単純移設・整形での書き換え波及は行わない。",
        # 例外文
        "**境界**: 例外は 2 つ — (1) 撤去条件付き暫定措置は「現在の不具合・撤去条件・確認方法」の 3 要素で書く (導入日は書かない) (2) 現行の主張への検証日・検証環境の付記は証拠の鮮度情報として許可する。",
        # 対象外文
        "commit message・PR 説明・issue body、および明示的に履歴を目的とする文書 (README の `### vX.Y.Z → vX.Y.Z` 変更履歴節を含む) は対象外。",
        # 境界末尾文 (現在形の設計理由は禁止対象外)
        "過去に言及しない現在形の設計理由の説明は禁止対象ではない。",
    ),
    "always-sonnet-{1,2,3}.md": (
        # 中核指示文
        "説明文書には現在の内容に対する説明のみを書き、過去の経緯・変更履歴の解説を書かない。",
        # 禁止対象文
        "禁止対象: 版数・日付・issue/PR 番号による過去の変更の記述 (「vX で追加」「#N で移設」等)、旧実装の説明 (「以前は〜だったが」)、移設・置換・廃止の記録、不採用案の経緯記録、出典としての issue/PR 番号参照。",
        # 履歴置き場文
        "履歴と検討経緯は commit message・PR 説明・issue に置く。",
        # 契約・制約文 (sonnet は 2 文に分かれる。両方を要求する)
        "契約・制約は issue 参照に頼らず、その場で読んで完結するように書く。",
        "コード変更で対応する説明が古くなる場合は同時に更新する。",
        # touch-time 文 (sonnet は 2 文に分かれる。両方を要求する)
        "適用は touch-time: 新規作成・意味を変更した説明ブロックに適用する。",
        "単純移設・整形のみの変更で既存記述の書き換えに波及させず、指示のない一括清掃を行わない。",
        # 例外文
        "**境界**: 例外は 2 つ — (1) 撤去条件付き暫定措置は「現在の不具合・撤去条件・確認方法」の 3 要素で書く (導入日は書かない) (2) 現行の主張への検証日・検証環境の付記 (「YYYY-MM-DD 実測」「バージョン X で確認」等) は証拠の鮮度情報として許可する。",
        # 対象外文
        "commit message・PR 説明・issue body、および明示的に履歴を目的とする文書 (README の `### vX.Y.Z → vX.Y.Z` 変更履歴節を含む) は対象外。",
        # 境界末尾文 (現在形の設計理由は禁止対象外)
        "過去に言及しない現在形の設計理由 (「なぜこうするか」「X 方式は〜のため使わない」) は禁止対象ではない。",
    ),
    "subagent-rules.md": (
        # 中核指示文
        "説明文書には現在の内容に対する説明のみを書き、過去の経緯・変更履歴の解説 (版数・日付・issue/PR 番号による過去の変更の記述、旧実装の説明、出典としての issue/PR 番号参照) を書かない。",
        # 履歴置き場文
        "履歴は commit message・PR 説明・issue に置く。",
        # 契約・制約文
        "契約・制約は issue 参照に頼らずその場で完結して書き、編集で説明が古くなる場合は同時に更新する。",
        # touch-time 文
        "適用は touch-time — 新規作成・意味変更した説明ブロックに適用し、指示のない一括清掃を行わない。",
        # 対象外文
        "commit message・PR 説明・issue body と、明示的に履歴を目的とする文書 (README の変更履歴節を含む) は対象外。",
        # 例外文
        "例外: 撤去条件付き暫定措置の「現在の不具合・撤去条件・確認方法」(導入日なし) と、現行の主張への検証日・検証環境の付記。",
        # 境界末尾文 (現在形の設計理由は禁止対象外。fable/sonnet の境界末尾文の compact 版)
        "過去に言及しない現在形の設計理由の説明は禁止対象ではない。",
    ),
}

# 各面のルールブロックが持つべき構造要素ラベル (太字見出し + コロン)。規律の
# 記述形式 (「意図 (なぜ) + 短い指示 + 境界 (いつ例外か)」等) が要求する見出しの
# 存在を検査する。文言の内容 (CANONICAL_SENTENCES) とは別に、規律としての骨格
# が欠落していないかを確認する。
STRUCTURAL_ELEMENT_LABELS = {
    "always-fable.md": ("**なぜ**:", "**指示**:", "**境界**:"),
    "always-sonnet-{1,2,3}.md": ("**適用範囲**:", "**なぜ**:", "**境界**:", "**例**:"),
    "subagent-rules.md": ("**適用範囲**:", "**なぜ**:"),
}

FORBIDDEN_ISSUE_NUMBER_PATTERN = re.compile(r"#[0-9]")
# issue 参照接頭辞は大文字小文字を区別しない (「issue #」「Issue #」「ISSUE #」)。
FORBIDDEN_ISSUE_PREFIX_PATTERN = re.compile(r"issue #", re.IGNORECASE)
# ISO 形式 (20XX-) に加え、20XX/ ・ 20XX. ・ 20XX年 の表記、および年 (月日) 単位と
# 数字の間の ASCII 空白・タブを挟む表記ゆらぎ (「2026 年」等) も検出対象に含める。
FORBIDDEN_DATE_PATTERN = re.compile(r"20[0-9]{2}[ \t]*[-/.年]")

# ヘッダ限定の既知ナラティブ語彙ブロックリスト (2 文字以上の句のみ)。既知形式の
# 回帰ガードであり、網羅性を保証するものではない。ルールブロック本文には適用
# しない — 本文は禁止形式そのものを引用言及する
# (例: 「旧実装の説明 (「以前は〜だったが」)」) ため、本文へ適用すると正当な
# 記述を誤検出する。
NARRATIVE_VOCABULARY_BLOCKLIST = (
    "以前",
    "従来",
    "当初",
    "移設",
    "分割した",
    "分割された",
    "分割前",
    "分割に伴う",
    "削除済み",
    "一字一句無変更",
)

# 「旧」は 1 文字トークンであり、他の多文字句と異なり裸の部分文字列一致にすると
# ナラティブと無関係な語 (固有名詞・他の複合語等) にも誤反応しうる。後続が
# 「設計」「実装」「形式」「来」(旧来)、またはファイル名・識別子として典型的な
# ASCII 英数字・記号の連なり (例:「旧always-sonnet.md」) の場合のみナラティブ句
# として成立するとみなす。
LEGACY_PREFIX_NARRATIVE_PATTERN = re.compile(
    r"旧(?:設計|実装|形式|来|[A-Za-z0-9_.\-]+)"
)

# ルールブロックの終端は、行頭の構造マーカー (`<!-- rule:` / `<!-- subagent-rule:`) に
# 限定する。任意の `<!--` を境界にすると、ブロック内の説明用 HTML コメント (構造
# マーカーではないもの) で意図せず切断されうるため。
STRUCTURAL_MARKER_PATTERN = re.compile(r"(?m)^<!-- (?:rule|subagent-rule):")


# ============================================================================
# 面固有の静的自己準拠述語 (static_surface_*)
# ============================================================================
# 以下の述語は、rule:comment-currency を配送する 3 つの静的ファイル
# (always-fable.md / always-sonnet-{1,2,3}.md / subagent-rules.md) 自身の
# ルールブロック・冒頭ヘッダにのみ適用する面固有の契約であり、ルール本文が
# 説明文書一般に対して規定する規律の実装ではない。
#
# 特に、ルールの境界条項は「現行の主張への検証日・検証環境の付記」を一般の
# 説明文書では証拠の鮮度情報として許可するが、この static_surface_* 述語群は
# その一般例外をこの 3 つの静的配送面には適用しない意図的な厳格化 (面固有契約)
# であり、一般規律の検査ではない。これら 3 ファイルはルールそのもののメタ記述
# であり、日付を伴う正当な検証注記が本来生じない性質の文書であるため、出現する
# 日付形式はすべて経緯記述の疑いとして一律に禁止する。
# ============================================================================


def static_surface_history_matches(text: str) -> list[str]:
    """text 内に禁止対象の経緯記述 (issue/PR 番号・年月日) が含まれるかを判定する。

    ルールブロック自己準拠検査・ヘッダ自己準拠検査の両方が共有する述語 (3 チェック:
    `#[0-9]`・`issue #` 接頭辞 (大文字小文字を区別しない)・年月日形式)。片方だけが
    再実装されて検査項目が乖離する (例: `issue #` チェックが片方から欠落する) のを
    防ぐ。
    """
    found = []
    if FORBIDDEN_ISSUE_NUMBER_PATTERN.search(text):
        found.append("#数字")
    if FORBIDDEN_ISSUE_PREFIX_PATTERN.search(text):
        found.append("issue #")
    if FORBIDDEN_DATE_PATTERN.search(text):
        found.append("年月日形式")
    return found


def static_surface_narrative_vocabulary_matches(text: str) -> list[str]:
    """text 内に既知のナラティブ語彙 (経緯記述の典型語) が含まれるかを判定する。

    NARRATIVE_VOCABULARY_BLOCKLIST (2 文字以上の句) は裸の部分文字列一致、
    「旧」は LEGACY_PREFIX_NARRATIVE_PATTERN による句境界付きの一致で判定する。
    既知形式の回帰ガードであり網羅性の保証ではない。ヘッダ限定で使う
    (static_surface_header_matches 参照)。
    """
    found = [word for word in NARRATIVE_VOCABULARY_BLOCKLIST if word in text]
    if LEGACY_PREFIX_NARRATIVE_PATTERN.search(text):
        found.append("旧")
    return found


def static_surface_header_matches(text: str) -> list[str]:
    """ヘッダ用の禁止判定述語: static_surface_history_matches にナラティブ語彙
    ブロックリストを加えたもの。
    """
    return static_surface_history_matches(
        text
    ) + static_surface_narrative_vocabulary_matches(text)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def utf16_length(text: str) -> int:
    """UTF-16 code unit 数を返す (配送予算の計測単位)。"""
    return len(text.encode("utf-16-le")) // 2


def within_size_budget(length: int) -> bool:
    """UTF-16 code unit 数が配送予算 (8,000 以下) に収まるかを判定する。

    判定は inject-always.sh の `-gt 8000` 縮退条件 (ちょうど 8,000 units は
    縮退させず受理する) と整合させる。配送経路検査 (SizeBudgetTests) とちょうど
    8,000 の境界 guard (synthetic 自己テスト) の両方がこの共有述語を呼ぶ
    (ローカルに比較式を再実装しない)。
    """
    return length <= SIZE_BUDGET_UNITS


def leading_html_comment(text: str) -> str | None:
    """ファイル冒頭の HTML コメントブロック (`<!--` 〜 `-->`) を返す。無ければ None。

    冒頭コメントが構造マーカー (`<!-- rule:` / `<!-- subagent-rule:` 形式)
    そのものである場合は、記述的なヘッダではなくルール本文の構造マーカーと
    みなし、ヘッダとしては受理しない (None を返す)。記述ヘッダが失われて
    ファイル先頭が rule マーカーになった場合、compose_face 側で「ヘッダ欠落」
    として fail-closed になる (空のヘッダへの silent fallback をしない)。
    """
    match = re.match(r"\A<!--.*?-->", text, re.DOTALL)
    if match is None:
        return None
    comment = match.group(0)
    if STRUCTURAL_MARKER_PATTERN.match(comment):
        return None
    return comment


def marker_line_pattern(marker: str = MARKER) -> re.Pattern[str]:
    """marker を単独行として検出する正規表現を返す (行頭〜行末が marker のみ、
    末尾の ASCII 空白・タブは許容する)。
    """
    return re.compile(r"(?m)^" + re.escape(marker) + r"[ \t]*$")


def count_marker_lines(text: str, marker: str = MARKER) -> int:
    """marker が単独行として出現する回数を返す。

    行全体一致で判定するため、prose 文中や inline code (`` `...` ``) 内への
    偶発的な部分文字列一致は出現として数えない。
    """
    return len(marker_line_pattern(marker).findall(text))


def marker_line_present(text: str, marker: str = MARKER) -> bool:
    """marker が単独行として text 内に存在するか。"""
    return marker_line_pattern(marker).search(text) is not None


def rule_block(text: str, marker: str = MARKER) -> str | None:
    """marker が単独行として出現する箇所の直後から、次の行頭構造マーカー・
    `---` 行・EOF 手前までを返す。

    marker は行全体一致 (行頭から行末までが marker のみ) でのみ検出する —
    prose 文中や inline code 内への偶発的な部分文字列一致をブロック開始とは
    みなさない。marker が本文中に単独行として存在しない場合は None を返す。
    """
    match = marker_line_pattern(marker).search(text)
    if match is None:
        return None
    content_start = match.end()
    boundary_candidates = []
    next_marker_match = STRUCTURAL_MARKER_PATTERN.search(text, content_start)
    if next_marker_match is not None:
        boundary_candidates.append(next_marker_match.start())
    for m in re.finditer(r"(?m)^---\s*$", text[content_start:]):
        boundary_candidates.append(content_start + m.start())
        break
    end = min(boundary_candidates) if boundary_candidates else len(text)
    return text[content_start:end]


def block_heading_line(block: str) -> str | None:
    """block 内の最初の非空行を返す (見出し行の検査に使う)。無ければ None。"""
    for line in block.splitlines():
        if line.strip() != "":
            return line
    return None


def block_starts_with_expected_heading(block: str) -> bool:
    """block の先頭見出し行が `## ` で始まり、EXPECTED_HEADING_TEXT を含むか。

    Markdown の ATX 見出し規則に合わせ、先頭インデントは 3 個以下の ASCII 空白
    までのみ許容する。4 空白以上・タブ字下げの行は Markdown ではインデント付き
    コードブロックとして描画され見出しとして機能しないため、無条件の lstrip
    ではなく厳密な先頭空白判定で弾く。
    """
    heading = block_heading_line(block)
    if heading is None:
        return False
    match = HEADING_INDENT_PATTERN.match(heading)
    if match is None:
        return False
    content = match.group(2)
    return content.startswith("## ") and EXPECTED_HEADING_TEXT in content


def split_into_paragraphs(text: str) -> list[str]:
    """text を空行区切りの段落に分割する。

    空行は完全な空文字列の行だけでなく、空白・タブのみの行 (`^[ \\t]+$`) も
    区切りとみなす (厳密な `\\n\\n` 一致では、空白入り空行を挟んだ 2 段落が
    1 段落として扱われてしまう)。連続する空行はまとめて 1 つの区切りとして
    扱い、前後の空行のみからなる要素は含めない。
    """
    paragraphs: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip() == "":
            if current:
                paragraphs.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append("\n".join(current))
    return paragraphs


def strip_ascii_whitespace(text: str) -> str:
    """text から ASCII 空白・タブ・改行 (CR/LF) をすべて除去する (対称正規化)。

    候補側 (ブロックから抽出した文) と canonical 側の双方に同じ関数を適用する
    ことで、継続行の先頭インデントや句読点隣接の折返し空白による非対称を
    解消する。段落分割 (split_into_paragraphs) の後に適用する — 空行・空白
    のみの行を挟んだ 2 段落は、この関数を通す前の段階で既に別要素になって
    いるため、ここで空白を除去しても再結合しない。
    """
    return re.sub(r"[ \t\r\n]+", "", text)


def block_sentence_set(block: str) -> set[str]:
    """block を段落 → 文 (「。」区切り) に分割し、各文を strip_ascii_whitespace
    で正規化した集合を返す。

    段落分割を先に行うため、空行 (空白のみの行を含む) を挟んで 2 段落に
    またがる文字列は 1 つの文として結合されない。
    """
    sentences: set[str] = set()
    for paragraph in split_into_paragraphs(block):
        for sentence in re.findall(r"[^。]*。", paragraph):
            sentences.add(strip_ascii_whitespace(sentence))
    return sentences


def block_matches_canonical_sentence(block: str, canonical: str) -> bool:
    """block 内のいずれかの文要素が canonical と完全一致するか (対称正規化後)。"""
    return strip_ascii_whitespace(canonical) in block_sentence_set(block)


def missing_canonical_sentences(block: str, canonicals: tuple[str, ...]) -> list[str]:
    """canonicals のうち block 内に文要素として完全一致で存在しないものを返す。"""
    sentences = block_sentence_set(block)
    return [c for c in canonicals if strip_ascii_whitespace(c) not in sentences]


def missing_structural_labels(block: str, labels: tuple[str, ...]) -> list[str]:
    """labels のうち block 内に部分文字列として存在しないものを返す。

    STRUCTURAL_ELEMENT_LABELS の各ラベル (太字見出し + コロン) が、規律の
    記述形式が要求する段落として実在するかを検査する (部分文字列照合で足りる
    — 見出し自体は短い定型句であり、文単位の完全一致は要求しない)。
    """
    return [label for label in labels if label not in block]


def resolve_marker_holder(named_texts: dict[str, str]) -> tuple[str | None, str | None]:
    """{ラベル: 本文} からマーカーを保持する唯一のラベルを解決する。

    マーカーは単独行としてのみ検出する (marker_line_present)。戻り値は
    (ラベル, 失敗理由)。成功時は (ラベル, None)。マーカーを保持するラベルが
    0 件なら (None, "マーカーが 0 件")、複数件なら該当ラベルを含む理由文字列
    を返す (fail-closed の理由を判別可能にする)。
    """
    holders = [label for label, text in named_texts.items() if marker_line_present(text)]
    if len(holders) == 0:
        return None, "マーカーが 0 件"
    if len(holders) > 1:
        return None, f"マーカーが複数 part に存在する ({', '.join(sorted(holders))})"
    return holders[0], None


def resolve_face_path(face: str) -> tuple[Path | None, str | None]:
    """配送面から、実際にマーカーを保持するファイルの Path を解決する。

    戻り値は (path, 失敗理由)。fable / subagent は固定ファイルのため常に成功する。
    sonnet はマーカーを保持する part がちょうど 1 つの場合のみ成功し、それ以外
    (0 件・複数件) は resolve_marker_holder の理由をそのまま伝播する。
    """
    if face == "always-fable.md":
        return FABLE_MD, None
    if face == "subagent-rules.md":
        return SUBAGENT_MD, None
    if face == "always-sonnet-{1,2,3}.md":
        holder, reason = resolve_marker_holder(
            {name: read(path) for name, path in SONNET_MD.items()}
        )
        if reason is not None:
            return None, reason
        return SONNET_MD[holder], None
    raise ValueError(f"unknown face: {face}")


def compose_face(text: str) -> tuple[str | None, str | None, str | None]:
    """text から (ルールブロック, 冒頭ヘッダ, 失敗理由) を解決する。

    失敗理由は「ルールブロックが抽出できない (マーカー不在または境界検出失敗)」
    「冒頭 HTML コメントヘッダが無い」のいずれかを判別可能にする。ヘッダ未解決を
    「空のヘッダ」へ silent fallback しない (マーカー解決・ルールブロック抽出と
    同じ fail-closed 姿勢に揃える)。
    """
    block = rule_block(text)
    if block is None:
        return None, None, "ルールブロックが抽出できない (マーカー不在または境界検出失敗)"
    header = leading_html_comment(text)
    if header is None:
        return None, None, "冒頭 HTML コメントヘッダが無い"
    return block, header, None


def resolve_face(
    face: str,
) -> tuple[Path | None, str | None, str | None, str | None]:
    """配送面から (path, ルールブロック, 冒頭ヘッダ, 失敗理由) を解決する。

    失敗理由が None であれば成功 (path/block/header がすべて非 None)。失敗
    理由は resolve_face_path (マーカー 0 件 / 複数 part) または compose_face
    (ブロック抽出不能 / ヘッダ欠落) のいずれかに由来し、判別可能な文字列として
    そのまま呼び出し側 (各検査の violation メッセージ) へ伝播する。
    """
    path, reason = resolve_face_path(face)
    if reason is not None:
        return None, None, None, reason
    block, header, reason = compose_face(read(path))
    if reason is not None:
        return None, None, None, reason
    return path, block, header, None


def run_hook(
    script: Path,
    payload: dict[str, str],
    tmp_dir: str,
    args: list[str] | None = None,
) -> str:
    """hook script を隔離 TMPDIR で実行し、additionalContext を返す。

    実リポジトリの ``${TMPDIR:-/tmp}/agent-discipline-state`` を汚さないよう、
    呼び出し側が用意した一時ディレクトリを TMPDIR として渡す。
    """
    env = os.environ.copy()
    env["TMPDIR"] = tmp_dir
    result = subprocess.run(
        ["/bin/bash", str(script), *(args or [])],
        cwd=REPO_ROOT,
        env=env,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{script.name} 異常終了 (code={result.returncode}): {result.stderr}"
        )
    if not result.stdout.strip():
        raise AssertionError(
            f"{script.name} が additionalContext を出力しなかった (stderr: {result.stderr})"
        )
    data = json.loads(result.stdout)
    return data["hookSpecificOutput"]["additionalContext"]


def delivery_fable(tmp_dir: str) -> str:
    """SessionStart (inject-always.sh) が fable 判定時に配送する additionalContext。"""
    payload = {
        "session_id": "size-budget-fable",
        "hook_event_name": "SessionStart",
        "model": "claude-fable-5",
    }
    return run_hook(INJECT_ALWAYS_SH, payload, tmp_dir)


def delivery_sonnet_part1_self_gate(tmp_dir: str) -> str:
    """SessionStart (inject-always.sh) がモデル判定不能時に配送する、self-gate
    前置き + always-sonnet-1.md (part 1 の最大構成)。
    """
    payload = {"session_id": "size-budget-sonnet-1", "hook_event_name": "SessionStart"}
    return run_hook(INJECT_ALWAYS_SH, payload, tmp_dir)


def delivery_sonnet_part_self_gate(part: str, tmp_dir: str) -> str:
    """UserPromptSubmit (inject-rules-part.sh <part>) が self-gate 付きで配送する、
    part-self-gate.md + always-sonnet-<part>.md (part 2/3 の最大構成)。
    """
    session_id = f"size-budget-sonnet-{part}"
    state_dir = Path(tmp_dir) / "agent-discipline-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"pending-model-{session_id}").write_text("", encoding="utf-8")
    payload = {"session_id": session_id, "hook_event_name": "UserPromptSubmit"}
    return run_hook(INJECT_RULES_PART_SH, payload, tmp_dir, args=[part])


def delivery_subagent(tmp_dir: str) -> str:
    """SubagentStart (inject-subagent-rules.sh) が配送する subagent-rules.md 全文。"""
    return run_hook(INJECT_SUBAGENT_RULES_SH, {}, tmp_dir)


def delivery_fable_one_shot_correction(tmp_dir: str) -> str:
    """UserPromptSubmit (resolve-model-on-prompt.sh) が、判定不能だったセッションを
    Fable と確定した際に配送する、self-heal + one-shot 補正 prefix +
    always-fable.md の最大構成。

    この経路は SessionStart 側の inject-always.sh のような段階的縮退ガード
    (8K 超過時の delivery-note 省略等) を持たない単一構成のため、この契約テスト
    での検出が予算超過に対する唯一の防衛線になる。
    """
    session_id = "size-budget-fable-correction"
    state_dir = Path(tmp_dir) / "agent-discipline-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"pending-model-{session_id}").write_text("", encoding="utf-8")

    transcript_path = Path(tmp_dir) / "transcript.jsonl"
    transcript_path.write_text(
        json.dumps(
            {"type": "assistant", "message": {"model": "claude-fable-5"}},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    payload = {
        "session_id": session_id,
        "hook_event_name": "UserPromptSubmit",
        "transcript_path": str(transcript_path),
    }
    return run_hook(RESOLVE_MODEL_ON_PROMPT_SH, payload, tmp_dir)


class MarkerPresenceTests(unittest.TestCase):
    """rule:comment-currency マーカーが各配送面に規定回数だけ単独行として存在すること。"""

    def test_fable_marker_appears_exactly_once(self) -> None:
        count = count_marker_lines(read(FABLE_MD))
        self.assertEqual(1, count, f"always-fable.md 内のマーカー出現数: {count}")

    def test_sonnet_marker_appears_exactly_once_across_parts(self) -> None:
        total = sum(count_marker_lines(read(path)) for path in SONNET_MD.values())
        self.assertEqual(
            1, total, f"always-sonnet-{{1,2,3}}.md 合計のマーカー出現数: {total}"
        )

    def test_subagent_marker_appears_exactly_once(self) -> None:
        count = count_marker_lines(read(SUBAGENT_MD))
        self.assertEqual(1, count, f"subagent-rules.md 内のマーカー出現数: {count}")


class CanonicalBodyTests(unittest.TestCase):
    """各配送面のルールブロックに canonical 文が文単位の完全一致で含まれ、
    期待される見出し行で始まること。
    """

    def test_canonical_sentences_present_in_rule_block(self) -> None:
        violations = []
        for face in FACES:
            _path, block, _header, reason = resolve_face(face)
            if reason is not None:
                violations.append(f"{face} ({reason})")
                continue
            missing = missing_canonical_sentences(block, CANONICAL_SENTENCES[face])
            if missing:
                violations.append(f"{face}: {missing}")
        self.assertEqual(
            [], violations, f"ルールブロックに canonical 文 (完全一致) が無い面: {violations}"
        )

    def test_rule_block_starts_with_expected_heading(self) -> None:
        violations = []
        for face in FACES:
            _path, block, _header, reason = resolve_face(face)
            if reason is not None:
                violations.append(f"{face} ({reason})")
                continue
            if not block_starts_with_expected_heading(block):
                violations.append(face)
        self.assertEqual(
            [], violations, f"ルールブロック先頭に期待される見出し行が無い面: {violations}"
        )

    def test_rule_block_contains_required_structural_labels(self) -> None:
        violations = []
        for face in FACES:
            _path, block, _header, reason = resolve_face(face)
            if reason is not None:
                violations.append(f"{face} ({reason})")
                continue
            missing = missing_structural_labels(block, STRUCTURAL_ELEMENT_LABELS[face])
            if missing:
                violations.append(f"{face}: {missing}")
        self.assertEqual(
            [], violations, f"ルールブロックに必須の構造要素ラベルが無い面: {violations}"
        )


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class SizeBudgetTests(unittest.TestCase):
    """各配送経路が実際に生成する additionalContext が配送予算 (8,000 UTF-16
    units 以下) に収まること。

    DELIVERY_BUILDERS のキーが計測対象の配送経路の一覧そのものである
    (fable 向け always 配送・sonnet part 1/2/3 配送・subagent-rules 配送・fable
    one-shot 補正配送の計 6 経路)。fable one-shot 補正配送 (resolve-model-on-
    prompt.sh) は他経路と異なり段階的縮退ガードを持たないため、本テストでの
    検出が予算超過に対する唯一の防衛線になる。判定は共有述語 within_size_budget
    を使う。
    """

    DELIVERY_BUILDERS = {
        "fable 向け always 配送 (inject-always.sh, model=fable)": delivery_fable,
        "sonnet part 1 配送 (inject-always.sh, self-gate)": delivery_sonnet_part1_self_gate,
        "sonnet part 2 配送 (inject-rules-part.sh 2, self-gate)": (
            lambda tmp_dir: delivery_sonnet_part_self_gate("2", tmp_dir)
        ),
        "sonnet part 3 配送 (inject-rules-part.sh 3, self-gate)": (
            lambda tmp_dir: delivery_sonnet_part_self_gate("3", tmp_dir)
        ),
        "subagent-rules 配送 (inject-subagent-rules.sh)": delivery_subagent,
        "fable one-shot 補正配送 (resolve-model-on-prompt.sh)": (
            delivery_fable_one_shot_correction
        ),
    }

    def test_all_delivery_paths_within_budget(self) -> None:
        violations = []
        for label, builder in self.DELIVERY_BUILDERS.items():
            with tempfile.TemporaryDirectory() as tmp_dir:
                context = builder(tmp_dir)
            length = utf16_length(context)
            if not within_size_budget(length):
                violations.append(f"{label}: {length}")
        self.assertEqual(
            [],
            violations,
            f"配送予算 ({SIZE_BUDGET_UNITS} UTF-16 units 以下) を超過: {violations}",
        )


class RuleBlockSelfComplianceTests(unittest.TestCase):
    """ルール本文ブロック自身が禁止対象の経緯記述を含まないこと (自己準拠)。"""

    def test_rule_blocks_free_of_history_references(self) -> None:
        violations = []
        for face in FACES:
            _path, block, _header, reason = resolve_face(face)
            if reason is not None:
                violations.append(f"{face} ({reason})")
                continue
            found = static_surface_history_matches(block)
            if found:
                violations.append(f"{face} ({', '.join(found)} を含む)")
        self.assertEqual([], violations, f"ルールブロックの自己準拠違反: {violations}")


class HeaderSelfComplianceTests(unittest.TestCase):
    """面ごとに解決した冒頭 HTML コメントヘッダが経緯記述を含まないこと。

    ルールブロック自己準拠検査 (RuleBlockSelfComplianceTests) の共有述語
    (static_surface_history_matches) に、ヘッダ限定の既知ナラティブ語彙ブロック
    リスト (static_surface_narrative_vocabulary_matches) を加えた
    static_surface_header_matches を使う。
    """

    def test_headers_free_of_issue_numbers_and_dates(self) -> None:
        violations = []
        for face in FACES:
            _path, _block, header, reason = resolve_face(face)
            if reason is not None:
                violations.append(f"{face} ({reason})")
                continue
            found = static_surface_header_matches(header)
            if found:
                violations.append(f"{face} ({', '.join(found)} を含む)")
        self.assertEqual([], violations, f"ヘッダの自己準拠違反: {violations}")


class HelperSyntheticContractTests(unittest.TestCase):
    """実ファイルに依存しない synthetic 入力で、共有 helper 自身の挙動を固定する。"""

    def test_canonical_phrase_outside_block_is_not_matched(self) -> None:
        """ブロック外にのみ存在する文は、ブロック抽出後の文単位照合では
        見つからないこと (ファイル全文/連結検索への回帰を防ぐ)。
        """
        sentence = "synthetic canonical sentence。"
        text = (
            f"{sentence} (before marker, must not count)\n\n"
            f"{MARKER}\n"
            "## synthetic heading\n\n"
            "block body without the sentence。\n\n"
            "---\n\n"
            f"{sentence} (after boundary, must not count)\n"
        )
        block = rule_block(text)
        self.assertIsNotNone(block)
        self.assertFalse(block_matches_canonical_sentence(block, sentence))

    def test_non_structural_comment_does_not_truncate_block(self) -> None:
        """ブロック内の非構造 HTML コメント (`<!-- rule:` / `<!-- subagent-rule:`
        以外) はブロック境界にならないこと (行頭の構造マーカー限定への変更の固定)。
        """
        text = (
            f"{MARKER}\n"
            "## synthetic heading\n\n"
            "before comment\n"
            "<!-- explanatory note, not a structural marker -->\n"
            "after comment (must remain inside the block)\n\n"
            "---\n"
        )
        block = rule_block(text)
        self.assertIsNotNone(block)
        self.assertIn("after comment (must remain inside the block)", block)

    def test_heading_detection_follows_markdown_indentation_rule(self) -> None:
        """見出し行の先頭インデントは Markdown の ATX 見出し規則 (3 個以下の
        ASCII 空白まで) に従うこと。4 空白以上・タブ字下げの `## ...` 行は
        Markdown ではインデント付きコードブロックとして描画されるため見出しと
        して受理せず、3 空白以下の字下げは受理すること。
        """
        no_indent = f"## 10. {EXPECTED_HEADING_TEXT}"
        three_spaces = f"   ## 10. {EXPECTED_HEADING_TEXT}"
        four_spaces = f"    ## 10. {EXPECTED_HEADING_TEXT}"
        tab_indent = f"\t## 10. {EXPECTED_HEADING_TEXT}"

        self.assertTrue(block_starts_with_expected_heading(no_indent))
        self.assertTrue(block_starts_with_expected_heading(three_spaces))
        self.assertFalse(block_starts_with_expected_heading(four_spaces))
        self.assertFalse(block_starts_with_expected_heading(tab_indent))

    def test_structural_label_check_fails_when_why_paragraph_missing(self) -> None:
        """「なぜ」段落 (`**なぜ**:`) を欠いた synthetic ブロックで、必須構造
        要素ラベルの検査がその欠落を検出すること。
        """
        block_without_why = (
            "## 10. synthetic heading\n\n"
            "**指示**: do the thing。\n\n"
            "**境界**: except when not。\n"
        )
        missing = missing_structural_labels(
            block_without_why, STRUCTURAL_ELEMENT_LABELS["always-fable.md"]
        )
        self.assertIn("**なぜ**:", missing)
        self.assertNotIn("**指示**:", missing)
        self.assertNotIn("**境界**:", missing)

    def test_marker_must_be_a_standalone_line(self) -> None:
        """マーカーが prose 文中や inline code 中に偶発的に現れても、行全体
        一致 (行頭から行末までがマーカーのみ) でなければ検出されないこと。
        """
        prose_mention = f"この文書では {MARKER} という形式のマーカーについて説明する。\n"
        inline_code_mention = f"マーカーの例: `{MARKER}` を参照。\n"
        standalone_line = f"{MARKER}\n"

        self.assertEqual(0, count_marker_lines(prose_mention))
        self.assertEqual(0, count_marker_lines(inline_code_mention))
        self.assertEqual(1, count_marker_lines(standalone_line))

        self.assertIsNone(rule_block(prose_mention + "body\n"))
        self.assertIsNotNone(rule_block(standalone_line + "body\n---\n"))

    def test_marker_holder_resolution_is_fail_closed(self) -> None:
        """マーカー保持ラベルが 0 件・複数件のときは解決が fail-closed になり、
        失敗理由を判別できること。ちょうど 1 件のときのみそのラベルを返すこと。
        マーカーは単独行としてのみ検出されるため、synthetic テキストでも
        マーカーを単独行で配置する (mid-sentence 埋め込みは「0 件」扱いになる)。
        """
        holder, reason = resolve_marker_holder(
            {"a": "no marker here", "b": "still none"}
        )
        self.assertIsNone(holder)
        self.assertIn("0 件", reason)

        holder, reason = resolve_marker_holder(
            {"a": f"prefix\n{MARKER}\nsuffix", "b": f"prefix\n{MARKER}\nsuffix"}
        )
        self.assertIsNone(holder)
        self.assertIn("複数", reason)

        holder, reason = resolve_marker_holder(
            {"a": f"prefix\n{MARKER}\nsuffix", "b": "no marker"}
        )
        self.assertEqual("a", holder)
        self.assertIsNone(reason)

    def test_canonical_sentence_matching_is_symmetric_and_paragraph_aware(
        self,
    ) -> None:
        """文単位の完全一致は、ASCII 空白位置での折返し・インデント付き継続行
        には対称正規化により頑健に一致し、段落分割 (空行。空白のみの行を含む)
        を跨ぐ場合は一致しないこと。
        """
        sentence = "AはBでありCである。"
        split_by_paragraph = "AはB\n\nでありCである。"
        wrapped_at_ascii_space = "AはB\nでありCである。"
        indented_continuation = "AはB\n    であり C である。"
        split_by_whitespace_only_blank_line = "AはB\n   \nでありCである。"

        self.assertFalse(
            block_matches_canonical_sentence(split_by_paragraph, sentence)
        )
        self.assertTrue(
            block_matches_canonical_sentence(wrapped_at_ascii_space, sentence)
        )
        self.assertTrue(
            block_matches_canonical_sentence(indented_continuation, sentence)
        )
        self.assertFalse(
            block_matches_canonical_sentence(
                split_by_whitespace_only_blank_line, sentence
            )
        )

    def test_budget_boundary_accepts_8000_and_rejects_8001(self) -> None:
        """ちょうど 8,000 UTF-16 units の文字列は共有述語 within_size_budget が
        受理し (inject-always.sh の `-gt 8000` 縮退条件と整合)、8,001 units は
        拒否すること。
        """
        at_budget = "あ" * SIZE_BUDGET_UNITS
        over_budget = "あ" * (SIZE_BUDGET_UNITS + 1)
        self.assertEqual(SIZE_BUDGET_UNITS, utf16_length(at_budget))
        self.assertEqual(SIZE_BUDGET_UNITS + 1, utf16_length(over_budget))
        self.assertTrue(within_size_budget(utf16_length(at_budget)))
        self.assertFalse(within_size_budget(utf16_length(over_budget)))

    def test_static_surface_predicate_catches_issue_prefix_variations(
        self,
    ) -> None:
        """「issue #」は直後に数字を伴わない形式・大文字小文字の表記ゆらぎ
        (「Issue #」「ISSUE #」) のいずれでも static_surface_history_matches で
        検出されること (`#[0-9]` 単独では検出できない)。
        """
        no_digit = "this references issue #N generically, not a numbered issue"
        self.assertIsNone(FORBIDDEN_ISSUE_NUMBER_PATTERN.search(no_digit))
        self.assertIn("issue #", static_surface_history_matches(no_digit))
        self.assertIn(
            "issue #", static_surface_history_matches("See Issue #42 for context")
        )
        self.assertIn(
            "issue #", static_surface_history_matches("See ISSUE #42 for context")
        )

    def test_static_surface_predicate_catches_date_with_internal_space(self) -> None:
        """年月日形式は、年 (月日) 単位と数字の間に ASCII 空白を挟む表記
        (「2026 年」等) も検出対象に含むこと。
        """
        self.assertIn("年月日形式", static_surface_history_matches("2026 年に実装した"))
        self.assertIn("年月日形式", static_surface_history_matches("2026-08-05 実測"))

    def test_missing_header_fails_closed(self) -> None:
        """冒頭 HTML コメントヘッダを持たないテキストは、ルールブロック自体は
        正常に抽出できても面の解決全体が fail-closed になり、失敗理由に
        ヘッダ欠落であることが判別できること (空文字列への silent fallback を
        せず、ヘッダ検査を空振りさせない)。
        """
        # 先頭が `<!--` で始まらない、ヘッダとなる HTML コメントが全く無い
        # テキスト (マーカーが先頭に来て構造マーカーとして誤受理されるケースは
        # test_leading_marker_line_is_not_accepted_as_header を参照)。
        text = f"no header here\n\n{MARKER}\n## synthetic heading\n\nblock body\n\n---\n"
        # fail-closed の原因がヘッダ欠如そのものであることを明確にするため、
        # rule_block 単体は正常に抽出できることを先に確認しておく。
        self.assertIsNotNone(rule_block(text))
        self.assertIsNone(leading_html_comment(text))
        block, header, reason = compose_face(text)
        self.assertIsNone(block)
        self.assertIsNone(header)
        self.assertIn("ヘッダ", reason)

    def test_leading_marker_line_is_not_accepted_as_header(self) -> None:
        """ファイル先頭が記述的なヘッダではなく構造マーカー行そのもの
        (`<!-- rule:` / `<!-- subagent-rule:` 形式) である場合、ヘッダとして
        受理せず fail-closed (ヘッダ欠落扱い) になること (記述ヘッダが失われて
        ファイル先頭が rule マーカーになった場合の検出)。
        """
        text = f"{MARKER}\n## synthetic heading\n\nblock body\n\n---\n"
        self.assertIsNone(leading_html_comment(text))
        block, header, reason = compose_face(text)
        self.assertIsNone(block)
        self.assertIsNone(header)
        self.assertIn("ヘッダ", reason)

        subagent_style_marker_text = "<!-- subagent-rule:report-facts -->\nbody\n"
        self.assertIsNone(leading_html_comment(subagent_style_marker_text))

    def test_header_narrative_vocabulary_blocklist_catches_known_words(self) -> None:
        """ヘッダ限定の既知ナラティブ語彙ブロックリストが、識別子述語では
        検出できない自由記述の経緯語彙 (多文字句「以前」等、句境界付きの「旧」)
        を検出すること。ルールブロック用の共有述語
        (static_surface_history_matches) 単体では検出できないことも合わせて
        確認する。
        """
        text = "この実装は旧実装を置き換えたものである"
        self.assertEqual([], static_surface_history_matches(text))
        self.assertIn("旧", static_surface_narrative_vocabulary_matches(text))
        self.assertIn("旧", static_surface_header_matches(text))

    def test_narrative_vocabulary_legacy_prefix_requires_qualifying_continuation(
        self,
    ) -> None:
        """「旧」は後続が設計/実装/形式/来、またはファイル名・識別子的な連なり
        の場合のみナラティブ句として検出され、無関係な後続 (仮名遣い等) では
        検出されないこと。多文字句 (「以前」等) は引き続き裸の部分文字列一致で
        検出されること (境界付けの対象外)。
        """
        self.assertIn(
            "旧", static_surface_narrative_vocabulary_matches("旧実装の説明を読む")
        )
        self.assertIn(
            "旧", static_surface_narrative_vocabulary_matches("旧設計を踏襲している")
        )
        self.assertIn(
            "旧", static_surface_narrative_vocabulary_matches("旧形式のまま残す")
        )
        self.assertIn(
            "旧", static_surface_narrative_vocabulary_matches("旧来の方式を使う")
        )
        self.assertIn(
            "旧",
            static_surface_narrative_vocabulary_matches(
                "旧always-sonnet.md を参照のこと"
            ),
        )
        self.assertNotIn(
            "旧",
            static_surface_narrative_vocabulary_matches("これは旧仮名遣いの例だ"),
        )
        self.assertIn(
            "以前", static_surface_narrative_vocabulary_matches("以前はこうだった")
        )


if __name__ == "__main__":
    unittest.main()
