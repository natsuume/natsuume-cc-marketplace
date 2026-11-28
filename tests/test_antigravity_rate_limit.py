"""antigravity-rate-limit plugin の契約テスト。

対象は次のとおり:

- `plugins/antigravity-rate-limit/scripts/antigravity-rate-limit.sh` の挙動
  (`ScriptBehaviorTestCase` を継承するクラス群)
  - 引数の validation (`--max-used-percent <N>` のみ 1 回まで受理し、N は 10 進整数・
    最大 3 桁・0〜100)。違反時は agy を起動しない
  - 依存 (agy / jq) の不在。jq が無いときは agy を起動しない
  - agy に渡す引数 (`-p /usage --output-format json --print-timeout 60s`) と、agy の
    非 0 終了 (終了コード 2 を含む) を取得失敗として扱うこと。そのとき stderr に出す
    agy の stderr は先頭 5 行まで
  - agy の応答の検証 (JSON がちょうど 1 つ・status・num_turns・command.name・groups・
    各グループの buckets 配列・bucket)
  - 正規化した JSON の出力 (usedPercent の丸め、未使用・欠損の resetTime、文字列でない
    name / description、空の buckets)
  - 閾値判定 (`--max-used-percent` 指定時の exit 0 / 2 と境界値。空の buckets は判定の
    対象が無いだけ)
- plugin の構造 (`PluginStructureTest`): plugin.json・marketplace.json・リポジトリ直下
  README・plugin README の version と配布メタデータ、skill の frontmatter

script の実行では、PATH を一時ディレクトリの bin だけに閉じる。bin には許可した外部
コマンドの symlink と、fake `agy` (受け取った引数を記録し、環境変数で指定した stdout・
stderr・exit code を返す shell script) を置く。HOME も一時ディレクトリに向け、実環境の
agy と `~/.gemini` に触れない。
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "antigravity-rate-limit"
PLUGIN_VERSION = "0.1.0"
PLUGIN_DESCRIPTION = "Antigravity (agy) の利用枠を取得する"
PLUGIN_KEYWORDS = [
    "antigravity",
    "agy",
    "rate-limit",
    "usage-limit",
    "quota",
    "skill",
]
PLUGIN_DIR = ROOT / "plugins" / PLUGIN_NAME
PLUGIN_JSON = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
PLUGIN_README = PLUGIN_DIR / "README.md"
SKILL_MD = PLUGIN_DIR / "skills" / "status" / "SKILL.md"
SCRIPT = PLUGIN_DIR / "scripts" / "antigravity-rate-limit.sh"
SCRIPT_RELATIVE_PATH = "scripts/antigravity-rate-limit.sh"
SKILL_COMMAND = f"/{PLUGIN_NAME}:status"
MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"
REPO_README = ROOT / "README.md"

BASH = shutil.which("bash") or "/bin/bash"
JQ = shutil.which("jq")
SCRIPT_TIMEOUT_SECONDS = 30

# script が agy に渡す引数 (順序を含めて固定する)。
EXPECTED_AGY_ARGS = [
    "-p",
    "/usage",
    "--output-format",
    "json",
    "--print-timeout",
    "60s",
]
# 付けると `/usage` が通常の prompt として送られる (quota を消費する) 引数。
FORBIDDEN_AGY_ARG = "--disable-slash-commands"
THRESHOLD_OPTION = "--max-used-percent"

# PATH を閉じた bin に symlink する外部コマンド。agy と jq は含めず、ケースごとに置く。
PATH_TOOLS = (
    "bash",
    "sh",
    "cat",
    "env",
    "printf",
    "test",
    "true",
    "false",
    "tr",
    "sed",
    "grep",
    "awk",
    "head",
    "tail",
    "wc",
    "cut",
    "sort",
    "uniq",
    "tee",
    "od",
    "expr",
    "dirname",
    "basename",
    "mktemp",
    "mkdir",
    "rmdir",
    "rm",
    "cp",
    "mv",
    "ls",
    "chmod",
    "touch",
    "date",
    "sleep",
    "kill",
    "id",
    "uname",
)

# fake agy が 1 回の起動ごとに呼び出し記録へ書く区切り行。続く行が受け取った引数。
CALL_SEPARATOR = "=== fake agy call ==="
FAKE_AGY_SCRIPT = """#!/bin/sh
{
  printf '%s\\n' "$FAKE_AGY_CALL_SEPARATOR"
  printf '%s\\n' "$@"
} >> "$FAKE_AGY_CALL_LOG"
cat "$FAKE_AGY_STDERR_FILE" >&2
cat "$FAKE_AGY_STDOUT_FILE"
exit "$FAKE_AGY_EXIT_CODE"
"""

UNUSED_RESET_TIME = "1970-01-01T00:00:00Z"

# `agy -p "/usage" --output-format json --print-timeout 60s` の stdout
# (2026-11-27 agy 1.5.18 / WSL2 で実測。説明文は一部省略)。
MEASURED_USAGE_RESPONSE: dict[str, Any] = {
    "conversation_id": "",
    "status": "SUCCESS",
    "response": "Gemini Models\tWeekly Limit Remaining\t99%\t2026-12-04T09:00:59Z\n...",
    "duration_seconds": 0,
    "num_turns": 0,
    "usage": {
        "input_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "cache_read_tokens": 0,
        "total_tokens": 0,
    },
    "command": {
        "name": "usage",
        "data": {
            "description": (
                "Within each group, models share a weekly limit and a 5-hour limit. ..."
            ),
            "groups": [
                {
                    "name": "Gemini Models",
                    "description": "Models within this group: Gemini 4 Neon, ...",
                    "buckets": [
                        {
                            "id": "gemini-weekly",
                            "name": "Weekly Limit Remaining",
                            "window": "weekly",
                            "remaining_fraction": 0.9880305528640747,
                            "reset_time": "2026-12-04T09:00:59Z",
                            "description": "... fully refresh in 6 days, 23 hours.",
                        },
                        {
                            "id": "gemini-5h",
                            "name": "Five Hour Limit Remaining",
                            "window": "5h",
                            "remaining_fraction": 0.9821221232414246,
                            "reset_time": "2026-11-27T14:00:59Z",
                        },
                    ],
                },
                {
                    "name": "Claude and GPT models",
                    "description": (
                        "Models within this group: Claude Opus 5.5, "
                        "Claude Sonnet 5.5, GPT-OSS 120B"
                    ),
                    "buckets": [
                        {
                            "id": "3p-weekly",
                            "name": "Weekly Limit Remaining",
                            "window": "weekly",
                            "remaining_fraction": 1,
                            "reset_time": UNUSED_RESET_TIME,
                        },
                        {
                            "id": "3p-5h",
                            "name": "Five Hour Limit Remaining",
                            "window": "5h",
                            "remaining_fraction": 1,
                            "reset_time": UNUSED_RESET_TIME,
                        },
                    ],
                },
            ],
        },
    },
}

# MEASURED_USAGE_RESPONSE に対して script が stdout に出す正規化 JSON。
EXPECTED_NORMALIZED_OUTPUT: dict[str, Any] = {
    "description": (
        "Within each group, models share a weekly limit and a 5-hour limit. ..."
    ),
    "groups": [
        {
            "name": "Gemini Models",
            "description": "Models within this group: Gemini 4 Neon, ...",
            "buckets": [
                {
                    "id": "gemini-weekly",
                    "name": "Weekly Limit Remaining",
                    "window": "weekly",
                    "remainingFraction": 0.9880305528640747,
                    "usedPercent": 1.2,
                    "resetTime": "2026-12-04T09:00:59Z",
                    "description": "... fully refresh in 6 days, 23 hours.",
                },
                {
                    "id": "gemini-5h",
                    "name": "Five Hour Limit Remaining",
                    "window": "5h",
                    "remainingFraction": 0.9821221232414246,
                    "usedPercent": 1.8,
                    "resetTime": "2026-11-27T14:00:59Z",
                    "description": None,
                },
            ],
        },
        {
            "name": "Claude and GPT models",
            "description": (
                "Models within this group: Claude Opus 5.5, "
                "Claude Sonnet 5.5, GPT-OSS 120B"
            ),
            "buckets": [
                {
                    "id": "3p-weekly",
                    "name": "Weekly Limit Remaining",
                    "window": "weekly",
                    "remainingFraction": 1,
                    "usedPercent": 0,
                    "resetTime": None,
                    "description": None,
                },
                {
                    "id": "3p-5h",
                    "name": "Five Hour Limit Remaining",
                    "window": "5h",
                    "remainingFraction": 1,
                    "usedPercent": 0,
                    "resetTime": None,
                    "description": None,
                },
            ],
        },
    ],
}


def measured_response() -> dict[str, Any]:
    return copy.deepcopy(MEASURED_USAGE_RESPONSE)


def response_with_fractions(fractions_by_group: list[list[float]]) -> dict[str, Any]:
    """bucket の remaining_fraction だけを指定した応答 (グループ・bucket の数も指定どおり)。"""
    response = measured_response()
    groups = []
    for group_index, fractions in enumerate(fractions_by_group):
        buckets = [
            {
                "id": f"group{group_index}-bucket{bucket_index}",
                "name": f"Bucket {bucket_index}",
                "window": "weekly" if bucket_index % 2 == 0 else "5h",
                "remaining_fraction": fraction,
                "reset_time": "2026-12-04T09:00:59Z",
            }
            for bucket_index, fraction in enumerate(fractions)
        ]
        groups.append(
            {
                "name": f"Group {group_index}",
                "description": f"Group {group_index} description",
                "buckets": buckets,
            }
        )
    response["command"]["data"]["groups"] = groups
    return response


def target_bucket(response: dict[str, Any]) -> dict[str, Any]:
    """検証の対象にする bucket (2 つ目のグループの 2 つ目の bucket)。

    先頭以外の bucket を壊すことで、script がすべてのグループ・bucket を検証することを
    確かめる。
    """
    return response["command"]["data"]["groups"][1]["buckets"][1]


def used_percents(output: dict[str, Any]) -> list[list[Any]]:
    return [
        [bucket["usedPercent"] for bucket in group["buckets"]]
        for group in output["groups"]
    ]


class ScriptBehaviorTestCase(unittest.TestCase):
    """fake agy と閉じた PATH で script を実行する共通処理。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.script_tmpdir = self.tmp / "tmp"
        self.script_tmpdir.mkdir()
        self.call_log = self.tmp / "agy-calls.log"
        self.agy_stdout_file = self.tmp / "agy-stdout"
        self.agy_stderr_file = self.tmp / "agy-stderr"

    def make_bin(self, *, with_agy: bool, with_jq: bool) -> Path:
        bin_dir = self.tmp / f"bin-agy{int(with_agy)}-jq{int(with_jq)}"
        if bin_dir.is_dir():
            return bin_dir
        bin_dir.mkdir()
        for tool in PATH_TOOLS:
            found = shutil.which(tool)
            if found is not None:
                (bin_dir / tool).symlink_to(found)
        if with_jq and JQ is not None:
            (bin_dir / "jq").symlink_to(JQ)
        if with_agy:
            fake_agy = bin_dir / "agy"
            fake_agy.write_text(FAKE_AGY_SCRIPT, encoding="utf-8")
            fake_agy.chmod(0o755)
        return bin_dir

    def run_script(
        self,
        *args: str,
        agy_stdout: str = "",
        agy_stderr: str = "",
        agy_exit_code: int = 0,
        with_agy: bool = True,
        with_jq: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        bin_dir = self.make_bin(with_agy=with_agy, with_jq=with_jq)
        self.call_log.unlink(missing_ok=True)
        self.agy_stdout_file.write_text(agy_stdout, encoding="utf-8")
        self.agy_stderr_file.write_text(agy_stderr, encoding="utf-8")

        env = os.environ.copy()
        for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "CLAUDE_PLUGIN_ROOT"):
            env.pop(name, None)
        env.update(
            {
                "PATH": str(bin_dir),
                "HOME": str(self.home),
                "TMPDIR": str(self.script_tmpdir),
                "FAKE_AGY_CALL_SEPARATOR": CALL_SEPARATOR,
                "FAKE_AGY_CALL_LOG": str(self.call_log),
                "FAKE_AGY_STDOUT_FILE": str(self.agy_stdout_file),
                "FAKE_AGY_STDERR_FILE": str(self.agy_stderr_file),
                "FAKE_AGY_EXIT_CODE": str(agy_exit_code),
            }
        )
        return subprocess.run(
            [BASH, str(SCRIPT), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            env=env,
            timeout=SCRIPT_TIMEOUT_SECONDS,
            check=False,
        )

    def run_with_response(
        self, response: Any, *args: str
    ) -> subprocess.CompletedProcess[str]:
        return self.run_script(
            *args, agy_stdout=json.dumps(response, ensure_ascii=False)
        )

    def agy_calls(self) -> list[list[str]]:
        """fake agy の起動ごとの引数の一覧。"""
        if not self.call_log.exists():
            return []
        calls: list[list[str]] = []
        for line in self.call_log.read_text(encoding="utf-8").splitlines():
            if line == CALL_SEPARATOR:
                calls.append([])
            elif calls:
                calls[-1].append(line)
        return calls

    def assert_agy_called_once_with_expected_args(self) -> None:
        self.assertEqual([EXPECTED_AGY_ARGS], self.agy_calls())

    def assert_agy_not_called(self) -> None:
        self.assertEqual([], self.agy_calls(), "agy が起動された")

    def assert_fetch_failure(self, result: subprocess.CompletedProcess[str]) -> None:
        """exit 1 で stdout に何も出さず、stderr に理由を出す。"""
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)
        self.assertNotEqual("", result.stderr.strip())

    def assert_success_output(
        self, result: subprocess.CompletedProcess[str], expected_exit_code: int
    ) -> dict[str, Any]:
        self.assertEqual(expected_exit_code, result.returncode, result.stderr)
        output = json.loads(result.stdout)
        self.assertIsInstance(output, dict)
        return output


