from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUSLINE_LIB = ROOT / "plugins" / "natsuume-statusline" / "statusline" / "lib.sh"

FILLED = "█"
EMPTY = "░"


class StatuslineProgressBarTest(unittest.TestCase):
    """progress_bar のバー長が常に width と一致する契約を検証する。

    render_line2 の幅予算はバー長 = bar_width 前提で組まれているため、
    pct が [0, 100] の範囲外でもバー長は width を超えてはならない。
    """

    def render(self, pct: str, width: str) -> str:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; progress_bar "$2" "$3"',
                "statusline-progress-bar-test",
                str(STATUSLINE_LIB),
                pct,
                width,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result.stdout.decode()

    def assert_bar(self, pct: str, width: str, filled: int, empty: int) -> None:
        self.assertEqual(
            self.render(pct, width),
            "[" + FILLED * filled + EMPTY * empty + "]",
        )

    def test_in_range_values_are_unchanged(self) -> None:
        self.assert_bar("0", "10", 0, 10)
        self.assert_bar("57", "10", 5, 5)
        self.assert_bar("100", "10", 10, 0)
        self.assert_bar("50", "20", 10, 10)

    def test_pct_above_100_is_clamped_to_full_bar(self) -> None:
        self.assert_bar("101", "10", 10, 0)
        self.assert_bar("150", "10", 10, 0)
        self.assert_bar("1000", "7", 7, 0)

    def test_negative_pct_is_clamped_to_empty_bar(self) -> None:
        self.assert_bar("-1", "10", 0, 10)
        self.assert_bar("-50", "10", 0, 10)

    def test_bar_length_always_matches_width(self) -> None:
        for pct in ("-200", "-1", "0", "33", "99", "100", "101", "250"):
            for width in ("1", "5", "20"):
                with self.subTest(pct=pct, width=width):
                    bar = self.render(pct, width)
                    self.assertEqual(len(bar), int(width) + 2)


if __name__ == "__main__":
    unittest.main()
