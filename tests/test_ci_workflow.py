"""CI workflow (.github/workflows/*.yml) の文言契約を検証する。

CI の Python には PyYAML が無いため、workflow は文字列と正規表現で検査する。
step は「`- name:` 行から、同じインデントの次の `- name:` 行の直前まで」を 1 step
として切り出す簡易パーサで扱う。

検証する契約:

- `env.CLAUDE_CODE_VERSION` は `"X.Y.Z"` 形式の固定値で、2.1.278 以上である
- marketplace root の `claude plugin validate . --strict` を実行する
- `plugins/*/` の各ディレクトリに `claude plugin validate "$dir" --strict` を実行する
  ループがあり、その step は `set -euo pipefail` で失敗を伝播し `|| true` で握りつぶさない
- `scripts/check_claude_code_pin_age.py` を実行する warning step が ubuntu-latest のみで
  動き、`continue-on-error: true` で job を失敗させない
- unit test step・`bash -n` step・ShellCheck step は現行の内容を保つ
- sync-shared-libs.yml のコメントは現行の plugins reference URL を参照する
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW_PATH = ROOT / ".github/workflows/ci.yml"
SYNC_SHARED_LIBS_WORKFLOW_PATH = ROOT / ".github/workflows/sync-shared-libs.yml"

MINIMUM_CLAUDE_CODE_VERSION = (2, 1, 278)
PINNED_VERSION_RE = re.compile(
    r'^\s*CLAUDE_CODE_VERSION:\s*"(\d+)\.(\d+)\.(\d+)"\s*$', re.MULTILINE
)
STEP_NAME_LINE_RE = re.compile(r"^(\s*)- name:")
MARKETPLACE_ROOT_VALIDATE_COMMAND = "claude plugin validate . --strict"
PLUGIN_DIRECTORY_VALIDATE_COMMAND = 'claude plugin validate "$dir" --strict'
PLUGIN_DIRECTORY_LOOP_RE = re.compile(r"\bfor\s+dir\s+in\s+plugins/\*/")
PIN_AGE_SCRIPT_PATH = "scripts/check_claude_code_pin_age.py"
CONTINUE_ON_ERROR_TRUE_RE = re.compile(
    r"^\s*continue-on-error:\s*true\s*$", re.MULTILINE
)
UNIT_TEST_RUN_LINE_RE = re.compile(
    r"^\s*run:\s*python3 -m unittest discover -s tests -p 'test_\*\.py'\s*$",
    re.MULTILINE,
)
CURRENT_PLUGINS_REFERENCE_URL = "https://code.claude.com/docs/en/plugins-reference"
RETIRED_PLUGINS_REFERENCE_URL_FRAGMENT = (
    "docs.claude.com/en/docs/claude-code/plugins-reference"
)


def split_steps(workflow_text: str) -> list[str]:
    """workflow 本文を `- name:` 行単位の step テキストに分割する。

    `- name:` を持たない step (`- uses:` のみ等) は直前の step に含まれうるが、
    本テストは `- name:` を持つ step の内容だけを検査するため問題にしない。
    """

    lines = workflow_text.splitlines()
    steps: list[str] = []
    current: list[str] = []
    current_indent: str | None = None
    for line in lines:
        match = STEP_NAME_LINE_RE.match(line)
        if match is not None and (
            current_indent is None or match.group(1) == current_indent
        ):
            if current:
                steps.append("\n".join(current))
            current = [line]
            current_indent = match.group(1)
            continue
        if current_indent is not None:
            current.append(line)
    if current:
        steps.append("\n".join(current))
    return steps


def find_steps_containing(workflow_text: str, needle: str) -> list[str]:
    return [step for step in split_steps(workflow_text) if needle in step]


def step_if_condition(step_text: str) -> str | None:
    match = re.search(r"^\s*if:\s*(.+)$", step_text, re.MULTILINE)
    return match.group(1) if match else None


class CiWorkflowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.ci_text = CI_WORKFLOW_PATH.read_text(encoding="utf-8")


class ClaudeCodeVersionPinTest(CiWorkflowTestCase):
    def test_claude_code_version_is_a_fixed_recent_release(self) -> None:
        match = PINNED_VERSION_RE.search(self.ci_text)
        self.assertIsNotNone(
            match,
            'env.CLAUDE_CODE_VERSION must be a fixed "X.Y.Z" string (not latest etc.)',
        )
        assert match is not None
        pinned = tuple(int(part) for part in match.groups())
        self.assertGreaterEqual(
            pinned,
            MINIMUM_CLAUDE_CODE_VERSION,
            f"CLAUDE_CODE_VERSION {'.'.join(map(str, pinned))} is older than "
            f"{'.'.join(map(str, MINIMUM_CLAUDE_CODE_VERSION))}",
        )


class PluginValidateStepTest(CiWorkflowTestCase):
    def test_marketplace_root_is_validated_strictly(self) -> None:
        self.assertIn(MARKETPLACE_ROOT_VALIDATE_COMMAND, self.ci_text)

    def test_each_plugin_directory_is_validated_in_a_fail_fast_loop(self) -> None:
        loop_steps = find_steps_containing(
            self.ci_text, PLUGIN_DIRECTORY_VALIDATE_COMMAND
        )
        self.assertEqual(
            len(loop_steps),
            1,
            f"exactly one step must run `{PLUGIN_DIRECTORY_VALIDATE_COMMAND}`",
        )
        loop_step = loop_steps[0]
        self.assertRegex(
            loop_step,
            PLUGIN_DIRECTORY_LOOP_RE,
            "the step must iterate `for dir in plugins/*/`",
        )
        self.assertIn("set -euo pipefail", loop_step)
        self.assertNotIn("|| true", loop_step)
        self.assertNotRegex(loop_step, CONTINUE_ON_ERROR_TRUE_RE)


class PinAgeWarningStepTest(CiWorkflowTestCase):
    def test_pin_age_warning_step_runs_on_ubuntu_without_failing_the_job(self) -> None:
        warning_steps = find_steps_containing(self.ci_text, PIN_AGE_SCRIPT_PATH)
        self.assertEqual(
            len(warning_steps),
            1,
            f"exactly one step must run {PIN_AGE_SCRIPT_PATH}",
        )
        warning_step = warning_steps[0]
        self.assertRegex(warning_step, CONTINUE_ON_ERROR_TRUE_RE)
        condition = step_if_condition(warning_step)
        self.assertIsNotNone(condition, "the warning step must have an `if:` condition")
        assert condition is not None
        self.assertIn("ubuntu-latest", condition)
        self.assertIn(
            "CLAUDE_CODE_VERSION",
            warning_step,
            "the warning step must pass the pinned CLAUDE_CODE_VERSION to the script",
        )


class UnchangedStepsTest(CiWorkflowTestCase):
    def test_unit_test_step_runs_unittest_discover(self) -> None:
        self.assertRegex(self.ci_text, UNIT_TEST_RUN_LINE_RE)

    def test_bash_syntax_check_step_is_kept(self) -> None:
        self.assertEqual(len(find_steps_containing(self.ci_text, 'bash -n "$file"')), 1)

    def test_shellcheck_step_is_kept(self) -> None:
        self.assertEqual(len(find_steps_containing(self.ci_text, "shellcheck \\")), 1)


class SyncSharedLibsWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sync_text = SYNC_SHARED_LIBS_WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_references_current_plugins_reference_url(self) -> None:
        self.assertIn(CURRENT_PLUGINS_REFERENCE_URL, self.sync_text)

    def test_does_not_reference_retired_plugins_reference_url(self) -> None:
        self.assertNotIn(RETIRED_PLUGINS_REFERENCE_URL_FRAGMENT, self.sync_text)


if __name__ == "__main__":
    unittest.main()
