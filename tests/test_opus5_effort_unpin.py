"""Opus 5 effort=medium 固定の撤廃 (Claude Opus 5 System Card 準拠) の契約テスト。

背景 (spec-first Phase A):
- Claude Opus 5 System Card の実測は、(1) 高難度タスクの性能が effort とともに
  スケールすること (§8.5 FrontierBench / §8.10.1 HLE / §8.12.2 BenchCAD)、
  (2) high 超の effort ではタスク範囲外の変更 (依頼外リファクタリング等) による
  スコア低下が起きるが、スコープ制限のプロンプト指示 1 文で大半が回復すること
  (§8.4 FrontierCode、モデル限界ではないと明記)、(3) 高 effort で自己修正ループ
  (検証済み回答の再検証の反復) が報告されること (§6.2.1) を示す。
- これに基づき、agent-discipline 分業規律 3 ファイルの「Opus 5 を使う委任では
  effort を medium にする」固定 (v0.20.0 導入) を撤廃し、非拘束の effort 選択
  指針とスコープ制限指示の必須化に置換する。pre-push-review の code-reviewer /
  security-reviewer frontmatter の effort: medium 固定も撤廃する (model: opus は
  維持。frontmatter の契約は tests/test_subagent_model_pins.py が検査する)。

Phase A で red、Phase B のプロンプト修正で green になる。
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS = REPO_ROOT / "plugins" / "agent-discipline" / "hooks" / "prompts"
PRE_PUSH_AGENTS = REPO_ROOT / "plugins" / "pre-push-review" / "agents"

THREE_WAY = {
    "discipline-fable.md": PROMPTS / "discipline-fable.md",
    "discipline-opus.md": PROMPTS / "discipline-opus.md",
    "discipline-sonnet.md": PROMPTS / "discipline-sonnet.md",
}

REVIEWERS = {
    "code-reviewer.md": PRE_PUSH_AGENTS / "code-reviewer.md",
    "security-reviewer.md": PRE_PUSH_AGENTS / "security-reviewer.md",
}

# 撤廃対象の旧固定文 (部分文字列)。
REMOVED_PHRASES = (
    "effort を medium にする",
    "medium 指定のみ",
)

# 置換後の新 canonical 文 (部分文字列)。
EFFORT_GUIDANCE_PHRASE = "Opus 5 を使う委任では effort を固定しない"
SCOPE_INSTRUCTION_PHRASE = "Opus 5 への委任指示にはスコープ制限の 1 文を必ず含める"
RECHECK_BAN_PHRASE = "Opus 5 への委任では汎用的な再確認指示を加えない"

# reviewer body の較正文 (effort 継承化に伴う高 effort 自己修正ループ対策)。
REVIEWER_CALIBRATION_PHRASE = (
    "do not loop back to re-verify findings you have already confirmed"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class DisciplineEffortUnpinTests(unittest.TestCase):
    """3-way 分業規律から medium 固定が消え、新指針が入ること。

    subTest は使わない: pytest (subtest 対応版) では個々の subTest 失敗が
    SUBFAILED として分離報告される一方、親テストノード自体は PASSED と表示され
    green/red の判定が曖昧になるため、リスト集約 + 1 テスト = 1 判定に保つ
    (tests/test_agent_discipline_opus_discipline.py と同じ慣行)。
    """

    def test_medium_lock_phrases_absent(self) -> None:
        for phrase in REMOVED_PHRASES:
            offenders = [
                name for name, path in THREE_WAY.items() if phrase in read(path)
            ]
            self.assertEqual(
                [], offenders, f"旧固定文 {phrase!r} が残るファイル: {offenders}"
            )

    def test_effort_selection_guidance_present(self) -> None:
        missing = [
            name
            for name, path in THREE_WAY.items()
            if EFFORT_GUIDANCE_PHRASE not in read(path)
        ]
        self.assertEqual([], missing, f"effort 選択指針が無いファイル: {missing}")

    def test_scope_instruction_requirement_present(self) -> None:
        missing = [
            name
            for name, path in THREE_WAY.items()
            if SCOPE_INSTRUCTION_PHRASE not in read(path)
        ]
        self.assertEqual(
            [], missing, f"スコープ制限指示の必須化が無いファイル: {missing}"
        )

    def test_scope_instruction_precedes_recheck_ban(self) -> None:
        """スコープ制限 → 汎用再確認禁止の順で隣接配置される (先に読ませる)。"""
        out_of_order = []
        for name, path in THREE_WAY.items():
            text = read(path)
            if SCOPE_INSTRUCTION_PHRASE not in text or RECHECK_BAN_PHRASE not in text:
                out_of_order.append(f"{name} (文言不在)")
            elif text.index(SCOPE_INSTRUCTION_PHRASE) > text.index(RECHECK_BAN_PHRASE):
                out_of_order.append(name)
        self.assertEqual([], out_of_order, f"配置順が不正: {out_of_order}")


class ReviewerCalibrationTests(unittest.TestCase):
    """effort 継承化に伴う reviewer body の較正文の存在。"""

    def test_reviewer_bodies_contain_verification_calibration(self) -> None:
        missing = [
            name
            for name, path in REVIEWERS.items()
            if REVIEWER_CALIBRATION_PHRASE not in read(path)
        ]
        self.assertEqual([], missing, f"検証較正文が無い reviewer: {missing}")


if __name__ == "__main__":
    unittest.main()
