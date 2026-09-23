"""pre-push-review の security-reviewer が中間 commit を検査する契約テスト。

marker hash は HEAD の commit OID を束縛するため、add → revert の鎖でも push gate は
再レビューを要求する。しかし reviewer が net diff だけを見ると、打ち消された中間
commit の中身 (秘匿情報・危険コード) はレビューに映らないまま remote 履歴へ到達する。
security-reviewer だけが per-commit patch を読み、code-reviewer は net diff のまま
とする分担と、検出後の修正方針 (履歴からの除去・ローテーション) を固定する。
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "pre-push-review"
SECURITY_REVIEWER = PLUGIN / "agents" / "security-reviewer.md"
CODE_REVIEWER = PLUGIN / "agents" / "code-reviewer.md"
REVIEW_COMMAND = PLUGIN / "commands" / "review.md"
PLUGIN_README = PLUGIN / "README.md"

PER_COMMIT_PATCH_COMMAND = "git log -p --no-ext-diff --no-textconv origin/HEAD..HEAD"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    """`heading` 行から次の同レベル以上の見出しまでを返す。"""
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines()
    start = lines.index(heading)
    collected = []
    for line in lines[start + 1 :]:
        stripped = line.lstrip("#")
        line_level = len(line) - len(stripped)
        if line_level and line_level <= level and stripped.startswith(" "):
            break
        collected.append(line)
    return "\n".join(collected)


class SecurityReviewerCommitHistoryScopeTest(unittest.TestCase):
    def test_security_reviewer_scope_reads_per_commit_patches(self) -> None:
        scope = section(read(SECURITY_REVIEWER), "## Scope")
        self.assertIn(PER_COMMIT_PATCH_COMMAND, scope)

    def test_security_reviewer_objective_covers_intermediate_commits(self) -> None:
        objective = section(read(SECURITY_REVIEWER), "## Objective")
        self.assertIn("intermediate commit", objective)
        self.assertIn("net diff", objective)

    def test_intermediate_commit_secrets_are_not_excluded_as_test_or_docs(self) -> None:
        exclusions = section(read(SECURITY_REVIEWER), "## Exclusions (do NOT report)")
        self.assertIn("intermediate commit", exclusions)

    def test_security_reviewer_locates_findings_by_commit(self) -> None:
        body = read(SECURITY_REVIEWER)
        self.assertIn("Location: <file>:<line-or-range> [(commit <short-sha>)]", body)

    def test_security_reviewer_fix_direction_removes_secret_from_history(self) -> None:
        body = read(SECURITY_REVIEWER)
        self.assertIn("remove it from the branch history", body)
        self.assertIn("rotate", body)

    def test_code_reviewer_keeps_net_diff_only(self) -> None:
        scope = section(read(CODE_REVIEWER), "## Scope")
        self.assertNotIn("git log -p", scope)


class CommitHistoryFixFlowDocumentationTest(unittest.TestCase):
    def test_review_command_fix_flow_requires_history_removal(self) -> None:
        body = read(REVIEW_COMMAND)
        self.assertIn("中間 commit", body)
        self.assertIn("履歴から除去", body)

    def test_readme_security_reviewer_section_describes_per_commit_scope(self) -> None:
        agent_section = section(read(PLUGIN_README), "#### `pre-push-review:security-reviewer` (subagent)")
        self.assertIn("per-commit patch", agent_section)

    def test_readme_known_limitations_state_llm_detection_is_not_deterministic(self) -> None:
        limitations = section(read(PLUGIN_README), "## 既知の制約")
        self.assertIn("中間 commit", limitations)
        self.assertIn("gitleaks", limitations)


if __name__ == "__main__":
    unittest.main()
