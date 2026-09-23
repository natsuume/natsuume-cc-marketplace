"""auto-lint-check commit parser の契約テスト (cd 順序 / heredoc 本文の除外)。"""

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


class AutoLintCommitParserHeredocBodyTest(unittest.TestCase):
    """heredoc 本文はデータであり、本文中の `git commit` を実 commit と
    判定しないことの契約。"""

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

    def test_subshell_commit_in_body_after_cd_is_not_repo_override(self) -> None:
        self.assertEqual(
            self.classify(
                "cd /tmp/x && cat > f.md <<'EOF'\nfoo (git commit)\nEOF"
            ),
            4,
        )

    def test_subshell_commit_in_body_is_not_commit(self) -> None:
        self.assertEqual(
            self.classify("cat > f.md <<'EOF'\nfoo (git commit)\nEOF"),
            4,
        )

    def test_git_commit_words_in_body_after_cd_is_not_commit(self) -> None:
        self.assertEqual(
            self.classify(
                "cd /tmp/x && cat > f.md <<'EOF'\nsee git commit docs\nEOF"
            ),
            4,
        )

    def test_parenthesized_word_before_commit_in_body_is_not_commit(self) -> None:
        self.assertEqual(
            self.classify(
                "cd /tmp/x && cat > f.md <<'EOF'\nx (git-guardrails) commit\nEOF"
            ),
            4,
        )

    def test_commit_reading_message_from_heredoc_remains_commit(self) -> None:
        self.assertEqual(
            self.classify(
                "git commit -F - <<'EOF'\nfix: message body\n\nsecond paragraph\nEOF"
            ),
            5,
        )

    def test_safe_message_substitution_heredoc_remains_commit(self) -> None:
        self.assertEqual(
            self.classify(
                "git commit -m \"$(cat <<'EOF'\nfix: message (git commit)\nEOF\n)\""
            ),
            5,
        )

    def test_double_quoted_delimiter_body_is_not_commit(self) -> None:
        self.assertEqual(
            self.classify('cat > f.md <<"EOF"\nfoo (git commit)\nEOF'),
            4,
        )

    def test_unquoted_delimiter_body_is_not_commit(self) -> None:
        self.assertEqual(
            self.classify("cat > f.md <<EOF\nfoo (git commit)\nEOF"),
            4,
        )

    def test_command_after_terminator_is_still_parsed(self) -> None:
        self.assertEqual(
            self.classify(
                "cat > f.md <<'EOF'\nfoo (git commit)\nEOF\n"
                "git add f.md && git commit -m change"
            ),
            0,
        )

    def test_dash_heredoc_body_with_tab_indented_terminator_is_not_commit(
        self,
    ) -> None:
        self.assertEqual(
            self.classify("cat > f.md <<-EOF\n\tfoo (git commit)\n\tEOF\n"),
            4,
        )

    def test_dash_heredoc_tab_indented_terminator_ends_body(self) -> None:
        self.assertEqual(
            self.classify(
                "cat > f.md <<-'EOF'\n\tbody\n\tEOF\ngit commit -m change"
            ),
            5,
        )

    def test_tab_indented_line_does_not_end_plain_heredoc(self) -> None:
        self.assertEqual(
            self.classify("cat > f.md <<'EOF'\n\tEOF\nfoo (git commit)\nEOF"),
            4,
        )

    def test_unterminated_heredoc_excludes_rest_as_body(self) -> None:
        self.assertEqual(
            self.classify("cat > f.md <<'EOF'\nfoo (git commit)\n"),
            4,
        )

    def test_unterminated_heredoc_with_unbalanced_quote_is_not_parse_failure(
        self,
    ) -> None:
        self.assertEqual(
            self.classify("cat > f.md <<'EOF'\nit's (git commit)"),
            4,
        )

    def test_multiple_heredocs_on_one_line_are_all_excluded(self) -> None:
        self.assertEqual(
            self.classify(
                "cat <<A <<B\nfirst (git commit)\nA\nsecond (git commit)\nB"
            ),
            4,
        )

    def test_multiple_heredocs_consume_bodies_in_operator_order(self) -> None:
        self.assertEqual(
            self.classify("cat <<A <<B\nB\nA\nfoo (git commit)\nB"),
            4,
        )

    def test_command_after_multiple_heredocs_is_still_parsed(self) -> None:
        self.assertEqual(
            self.classify(
                "cat <<A <<B\nfirst\nA\nsecond\nB\ngit commit -m change"
            ),
            5,
        )

    def test_here_string_is_not_treated_as_heredoc(self) -> None:
        self.assertEqual(
            self.classify("cat <<<EOF\ngit commit -m change\nEOF"),
            5,
        )

    def test_partial_delimiter_line_does_not_end_body(self) -> None:
        self.assertEqual(
            self.classify("cat > f.md <<'EOF'\nEOFX\nfoo (git commit)\nEOF"),
            4,
        )

    def test_unquoted_delimiter_body_substitution_still_fails_closed(
        self,
    ) -> None:
        self.assertEqual(
            self.classify("cat > f.md <<EOF\n$(git commit -am bypass)\nEOF"),
            3,
        )


if __name__ == "__main__":
    unittest.main()
