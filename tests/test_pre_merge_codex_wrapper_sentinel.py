"""pre-merge-codex-review の codex review wrapper が書く terminal sentinel の契約テスト。

wrapper は起動ごとに一意な run id を作り、その run 専用の sentinel
(`<git-dir>/pre-merge-codex-review-terminal-<run id>`) の絶対パスと run id を案内行
(`terminal sentinel: <絶対パス> run=<id>`) として stdout と stderr の両方に出す。EXIT
trap は stdout へ終了行 (`terminal sentinel end run=<id>`) を出してから、sentinel に
終了状態を 1 行 (`status=<ok|failed> run=<id>`) だけ書く。起動時の掃除は 24 時間より
古い sentinel だけを対象とし、実行中の別 run の sentinel は残す。

background へ移行した wrapper の終了を codex-reviewer subagent はこの sentinel の出現で
検知し、終了行で本文の完結を確認するため、判定は出力ストリームの review テキストにも
別 run の残骸にも依存しない。git-dir を解決できない (git repository でない) 場合は
書き込み先が定まらないので sentinel を書かない。

テストは隔離した一時 git repository で wrapper を実行する。PR を解決できない状態で
失敗する経路だけを使い、codex companion には到達させない。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "pre-merge-codex-review"
WRAPPER = PLUGIN / "hooks" / "scripts" / "run-pre-merge-codex-review.sh"
MARKERS_LIB = PLUGIN / "hooks" / "scripts" / "lib" / "markers.sh"

# run ごとの sentinel の固定 prefix と、その path を組み立てる lib/markers.sh の helper
# (引数は git-dir と run id の 2 つ)。
SENTINEL_NAME_PREFIX = "pre-merge-codex-review-terminal"
SENTINEL_PATH_HELPER = "pre_merge_terminal_sentinel_path"
# 起動時の掃除が対象にする古さ。
STALE_SENTINEL_AGE_SECONDS = 24 * 60 * 60

# 起動時に stdout / stderr へ出る案内行、EXIT trap が stdout へ出す終了行、
# sentinel の 1 行の形式。
ANNOUNCEMENT_PATTERN = re.compile(r"terminal sentinel: (?P<path>\S+) run=(?P<run>\S+)")
SENTINEL_LINE_PATTERN = re.compile(r"\Astatus=(?P<status>ok|failed) run=(?P<run>\S+)\Z")
END_LINE_TEMPLATE = "terminal sentinel end run={run_id}"
# run id は `<pid>-<epoch 秒>-<8 桁の 16 進>`。待機ループが `grep -qE " run=<id>$"` で
# 照合するため、正規表現のメタ文字を含まない文字集合 ([0-9a-f-]) に限る。
RUN_ID_PATTERN = re.compile(r"\A[0-9]+-[0-9]+-[0-9a-f]{8}\Z")

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

    def detached_repository(self) -> Path:
        """PR を解決できない (detached HEAD の) repository を作る。"""
        repository = self.make_repository()
        self.git("switch", "--detach", "HEAD", cwd=repository)
        return repository

    def sentinel_path(self, repository: Path, run_id: str) -> Path:
        return repository / ".git" / f"{SENTINEL_NAME_PREFIX}-{run_id}"

    def place_foreign_sentinel(
        self, repository: Path, run_id: str, *, age_seconds: float
    ) -> Path:
        """別 run の sentinel を、指定した古さ (mtime) で置く。"""
        path = self.sentinel_path(repository, run_id)
        path.write_text(f"status=ok run={run_id}\n", encoding="utf-8")
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

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
        """起動時の案内行から run id を取り出す (run ごとの path も確認する)。

        案内行は stdout にも出る (回収時に output file の先頭行から run id を読むため)
        が、run id 自体はどちらの stream から取っても同じなので stderr から取る。
        """
        match = ANNOUNCEMENT_PATTERN.search(result.stderr)
        if match is None:
            self.fail(
                f"起動時の sentinel 案内行が stderr に無い: {result.stderr[:300]}"
            )
        run_id = match.group("run")
        self.assertEqual(
            match.group("path"), str(self.sentinel_path(repository, run_id))
        )
        return run_id

    def assert_sentinel(
        self, repository: Path, *, status: str, run_id: str
    ) -> None:
        """自 run の sentinel が 1 行だけで、終了状態と run id を持つことを確認する。"""
        sentinel = self.sentinel_path(repository, run_id)
        self.assertTrue(sentinel.is_file(), f"missing sentinel: {sentinel}")
        lines = sentinel.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1, lines)
        match = SENTINEL_LINE_PATTERN.match(lines[0])
        if match is None:
            self.fail(f"{sentinel}: 想定の形式ではない: {lines[0]}")
        self.assertEqual(match.group("status"), status)
        self.assertEqual(match.group("run"), run_id)

    def assert_end_line(
        self, result: subprocess.CompletedProcess[str], run_id: str
    ) -> None:
        """stdout の最終行が自 run の終了行であることを確認する。"""
        lines = result.stdout.splitlines()
        expected = END_LINE_TEMPLATE.format(run_id=run_id)
        if not lines:
            self.fail(f"stdout が空で終了行が無い (expected: {expected})")
        self.assertEqual(lines[-1], expected)

    @unittest.skipUnless(PREREQUISITES, "requires gh, jq and node")
    def test_run_id_has_the_fixed_shape(self) -> None:
        """run id は `<pid>-<epoch 秒>-<8 桁の 16 進>` で、正規表現メタ文字を含まない。"""
        repository = self.detached_repository()
        run_id = self.announced_run_id(repository, self.run_wrapper(repository))
        if RUN_ID_PATTERN.match(run_id) is None:
            self.fail(f"run id が想定の形式ではない: {run_id}")

    @unittest.skipUnless(PREREQUISITES, "requires gh, jq and node")
    def test_failing_run_records_failed_status(self) -> None:
        repository = self.detached_repository()
        result = self.run_wrapper(repository)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        run_id = self.announced_run_id(repository, result)
        self.assert_sentinel(repository, status="failed", run_id=run_id)
        self.assert_end_line(result, run_id)

    @unittest.skipUnless(PREREQUISITES, "requires gh, jq and node")
    def test_startup_removes_only_stale_sentinels(self) -> None:
        repository = self.detached_repository()
        stale = self.place_foreign_sentinel(
            repository,
            "11111-1700000000-aaaaaaaa",
            age_seconds=STALE_SENTINEL_AGE_SECONDS + 3600,
        )
        fresh = self.place_foreign_sentinel(
            repository, "22222-1700000001-bbbbbbbb", age_seconds=60
        )
        result = self.run_wrapper(repository)
        run_id = self.announced_run_id(repository, result)
        self.assertFalse(stale.exists(), f"stale sentinel が残っている: {stale}")
        self.assertTrue(fresh.is_file(), f"別 run の sentinel が消された: {fresh}")
        self.assert_sentinel(repository, status="failed", run_id=run_id)

    @unittest.skipUnless(PREREQUISITES, "requires gh, jq and node")
    def test_each_run_writes_its_own_sentinel(self) -> None:
        repository = self.detached_repository()
        first = self.announced_run_id(repository, self.run_wrapper(repository))
        second = self.announced_run_id(repository, self.run_wrapper(repository))
        self.assertNotEqual(first, second)
        self.assertTrue(self.sentinel_path(repository, first).is_file())
        self.assertTrue(self.sentinel_path(repository, second).is_file())

    def test_outside_a_git_repository_writes_no_sentinel(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        result = self.run_wrapper(plain)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.root.rglob(f"{SENTINEL_NAME_PREFIX}-*")), [])

    @unittest.skipUnless(PREREQUISITES, "requires gh, jq and node")
    def test_startup_announces_the_sentinel_on_both_streams(self) -> None:
        """案内行は stdout にも出る (background 時に output file の先頭行になる)。"""
        repository = self.detached_repository()
        result = self.run_wrapper(repository)
        run_id = self.announced_run_id(repository, result)
        expected = (
            f"terminal sentinel: {self.sentinel_path(repository, run_id)} "
            f"run={run_id}"
        )
        self.assertIn(expected, result.stdout)
        self.assertEqual(result.stdout.splitlines()[0], expected)

    def test_markers_lib_defines_the_sentinel_path_helper(self) -> None:
        body = MARKERS_LIB.read_text(encoding="utf-8")
        for needle in (f"{SENTINEL_PATH_HELPER}()", SENTINEL_NAME_PREFIX):
            with self.subTest(needle=needle):
                if needle not in body:
                    self.fail(f"{MARKERS_LIB}: 期待する定義が無い: {needle}")


if __name__ == "__main__":
    unittest.main()
