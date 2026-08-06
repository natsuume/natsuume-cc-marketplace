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

変更要求の契約は Phase A で red、Phase B のプロンプト修正で green になる。
存続規範の保全ガード (test_sonnet_no_effort_rule_preserved) は Phase A から
green であり、Phase B の書換えが既存規範を丸ごと失わないことを固定する。
"""

from __future__ import annotations

import re
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

PRE_PUSH_README = REPO_ROOT / "plugins" / "pre-push-review" / "README.md"

# 撤廃対象の旧固定文 (部分文字列)。ルール見出しだけでなく、旧 bullet 内の
# Workflow 向け明示 (effort: 'medium') と fail-closed 節 (実効 effort が medium に
# なると保証できない場合の Sonnet 降格) の断片も含め、部分書換えの残存を検知する。
REMOVED_PHRASES = (
    "effort を medium にする",
    "medium 指定のみ",
    "effort: 'medium'",
    "実効 effort が medium",
    "Opus 5 を使わず実効 model を Sonnet 系に",
    "難度による引き上げ・引き下げをしない",
)

# 置換後の新 canonical 文 (部分文字列)。
EFFORT_GUIDANCE_PHRASE = "Opus 5 を使う委任では effort を固定しない"
SCOPE_INSTRUCTION_PHRASE = "Opus 5 への委任指示にはスコープ制限の 1 文を必ず含める"
RECHECK_BAN_PHRASE = "Opus 5 への委任では汎用的な再確認指示を加えない"

# 撤廃断片 (medium 指定のみ) を含む既存 bullet のうち、存続させるべき規範文。
# Phase B の書換えが bullet 全体を誤って削除しないことを固定する保全ガード。
SONNET_RULE_PHRASE = "Sonnet 系には effort を指定しない"

# 節スコープ検査で切り出すセクション境界 (rule ID マーカー)。
DELEGATION_RULES_MARKER = "<!-- rule:delegation-rules -->"
DELEGATION_INSTRUCTION_MARKER = "<!-- rule:delegation-instruction -->"
ESCALATION_MARKER = "<!-- rule:escalation -->"

# reviewer body の較正文 (effort 継承化に伴う高 effort 自己修正ループ対策)。
REVIEWER_CALIBRATION_PHRASE = (
    "do not loop back to re-verify findings you have already confirmed"
)


FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def body_after_frontmatter(text: str) -> str:
    """YAML frontmatter (先頭の --- ... ---) を除いた本文を返す。

    reviewer 較正文は実行時に subagent へ配送される本文に存在しなければ意味が
    無いため、frontmatter (description 等の metadata) への記載を green と誤認
    しないよう検査対象から除く (tests/test_subagent_model_pins.py の同名
    ヘルパーと同じ方式)。
    """
    match = FRONTMATTER_PATTERN.match(text)
    if match is None:
        return text
    return text[match.end():]


class DisciplineEffortUnpinTests(unittest.TestCase):
    """3-way 分業規律から medium 固定が消え、新指針が入ること。

    subTest は使わない: pytest (subtest 対応版) では個々の subTest 失敗が
    SUBFAILED として分離報告される一方、親テストノード自体は PASSED と表示され
    green/red の判定が曖昧になるため、リスト集約 + 1 テスト = 1 判定に保つ
    (tests/test_agent_discipline_opus_discipline.py と同じ慣行)。
    """

    def test_medium_lock_phrases_absent(self) -> None:
        violations = [
            f"{name}: {phrase!r}"
            for phrase in REMOVED_PHRASES
            for name, path in THREE_WAY.items()
            if phrase in read(path)
        ]
        self.assertEqual([], violations, f"旧固定文が残る箇所: {violations}")

    def test_effort_selection_guidance_present(self) -> None:
        """effort 選択指針が delegation-rules 節内に存在する (節外の言及は不可)。"""
        missing = []
        for name, path in THREE_WAY.items():
            text = read(path)
            start = text.find(DELEGATION_RULES_MARKER)
            end = text.find(DELEGATION_INSTRUCTION_MARKER, max(start, 0))
            section = text[start:end] if 0 <= start < end else ""
            if EFFORT_GUIDANCE_PHRASE not in section:
                missing.append(name)
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
        """スコープ制限 → 汎用再確認禁止の順の隣接配置 (対で先に読ませる)。

        rule:delegation-instruction 節を切り出し、その中で両段落が各 1 回、
        間に別段落を挟まず連続して現れることを検査する。順序のみの検査では
        別セクションへの分離や対ごとの移動を検知できないため、節スコープ +
        段落 index の隣接まで固定する。
        """
        violations = []
        for name, path in THREE_WAY.items():
            text = read(path)
            start = text.find(DELEGATION_INSTRUCTION_MARKER)
            if start < 0:
                violations.append(f"{name} (delegation-instruction 節が無い)")
                continue
            end = text.find(ESCALATION_MARKER, start)
            section = text[start:end] if end >= 0 else text[start:]
            paragraphs = section.split("\n\n")
            scope_idxs = [
                i for i, p in enumerate(paragraphs) if SCOPE_INSTRUCTION_PHRASE in p
            ]
            recheck_idxs = [
                i for i, p in enumerate(paragraphs) if RECHECK_BAN_PHRASE in p
            ]
            if len(scope_idxs) != 1 or len(recheck_idxs) != 1:
                violations.append(
                    f"{name} (出現回数 scope={len(scope_idxs)},"
                    f" recheck={len(recheck_idxs)})"
                )
            elif recheck_idxs[0] != scope_idxs[0] + 1:
                violations.append(
                    f"{name} (scope={scope_idxs[0]}, recheck={recheck_idxs[0]})"
                )
        self.assertEqual([], violations, f"隣接配置が不成立: {violations}")

    def test_sonnet_no_effort_rule_preserved(self) -> None:
        """「Sonnet 系には effort を指定しない」ルール本体の保全ガード (Phase A から green)。

        撤廃断片「medium 指定のみ」はこの bullet の末尾にあり、Phase B は末尾の
        参照だけを書き換える。bullet 全体の削除 (存続規範の喪失) を red にする。
        """
        missing = [
            name
            for name, path in THREE_WAY.items()
            if SONNET_RULE_PHRASE not in read(path)
        ]
        self.assertEqual(
            [], missing, f"存続すべき Sonnet 規範文が無いファイル: {missing}"
        )


class ReviewerCalibrationTests(unittest.TestCase):
    """effort 継承化に伴う reviewer body の較正文の存在。"""

    def test_reviewer_bodies_contain_verification_calibration(self) -> None:
        missing = [
            name
            for name, path in REVIEWERS.items()
            if REVIEWER_CALIBRATION_PHRASE not in body_after_frontmatter(read(path))
        ]
        self.assertEqual([], missing, f"検証較正文が無い reviewer: {missing}")


class ReviewerDocConsistencyTests(unittest.TestCase):
    """pre-push-review README の現状参照節が撤廃後の構成と整合すること。

    検査文字列は現状参照節 (Agents 節の動作 bullet) に現れる書式を選んでいる。
    """

    def test_reference_prose_does_not_assert_retired_effort_pin(self) -> None:
        text = read(PRE_PUSH_README)
        self.assertNotIn("model は `opus` + `effort: medium` に固定", text)

    def test_reference_prose_describes_session_default_inheritance(self) -> None:
        text = read(PRE_PUSH_README)
        self.assertIn("effort は指定せずセッション既定を継承", text)


if __name__ == "__main__":
    unittest.main()
