"""委任先の effort を固定しない規律と、スコープ制限指示の配置の契約テスト。

- 分業規律 (discipline.md) は委任先の effort を medium に固定する記述を持たない
  (``REMOVED_PHRASES``)。effort の選び方は rule:delegation-rules 節の Opus 5.5 基準の指針が
  定める (tests/test_opus55_fable_advisor_discipline.py が検査する)。
- rule:delegation-instruction 節では、スコープ制限の 1 文を必須とする段落の直後に、汎用的な
  再確認指示を加えない段落を置く (対で先に読ませる)。両段落の存在は
  tests/test_agent_discipline_unified_discipline.py が検査する。
- pre-push-review の code-reviewer / security-reviewer は effort を指定せずセッション既定を
  継承し、高 effort での自己修正ループを避ける較正文を本文に持つ。README はその構成を書く
  (frontmatter の契約は tests/test_subagent_model_pins.py が検査する)。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS = REPO_ROOT / "plugins" / "agent-discipline" / "hooks" / "prompts"
PRE_PUSH_AGENTS = REPO_ROOT / "plugins" / "pre-push-review" / "agents"

DISCIPLINE_VARIANTS = {
    "discipline.md": PROMPTS / "discipline.md",
}

REVIEWERS = {
    "code-reviewer.md": PRE_PUSH_AGENTS / "code-reviewer.md",
    "security-reviewer.md": PRE_PUSH_AGENTS / "security-reviewer.md",
}

PRE_PUSH_README = REPO_ROOT / "plugins" / "pre-push-review" / "README.md"

# effort を medium に固定する記述 (部分文字列)。ルール見出しだけでなく、Workflow 向けの
# 明示 (effort: 'medium') と、実効 effort が medium にならない場合の降格の断片も含める。
REMOVED_PHRASES = (
    "effort を medium にする",
    "medium 指定のみ",
    "effort: 'medium'",
    "実効 effort が medium",
    "Opus 5 を使わず実効 model を Sonnet 系に",
    "難度による引き上げ・引き下げをしない",
)

# rule:delegation-instruction 節で隣接させる 2 段落の書き出し (部分文字列)。
SCOPE_INSTRUCTION_PHRASE = "委任指示にはスコープ制限の 1 文を必ず含める"
RECHECK_BAN_PHRASE = "委任では汎用的な再確認指示を加えない"

# 節スコープ検査で切り出すセクション境界 (rule ID マーカー)。
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
    """分業規律が effort の medium 固定を持たず、スコープ制限指示を対で置くこと。

    subTest は使わない: pytest (subtest 対応版) では個々の subTest 失敗が
    SUBFAILED として分離報告される一方、親テストノード自体は PASSED と表示され
    green/red の判定が曖昧になるため、リスト集約 + 1 テスト = 1 判定に保つ
    (tests/test_agent_discipline_opus_discipline.py と同じ慣行)。
    """

    def test_medium_lock_phrases_absent(self) -> None:
        violations = [
            f"{name}: {phrase!r}"
            for phrase in REMOVED_PHRASES
            for name, path in DISCIPLINE_VARIANTS.items()
            if phrase in read(path)
        ]
        self.assertEqual([], violations, f"effort の medium 固定文を含む箇所: {violations}")

    def test_scope_instruction_precedes_recheck_ban(self) -> None:
        """スコープ制限 → 汎用再確認禁止の順の隣接配置 (対で先に読ませる)。

        rule:delegation-instruction 節を切り出し、その中で両段落が各 1 回、
        間に別段落を挟まず連続して現れることを検査する。順序のみの検査では
        別セクションへの分離や対ごとの移動を検知できないため、節スコープ +
        段落 index の隣接まで固定する。
        """
        violations = []
        for name, path in DISCIPLINE_VARIANTS.items():
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


class ReviewerCalibrationTests(unittest.TestCase):
    """effort を継承する reviewer の本文にある検証較正文。"""

    def test_reviewer_bodies_contain_verification_calibration(self) -> None:
        missing = [
            name
            for name, path in REVIEWERS.items()
            if REVIEWER_CALIBRATION_PHRASE not in body_after_frontmatter(read(path))
        ]
        self.assertEqual([], missing, f"検証較正文が無い reviewer: {missing}")


class ReviewerDocConsistencyTests(unittest.TestCase):
    """pre-push-review README の現状参照節が effort 継承の構成と整合すること。

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
