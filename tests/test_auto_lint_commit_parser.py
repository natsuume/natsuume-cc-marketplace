"""auto-lint-check commit parser の cd 順序契約テスト (issue #146)。"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARSER = (
    ROOT
    / "plugins"
    / "auto-lint-check"
    / "hooks"
    / "scripts"
    / "lib"
    / "parse-commit-command.py"
)


class AutoLintCommitParserCdOrderTest(unittest.TestCase):
    def classify(self, command: str) -> int:
        result = subprocess.run(
            [sys.executable, str(PARSER), command],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        return result.returncode

    def test_cd_after_plain_commit_does_not_override_that_commit(self) -> None:
        self.assertEqual(
            self.classify('git commit -m "change" && cd /tmp && npm test'),
            5,
        )

    def test_cd_in_trailing_subshell_does_not_override_prior_commit(self) -> None:
        self.assertEqual(
            self.classify('git commit -m "change"; (cd /tmp && ls)'),
            5,
        )

    def test_cd_before_commit_remains_repo_override(self) -> None:
        self.assertEqual(
            self.classify('cd /tmp && git commit -m "change"'),
            3,
        )

    def test_cd_between_two_commits_overrides_the_later_commit(self) -> None:
        self.assertEqual(
            self.classify(
                'git commit -m "first" && cd /tmp && git commit -m "second"'
            ),
            3,
        )


if __name__ == "__main__":
    unittest.main()
