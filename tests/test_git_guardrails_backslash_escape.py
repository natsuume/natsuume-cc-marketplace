r"""git-guardrails の default-branch.sh における backslash escape 消費の契約テスト。

`find_group_close` と `strip_squoted_text` は command 文字列を 1 文字ずつ走査し、
quote 文脈 (single quote / double quote) を追跡する。bash の quote 規則に合わせて、
次の 2 種類の backslash escape を「2 文字まとめて消費する」ことが契約である:

- double quote 内の `\` + (`$` / `` ` `` / `"` / `\`): 2 文字目は quote 状態を変えない
  (`"a\\"` の閉じ `"` は escape されていない本物の閉じ quote)
- quote 外の `\` + 任意 1 文字: 2 文字目は literal (`\)` / `\}` / `\'` は group の
  閉じ文字・quote の開始として扱わない)

この消費が不発になると、`find_group_close` は閉じ文字の index を返せないか誤った
位置を返し、hook のグループ unwrap は「閉じ文字不明」経路 (先頭 1 文字のみ剥がして
再分割) に落ちる。末尾 token に `)` が付着したまま (`master)` / `push)` / `commit)` /
`create)`) invocation / refspec 判定に進むため、default branch への push / commit /
PR 作成が allow される (fail-open)。`strip_squoted_text` の不発は、quote 外 `\'` を
single quote の開始と誤認して後続の `$(...)` を空白化する (置換 shape の第 2 パスの
検出漏れ = fail-open) か、double quote の閉じを見失って single quote 領域を残す
(実行されない literal を deny する誤 deny) 方向に判定を反転させる。

テストは 2 層で契約を固定する:

1. lib の関数単体: bash を subprocess で起動して lib を source し、入力文字列を
   argv の位置引数として渡す (shell の再クオートを経由させないため、backslash の
   個数がテストコードから関数の引数まで変化しない)。
2. hook の process boundary: 使い捨ての git リポジトリをカレントディレクトリにして
   hook に PreToolUse JSON を stdin で渡し、permissionDecision を観測する。probe 対象の
   command 文字列自体は実行しない。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK_DIR = ROOT / "plugins" / "git-guardrails" / "hooks" / "scripts"
DEFAULT_BRANCH_LIB = HOOK_DIR / "lib" / "default-branch.sh"
PUSH_HOOK = HOOK_DIR / "block-default-branch-push.sh"
COMMIT_HOOK = HOOK_DIR / "block-default-branch-commit.sh"
PR_HOOK = HOOK_DIR / "block-default-branch-pr.sh"


def call_lib_function(function_name: str, argument: str) -> subprocess.CompletedProcess[bytes]:
    """default-branch.sh を source した bash で `<function_name> "<argument>"` を実行する。"""
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1" || exit 99; "$2" "$3"',
            "default-branch-test",
            str(DEFAULT_BRANCH_LIB),
            function_name,
            argument,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )


@unittest.skipUnless(shutil.which("bash"), "lib test requires bash")
class FindGroupCloseEscapeTest(unittest.TestCase):
    def assert_close_index(self, segment: str, expected_index: int) -> None:
        self.assertIn(segment[expected_index], ")}", "テスト入力の期待 index が閉じ文字を指していない")
        result = call_lib_function("find_group_close", segment)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stdout.decode("utf-8"), str(expected_index))

    def test_escaped_backslash_in_dquote_does_not_escape_closing_quote(self) -> None:
        segment = r'(echo "a\\" ; echo SECOND)'
        self.assert_close_index(segment, len(segment) - 1)

    def test_escaped_backslash_in_dquote_directly_before_group_close(self) -> None:
        segment = r'(echo "a\\")'
        self.assert_close_index(segment, len(segment) - 1)

    def test_escaped_paren_outside_quote_is_not_group_close(self) -> None:
        segment = r"(echo a\) ; echo SECOND)"
        self.assert_close_index(segment, len(segment) - 1)

    def test_escaped_brace_outside_quote_is_not_group_close(self) -> None:
        segment = r"{ echo \} ; }"
        self.assert_close_index(segment, len(segment) - 1)

    def test_escaped_semicolon_outside_quote_keeps_close_index(self) -> None:
        segment = r"(echo a\; echo SECOND)"
        self.assert_close_index(segment, len(segment) - 1)

    def test_escaped_backslash_outside_quote_before_close_is_group_close(self) -> None:
        segment = r"(echo x\\) ; echo SECOND"
        self.assert_close_index(segment, segment.index(")"))

    def test_escaped_dollar_in_dquote_is_preserved(self) -> None:
        segment = r'(echo "a\$" ; echo SECOND)'
        self.assert_close_index(segment, len(segment) - 1)

    def test_escaped_dquote_in_dquote_is_preserved(self) -> None:
        segment = r'(echo "\"" ; echo SECOND)'
        self.assert_close_index(segment, len(segment) - 1)

    def test_unterminated_group_returns_failure(self) -> None:
        result = call_lib_function("find_group_close", r'(echo "a\\" ; echo SECOND')
        self.assertEqual(result.returncode, 1, result.stderr.decode("utf-8"))
        self.assertEqual(result.stdout, b"")


@unittest.skipUnless(shutil.which("bash"), "lib test requires bash")
class StripSquotedTextEscapeTest(unittest.TestCase):
    def assert_stripped(self, command: str, expected: str) -> None:
        result = call_lib_function("strip_squoted_text", command)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stdout.decode("utf-8"), expected)

    def test_escaped_backslash_in_dquote_does_not_escape_closing_quote(self) -> None:
        self.assert_stripped(r"""echo "a\\" ; echo 'SECOND'""", r'echo "a\\" ; echo  ')

    def test_squoted_substitution_after_escaped_backslash_in_dquote_is_stripped(self) -> None:
        self.assert_stripped(
            r"""echo "a\\" '$(git push origin master)'""", r'echo "a\\"  '
        )

    def test_escaped_squote_outside_quote_does_not_open_squote(self) -> None:
        command = r"echo \' $(git push origin master) \'"
        self.assert_stripped(command, command)

    def test_escaped_semicolon_outside_quote_is_preserved(self) -> None:
        self.assert_stripped(r"echo a\; 'x'", r"echo a\;  ")

    def test_escaped_backslash_outside_quote_before_squote(self) -> None:
        self.assert_stripped(r"echo \\'x'", "echo \\\\ ")

    def test_escaped_dollar_in_dquote_is_preserved(self) -> None:
        self.assert_stripped(r"""echo "\$" '$(x)'""", r'echo "\$"  ')


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("git") and shutil.which("jq"),
    "hook integration requires bash, git, and jq",
)
class BackslashEscapeHookDecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        self.git("init", "-q")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def checkout_unborn_branch(self, branch: str) -> None:
        self.git("symbolic-ref", "HEAD", f"refs/heads/{branch}")

    def decision(self, hook: Path, command: str) -> str:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        result = subprocess.run(
            ["bash", str(hook)],
            cwd=self.repo,
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        if result.stdout == b"":
            return "allow"
        return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]

    def assert_decisions(self, branch: str, cases: tuple[tuple[Path, str, str], ...]) -> None:
        self.checkout_unborn_branch(branch)
        for hook, command, expected in cases:
            with self.subTest(branch=branch, hook=hook.name, command=command):
                self.assertEqual(self.decision(hook, command), expected)

    def test_group_with_escaped_backslash_in_dquote_on_feature_branch(self) -> None:
        self.assert_decisions(
            "feature",
            (
                (PUSH_HOOK, r'(echo "a\\"; git push origin master)', "deny"),
                (PUSH_HOOK, r'(echo "a\\"; git push origin feature)', "allow"),
                (COMMIT_HOOK, r'(echo "a\\"; git switch master; git commit)', "deny"),
                (COMMIT_HOOK, r'(echo "a\\"; git commit -m x)', "allow"),
                (PR_HOOK, r'(echo "a\\"; gh pr create --head master)', "deny"),
                (PR_HOOK, r'(echo "a\\"; gh pr create --head feature)', "allow"),
            ),
        )

    def test_group_with_escaped_backslash_in_dquote_on_default_branch(self) -> None:
        self.assert_decisions(
            "master",
            (
                (PUSH_HOOK, r'(echo "a\\"; git push)', "deny"),
                (COMMIT_HOOK, r'(echo "a\\"; git commit)', "deny"),
                (PR_HOOK, r'(echo "a\\"; gh pr create)', "deny"),
            ),
        )

    def test_group_with_escaped_paren_outside_quote(self) -> None:
        self.assert_decisions(
            "feature",
            ((PUSH_HOOK, r"(echo a\); git push origin master)", "deny"),),
        )
        self.assert_decisions(
            "master",
            (
                (COMMIT_HOOK, r"(echo a\); git commit)", "deny"),
                (PR_HOOK, r"(echo a\); gh pr create)", "deny"),
            ),
        )

    def test_substitution_after_escaped_squote_outside_quote_is_denied(self) -> None:
        self.assert_decisions(
            "feature",
            (
                (PUSH_HOOK, r"echo \' $(git push origin master) \'", "deny"),
                (COMMIT_HOOK, r"echo \' $(git commit -m x) \'", "deny"),
                (PR_HOOK, r"echo \' $(gh pr create --head master) \'", "deny"),
            ),
        )
        self.assert_decisions(
            "master",
            ((PUSH_HOOK, r"echo \' $(git push) \'", "deny"),),
        )

    def test_squoted_substitution_after_escaped_backslash_in_dquote_is_allowed(self) -> None:
        self.assert_decisions(
            "feature",
            (
                (PUSH_HOOK, r"""echo "a\\" '$(git push origin master)'""", "allow"),
                (COMMIT_HOOK, r"""echo "a\\" '$(git commit -m x)'""", "allow"),
                (PR_HOOK, r"""echo "a\\" '$(gh pr create --head master)'""", "allow"),
            ),
        )


if __name__ == "__main__":
    unittest.main()
