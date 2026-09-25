"""agent-discipline 分業規律の委任対象 (スケルトン一括作成・指摘修正の一括反映) の契約テスト。

分業規律 (discipline.md) の「サブエージェントに委任する作業」
リストが、初期スケルトンの一括作成とレビュー・受入検証の指摘修正の一括反映を委任対象として
明示し、その規模境界を role-split 節内に持つことを固定する。既存の委任項目・直接作業の例外
文言が保全されていることも検査する。

検査方式:
- 検査範囲は「委任リスト見出し直下の bullet 行の並び」「その直後の空行区切り段落」
  「例外見出しと同一段落」に限定し、節全体の部分文字列検索による過検出・過小検出
  (無関係な箇所での偶然一致・節境界をまたいだ誤検出) を防ぐ。
- 規模境界・小規模修正の判定文字列は句点込みの全文で固定し、部分文字列一致による意味反転
  (否定を含む書換えなど) の見逃しを防ぐ。境界段落は bullet ブロック直後の段落を行末 rstrip
  連結で正規化したうえで ``SCALE_SENTENCE + SMALL_FIX_SENTENCE`` と完全一致させる。
  bullet ブロックと境界段落の間には空行を必須とする (空行が無いと markdown の lazy
  continuation により最後の bullet の継続行として render され、規模境界がリスト全体に
  掛からないため)。
- delegation_bullet_block の bullet 収集は行頭 (インデント無し) の ``- `` に限定し、ネスト
  した子 bullet を top-level 項目と誤認しない (この契約における「top-level」は Markdown
  一般の定義ではなく、この判定基準を指す)。
- 例外段落は太字 + コロンの段落先頭形 (DIRECT_EXCEPTION_PARAGRAPH_PREFIX) の先頭一致で
  特定し、段落を正規化して「。」区切りで分割した文リストに canonical 肯定文
  (DIRECT_EXCEPTION_CANONICAL_SENTENCE) が完全一致で存在することを検査する (substring
  存在検査では文中の否定化・改変を検出できないため)。例外段落に後置の矛盾文を追加する改変は
  検出対象外であり、「先頭の肯定文が変質していないこと」に絞った smoke 契約とする。
- 正規化 helper ``normalize_soft_wrapped`` は規模境界の段落一致・例外段落の文抽出・節外漂流
  ガードの prefix/suffix 検索の 3 箇所で共有する。soft-wrap 正規化は行間に空白を挿入しない
  (対象は日本語の canonical 文であり、空白挿入は正当な折返しを false-red にするため)。
- canonical bullet の重複出現は検出しない (存在判定のみ)。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS = REPO_ROOT / "plugins" / "agent-discipline" / "hooks" / "prompts"

DISCIPLINE_VARIANTS = {
    "discipline.md": PROMPTS / "discipline.md",
}

# 委任 bullet (column-zero の raw 行と完全一致させる)。
ITEM_SKELETON = "- 実装初期のスケルトン / スタブ / 型骨格の一括作成 (設計契約をコメントとして埋め込む場合を含む)"
ITEM_REVIEW_FIX = "- レビュー・受入検証の指摘修正の一括反映"

# 規模境界 (完全文・句点込み。意味反転を排除するため主語 + 述部の全文で固定する)
SCALE_SENTENCE = "スケルトン一括作成・指摘修正の一括反映は複数ファイルまたは数十行以上の規模で適用する。"
SMALL_FIX_SENTENCE = "単一ファイル数行の指摘修正は後述の「委任しない作業」として直接行ってよい。"

# 「サブエージェントに委任する作業:」見出し (この見出し直下の bullet 行を検査範囲にする)。
DELEGATION_LIST_HEADING = "サブエージェントに委任する作業:"

# 節スコープ検査で切り出すセクション境界 (rule ID マーカー)。
ROLE_SPLIT_MARKER = "<!-- rule:role-split -->"
DELEGATION_RULES_MARKER = "<!-- rule:delegation-rules -->"

# 保全ガード対象の既存委任項目。
EXISTING_ITEM_IMPLEMENTATION = "- 明確化された仕様に基づく、相応の規模がある実装"
EXISTING_ITEM_INVESTIGATION = "- 方針・仕様の検討・決定のための具体的な調査"
EXISTING_ITEM_MECHANICAL = "- 機械的で並列化可能な作業 (一括修正、テスト実行と修正のループ等)"

# 直接作業してよいものの例外段落の先頭 (太字 + コロンまで)。段落先頭一致で使うため、
# 実ファイルの段落冒頭の整形 (`**...**:`) をそのまま含める。
DIRECT_EXCEPTION_PARAGRAPH_PREFIX = "**委任しない作業 (直接行ってよいもの)**:"
# 直接作業の例外段落 (上記 prefix で始まる段落) の第 1 文全文
# (太字 prefix + 主語 + 述部 + 句点)。段落を正規化し「。」区切りで文に分割した
# うえで、この文が文リストに完全一致で存在することを検査する。
DIRECT_EXCEPTION_CANONICAL_SENTENCE = (
    "**委任しない作業 (直接行ってよいもの)**: 数回の tool call で完結する作業"
    " (自明な修正、単発の確認、1 ファイルの小さな編集) は、委任オーバーヘッド"
    " (サブエージェント起動 + コンテキスト再構築) の方が高くつくため直接行う。"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def role_split_bounds(text: str) -> tuple[int, int] | None:
    """role-split 節 (rule:role-split 〜 rule:delegation-rules) の開始・終了位置。

    どちらかのマーカーが見つからない場合は None を返し、呼び出し側で
    violation として報告する。
    """
    start = text.find(ROLE_SPLIT_MARKER)
    if start < 0:
        return None
    end = text.find(DELEGATION_RULES_MARKER, start)
    if end < 0:
        return None
    return start, end


def role_split_section(text: str) -> str | None:
    bounds = role_split_bounds(text)
    if bounds is None:
        return None
    start, end = bounds
    return text[start:end]


def scale_boundary_phrases() -> tuple[str, str]:
    """規模境界の 2 文 (存在検査・節外漂流検査の両方で共有する単一ソース)。

    片方のテストだけを更新して他方が古い定数を参照し続ける (片側更新漏れ) を
    構造的に防ぐため、検査フレーズの取得口をこの関数に一本化する。
    """
    return SCALE_SENTENCE, SMALL_FIX_SENTENCE


def normalize_soft_wrapped(text: str) -> str:
    """行末の改行・空白のみを吸収して連結する (行頭インデントは保持する)。

    soft line-wrap (長い文が複数行に折り返されている) された文字列を、行を
    区切り文字なしで連結して 1 本の文字列に正規化する。規模境界の段落一致・
    例外段落の文抽出・節外漂流ガードの prefix/suffix 検索の 3 箇所で共有し、
    正規化ロジックの分岐 (片側だけ更新されて挙動がずれる) を防ぐ。
    """
    return "".join(line.rstrip() for line in text.splitlines())


def delegation_bullet_block(section: str) -> tuple[list[str], int] | None:
    """委任リスト見出し直下の column-zero bullet 行の並びと、ブロック終端の行 index を返す。

    (i) `line.rstrip() == DELEGATION_LIST_HEADING` (先頭インデント不許可) で
    見出し行を探す。見出し自体も column-zero であることを要求する。
    (ii) 見出し行の直後に空行があれば 1 行だけ許容してスキップする。
    (iii) それに続く行を走査し、次の 4 分類で扱う:
      - raw 行が `- ` で始まる (インデント無し) → column-zero の top-level
        bullet として `raw.rstrip()` を収集する
      - strip 後が空 → 空行として読み飛ばす (打ち切らない)
      - raw にインデントがあり strip 後が `- ` で始まる → 既存 top-level 項目
        にぶら下がる子 bullet とみなし、ブロック内ではあるが top-level には
        収集しない (読み飛ばして走査継続)
      - それ以外 (非空・非 bullet な地の文) → その行で打ち切る
    戻り値の終端 index は打ち切り行 (直後段落の先頭) を指す。

    注記: ここでの「top-level」は Markdown 一般の構文規則ではなく、この契約
    (bullet 行がインデント無しで書かれていること) 固有の判定基準である。

    見出しが節内に見つからない場合は None を返す。
    """
    lines = section.splitlines()
    heading_idx = None
    for i, line in enumerate(lines):
        if line.rstrip() == DELEGATION_LIST_HEADING:
            heading_idx = i
            break
    if heading_idx is None:
        return None

    idx = heading_idx + 1
    if idx < len(lines) and lines[idx].strip() == "":
        idx += 1

    bullets: list[str] = []
    while idx < len(lines):
        raw = lines[idx]
        if raw.startswith("- "):
            bullets.append(raw.rstrip())
            idx += 1
            continue
        stripped = raw.strip()
        if stripped == "":
            idx += 1
            continue
        if stripped.startswith("- "):
            # インデント付きの子 bullet: ブロック内だが top-level ではないため収集しない
            idx += 1
            continue
        break
    return bullets, idx


def paragraph_after(lines: list[str], start_idx: int) -> str | None:
    """指定行 index 以降で最初に現れる、空行区切りの段落を返す。

    先頭の空行は読み飛ばす。段落が存在しない (start_idx 以降が空行のみ、
    または範囲外) 場合は None を返す。
    """
    idx = start_idx
    while idx < len(lines) and lines[idx].strip() == "":
        idx += 1
    if idx >= len(lines):
        return None
    para_lines: list[str] = []
    while idx < len(lines) and lines[idx].strip() != "":
        para_lines.append(lines[idx])
        idx += 1
    return "\n".join(para_lines)


def find_paragraph_starting_with(section: str, prefix: str) -> str | None:
    """節内を空行区切りの段落に分割し、prefix で始まる最初の段落を返す。

    substring 包含では、境界段落の本文が偶然アンカー文字列を含むだけで誤って
    一致してしまう (段落衝突) ため、段落先頭一致に限定する。該当する段落が無ければ
    None。
    """
    for para in section.split("\n\n"):
        if para.startswith(prefix):
            return para
    return None


def line_exists_stripped(section: str, target: str) -> bool:
    """節内のいずれかの行が strip() 後に target と完全一致するか。"""
    return any(line.strip() == target for line in section.splitlines())


def missing_item_violation(
    section: str, bullets: list[str], item: str, name: str, description: str
) -> str | None:
    """item が bullets (top-level) に無い場合の violation メッセージを返す。

    bullets に無くても、節内に strip 一致で存在する場合は「top-level ではなく
    ネスト/ブロック外に存在」の診断に切り替え、単純な不在と区別する
    (診断の精度向上。原因箇所の特定を早める)。item が bullets にあれば None。
    """
    if item in bullets:
        return None
    if line_exists_stripped(section, item):
        return f"{name} ({description}: top-level ではなくネスト/ブロック外に存在)"
    return f"{name} ({description}が無い)"


class DelegationItemsAdditionTests(unittest.TestCase):
    """委任対象の 2 項目 (スケルトン一括作成・指摘修正の一括反映) と規模境界の契約。

    subTest は使わない: pytest (subtest 対応版) では個々の subTest 失敗が
    SUBFAILED として分離報告される一方、親テストノード自体は PASSED と表示され
    green/red の判定が曖昧になるため、リスト集約 + 1 テスト = 1 判定に保つ
    (tests/test_opus5_effort_unpin.py と同じ慣行)。
    """

    def test_delegation_items_present_as_bullets(self) -> None:
        violations = []
        for name, path in DISCIPLINE_VARIANTS.items():
            section = role_split_section(read(path))
            if section is None:
                violations.append(f"{name} (role-split 節が見つからない)")
                continue
            block = delegation_bullet_block(section)
            if block is None:
                violations.append(f"{name} (委任リスト見出しが節内に無い)")
                continue
            bullets, _end_idx = block
            v = missing_item_violation(
                section, bullets, ITEM_SKELETON, name, "スケルトン一括作成の項目"
            )
            if v is not None:
                violations.append(v)
            v = missing_item_violation(
                section, bullets, ITEM_REVIEW_FIX, name, "レビュー指摘修正一括反映の項目"
            )
            if v is not None:
                violations.append(v)
        self.assertEqual([], violations, f"委任項目の追加が未反映: {violations}")

    def test_scale_boundary_present(self) -> None:
        """bullet ブロック直後の段落が規模境界の canonical 2 文と完全一致すること。

        段落抽出の前提として、bullet ブロックと直後段落の間に空行が必須である
        ことをまず検査する。空行なしで境界文を置くと markdown の lazy
        continuation により最後の bullet の継続行として render され、規模境界
        がリスト全体ではなく最後の項目のみに掛かって見える。空行が確認できた
        場合のみ、段落を normalize_soft_wrapped (行末 rstrip 連結。行頭
        インデントは保持し、行間に区切り文字を挟まない) で正規化したうえで
        `SCALE_SENTENCE + SMALL_FIX_SENTENCE` と完全一致するかを検査する
        (部分文字列の包含では、無関係な文脈に同じ部分文字列が現れる場合を
        区別できないため)。
        """
        violations = []
        for name, path in DISCIPLINE_VARIANTS.items():
            section = role_split_section(read(path))
            if section is None:
                violations.append(f"{name} (role-split 節が見つからない)")
                continue
            block = delegation_bullet_block(section)
            if block is None:
                violations.append(f"{name} (委任リスト見出しが節内に無い)")
                continue
            _bullets, end_idx = block
            lines = section.splitlines()
            if end_idx == 0 or lines[end_idx - 1].strip() != "":
                violations.append(
                    f"{name} (bullet ブロックと直後段落の間に空行が無い"
                    " — markdown lazy continuation)"
                )
                continue
            para = paragraph_after(lines, end_idx)
            if para is None:
                violations.append(f"{name} (bullet ブロック直後に段落が無い)")
                continue
            normalized = normalize_soft_wrapped(para)
            scale_sentence, small_fix_sentence = scale_boundary_phrases()
            expected = scale_sentence + small_fix_sentence
            if normalized != expected:
                violations.append(f"{name} (直後の段落が canonical 2 文と完全一致しない)")
        self.assertEqual([], violations, f"規模境界の記述が未反映: {violations}")


class ExistingDelegationRulesPreservedTests(unittest.TestCase):
    """既存の委任規範の保全ガード。

    既存の委任項目・例外文言が保たれ、規模境界の記述が role-split 節の外へ漂流して
    いないことを固定する。
    """

    def test_existing_delegation_items_preserved(self) -> None:
        violations = []
        for name, path in DISCIPLINE_VARIANTS.items():
            section = role_split_section(read(path))
            if section is None:
                violations.append(f"{name} (role-split 節が見つからない)")
                continue
            block = delegation_bullet_block(section)
            if block is None:
                violations.append(f"{name} (委任リスト見出しが節内に無い)")
                continue
            bullets, _end_idx = block
            v = missing_item_violation(
                section,
                bullets,
                EXISTING_ITEM_IMPLEMENTATION,
                name,
                "既存の実装委任項目",
            )
            if v is not None:
                violations.append(v)
            v = missing_item_violation(
                section, bullets, EXISTING_ITEM_INVESTIGATION, name, "既存の調査委任項目"
            )
            if v is not None:
                violations.append(v)
            v = missing_item_violation(
                section, bullets, EXISTING_ITEM_MECHANICAL, name, "既存の機械的作業委任項目"
            )
            if v is not None:
                violations.append(v)
        self.assertEqual([], violations, f"既存の委任項目が失われている: {violations}")

    def test_direct_edit_exception_preserved(self) -> None:
        """例外段落の第 1 文が canonical 肯定文と文単位で完全一致すること。

        段落を find_paragraph_starting_with (段落先頭一致) で特定したうえで
        normalize_soft_wrapped で正規化し、「。」区切りで文に分割する
        (句点は各文の末尾に保持する)。canonical 文がその文リストに完全一致で
        存在するかを検査する。部分文字列の存在検査では文中の否定化・改変
        (「〜直接行う。」→「〜直接行わない。」等) を検出できないため、文境界の
        完全一致で照合する (段落全体の意味保証は対象外。モジュール docstring の
        検査方式を参照)。
        """
        violations = []
        for name, path in DISCIPLINE_VARIANTS.items():
            section = role_split_section(read(path))
            if section is None:
                violations.append(f"{name} (role-split 節が見つからない)")
                continue
            prefix = DIRECT_EXCEPTION_PARAGRAPH_PREFIX
            para = find_paragraph_starting_with(section, prefix)
            if para is None:
                violations.append(f"{name} (直接作業の例外段落 (prefix 先頭一致) が節内に無い)")
                continue
            normalized = normalize_soft_wrapped(para)
            sentences = re.findall(r"[^。]*。", normalized)
            canonical_sentence = DIRECT_EXCEPTION_CANONICAL_SENTENCE
            if canonical_sentence not in sentences:
                violations.append(f"{name} (canonical 肯定文が文単位で一致しない)")
        self.assertEqual(
            [], violations, f"直接作業の例外文言が失われている: {violations}"
        )

    def test_scale_boundary_absent_outside_role_split(self) -> None:
        """規模境界の記述が role-split 節の外へ漂流していないことの固定。

        prefix / suffix を連結せず個別に normalize_soft_wrapped で正規化して
        から substring 検索する (連結境界での偶然一致を排除しつつ、soft
        line-wrap された節外複製も検出できるようにする)。存在検査
        (test_scale_boundary_present) と同じ scale_boundary_phrases から
        検査フレーズを取得し、片側更新漏れを防ぐ。不在検査のため正規化後の
        substring 検索のまま維持する (完全一致は「無いこと」の検査には不適)。
        """
        violations = []
        for name, path in DISCIPLINE_VARIANTS.items():
            text = read(path)
            bounds = role_split_bounds(text)
            if bounds is None:
                violations.append(f"{name} (role-split 節が見つからない)")
                continue
            start, end = bounds
            prefix = normalize_soft_wrapped(text[:start])
            suffix = normalize_soft_wrapped(text[end:])
            for phrase in scale_boundary_phrases():
                if phrase in prefix or phrase in suffix:
                    violations.append(f"{name}: {phrase!r}")
        self.assertEqual([], violations, f"規模境界の記述が節外に漂流: {violations}")


if __name__ == "__main__":
    unittest.main()
