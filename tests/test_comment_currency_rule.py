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
   必須の構造要素ラベル (太字見出し + コロン、面ごとに異なる) を含み、
   canonical 文セット (文単位の完全一致) を含むこと。さらにブロック全体が、
   段落構造を保持したまま対称正規化した後 CONFIRMED_BLOCK_TEXT (配送本文の
   確定内容の正本) と完全一致すること — 文単位・見出し・ラベルの
   個別検査は canonical 要素を保持したままの追記 (追加型矛盾) を見逃すため、
   ブロック全体一致が最終的な契約になる
3. 各配送経路 — SessionStart (inject-always.sh の fable 配送・sonnet part 1
   self-gate 配送)、UserPromptSubmit (inject-rules-part.sh の sonnet part 2/3
   self-gate 配送、resolve-model-on-prompt.sh の Fable one-shot 補正配送)、
   SubagentStart (inject-subagent-rules.sh の配送) — が実際に生成する
   additionalContext の最大構成が UTF-16 code units 8,000 以下 (inject-always.sh
   の `-gt 8000` 縮退条件と整合する境界) に収まること (判定は単一の共有述語
   within_size_budget を使う)。6 経路すべてこの検査を無条件に実行する。
   段階的縮退ガードを持つ 2 経路 (inject-always.sh 系) はさらに、
   inject-always.sh が明示的に許可する第一段縮退 ((参照パス) 行のみを落とす)
   は許容しつつ、配送メモ (delivery-note.md) 本文の残存は無条件必須とする
   (「縮退後の payload がたまたま収まっただけ」の本文喪失・第二段以降の
   縮退を green にしない)
4. ルール本文ブロック自身、および面ごとに解決した冒頭 HTML コメントヘッダが、
   面固有の静的自己準拠述語 (static_surface_*、定義直前のコメント参照) が
   定める禁止対象の経緯記述 (issue/PR 番号・年月日。大文字小文字や年月日単位
   前の空白の表記ゆらぎを含む) を含まないこと。ヘッダはさらに既知ナラティブ
   語彙のブロックリストでも検査する (ルール本文は禁止形式を引用言及するため
   対象外)
5. agent-discipline plugin の version (plugin.json) が、この機能追加が要求
   する下限以上であること (既存の version policy 検査は patch bump のみでも
   green になるため、この機能追加固有の下限を独立に固定する)

