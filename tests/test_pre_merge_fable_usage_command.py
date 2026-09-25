"""pre-merge-cross-review の Fable 週次枠判定コマンドの契約テスト。

`bin/pre-merge-cross-review-fable-usage` は merge 前に fable-reviewer を起動するかを判定する。
構造は cross-model-advisor の `cross-model-advisor-fable-usage` と同一で、判定は plugin が
保持する `hooks/scripts/lib/fable-weekly-usage.sh` (cross-model-advisor の canonical の
byte-identical コピー。同一性は tests/test_pre_merge_lib_copies.py が検査する) に委ねる。

- 出力は常に 2 行で exit 0 (1 行目 = available / over / unknown、2 行目 = 理由)
- available の 2 行目は fable-reviewer を起動する旨、over / unknown の 2 行目は Fable
  review をスキップする旨を述べる
- 使用率 cache は読むだけで書き換えない
- lib を読み込めない場合は unknown を出力する

判定表は cross-model-advisor の判定コマンドのテスト (tests/test_cross_model_advisor_fable.py
の USAGE_TABLE) を共有する。
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


def _load_advisor_fable_tests():
    """cross-model-advisor の判定コマンドのテストが持つ判定表と cache helper を読み込む。

    `import` 文で書くと「sys.path 操作より前に import 文が来る」という lint 制約
    (E402) に抵触するため、既存テストと同じ importlib 経由の明示 import にする。
    """
    return importlib.import_module("test_cross_model_advisor_fable")


_advisor_fable_tests = _load_advisor_fable_tests()

USAGE_TABLE = _advisor_fable_tests.USAGE_TABLE
CACHE_RELATIVE = _advisor_fable_tests.CACHE_RELATIVE
UNSET = _advisor_fable_tests.UNSET
cache = _advisor_fable_tests.cache
resolve_fetched_at = _advisor_fable_tests.resolve_fetched_at

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "pre-merge-cross-review"
USAGE_COMMAND_NAME = "pre-merge-cross-review-fable-usage"
USAGE_COMMAND = PLUGIN / "bin" / USAGE_COMMAND_NAME

AVAILABLE_REASON_WORD = "fable-reviewer"
SKIP_REASON_WORD = "Fable review をスキップ"


class UsageCommandFileTest(unittest.TestCase):
    def test_command_is_an_executable_bash_script(self) -> None:
        self.assertTrue(USAGE_COMMAND.is_file(), f"{USAGE_COMMAND} が無い")
        self.assertTrue(os.access(USAGE_COMMAND, os.X_OK))
        first_line = USAGE_COMMAND.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual("#!/bin/bash", first_line)


@unittest.skipUnless(shutil.which("jq"), "usage command requires jq")
class UsageCommandDecisionTableTest(unittest.TestCase):
    """判定コマンドの出力 (1 行目 = available / over / unknown、2 行目 = 理由)。"""

    def run_case(
        self, case: dict[str, object], command: Path = USAGE_COMMAND
    ) -> tuple[subprocess.CompletedProcess[str], bool]:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            home = temp / "home"
            cache_home = temp / "cache"
            home.mkdir()
            cache_home.mkdir()
            env = {
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "XDG_CACHE_HOME": str(cache_home),
            }
            if case["threshold"] is not UNSET:
                env["FABLE_WEEKLY_MAX_PERCENT"] = str(case["threshold"])
            cache_path = cache_home / CACHE_RELATIVE
            cache_path.parent.mkdir(parents=True)
            body = resolve_fetched_at(
                cache() if case["cache_body"] is UNSET else case["cache_body"]
            )
            text = (
                case["cache_raw"]
                if case["cache_raw"] is not None
                else json.dumps(body)
            )
            if case["cache_kind"] == "file":
                cache_path.write_text(str(text), encoding="utf-8")
            elif case["cache_kind"] == "symlink":
                target = temp / "real.json"
                target.write_text(str(text), encoding="utf-8")
                cache_path.symlink_to(target)
            before = sorted(p.name for p in cache_path.parent.iterdir())
            before_text = (
                cache_path.read_text(encoding="utf-8")
                if cache_path.is_file()
                else None
            )
            result = subprocess.run(
                ["/bin/bash", str(command)],
                cwd=temp,
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            after = sorted(p.name for p in cache_path.parent.iterdir())
            after_text = (
                cache_path.read_text(encoding="utf-8")
                if cache_path.is_file()
                else None
            )
            return result, (before != after or before_text != after_text)

    def test_decision_table(self) -> None:
        if not USAGE_COMMAND.is_file():
            self.fail(f"{USAGE_COMMAND} が無い")
        violations = []
        for case in USAGE_TABLE:
            label = str(case["label"])
            result, changed = self.run_case(case)
            if result.returncode != 0:
                violations.append(
                    f"{label}: exit {result.returncode} ({result.stderr.strip()})"
                )
            if changed:
                violations.append(f"{label}: cache が書き換えられた")
            lines = result.stdout.splitlines()
            if len(lines) != 2:
                violations.append(f"{label}: 出力が 2 行でない ({result.stdout!r})")
                continue
            if lines[0] != case["status"]:
                violations.append(
                    f"{label}: status が {lines[0]!r} (期待 {case['status']!r})"
                )
            for pattern in case["reason"]:  # type: ignore[union-attr]
                if not re.search(str(pattern), lines[1]):
                    violations.append(f"{label}: 理由に {pattern!r} が無い ({lines[1]})")
            expected_word = (
                AVAILABLE_REASON_WORD
                if case["status"] == "available"
                else SKIP_REASON_WORD
            )
            if expected_word not in lines[1]:
                violations.append(
                    f"{label}: 理由に {expected_word!r} が無い ({lines[1]})"
                )
        self.assertEqual([], violations, "\n".join(violations))

    def test_missing_lib_reports_unknown(self) -> None:
        """lib を欠いた plugin tree のコピーでは unknown を出力する (fail-closed)。"""
        if not USAGE_COMMAND.is_file():
            self.fail(f"{USAGE_COMMAND} が無い")
        with tempfile.TemporaryDirectory() as temporary:
            copied_plugin = Path(temporary) / "plugin"
            shutil.copytree(PLUGIN, copied_plugin, symlinks=True)
            copied_lib = (
                copied_plugin / "hooks" / "scripts" / "lib" / "fable-weekly-usage.sh"
            )
            copied_lib.unlink(missing_ok=True)
            copied_command = copied_plugin / "bin" / USAGE_COMMAND_NAME
            available_case = next(
                case for case in USAGE_TABLE if case["status"] == "available"
            )
            result, changed = self.run_case(available_case, copied_command)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(changed, "cache が書き換えられた")
        lines = result.stdout.splitlines()
        self.assertEqual(2, len(lines), result.stdout)
        self.assertEqual("unknown", lines[0])
        self.assertIn(SKIP_REASON_WORD, lines[1])


if __name__ == "__main__":
    unittest.main()
