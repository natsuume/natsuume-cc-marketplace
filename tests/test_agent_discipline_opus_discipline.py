"""agent-discipline: 分業規律 (discipline.md) の verifier 委任・委任粒度の契約テスト。

分業規律はメインセッションとワーカーを Opus 5.5 で動かす前提で書く。Opus 系は指示がなくても
自分の作業を検証・修正するため、verifier 委任は基準を満たす場合に限る限定式にし、一律に
verifier 委任を義務付ける文言を持たない。委任は真に独立した相応の規模の作業に限る。

配送経路・サイズ・rule ID 集合・必須文言のうち分業規律全体に関わる契約は
tests/test_agent_discipline_unified_discipline.py が検査する。
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISCIPLINE_MD = ROOT / "plugins" / "agent-discipline" / "hooks" / "prompts" / "discipline.md"

# verifier 委任の基準と委任粒度の必須文言。
REQUIRED_PHRASES = (
    "verifier 委任の基準 (限定式)",
    "委任は真に独立した相応の規模の作業に限る",
)

# verifier 委任を一律に義務付ける文言 (限定式の基準と矛盾するため含めない)。
UNIFORM_VERIFIER_OBLIGATION_PHRASES = (
    "verifier 委任の義務 (必須)",
    "非自明な全成果物",
)


class DisciplineVerifierDelegationTests(unittest.TestCase):
    """discipline.md の verifier 委任の基準と委任粒度の記述。"""

    def test_discipline_contains_required_phrases(self) -> None:
        """discipline.md が verifier 委任の基準 (限定式) と委任粒度の抑制の文言を含むこと。"""
        text = DISCIPLINE_MD.read_text(encoding="utf-8")
        # subTest は使わない: pytest (subtest 対応版) では個々の subTest 失敗が
        # SUBFAILED として分離報告される一方、親テストノード自体は PASSED と表示され
        # green/red の判定が曖昧になるため、1 テスト = 1 判定に保つ。
        missing = [phrase for phrase in REQUIRED_PHRASES if phrase not in text]
        self.assertEqual([], missing, f"discipline.md に含まれない必須文言: {missing}")

    def test_discipline_excludes_uniform_verifier_obligation_phrases(self) -> None:
        """discipline.md が verifier 委任を一律に義務付ける文言を含まないこと。"""
        text = DISCIPLINE_MD.read_text(encoding="utf-8")
        leaked = [phrase for phrase in UNIFORM_VERIFIER_OBLIGATION_PHRASES if phrase in text]
        self.assertEqual([], leaked, f"discipline.md に含めない文言: {leaked}")


if __name__ == "__main__":
    unittest.main()
