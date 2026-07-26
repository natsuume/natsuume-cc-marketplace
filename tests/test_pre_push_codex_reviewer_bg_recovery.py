"""pre-push-review:codex-reviewer の background-move 回収契約テスト (issue #337)。

`pre-push-review:codex-reviewer` subagent は tools が `Bash` のみのため、Claude Code
の Bash tool が timeout 時にプロセスを kill せず background へ自動移行させる現仕様下
では、移行後の出力を回収する手段が構造的に無い。Codex review 本体は完走している
のに subagent は正規 report (`Status: pass|findings`) を返せない。

修正は (1) tools を `Bash, TaskOutput, Read` に拡張、(2) background 移行時の回収
手順を手順書に明記、(3) 境界 3 種 (task ID/path 喪失・回収予算超過・task 見失い) で
`Status: execution-failed` 終了、(4) 既存 report 契約と single-run 契約は不変。

本テストは spec-first Phase A で実装前の red テストとして追加する。Phase B で
`plugins/pre-push-review/agents/codex-reviewer.md` が改訂されると green になる。

本改訂は、初版に対するレビュー指摘 (assert が非識別的で無関係な既存文言にも
誤マッチしうる、境界 3 種が個別に固定されておらず 1 つの assert で束ねられていた、
回収手順の意味論 — poll 継続条件・truncated 時の Read 補完・境界時の report 記述 —
が一文単位で契約化されていなかった) に対応し、Phase B で md に書かれるべき canonical
な一文をモジュール定数として固定した上で、`## Background-move recovery` セクション
内に空白正規化した上でその一文が存在することを個別の assert で検証する形に再構成した。

さらに 2 回目のレビュー指摘 (poll 継続条件が回収予算の超過を終了条件として含まず
無限 poll を許す抜け穴になっていた、予算超過時の report 記述に手動確認への案内が
欠けていた) に対応し、初回自動回収の予算 (TaskOutput call 回数の上限 × 各 call の
timeout 上限) と、resume 後に行う単発の bounded な status check を canonical な
一文として固定した。POLL_SENTENCE の終了条件に予算超過を明記し、BUDGET_SENTENCE
に手動確認経路 (この同じ subagent を focused な status-check question で resume
する) の案内を統合した。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODEX_REVIEWER = ROOT / "plugins" / "pre-push-review" / "agents" / "codex-reviewer.md"

FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

POLL_SENTENCE = (
    "Recover with TaskOutput (block=true) against the same task ID, "
    "repeating until the task reaches a terminal state "
    "or the recovery budget is exhausted"
)
SECOND_RUN_SENTENCE = "Do not start a second wrapper run"
TRUNCATED_SENTENCE = (
    "If the recovered report is truncated, "
    "Read the recorded output file path to complete it."
)
LOST_SENTENCE = (
    "If you lost the task ID or the output file path, "
    "return `Status: execution-failed`"
)
BUDGET_DEFINITION_SENTENCE = (
    "For the initial automatic recovery, make at most five TaskOutput calls, "
    "each with a timeout of at most ten minutes, so the recovery stays "
    "within the 60-minute background-shell limit."
)
BUDGET_SENTENCE = (
    "If the recovery budget is exhausted before the task reaches a terminal "
    "state, return `Status: execution-failed`, state in the recovery "
    "direction that the codex review is likely still running in the "
    "background, and name promptly resuming this same subagent with a "
    "focused status-check question as the manual confirmation path."
)
RESUME_CHECK_SENTENCE = (
    "A resumed status check is a single bounded TaskOutput call "
    "outside the initial recovery budget."
)
NOT_FOUND_SENTENCE = (
    "If TaskOutput reports that the task can no longer be found, "
    "return `Status: execution-failed`"
)


def recovery_section(body: str) -> str:
    """`## Background-move recovery` 見出しから次の `\\n## ` 見出し (または文書末尾)

    までの slice を返す。見出しが無ければ空文字列を返す。
    """
    marker = "## Background-move recovery"
    start = body.find(marker)
    if start == -1:
        return ""
    rest = body[start:]
    next_heading = rest.find("\n## ", len(marker))
    if next_heading == -1:
        return rest
    return rest[:next_heading]


def normalize(text: str) -> str:
    """空白・改行を単一スペースに正規化する (md の 80 桁前後の折り返し差異を吸収する)。"""
    return " ".join(text.split())


class CodexReviewerBackgroundMoveRecoveryTest(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def frontmatter_lines(self, body: str) -> list[str]:
        match = FRONTMATTER_PATTERN.match(body)
        if match is None:
            return []
        return match.group(1).splitlines()

    def test_tools_frontmatter_grants_recovery_tools(self) -> None:
        body = self.read(CODEX_REVIEWER)
        lines = self.frontmatter_lines(body)
        self.assertIn("tools: Bash, TaskOutput, Read", lines)

    def test_recovery_section_documents_background_move(self) -> None:
        body = self.read(CODEX_REVIEWER)
        section = recovery_section(body)
        self.assertNotEqual(section, "")
        normalized = normalize(section)
        self.assertIn("moved to the background", normalized)
        self.assertIn("record the task ID and the output file path", normalized)

    def test_recovery_polls_same_task_to_terminal(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(POLL_SENTENCE), normalized)

    def test_recovery_forbids_second_wrapper_run(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(SECOND_RUN_SENTENCE), normalized)

    def test_truncated_output_recovered_via_read(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(TRUNCATED_SENTENCE), normalized)

    def test_boundary_lost_id_or_path_ends_execution_failed(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(LOST_SENTENCE), normalized)

    def test_boundary_budget_exhausted_reports_still_running(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(BUDGET_SENTENCE), normalized)

    def test_recovery_budget_is_bounded(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(BUDGET_DEFINITION_SENTENCE), normalized)

    def test_resumed_check_is_bounded(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(RESUME_CHECK_SENTENCE), normalized)

    def test_boundary_task_not_found_ends_execution_failed(self) -> None:
        body = self.read(CODEX_REVIEWER)
        normalized = normalize(recovery_section(body))
        self.assertIn(normalize(NOT_FOUND_SENTENCE), normalized)

    def test_bash_only_wording_removed(self) -> None:
        body = self.read(CODEX_REVIEWER)
        self.assertNotIn("Do not invoke other tools.", body)
        self.assertNotIn("grants `Bash` only", body)


if __name__ == "__main__":
    unittest.main()
