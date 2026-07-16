"""pre-push-review reviewer の parent-safe report 契約テスト (issue #281)。

Fable-sensitive な実行可能詳細は reviewer subagent の context に留め、親 session
には修正要否を判断できる構造化 summary だけを返す。3 reviewer の agent 定義と
orchestration command が同じ契約を維持し、raw stdout / stderr や再現 recipe を
親へ relay する指示へ退行しないことを固定する。

本テストは TDD Phase A で実装前の red テストとして追加する。
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "pre-push-review"
AGENTS = {
    "Code": PLUGIN / "agents" / "code-reviewer.md",
    "Codex": PLUGIN / "agents" / "codex-reviewer.md",
    "Security": PLUGIN / "agents" / "security-reviewer.md",
}
COMMAND = PLUGIN / "commands" / "review.md"

REQUIRED_REPORT_FIELDS = (
    "Status: pass | findings | execution-failed",
    "Severity: P1 | P2 | P3",
    "Confidence: high | medium | low",
    "Location:",
    "Cause class:",
    "Violated invariant:",
    "Impact:",
    "Verification: verified | partially-verified | unverified",
    "Fix direction:",
    "Disposition: must-fix-before-push | may-defer",
)

REQUIRED_SAFETY_RULES = (
    "Do not include executable command lines",
    "Do not include reusable payloads",
    "Do not include step-by-step reproduction",
    "Do not include raw stdout or stderr",
    "resume this same subagent",
)


class ReviewerParentSafeReportContractTest(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_all_reviewers_define_the_same_parent_safe_schema(self) -> None:
        for reviewer, path in AGENTS.items():
            with self.subTest(reviewer=reviewer):
                body = self.read(path)
                self.assertIn("## Parent-safe report contract", body)
                for field in REQUIRED_REPORT_FIELDS:
                    self.assertIn(field, body)
                self.assertIn("Findings: 0", body)
                self.assertIn("unknown", body)

    def test_all_reviewers_keep_executable_detail_in_subagent_context(self) -> None:
        for reviewer, path in AGENTS.items():
            with self.subTest(reviewer=reviewer):
                body = self.read(path)
                for rule in REQUIRED_SAFETY_RULES:
                    self.assertIn(rule, body)

    def test_codex_reviewer_no_longer_relays_wrapper_output_verbatim(self) -> None:
        body = self.read(AGENTS["Codex"])
        forbidden = (
            "<stderr verbatim>",
            "<stdout verbatim>",
            "Return the captured output",
            "relay its output",
            "stdout / stderr をまとめた markdown report",
        )
        for text in forbidden:
            with self.subTest(text=text):
                self.assertNotIn(text, body)

    def test_review_command_requests_parent_safe_reports(self) -> None:
        body = self.read(COMMAND)
        self.assertIn("parent-safe", body)
        self.assertIn("実行可能な詳細を親 session に返さない", body)
        self.assertNotIn(
            "stdout / stderr をまとめた markdown report を返してください", body
        )


if __name__ == "__main__":
    unittest.main()
