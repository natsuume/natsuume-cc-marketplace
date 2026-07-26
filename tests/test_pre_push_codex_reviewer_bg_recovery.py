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
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODEX_REVIEWER = ROOT / "plugins" / "pre-push-review" / "agents" / "codex-reviewer.md"

FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


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

    def test_procedure_documents_background_move_recovery(self) -> None:
        body = self.read(CODEX_REVIEWER)
        self.assertIn("## Background-move recovery", body)
        self.assertIn("moved to the background", body)
        self.assertIn("task ID", body)
        self.assertIn("output file path", body)
        self.assertIn("TaskOutput", body)

    def test_recovery_forbids_second_wrapper_run(self) -> None:
        body = self.read(CODEX_REVIEWER)
        self.assertIn("Do not start a second wrapper run", body)

    def test_truncated_output_recovered_via_read(self) -> None:
        body = self.read(CODEX_REVIEWER)
        self.assertIn("truncated", body)
        self.assertIn("Read", body)
        self.assertIn("output file", body)

    def test_boundary_cases_end_with_execution_failed(self) -> None:
        body = self.read(CODEX_REVIEWER)
        self.assertIn("lost the task ID", body)
        self.assertIn("recovery budget", body)
        self.assertIn("still running in the background", body)
        self.assertIn("Status: execution-failed", body)

    def test_bash_only_wording_removed(self) -> None:
        body = self.read(CODEX_REVIEWER)
        self.assertNotIn("Do not invoke other tools.", body)
        self.assertNotIn("grants `Bash` only", body)


if __name__ == "__main__":
    unittest.main()
