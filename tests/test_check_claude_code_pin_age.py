"""scripts/check_claude_code_pin_age.py の単体テスト。

スクリプトの契約:

- `pin_lag_days(release_times, pinned, latest)`: `npm view <pkg> time --json` の
  「version → ISO 8601 公開日時」dict から、latest の公開日時 − pinned の公開日時 を
  日数 (切り捨て) で返す。どちらかが dict に無ければ None
- `build_message(pinned, latest, lag_days)`: lag が `MAX_LAG_DAYS` (30) 以上なら
  GitHub Actions の warning 注釈 `::warning::` で始まる 1 行、30 未満なら `::warning::`
  で始まらない通常メッセージ、None なら skip を示すメッセージを返す
- `main(argv, run_npm=...)`: argv の先頭に固定バージョンを受け取り、注入された
  `run_npm(args: list[str]) -> str` (npm に渡す引数列を受け取り stdout を返す。失敗時は
  例外を送出) で npm を呼ぶ。npm の失敗・JSON 不正・公開日時の欠落は skip として扱い、
  どの場合も 0 を返す

テストは npm・ネットワークを呼ばず、偽の run_npm を注入する。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/check_claude_code_pin_age.py"
MODULE_NAME = "check_claude_code_pin_age"

PACKAGE_NAME = "@anthropic-ai/claude-code"
WARNING_PREFIX = "::warning::"
PINNED_VERSION = "2.1.210"
LATEST_VERSION = "2.1.280"
RELEASE_TIMES = {
    "created": "2025-02-24T00:00:00.000Z",
    "modified": "2026-09-20T09:00:00.000Z",
    PINNED_VERSION: "2026-07-14T10:00:00.000Z",
    LATEST_VERSION: "2026-09-20T09:00:00.000Z",
}
# 2026-07-14T10:00Z から 2026-09-20T09:00Z までは 67 日 23 時間
EXPECTED_LAG_DAYS = 67


def load_pin_age_module(test_case: unittest.TestCase) -> ModuleType:
    """スクリプトを読み込む。未作成なら ERROR ではなく FAIL として報告させる。"""

    test_case.assertTrue(
        SCRIPT_PATH.exists(), f"{SCRIPT_PATH.relative_to(ROOT)} does not exist"
    )
    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT_PATH)
    test_case.assertIsNotNone(spec, f"cannot build an import spec for {SCRIPT_PATH}")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def fake_npm(
    release_times_output: str, latest_output: str, calls: list[list[str]]
) -> Callable[[list[str]], str]:
    """`npm view <pkg> time --json` と `npm view <pkg> version` に応答する偽の run_npm。"""

    def run_npm(args: list[str]) -> str:
        calls.append(list(args))
        if "view" not in args or PACKAGE_NAME not in args:
            raise AssertionError(f"unexpected npm arguments: {args}")
        if "time" in args:
            return release_times_output
        if "version" in args:
            return latest_output
        raise AssertionError(f"unexpected npm arguments: {args}")

    return run_npm


def failing_npm(error: Exception) -> Callable[[list[str]], str]:
    def run_npm(args: list[str]) -> str:
        raise error

    return run_npm


class PinAgeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.pin_age = load_pin_age_module(self)

    def run_main(self, run_npm: Callable[[list[str]], str]) -> tuple[int, str]:
        """main を実行し、戻り値と stdout + stderr の出力を返す。"""

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = self.pin_age.main([PINNED_VERSION], run_npm=run_npm)
        return exit_code, stdout.getvalue() + stderr.getvalue()

    def assert_has_no_warning_line(self, output: str) -> None:
        for line in output.splitlines():
            self.assertFalse(
                line.startswith(WARNING_PREFIX), f"unexpected warning: {line!r}"
            )

    def assert_reports_skip(self, output: str) -> None:
        self.assertIn(
            "skip", output.lower(), f"output must say the check was skipped: {output!r}"
        )


class MaxLagDaysTest(PinAgeTestCase):
    def test_threshold_is_thirty_days(self) -> None:
        self.assertEqual(self.pin_age.MAX_LAG_DAYS, 30)


class PinLagDaysTest(PinAgeTestCase):
    def test_returns_whole_days_between_pinned_and_latest_release(self) -> None:
        self.assertEqual(
            self.pin_age.pin_lag_days(RELEASE_TIMES, PINNED_VERSION, LATEST_VERSION),
            EXPECTED_LAG_DAYS,
        )

    def test_exactly_thirty_days_is_thirty(self) -> None:
        release_times = {
            "1.0.0": "2026-08-01T00:00:00.000Z",
            "1.0.1": "2026-08-31T00:00:00.000Z",
        }
        self.assertEqual(self.pin_age.pin_lag_days(release_times, "1.0.0", "1.0.1"), 30)

    def test_twenty_nine_days_and_twenty_three_hours_rounds_down(self) -> None:
        release_times = {
            "1.0.0": "2026-08-01T00:00:00.000Z",
            "1.0.1": "2026-08-30T23:00:00.000Z",
        }
        self.assertEqual(self.pin_age.pin_lag_days(release_times, "1.0.0", "1.0.1"), 29)

    def test_same_version_has_no_lag(self) -> None:
        self.assertEqual(
            self.pin_age.pin_lag_days(RELEASE_TIMES, LATEST_VERSION, LATEST_VERSION), 0
        )

    def test_missing_pinned_release_time_returns_none(self) -> None:
        self.assertIsNone(
            self.pin_age.pin_lag_days(RELEASE_TIMES, "2.1.999", LATEST_VERSION)
        )

    def test_missing_latest_release_time_returns_none(self) -> None:
        self.assertIsNone(
            self.pin_age.pin_lag_days(RELEASE_TIMES, PINNED_VERSION, "2.1.999")
        )


class BuildMessageTest(PinAgeTestCase):
    def test_lag_at_threshold_is_a_single_line_warning_naming_both_versions(
        self,
    ) -> None:
        message = self.pin_age.build_message(PINNED_VERSION, LATEST_VERSION, 30)
        self.assertTrue(message.startswith(WARNING_PREFIX), message)
        self.assertIn(PINNED_VERSION, message)
        self.assertIn(LATEST_VERSION, message)
        self.assertNotIn("\n", message.rstrip("\n"))

    def test_lag_below_threshold_is_not_a_warning(self) -> None:
        message = self.pin_age.build_message(PINNED_VERSION, LATEST_VERSION, 29)
        self.assertFalse(message.startswith(WARNING_PREFIX), message)

    def test_unknown_lag_reports_skip_without_warning(self) -> None:
        message = self.pin_age.build_message(PINNED_VERSION, LATEST_VERSION, None)
        self.assertFalse(message.startswith(WARNING_PREFIX), message)
        self.assert_reports_skip(message)


class MainTest(PinAgeTestCase):
    def test_stale_pin_emits_warning_annotation_and_exits_zero(self) -> None:
        calls: list[list[str]] = []
        exit_code, output = self.run_main(
            fake_npm(json.dumps(RELEASE_TIMES), f"{LATEST_VERSION}\n", calls)
        )
        self.assertEqual(exit_code, 0)
        warning_lines = [
            line for line in output.splitlines() if line.startswith(WARNING_PREFIX)
        ]
        self.assertEqual(len(warning_lines), 1, output)
        self.assertIn(PINNED_VERSION, warning_lines[0])
        self.assertIn(LATEST_VERSION, warning_lines[0])
        self.assertTrue(
            any("time" in call and "--json" in call for call in calls), calls
        )
        self.assertTrue(any("version" in call for call in calls), calls)

    def test_npm_non_zero_exit_is_skipped_and_exits_zero(self) -> None:
        error = subprocess.CalledProcessError(
            1, ["npm", "view", PACKAGE_NAME, "time", "--json"]
        )
        exit_code, output = self.run_main(failing_npm(error))
        self.assertEqual(exit_code, 0)
        self.assert_has_no_warning_line(output)
        self.assert_reports_skip(output)

    def test_npm_missing_is_skipped_and_exits_zero(self) -> None:
        exit_code, output = self.run_main(failing_npm(FileNotFoundError("npm")))
        self.assertEqual(exit_code, 0)
        self.assert_has_no_warning_line(output)
        self.assert_reports_skip(output)

    def test_invalid_release_times_json_is_skipped_and_exits_zero(self) -> None:
        exit_code, output = self.run_main(
            fake_npm("not json", f"{LATEST_VERSION}\n", [])
        )
        self.assertEqual(exit_code, 0)
        self.assert_has_no_warning_line(output)
        self.assert_reports_skip(output)

    def test_unpublished_pinned_version_is_skipped_and_exits_zero(self) -> None:
        release_times = {
            key: value for key, value in RELEASE_TIMES.items() if key != PINNED_VERSION
        }
        exit_code, output = self.run_main(
            fake_npm(json.dumps(release_times), f"{LATEST_VERSION}\n", [])
        )
        self.assertEqual(exit_code, 0)
        self.assert_has_no_warning_line(output)
        self.assert_reports_skip(output)

    def test_pin_equal_to_latest_emits_no_warning_and_exits_zero(self) -> None:
        exit_code, output = self.run_main(
            fake_npm(json.dumps(RELEASE_TIMES), f"{PINNED_VERSION}\n", [])
        )
        self.assertEqual(exit_code, 0)
        self.assert_has_no_warning_line(output)


if __name__ == "__main__":
    unittest.main()
