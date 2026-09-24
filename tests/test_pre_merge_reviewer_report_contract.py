"""pre-merge-cross-review の codex-reviewer parent-safe report 契約テスト。

wrapper が PR レビューコメントの header に付ける `status=pass|findings` は
`lib/review-status.sh` の heuristic 判定であり、Codex の report 本文の結論と
食い違うことがある。codex-reviewer subagent は `Status` を必ず report 本文
から導出し、header との食い違いを finding として捏造しない (`Note:` 1 行で
表現する) ことをこのテストで固定する。

あわせて、この subagent の tool grant と background-move 回収契約が
pre-push-codex-review 側の codex-reviewer と同一であることを固定する。共通の
canonical 文と抽出 helper は `test_pre_push_codex_reviewer_bg_recovery` を正本と
して読み込み、gate 名を含む resume 時の一文だけを本ファイルで定義する。
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "pre-merge-cross-review"
AGENT = PLUGIN / "agents" / "codex-reviewer.md"
PLUGIN_README = PLUGIN / "README.md"

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


def _load_shared_recovery_contract():
    """両 plugin 共通の回収契約 module (canonical 文と抽出 helper) を読み込む。

    `import` 文で書くと「sys.path 操作より前に import 文が来る」という lint 制約
    (E402) に抵触するため、既存テストと同じ importlib 経由の明示 import にする。
    """
    return importlib.import_module("test_pre_push_codex_reviewer_bg_recovery")


_shared_contract = _load_shared_recovery_contract()

ContractTestCase = _shared_contract.ContractTestCase
SHARED_RECOVERY_CLAUSES = _shared_contract.SHARED_RECOVERY_CLAUSES
SHARED_BODY_CLAUSES = _shared_contract.SHARED_BODY_CLAUSES
TOOL_GRANT_LITERAL = _shared_contract.TOOL_GRANT_LITERAL
FORBIDDEN_EXECUTION_TOOL = _shared_contract.FORBIDDEN_EXECUTION_TOOL
FORBIDDEN_TERMINAL_CONCEPT = _shared_contract.FORBIDDEN_TERMINAL_CONCEPT

# wrapper が書く terminal sentinel の固定 prefix (plugin ごとに異なる)。案内行に
# path が全く無い場合の fallback としてだけ使う (path があって形状検証に落ちる状態は
# run 同定不能境界)。合成に使う git directory は cwd に依存しない絶対形にする。
SENTINEL_NAME_PREFIX = "pre-merge-cross-review-terminal"
SENTINEL_PATH_SENTENCE = (
    "Only when the announcement line carries a run id but no path at all, "
    "compose the sentinel path from the absolute git directory that "
    "`git rev-parse --absolute-git-dir` prints, the fixed prefix "
    f"`{SENTINEL_NAME_PREFIX}-` and this run id, and put the composed path "
    "through the same shape check before interpolating it; a path that is "
    "present but fails those checks is the unidentifiable-run boundary, not a "
    "case for this fallback."
)
# resume 後の status check の位置づけ。merge gate はローカル記録を検証するため、
# 診断目的の再読では gate を満たせないことを pre-merge 側の文言で固定する。
RESUME_CHECK_SENTENCE = (
    "A resumed status check is a single bounded Read of the recorded output "
    "file outside the initial recovery budget; it is diagnostic only and "
    "cannot write the local record — satisfying the merge gate requires a "
    "completed wrapper run that writes it."
)

REQUIRED_REPORT_FIELDS = (
    "## Parent-safe report contract",
    "### Deriving `Status`",
    "Status: pass | findings | execution-failed",
    "Findings: 0",
    "Note: recorded header status=",
    "regardless of the posted header status",
    "Never turn wrapper behavior",
    "applies only to a finding that Codex reported without a severity label",
    "Disposition: must-fix-before-merge | may-defer",
    "If the body is inconclusive",
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


class PreMergeReviewerToolGrantTest(ContractTestCase):
    """回収に使うツールの公開契約 (frontmatter の tools 行)。"""

    def test_tools_frontmatter_grants_bash_and_read(self) -> None:
        self.assert_tools_line(AGENT)

    def test_agent_body_never_mentions_task_output(self) -> None:
        self.assert_text_absent(AGENT, "TaskOutput")

    def test_agent_body_never_mentions_a_second_execution_tool(self) -> None:
        """待機を含むコマンド実行は Bash tool に閉じる (実行経路を 1 本に保つ)。"""
        self.assert_text_absent(AGENT, FORBIDDEN_EXECUTION_TOOL)

    def test_agent_body_never_uses_output_text_as_terminal_signal(self) -> None:
        """終端判定は sentinel ファイルで行い、出力テキストに依存しない。"""
        self.assert_text_absent(AGENT, FORBIDDEN_TERMINAL_CONCEPT)

    def test_agent_body_omits_agent_launch_mode_parameter(self) -> None:
        self.assert_no_agent_launch_mode_parameter(AGENT)


class PreMergeReviewerBackgroundMoveRecoveryTest(ContractTestCase):
    """`## Background-move recovery` セクションが固定すべき回収契約の一文。"""

    def test_recovery_section_states_every_shared_clause(self) -> None:
        for clause, sentence in SHARED_RECOVERY_CLAUSES.items():
            with self.subTest(clause=clause):
                self.assert_recovery_clause(AGENT, sentence)

    def test_sentinel_path_falls_back_to_the_fixed_name(self) -> None:
        self.assert_recovery_clause(AGENT, SENTINEL_PATH_SENTENCE)

    def test_resumed_status_check_is_bounded_and_diagnostic_only(self) -> None:
        self.assert_recovery_clause(AGENT, RESUME_CHECK_SENTENCE)


class PreMergeReviewerReportNormalizationTest(ContractTestCase):
    """background 移行の有無に依らず適用される report 正規化の契約。"""

    def test_body_states_every_shared_body_clause_outside_recovery(self) -> None:
        for clause, sentence in SHARED_BODY_CLAUSES.items():
            with self.subTest(clause=clause):
                self.assert_body_clause_outside_recovery(AGENT, sentence)


class PreMergeReviewerDocumentationTest(ContractTestCase):
    """README が現在の tool grant と起動仕様を説明する。"""

    def test_plugin_readme_documents_current_tool_grant(self) -> None:
        self.assert_text_absent(PLUGIN_README, "TaskOutput")
        self.assert_text_absent_outside_known_constraints(
            PLUGIN_README, FORBIDDEN_EXECUTION_TOOL
        )
        self.assert_text_contains(PLUGIN_README, TOOL_GRANT_LITERAL)

    def test_plugin_readme_omits_agent_launch_mode_parameter(self) -> None:
        self.assert_no_agent_launch_mode_parameter(PLUGIN_README)


if __name__ == "__main__":
    unittest.main()
