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