@unittest.skipUnless(JQ, "script の実行には jq が必要")
class ArgumentValidationTest(ScriptBehaviorTestCase):
    """`--max-used-percent <N>` 以外の引数・2 回目の指定・N の validation 違反は exit 1。"""

    def assert_usage_error(self, *args: str) -> None:
        result = self.run_with_response(measured_response(), *args)
        self.assert_fetch_failure(result)
        self.assertIn(THRESHOLD_OPTION, result.stderr, "usage を stderr に出していない")
        self.assert_agy_not_called()

    def test_invalid_threshold_values_are_rejected_before_running_agy(self) -> None:
        for value in (
            "",
            "-1",
            "101",
            "50.5",
            "1000",
            "0050",
            "abc",
            " 50",
            "50 ",
            "+5",
            "1e2",
            # 行単位の照合 (grep 等) では数字だけの行を含むため通ってしまう値。
            "5\n0",
            "50\n",
        ):
            with self.subTest(value=value):
                self.assert_usage_error(THRESHOLD_OPTION, value)

    def test_missing_threshold_value_is_rejected(self) -> None:
        self.assert_usage_error(THRESHOLD_OPTION)

    def test_unknown_arguments_are_rejected(self) -> None:
        for args in (
            ("--foo",),
            ("50",),
            (f"{THRESHOLD_OPTION}=50",),
            (THRESHOLD_OPTION, "50", "extra"),
            (FORBIDDEN_AGY_ARG,),
        ):
            with self.subTest(args=args):
                self.assert_usage_error(*args)

    def test_repeated_threshold_option_is_rejected(self) -> None:
        # どちらの値も単独では有効な値にし、2 回目の指定そのものが拒否されることを確かめる。
        for first, second in (("50", "50"), ("50", "60")):
            with self.subTest(first=first, second=second):
                self.assert_usage_error(
                    THRESHOLD_OPTION, first, THRESHOLD_OPTION, second
                )


