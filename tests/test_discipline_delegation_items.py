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
  Phase B のプロンプト本文修正で green になる。既存の委任項目・直接作業の
  例外文言の保全ガードと、規模境界の記述が role-split 節の外へ漂流しないことの
  固定は Phase A から green である (Phase B の書換えが既存規範を破壊しないことを
  保証する)。
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

# 規模境界の記述 (両方が role-split 節内に必要)。
SCALE_BOUNDARY = "複数ファイルまたは数十行以上の規模で適用する"
SMALL_FIX_PHRASE = "単一ファイル数行の指摘修正は"

# 「サブエージェントに委任する作業:」見出し (この見出し以降を検査範囲にする)。
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
            heading_pos = section.find(DELEGATION_LIST_HEADING)
            if heading_pos < 0:
                violations.append(f"{name} (委任リスト見出しが節内に無い)")
                continue
            scope_lines = [
                line.strip() for line in section[heading_pos:].splitlines()
            ]
            if ITEM_SKELETON not in scope_lines:
                violations.append(f"{name} (スケルトン一括作成の項目が無い)")
            if ITEM_REVIEW_FIX not in scope_lines:
                violations.append(f"{name} (レビュー指摘修正一括反映の項目が無い)")
        self.assertEqual([], violations, f"委任項目の追加が未反映: {violations}")

    def test_scale_boundary_present(self) -> None:
        violations = []
        for name, path in THREE_WAY.items():
            section = role_split_section(read(path))
            if section is None:
                violations.append(f"{name} (role-split 節が見つからない)")
                continue
            if SCALE_BOUNDARY not in section:
                violations.append(f"{name} (規模境界の記述が無い)")
            if SMALL_FIX_PHRASE not in section:
                violations.append(f"{name} (小規模修正を除外する記述が無い)")
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
            lines = [line.strip() for line in section.splitlines()]
            if EXISTING_ITEM_IMPLEMENTATION[name] not in lines:
                violations.append(f"{name} (既存の実装委任項目が無い)")
            if EXISTING_ITEM_INVESTIGATION not in lines:
                violations.append(f"{name} (既存の調査委任項目が無い)")
            if EXISTING_ITEM_MECHANICAL not in lines:
                violations.append(f"{name} (既存の機械的作業委任項目が無い)")
        self.assertEqual([], violations, f"既存の委任項目が失われている: {violations}")

    def test_direct_edit_exception_preserved(self) -> None:
        violations = []
        for name, path in THREE_WAY.items():
            section = role_split_section(read(path))
            if section is None:
                violations.append(f"{name} (role-split 節が見つからない)")
                continue
            if DIRECT_EXCEPTION_HEADING[name] not in section:
                violations.append(f"{name} (直接作業の例外見出しが無い)")
        self.assertEqual(
            [], violations, f"直接作業の例外見出しが失われている: {violations}"
        )

    def test_scale_boundary_absent_outside_role_split(self) -> None:
        """規模境界の記述が role-split 節の外へ漂流していないことの固定。"""
        violations = []
        for name, path in THREE_WAY.items():
            text = read(path)
            bounds = role_split_bounds(text)
            if bounds is None:
                violations.append(f"{name} (role-split 節が見つからない)")
                continue
            start, end = bounds
            outside = text[:start] + text[end:]
            if SCALE_BOUNDARY in outside:
                violations.append(f"{name} (role-split 節の外に規模境界の記述がある)")
        self.assertEqual([], violations, f"規模境界の記述が節外に漂流: {violations}")


if __name__ == "__main__":
    unittest.main()
