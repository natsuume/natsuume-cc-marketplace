"""pre-merge-codex-review の codex review wrapper が書く terminal sentinel の契約テスト。

wrapper は git-dir を解決した直後に sentinel を捨て、EXIT trap で終了状態を 1 行
(`status=ok` = exit 0 の全経路 / `status=failed` = fail 経路) だけ書く。background へ
移行した wrapper の終了を codex-reviewer subagent はこの sentinel の出現で検知するため、
sentinel は出力ストリームのテキストに依存しない唯一の終端信号になる。git-dir を解決
できない (git repository でない) 場合は書き込み先が定まらないので sentinel を書かない。

テストは隔離した一時 git repository で wrapper を実行する。PR を解決できない状態で
失敗する経路だけを使い、codex companion には到達させない。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "pre-merge-codex-review"
WRAPPER = PLUGIN / "hooks" / "scripts" / "run-pre-merge-codex-review.sh"
MARKERS_LIB = PLUGIN / "hooks" / "scripts" / "lib" / "markers.sh"

# sentinel の固定名と、その path を組み立てる lib/markers.sh の helper。
SENTINEL_NAME = "pre-merge-codex-review-terminal"
SENTINEL_PATH_HELPER = "pre_merge_terminal_sentinel_path"

GIT = shutil.which("git")
BASH = shutil.which("bash")
# wrapper は git-dir 解決より前に gh / jq / node の存在を確認して fail するため、
# sentinel を観測するテストはこれらが揃っている環境でのみ実行する。
PREREQUISITES = all(
    shutil.which(command) is not None for command in ("gh", "jq", "node")
)


@unittest.skipUnless(GIT and BASH, "requires git and bash")
class WrapperTerminalSentinelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # macOS の一時ディレクトリは symlink なので、git が出力する path と
        # 比較できるよう解決済みの path を使う。
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["TMPDIR"] = str(self.root)
        # 実行環境の git 設定と、一時ディレクトリより上への repository 探索を遮断する。
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CEILING_DIRECTORIES"] = str(self.root)
        env["GIT_AUTHOR_NAME"] = "wrapper sentinel test"
        env["GIT_AUTHOR_EMAIL"] = "wrapper-sentinel@example.invalid"
        env["GIT_COMMITTER_NAME"] = "wrapper sentinel test"
        env["GIT_COMMITTER_EMAIL"] = "wrapper-sentinel@example.invalid"
        return env

    def git(self, *args: str, cwd: Path) -> str:
        assert GIT is not None
        result = subprocess.run(
            [GIT, *args],
            cwd=cwd,
            env=self.env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def make_repository(self) -> Path:
        repository = self.root / "repo"
        repository.mkdir()
        self.git("-c", "init.defaultBranch=master", "init", cwd=repository)
        (repository / "file.txt").write_text("content\n", encoding="utf-8")
        self.git("add", "file.txt", cwd=repository)
        self.git("commit", "-m", "initial commit", cwd=repository)
        return repository

    def sentinel_path(self, repository: Path) -> Path:
        return repository / ".git" / SENTINEL_NAME

    def run_wrapper(self, cwd: Path) -> subprocess.CompletedProcess[str]:
        assert BASH is not None
        self.assertTrue(WRAPPER.is_file(), f"missing wrapper: {WRAPPER}")
        return subprocess.run(
            [BASH, str(WRAPPER)],
            cwd=cwd,
            env=self.env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )

    def sentinel_lines(self, repository: Path) -> list[str]:
        sentinel = self.sentinel_path(repository)
        self.assertTrue(sentinel.is_file(), f"missing sentinel: {sentinel}")
        return sentinel.read_text(encoding="utf-8").splitlines()

    @unittest.skipUnless(PREREQUISITES, "requires gh, jq and node")
    def test_failing_run_records_failed_status(self) -> None:
        repository = self.make_repository()
        self.git("switch", "--detach", "HEAD", cwd=repository)
        result = self.run_wrapper(repository)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sentinel_lines(repository), ["status=failed"])

    @unittest.skipUnless(PREREQUISITES, "requires gh, jq and node")
    def test_startup_discards_a_stale_sentinel(self) -> None:
        repository = self.make_repository()
        self.sentinel_path(repository).write_text(
            "status=ok\nstale second line\n", encoding="utf-8"
        )
        self.git("switch", "--detach", "HEAD", cwd=repository)
        self.run_wrapper(repository)
        self.assertEqual(self.sentinel_lines(repository), ["status=failed"])

    def test_outside_a_git_repository_writes_no_sentinel(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        result = self.run_wrapper(plain)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.root.rglob(SENTINEL_NAME)), [])

    @unittest.skipUnless(PREREQUISITES, "requires gh, jq and node")
    def test_startup_announces_the_sentinel_path_on_stderr(self) -> None:
        repository = self.make_repository()
        self.git("switch", "--detach", "HEAD", cwd=repository)
        result = self.run_wrapper(repository)
        self.assertIn(str(self.sentinel_path(repository)), result.stderr)

    def test_markers_lib_defines_the_sentinel_path_helper(self) -> None:
        body = MARKERS_LIB.read_text(encoding="utf-8")
        for needle in (f"{SENTINEL_PATH_HELPER}()", SENTINEL_NAME):
            with self.subTest(needle=needle):
                if needle not in body:
                    self.fail(f"{MARKERS_LIB}: 期待する定義が無い: {needle}")


if __name__ == "__main__":
    unittest.main()
