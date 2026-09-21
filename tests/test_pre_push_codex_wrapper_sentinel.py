"""pre-push-codex-review の codex review wrapper が書く terminal sentinel の契約テスト。

wrapper は起動ごとに一意な run id を作り、sentinel の絶対パスと run id を stderr の
案内行 (`terminal sentinel: <絶対パス> run=<id>`) で知らせる。git-dir を解決した直後に
sentinel を捨て、EXIT trap で終了状態を 1 行 (`status=<ok|failed> run=<id>`) だけ書く。
background へ移行した wrapper の終了を codex-reviewer subagent はこの sentinel の出現で
検知し、run id の一致で自分が待っている run のものだと確認するため、sentinel は出力
ストリームのテキストに依存しない唯一の終端信号になる。git-dir を解決できない
(git repository でない) 場合は書き込み先が定まらないので sentinel を書かない。

テストは隔離した一時 git repository で wrapper を実行する。codex companion に到達する
経路は使わない (companion へ到達する前に終わる経路だけを検証する)。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "pre-push-codex-review"
WRAPPER = PLUGIN / "hooks" / "scripts" / "run-pre-push-codex-review.sh"
MARKERS_LIB = PLUGIN / "hooks" / "scripts" / "lib" / "markers.sh"

# sentinel の固定名と、その path を組み立てる lib/markers.sh の helper。
SENTINEL_NAME = "pre-push-codex-review-terminal"
SENTINEL_PATH_HELPER = "codex_terminal_sentinel_path"

# 起動時に stderr へ出る案内行と、sentinel の 1 行の形式。
ANNOUNCEMENT_PATTERN = re.compile(r"terminal sentinel: (?P<path>\S+) run=(?P<run>\S+)")
SENTINEL_LINE_PATTERN = re.compile(r"\Astatus=(?P<status>ok|failed) run=(?P<run>\S+)\Z")

GIT = shutil.which("git")
BASH = shutil.which("bash")


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

    def announced_run_id(
        self, repository: Path, result: subprocess.CompletedProcess[str]
    ) -> str:
        """起動時の案内行から run id を取り出す (path の一致も確認する)。

        案内行は stdout にも出る (回収時に output file の先頭から run id を読むため)
        が、run id 自体はどちらの stream から取っても同じなので stderr から取る。
        """
        match = ANNOUNCEMENT_PATTERN.search(result.stderr)
        if match is None:
            self.fail(
                f"起動時の sentinel 案内行が stderr に無い: {result.stderr[:300]}"
            )
        self.assertEqual(match.group("path"), str(self.sentinel_path(repository)))
        return match.group("run")

    def assert_sentinel(
        self, repository: Path, *, status: str, run_id: str
    ) -> None:
        """sentinel が 1 行だけで、終了状態と run id を持つことを確認する。"""
        sentinel = self.sentinel_path(repository)
        self.assertTrue(sentinel.is_file(), f"missing sentinel: {sentinel}")
        lines = sentinel.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1, lines)
        match = SENTINEL_LINE_PATTERN.match(lines[0])
        if match is None:
            self.fail(f"{sentinel}: 想定の形式ではない: {lines[0]}")
        self.assertEqual(match.group("status"), status)
        self.assertEqual(match.group("run"), run_id)

    def test_failing_run_records_failed_status(self) -> None:
        repository = self.make_repository()
        self.git("switch", "--detach", "HEAD", cwd=repository)
        result = self.run_wrapper(repository)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        run_id = self.announced_run_id(repository, result)
        self.assert_sentinel(repository, status="failed", run_id=run_id)

    def test_default_branch_run_records_ok_status(self) -> None:
        repository = self.make_repository()
        result = self.run_wrapper(repository)
        self.assertEqual(result.returncode, 0, result.stderr)
        run_id = self.announced_run_id(repository, result)
        self.assert_sentinel(repository, status="ok", run_id=run_id)

    def test_startup_discards_a_stale_sentinel(self) -> None:
        repository = self.make_repository()
        self.sentinel_path(repository).write_text(
            "status=ok run=stale-run-id\nstale second line\n", encoding="utf-8"
        )
        self.git("switch", "--detach", "HEAD", cwd=repository)
        result = self.run_wrapper(repository)
        run_id = self.announced_run_id(repository, result)
        self.assertNotEqual(run_id, "stale-run-id")
        self.assert_sentinel(repository, status="failed", run_id=run_id)

    def test_each_run_uses_a_distinct_run_id(self) -> None:
        repository = self.make_repository()
        first = self.announced_run_id(repository, self.run_wrapper(repository))
        second = self.announced_run_id(repository, self.run_wrapper(repository))
        self.assertNotEqual(first, second)

    def test_outside_a_git_repository_writes_no_sentinel(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        result = self.run_wrapper(plain)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.root.rglob(SENTINEL_NAME)), [])

    def test_startup_announces_the_sentinel_on_both_streams(self) -> None:
        """案内行は stdout にも出る (background 時に output file の先頭へ入る)。"""
        repository = self.make_repository()
        result = self.run_wrapper(repository)
        run_id = self.announced_run_id(repository, result)
        expected = (
            f"terminal sentinel: {self.sentinel_path(repository)} run={run_id}"
        )
        self.assertIn(expected, result.stdout)

    def test_markers_lib_defines_the_sentinel_path_helper(self) -> None:
        body = MARKERS_LIB.read_text(encoding="utf-8")
        for needle in (f"{SENTINEL_PATH_HELPER}()", SENTINEL_NAME):
            with self.subTest(needle=needle):
                if needle not in body:
                    self.fail(f"{MARKERS_LIB}: 期待する定義が無い: {needle}")


if __name__ == "__main__":
    unittest.main()
