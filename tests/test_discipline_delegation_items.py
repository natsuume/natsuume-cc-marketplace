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

改訂 (pre-push review (codex / correctness) の指摘と Codex rescue 壁打ちで確定):
検査範囲を「委任リスト見出し直下の bullet 行の並び」「その直後の空行区切り
段落」「例外見出しと同一段落」まで精密化し、節全体の部分文字列検索による
過検出・過小検出 (無関係な箇所での偶然一致・節境界をまたいだ誤検出) を防ぐ。
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

# 追加対象の委任項目 (canonical 文字列。一字一句この通り)。
ITEM_SKELETON = (
    "- 実装初期のスケルトン / スタブ / 型骨格の一括作成"
    " (設計契約をコメントとして埋め込む場合を含む)"
)
ITEM_REVIEW_FIX = "- レビュー・受入検証の指摘修正の一括反映"

# 規模境界の記述 (両方が同一段落内に必要)。
SCALE_BOUNDARY = "複数ファイルまたは数十行以上の規模で適用する"
SMALL_FIX_PHRASE = "単一ファイル数行の指摘修正は"

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

# 直接編集/直接作業してよいものの例外見出し (fable / sonnet と opus で文言が異なる)。
DIRECT_EXCEPTION_HEADING = {
    "discipline-fable.md": "例外 (直接編集してよいもの)",
    "discipline-sonnet.md": "例外 (直接編集してよいもの)",
    "discipline-opus.md": "委任しない作業 (直接行ってよいもの)",
}
# 直接編集/直接作業の例外見出しと同一段落に残るべき本文フレーズ。
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


def delegation_bullet_block(section: str) -> tuple[list[str], int] | None:
    """委任リスト見出し直下の bullet 行の並びと、ブロック終端の行 index を返す。

    (i) strip() 後の完全一致で DELEGATION_LIST_HEADING に一致する行を探す
    (部分文字列検索では他文脈への偶然一致を拾うため)。
    (ii) 見出し行の直後に空行があれば 1 行だけ許容してスキップする。
    (iii) それに続く行を走査し、`- ` で始まる行 (strip 後) だけを bullet として
    連続収集する。空行は打ち切り条件を満たさない (非空 かつ 非 bullet) ため
    読み飛ばして走査を継続し、最初の「非空・非 bullet」行に到達したところで
    打ち切る。戻り値の終端 index はその行 (直後段落の先頭) を指す。

    見出しが節内に見つからない場合は None を返す。
    """
    lines = section.splitlines()
    heading_idx = None
    for i, line in enumerate(lines):
        if line.strip() == DELEGATION_LIST_HEADING:
            heading_idx = i
            break
    if heading_idx is None:
        return None

    idx = heading_idx + 1
    if idx < len(lines) and lines[idx].strip() == "":
        idx += 1

    bullets: list[str] = []
    while idx < len(lines):
        stripped = lines[idx].strip()
        if stripped.startswith("- "):
            bullets.append(stripped)
            idx += 1
            continue
        if stripped == "":
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


def find_paragraph_with(section: str, marker: str) -> str | None:
    """節内を空行区切りの段落に分割し、marker を含む最初の段落を返す。"""
    for para in section.split("\n\n"):
        if marker in para:
            return para
    return None


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
            if ITEM_SKELETON not in bullets:
                violations.append(f"{name} (スケルトン一括作成の項目が無い)")
            if ITEM_REVIEW_FIX not in bullets:
                violations.append(f"{name} (レビュー指摘修正一括反映の項目が無い)")
        self.assertEqual([], violations, f"委任項目の追加が未反映: {violations}")

    def test_scale_boundary_present(self) -> None:
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
            if SCALE_BOUNDARY not in para:
                violations.append(f"{name} (規模境界の記述が直後の段落に無い)")
            if SMALL_FIX_PHRASE not in para:
                violations.append(f"{name} (小規模修正を除外する記述が直後の段落に無い)")
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
            if EXISTING_ITEM_IMPLEMENTATION[name] not in bullets:
                violations.append(f"{name} (既存の実装委任項目が無い)")
            if EXISTING_ITEM_INVESTIGATION not in bullets:
                violations.append(f"{name} (既存の調査委任項目が無い)")
            if EXISTING_ITEM_MECHANICAL not in bullets:
                violations.append(f"{name} (既存の機械的作業委任項目が無い)")
        self.assertEqual([], violations, f"既存の委任項目が失われている: {violations}")

    def test_direct_edit_exception_preserved(self) -> None:
        violations = []
        for name, path in THREE_WAY.items():
            section = role_split_section(read(path))
            if section is None:
                violations.append(f"{name} (role-split 節が見つからない)")
                continue
            heading = DIRECT_EXCEPTION_HEADING[name]
            body_phrase = DIRECT_EXCEPTION_BODY_PHRASE[name]
            para = find_paragraph_with(section, heading)
            if para is None:
                violations.append(f"{name} (直接作業の例外見出しが節内に無い)")
                continue
            if body_phrase not in para:
                violations.append(f"{name} (例外見出しと同一段落に本文フレーズが無い)")
        self.assertEqual(
            [], violations, f"直接作業の例外文言が失われている: {violations}"
        )

    def test_scale_boundary_absent_outside_role_split(self) -> None:
        """規模境界の記述が role-split 節の外へ漂流していないことの固定。

        prefix / suffix を連結せず個別に検索する (連結境界での偶然一致を排除)。
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
            for phrase in (SCALE_BOUNDARY, SMALL_FIX_PHRASE):
                if phrase in prefix or phrase in suffix:
                    violations.append(f"{name}: {phrase!r}")
        self.assertEqual([], violations, f"規模境界の記述が節外に漂流: {violations}")


if __name__ == "__main__":
    unittest.main()
