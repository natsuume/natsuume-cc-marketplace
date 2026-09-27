from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUSLINE_DIR = ROOT / "plugins" / "natsuume-statusline" / "statusline"
STATUSLINE_MAIN = STATUSLINE_DIR / "main.sh"
GIT_STATUS_SH = STATUSLINE_DIR / "git-status.sh"

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

SAMPLE_V2 = "\n".join(
    [
        "# branch.oid 0123456789abcdef0123456789abcdef01234567",
        "# branch.head main",
        "# branch.upstream origin/main",
        "# branch.ab +1 -0",
        "1 M. N... 100644 100644 100644 aaaa bbbb staged.txt",
        "1 .M N... 100644 100644 100644 aaaa aaaa modified.txt",
        "1 MM N... 100644 100644 100644 aaaa bbbb both.txt",
        "1 .D N... 100644 100644 000000 aaaa aaaa deleted.txt",
        "2 R. N... 100644 100644 100644 aaaa aaaa R100 new.txt\told.txt",
        "u UU N... 100644 100644 100644 100644 aaaa bbbb cccc conflict.txt",
        "? untracked.txt",
        "! ignored.txt",
        "",
    ]
)


def run_parser(function: str, text: str) -> str:
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; "$2" "$3"', "_", str(GIT_STATUS_SH), function, text],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode())
    return result.stdout.decode()


class ParseStatusBranchTest(unittest.TestCase):
    """parse_status_branch が porcelain v2 の branch.head をブランチ名として返す契約を検証する。"""

    def test_returns_branch_head(self) -> None:
        self.assertEqual(run_parser("parse_status_branch", SAMPLE_V2), "main")

    def test_detached_head_returns_empty(self) -> None:
        text = "# branch.oid 0123\n# branch.head (detached)\n"
        self.assertEqual(run_parser("parse_status_branch", text), "")

    def test_missing_header_returns_empty(self) -> None:
        self.assertEqual(run_parser("parse_status_branch", "? a.txt\n"), "")
        self.assertEqual(run_parser("parse_status_branch", ""), "")


class ParseStatusEntriesTest(unittest.TestCase):
    """parse_status_entries が porcelain v2 のエントリを v1 互換の XY 行へ変換する契約を検証する。"""

    def test_converts_entries_to_v1_xy(self) -> None:
        output = run_parser("parse_status_entries", SAMPLE_V2)
        self.assertEqual(
            output.split("\n"),
            ["M ", " M", "MM", " D", "R ", "UU", "??", ""],
        )

    def test_headers_only_returns_empty(self) -> None:
        text = "# branch.oid 0123\n# branch.head main\n"
        self.assertEqual(run_parser("parse_status_entries", text), "")

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(run_parser("parse_status_entries", ""), "")


