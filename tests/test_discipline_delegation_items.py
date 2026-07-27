"""agent-discipline 分業規律 3 ファイルへの委任対象追加 (issue #238) の契約テスト。

背景 (spec-first Phase A):
- 週次の自己観測調査で、Fable 本体による「初期スケルトンの一括 Write」「レビュー
  指摘後の直接パッチ」への逸脱が集中して発生していることが判明した (issue #238)。
  分業規律の「サブエージェントに委任する作業」リストにこの 2 種類の作業を明示
  追加し、委任対象であることをメインセッション自身が読み取れるようにする。
- 対象は discipline-fable.md / discipline-sonnet.md に加え、issue #238 起票後に
  ユーザ decision により新設された discipline-opus.md も含む 3 ファイル
  (起票時点では opus 版は存在しなかったが、分業規律の 3-way 構成に合わせて
  反映対象へ含める)。
- 変更要求の契約 (委任項目 2 件の追加・規模境界の明記) は Phase A で red、
  Phase B のプロンプト本文修正で green になる
  (test_delegation_items_present_as_bullets / test_scale_boundary_present)。
  既存の委任項目・直接作業の例外文言の保全ガードと、規模境界の記述が
  role-split 節の外へ漂流しないことの固定は Phase A から green である
  (test_existing_delegation_items_preserved / test_direct_edit_exception_preserved /
  test_scale_boundary_absent_outside_role_split。Phase B の書換えが既存規範を
  破壊しないことを保証する)。

改訂 1 回目 (pre-push review (codex / correctness) の指摘と Codex rescue 壁打ちで確定):
検査範囲を「委任リスト見出し直下の bullet 行の並び」「その直後の空行区切り
段落」「例外見出しと同一段落」まで精密化し、節全体の部分文字列検索による
過検出・過小検出 (無関係な箇所での偶然一致・節境界をまたいだ誤検出) を防ぐ。

改訂 2 回目 (再レビュー (codex P2 x2 / correctness P2 x1) の指摘、Codex rescue の
approve と親セッションの決定で確定):
(1) 完全文 anchoring — 規模境界・小規模修正の判定文字列を句点込みの全文に
することで、部分文字列一致による意味反転 (否定を含む書換えなどの誤検出漏れ)
を排除する。(2) column-zero bullet 判定 — delegation_bullet_block の bullet
収集を行頭 (インデント無し) の `- ` に限定し、ネストした子 bullet を top-level
項目と誤認しないようにする (この契約における「top-level」は Markdown 一般の
定義ではなく、この判定基準を指す)。(3) sonnet 版 discipline-sonnet.md は
配送予算 (self-gate 前置き + 本体の合算が 8,000 UTF-16 units、残余 ~156 units)
の制約があるため、スケルトン一括作成の bullet 文言のみ圧縮した canonical 文言
を契約とする (ITEM_SKELETON をファイル別の辞書にする)。

改訂 3 回目 (3 巡目レビュー (codex P1 x2 must-fix / correctness P2 x1 must-fix、
後者は codex Finding 1 と同一バグ) への対応、Codex rescue の条件付き approve で
確定):
(1) 段落衝突の解消 — fable の SMALL_FIX_SENTENCE が旧アンカー「例外 (直接編集
してよいもの)」を substring として含むため、Phase B で挿入される境界段落を
find_paragraph_with が誤って拾う (保全ガードの誤 red) 経路があった。アンカーを
太字 + コロンの段落先頭形 (DIRECT_EXCEPTION_PARAGRAPH_PREFIX) にし、
`para.startswith(prefix)` の先頭一致 (find_paragraph_starting_with) へ変更して
解消する。(2) 境界段落の判定を、bullet ブロック直後の段落を行末 rstrip 連結で
正規化したうえで `SCALE_SENTENCE + SMALL_FIX_SENTENCE[name]` との完全一致に
変更する (substring 包含 2 回では再文脈化 (別の文脈に同じ部分文字列が現れる)
によるすり抜けを防げないため)。節外漂流ガードは不在検査のため substring 検索
のまま維持する。
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS = REPO_ROOT / "plugins" / "agent-discipline" / "hooks" / "prompts"

THREE_WAY = {
    "discipline-fable.md": PROMPTS / "discipline-fable.md",
    "discipline-opus.md": PROMPTS / "discipline-opus.md",
    "discipline-sonnet.md": PROMPTS / "discipline-sonnet.md",
}

# 委任 bullet (column-zero の raw 行と完全一致させる)。sonnet は配送予算
# (self-gate + sonnet 合算 8,000 UTF-16 units、残余 ~156 units) に収める圧縮文言を契約とする。
ITEM_SKELETON = {
    "discipline-fable.md": "- 実装初期のスケルトン / スタブ / 型骨格の一括作成 (設計契約をコメントとして埋め込む場合を含む)",
    "discipline-opus.md": "- 実装初期のスケルトン / スタブ / 型骨格の一括作成 (設計契約をコメントとして埋め込む場合を含む)",
    "discipline-sonnet.md": "- 実装初期のスケルトン / スタブ / 型骨格の一括作成",
}
ITEM_REVIEW_FIX = "- レビュー・受入検証の指摘修正の一括反映"  # 3 ファイル共通

# 規模境界 (完全文・句点込み。意味反転を排除するため主語 + 述部の全文で固定する)
SCALE_SENTENCE = "スケルトン一括作成・指摘修正の一括反映は複数ファイルまたは数十行以上の規模で適用する。"  # 3 ファイル共通
SMALL_FIX_SENTENCE = {
    "discipline-fable.md": "単一ファイル数行の指摘修正は下記の例外 (直接編集してよいもの) の範囲である。",
    "discipline-sonnet.md": "単一ファイル数行の指摘修正は後述の「自明な修正」の定義に従って扱う。",
    "discipline-opus.md": "単一ファイル数行の指摘修正は後述の「委任しない作業」として直接行ってよい。",
}

# 「サブエージェントに委任する作業:」見出し (この見出し直下の bullet 行を検査範囲にする)。
DELEGATION_LIST_HEADING = "サブエージェントに委任する作業:"

# 節スコープ検査で切り出すセクション境界 (rule ID マーカー)。
ROLE_SPLIT_MARKER = "<!-- rule:role-split -->"
DELEGATION_RULES_MARKER = "<!-- rule:delegation-rules -->"

# 保全ガード対象の既存委任項目 (fable / sonnet と opus で文言が異なる)。
EXISTING_ITEM_IMPLEMENTATION = {
    "discipline-fable.md": "- 明確化された仕様に基づく実装",
    "discipline-sonnet.md": "- 明確化された仕様に基づく実装",
    "discipline-opus.md": "- 明確化された仕様に基づく、相応の規模がある実装",
}
# 3 ファイル共通の既存委任項目。
EXISTING_ITEM_INVESTIGATION = "- 方針・仕様の検討・決定のための具体的な調査"
EXISTING_ITEM_MECHANICAL = "- 機械的で並列化可能な作業 (一括修正、テスト実行と修正のループ等)"

# 直接編集/直接作業してよいものの例外段落の先頭 (太字 + コロンまで。fable / sonnet
# と opus で文言が異なる)。段落先頭一致で使うため、実ファイルの段落冒頭の整形
# (`**...**:`) をそのまま含める。
DIRECT_EXCEPTION_PARAGRAPH_PREFIX = {
    "discipline-fable.md": "**例外 (直接編集してよいもの)**:",
    "discipline-sonnet.md": "**例外 (直接編集してよいもの)**:",
    "discipline-opus.md": "**委任しない作業 (直接行ってよいもの)**:",
}
# 直接編集/直接作業の例外段落 (上記 prefix で始まる段落) に残るべき本文フレーズ。
DIRECT_EXCEPTION_BODY_PHRASE = {
    "discipline-fable.md": "数行規模で仕様の曖昧さがない自明な修正",
    "discipline-sonnet.md": "この場合も verifier は不要",
    "discipline-opus.md": "数回の tool call で完結する作業",
}


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


def scale_boundary_phrases(name: str) -> tuple[str, str]:
    """規模境界の 2 文 (存在検査・節外漂流検査の両方で共有する単一ソース)。

    片方のテストだけを更新して他方が古い定数を参照し続ける (片側更新漏れ) を
    構造的に防ぐため、検査フレーズの取得口をこの関数に一本化する。
    """
    return SCALE_SENTENCE, SMALL_FIX_SENTENCE[name]


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

    substring 包含 (旧 find_paragraph_with) では、境界段落の本文が偶然
    アンカー文字列を含むだけで誤って一致してしまう (段落衝突) ため、段落先頭
    一致に限定する。該当する段落が無ければ None。
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
    """委任対象への 2 項目追加契約 (issue #238)。Phase A では red。

    subTest は使わない: pytest (subtest 対応版) では個々の subTest 失敗が
    SUBFAILED として分離報告される一方、親テストノード自体は PASSED と表示され
    green/red の判定が曖昧になるため、リスト集約 + 1 テスト = 1 判定に保つ
    (tests/test_opus5_effort_unpin.py と同じ慣行)。
    """

    def test_delegation_items_present_as_bullets(self) -> None:
        violations = []
        for name, path in THREE_WAY.items():
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
                section, bullets, ITEM_SKELETON[name], name, "スケルトン一括作成の項目"
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

        段落を行末 rstrip 連結 (行頭インデントは保持し、行間に区切り文字を挟ま
        ない) で正規化したうえで `SCALE_SENTENCE + SMALL_FIX_SENTENCE[name]`
        と完全一致するかを検査する。substring 包含 2 回では、無関係な文脈に
        同じ部分文字列が現れる再文脈化ですり抜ける可能性があったため。
        """
        violations = []
        for name, path in THREE_WAY.items():
            section = role_split_section(read(path))
            if section is None:
                violations.append(f"{name} (role-split 節が見つからない)")
                continue
            block = delegation_bullet_block(section)
            if block is None:
                violations.append(f"{name} (委任リスト見出しが節内に無い)")
                continue
            _bullets, end_idx = block
            para = paragraph_after(section.splitlines(), end_idx)
            if para is None:
                violations.append(f"{name} (bullet ブロック直後に段落が無い)")
                continue
            normalized = "".join(line.rstrip() for line in para.splitlines())
            scale_sentence, small_fix_sentence = scale_boundary_phrases(name)
            expected = scale_sentence + small_fix_sentence
            if normalized != expected:
                violations.append(f"{name} (直後の段落が canonical 2 文と完全一致しない)")
        self.assertEqual([], violations, f"規模境界の記述が未反映: {violations}")


class ExistingDelegationRulesPreservedTests(unittest.TestCase):
    """既存の委任規範の保全ガード (Phase A から green)。

    Phase B は「サブエージェントに委任する作業」節への追記のみを行い、既存の
    委任項目・例外文言を書き換えないことを固定する。
    """

    def test_existing_delegation_items_preserved(self) -> None:
        violations = []
        for name, path in THREE_WAY.items():
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
                EXISTING_ITEM_IMPLEMENTATION[name],
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
        violations = []
        for name, path in THREE_WAY.items():
            section = role_split_section(read(path))
            if section is None:
                violations.append(f"{name} (role-split 節が見つからない)")
                continue
            prefix = DIRECT_EXCEPTION_PARAGRAPH_PREFIX[name]
            body_phrase = DIRECT_EXCEPTION_BODY_PHRASE[name]
            para = find_paragraph_starting_with(section, prefix)
            if para is None:
                violations.append(f"{name} (直接作業の例外段落 (prefix 先頭一致) が節内に無い)")
                continue
            if body_phrase not in para:
                violations.append(f"{name} (例外段落に本文フレーズが無い)")
        self.assertEqual(
            [], violations, f"直接作業の例外文言が失われている: {violations}"
        )

    def test_scale_boundary_absent_outside_role_split(self) -> None:
        """規模境界の記述が role-split 節の外へ漂流していないことの固定。

        prefix / suffix を連結せず個別に検索する (連結境界での偶然一致を排除)。
        存在検査 (test_scale_boundary_present) と同じ scale_boundary_phrases
        から検査フレーズを取得し、片側更新漏れを防ぐ。不在検査のため substring
        検索のまま維持する (完全一致は「無いこと」の検査には不適)。
        """
        violations = []
        for name, path in THREE_WAY.items():
            text = read(path)
            bounds = role_split_bounds(text)
            if bounds is None:
                violations.append(f"{name} (role-split 節が見つからない)")
                continue
            start, end = bounds
            prefix = text[:start]
            suffix = text[end:]
            for phrase in scale_boundary_phrases(name):
                if phrase in prefix or phrase in suffix:
                    violations.append(f"{name}: {phrase!r}")
        self.assertEqual([], violations, f"規模境界の記述が節外に漂流: {violations}")


if __name__ == "__main__":
    unittest.main()