@unittest.skipUnless(JQ, "script の実行には jq が必要")
class DependencyTest(ScriptBehaviorTestCase):
    """agy / jq が PATH に無ければ exit 1 で、stderr で区別できる。"""

    def run_without(self, *, agy: bool, jq: bool) -> subprocess.CompletedProcess[str]:
        result = self.run_script(
            agy_stdout=json.dumps(measured_response()), with_agy=agy, with_jq=jq
        )
        bin_dir = self.make_bin(with_agy=agy, with_jq=jq)
        if not agy:
            self.assertIsNone(shutil.which("agy", path=str(bin_dir)))
        if not jq:
            self.assertIsNone(shutil.which("jq", path=str(bin_dir)))
        return result

    def test_missing_agy_is_a_fetch_failure(self) -> None:
        result = self.run_without(agy=False, jq=True)
        self.assert_fetch_failure(result)
        self.assertIn("agy", result.stderr)

    def test_missing_jq_is_a_fetch_failure_without_running_agy(self) -> None:
        result = self.run_without(agy=True, jq=False)
        self.assert_fetch_failure(result)
        self.assertIn("jq", result.stderr)
        self.assert_agy_not_called()

    def test_missing_agy_and_missing_jq_are_reported_differently(self) -> None:
        missing_agy = self.run_without(agy=False, jq=True)
        missing_jq = self.run_without(agy=True, jq=False)
        self.assertIn("agy", missing_agy.stderr)
        self.assertIn("jq", missing_jq.stderr)
        self.assertNotEqual(missing_agy.stderr, missing_jq.stderr)


