"""block-pre-push.sh の fail-closed push gate 契約テスト (issue #128)。

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


if __name__ == "__main__":
    unittest.main()
