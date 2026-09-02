"""pre-merge-codex-review の codex-reviewer parent-safe report 契約テスト。

wrapper が PR レビューコメントの header に付ける `status=pass|findings` は
`lib/review-status.sh` の heuristic 判定であり、Codex の report 本文の結論と
食い違うことがある。codex-reviewer subagent は `Status` を必ず report 本文
から導出し、header との食い違いを finding として捏造しない (`Note:` 1 行で
表現する) ことをこのテストで固定する。
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "pre-merge-codex-review"
AGENT = PLUGIN / "agents" / "codex-reviewer.md"
PLUGIN_README = PLUGIN / "README.md"

REQUIRED_REPORT_FIELDS = (
    "## Parent-safe report contract",
    "### Deriving `Status`",
    "Status: pass | findings | execution-failed",
    "Findings: 0",
    "Note: posted header status=",
    "regardless of the posted header status",
    "Never turn wrapper behavior",
    "applies only to a finding that Codex reported without a severity label",
    "Disposition: must-fix-before-merge | may-defer",
)

REQUIRED_SAFETY_RULES = (
    "Do not include executable command lines",
    "Do not include reusable payloads",
    "Do not include step-by-step reproduction",
    "Do not include raw stdout or stderr",
    "resume this same subagent",
)


class PreMergeReviewerParentSafeReportContractTest(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_agent_defines_the_status_derivation_contract(self) -> None:
        body = self.read(AGENT)
        for field in REQUIRED_REPORT_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, body)

    def test_agent_keeps_executable_detail_in_subagent_context(self) -> None:
        body = self.read(AGENT)
        for rule in REQUIRED_SAFETY_RULES:
            with self.subTest(rule=rule):
                self.assertIn(rule, body)

    def test_agent_preserves_upstream_p0_without_local_downgrade(self) -> None:
        body = self.read(AGENT)
        self.assertIn("Source severity: P0", body)
        self.assertIn("normalize it to `Severity: P1`", body)
        self.assertIn("Disposition: must-fix-before-merge", body)
        self.assertIn("default to `Severity: P1`", body)

    def test_documentation_states_status_is_derived_from_report_body(self) -> None:
        body = self.read(PLUGIN_README)
        self.assertIn("Note:", body)
        self.assertIn("Codex の report 本文", body)


if __name__ == "__main__":
    unittest.main()
