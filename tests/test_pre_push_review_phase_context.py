"""pre-push-review の Phase 文脈伝達契約テスト (issue #280)。

spec-first 2 段階の Phase A を通常の実装 diff として reviewer が解釈すると、
テスト一括先行そのものを finding にするノイズが生じる。呼び出し側が判定した
Phase 文脈を code / security reviewer にだけ渡し、判定不能時と Codex review は
従来の prompt を保つ契約を固定する。

本テストは Phase A の red test として追加し、Phase B で command 文書を実装する。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMAND = ROOT / "plugins" / "pre-push-review" / "commands" / "review.md"
PHASE_PLACEHOLDER = "{{PHASE_CONTEXT}}"


class ReviewPhaseContextContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.body = COMMAND.read_text(encoding="utf-8")

    def prompt_line(self, reviewer: str) -> str:
        match = re.search(
            rf'^\d+\. \*\*Agent / Task tool\*\*:.*'
            rf'subagent_type: "pre-push-review:{reviewer}".*$',
            self.body,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(match, f"{reviewer} launch instruction is missing")
        return match.group(0)

    def test_caller_determines_phase_without_blocking_unknown_work(self) -> None:
        self.assertIn("呼び出し側", self.body)
        self.assertIn("Phase A / Phase B / 判定不能", self.body)
        self.assertIn("判定不能の場合", self.body)
        self.assertIn("空文字列", self.body)
        self.assertIn("AskUserQuestion", self.body)

    def test_phase_a_context_excludes_test_first_workflow_from_findings(self) -> None:
        self.assertIn("Phase A", self.body)
        self.assertIn("spec-first 2 段階", self.body)
        self.assertIn("テスト一括先行", self.body)
        self.assertIn("ワークフローの仕様", self.body)
        self.assertIn("指摘対象外", self.body)

    def test_only_code_and_security_prompts_receive_phase_context(self) -> None:
        for reviewer in ("code-reviewer", "security-reviewer"):
            with self.subTest(reviewer=reviewer):
                self.assertIn(PHASE_PLACEHOLDER, self.prompt_line(reviewer))

        self.assertNotIn(PHASE_PLACEHOLDER, self.prompt_line("codex-reviewer"))

    def test_phase_b_context_is_defined(self) -> None:
        self.assertIn("Phase B", self.body)
        self.assertIn("実装本体", self.body)


if __name__ == "__main__":
    unittest.main()