@unittest.skipUnless(JQ, "script の実行には jq が必要")
class AgyInvocationTest(ScriptBehaviorTestCase):
    """agy を print mode の `/usage` で 1 回だけ起動し、非 0 終了は取得失敗にする。"""

    def test_agy_is_called_once_with_usage_print_mode_arguments(self) -> None:
        result = self.run_with_response(measured_response())
        self.assert_success_output(result, 0)
        self.assert_agy_called_once_with_expected_args()
        self.assertNotIn(FORBIDDEN_AGY_ARG, self.agy_calls()[0])

    def test_threshold_option_does_not_change_agy_arguments(self) -> None:
        result = self.run_with_response(measured_response(), THRESHOLD_OPTION, "50")
        self.assert_success_output(result, 0)
        self.assert_agy_called_once_with_expected_args()

    def test_nonzero_agy_exit_reports_exit_code_and_agy_stderr(self) -> None:
        result = self.run_script(
            agy_stdout="",
            agy_stderr="fake-agy-error-first-line\nfake-agy-error-second-line\n",
            agy_exit_code=37,
        )
        self.assert_fetch_failure(result)
        self.assertIn("37", result.stderr)
        self.assertIn("fake-agy-error-first-line", result.stderr)
        self.assert_agy_called_once_with_expected_args()

    def test_agy_exit_code_two_is_not_reported_as_threshold_exceeded(self) -> None:
        """agy の終了コード 2 は閾値超過 (exit 2) ではなく取得失敗 (exit 1)。"""
        result = self.run_script(
            THRESHOLD_OPTION,
            "50",
            agy_stdout=json.dumps(measured_response()),
            agy_stderr="fake-agy-api-key-error\n",
            agy_exit_code=2,
        )
        self.assert_fetch_failure(result)
        self.assertIn("fake-agy-api-key-error", result.stderr)
        self.assert_agy_called_once_with_expected_args()

    def test_agy_stderr_is_limited_to_first_five_lines(self) -> None:
        lines = [f"fake-agy-stderr-line-{number}" for number in range(1, 9)]
        result = self.run_script(
            agy_stderr="".join(f"{line}\n" for line in lines), agy_exit_code=1
        )
        self.assert_fetch_failure(result)
        for line in lines[:5]:
            self.assertIn(line, result.stderr)
        for line in lines[5:]:
            self.assertNotIn(line, result.stderr)


