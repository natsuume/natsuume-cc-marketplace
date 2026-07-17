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

    def test_staging_commit_before_cd_does_not_hide_later_override(self) -> None:
        self.assertEqual(
            self.classify(
                'git commit -am "first" && cd /tmp && git commit -m "second"'
            ),
            3,
        )

    def test_overridden_add_does_not_mark_cwd_commit_as_staging(self) -> None:
        self.assertEqual(
            self.classify(
                'git -C /tmp/other add file.py && git commit -m "change"'
            ),
            5,
        )

    def test_later_overridden_non_commit_does_not_hide_cwd_add(self) -> None:
        self.assertEqual(
            self.classify(
                "git add file.py && git commit -m change "
                "&& git -C /tmp/other status"
            ),
            0,
        )

    def test_dry_run_after_cd_does_not_override_prior_commit(self) -> None:
        self.assertEqual(
            self.classify(
                'git commit -m "first" && cd /tmp && git commit --dry-run'
            ),
            5,
        )

    def test_help_after_cd_does_not_override_prior_staging_commit(self) -> None:
        self.assertEqual(
            self.classify(
                'git commit -am "first" && cd /tmp && git commit --help'
            ),
            0,
        )

    def test_cd_before_only_dry_run_has_no_mutating_commit(self) -> None:
        self.assertEqual(
            self.classify("cd /tmp && git commit --dry-run"),
            4,
        )


if __name__ == "__main__":
    unittest.main()