面の解決 (resolve_face) は失敗原因 (マーカー 0 件 / 複数 part / ブロック抽出
不能 / ヘッダ欠落) を判別可能な形で返し、全検査の失敗メッセージに実際の原因が
表示される。共有ロジックは、実ファイルに依存しない synthetic な入力での
自己テストも別途持つ。
"""

from __future__ import annotations

import inspect
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
AGENT_DISCIPLINE_PLUGIN_JSON = (
    REPO_ROOT / "plugins" / "agent-discipline" / ".claude-plugin" / "plugin.json"
)

# rule:comment-currency 追加が要求する agent-discipline plugin の version の
# 下限。ちょうど 0.25.0 には固定しない (以上判定のみ) — 次回以降の bump で
# 恒久テストが壊れるのを避けるため、下限のみを契約とする。
MINIMUM_AGENT_DISCIPLINE_VERSION = (0, 25, 0)

FABLE_MD = PROMPTS / "always-fable.md"
SONNET_MD = {
    "always-sonnet-1.md": PROMPTS / "always-sonnet-1.md",
    "always-sonnet-2.md": PROMPTS / "always-sonnet-2.md",
    "always-sonnet-3.md": PROMPTS / "always-sonnet-3.md",
}
SUBAGENT_MD = PROMPTS / "subagent-rules.md"
DELIVERY_NOTE_MD = PROMPTS / "delivery-note.md"

INJECT_ALWAYS_SH = SCRIPTS / "inject-always.sh"
INJECT_RULES_PART_SH = SCRIPTS / "inject-rules-part.sh"
INJECT_SUBAGENT_RULES_SH = SCRIPTS / "inject-subagent-rules.sh"
RESOLVE_MODEL_ON_PROMPT_SH = SCRIPTS / "resolve-model-on-prompt.sh"

# inject-always.sh の段階的縮退 (8K 超過時) が最初に落とす要素の接頭辞。
# inject-always.sh はこの行のみを落とす第一段縮退を明示的に許可しているため、
# この行の欠落単独は fail 条件にしない (pre_degradation_missing_elements 参照)。
# 深い checkout パス等で正当に発動しうる縮退を誤検知しないための境界。
PATH_LINE_PREFIX = "(参照パス) "

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
        "commit message・PR 説明・issue body、および明示的に履歴を目的とする文書は対象外。",
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
        "commit message・PR 説明・issue body、および明示的に履歴を目的とする文書は対象外。",
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
        "commit message・PR 説明・issue body と、明示的に履歴を目的とする文書は対象外。",
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

# 配送する確定本文 3 面の全文 (マーカー直後から末尾まで、rule_block() の
# 抽出単位と同一)。ブロック全体一致検査
# (test_rule_block_matches_confirmed_body_exactly) の正本であり、この
# 定数からそのまま各面の確定本文を逐語復元できる。
#
# 配置先の決定 (契約): sonnet 面の配置先は always-sonnet-2.md とする (3 part
# (always-sonnet-{1,2,3}.md) のうち配送予算の残余が最大のため)。ルール番号
# ("## N.") は 3 part 集合全体で一意に振られ、単一 part 内での連番性は
# 要求しない — always-sonnet-2.md は既存ルール 3〜6 を持つため、本ルールの
# 見出し番号がその直後の連番になるとは限らない。FACES / CANONICAL_SENTENCES /
# STRUCTURAL_ELEMENT_LABELS の "always-sonnet-{1,2,3}.md" キーは、マーカーを
# 保持する part を動的に解決する (resolve_face_path 参照) ため、この配置先
# 決定自体をテストの検査対象として固定するものではない。
CONFIRMED_BLOCK_TEXT = {
    'always-fable.md': (
        '\n## 10. 説明は常に最新の内容のみ\n\n**なぜ**: 履歴の正規の置き場は git log / PR / issue であり、コメント・README に書いた経緯は更新されず腐る。読者の多くは AI エージェントでリポジトリ内テキストを信頼ソースとして扱うため、古い経緯記述は誤誘導になる。セッションへ注入される文書では経緯記述がトークンと配送予算を恒常的に消費する。\n\n**指示**: コードコメント・docstring・README 等の説明文書には現在の内容に対する説明のみを書き、過去の経緯・変更履歴の解説 (版数・日付・issue/PR 番号による過去の変更の記述、旧実装の説明、移設・置換・廃止の記録、不採用案の経緯記録、出典としての issue/PR 番号参照) を書かない。履歴と検討経緯は commit message・PR 説明・issue に置く。契約・制約は issue 参照に頼らずその場で完結して書き、コード変更で説明が古くなる場合は同時に更新する。適用は touch-time — 新規作成・意味変更した説明ブロックに適用し、指示のない一括清掃や単純移設・整形での書き換え波及は行わない。\n\n**境界**: 例外は 2 つ — (1) 撤去条件付き暫定措置は「現在の不具合・撤去条件・確認方法」の 3 要素で書く (導入日は書かない) (2) 現行の主張への検証日・検証環境の付記は証拠の鮮度情報として許可する。commit message・PR 説明・issue body、および明示的に履歴を目的とする文書は対象外。過去に言及しない現在形の設計理由の説明は禁止対象ではない。\n\n'
    ),
    'always-sonnet-{1,2,3}.md': (
        '\n## 10. 説明は常に最新の内容のみ\n\n**適用範囲**: コードコメント・docstring・README 等、リポジトリ内の説明文書を新規作成・編集するすべての場面に適用する。\n\n説明文書には現在の内容に対する説明のみを書き、過去の経緯・変更履歴の解説を書かない。禁止対象: 版数・日付・issue/PR 番号による過去の変更の記述 (「vX で追加」「#N で移設」等)、旧実装の説明 (「以前は〜だったが」)、移設・置換・廃止の記録、不採用案の経緯記録、出典としての issue/PR 番号参照。履歴と検討経緯は commit message・PR 説明・issue に置く。契約・制約は issue 参照に頼らず、その場で読んで完結するように書く。コード変更で対応する説明が古くなる場合は同時に更新する。\n\n適用は touch-time: 新規作成・意味を変更した説明ブロックに適用する。単純移設・整形のみの変更で既存記述の書き換えに波及させず、指示のない一括清掃を行わない。\n\n**なぜ**: 履歴の正規の置き場は git log / PR / issue であり、コメント・README に書いた経緯は更新されず腐る。読者の多くは AI エージェントでリポジトリ内テキストを信頼ソースとして扱うため、古い経緯記述は誤誘導になる。セッションへ注入される文書では経緯記述が毎セッションのトークンと配送予算を消費する。\n\n**境界**: 例外は 2 つ — (1) 撤去条件付き暫定措置は「現在の不具合・撤去条件・確認方法」の 3 要素で書く (導入日は書かない) (2) 現行の主張への検証日・検証環境の付記 (「YYYY-MM-DD 実測」「バージョン X で確認」等) は証拠の鮮度情報として許可する。commit message・PR 説明・issue body、および明示的に履歴を目的とする文書は対象外。過去に言及しない現在形の設計理由 (「なぜこうするか」「X 方式は〜のため使わない」) は禁止対象ではない。\n\n**例**:\n- 悪い例: リファクタリング時に「以前の実装を issue 対応で置き換えた」という経緯コメントを版数・issue 番号付きで書き添える\n- 良い例: 現在の実装が前提とする制約のみをコメントに書き、置き換えの経緯は commit message と PR 説明に書く\n'
    ),
    'subagent-rules.md': (
        '\n## 5. 説明は常に最新の内容のみ\n\n**適用範囲**: コードコメント・docstring・README 等、リポジトリ内の説明文書を新規作成・編集するすべての場面に適用する。\n\n説明文書には現在の内容に対する説明のみを書き、過去の経緯・変更履歴の解説 (版数・日付・issue/PR 番号による過去の変更の記述、旧実装の説明、出典としての issue/PR 番号参照) を書かない。履歴は commit message・PR 説明・issue に置く。契約・制約は issue 参照に頼らずその場で完結して書き、編集で説明が古くなる場合は同時に更新する。適用は touch-time — 新規作成・意味変更した説明ブロックに適用し、指示のない一括清掃を行わない。commit message・PR 説明・issue body と、明示的に履歴を目的とする文書は対象外。例外: 撤去条件付き暫定措置の「現在の不具合・撤去条件・確認方法」(導入日なし) と、現行の主張への検証日・検証環境の付記。過去に言及しない現在形の設計理由の説明は禁止対象ではない。\n\n**なぜ**: 履歴の正規の置き場は git であり、説明文書内の経緯は更新されず腐って後続の読者 (主に AI エージェント) を誤誘導する。\n'
    ),
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


def parse_semver_triple(version: str) -> tuple[int, int, int]:
    """"X.Y.Z..." 形式の文字列の先頭 3 要素を (major, minor, patch) の int
    タプルに変換する (pre-release/build メタデータが付いていても patch の
    数字部分のみを読む)。
    """
    major, minor, patch = version.split(".")[:3]
    patch_number = re.match(r"[0-9]+", patch)
    return int(major), int(minor), int(patch_number.group(0) if patch_number else "0")


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


BLANK_LINE_PATTERN = re.compile(r"^[ \t]*$")


def is_ascii_blank_line(line: str) -> bool:
    """line が ASCII 空白・タブのみ (または完全な空文字列) で構成されるかを
    判定する。

    Python 既定の `str.strip()` は全角空白 (U+3000) や NBSP 等の Unicode
    空白も除去するため、それらのみからなる行を誤って「空行」と判定して
    しまう。Markdown (CommonMark) の空行判定は ASCII 空白・タブのみを
    非有意とするため、段落分割 (split_into_paragraphs) はこの ASCII 限定
    判定を使う (全角空白のみの行は Markdown 上 soft-wrap の継続行になり、
    新しい段落を作らないため)。
    """
    return BLANK_LINE_PATTERN.match(line) is not None


def is_unicode_blank_line(line: str) -> bool:
    """line が (全角空白・NBSP 等を含む) Unicode 空白のみ、または完全な
    空文字列で構成されるかを判定する (Python 既定の `str.strip()` と同じ判定)。

    段落分割 (is_ascii_blank_line、Markdown の空行判定に合わせ ASCII 限定)
    とは別の目的 — 見出し行探索 (block_heading_line) では、Markdown 上の
    段落境界とは独立に「視覚的に空白しかない行」を先頭候補から除外したい
    ため、こちらは Unicode 全体で判定する。
    """
    return line.strip() == ""


def block_heading_line(block: str) -> str | None:
    """block 内の最初の非空行 (Unicode 空白のみの行は空行扱い) を返す
    (見出し行の検査に使う)。無ければ None。
    """
    for line in block.splitlines():
        if not is_unicode_blank_line(line):
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

    空行は完全な空文字列の行だけでなく、ASCII 空白・タブのみの行
    (行頭から行末までが ASCII 空白・タブのみ、is_ascii_blank_line 参照) も
    区切りとみなす (厳密な二重改行一致では、空白入り空行を挟んだ 2 段落が
    1 段落として扱われてしまう)。全角空白 (U+3000) や NBSP 等の Unicode
    空白のみの行は Markdown 上は空行にならない (soft-wrap の継続行として
    扱われる) ため区切りとみなさない (is_ascii_blank_line 参照)。段落内部の
    空白 (全角空白を含む) は後続の文正規化 (strip_whitespace) が対称に
    除去するため、ここでの ASCII 限定は段落境界の判定にのみ影響し、内容の
    一致判定には影響しない。連続する空行はまとめて 1 つの区切りとして扱い、
    前後の空行のみからなる要素は含めない。
    """
    paragraphs: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if is_ascii_blank_line(line):
            if current:
                paragraphs.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append("\n".join(current))
    return paragraphs


