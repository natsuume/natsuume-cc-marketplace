from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUSLINE_LIB = ROOT / "plugins" / "natsuume-statusline" / "statusline" / "lib.sh"
ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")


class StatuslineDisplayWidthTest(unittest.TestCase):
    def run_function(
        self,
        function: str,
        text: str,
        *args: str,
        env: dict[str, str] | None = None,
    ) -> str:
        shell_args = " ".join(f'"${index}"' for index in range(2, 2 + len(args) + 1))
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'source "$1"; {function} {shell_args}',
                "statusline-width-test",
                str(STATUSLINE_LIB),
                text,
                *args,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result.stdout.decode()

    def visible_width(self, text: str) -> int:
        return int(self.run_function("visible_length", text))

    def test_visible_length_counts_terminal_cells_and_ignores_ansi(self) -> None:
        colored = "\x1b[01;34m日本語\x1b[00m/abc"
        self.assertEqual(self.visible_width(colored), 10)
        self.assertEqual(self.visible_width("e\u0301"), 1)
        self.assertEqual(self.visible_width("🚀"), 2)

    def test_truncate_visible_never_emits_a_wide_character_past_limit(self) -> None:
        colored = "\x1b[01;34m日本語abc\x1b[00m"

        truncated = self.run_function("truncate_visible", colored, "5")

        self.assertEqual(ANSI_SGR.sub("", truncated), "日本")
        self.assertLessEqual(self.visible_width(truncated), 5)
        self.assertTrue(truncated.endswith("\x1b[00m"))

    def test_ascii_width_and_truncation_are_unchanged(self) -> None:
        self.assertEqual(self.visible_width("abcde"), 5)
        truncated = self.run_function("truncate_visible", "abcdef", "5")
        self.assertEqual(ANSI_SGR.sub("", truncated), "abcde")

    def test_c_locale_is_normalized_for_width_operations(self) -> None:
        env = os.environ.copy()
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        env["_STATUSLINE_FORCE_PYTHON_WIDTH"] = "1"

        self.assertEqual(self.run_function("visible_length", "日本語", env=env), "6")
        truncated = self.run_function("truncate_visible", "日本語", "5", env=env)
        self.assertEqual(ANSI_SGR.sub("", truncated), "日本")


if __name__ == "__main__":
    unittest.main()
