from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "session-handoff"
INJECT = PLUGIN / "hooks" / "scripts" / "inject-pending-handoff.sh"
HOOKS = PLUGIN / "hooks" / "hooks.json"


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
@unittest.skipUnless(shutil.which("git"), "hook integration requires git")
class SessionHandoffPendingConsumerTest(unittest.TestCase):
    """inject-pending-handoff.sh (Claude 側 consumer) の検証。

    save-codex-handoff.sh・codex-summary-opt-in・setup-codex-summary 等の Codex
    producer 側と、pending-codex-* の同一 session_id マッチング分岐 (Phase B で
    inject-pending-handoff.sh から削除される) は対象外。ここでは producer を問わない
    汎用 pending ファイルの consumer 挙動のみを検証する。
    """

    def git(self, cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def make_repo(self, root: Path) -> Path:
        repo = root / "repo"
        self.git(root, "init", str(repo))
        return repo

    def run_hook(
        self,
        script: Path,
        payload: object,
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(script)],
            cwd=cwd,
            env=env,
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_resume_leaves_generic_pending_for_clear(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            repo = self.make_repo(temporary)
            handoff_dir = repo / ".git" / "session-handoff"
            handoff_dir.mkdir(mode=0o700)
            pending = handoff_dir / "pending-fixture.md"
            pending.write_text("# fixture handoff\n", encoding="utf-8")
            env = os.environ.copy()

            compact = self.run_hook(
                INJECT,
                {
                    "hook_event_name": "SessionStart",
                    "source": "compact",
                    "session_id": "session",
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )
            self.assertEqual(compact.returncode, 0)
            self.assertEqual(compact.stdout, b"")
            self.assertTrue(pending.exists())

            resume = self.run_hook(
                INJECT,
                {
                    "hook_event_name": "SessionStart",
                    "source": "resume",
                    "session_id": "session",
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )
            self.assertEqual(resume.returncode, 0, resume.stderr.decode())
            self.assertEqual(resume.stdout, b"")
            self.assertTrue(pending.exists())

            clear = self.run_hook(
                INJECT,
                {
                    "hook_event_name": "SessionStart",
                    "source": "clear",
                    "session_id": "session",
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )
            self.assertEqual(clear.returncode, 0, clear.stderr.decode())
            self.assertIn("fixture handoff", clear.stdout.decode("utf-8"))
            self.assertFalse(pending.exists())

    def test_wrong_event_does_not_claim_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            repo = self.make_repo(temporary)
            handoff_dir = repo / ".git" / "session-handoff"
            handoff_dir.mkdir(mode=0o700)
            pending = handoff_dir / "pending-fixture.md"
            pending.write_text("# fixture\n", encoding="utf-8")
            env = os.environ.copy()

            result = self.run_hook(
                INJECT,
                {
                    "hook_event_name": "PreCompact",
                    "source": "clear",
                    "session_id": "session",
                    "cwd": str(repo),
                },
                cwd=repo,
                env=env,
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertTrue(pending.exists())


class SessionHandoffHooksContractTest(unittest.TestCase):
    """hooks/hooks.json の Phase B 最終状態契約。

    別エージェントが並行して plugins/ 配下を編集中のため、実行タイミングによっては
    このテストが red になりうる (それが正しい状態であり、最終検証は親セッションが行う)。
    save-codex-handoff.sh の PreCompact entry が削除され、SessionStart の matcher が
    'clear|startup' に縮小され、inject-pending-handoff.sh の登録は残ることを検証する。
    """

    def test_precompact_entry_removed_and_session_start_matcher_narrowed(self) -> None:
        hooks = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"]

        self.assertNotIn("PreCompact", hooks)

        session_start_groups = hooks["SessionStart"]
        self.assertEqual(1, len(session_start_groups))
        self.assertEqual("clear|startup", session_start_groups[0]["matcher"])

        commands = [handler["command"] for handler in session_start_groups[0]["hooks"]]
        self.assertTrue(
            any(
                command.endswith("/hooks/scripts/inject-pending-handoff.sh")
                for command in commands
            ),
            commands,
        )


if __name__ == "__main__":
    unittest.main()
