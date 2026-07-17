"""git-guardrails の target-mismatch 判定契約テスト (issue #140)。"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK_DIR = ROOT / "plugins" / "git-guardrails" / "hooks" / "scripts"
COMMIT_HOOK = HOOK_DIR / "block-default-branch-commit.sh"
PUSH_HOOK = HOOK_DIR / "block-default-branch-push.sh"


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("git") and shutil.which("jq"),
    "hook integration requires bash, git, and jq",
)
class GitGuardrailsTargetMismatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        self.git("init")
        self.git("config", "user.name", "Marketplace Test")
        self.git("config", "user.email", "marketplace@example.invalid")
        (self.repo / "file.txt").write_text("base\n", encoding="utf-8")
        self.git("add", "file.txt")
        self.git("commit", "-m", "base")
        self.git("switch", "-c", "feature")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def run_hook(
        self, hook: Path, command: str
    ) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        return subprocess.run(
            ["bash", str(hook)],
            cwd=self.repo,
            input=json.dumps(payload).encode("utf-8"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def assert_allowed(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def assert_target_mismatch_denied(
        self, result: subprocess.CompletedProcess[bytes]
    ) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        response = json.loads(result.stdout)
        output = response["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("git -C", output["permissionDecisionReason"])

    def test_blame_copy_detection_before_commit_is_allowed(self) -> None:
        result = self.run_hook(
            COMMIT_HOOK,
            'git blame -C file.txt && git commit -am "review notes"',
        )

        self.assert_allowed(result)

    def test_log_copy_detection_before_push_is_allowed(self) -> None:
        result = self.run_hook(
            PUSH_HOOK,
            "git log --oneline -C && git push origin feature",
        )

        self.assert_allowed(result)

    def test_global_c_before_commit_remains_denied(self) -> None:
        result = self.run_hook(
            COMMIT_HOOK,
            'git -C /other status && git commit -m "change"',
        )

        self.assert_target_mismatch_denied(result)

    def test_global_c_after_config_option_remains_denied(self) -> None:
        result = self.run_hook(
            COMMIT_HOOK,
            'git -c user.name=test -C /other status && git commit -m "change"',
        )

        self.assert_target_mismatch_denied(result)

    def test_quoted_global_c_remains_denied(self) -> None:
        result = self.run_hook(
            COMMIT_HOOK,
            'git "-C" "/other" status && git commit -m "change"',
        )

        self.assert_target_mismatch_denied(result)

    def test_local_c_after_config_option_is_allowed(self) -> None:
        result = self.run_hook(
            COMMIT_HOOK,
            'git -c user.name=test blame -C file.txt && git commit -m "change"',
        )

        self.assert_allowed(result)

    def test_global_git_dir_before_commit_remains_denied(self) -> None:
        result = self.run_hook(
            COMMIT_HOOK,
            'git --git-dir=/other/.git status && git commit -m "change"',
        )

        self.assert_target_mismatch_denied(result)

    def test_path_qualified_git_with_global_c_remains_denied(self) -> None:
        result = self.run_hook(
            COMMIT_HOOK,
            '/usr/bin/git -C /other status && /usr/bin/git commit -m "change"',
        )

        self.assert_target_mismatch_denied(result)

    def test_quoted_path_qualified_git_with_global_c_remains_denied(self) -> None:
        result = self.run_hook(
            COMMIT_HOOK,
            '"/usr/bin/git" -C /other status && git commit -m "change"',
        )

        self.assert_target_mismatch_denied(result)

    def test_concatenated_global_git_dir_remains_denied(self) -> None:
        result = self.run_hook(
            COMMIT_HOOK,
            "git --git-'dir'=/other/.git status && git commit -m change",
        )

        self.assert_target_mismatch_denied(result)

    def test_escaped_global_git_dir_remains_denied(self) -> None:
        result = self.run_hook(
            COMMIT_HOOK,
            r"git --git-dir\=/other/.git status && git commit -m change",
        )

        self.assert_target_mismatch_denied(result)

    def test_path_qualified_git_with_local_c_is_allowed(self) -> None:
        result = self.run_hook(
            COMMIT_HOOK,
            '/usr/bin/git blame -C file.txt && /usr/bin/git commit -m "change"',
        )

        self.assert_allowed(result)


if __name__ == "__main__":
    unittest.main()
