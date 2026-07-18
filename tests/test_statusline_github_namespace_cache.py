from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUSLINE_LIB = ROOT / "plugins" / "natsuume-statusline" / "statusline" / "lib.sh"


class GithubNamespaceCacheTest(unittest.TestCase):
    def make_environment(
        self, root: Path, *, sleep_seconds: int
    ) -> tuple[dict[str, str], Path]:
        home = root / "home"
        bin_dir = root / "bin"
        home.mkdir()
        bin_dir.mkdir()
        call_log = root / "gh-calls.log"
        fake_gh = bin_dir / "gh"
        fake_gh.write_text(
            """#!/bin/sh
printf '%s\n' "$*" >> "$GH_CALL_LOG"
sleep "$GH_FAKE_SLEEP_SECONDS"
case "$2" in
  user) printf '%s\n' 'test-user' ;;
  user/orgs) printf '%s\n' 'test-org' ;;
esac
""",
            encoding="utf-8",
        )
        fake_gh.chmod(0o755)

        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        env["GH_CALL_LOG"] = str(call_log)
        env["GH_FAKE_SLEEP_SECONDS"] = str(sleep_seconds)
        return env, call_log

    def namespace_process(self, env: dict[str, str]) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [
                "bash",
                "-c",
                'source "$1"; owned_github_namespaces',
                "statusline-test",
                str(STATUSLINE_LIB),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )

    def test_slow_github_calls_have_a_rendering_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env, _ = self.make_environment(Path(tmp), sleep_seconds=3)
            started = time.monotonic()

            process = self.namespace_process(env)
            try:
                stdout, stderr = process.communicate(timeout=4.5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                self.fail("namespace refresh blocked statusline rendering past its deadline")

            elapsed = time.monotonic() - started
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertEqual(stdout, b"")
            self.assertLess(elapsed, 4.0)

    def test_concurrent_cache_misses_share_one_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env, call_log = self.make_environment(Path(tmp), sleep_seconds=1)

            first = self.namespace_process(env)
            second = self.namespace_process(env)
            first_stdout, first_stderr = first.communicate(timeout=6)
            second_stdout, second_stderr = second.communicate(timeout=6)

            self.assertEqual(first.returncode, 0, first_stderr.decode())
            self.assertEqual(second.returncode, 0, second_stderr.decode())
            self.assertCountEqual(
                (first_stdout + second_stdout).decode().splitlines(),
                ["test-user", "test-org"],
            )
            calls = call_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 2)
            self.assertCountEqual(
                calls,
                ["api user --jq .login", "api user/orgs --jq .[].login"],
            )


if __name__ == "__main__":
    unittest.main()
