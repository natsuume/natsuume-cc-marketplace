from __future__ import annotations

import os
import pty
import select
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "plugins" / "codex-advisor" / "scripts" / "run-codex-advisor.sh"
SKILL = ROOT / "plugins" / "codex-advisor" / "skills" / "consult" / "SKILL.md"

DIRECT_CODEX_ARGS = [
    "exec",
    "--sandbox",
    "read-only",
    "--ephemeral",
    "--disable",
    "hooks",
    "--skip-git-repo-check",
    "--color",
    "never",
    "-c",
    'model_reasoning_effort="xhigh"',
    "-",
]


class CodexAdvisorAdapterTest(unittest.TestCase):
    @staticmethod
    def _write_all(fd: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])

    @staticmethod
    def _read_terminal_output(fd: int) -> bytes:
        output = b""
        while True:
            readable, _, _ = select.select([fd], [], [], 0)
            if not readable:
                return output
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                return output
            if not chunk:
                return output
            output += chunk

    @staticmethod
    def _wait_for_readiness(process: subprocess.Popen[bytes]) -> bytes:
        assert process.stderr is not None
        ready, _, _ = select.select([process.stderr], [], [], 5)
        if not ready:
            return b""
        return process.stderr.readline()

    def _assert_process_gone(self, pid: int, label: str) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        self.fail(f"{label} process {pid} survived watchdog cleanup")

    def test_skill_uses_separate_codex_stdin_channel(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        self.assertIn("### Claude Code host", skill)
        self.assertIn("### Codex host", skill)
        self.assertIn("--codex-session-stdin", skill)
        self.assertIn("write_stdin", skill)
        self.assertIn("EOT framing byte を 2 byte (`0x04 0x04`)", skill)
        self.assertIn("raw/noncanonical mode", skill)
        self.assertIn("ready for Codex session stdin", skill)
        self.assertIn("prompt file を作らない", skill)
        self.assertIn("--skip-git-repo-check", skill)
        self.assertIn("モデルは Codex 側の既定に委ねる", skill)
        self.assertIn("既定 600 秒", skill)
        self.assertIn("process group 全体", skill)
        self.assertIn("TERM", skill)
        self.assertIn("KILL", skill)
        self.assertIn("`wait`", skill)

    def test_codex_mode_requires_a_pty(self) -> None:
        result = subprocess.run(
            ["bash", str(WRAPPER), "--codex-session-stdin"],
            input=b"prompt",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn(b"PTY", result.stderr)

    def test_claude_file_stdin_still_uses_companion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            node_args = temp / "node-args"
            fake_node = fake_bin / "node"
            fake_node.write_text(
                "#!/bin/bash\n"
                "printf '%s\\n' \"$@\" > \"$NODE_ARGS_FILE\"\n"
                "cat\n",
                encoding="utf-8",
            )
            fake_node.chmod(0o755)
            fake_codex = fake_bin / "codex"
            fake_codex.write_text("#!/bin/bash\nexit 99\n", encoding="utf-8")
            fake_codex.chmod(0o755)

            home = temp / "home"
            home.mkdir()
            companion = (
                home
                / ".claude/plugins/cache/openai-codex/codex/9.9.9/scripts"
                / "codex-companion.mjs"
            )
            companion.parent.mkdir(parents=True)
            companion.write_text("// fake companion\n", encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "NODE_ARGS_FILE": str(node_args),
                }
            )

            prompt = b"claude file stdin path\n\n"
            result = subprocess.run(
                ["bash", str(WRAPPER)],
                input=prompt,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=temp,
                env=env,
                check=False,
                timeout=10,
            )

            self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
            # The historical Claude path uses command substitution, which strips
            # trailing newlines before handing the prompt to the companion.
            self.assertEqual(prompt.rstrip(b"\n"), result.stdout)
            self.assertEqual(
                [str(companion), "task", "--effort", "xhigh"],
                node_args.read_text(encoding="utf-8").splitlines(),
            )
            self.assertIn(b"codex companion", result.stderr)

    def test_pty_round_trip_and_direct_codex_safety_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            args_file = temp / "codex-args"
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                "#!/bin/bash\n"
                "printf '%s\\n' \"$@\" > \"$CODEX_ARGS_FILE\"\n"
                "cat\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            fake_node = fake_bin / "node"
            fake_node.write_text("#!/bin/bash\nexit 99\n", encoding="utf-8")
            fake_node.chmod(0o755)

            master_fd, slave_fd = pty.openpty()
            env = os.environ.copy()
            env["HOME"] = str(temp / "home")
            env["PATH"] = f"{fake_bin}:/usr/bin:/bin"
            env["CODEX_ARGS_FILE"] = str(args_file)
            Path(env["HOME"]).mkdir()
            # Codex host mode must ignore an accidentally installed Claude
            # companion and retain the current Codex CLI/provider context.
            companion = (
                Path(env["HOME"])
                / ".claude/plugins/cache/openai-codex/codex/9.9.9/scripts"
                / "codex-companion.mjs"
            )
            companion.parent.mkdir(parents=True)
            companion.write_text("// must not run from a Codex host\n", encoding="utf-8")
            process = subprocess.Popen(
                ["bash", str(WRAPPER), "--codex-session-stdin"],
                stdin=slave_fd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=temp,  # Deliberately outside a git repository.
                env=env,
                close_fds=True,
            )
            os.close(slave_fd)
            try:
                readiness = self._wait_for_readiness(process)
                self.assertIn(b"ready for Codex session stdin", readiness)

                # The first line is larger than common MAX_CANON limits and CR
                # bytes must not be rewritten to LF by the PTY input discipline.
                prompt = b"<task>" + (b"x" * 9000) + b"\rsegment\r\nend</task>\n"
                self._write_all(master_fd, prompt + b"\x04\x04")
                stdout, stderr = process.communicate(timeout=10)
                terminal_echo = self._read_terminal_output(master_fd)

                self.assertEqual(0, process.returncode, stderr.decode(errors="replace"))
                self.assertEqual(prompt, stdout)
                self.assertNotIn(b"x" * 256, terminal_echo)
                self.assertEqual(
                    DIRECT_CODEX_ARGS,
                    args_file.read_text(encoding="utf-8").splitlines(),
                )
                self.assertIn(b"direct codex exec", stderr)
            finally:
                os.close(master_fd)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

    def test_codex_timeout_terminates_and_reaps_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            pid_file = temp / "codex-pid"
            descendant_pid_file = temp / "codex-descendant-pid"
            term_file = temp / "codex-term"
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                "#!/bin/bash\n"
                "printf '%s\\n' \"$$\" > \"$CODEX_PID_FILE\"\n"
                "trap 'printf received > \"$CODEX_TERM_FILE\"; while :; do :; done' TERM\n"
                "(trap '' TERM; while :; do :; done) &\n"
                "descendant_pid=$!\n"
                "printf '%s\\n' \"$descendant_pid\" > \"$CODEX_DESCENDANT_PID_FILE\"\n"
                "wait \"$descendant_pid\"\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)

            master_fd, slave_fd = pty.openpty()
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(temp / "home"),
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "CODEX_PID_FILE": str(pid_file),
                    "CODEX_DESCENDANT_PID_FILE": str(descendant_pid_file),
                    "CODEX_TERM_FILE": str(term_file),
                    "CODEX_ADVISOR_TIMEOUT_SECONDS": "1",
                    "CODEX_ADVISOR_KILL_GRACE_SECONDS": "1",
                }
            )
            Path(env["HOME"]).mkdir()
            process = subprocess.Popen(
                ["bash", str(WRAPPER), "--codex-session-stdin"],
                stdin=slave_fd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=temp,
                env=env,
                close_fds=True,
            )
            os.close(slave_fd)
            nested_pid: int | None = None
            descendant_pid: int | None = None
            started = time.monotonic()
            try:
                readiness = self._wait_for_readiness(process)
                self.assertIn(b"ready for Codex session stdin", readiness)
                self._write_all(master_fd, b"timeout prompt\x04\x04")
                _, stderr = process.communicate(timeout=10)
                elapsed = time.monotonic() - started

                self.assertNotEqual(0, process.returncode)
                self.assertLess(elapsed, 8)
                self.assertIn(b"advisor timed out after 1 seconds", stderr)
                self.assertTrue(term_file.exists(), "watchdog did not send TERM")
                nested_pid = int(pid_file.read_text(encoding="utf-8").strip())
                descendant_pid = int(
                    descendant_pid_file.read_text(encoding="utf-8").strip()
                )
                self._assert_process_gone(nested_pid, "advisor")
                self._assert_process_gone(descendant_pid, "advisor descendant")
            finally:
                os.close(master_fd)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if nested_pid is None and pid_file.exists():
                    nested_pid = int(pid_file.read_text(encoding="utf-8").strip())
                if descendant_pid is None and descendant_pid_file.exists():
                    descendant_pid = int(
                        descendant_pid_file.read_text(encoding="utf-8").strip()
                    )
                for residual_pid in (descendant_pid, nested_pid):
                    if residual_pid is not None:
                        try:
                            os.kill(residual_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass


if __name__ == "__main__":
    unittest.main()