class StatuslineGitIntegrationTest(unittest.TestCase):
    """main.sh が git の作業ツリーを変更せずに 1 行目の git 情報を描画する契約を検証する。"""

    def setUp(self) -> None:
        # HOME / XDG_CACHE_HOME / TMPDIR を一時ディレクトリへ向け、キャッシュ書き込みと
        # git の global 設定の参照を実環境から隔離する。
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp_path = Path(self.tmp.name)
        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(tmp_path / "home"),
                "XDG_CACHE_HOME": str(tmp_path / "cache"),
                "TMPDIR": str(tmp_path / "tmp"),
                "COLUMNS": "200",
                "GIT_CONFIG_NOSYSTEM": "1",
            }
        )
        self.env.pop("GIT_OPTIONAL_LOCKS", None)
        for name in ("home", "cache", "tmp", "repo", "bin"):
            (tmp_path / name).mkdir()
        self.repo = tmp_path / "repo"
        self.bin_dir = tmp_path / "bin"
        self.git_log = tmp_path / "git-calls.log"

        self.git("init", "-q", "-b", "main")
        for i in range(20):
            (self.repo / f"file{i}.txt").write_text(f"{i}\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "init")

    def git(self, *args: str) -> str:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@example.com",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            check=True,
            timeout=30,
        )
        return result.stdout.decode()

    def run_main(self, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
        # model_scoped を非空にして OAuth usage API の background fetch を起動させない。
        payload = {
            "workspace": {"current_dir": str(cwd or self.repo)},
            "model": {"display_name": "TestModel"},
            "rate_limits": {
                "model_scoped": [{"display_name": "Scoped", "utilization": 10, "resets_at": ""}]
            },
        }
        result = subprocess.run(
            ["bash", str(STATUSLINE_MAIN)],
            input=json.dumps(payload).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.tmp.name,
            env=env or self.env,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return ANSI_ESCAPE.sub("", result.stdout.decode())

    def index_identity(self) -> tuple[int, int]:
        st = (self.repo / ".git" / "index").stat()
        return (st.st_ino, st.st_mtime_ns)

    def make_index_stat_stale(self) -> None:
        # 内容は変えずに mtime だけ進め、git status が index の stat 情報を書き戻す状況を作る。
        # racy-git 判定を避けるため、index の mtime より十分後の時刻にする。
        time.sleep(1.1)
        future = time.time() + 5
        for path in self.repo.glob("file*.txt"):
            os.utime(path, (future, future))

    def test_statusline_does_not_rewrite_index(self) -> None:
        self.make_index_stat_stale()
        before = self.index_identity()

        self.run_main()

        self.assertEqual(self.index_identity(), before)
        # 同じ状態で素の git status は index を書き戻す (= この状況が index.lock を取る条件である) ことを確認する。
        self.git("status", "--porcelain")
        self.assertNotEqual(self.index_identity(), before)

    def env_with_git_wrapper(self, fail_status: bool = False) -> dict[str, str]:
        # 呼び出しを記録してから本物の git に委ねる wrapper を PATH の先頭に置く。
        # fail_status=True では、status サブコマンドだけを古い git の unknown option と同じく失敗させる。
        real_git = shutil.which("git", path=self.env.get("PATH"))
        self.assertIsNotNone(real_git)
        fail_line = (
            'case " $* " in *" status "*) echo "error: unknown option" >&2; exit 129 ;; esac\n'
            if fail_status
            else ""
        )
        wrapper = self.bin_dir / "git"
        wrapper.write_text(
            "#!/bin/bash\n"
            f'printf \'%s|%s\\n\' "${{GIT_OPTIONAL_LOCKS-unset}}" "$*" >> "{self.git_log}"\n'
            f"{fail_line}"
            f'exec "{real_git}" "$@"\n'
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        env = dict(self.env)
        env["PATH"] = f"{self.bin_dir}{os.pathsep}{env.get('PATH', '')}"
        return env

    def test_git_invocations_disable_optional_locks(self) -> None:
        self.run_main(env=self.env_with_git_wrapper())

        calls = self.git_log.read_text().splitlines()
        self.assertTrue(calls, "statusline が git を呼んでいない")
        for call in calls:
            self.assertTrue(call.startswith("0|"), f"GIT_OPTIONAL_LOCKS=0 でない呼び出し: {call}")
        self.assertLessEqual(len(calls), 3, calls)
        status_calls = [c for c in calls if " status " in f" {c.split('|', 1)[1]} "]
        self.assertEqual(len(status_calls), 1, calls)
        self.assertIn("--porcelain=v2", status_calls[0])
        self.assertIn("--branch", status_calls[0])
        self.assertIn("--no-ahead-behind", status_calls[0])

    def test_status_failure_hides_change_segments(self) -> None:
        (self.repo / "file0.txt").write_text("changed\n")

        first_line = self.run_main(env=self.env_with_git_wrapper(fail_status=True)).split("\n", 1)[0]

        self.assertNotIn("clean", first_line)
        self.assertNotIn("uncommitted", first_line)
        self.assertNotIn("modified:", first_line)
        self.assertNotIn("branch:", first_line)
        self.assertIn("repo", first_line)

    def test_renders_branch_and_change_counts(self) -> None:
        (self.repo / "file0.txt").write_text("changed\n")
        self.git("add", "file0.txt")
        (self.repo / "file1.txt").write_text("changed\n")
        self.git("mv", "file2.txt", "renamed.txt")
        (self.repo / "untracked.txt").write_text("new\n")

        output = self.run_main()
        first_line = output.split("\n", 1)[0]

        self.assertIn("branch: main", first_line)
        self.assertIn("staged:2", first_line)
        self.assertIn("modified:1", first_line)
        self.assertIn("4 uncommitted", first_line)

    def test_clean_repository_renders_clean(self) -> None:
        first_line = self.run_main().split("\n", 1)[0]

        self.assertIn("branch: main", first_line)
        self.assertIn("clean", first_line)
        self.assertNotIn("staged:", first_line)
        self.assertNotIn("modified:", first_line)

    def test_detached_head_hides_branch(self) -> None:
        self.git("switch", "-q", "--detach", "HEAD")

        first_line = self.run_main().split("\n", 1)[0]

        self.assertNotIn("branch:", first_line)
        self.assertIn("clean", first_line)

    def test_subdirectory_renders_repository_status(self) -> None:
        sub = self.repo / "sub"
        sub.mkdir()
        (sub / "new.txt").write_text("new\n")

        first_line = self.run_main(cwd=sub).split("\n", 1)[0]

        self.assertIn("branch: main", first_line)
        self.assertIn("1 uncommitted", first_line)

    def test_outside_repository_renders_no_git_segments(self) -> None:
        outside = Path(self.tmp.name) / "tmp"

        first_line = self.run_main(cwd=outside).split("\n", 1)[0]

        self.assertNotIn("branch:", first_line)
        self.assertNotIn("clean", first_line)
        self.assertNotIn("uncommitted", first_line)


if __name__ == "__main__":
    unittest.main()