def strip_whitespace(text: str) -> str:
    """text から Unicode 空白 (ASCII 空白・タブ・改行に加え、全角空白 U+3000・
    NBSP 等を含む) をすべて除去する (対称正規化)。

    候補側 (ブロックから抽出した文・段落) と canonical/confirmed body 側の
    双方に同じ関数を適用することで、継続行の先頭インデントや句読点隣接の
    折返し空白による非対称を解消する。段落分割 (split_into_paragraphs) の
    後に適用する — 空行・空白のみの行を挟んだ 2 段落は、この関数を通す前の
    段階で既に別要素になっているため、ここで空白を除去しても再結合しない。
    段落分割自体は ASCII 限定の空行判定 (is_ascii_blank_line) のままだが、
    全角空白のみの行は段落を割らずに同一段落へ soft-wrap 連結されたうえで
    ここで除去されるため、比較両辺を対称に扱える。
    """
    return re.sub(r"\s+", "", text)


def block_sentence_set(block: str) -> set[str]:
    """block を段落 → 文 (「。」区切り) に分割し、各文を strip_whitespace
    で正規化した集合を返す。

    段落分割を先に行うため、空行 (ASCII 空白のみの行を含む) を挟んで 2 段落
    にまたがる文字列は 1 つの文として結合されない。
    """
    sentences: set[str] = set()
    for paragraph in split_into_paragraphs(block):
        for sentence in re.findall(r"[^。]*。", paragraph):
            sentences.add(strip_whitespace(sentence))
    return sentences


def block_matches_canonical_sentence(block: str, canonical: str) -> bool:
    """block 内のいずれかの文要素が canonical と完全一致するか (対称正規化後)。"""
    return strip_whitespace(canonical) in block_sentence_set(block)


def missing_canonical_sentences(block: str, canonicals: tuple[str, ...]) -> list[str]:
    """canonicals のうち block 内に文要素として完全一致で存在しないものを返す。"""
    sentences = block_sentence_set(block)
    return [c for c in canonicals if strip_whitespace(c) not in sentences]


def missing_structural_labels(block: str, labels: tuple[str, ...]) -> list[str]:
    """labels のうち、block 内のいずれの行の先頭 (空白 3 個以下の字下げまで
    許容、タブ字下げは不可) にも現れないものを返す。

    STRUCTURAL_ELEMENT_LABELS の各ラベル (太字見出し + コロン) が、規律の
    記述形式が要求する段落として実在するかを検査する。単純な部分文字列包含
    では、実際の構造段落が削除されていても prose や例文中の偶発出現で
    「ラベルあり」と誤判定される (false-green) ため、マーカー行アンカー化
    (marker_line_pattern)・見出し字下げ規則 (HEADING_INDENT_PATTERN) と同じ
    発想で行頭アンカーの一致に限定する。
    """
    missing = []
    for label in labels:
        pattern = re.compile(r"(?m)^ {0,3}" + re.escape(label))
        if pattern.search(block) is None:
            missing.append(label)
    return missing


def normalize_block_for_exact_match(block: str) -> str:
    """block を近逐語比較用に正規化した 1 本の文字列を返す (ブロック全体一致検査用)。

    各行の行末の ASCII 空白・タブのみを除去し (is_ascii_blank_line と同じ
    CommonMark 準拠の空行定義を空行判定にも一貫して使う)、空行が連続する区間を
    1 行に圧縮し、先頭・末尾の空行を除去する。行内の空白 (単語間スペース含む)
    と改行位置は保持したまま比較する — 文レベルの canonical 検査
    (block_sentence_set / strip_whitespace) が採用する「全 Unicode 空白除去」の
    soft-wrap 耐性正規化とは別の、より厳密な層である。空行判定・行末除去の
    双方を ASCII 限定にすることで、全角空白 (U+3000) 等 ASCII 以外の空白のみで
    構成される行は「内容行」として保持される (Markdown 上この種の行は空行に
    ならず soft-wrap の継続行として段落を連結する意味を持つため、空行区切りを
    この種の行へ置換する変質は不一致として検出する必要がある。デフォルトの
    `str.rstrip()` は Unicode 空白も除去するため、これを使うとそのような行が
    空文字列に潰れて空行と誤認され、変質を素通りしてしまう)。「commit message」
    →「commitmessage」のような行内空白の除去変質が canonical と同一の正規化
    形になり exact-match をすり抜ける問題も、全空白を除去しないことで塞ぐ
    (2 層構成: 文レベルは soft-wrap 耐性のある診断層、ブロック全体はここでの
    厳密な契約層)。折返し位置 (改行の入る場所) 自体の変更は契約の意図的な
    更新として扱い、その場合は CONFIRMED_BLOCK_TEXT 側を実ファイルに合わせて
    更新する運用とする (この正規化を緩めて追従させない)。
    """
    lines = [line.rstrip(" \t") for line in block.splitlines()]
    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = is_ascii_blank_line(line)
        if is_blank and previous_blank:
            continue
        collapsed.append(line)
        previous_blank = is_blank
    while collapsed and is_ascii_blank_line(collapsed[0]):
        collapsed.pop(0)
    while collapsed and is_ascii_blank_line(collapsed[-1]):
        collapsed.pop()
    return "\n".join(collapsed)


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


def _utf8_locale_priority(name: str) -> int | None:
    """name が UTF-8 系 locale 名なら優先順 (0 が最優先) を、そうでなければ
    None を返す。大文字小文字・ハイフン有無の表記ゆらぎを無視して "utf8" を
    含むかどうかで判定する。優先順は C.UTF-8 系 (0) → en_US.UTF-8 系 (1) →
    その他の UTF-8 系 (2) の順。
    """
    normalized = name.lower().replace("-", "")
    if "utf8" not in normalized:
        return None
    if normalized.startswith("c."):
        return 0
    if normalized.startswith("en_us."):
        return 1
    return 2


