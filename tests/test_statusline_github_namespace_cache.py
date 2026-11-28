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
        # 呼ばれた時点で refresh の lock があるか (held / free) を、呼び出しごとに 1 行記録する。
        fake_gh.write_text(
            """#!/bin/sh
printf '%s\n' "$*" >> "$GH_CALL_LOG"
if [ -d "$GH_LOCK_DIR" ]; then lock_state=held; else lock_state=free; fi
printf '%s\n' "$lock_state" >> "$GH_LOCK_STATE_LOG"
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
        env["GH_LOCK_DIR"] = str(
            home / ".claude" / "cache" / ".owned-github-namespaces.lock"
        )
        env["GH_LOCK_STATE_LOG"] = str(root / "gh-lock-states.log")
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
                self.fail(
                    "namespace refresh blocked statusline rendering past its deadline"
                )

            elapsed = time.monotonic() - started
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertEqual(stdout, b"")
            self.assertLess(elapsed, 4.0)

    # 並行した cache miss で 1 process だけが refresh することは、2 つの process の起動の
    # タイミングに頼らずに確かめる。refresh 中の process が持つ lock をあらかじめ作り、
    # lock がある間の process が gh を呼ばずに返すこと (下の 2 件) と、lock が無い process が
    # lock を持ったまま 1 回だけ refresh すること (その下の 1 件) を別々に検査する。
    @staticmethod
    def cache_directory(env: dict[str, str]) -> Path:
        return Path(env["HOME"]) / ".claude" / "cache"

    def run_namespace_process(
        self, env: dict[str, str]
    ) -> tuple[bytes, subprocess.Popen[bytes]]:
        process = self.namespace_process(env)
        stdout, stderr = process.communicate(timeout=6)
        self.assertEqual(process.returncode, 0, stderr.decode())
        return stdout, process

    def test_cache_miss_during_another_refresh_returns_without_calling_gh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env, call_log = self.make_environment(Path(tmp), sleep_seconds=0)
            lock_dir = self.cache_directory(env) / ".owned-github-namespaces.lock"
            lock_dir.mkdir(parents=True)

            stdout, _ = self.run_namespace_process(env)

            self.assertEqual(stdout, b"")
            self.assertFalse(
                call_log.exists(), "gh was called while another refresh held the lock"
            )
            self.assertTrue(
                lock_dir.is_dir(), "the lock of the other refresh was removed"
            )

    def test_cache_miss_during_another_refresh_returns_the_stale_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env, call_log = self.make_environment(Path(tmp), sleep_seconds=0)
            cache_dir = self.cache_directory(env)
            (cache_dir / ".owned-github-namespaces.lock").mkdir(parents=True)
            cache_file = cache_dir / "owned-github-namespaces.txt"
            cache_file.write_text("old-user\nold-org\n", encoding="utf-8")
            two_days_ago = time.time() - 2 * 24 * 3600
            os.utime(cache_file, (two_days_ago, two_days_ago))

            stdout, _ = self.run_namespace_process(env)

            self.assertEqual(stdout.decode().splitlines(), ["old-user", "old-org"])
            self.assertFalse(
                call_log.exists(), "gh was called while another refresh held the lock"
            )

    def test_cache_miss_without_another_refresh_refreshes_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env, call_log = self.make_environment(Path(tmp), sleep_seconds=0)
            cache_dir = self.cache_directory(env)

            stdout, _ = self.run_namespace_process(env)

            self.assertCountEqual(
                stdout.decode().splitlines(), ["test-user", "test-org"]
            )
            calls = call_log.read_text(encoding="utf-8").splitlines()
            self.assertCountEqual(
                calls,
                ["api user --jq .login", "api user/orgs --jq .[].login"],
            )
            lock_states = (
                Path(env["GH_LOCK_STATE_LOG"]).read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(
                lock_states, ["held", "held"], "gh was called without holding the lock"
            )
            self.assertFalse((cache_dir / ".owned-github-namespaces.lock").exists())
            self.assertCountEqual(
                (cache_dir / "owned-github-namespaces.txt")
                .read_text(encoding="utf-8")
                .splitlines(),
                ["test-user", "test-org"],
            )


if __name__ == "__main__":
    unittest.main()
