from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUSLINE_LIB = ROOT / "plugins" / "natsuume-statusline" / "statusline" / "lib.sh"


class StatuslineHumanizeTokensTest(unittest.TestCase):
    """humanize_tokens の短縮表記を検証する。

    小数 1 桁への丸めで k の値が 1000 に達する範囲 (999,950〜999,999) は、
    "1000k" ではなく M 単位へ繰り上げて表示する。
    """

    def humanize(self, value: str) -> str:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; humanize_tokens "$2"',
                "statusline-humanize-tokens-test",
                str(STATUSLINE_LIB),
                value,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result.stdout.decode()

    def assert_humanized(self, cases: dict[str, str]) -> None:
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(self.humanize(value), expected)

    def test_values_below_1000_are_printed_as_is(self) -> None:
        self.assert_humanized({"0": "0", "512": "512", "999": "999"})

    def test_thousands_use_k_with_one_decimal(self) -> None:
        self.assert_humanized(
            {
                "1000": "1k",
                "75100": "75.1k",
                "200000": "200k",
                "999949": "999.9k",
            }
        )

    def test_values_rounding_to_1000k_roll_over_to_m(self) -> None:
        self.assert_humanized(
            {
                "999950": "1M",
                "999951": "1M",
                "999999": "1M",
            }
        )

    def test_millions_use_m_with_one_decimal(self) -> None:
        self.assert_humanized(
            {
                "1000000": "1M",
                "1049999": "1M",
                "1500000": "1.5M",
            }
        )

    def test_non_integer_input_is_passed_through(self) -> None:
        self.assert_humanized({"": "", "abc": "abc", "12.5": "12.5"})


if __name__ == "__main__":
    unittest.main()
