"""block-pre-push.sh の fail-closed push gate 契約テスト (issue #128 / #129)。

Phase A の公開 seam は Claude Code の PreToolUse hook 入出力です。jq が PATH に無い
環境でも、git push を含む payload は valid な deny JSON を返し、push と無関係な payload
は従来どおり空出力で通過する契約を固定します。内部の jq 検出方法や JSON の生成手段には
依存しません。
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
HOOK = (
    ROOT
    / "plugins"
    / "pre-push-review"
    / "hooks"
    / "scripts"
    / "block-pre-push.sh"
)


class BlockPrePushMissingJqTest(unittest.TestCase):
    def run_hook_without_jq(
        self, payload: dict[str, object]
    ) -> subprocess.CompletedProcess[bytes]:
        bash = shutil.which("bash")
        cat = shutil.which("cat")
        dirname = shutil.which("dirname")
        if bash is None or cat is None or dirname is None:
            self.skipTest("hook integration requires bash, cat, and dirname")

        with tempfile.TemporaryDirectory() as name:
            work = Path(name)
            shims = work / "bin"
            shims.mkdir()
            (shims / "cat").symlink_to(cat)
            (shims / "dirname").symlink_to(dirname)

            env = os.environ.copy()
            env["PATH"] = str(shims)
            return subprocess.run(
                [bash, str(HOOK)],
                input=json.dumps(payload).encode("utf-8"),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=work,
                env=env,
            )

    def test_git_push_is_denied_with_actionable_reason_without_jq(self) -> None:
        result = self.run_hook_without_jq(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git push origin feature"},
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        response = json.loads(result.stdout)
        output = response["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("jq", output["permissionDecisionReason"])
        self.assertIn("push gate", output["permissionDecisionReason"])

    def test_unrelated_command_is_allowed_without_jq(self) -> None:
        result = self.run_hook_without_jq(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "printf hello"},
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("git") and shutil.which("jq"),
    "hook integration requires bash, git, and jq",
)
class BlockPrePushBundledDeleteOptionTest(unittest.TestCase):
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

    def run_hook(self, command: str) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        return subprocess.run(
            ["bash", str(HOOK)],
            cwd=self.repo,
            input=json.dumps(payload).encode("utf-8"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def assert_allowed(self, command: str) -> None:
        result = self.run_hook(command)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_delete_force_cluster_is_allowed(self) -> None:
        self.assert_allowed("git push -df origin other-branch")

    def test_force_delete_cluster_is_allowed(self) -> None:
        self.assert_allowed("git push -fd origin other-branch")

    def test_force_without_delete_keeps_refspec_mismatch_deny(self) -> None:
        result = self.run_hook("git push -f origin other-branch")

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        response = json.loads(result.stdout)
        output = response["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("別ブランチ", output["permissionDecisionReason"])


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class BlockPrePushShapeFilterTest(unittest.TestCase):
    """Shape check は token-level の実 push だけを保守的 deny する。"""

    def run_hook(self, command: str) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        with tempfile.TemporaryDirectory() as name:
            return subprocess.run(
                ["bash", str(HOOK)],
                cwd=name,
                input=json.dumps(payload).encode("utf-8"),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

    def assert_wrapper_push_is_denied(self, command: str) -> None:
        result = self.run_hook(command)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        response = json.loads(result.stdout)
        output = response["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("シェルラッパー", output["permissionDecisionReason"])

    def test_script_path_containing_push_is_allowed(self) -> None:
        result = self.run_hook(
            "git status && "
            "bash ./scripts/push-deploy-notifications.sh --env prod"
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_token_level_env_wrapper_push_is_still_denied(self) -> None:
        self.assert_wrapper_push_is_denied(
            "git status && env git push origin feature"
        )

    def test_quoted_shell_wrapper_push_is_still_denied(self) -> None:
        self.assert_wrapper_push_is_denied(
            'git status && bash -c "git push origin feature"'
        )


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class BlockPrePushWorktreeDiagnosticsTest(unittest.TestCase):
    """Linked worktree の marker 保存先を deny 出力から確認できる契約。"""

    def git(self, cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def create_linked_worktree(self, temporary: Path) -> Path:
        origin = temporary / "origin.git"
        main = temporary / "main"
        linked = temporary / "linked"
        self.git(temporary, "init", "--bare", str(origin))
        self.git(temporary, "init", str(main))
        self.git(main, "config", "user.name", "Marketplace Test")
        self.git(main, "config", "user.email", "marketplace@example.invalid")
        (main / "example.txt").write_text("base\n", encoding="utf-8")
        self.git(main, "add", "example.txt")
        self.git(main, "commit", "-m", "base")
        self.git(main, "branch", "-M", "master")
        self.git(main, "remote", "add", "origin", str(origin))
        self.git(main, "push", "-u", "origin", "master")
        self.git(main, "remote", "set-head", "origin", "master")
        self.git(
            main,
            "worktree",
            "add",
            "-b",
            "feature/linked",
            str(linked),
            "master",
        )
        (linked / "example.txt").write_text("linked change\n", encoding="utf-8")
        self.git(linked, "add", "example.txt")
        self.git(linked, "commit", "-m", "linked change")
        return linked

    def test_deny_reason_names_linked_worktree_marker_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            linked = self.create_linked_worktree(Path(temporary_name))
            git_dir = subprocess.check_output(
                ["git", "rev-parse", "--absolute-git-dir"], cwd=linked
            ).decode().strip()
            payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "git push origin HEAD"},
            }
            result = subprocess.run(
                ["bash", str(HOOK)],
                cwd=linked,
                input=json.dumps(payload).encode("utf-8"),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr.decode())
            response = json.loads(result.stdout)
            reason = response["hookSpecificOutput"][
                "permissionDecisionReason"
            ]
            self.assertIn(f"marker storage: {git_dir}", reason)
            self.assertIn("git rev-parse --absolute-git-dir", reason)


if __name__ == "__main__":
    unittest.main()
