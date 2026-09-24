from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUSLINE_MAIN = ROOT / "plugins" / "natsuume-statusline" / "statusline" / "main.sh"
WEEKLY_SCOPED_LIB = STATUSLINE_MAIN.parent / "weekly-scoped-limits.sh"

# cache の fetched_at は main.sh 実行中の epoch 秒のため、実行前後の時刻との差に許容幅を持たせる。
CLOCK_SLACK_SEC = 5


class StatuslineWeeklyScopedStdinCacheTest(unittest.TestCase):
    """stdin の rate_limits.model_scoped[] (公式経路) から週次枠 cache を書き出す契約を検証する。

    - 抽出条件 (display_name が非空文字列、utilization が数値) を満たす entry が 1 件以上あれば、
      weekly-scoped-limits.sh の cache schema で weekly-scoped.json を書き出す
    - 既存 cache が 300 秒以内かつ weekly_scoped が同一なら書き出さない
    - 既存 cache が 300 秒以内で、display_name と resets_at が一致する entry の percent が
      今回より大きいものがあれば書き出さない (単調性ガード)
    - 抽出 entry が 0 件なら書き出さない (cache 経路の kick のみ)
    - 書き出し失敗は表示・exit code・stderr に影響させない
    """

    def setUp(self) -> None:
        # HOME / XDG_CACHE_HOME / TMPDIR を一時ディレクトリへ向け、cache 書き込みと
        # 認証情報の参照を実環境から隔離する。
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp_path = Path(self.tmp.name)
        self.cache_home = tmp_path / "cache"
        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(tmp_path / "home"),
                "XDG_CACHE_HOME": str(self.cache_home),
                "TMPDIR": str(tmp_path / "tmp"),
                "COLUMNS": "200",
            }
        )
        for name in ("home", "cache", "tmp", "work"):
            (tmp_path / name).mkdir()
        self.work_dir = tmp_path / "work"
        self.cache_dir = self.cache_home / "natsuume-statusline"
        self.cache_file = self.cache_dir / "weekly-scoped.json"

    def payload(self, model_scoped: object) -> str:
        return json.dumps(
            {
                "workspace": {"current_dir": str(self.work_dir)},
                "model": {"display_name": "TestModel"},
                "rate_limits": {
                    "seven_day": {"used_percentage": 20},
                    "model_scoped": model_scoped,
                },
            }
        )

    def run_main(self, stdin: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(STATUSLINE_MAIN)],
            input=stdin.encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.work_dir,
            env=self.env,
            check=False,
            timeout=30,
        )

    def run_main_ok(self, stdin: str) -> subprocess.CompletedProcess[bytes]:
        result = self.run_main(stdin)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr.decode(), "")
        return result

    def write_cache(self, content: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(json.dumps(content))

    def read_cache(self) -> dict:
        return json.loads(self.cache_file.read_text())

    def test_writes_cache_with_existing_schema(self) -> None:
        before = int(time.time())
        self.run_main_ok(
            self.payload(
                [
                    {"display_name": "Fable", "utilization": 42.5, "resets_at": "2026-10-01T00:00:00Z"},
                    {"display_name": "Opus", "utilization": 7},
                ]
            )
        )
        after = int(time.time())

        cache = self.read_cache()
        self.assertEqual(
            set(cache), {"fetched_at", "consecutive_failures", "next_attempt_at", "weekly_scoped"}
        )
        self.assertGreaterEqual(cache["fetched_at"], before - CLOCK_SLACK_SEC)
        self.assertLessEqual(cache["fetched_at"], after + CLOCK_SLACK_SEC)
        self.assertEqual(cache["consecutive_failures"], 0)
        self.assertEqual(cache["next_attempt_at"], cache["fetched_at"] + 300)
        self.assertEqual(
            cache["weekly_scoped"],
            [
                {"display_name": "Fable", "percent": 42.5, "resets_at": "2026-10-01T00:00:00Z"},
                {"display_name": "Opus", "percent": 7, "resets_at": ""},
            ],
        )

    def test_only_entries_matching_extraction_condition_are_written(self) -> None:
        self.run_main_ok(
            self.payload(
                [
                    {"display_name": "", "utilization": 10},
                    {"display_name": 1, "utilization": 10},
                    {"display_name": "NoUtil"},
                    {"display_name": "StrUtil", "utilization": "10"},
                    {"display_name": "Fable", "utilization": 55, "resets_at": "r"},
                ]
            )
        )

        self.assertEqual(
            self.read_cache()["weekly_scoped"],
            [{"display_name": "Fable", "percent": 55, "resets_at": "r"}],
        )

    def test_cache_file_and_dir_are_owner_only(self) -> None:
        # cache ディレクトリが未作成の状態から書き出させ、作成時の権限を検証する。
        self.run_main_ok(self.payload([{"display_name": "Fable", "utilization": 1}]))

        self.assertEqual(stat.S_IMODE(self.cache_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.cache_file.stat().st_mode), 0o600)
        # atomic write の一時ファイルが残っていないこと。
        self.assertEqual(sorted(p.name for p in self.cache_dir.iterdir()), ["weekly-scoped.json"])

    def test_fresh_identical_cache_is_not_rewritten(self) -> None:
        fetched_at = int(time.time()) - 10
        original = {
            "fetched_at": fetched_at,
            "consecutive_failures": 0,
            "next_attempt_at": fetched_at + 300,
            "weekly_scoped": [{"display_name": "Fable", "percent": 30, "resets_at": "r"}],
        }
        self.write_cache(original)
        original_text = self.cache_file.read_text()

        self.run_main_ok(
            self.payload([{"display_name": "Fable", "utilization": 30, "resets_at": "r"}])
        )

        self.assertEqual(self.cache_file.read_text(), original_text)

    def test_fresh_cache_with_different_content_is_rewritten(self) -> None:
        fetched_at = int(time.time()) - 10
        self.write_cache(
            {
                "fetched_at": fetched_at,
                "consecutive_failures": 0,
                "next_attempt_at": fetched_at + 300,
                "weekly_scoped": [{"display_name": "Fable", "percent": 30, "resets_at": "r"}],
            }
        )

        self.run_main_ok(
            self.payload([{"display_name": "Fable", "utilization": 31, "resets_at": "r"}])
        )

        self.assertEqual(
            self.read_cache()["weekly_scoped"],
            [{"display_name": "Fable", "percent": 31, "resets_at": "r"}],
        )

    def test_stale_identical_cache_is_rewritten(self) -> None:
        fetched_at = int(time.time()) - 301 - CLOCK_SLACK_SEC
        self.write_cache(
            {
                "fetched_at": fetched_at,
                "consecutive_failures": 3,
                "next_attempt_at": fetched_at + 300,
                "weekly_scoped": [{"display_name": "Fable", "percent": 30, "resets_at": "r"}],
            }
        )

        self.run_main_ok(
            self.payload([{"display_name": "Fable", "utilization": 30, "resets_at": "r"}])
        )

        cache = self.read_cache()
        self.assertGreater(cache["fetched_at"], fetched_at)
        self.assertEqual(cache["consecutive_failures"], 0)

    def fresh_cache(self, weekly_scoped: list[dict], age_sec: int = 10) -> str:
        fetched_at = int(time.time()) - age_sec
        self.write_cache(
            {
                "fetched_at": fetched_at,
                "consecutive_failures": 0,
                "next_attempt_at": fetched_at + 300,
                "weekly_scoped": weekly_scoped,
            }
        )
        return self.cache_file.read_text()

    def test_fresh_cache_with_higher_percent_in_same_window_is_not_overwritten(self) -> None:
        original_text = self.fresh_cache(
            [{"display_name": "Fable", "percent": 40, "resets_at": "r1"}]
        )

        self.run_main_ok(
            self.payload([{"display_name": "Fable", "utilization": 35, "resets_at": "r1"}])
        )

        self.assertEqual(self.cache_file.read_text(), original_text)

    def test_monotonic_guard_blocks_whole_write_if_any_entry_decreases(self) -> None:
        original_text = self.fresh_cache(
            [
                {"display_name": "Fable", "percent": 40, "resets_at": "r1"},
                {"display_name": "Opus", "percent": 10, "resets_at": "r2"},
            ]
        )

        self.run_main_ok(
            self.payload(
                [
                    {"display_name": "Fable", "utilization": 35, "resets_at": "r1"},
                    {"display_name": "Opus", "utilization": 20, "resets_at": "r2"},
                ]
            )
        )

        self.assertEqual(self.cache_file.read_text(), original_text)

    def test_higher_percent_in_different_window_does_not_block_write(self) -> None:
        self.fresh_cache([{"display_name": "Fable", "percent": 90, "resets_at": "old-window"}])

        self.run_main_ok(
            self.payload([{"display_name": "Fable", "utilization": 3, "resets_at": "new-window"}])
        )

        self.assertEqual(
            self.read_cache()["weekly_scoped"],
            [{"display_name": "Fable", "percent": 3, "resets_at": "new-window"}],
        )

    def test_snapshot_from_earlier_window_does_not_overwrite_newer_window(self) -> None:
        for cached_reset, new_reset in (
            ("2026-10-08T00:00:00Z", "2026-10-01T00:00:00Z"),
            (1791417600, 1790812800),
        ):
            with self.subTest(cached_reset=cached_reset):
                original_text = self.fresh_cache(
                    [{"display_name": "Fable", "percent": 3, "resets_at": cached_reset}]
                )

                self.run_main_ok(
                    self.payload(
                        [{"display_name": "Fable", "utilization": 95, "resets_at": new_reset}]
                    )
                )

                self.assertEqual(self.cache_file.read_text(), original_text)
                self.cache_file.unlink()

    def test_snapshot_from_later_window_overwrites(self) -> None:
        self.fresh_cache(
            [{"display_name": "Fable", "percent": 95, "resets_at": "2026-10-01T00:00:00Z"}]
        )

        self.run_main_ok(
            self.payload(
                [{"display_name": "Fable", "utilization": 3, "resets_at": "2026-10-08T00:00:00Z"}]
            )
        )

        self.assertEqual(
            self.read_cache()["weekly_scoped"],
            [{"display_name": "Fable", "percent": 3, "resets_at": "2026-10-08T00:00:00Z"}],
        )

    def test_resets_at_of_different_types_or_empty_is_not_compared(self) -> None:
        for cached_reset, new_reset in (
            ("2026-10-08T00:00:00Z", 1790812800),
            ("2026-10-08T00:00:00Z", ""),
        ):
            with self.subTest(cached_reset=cached_reset, new_reset=new_reset):
                self.fresh_cache(
                    [{"display_name": "Fable", "percent": 3, "resets_at": cached_reset}]
                )
                entry = {"display_name": "Fable", "utilization": 95, "resets_at": new_reset}

                self.run_main_ok(self.payload([entry]))

                self.assertEqual(
                    self.read_cache()["weekly_scoped"],
                    [{"display_name": "Fable", "percent": 95, "resets_at": new_reset}],
                )
                self.cache_file.unlink()

    def test_monotonic_guard_does_not_apply_to_stale_cache(self) -> None:
        self.fresh_cache(
            [{"display_name": "Fable", "percent": 42, "resets_at": "r1"}],
            age_sec=301 + CLOCK_SLACK_SEC,
        )

        self.run_main_ok(
            self.payload([{"display_name": "Fable", "utilization": 41.7, "resets_at": "r1"}])
        )

        self.assertEqual(
            self.read_cache()["weekly_scoped"],
            [{"display_name": "Fable", "percent": 41.7, "resets_at": "r1"}],
        )

    def make_lock(self, name: str, stale: bool, owner: str | None = None) -> Path:
        lock_dir = self.cache_dir / name
        lock_dir.mkdir(parents=True)
        if owner is not None:
            (lock_dir / "owner").write_text(owner)
        if stale:
            old = int(time.time()) - 121 - CLOCK_SLACK_SEC
            os.utime(lock_dir, (old, old))
        return lock_dir

    def lock_identity(self, lock_dir: Path) -> str:
        owner_file = lock_dir / "owner"
        owner = owner_file.read_text() if owner_file.exists() else ""
        return f"{owner}-{int(lock_dir.stat().st_mtime)}"

    def run_bash_with_lib(self, script: str) -> subprocess.CompletedProcess[bytes]:
        # weekly-scoped-limits.sh を source して内部関数を直接呼ぶ。
        return subprocess.run(
            ["bash", "-c", 'source "$1"\n' + script, "test", str(WEEKLY_SCOPED_LIB)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            check=False,
            timeout=30,
        )

    def test_no_write_while_another_writer_holds_the_lock(self) -> None:
        lock_dir = self.make_lock(".write.lock", stale=False, owner="other")

        self.run_main_ok(self.payload([{"display_name": "Fable", "utilization": 5}]))

        self.assertFalse(self.cache_file.exists())
        # 他の書き手の lock は解放しない。
        self.assertEqual((lock_dir / "owner").read_text(), "other")

    def test_stale_lock_is_taken_over(self) -> None:
        lock_dir = self.make_lock(".write.lock", stale=True, owner="crashed")

        self.run_main_ok(self.payload([{"display_name": "Fable", "utilization": 5}]))

        self.assertEqual(
            self.read_cache()["weekly_scoped"],
            [{"display_name": "Fable", "percent": 5, "resets_at": ""}],
        )
        self.assertFalse(lock_dir.exists())
        self.assertEqual(list(self.cache_dir.glob(".write.lock.takeover.*")), [])

    def test_stale_lock_is_not_taken_over_while_takeover_lock_is_held(self) -> None:
        lock_dir = self.make_lock(".write.lock", stale=True, owner="crashed")
        takeover_dir = self.make_lock(
            f".write.lock.takeover.{self.lock_identity(lock_dir)}", stale=False
        )

        self.run_main_ok(self.payload([{"display_name": "Fable", "utilization": 5}]))

        self.assertFalse(self.cache_file.exists())
        self.assertEqual((lock_dir / "owner").read_text(), "crashed")
        self.assertTrue(takeover_dir.is_dir())

    def test_stale_takeover_lock_is_cleaned_and_takeover_retried_on_next_render(self) -> None:
        # 奪取中に強制終了して残った奪取用 lock は、削除した描画では奪取せず、次の描画で奪取する。
        lock_dir = self.make_lock(".write.lock", stale=True, owner="crashed")
        leftover = self.make_lock(
            f".write.lock.takeover.{self.lock_identity(lock_dir)}", stale=True
        )
        stdin = self.payload([{"display_name": "Fable", "utilization": 5}])

        self.run_main_ok(stdin)
        self.assertFalse(self.cache_file.exists())
        self.assertFalse(leftover.exists())

        self.run_main_ok(stdin)
        self.assertEqual(
            self.read_cache()["weekly_scoped"],
            [{"display_name": "Fable", "percent": 5, "resets_at": ""}],
        )
        self.assertFalse(lock_dir.exists())

    def test_takeover_aborts_when_lock_identity_changed(self) -> None:
        # 奪取用 lock を取った後に lock が作り直されていた (他の書き手が奪取済み) 場合は奪取しない。
        lock_dir = self.make_lock(".write.lock", stale=True, owner="crashed")
        result = self.run_bash_with_lib(
            "weekly_scoped_write_lock_identity() {\n"
            # $(...) はサブシェルで実行されるため、呼び出し回数はファイルの有無で数える。
            '  if [ ! -e "$TMPDIR/identity-called" ]; then\n'
            '    : > "$TMPDIR/identity-called"; printf "crashed-1"\n'
            '  else printf "other-2"; fi\n'
            "}\n"
            'weekly_scoped_acquire_write_lock "$(date +%s)" && exit 3\n'
            "exit 0\n"
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual((lock_dir / "owner").read_text(), "crashed")

    def test_release_keeps_lock_owned_by_another_writer(self) -> None:
        # lock 保持中に奪取され、所有者トークンが他の書き手のものに変わった場合を再現する。
        result = self.run_bash_with_lib(
            'weekly_scoped_acquire_write_lock "$(date +%s)" || exit 1\n'
            'printf other > "$WEEKLY_SCOPED_WRITE_LOCK_DIR/owner"\n'
            "weekly_scoped_release_write_lock\n"
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        lock_dir = self.cache_dir / ".write.lock"
        self.assertEqual((lock_dir / "owner").read_text(), "other")

    def test_worker_write_skips_when_cache_updated_after_worker_start(self) -> None:
        original_text = self.fresh_cache(
            [{"display_name": "Fable", "percent": 50, "resets_at": "r"}], age_sec=5
        )
        worker_start_fetched_at = int(time.time()) - 10000

        result = self.run_bash_with_lib(
            f"weekly_scoped_worker_write {worker_start_fetched_at} "
            f"weekly_scoped_record_failure {worker_start_fetched_at} 0\n"
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.cache_file.read_text(), original_text)
        self.assertFalse((self.cache_dir / ".write.lock").exists())

    def test_worker_write_proceeds_when_cache_not_updated(self) -> None:
        fetched_at = int(time.time()) - 10000
        self.write_cache(
            {
                "fetched_at": fetched_at,
                "consecutive_failures": 1,
                "next_attempt_at": fetched_at + 60,
                "weekly_scoped": [{"display_name": "Fable", "percent": 50, "resets_at": "r"}],
            }
        )

        result = self.run_bash_with_lib(
            f"weekly_scoped_worker_write {fetched_at} weekly_scoped_record_failure {fetched_at} 1\n"
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        cache = self.read_cache()
        self.assertEqual(cache["fetched_at"], fetched_at)
        self.assertEqual(cache["consecutive_failures"], 2)
        self.assertFalse((self.cache_dir / ".write.lock").exists())

    def run_fetch_worker_with_token_failure(self, write_lock_held: bool) -> None:
        # kick が取得した fetch lock を worker が引き継いだ状態を作り、token 取得の失敗
        # (実環境の認証情報・API に触れないようスタブで失敗させる) で worker を終了させる。
        fetched_at = int(time.time()) - 10000
        self.write_cache(
            {
                "fetched_at": fetched_at,
                "consecutive_failures": 1,
                "next_attempt_at": fetched_at + 60,
                "weekly_scoped": [],
            }
        )
        self.make_lock(".fetch.lock", stale=False)
        if write_lock_held:
            self.make_lock(".write.lock", stale=False, owner="other")

        result = self.run_bash_with_lib(
            "WEEKLY_SCOPED_WRITE_LOCK_WORKER_TRIES=2\n"
            "weekly_scoped_read_token() { return 1; }\n"
            "weekly_scoped_fetch_worker\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_fetch_worker_releases_fetch_lock_after_recording(self) -> None:
        self.run_fetch_worker_with_token_failure(write_lock_held=False)

        self.assertEqual(self.read_cache()["consecutive_failures"], 2)
        self.assertFalse((self.cache_dir / ".fetch.lock").exists())

    def test_fetch_worker_keeps_fetch_lock_when_write_lock_unavailable(self) -> None:
        # 書き込めず next_attempt_at を進められないため、fetch lock を残して次の kick を止める。
        self.run_fetch_worker_with_token_failure(write_lock_held=True)

        self.assertEqual(self.read_cache()["consecutive_failures"], 1)
        self.assertTrue((self.cache_dir / ".fetch.lock").is_dir())

    def test_worker_write_gives_up_while_lock_is_held(self) -> None:
        fetched_at = int(time.time()) - 10000
        self.write_cache(
            {
                "fetched_at": fetched_at,
                "consecutive_failures": 1,
                "next_attempt_at": fetched_at + 60,
                "weekly_scoped": [],
            }
        )
        original_text = self.cache_file.read_text()
        self.make_lock(".write.lock", stale=False, owner="other")

        result = self.run_bash_with_lib(
            "WEEKLY_SCOPED_WRITE_LOCK_WORKER_TRIES=2\n"
            f"weekly_scoped_worker_write {fetched_at} weekly_scoped_record_failure {fetched_at} 1\n"
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.cache_file.read_text(), original_text)

    def test_unparseable_cache_is_rewritten(self) -> None:
        self.cache_dir.mkdir(parents=True)
        self.cache_file.write_text("{not json")

        self.run_main_ok(self.payload([{"display_name": "Fable", "utilization": 5}]))

        self.assertEqual(
            self.read_cache()["weekly_scoped"],
            [{"display_name": "Fable", "percent": 5, "resets_at": ""}],
        )

    def test_no_write_when_no_entry_is_extracted(self) -> None:
        # stale かつ内容の異なる cache を置く (書き出し経路が動けば必ず書き換わる状態)。
        # next_attempt_at を未来にして cache 経路の background fetch も起動させない。
        now = int(time.time())
        self.write_cache(
            {
                "fetched_at": now - 10000,
                "consecutive_failures": 2,
                "next_attempt_at": now + 10000,
                "weekly_scoped": [{"display_name": "Cached", "percent": 99, "resets_at": ""}],
            }
        )
        original_text = self.cache_file.read_text()

        for model_scoped in (
            None,
            [],
            [{"display_name": "", "utilization": 10}, {"display_name": "X", "utilization": "1"}],
        ):
            with self.subTest(model_scoped=model_scoped):
                result = self.run_main_ok(self.payload(model_scoped))
                self.assertEqual(self.cache_file.read_text(), original_text)
                # 0 件時は cache 経路のデータが 3 行目に表示される (現行のデータ優先順位)。
                self.assertIn("Cached", result.stdout.decode())

    def test_write_failure_does_not_affect_output(self) -> None:
        stdin = self.payload([{"display_name": "Fable", "utilization": 12}])
        normal = self.run_main_ok(stdin)

        # cache ディレクトリの位置を通常ファイルにして mkdir を失敗させる。
        broken_home = Path(self.tmp.name) / "broken-cache"
        broken_home.write_text("")
        self.env["XDG_CACHE_HOME"] = str(broken_home)

        failed = self.run_main_ok(stdin)
        self.assertEqual(failed.stdout, normal.stdout)

    def test_line3_prefers_stdin_over_cache(self) -> None:
        fetched_at = int(time.time()) - 10
        self.write_cache(
            {
                "fetched_at": fetched_at,
                "consecutive_failures": 0,
                "next_attempt_at": fetched_at + 300,
                "weekly_scoped": [{"display_name": "Cached", "percent": 99, "resets_at": ""}],
            }
        )

        result = self.run_main_ok(self.payload([{"display_name": "Fable", "utilization": 12}]))

        out = result.stdout.decode()
        self.assertIn("Fable", out)
        self.assertNotIn("Cached", out)


if __name__ == "__main__":
    unittest.main()