@unittest.skipUnless(JQ, "script の実行には jq が必要")
class ResponseValidationTest(ScriptBehaviorTestCase):
    """agy の応答が契約を満たさなければ exit 1 で、stdout に何も出さない。"""

    def assert_rejected(self, agy_stdout: str) -> subprocess.CompletedProcess[str]:
        result = self.run_script(agy_stdout=agy_stdout)
        self.assert_fetch_failure(result)
        self.assert_agy_called_once_with_expected_args()
        return result

    def assert_rejected_response(
        self, response: Any
    ) -> subprocess.CompletedProcess[str]:
        return self.assert_rejected(json.dumps(response, ensure_ascii=False))

    def test_output_that_is_not_json_is_rejected(self) -> None:
        valid = json.dumps(measured_response())
        for agy_stdout in (
            "",
            "\n  \n",
            "not json\n",
            valid[: len(valid) // 2],
            "Gemini Models\tWeekly Limit Remaining\t99%\t2026-12-04T09:00:59Z\n",
        ):
            with self.subTest(agy_stdout=agy_stdout[:40]):
                self.assert_rejected(agy_stdout)

    def test_concatenated_json_documents_are_rejected(self) -> None:
        valid = json.dumps(measured_response())
        for agy_stdout in (
            f"{valid}\n{valid}\n",
            f"{valid}{valid}",
            f"{valid}\n{{}}\n",
            f"{valid} 0",
        ):
            with self.subTest(agy_stdout=agy_stdout[-20:]):
                self.assert_rejected(agy_stdout)

    def test_json_that_is_not_a_success_object_is_rejected(self) -> None:
        for agy_stdout in ("[]", "null", '"SUCCESS"', "0"):
            with self.subTest(agy_stdout=agy_stdout):
                self.assert_rejected(agy_stdout)

    def test_non_success_status_is_rejected(self) -> None:
        for status in ("ERROR", "success", "", None):
            with self.subTest(status=status):
                response = measured_response()
                response["status"] = status
                self.assert_rejected_response(response)
        response = measured_response()
        del response["status"]
        self.assert_rejected_response(response)

    def test_nonzero_num_turns_reports_possible_quota_consumption(self) -> None:
        response = measured_response()
        response["num_turns"] = 1
        result = self.assert_rejected_response(response)
        self.assertIn("num_turns", result.stderr)
        self.assertIn("quota", result.stderr)

    def test_quota_warning_is_not_hidden_by_status_violation(self) -> None:
        # agent turn が実行された応答は、status が SUCCESS でなくても quota の警告を出す。
        response = measured_response()
        response["num_turns"] = 2
        response["status"] = "ERROR"
        result = self.assert_rejected_response(response)
        self.assertIn("num_turns", result.stderr)
        self.assertIn("quota", result.stderr)

    def test_missing_or_non_numeric_num_turns_is_rejected(self) -> None:
        for num_turns in ("0", None, False):
            with self.subTest(num_turns=num_turns):
                response = measured_response()
                response["num_turns"] = num_turns
                self.assert_rejected_response(response)
        response = measured_response()
        del response["num_turns"]
        self.assert_rejected_response(response)

    def test_non_usage_command_is_rejected(self) -> None:
        for name in ("help", "", None):
            with self.subTest(name=name):
                response = measured_response()
                response["command"]["name"] = name
                self.assert_rejected_response(response)
        with self.subTest(case="command.name missing"):
            response = measured_response()
            del response["command"]["name"]
            self.assert_rejected_response(response)
        with self.subTest(case="command null"):
            response = measured_response()
            response["command"] = None
            self.assert_rejected_response(response)
        with self.subTest(case="command missing"):
            response = measured_response()
            del response["command"]
            self.assert_rejected_response(response)

    def test_missing_or_empty_groups_are_rejected(self) -> None:
        for groups in ([], {}, "groups", None):
            with self.subTest(groups=groups):
                response = measured_response()
                response["command"]["data"]["groups"] = groups
                self.assert_rejected_response(response)
        with self.subTest(case="groups missing"):
            response = measured_response()
            del response["command"]["data"]["groups"]
            self.assert_rejected_response(response)
        with self.subTest(case="data missing"):
            response = measured_response()
            del response["command"]["data"]
            self.assert_rejected_response(response)

    def test_group_without_bucket_array_is_rejected(self) -> None:
        for buckets in ({}, "buckets", None, 1):
            with self.subTest(buckets=buckets):
                response = measured_response()
                response["command"]["data"]["groups"][1]["buckets"] = buckets
                self.assert_rejected_response(response)
        with self.subTest(buckets="missing"):
            response = measured_response()
            del response["command"]["data"]["groups"][1]["buckets"]
            self.assert_rejected_response(response)

    def test_group_that_is_not_an_object_is_rejected(self) -> None:
        for group in ("group", [], None):
            with self.subTest(group=group):
                response = measured_response()
                response["command"]["data"]["groups"][1] = group
                self.assert_rejected_response(response)

    def test_bucket_without_string_id_or_window_is_rejected(self) -> None:
        for field in ("id", "window"):
            for value in (None, 3, ["x"]):
                with self.subTest(field=field, value=value):
                    response = measured_response()
                    target_bucket(response)[field] = value
                    self.assert_rejected_response(response)
            with self.subTest(field=field, value="missing"):
                response = measured_response()
                del target_bucket(response)[field]
                self.assert_rejected_response(response)

    def test_bucket_without_fraction_in_range_is_rejected(self) -> None:
        for value in (-0.01, 1.01, 2, "0.5", None, True):
            with self.subTest(remaining_fraction=value):
                response = measured_response()
                target_bucket(response)["remaining_fraction"] = value
                self.assert_rejected_response(response)
        with self.subTest(remaining_fraction="missing"):
            response = measured_response()
            del target_bucket(response)["remaining_fraction"]
            self.assert_rejected_response(response)

    def test_bucket_that_is_not_an_object_is_rejected(self) -> None:
        for bucket in ("bucket", 1, None):
            with self.subTest(bucket=bucket):
                response = measured_response()
                response["command"]["data"]["groups"][1]["buckets"][1] = bucket
                self.assert_rejected_response(response)


@unittest.skipUnless(JQ, "script の実行には jq が必要")
class NormalizedOutputTest(ScriptBehaviorTestCase):
    """取得に成功したら、正規化した JSON を stdout に出す。"""

    def test_measured_response_is_normalized(self) -> None:
        output = self.assert_success_output(
            self.run_with_response(measured_response()), 0
        )
        self.assertEqual(EXPECTED_NORMALIZED_OUTPUT, output)

    def test_used_percent_is_rounded_to_one_decimal_place(self) -> None:
        cases = (
            (0.9880305528640747, 1.2),
            (0.9821221232414246, 1.8),
            (1, 0),
            (0, 100),
            (0.5, 50),
            (0.123, 87.7),
            (0.3333, 66.7),
            (0.99999, 0),
            # (1 - 0.9995) * 1000 は 2 進誤差で 0.4999... になる。0.05 の四捨五入は 0.1
            (0.9995, 0.1),
        )
        output = self.assert_success_output(
            self.run_with_response(
                response_with_fractions([[fraction for fraction, _ in cases]])
            ),
            0,
        )
        self.assertEqual([[expected for _, expected in cases]], used_percents(output))
        for bucket, (fraction, _) in zip(output["groups"][0]["buckets"], cases):
            with self.subTest(remaining_fraction=fraction):
                self.assertEqual(fraction, bucket["remainingFraction"])
                self.assertIsInstance(bucket["usedPercent"], (int, float))
                self.assertNotIsInstance(bucket["usedPercent"], bool)

    def test_unused_bucket_reset_time_is_null(self) -> None:
        output = self.assert_success_output(
            self.run_with_response(measured_response()), 0
        )
        reset_times = {
            bucket["id"]: bucket["resetTime"]
            for group in output["groups"]
            for bucket in group["buckets"]
        }
        self.assertEqual(
            {
                "gemini-weekly": "2026-12-04T09:00:59Z",
                "gemini-5h": "2026-11-27T14:00:59Z",
                "3p-weekly": None,
                "3p-5h": None,
            },
            reset_times,
        )

    def test_bucket_description_is_kept_or_null(self) -> None:
        output = self.assert_success_output(
            self.run_with_response(measured_response()), 0
        )
        descriptions = {
            bucket["id"]: bucket["description"]
            for group in output["groups"]
            for bucket in group["buckets"]
        }
        self.assertEqual(
            {
                "gemini-weekly": "... fully refresh in 6 days, 23 hours.",
                "gemini-5h": None,
                "3p-weekly": None,
                "3p-5h": None,
            },
            descriptions,
        )

    def test_full_usage_without_threshold_exits_zero(self) -> None:
        output = self.assert_success_output(
            self.run_with_response(response_with_fractions([[0, 0]])), 0
        )
        self.assertEqual([[100, 100]], used_percents(output))

    def test_non_string_texts_and_missing_reset_time_become_null(self) -> None:
        response = measured_response()
        data = response["command"]["data"]
        data["description"] = 1
        data["groups"][0]["name"] = None
        del data["groups"][0]["description"]
        weekly, five_hour = data["groups"][0]["buckets"]
        weekly["name"] = ["Weekly"]
        weekly["description"] = {"text": "x"}
        del weekly["reset_time"]
        del five_hour["name"]
        five_hour["reset_time"] = 1764597659
        output = self.assert_success_output(self.run_with_response(response), 0)

        self.assertIsNone(output["description"])
        group = output["groups"][0]
        self.assertIsNone(group["name"])
        self.assertIsNone(group["description"])
        for bucket in group["buckets"]:
            with self.subTest(bucket=bucket["id"]):
                self.assertIsNone(bucket["name"])
                self.assertIsNone(bucket["description"])
                self.assertIsNone(bucket["resetTime"])
        self.assertEqual(EXPECTED_NORMALIZED_OUTPUT["groups"][1], output["groups"][1])

    def test_group_with_empty_buckets_is_kept(self) -> None:
        output = self.assert_success_output(
            self.run_with_response(response_with_fractions([[0.5], []])), 0
        )
        self.assertEqual([[50], []], used_percents(output))
        self.assertEqual(
            {"name": "Group 1", "description": "Group 1 description", "buckets": []},
            output["groups"][1],
        )


@unittest.skipUnless(JQ, "script の実行には jq が必要")
class ThresholdTest(ScriptBehaviorTestCase):
    """`--max-used-percent <N>`: usedPercent が N を超える bucket があれば exit 2。"""

    def run_threshold(
        self, response: dict[str, Any], threshold: str, expected_exit_code: int
    ) -> dict[str, Any]:
        result = self.run_with_response(response, THRESHOLD_OPTION, threshold)
        return self.assert_success_output(result, expected_exit_code)

    def test_used_percent_equal_to_threshold_exits_zero(self) -> None:
        output = self.run_threshold(response_with_fractions([[0.5, 1]]), "50", 0)
        self.assertEqual([[50, 0]], used_percents(output))

    def test_used_percent_above_threshold_exits_two_with_json(self) -> None:
        output = self.run_threshold(response_with_fractions([[0.5, 1]]), "49", 2)
        self.assertEqual([[50, 0]], used_percents(output))

    def test_measured_response_against_thresholds(self) -> None:
        # 実測応答の usedPercent の最大は 1.8。
        for threshold, expected_exit_code in (
            ("0", 2),
            ("1", 2),
            ("2", 0),
            ("100", 0),
        ):
            with self.subTest(threshold=threshold):
                output = self.run_threshold(
                    measured_response(), threshold, expected_exit_code
                )
                self.assertEqual(EXPECTED_NORMALIZED_OUTPUT, output)

    def test_zero_threshold_with_unused_buckets_exits_zero(self) -> None:
        self.run_threshold(response_with_fractions([[1, 1], [1, 1]]), "0", 0)

    def test_full_usage_with_threshold_hundred_exits_zero(self) -> None:
        self.run_threshold(response_with_fractions([[0, 0]]), "100", 0)

    def test_comparison_uses_rounded_used_percent(self) -> None:
        # remaining_fraction 0.4996 は使用率 50.04% で、usedPercent は 50 に丸まる。
        output = self.run_threshold(response_with_fractions([[0.4996]]), "50", 0)
        self.assertEqual([[50]], used_percents(output))
        # remaining_fraction 0.4994 は使用率 50.06% で、usedPercent は 50.1 に丸まる。
        output = self.run_threshold(response_with_fractions([[0.4994]]), "50", 2)
        self.assertEqual([[50.1]], used_percents(output))

    def test_bucket_in_any_group_is_checked(self) -> None:
        output = self.run_threshold(
            response_with_fractions([[1, 1], [1, 0.25]]), "50", 2
        )
        self.assertEqual([[0, 0], [0, 75]], used_percents(output))

    def test_empty_buckets_are_not_judged(self) -> None:
        for fractions, threshold, expected_exit_code in (
            ([[0.5], []], "49", 2),
            ([[], [1]], "0", 0),
            ([[], []], "0", 0),
        ):
            with self.subTest(fractions=fractions, threshold=threshold):
                self.run_threshold(
                    response_with_fractions(fractions), threshold, expected_exit_code
                )

    def test_exceeded_output_equals_output_without_threshold(self) -> None:
        response = response_with_fractions([[0.9, 0.2], [0.6]])
        without_threshold = self.assert_success_output(
            self.run_with_response(response), 0
        )
        exceeded = self.run_threshold(response, "10", 2)
        self.assertEqual(without_threshold, exceeded)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(read_text(path))


def skill_frontmatter(text: str) -> dict[str, str]:
    """`---` で囲んだ先頭の frontmatter を `key: value` の対応として読む。"""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise AssertionError("SKILL.md が frontmatter (`---`) で始まらない")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise AssertionError("SKILL.md の frontmatter が閉じていない") from exc
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if not separator:
            raise AssertionError(f"frontmatter の行が `key: value` でない: {line!r}")
        fields[key.strip()] = value.strip()
    return fields


SLOW_FAKE_AGY_SCRIPT = """#!/bin/sh
printf '%s\\n' "$$" > "$FAKE_AGY_PID_FILE"
exec "$FAKE_AGY_SLEEP" 30
"""

# TERM を無視する agy。無視の設定は exec 後の sleep にも引き継がれる。
TERM_IGNORING_FAKE_AGY_SCRIPT = """#!/bin/sh
trap '' TERM
printf '%s\\n' "$$" > "$FAKE_AGY_PID_FILE"
exec "$FAKE_AGY_SLEEP" 30
"""


class SignalHandlingTest(ScriptBehaviorTestCase):
    """応答しない agy を待っている間に script だけがシグナルを受けた場合の後始末。"""

    def wait_for_file(self, path: Path, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if path.exists() and path.read_text(encoding="utf-8").strip():
                return
            time.sleep(0.05)
        raise AssertionError(f"{path} が作られなかった")

    def test_term_stops_waiting_agy_and_removes_work_dir(self) -> None:
        self.assert_signal_stops_agy(SLOW_FAKE_AGY_SCRIPT, max_seconds=5)

    def test_agy_ignoring_term_is_killed_after_grace_period(self) -> None:
        # TERM の転送後 5 秒待っても終わらない agy は KILL で止める。
        self.assert_signal_stops_agy(TERM_IGNORING_FAKE_AGY_SCRIPT, max_seconds=9)

    def test_quit_stops_waiting_agy_and_removes_work_dir(self) -> None:
        self.assert_signal_stops_agy(
            SLOW_FAKE_AGY_SCRIPT, max_seconds=5, script_signal=signal.SIGQUIT
        )

    def assert_signal_stops_agy(
        self,
        fake_agy_script: str,
        max_seconds: float,
        script_signal: signal.Signals = signal.SIGTERM,
    ) -> None:
        sleep_path = shutil.which("sleep")
        self.assertIsNotNone(sleep_path)
        bin_dir = self.make_bin(with_agy=False, with_jq=True)
        slow_agy = bin_dir / "agy"
        slow_agy.write_text(fake_agy_script, encoding="utf-8")
        slow_agy.chmod(0o755)
        pid_file = self.tmp / "agy.pid"
        pid_file.unlink(missing_ok=True)

        env = os.environ.copy()
        env.update(
            {
                "PATH": str(bin_dir),
                "HOME": str(self.home),
                "TMPDIR": str(self.script_tmpdir),
                "FAKE_AGY_PID_FILE": str(pid_file),
                "FAKE_AGY_SLEEP": str(sleep_path),
            }
        )
        process = subprocess.Popen(
            [BASH, str(SCRIPT)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.wait_for_file(pid_file, 10)
        agy_pid = int(pid_file.read_text(encoding="utf-8").strip())

        # script のプロセスだけにシグナルを送る (agy には直接送らない)。
        process.send_signal(script_signal)
        started = time.monotonic()
        process.communicate(timeout=15)
        self.assertLess(time.monotonic() - started, max_seconds)
        self.assertNotEqual(0, process.returncode)

        # script の終了時点で agy も終了している (zombie の回収待ちを除く)。
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(agy_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(agy_pid, signal.SIGKILL)
            self.fail("script の終了後も agy が残っている")

        # 一時ディレクトリが残っていない。
        self.assertEqual([], list(self.script_tmpdir.iterdir()))


class PluginStructureTest(unittest.TestCase):
    """plugin の配布メタデータ・README・skill・script の構造。"""

    def test_plugin_manifest(self) -> None:
        manifest = load_json(PLUGIN_JSON)
        self.assertEqual(PLUGIN_NAME, manifest["name"])
        self.assertEqual(PLUGIN_VERSION, manifest["version"])
        self.assertEqual(PLUGIN_DESCRIPTION, manifest["description"])

    def test_marketplace_entry(self) -> None:
        entries = [
            entry
            for entry in load_json(MARKETPLACE_JSON)["plugins"]
            if entry["name"] == PLUGIN_NAME
        ]
        self.assertEqual(1, len(entries))
        entry = entries[0]
        self.assertEqual(f"./plugins/{PLUGIN_NAME}", entry["source"])
        self.assertEqual(PLUGIN_VERSION, entry["version"])
        self.assertEqual(PLUGIN_DESCRIPTION, entry["description"])
        self.assertEqual(PLUGIN_KEYWORDS, entry["keywords"])

    def test_repository_readme_lists_the_plugin(self) -> None:
        lines = read_text(REPO_README).splitlines()
        row_prefix = f"| [{PLUGIN_NAME}](#{PLUGIN_NAME}) | {PLUGIN_VERSION} |"
        self.assertEqual(1, sum(1 for line in lines if line.startswith(row_prefix)))
        self.assertEqual(1, lines.count(f"## {PLUGIN_NAME}"))

    def test_plugin_readme_version_section(self) -> None:
        lines = read_text(PLUGIN_README).splitlines()
        self.assertIn("## バージョン", lines)
        following = [
            line.strip()
            for line in lines[lines.index("## バージョン") + 1 :]
            if line.strip()
        ]
        self.assertTrue(following, "## バージョン の後に本文が無い")
        self.assertEqual(f"v{PLUGIN_VERSION}", following[0])

    def test_skill_frontmatter(self) -> None:
        fields = skill_frontmatter(read_text(SKILL_MD))
        self.assertEqual("status", fields.get("name"))
        self.assertTrue(fields.get("description"), "description が空")

    def test_skill_runs_the_script(self) -> None:
        text = read_text(SKILL_MD)
        self.assertIn(SKILL_COMMAND, text)
        self.assertIn(SCRIPT_RELATIVE_PATH, text)
        self.assertIn(SCRIPT_RELATIVE_PATH, read_text(PLUGIN_README))

    def test_script_is_a_bash_script_with_valid_syntax(self) -> None:
        self.assertEqual("#!/bin/bash", read_text(SCRIPT).splitlines()[0])
        result = subprocess.run(
            [BASH, "-n", str(SCRIPT)],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
