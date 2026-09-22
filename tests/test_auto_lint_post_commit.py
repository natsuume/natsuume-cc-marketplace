"""auto-lint-check の post-commit-lint hook の配線と I/O 契約。

post-commit-lint は `git commit` を含む Bash 実行の直後に現 HEAD を再 lint する
safety net であり、Bash が非 0 exit で終わった実行 (`PostToolUseFailure`) でも同じ
lint を行う。そのため hooks.json は `PostToolUse` と `PostToolUseFailure` の両方に
matcher `Bash` で同じ script を登録し、script は event ごとに出力形を使い分ける:

- `PostToolUse`: `{"decision": "block", "reason": ...}`
- `PostToolUseFailure`: `{"hookSpecificOutput": {"hookEventName": "PostToolUseFailure",
  "additionalContext": ...}}` (`decision` キーは持たない)

`PostToolUseFailure` では、`error` の先頭行が `Exit code <10 進整数>` の完全一致行で
ある場合だけ「コマンドが実際に走って非 0 で終了した」と判断して lint に進む。
プロセス起動失敗等で `Exit code N` 行が無い入力、およびユーザ中断
(`is_interrupt: true`) では何も出力しない。

lint 実行の検証には ruff を使う (eslint は使わない)。fixture の一時 git repo は
python の subprocess 経由で作り、hook には TMPDIR を一時ディレクトリへ向けた env を
渡して利用者の state に触れないようにする。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "auto-lint-check"
HOOK = PLUGIN / "hooks" / "scripts" / "post-commit-lint.sh"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
README = PLUGIN / "README.md"

SCRIPTS_PLACEHOLDER_PREFIX = "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/"
POST_COMMIT_LINT_COMMAND = f"{SCRIPTS_PLACEHOLDER_PREFIX}post-commit-lint.sh"
BLOCK_COMMIT_LINT_COMMAND = f"{SCRIPTS_PLACEHOLDER_PREFIX}block-commit-lint.sh"
BLOCK_IGNORE_COMMENT_COMMAND = f"{SCRIPTS_PLACEHOLDER_PREFIX}block-ignore-lint-comment.sh"
CODE_FORMAT_COMMAND = f"{SCRIPTS_PLACEHOLDER_PREFIX}code-format.sh"
EDIT_MATCHER = "Write|Edit|MultiEdit"
BASH_MATCHER = "Bash"

FAILURE_EVENT = "PostToolUseFailure"

# fixture の commit コマンド (parse-commit-command.py が実 commit invocation と判定する形)。
COMMIT_COMMAND = "git commit -m x"
# `git` と `commit` の両語を含む (hook の入力前置フィルタを通過する) が、実 commit
# invocation ではないコマンド。commit 判定 (parse-commit-command.py) の分岐を実際に通す。
NON_COMMIT_COMMAND = "git log --grep=commit --oneline"

# `error` の先頭行の形。`Exit code <10 進整数>` の完全一致行だけが「コマンドが走って
# 非 0 で終了した」ことを表す。
ERROR_WITH_EXIT_CODE = "Exit code 1\nfatal: some stderr"
# プロセス起動自体が失敗した場合 (先頭行が `Exit code N` ではない)。
ERROR_WITHOUT_EXIT_CODE = "spawn failed"
# `Exit code` の後に別の文字列が続く行は `Exit code N` に該当しない。
ERROR_WITH_TRAILING_COLON = "Exit code: 1\nfatal: some stderr"
# ユーザ中断 (Esc/Ctrl+C) の `error`。
INTERRUPT_ERROR = "Exit code 130"

# ruff が fixture の `.py` に対して報告する規則 ID。
EXPECTED_RUFF_RULE = "F401"

PYPROJECT_WITH_RUFF_CONFIG = """[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["F"]
"""
# 未使用 import (F401) を 1 件だけ含む lint 対象ファイル。
PYTHON_FILE_WITH_LINT_ERROR = "import os\n\n\ndef main():\n    return 1\n"


def load_hooks() -> dict[str, list[dict[str, object]]]:
    return json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]


def command_entries(
    hooks: dict[str, list[dict[str, object]]], event: str
) -> list[tuple[str, str, tuple[str, ...]]]:
    """(matcher, command, args) の一覧。matcher を持たない group は `""` で表す。"""
    entries: list[tuple[str, str, tuple[str, ...]]] = []
    for group in hooks.get(event, []):
        matcher = str(group.get("matcher", ""))
        for hook in group["hooks"]:
            if hook.get("type") != "command":
                continue
            entries.append(
                (matcher, hook["command"], tuple(hook.get("args", ())))
            )
    return entries


def readme_text() -> str:
    return README.read_text(encoding="utf-8")


def readme_section(heading_keyword: str) -> str:
    """`#### ` 見出しのうち `heading_keyword` を含む節の本文を返す。"""
    lines = readme_text().splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("#### ") or heading_keyword not in line:
            continue
        body: list[str] = []
        for following in lines[index + 1 :]:
            if following.startswith("#"):
                break
            body.append(following)
        return "\n".join(body)
    raise AssertionError(f"{README}: '{heading_keyword}' の #### 節が無い")


class PostCommitLintHooksContractTest(unittest.TestCase):
    """hooks.json が post-commit-lint を PostToolUse と PostToolUseFailure に登録する。"""

    def test_post_tool_use_failure_registers_post_commit_lint_for_bash(self) -> None:
        entries = command_entries(load_hooks(), FAILURE_EVENT)
        self.assertIn(
            (BASH_MATCHER, POST_COMMIT_LINT_COMMAND, ()),
            entries,
            f"{FAILURE_EVENT} に matcher {BASH_MATCHER!r} の "
            f"{POST_COMMIT_LINT_COMMAND} (args: []) を登録する: {entries!r}",
        )

    def test_pre_tool_use_and_post_tool_use_entries_are_unchanged(self) -> None:
        hooks = load_hooks()
        self.assertEqual(
            [
                (EDIT_MATCHER, BLOCK_IGNORE_COMMENT_COMMAND, ()),
                (BASH_MATCHER, BLOCK_COMMIT_LINT_COMMAND, ()),
            ],
            command_entries(hooks, "PreToolUse"),
        )
        self.assertEqual(
            [
                (EDIT_MATCHER, CODE_FORMAT_COMMAND, ()),
                (BASH_MATCHER, POST_COMMIT_LINT_COMMAND, ()),
            ],
            command_entries(hooks, "PostToolUse"),
        )


@unittest.skipUnless(shutil.which("git"), "post-commit-lint の実行には git が要る")
@unittest.skipUnless(shutil.which("jq"), "post-commit-lint の実行には jq が要る")
@unittest.skipUnless(shutil.which("ruff"), "lint 実行の検証には ruff が要る")
class PostCommitLintRunTest(unittest.TestCase):
    """post-commit-lint.sh に stdin JSON を与えたときの分岐と出力形。"""

    def setUp(self) -> None:
        if not HOOK.is_file():
            self.fail(f"post-commit-lint script is missing: {HOOK}")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)

        self.repo = base / "repo"
        self.repo.mkdir()
        temporary_tmpdir = base / "tmp"
        temporary_tmpdir.mkdir()
        empty_git_config = base / "gitconfig"
        empty_git_config.write_text("", encoding="utf-8")

        # TMPDIR は hook の作業ファイルを一時ディレクトリへ閉じ込めるために差し替える。
        # HOME は継承する (PATH 上の linter が HOME 依存の version manager 経由で
        # 解決される環境があり、差し替えると linter 自体が起動できなくなる)。
        self.env = os.environ.copy()
        self.env["TMPDIR"] = str(temporary_tmpdir)
        self.env["GIT_CONFIG_GLOBAL"] = str(empty_git_config)
        self.env["GIT_CONFIG_SYSTEM"] = str(empty_git_config)

        self.head_sha = self.build_repository_with_lint_error()

    # -- fixture ------------------------------------------------------------

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            text=True,
            capture_output=True,
            env=self.env,
        )
        return result.stdout.strip()

    def build_repository_with_lint_error(self) -> str:
        """ruff config と lint エラーを含む `.py` を HEAD に持つ git repo を作る。

        戻り値は HEAD の短縮 SHA (hook の出力が言及する)。
        """
        self.git("init", "-b", "main")
        (self.repo / "pyproject.toml").write_text(
            PYPROJECT_WITH_RUFF_CONFIG, encoding="utf-8"
        )
        (self.repo / "sample.py").write_text(
            PYTHON_FILE_WITH_LINT_ERROR, encoding="utf-8"
        )
        self.git("add", "pyproject.toml", "sample.py")
        self.git(
            "-c",
            "user.name=auto-lint-check test",
            "-c",
            "user.email=auto-lint-check@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "fixture commit with a lint error",
        )
        # git は symlink を解決した実パスを返すため (macOS の一時ディレクトリ等)、
        # 比較側も resolve して揃える。
        self.assertEqual(
            str(self.repo.resolve()),
            self.git("rev-parse", "--show-toplevel"),
            "fixture repo",
        )
        return self.git("rev-parse", "--short", "HEAD")

    # -- hook 実行 ----------------------------------------------------------

    def run_hook(self, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=str(self.repo),
            env=self.env,
            timeout=180,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return result

    def post_tool_use_payload(
        self, *, command: str = COMMIT_COMMAND
    ) -> dict[str, object]:
        return {
            "hook_event_name": "PostToolUse",
            "session_id": "session-post-commit-lint",
            "cwd": str(self.repo),
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"exit_code": 0},
            "tool_use_id": "toolu_test",
        }

    def failure_payload(
        self,
        *,
        command: str = COMMIT_COMMAND,
        error: str = ERROR_WITH_EXIT_CODE,
        tool_name: str = "Bash",
        is_interrupt: bool | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "hook_event_name": FAILURE_EVENT,
            "session_id": "session-post-commit-lint",
            "cwd": str(self.repo),
            "tool_name": tool_name,
            "tool_input": {"command": command},
            "error": error,
            "tool_use_id": "toolu_test",
            "duration_ms": 12,
        }
        if is_interrupt is not None:
            payload["is_interrupt"] = is_interrupt
        return payload

    # -- assertions ---------------------------------------------------------

    def assert_silent(self, payload: dict[str, object]) -> None:
        result = self.run_hook(payload)
        self.assertEqual("", result.stdout.strip(), result.stdout)

    def assert_reports_head_lint_error(self, text: str) -> None:
        self.assertIn(EXPECTED_RUFF_RULE, text)
        self.assertIn(self.head_sha, text)

    # -- PostToolUse (既存契約) --------------------------------------------

    def test_post_tool_use_reports_head_lint_errors_as_a_block_decision(self) -> None:
        result = self.run_hook(self.post_tool_use_payload())
        response = json.loads(result.stdout)
        self.assertEqual("block", response["decision"])
        self.assert_reports_head_lint_error(response["reason"])

    # -- PostToolUseFailure -------------------------------------------------

    def test_post_tool_use_failure_reports_head_lint_errors_as_additional_context(
        self,
    ) -> None:
        result = self.run_hook(self.failure_payload())
        response = json.loads(result.stdout)
        self.assertNotIn(
            "decision",
            response,
            f"{FAILURE_EVENT} の出力は additionalContext のみ: {response!r}",
        )
        hook_output = response["hookSpecificOutput"]
        self.assertEqual(FAILURE_EVENT, hook_output["hookEventName"])
        self.assert_reports_head_lint_error(hook_output["additionalContext"])

    def test_failure_without_an_exit_code_line_is_silent(self) -> None:
        """プロセス起動失敗 (`Exit code N` 行が無い) では commit 判定に入らない。"""
        self.assert_silent(self.failure_payload(error=ERROR_WITHOUT_EXIT_CODE))

    def test_failure_with_text_after_exit_code_is_silent(self) -> None:
        """`Exit code` の後に別の文字列が続く行は `Exit code N` に該当しない。"""
        self.assert_silent(self.failure_payload(error=ERROR_WITH_TRAILING_COLON))

    def test_interrupted_failure_is_silent(self) -> None:
        self.assert_silent(
            self.failure_payload(error=INTERRUPT_ERROR, is_interrupt=True)
        )

    def test_failure_of_a_non_bash_tool_is_silent(self) -> None:
        self.assert_silent(self.failure_payload(tool_name="Write"))

    def test_failure_without_a_commit_invocation_is_silent(self) -> None:
        self.assert_silent(self.failure_payload(command=NON_COMMIT_COMMAND))


class PostCommitLintDocumentationTest(unittest.TestCase):
    """README が post-commit-lint の配送イベントとして PostToolUseFailure を書く。"""

    def test_overview_table_row_lists_the_failure_event(self) -> None:
        rows = [
            line
            for line in readme_text().splitlines()
            if line.startswith("|") and "`post-commit-lint`" in line
        ]
        self.assertTrue(rows, f"{README}: 概要テーブルに post-commit-lint の行が無い")
        for row in rows:
            with self.subTest(row=row):
                self.assertIn(FAILURE_EVENT, row)

    def test_hook_section_event_line_lists_the_failure_event(self) -> None:
        section = readme_section("post-commit-lint")
        event_lines = [
            line for line in section.splitlines() if line.startswith("**イベント**")
        ]
        self.assertTrue(
            event_lines, f"{README}: post-commit-lint 節に **イベント** 行が無い"
        )
        for line in event_lines:
            with self.subTest(line=line):
                self.assertIn(FAILURE_EVENT, line)


if __name__ == "__main__":
    unittest.main()