def detect_utf8_locale() -> str | None:
    """`locale -a` の出力から利用可能な UTF-8 系 locale を優先順
    (C.UTF-8 系 → en_US.UTF-8 系 → その他の UTF-8 系、_utf8_locale_priority
    参照) で 1 つ選んで返す。無ければ None を返す。

    戻り値は run_hook が hook subprocess の env (LC_ALL/LANG) に渡す
    best-effort な補助にのみ使う。この関数が None を返しても、あるいは
    subprocess 呼び出し自体が失敗しても、いずれの検査の実行可否・合否判定
    にも影響しない (検査は常に無条件で実行する)。
    """
    try:
        result = subprocess.run(
            ["locale", "-a"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    available = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    candidates = [
        (priority, name)
        for name in available
        for priority in (_utf8_locale_priority(name),)
        if priority is not None
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


# `locale -a` はモジュール読み込み時に 1 回だけ実行する (hook 起動のたびに
# 実行するとオーバーヘッドが積み重なるため)。この値は run_hook が subprocess
# env を設定する best-effort な補助にのみ使い、どの検査も gate しない。
UTF8_LOCALE = detect_utf8_locale()

# import 時点の probe 結果の不変 snapshot。run_hook の locale 既定値が定義時
# 束縛であることの契約検査 (RunnerLocaleDefaultBindingTests) の期待値にのみ
# 使う。UTF8_LOCALE と異なり、テストからの一時的な書き換え対象にしない。
UTF8_LOCALE_AT_IMPORT = UTF8_LOCALE


def detect_non_utf8_locale() -> str | None:
    """`locale -a` の出力から制御用の単一バイト locale を優先順 (C → POSIX)
    で 1 つ選んで返す。確認できなければ None を返す。

    locale fallback 経路の実走検証 (LocaleFallbackDeliveryTests) が、テスト
    プロセスの ambient (os.environ) の LC_ALL/LANG へ一時設定する制御用
    locale の選定にのみ使う。
    契約: 候補は C と POSIX のみに限定する。任意の「非 UTF-8 系」まで許容
    すると、マルチバイト非 UTF-8 locale (EUC-JP 等) が選ばれた場合に
    `wc -m` の計上がバイト基準になる保証が無く、縮退発動の前提が崩れて
    製品欠陥なしの red を作るため。C / POSIX は文字 = 1 バイトが保証される。
    `locale -a` の実行失敗・タイムアウト・非ゼロ終了・候補ゼロはすべて
    None を返し、呼び出し側が理由付きの明示 skip に変換する (silent green に
    しない)。ホスト locale の環境生成による代替は行わない (検証はホストに
    実在する locale のみで行う)。
    """
    try:
        result = subprocess.run(
            ["locale", "-a"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    available = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if "C" in available:
        return "C"
    if "POSIX" in available:
        return "POSIX"
    return None


def run_hook(
    script: Path,
    payload: dict[str, str],
    tmp_dir: str,
    args: list[str] | None = None,
    locale: str | None = UTF8_LOCALE,
) -> str:
    """hook script を隔離 TMPDIR で実行し、additionalContext を返す。

    実リポジトリの ``${TMPDIR:-/tmp}/agent-discipline-state`` を汚さないよう、
    呼び出し側が用意した一時ディレクトリを TMPDIR として渡す。locale が
    None の場合は LC_ALL/LANG を設定せず、呼び出し元プロセスの ambient を
    subprocess へ継承させる (locale 未指定 fallback。この経路の実走検証は
    LocaleFallbackDeliveryTests が ambient を制御して行う)。locale が
    利用可能であれば LC_ALL/LANG に固定する (detect_utf8_locale 参照) が、
    これは hook script 内部 (`wc -m` 等) の locale 依存動作を安定させる
    best-effort な補助にすぎず、この Python プロセス自身の subprocess 出力
    デコードは encoding="utf-8" を明示し、親プロセスの locale 設定に依存
    させない。既定値 (locale=UTF8_LOCALE) は関数定義時点の検出済み値に
    束縛されるため、呼び出し側が明示的に locale 引数を渡さない限り、この後
    グローバル UTF8_LOCALE が (テスト等で) 書き換えられても影響を受けない
    (env pin の決定性を、locale probe 結果の後からの変更から切り離すため)。
    """
    env = os.environ.copy()
    env["TMPDIR"] = tmp_dir
    if locale is not None:
        env["LC_ALL"] = locale
        env["LANG"] = locale
    result = subprocess.run(
        ["/bin/bash", str(script), *(args or [])],
        cwd=REPO_ROOT,
        env=env,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
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


def delivery_fable(tmp_dir: str, locale: str | None = UTF8_LOCALE) -> str:
    """SessionStart (inject-always.sh) が fable 判定時に配送する additionalContext。

    locale は run_hook へそのまま渡す (既定は定義時点で束縛された検出済み
    UTF-8 locale。locale fallback 検証だけが None を明示指定し、run_hook に
    LC_ALL/LANG を設定させず、テストが制御した ambient を継承させる)。
    """
    payload = {
        "session_id": "size-budget-fable",
        "hook_event_name": "SessionStart",
        "model": "claude-fable-5",
    }
    return run_hook(INJECT_ALWAYS_SH, payload, tmp_dir, locale=locale)


def delivery_sonnet_part1_self_gate(
    tmp_dir: str, locale: str | None = UTF8_LOCALE
) -> str:
    """SessionStart (inject-always.sh) がモデル判定不能時に配送する、self-gate
    前置き + always-sonnet-1.md (part 1 の最大構成)。

    locale は run_hook へそのまま渡す (既定と None の意味は delivery_fable と
    同じ契約)。
    """
    payload = {"session_id": "size-budget-sonnet-1", "hook_event_name": "SessionStart"}
    return run_hook(INJECT_ALWAYS_SH, payload, tmp_dir, locale=locale)


def payload_content_missing(context: str, source_md: Path) -> list[str]:
    """context (hook が生成した additionalContext) に、source_md の本文全体が
    実際に含まれているかを検査する。

    サイズ (UTF-16 code unit 数) のみの検査、あるいは先頭見出し行 1 行のみの
    照合では、見出しだけ含んで本文の大半を欠落させた payload や、self-gate
    前置き等の別要素が同じ見出しを引用しているだけの payload でも green に
    なってしまう。6 経路すべての実 payload を確認したところ、各 hook は
    対応する md ファイル (冒頭のヘッダコメントを含む全文) を `$(cat ...)`
    でそのまま埋め込んでおり、bash のコマンド置換が末尾の改行を除去する
    以外は逐語一致する (空白・改行の変形は発生しない) ため、正規化なしの
    単純な substring 包含で判定できる。ヘッダコメントを除外する必要もない
    — 実 payload にヘッダコメントを含む全文がそのまま現れるため。
    """
    text = read(source_md)
    if text.rstrip("\n") not in context:
        return [f"{source_md.name} の本文全体が payload に含まれない"]
    return []


def delivery_note_payload_text() -> str:
    """delivery-note.md 本文からヘッダコメントを除いた配送ペイロード部分を返す。

    inject-always.sh の NOTE 変数 (`$(cat delivery-note.md)`) がそのまま
    additionalContext に埋め込まれるため、ヘッダコメント除去後のペイロード
    文字列が context に残存していれば、この要素が縮退で落とされていないと
    確認できる。
    """
    text = read(DELIVERY_NOTE_MD)
    header = leading_html_comment(text)
    payload = text[len(header):] if header else text
    return payload.strip()


def pre_degradation_missing_elements(context: str, note_payload: str) -> list[str]:
    """inject-always.sh の段階的縮退のうち、配送メモ (delivery-note.md) 本文の
    残存 (無条件必須) を context から確認する。

    inject-always.sh は 8K 超過時にまず (参照パス) 行のみを落とす第一段縮退を
    明示的に許可しているため、この行単独の欠落は fail 条件にしない (深い
    checkout パス等での正当な縮退を誤検知しないため)。一方、配送メモ本文は
    段階的縮退ガードを持つ経路 (delivery_fable / delivery_sonnet_part1_self_gate)
    のサイズ測定が「縮退後 payload がたまたま予算内に収まっただけ」の場合にも
    green になりうる盲点を塞ぐ、本文喪失・第二段以降の縮退に対する唯一の
    防衛線であるため、無条件必須とする。note_payload は呼び出し側が
    delivery_note_payload_text() 等で用意した配送メモ本文の期待値を渡す
    (この関数自体はファイル I/O を行わない)。
    """
    missing = []
    if not note_payload:
        # note_payload が空 (ヘッダのみ・空白のみのメモから抽出された場合等)
        # だと比較対象が空文字列になり `in` 判定が常に真になって検査が
        # skip されてしまう (副作用として常に成功扱いになる)。空・空白のみの
        # note_payload はそれ自体を「メモ本文の欠落」として fail-closed に扱う。
        missing.append("delivery-note.md から配送メモ本文を抽出できない (空・ヘッダのみの疑い)")
    elif note_payload not in context:
        missing.append("delivery-note.md の配送メモ本文が無い (縮退の疑い)")
    return missing


# 段階的縮退ガードを持つ 2 経路 (inject-always.sh 系のみ。inject-rules-part.sh /
# inject-subagent-rules.sh / resolve-model-on-prompt.sh は縮退ガードを持たない)。
PRE_DEGRADATION_DELIVERY_BUILDERS = {
    "fable 向け always 配送 (inject-always.sh, model=fable)": delivery_fable,
    "sonnet part 1 配送 (inject-always.sh, self-gate)": delivery_sonnet_part1_self_gate,
}


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

    文単位・見出し・構造ラベルの個別検査 (test_canonical_sentences_present_in_
    rule_block 等) は診断メッセージの分かりやすさのために残す (冗長化は許容
    する) が、これらは canonical 文・見出し・ラベルさえ保持されていれば新しい
    文や段落の追記を見逃す「追加型矛盾」に弱い。ブロック全体一致検査
    (test_rule_block_matches_confirmed_body_exactly) がこの盲点を塞ぐ最終的な
    契約であり、CONFIRMED_BLOCK_TEXT が配送する確定本文の正本となる。
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

    def test_rule_block_matches_confirmed_body_exactly(self) -> None:
        """各面のルールブロック全体が、正規化 (段落構造を保持したまま両辺
        対称に空白を除去) した後 CONFIRMED_BLOCK_TEXT と完全一致すること。

        文単位・見出し・構造ラベルを全部保持したまま新しい文や段落を追記
        しても (例: 末尾に「ただし、過去の変更履歴を書いてよい。」を追記)
        個別検査は green のままになりうる。本検査は段落の個数・順序まで含む
        ブロック全体を確定本文と突き合わせることでこれを塞ぐ。
        """
        violations = []
        for face in FACES:
            _path, block, _header, reason = resolve_face(face)
            if reason is not None:
                violations.append(f"{face} ({reason})")
                continue
            actual = normalize_block_for_exact_match(block)
            expected = normalize_block_for_exact_match(CONFIRMED_BLOCK_TEXT[face])
            if actual != expected:
                violations.append(face)
        self.assertEqual(
            [], violations, f"ルールブロック全体が確定本文と完全一致しない面: {violations}"
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
    を使い、6 経路すべて無条件に実行する (locale 検出の成否によらず skip しない)。

    段階的縮退ガードを持つ 2 経路 (PRE_DEGRADATION_DELIVERY_BUILDERS の
    fable / sonnet part 1) はさらに、配送メモ (delivery-note.md) 本文が
    payload に残存していることをサイズ予算検査・payload 内容検査の両方の
    合否判定に無条件で組み込む (pre_degradation_missing_elements 参照)。
    inject-always.sh が明示的に許可する第一段縮退 ((参照パス) 行のみを落とす)
    は許容し、この行単独の欠落は fail 条件にしない。UTF8_LOCALE (detect_utf8_
    locale が best-effort に検出する UTF-8 系 locale) は run_hook が hook
    subprocess の env を設定する補助にのみ使い、いずれの検査も gate しない。
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

    # DELIVERY_BUILDERS と同一のラベルキーで、各経路が実際に配送する md
    # ファイルを対応付ける (payload の内容検査用)。
    DELIVERY_PATH_SOURCE_FILES = {
        "fable 向け always 配送 (inject-always.sh, model=fable)": FABLE_MD,
        "sonnet part 1 配送 (inject-always.sh, self-gate)": SONNET_MD["always-sonnet-1.md"],
        "sonnet part 2 配送 (inject-rules-part.sh 2, self-gate)": SONNET_MD[
            "always-sonnet-2.md"
        ],
        "sonnet part 3 配送 (inject-rules-part.sh 3, self-gate)": SONNET_MD[
            "always-sonnet-3.md"
        ],
        "subagent-rules 配送 (inject-subagent-rules.sh)": SUBAGENT_MD,
        "fable one-shot 補正配送 (resolve-model-on-prompt.sh)": FABLE_MD,
    }

    def test_all_delivery_paths_within_budget(self) -> None:
        """各配送経路の additionalContext が配送予算 (SIZE_BUDGET_UNITS) 以下に
        収まること。段階的縮退ガード付き 2 経路は配送メモ本文の残存も合否
        判定に組み込む (クラス docstring 参照)。6 経路すべて無条件に実行する。
        """
        note_payload = delivery_note_payload_text()
        violations = []
        for label, builder in self.DELIVERY_BUILDERS.items():
            with tempfile.TemporaryDirectory() as tmp_dir:
                context = builder(tmp_dir)
            length = utf16_length(context)
            if not within_size_budget(length):
                violations.append(f"{label}: 予算超過 ({length} units)")
            if label in PRE_DEGRADATION_DELIVERY_BUILDERS:
                missing = pre_degradation_missing_elements(context, note_payload)
                if missing:
                    violations.append(f"{label}: 配送メモ本文の証拠が無い {missing}")
        self.assertEqual(
            [],
            violations,
            f"配送予算 ({SIZE_BUDGET_UNITS} UTF-16 units 以下) を超過、または"
            f"配送メモ本文の証拠を確認できない経路: {violations}",
        )

    def test_all_delivery_paths_include_source_file_content(self) -> None:
        """各配送経路の additionalContext に、その経路が配送する md ファイル
        (DELIVERY_PATH_SOURCE_FILES) の本文全体が実際に含まれること
        (payload_content_missing 参照)。段階的縮退ガード付き 2 経路は配送メモ
        本文の残存も合否判定に組み込む (クラス docstring 参照)。6 経路すべて
        無条件に実行する。

        サイズ検査 (test_all_delivery_paths_within_budget) はサイズのみを
        見るため、hook が本文を欠落させた (それでいて長さだけは偶然予算内に
        収まる) 出力でも green になる盲点がある。見出し 1 行のみの照合でも、
        見出しだけ含んで本文を欠落させた payload や別要素が同じ見出しを
        引用しているだけの payload を見逃す。判定は動的に行う (対象 md
        ファイルを都度読んで全文照合する) ため、確定本文の逐語に依存せず、
        現状ファイルでも今後の本文更新後でも自然に成立する。
        """
        note_payload = delivery_note_payload_text()
        violations = []
        for label, builder in self.DELIVERY_BUILDERS.items():
            with tempfile.TemporaryDirectory() as tmp_dir:
                context = builder(tmp_dir)
            source_md = self.DELIVERY_PATH_SOURCE_FILES[label]
            missing = payload_content_missing(context, source_md)
            if missing:
                violations.append(f"{label}: {missing}")
            if label in PRE_DEGRADATION_DELIVERY_BUILDERS:
                missing_evidence = pre_degradation_missing_elements(context, note_payload)
                if missing_evidence:
                    violations.append(f"{label}: 配送メモ本文の証拠が無い {missing_evidence}")
        self.assertEqual(
            [],
            violations,
            f"配送経路の payload に本文内容が含まれていない、または配送メモ"
            f"本文の証拠を確認できない経路: {violations}",
        )

    def test_pre_degradation_check_runs_unconditionally_without_utf8_locale(
        self,
    ) -> None:
        """UTF8_LOCALE が None (locale probe 失敗) をシミュレートしても、fable
        配送経路の hook 出力が実検出済み locale で pin した場合と同一であり、
        予算・配送メモ本文の各検査が skip されず実行されること。

        run_hook の locale 引数は既定で (関数定義時点で束縛されるため、この後
        グローバル UTF8_LOCALE を書き換えても) 検出済みの値を使い続ける。この
        独立性により、hook subprocess の起動 env はこのシミュレーション中も
        実検出済み locale で決定的に pin されたままになり、検査結果が test
        実行環境の ambient locale (既定 locale) に左右されない。
        """
        with tempfile.TemporaryDirectory() as tmp_dir_pinned:
            pinned_context = delivery_fable(tmp_dir_pinned)

        global UTF8_LOCALE
        original = UTF8_LOCALE
        UTF8_LOCALE = None
        try:
            with tempfile.TemporaryDirectory() as tmp_dir_simulated:
                simulated_context = delivery_fable(tmp_dir_simulated)
        finally:
            UTF8_LOCALE = original

        self.assertEqual(
            pinned_context,
            simulated_context,
            "UTF8_LOCALE=None のシミュレーション下で hook の出力が変化した"
            " (env pin がグローバルの書き換えに追従してしまっている疑い)",
        )
        length = utf16_length(simulated_context)
        self.assertTrue(within_size_budget(length), f"予算超過 ({length} units)")
        missing = pre_degradation_missing_elements(
            simulated_context, delivery_note_payload_text()
        )
        self.assertEqual([], missing, f"配送メモ本文の証拠が無い: {missing}")


class RunnerLocaleDefaultBindingTests(unittest.TestCase):
    """run_hook の locale 既定値が import 時の probe 結果へ定義時束縛されて
    いることの独立契約検査。

    配送出力の比較では束縛方式を検証しない — UTF-8 系 ambient のホストでは
    束縛方式に依らず出力が一致しうるため、比較は判別力を持たない。既定値
    そのものを、import 時に保存した不変 snapshot (UTF8_LOCALE_AT_IMPORT) と
    突き合わせる。probe の再実行もしない (再実行は一時的な失敗という別の
    不確実性を期待値に持ち込むため)。
    """

    def test_run_hook_locale_default_is_bound_at_definition_time(self) -> None:
        runner_default = inspect.signature(run_hook).parameters["locale"].default
        self.assertEqual(
            UTF8_LOCALE_AT_IMPORT,
            runner_default,
            "run_hook の locale 既定値が import 時の検出値と一致しない"
            " (定義時束縛の後退の疑い)",
        )


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class LocaleFallbackDeliveryTests(unittest.TestCase):
    """UTF-8 locale が env に固定されない hook 起動経路 (locale fallback) の実走検証。

    既存の SizeBudgetTests は hook subprocess の LC_ALL/LANG を検出済み UTF-8
    locale に固定して起動するため、「非 UTF-8 locale では inject-always.sh の
    `wc -m` がバイト計上になり縮退が発動する」経路はどのケースでも実走しない。
    本クラスはその経路を、ambient locale に依存せず決定的に実走させる:

    - 制御用 locale: detect_non_utf8_locale が `locale -a` から選ぶ単一バイト
      locale (C / POSIX) を、テストが os.environ の LC_ALL/LANG へ一時設定し
      (元々キーが無かった状態も含めて try/finally で完全復元)、builder には
      locale=None を渡す。run_hook 自身は locale を上書きせず、テストが制御
      した ambient を hook subprocess が env の copy 経由で継承する (= locale
      未指定 fallback 分岐の実走)。ambient は区間内でテストが明示制御する
      ため、テスト実行環境の既定 locale には依存しない
    - 対象経路: 段階的縮退ガードを持つ 2 経路 (inject-always.sh の fable 配送 /
      sonnet part 1 self-gate 配送) のみ。他 4 経路はサイズ計測を持たず locale
      で挙動が変わらないため対象外
    - 期待挙動: 非 UTF-8 locale では `wc -m` が日本語 payload をバイト数
      (UTF-8 で 1 文字 3 バイト前後) で計上するため、現行 payload は必ず
      8,000 を超え、二段縮退 ((参照パス) 行の除去 → 配送メモ全体の除去) が
      発動して ESSENTIAL (自己修復指示 + ルール md 全文) だけが配送される。
      検査は文言の逐語固定ではなく縮退機構の意味的検証とする:
      (a) hook が正常終了し additionalContext を持つ有効な JSON を返す
      (b) 配送メモ (delivery-note.md) 本文が payload に存在しない
      (c) (参照パス) 行が payload に存在しない
      (d) その経路のルール md 全文が payload に残存する (ESSENTIAL 不落)
      (e) payload が自己修復指示 (「(自己修復)」) で始まる
    - 前提の明示: 本検査は「バイト計上では現行 payload が必ず予算を超える」
      という現行サイズを前提とする。payload がバイト計上でも 8,000 以下まで
      縮小した場合は縮退が発動せず (b)(c) が red になるため、その時点で本
      契約を見直す
    - skip 境界: 制御用の単一バイト locale (C / POSIX) がホストで確認できない
      場合 (detect_non_utf8_locale が None) のみ、理由付きの明示 skip とする。
      locale 由来の skip 条件はこれ以外に設けない (クラスに付く jq 可用性の
      skip guard は hook 統合テスト共通の環境前提であり、本契約の対象外として
      維持する)
    - 既存検査への不干渉: UTF-8 固定で実行される既存の予算・内容・縮退証拠
      検査の合否と設計 (定義時束縛の locale 固定・無条件実行) は変更しない
    """

    def _deliver_under_non_utf8_locale(self, builder) -> str:
        """制御用単一バイト locale を ambient に一時設定し、locale=None で
        builder を実行して additionalContext を返す。

        run_hook 自身は locale を上書きせず、テストが制御した ambient
        LC_ALL/LANG を hook subprocess が継承する (locale 未指定 fallback
        分岐の実走)。detect_non_utf8_locale が None の場合は理由付きで
        skipTest する。locale の検出と skip 判定は環境変更より前に行い、
        環境変更は builder 1 回の実行区間に限定し、元々キーが無かった状態も
        含めて完全復元する。
        """
        locale = detect_non_utf8_locale()
        if locale is None:
            self.skipTest(
                "制御用の単一バイト locale (C / POSIX) がホストで確認できないため skip"
            )
        keys = ("LC_ALL", "LANG")
        saved = {key: os.environ[key] for key in keys if key in os.environ}
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                os.environ["LC_ALL"] = locale
                os.environ["LANG"] = locale
                return builder(tmp_dir, None)
            finally:
                for key in keys:
                    if key in saved:
                        os.environ[key] = saved[key]
                    else:
                        os.environ.pop(key, None)

    def test_fable_delivery_degrades_to_essential_under_non_utf8_locale(
        self,
    ) -> None:
        """fable 配送がバイト計上の縮退で ESSENTIAL のみになること (a)〜(e)。"""
        context = self._deliver_under_non_utf8_locale(delivery_fable)
        # (a) は run_hook 内の既存検証 (正常終了・additionalContext 存在) が担う。
        note_payload = delivery_note_payload_text()
        self.assertTrue(
            note_payload, "delivery-note.md から配送メモ本文を抽出できない (空・ヘッダのみの疑い)"
        )
        self.assertNotIn(
            note_payload,
            context,
            "配送メモ本文が縮退後も payload に残存している"
            " (バイト計上での二段縮退が発動していない疑い)",
        )
        self.assertNotIn(
            PATH_LINE_PREFIX,
            context,
            "(参照パス) 行が縮退後も payload に残存している (第一段縮退が発動していない疑い)",
        )
        self.assertEqual(
            [],
            payload_content_missing(context, FABLE_MD),
            "ルール md 全文が縮退で欠落している (ESSENTIAL が不落単位になっていない疑い)",
        )
        self.assertTrue(
            context.startswith("(自己修復)"),
            "payload が自己修復指示で始まっていない",
        )

    def test_sonnet_part1_delivery_degrades_to_essential_under_non_utf8_locale(
        self,
    ) -> None:
        """sonnet part 1 self-gate 配送がバイト計上の縮退で ESSENTIAL のみに
        なること (a)〜(e)。(d) のルール md は always-sonnet-1.md。
        """
        context = self._deliver_under_non_utf8_locale(delivery_sonnet_part1_self_gate)
        # (a) は run_hook 内の既存検証 (正常終了・additionalContext 存在) が担う。
        note_payload = delivery_note_payload_text()
        self.assertTrue(
            note_payload, "delivery-note.md から配送メモ本文を抽出できない (空・ヘッダのみの疑い)"
        )
        self.assertNotIn(
            note_payload,
            context,
            "配送メモ本文が縮退後も payload に残存している"
            " (バイト計上での二段縮退が発動していない疑い)",
        )
        self.assertNotIn(
            PATH_LINE_PREFIX,
            context,
            "(参照パス) 行が縮退後も payload に残存している (第一段縮退が発動していない疑い)",
        )
        self.assertEqual(
            [],
            payload_content_missing(context, SONNET_MD["always-sonnet-1.md"]),
            "ルール md 全文が縮退で欠落している (ESSENTIAL が不落単位になっていない疑い)",
        )
        self.assertTrue(
            context.startswith("(自己修復)"),
            "payload が自己修復指示で始まっていない",
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


class AgentDisciplinePluginVersionFloorTests(unittest.TestCase):
    """agent-discipline plugin の version が rule:comment-currency 追加分の
    bump を満たすこと。

    既存の version policy 検査 (scripts/check_plugin_versions.py) は変更
    plugin の bump 発生の有無のみを見るため、patch bump のみ (例: 0.24.0 →
    0.24.1) に留めても green になる。本テストはこの機能追加が要求する具体的
    な version floor (0.25.0 以上) を独立に固定する。
    """

    def test_plugin_json_version_is_at_least_the_floor(self) -> None:
        data = json.loads(read(AGENT_DISCIPLINE_PLUGIN_JSON))
        version_str = data["version"]
        version = parse_semver_triple(version_str)
        floor_str = ".".join(str(part) for part in MINIMUM_AGENT_DISCIPLINE_VERSION)
        self.assertGreaterEqual(
            version,
            MINIMUM_AGENT_DISCIPLINE_VERSION,
            f"plugin.json の version {version_str} が {floor_str} 未満",
        )


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

    def test_structural_label_check_requires_line_start_anchor(self) -> None:
        """構造要素ラベルが行頭ではなく prose 文中にのみ偶発的に出現する
        block では、そのラベルが「無い」ものとして検出されること (部分文字列
        包含への回帰を防ぐ false-green ガード)。
        """
        block_with_prose_only_mention = (
            "## 10. synthetic heading\n\n"
            "This paragraph casually mentions **なぜ**: as an example phrase, "
            "not as an actual why-paragraph heading。\n\n"
            "**指示**: do the thing。\n\n"
            "**境界**: except when not。\n"
        )
        missing = missing_structural_labels(
            block_with_prose_only_mention, STRUCTURAL_ELEMENT_LABELS["always-fable.md"]
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

    def test_fullwidth_space_only_line_is_not_a_paragraph_boundary(self) -> None:
        """全角空白 (U+3000) のみの行は ASCII 限定の空行判定では空行とみなされず、
        段落区切りにならないこと (soft-wrap の継続行として 1 段落に連結される)。
        連結後は文正規化 (strip_whitespace) が全角空白も除去するため、
        canonical 文と一致すること。対比として、ASCII 空白のみの行は
        引き続き段落区切りになる (soft-wrap されない) ことも確認する。
        """
        sentence = "AはBでありCである。"
        text_with_fullwidth_space_line = "AはB\n　\nでありCである。"
        paragraphs = split_into_paragraphs(text_with_fullwidth_space_line)
        self.assertEqual([text_with_fullwidth_space_line], paragraphs)
        self.assertTrue(
            block_matches_canonical_sentence(text_with_fullwidth_space_line, sentence)
        )

        text_with_ascii_space_line = "AはB\n \nでありCである。"
        self.assertEqual(2, len(split_into_paragraphs(text_with_ascii_space_line)))

    def test_block_exact_match_detects_inline_whitespace_removal(self) -> None:
        """ブロック全体一致の正規化 (normalize_block_for_exact_match) は行内の
        空白 (単語間スペース) を保持するため、「commit message」→
        「commitmessage」のような行内空白の除去変質をブロック不一致として
        検出すること (文レベルの soft-wrap 耐性正規化とは異なる、より厳密な層)。
        """
        reference = "## heading\n\ncommit message body。\n"
        mutated = "## heading\n\ncommitmessage body。\n"
        self.assertNotEqual(
            normalize_block_for_exact_match(reference),
            normalize_block_for_exact_match(mutated),
        )

    def test_block_exact_match_tolerates_trailing_whitespace_and_blank_line_runs(
        self,
    ) -> None:
        """ブロック全体一致の正規化は、行末の空白の有無・連続空行の本数の違い
        (先頭・末尾の空行を含む) を無視して一致とみなすこと。行内の空白・
        改行位置そのものは保持されたまま比較される。
        """
        reference = "## heading\n\ncommit message body。\n"
        trailing_whitespace_variant = "## heading   \n\ncommit message body。   \n"
        extra_blank_lines_variant = "\n\n## heading\n\n\n\ncommit message body。\n\n\n"
        self.assertEqual(
            normalize_block_for_exact_match(reference),
            normalize_block_for_exact_match(trailing_whitespace_variant),
        )
        self.assertEqual(
            normalize_block_for_exact_match(reference),
            normalize_block_for_exact_match(extra_blank_lines_variant),
        )

    def test_block_exact_match_detects_blank_line_replaced_by_fullwidth_space_line(
        self,
    ) -> None:
        """段落を区切る空行を、全角空白 (U+3000) のみの行へ置換した変質は
        ブロック不一致として検出すること。全角空白のみの行は Markdown 上は
        空行にならず soft-wrap の継続行として段落を連結してしまうため、この
        置換は「段落が 1 つに統合される」実質的な内容変更であり、既定の
        `str.rstrip()` (Unicode 空白まで除去する) を使った空行判定ではこの
        行が空文字列に潰れて見分けが付かなくなる (見逃す) ことの回帰ガード。
        """
        reference = "## heading\n\ncommit message body。\n"
        blank_line_replaced_with_fullwidth_space = (
            "## heading\n　\ncommit message body。\n"
        )
        self.assertNotEqual(
            normalize_block_for_exact_match(reference),
            normalize_block_for_exact_match(blank_line_replaced_with_fullwidth_space),
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

    def test_pre_degradation_check_tolerates_path_line_removal(self) -> None:
        """(参照パス) 行が欠落した payload であっても、配送メモ本文さえ残って
        いれば pre_degradation_missing_elements は fail させないこと
        (inject-always.sh が明示的に許可する第一段縮退への追従)。
        """
        note_payload = "synthetic delivery note body。"
        context_without_path_line = f"body text without a path line\n\n{note_payload}\n"
        self.assertNotIn(PATH_LINE_PREFIX, context_without_path_line)
        self.assertEqual(
            [],
            pre_degradation_missing_elements(context_without_path_line, note_payload),
        )

    def test_pre_degradation_check_fails_when_note_body_missing(self) -> None:
        """配送メモ本文が欠落した payload は、(参照パス) 行の有無に関わらず
        pre_degradation_missing_elements を fail させること (本文喪失・
        第二段以降の縮退を検出する唯一の防衛線)。
        """
        note_payload = "synthetic delivery note body。"
        context_without_note = f"{PATH_LINE_PREFIX}/some/path\n\nunrelated body text\n"
        self.assertNotEqual(
            [], pre_degradation_missing_elements(context_without_note, note_payload)
        )

    def test_pre_degradation_check_fails_closed_on_empty_note_payload(self) -> None:
        """note_payload 自体が空文字列 (delivery-note.md がヘッダのみ等で本文
        を抽出できない場合) は、`in` 判定が常に真になり検査が事実上無効化
        される事故を避けるため、それ自体を本文欠落として fail-closed に
        扱うこと。
        """
        context = f"{PATH_LINE_PREFIX}/some/path\n\nany body text\n"
        self.assertNotEqual([], pre_degradation_missing_elements(context, ""))

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
