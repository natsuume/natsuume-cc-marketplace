from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUSLINE_DIR = ROOT / "plugins" / "natsuume-statusline" / "statusline"
STATUSLINE_MAIN = STATUSLINE_DIR / "main.sh"

# Claude Code のフッターが statusline 列の外側に取る幅 (paddingX 2 × 2 + columnGap 1)。
FOOTER_RESERVED_COLUMNS = 5

LONG_MODEL_NAME = "Opus 5.5 (1M context)"
ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return ANSI_SGR.sub("", text)


class StatuslineWidthTest(unittest.TestCase):
    """statusline_width が COLUMNS からフッターの予約幅を差し引く契約を検証する。"""

    def width_for(self, columns: str) -> str:
        env = os.environ.copy()
        env["COLUMNS"] = columns
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; statusline_width', "statusline-width-test",
             str(STATUSLINE_DIR / "lib.sh")],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result.stdout.decode()

    def test_reserves_footer_columns(self) -> None:
        self.assertEqual(self.width_for("100"), "95")
        self.assertEqual(self.width_for("80"), "75")

    def test_never_below_one(self) -> None:
        self.assertEqual(self.width_for("5"), "1")
        self.assertEqual(self.width_for("3"), "1")


class GaugeLineContextPercentOnlyTest(unittest.TestCase):
    """バーを削除しても収まらない場合に ctx を使用率のみにする段階 (段階4) を検証する。"""

    def render_line2(self, width: int) -> str:
        reset = str(int(time.time()) + 3500)
        result = subprocess.run(
            [
                "bash",
                "-c",
                'd="$1"; shift; source "$d/lib.sh"; source "$d/gauges.sh"; '
                'source "$d/line2.sh"; TERM_WIDTH="$1"; shift; render_line2 "$@"',
                "statusline-line2-test",
                str(STATUSLINE_DIR),
                str(width),
                LONG_MODEL_NAME,
                "xhigh",
                "45.23",
                "452300",
                "1000000",
                "62.5",
                reset,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return plain(result.stdout.decode())

    def test_tokens_kept_while_bar_removal_suffices(self) -> None:
        line = self.render_line2(65)
        self.assertIn("ctx: 452.3k/1M", line)
        self.assertNotIn("…", line)

    def test_context_shows_percent_only_before_truncation(self) -> None:
        line = self.render_line2(60)
        self.assertIn("ctx: (45%)", line)
        self.assertNotIn("452.3k", line)
        self.assertNotIn("…", line)
        self.assertLessEqual(len(line), 60)


class StatuslineMainFitsFooterTest(unittest.TestCase):
    """main.sh の全行が Claude Code フッターの描画幅 (COLUMNS - 5) に収まることを検証する。"""

    def setUp(self) -> None:
        # HOME / XDG_CACHE_HOME / TMPDIR を一時ディレクトリへ向け、キャッシュ書き込みと
        # 認証情報の参照を実環境から隔離する。
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp_path = Path(self.tmp.name)
        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(tmp_path / "home"),
                "XDG_CACHE_HOME": str(tmp_path / "cache"),
                "TMPDIR": str(tmp_path / "tmp"),
            }
        )
        for name in ("home", "cache", "tmp", "work"):
            (tmp_path / name).mkdir()
        self.work_dir = tmp_path / "work"

    def run_main(self, columns: int) -> str:
        now = int(time.time())
        # model_scoped を非空にして OAuth usage API の background fetch を起動させない。
        payload = {
            "workspace": {"current_dir": str(self.work_dir)},
            "model": {"display_name": LONG_MODEL_NAME},
            "effort": {"level": "xhigh"},
            "context_window": {
                "used_percentage": 45.23,
                "total_input_tokens": 452300,
                "context_window_size": 1000000,
            },
            "rate_limits": {
                "five_hour": {"used_percentage": 62.5, "resets_at": now + 3500},
                "seven_day": {"used_percentage": 31.4, "resets_at": now + 200000},
                "model_scoped": [
                    {"display_name": "Fable", "utilization": 12.5, "resets_at": ""}
                ],
            },
        }
        env = dict(self.env, COLUMNS=str(columns))
        result = subprocess.run(
            ["bash", str(STATUSLINE_MAIN)],
            input=json.dumps(payload).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.work_dir,
            env=env,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return plain(result.stdout.decode())

    def test_every_line_fits_footer_width(self) -> None:
        for columns in range(50, 101):
            with self.subTest(columns=columns):
                output = self.run_main(columns)
                self.assertIn(LONG_MODEL_NAME, output)
                for line in output.split("\n"):
                    self.assertLessEqual(len(line), columns - FOOTER_RESERVED_COLUMNS, line)


if __name__ == "__main__":
    unittest.main()
