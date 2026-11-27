"""antigravity-advisor plugin の wrapper と plugin 構造の契約を検査する。

wrapper (`scripts/run-antigravity-advisor.sh`) の検査は fake `agy` を使う。fake `agy` は
受け取った引数・cwd・`--add-dir` の中身を記録し、環境変数で指定された stdout と
exit code を返す。wrapper に渡す PATH は fake `agy` の置き場所と、wrapper が使う
コマンドへの symlink の置き場所だけで組み立てるため、実 agy は起動しない。

wrapper の一時ディレクトリが残らないことは、TMPDIR をテスト専用の空ディレクトリに
して、終了後も空であることで確かめる。git の work tree 内での検査は、tempfile の
一時ディレクトリに作った git repo で行う。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "antigravity-advisor"
PLUGIN_DIR = ROOT / "plugins" / PLUGIN_NAME
PLUGIN_JSON = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
PLUGIN_README = PLUGIN_DIR / "README.md"
WRAPPER = PLUGIN_DIR / "scripts" / "run-antigravity-advisor.sh"
RUNNER_AGENT = PLUGIN_DIR / "agents" / "antigravity-advisor-runner.md"
CONSULT_SKILL = PLUGIN_DIR / "skills" / "consult" / "SKILL.md"
MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"
REPO_README = ROOT / "README.md"

EXPECTED_VERSION = "0.1.0"
EXPECTED_DESCRIPTION = "Antigravity (agy) へ read-only で相談する"
EXPECTED_KEYWORDS = {
    "antigravity",
    "agy",
    "advisor",
    "second-opinion",
    "skill",
    "gemini",
}
RUNNER_SUBAGENT_TYPE = "antigravity-advisor:antigravity-advisor-runner"

# wrapper が agy に必ず渡す、ファイル書き込み・shell・MCP・URL 取得を拒否する権限設定。
SETTINGS_ARGUMENT = (
    '{"permissions":{"deny":["write_file(*)","command(*)","mcp(*)","read_url(*)"]}}'
)

# runner が plugin cache から wrapper の最新版を探すコマンド。
FALLBACK_LOOKUP_COMMAND = (
    'find "$HOME/.claude/plugins/cache" -path '
    "'*antigravity-advisor*/scripts/run-antigravity-advisor.sh' -type f 2>/dev/null"
    " | awk -F'antigravity-advisor/' '{split($2,p,\"/\");"
    'if(split(p[1],v,".")==3)printf "%06d.%06d.%06d %s\\n",v[1],v[2],v[3],$0}\''
    " | sort -r | head -1 | cut -d' ' -f2-"
)

# wrapper の終了コードと、runner が report に書く Failure class の対応。
FAILURE_CLASS_BY_EXIT = {
    2: "invalid-request",
    3: "agy-unavailable",
    4: "agy-failed",
    5: "workspace-modified",
    6: "incomplete-response",
}
# 複数の失敗条件が同時に成り立つときに採用する exit code の順。
EXIT_PRECEDENCE = "5 (workspace-modified) > 4 (agy-failed) > 6 (incomplete-response)"
FAILURE_CLASSES = (
    "agy-unavailable",
    "agy-failed",
    "incomplete-response",
    "workspace-modified",
    "invalid-request",
    "other",
)

# wrapper を起動する bash。macOS では bash 3.2 の /bin/bash を使い、互換性を検査する。
BASH = "/bin/bash" if Path("/bin/bash").exists() else shutil.which("bash")

# wrapper と fake agy が使いうるコマンド。PATH を組み立てるときに、見つかったものだけを
# symlink で並べる。
TOOL_COMMANDS = (
    "awk",
    "basename",
    "bash",
    "cat",
    "chmod",
    "cmp",
    "cp",
    "cut",
    "date",
    "diff",
    "dirname",
    "env",
    "git",
    "grep",
    "head",
    "id",
    "jq",
    "ls",
    "mkdir",
    "mktemp",
    "mv",
    "od",
    "printf",
    "readlink",
    "realpath",
    "rm",
    "rmdir",
    "sed",
    "sh",
    "sleep",
    "sort",
    "stat",
    "tail",
    "test",
    "touch",
    "tr",
    "uniq",
    "wc",
)

# 受け取った引数・cwd・--add-dir の中身を記録し、環境変数で指定された出力を返す fake agy。
FAKE_AGY_SCRIPT = r"""#!/bin/bash
record_dir="${FAKE_AGY_RECORD_DIR:?}"
printf 'invoked\n' >> "$record_dir/invocations"
: > "$record_dir/args"
for arg in "$@"; do
  printf '%s\0' "$arg" >> "$record_dir/args"
done
pwd -P > "$record_dir/cwd"
add_dir=""
previous=""
for arg in "$@"; do
  if [ "$previous" = "--add-dir" ]; then
    add_dir="$arg"
  fi
  previous="$arg"
done
if [ -n "$add_dir" ] && [ -d "$add_dir" ]; then
  mkdir -p "$record_dir/add-dir"
  cp -R "$add_dir"/. "$record_dir/add-dir"/
fi
if [ -n "${FAKE_AGY_CREATE_FILE:-}" ]; then
  printf 'created by fake agy\n' > "$FAKE_AGY_CREATE_FILE"
fi
if [ -n "${FAKE_AGY_APPEND_FILE:-}" ]; then
  printf 'appended by fake agy\n' >> "$FAKE_AGY_APPEND_FILE"
fi
if [ -n "${FAKE_AGY_COMMIT:-}" ]; then
  git commit --quiet --allow-empty -m "commit by fake agy" > /dev/null 2>&1
fi
if [ -n "${FAKE_AGY_STDERR:-}" ]; then
  printf '%s\n' "$FAKE_AGY_STDERR" >&2
fi
if [ -n "${FAKE_AGY_STDOUT_FILE:-}" ]; then
  cat "$FAKE_AGY_STDOUT_FILE"
fi
exit "${FAKE_AGY_EXIT:-0}"
"""

DEFAULT_REQUEST = (
    "## 目的\n"
    "設定ファイルの読み込み順を決める。REQUEST-BODY-MARKER-4f1c\n"
    "\n"
    "## 問い\n"
    "環境変数と設定ファイルのどちらを優先すべきか。\n"
)

JAPANESE_CHARACTER = re.compile(r"[぀-ヿ㐀-鿿]")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
ISSUE_OR_PR_REFERENCE = re.compile(r"(?<![\w&])#\d+\b")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> dict:
    return json.loads(read_text(path))


def success_payload(response: str, **extra: object) -> dict:
    """agy の成功時の stdout と同じ形の JSON object。"""
    payload: dict = {
        "conversation_id": "conversation-1",
        "status": "SUCCESS",
        "response": response,
        "duration_seconds": 1.5,
        "num_turns": 2,
        "usage": {"input_tokens": 120, "output_tokens": 40},
    }
    payload.update(extra)
    return payload


def frontmatter_fields(text: str) -> dict[str, str]:
    """平坦な `key: value` 形式の frontmatter を辞書にする。"""
    match = FRONTMATTER.match(text)
    if match is None:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def body_after_frontmatter(text: str) -> str:
    match = FRONTMATTER.match(text)
    return text if match is None else text[match.end() :]


def git_environment(home: Path) -> dict[str, str]:
    """利用者の git 設定と GIT_* 変数の影響を受けない、テスト用 git 操作の環境。"""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and key != "ANTIGRAVITY_ADVISOR_MODEL"
    }
    environment.update(
        {
            "HOME": str(home),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    return environment


class WrapperHarness(unittest.TestCase):
    """fake agy と隔離した PATH・TMPDIR で wrapper を実行する共通部分。"""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(os.path.realpath(temporary.name))
        self.home = self.base / "home"
        self.records = self.base / "records"
        self.wrapper_tmpdir = self.base / "wrapper-tmp"
        self.workdir = self.base / "workdir"
        self.fake_bin = self.base / "fake-bin"
        self.agy_stdout_file = self.base / "agy-stdout"
        for directory in (
            self.home,
            self.records,
            self.wrapper_tmpdir,
            self.workdir,
            self.fake_bin,
        ):
            directory.mkdir()
        fake_agy = self.fake_bin / "agy"
        fake_agy.write_text(FAKE_AGY_SCRIPT, encoding="utf-8")
        fake_agy.chmod(0o755)
        self.request = self.base / "request" / "request.md"
        self.request.parent.mkdir()
        self.request.write_text(DEFAULT_REQUEST, encoding="utf-8")

    def tool_bin(self, *, with_jq: bool) -> Path:
        """wrapper が使うコマンドへの symlink だけを置いたディレクトリ。"""
        name = "tool-bin" if with_jq else "tool-bin-without-jq"
        directory = self.base / name
        if directory.exists():
            return directory
        directory.mkdir()
        for command in TOOL_COMMANDS:
            if command == "jq" and not with_jq:
                continue
            found = shutil.which(command)
            if found is not None:
                (directory / command).symlink_to(found)
        return directory

    def wrapper_environment(
        self,
        *,
        with_agy: bool,
        with_jq: bool,
        extra_env: dict[str, str] | None,
    ) -> dict[str, str]:
        path_entries = [str(self.tool_bin(with_jq=with_jq))]
        if with_agy:
            path_entries.insert(0, str(self.fake_bin))
        environment = {
            "PATH": os.pathsep.join(path_entries),
            "HOME": str(self.home),
            "TMPDIR": str(self.wrapper_tmpdir),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CEILING_DIRECTORIES": str(self.base),
            "FAKE_AGY_RECORD_DIR": str(self.records),
        }
        if extra_env:
            environment.update(extra_env)
        return environment

    def run_wrapper(
        self,
        args: list[str] | None = None,
        *,
        cwd: Path | None = None,
        agy_stdout: str | dict | list | None = None,
        agy_exit: int = 0,
        with_agy: bool = True,
        with_jq: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if agy_stdout is None:
            agy_stdout = success_payload("既定の助言")
        if isinstance(agy_stdout, (dict, list)):
            agy_stdout = json.dumps(agy_stdout, ensure_ascii=False)
        self.agy_stdout_file.write_text(agy_stdout, encoding="utf-8")
        fake_env = {
            "FAKE_AGY_STDOUT_FILE": str(self.agy_stdout_file),
            "FAKE_AGY_EXIT": str(agy_exit),
        }
        fake_env.update(extra_env or {})
        if args is None:
            args = [str(self.request)]
        return subprocess.run(
            [BASH, str(WRAPPER), *args],
            cwd=str(cwd or self.workdir),
            env=self.wrapper_environment(
                with_agy=with_agy, with_jq=with_jq, extra_env=fake_env
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            check=False,
            timeout=60,
        )

    def agy_invocation_count(self) -> int:
        invocations = self.records / "invocations"
        if not invocations.exists():
            return 0
        return len(read_text(invocations).splitlines())

    def recorded_args(self) -> list[str]:
        raw = (self.records / "args").read_bytes().decode("utf-8")
        self.assertTrue(raw.endswith("\0"), "fake agy の引数の記録が壊れている")
        return raw[:-1].split("\0")

    def recorded_cwd(self) -> str:
        return read_text(self.records / "cwd").strip()

    def assert_succeeded(
        self, result: subprocess.CompletedProcess[str], response: str
    ) -> None:
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(
            result.stdout,
            (response, response + "\n"),
            "stdout には .response の本文だけを出す",
        )
        self.assertEqual(1, self.agy_invocation_count(), "agy は 1 回だけ起動する")

    def assert_failed(
        self,
        result: subprocess.CompletedProcess[str],
        expected_exit: int,
        *,
        agy_invoked: bool,
    ) -> None:
        self.assertEqual(expected_exit, result.returncode, result.stderr)
        self.assertEqual("", result.stdout, "失敗時は stdout に何も出さない")
        self.assertRegex(
            result.stderr, JAPANESE_CHARACTER, "失敗の理由を stderr に日本語で出す"
        )
        self.assertEqual(
            1 if agy_invoked else 0,
            self.agy_invocation_count(),
            "agy の起動回数",
        )

    def assert_wrapper_tmpdir_is_empty(self) -> None:
        self.assertEqual(
            [],
            sorted(path.name for path in self.wrapper_tmpdir.iterdir()),
            "wrapper の一時ディレクトリを残さない",
        )


class WrapperSuccessTest(WrapperHarness):
    """成功時に .response だけを出し、agy を決まった引数で起動する。"""

    def test_prints_only_the_response_text(self) -> None:
        response = (
            '推奨: 環境変数を優先する。\n理由: "上書き" が容易\tで、`%s` や \\ も含む。'
        )
        result = self.run_wrapper(agy_stdout=success_payload(response))
        self.assert_succeeded(result, response)

    def test_empty_denied_actions_list_is_a_success(self) -> None:
        response = "拒否された操作は無い"
        result = self.run_wrapper(
            agy_stdout=success_payload(response, denied_actions=[])
        )
        self.assert_succeeded(result, response)

    def test_agy_command_line_puts_the_prompt_flag_last(self) -> None:
        result = self.run_wrapper()
        self.assert_succeeded(result, "既定の助言")
        args = self.recorded_args()
        self.assertIn("--add-dir", args)
        add_dir = args[args.index("--add-dir") + 1]
        self.assertEqual(
            [
                "--mode",
                "plan",
                "--output-format",
                "json",
                "--settings",
                SETTINGS_ARGUMENT,
                "--add-dir",
                add_dir,
                "--print-timeout",
                "25m",
                "-p",
                args[-1],
            ],
            args,
        )

    def test_settings_deny_writes_shell_mcp_and_url_access(self) -> None:
        for model in ("", "gemini-3-pro"):
            with self.subTest(model=model):
                self.setUp()
                result = self.run_wrapper(
                    extra_env={"ANTIGRAVITY_ADVISOR_MODEL": model}
                )
                self.assert_succeeded(result, "既定の助言")
                args = self.recorded_args()
                self.assertEqual(1, args.count("--settings"))
                settings = args[args.index("--settings") + 1]
                self.assertEqual(SETTINGS_ARGUMENT, settings)
                self.assertEqual(
                    {
                        "permissions": {
                            "deny": [
                                "write_file(*)",
                                "command(*)",
                                "mcp(*)",
                                "read_url(*)",
                            ]
                        }
                    },
                    json.loads(settings),
                )
                self.assertLess(args.index("--settings"), args.index("-p"))

    def test_add_dir_holds_only_the_request_copy(self) -> None:
        result = self.run_wrapper()
        self.assert_succeeded(result, "既定の助言")
        snapshot = self.records / "add-dir"
        files = [path for path in sorted(snapshot.rglob("*")) if path.is_file()]
        self.assertEqual(1, len(files), [str(path) for path in files])
        self.assertEqual(self.request.read_bytes(), files[0].read_bytes())

    def test_add_dir_is_a_wrapper_directory_under_tmpdir(self) -> None:
        result = self.run_wrapper()
        self.assert_succeeded(result, "既定の助言")
        args = self.recorded_args()
        add_dir = Path(args[args.index("--add-dir") + 1])
        self.assertTrue(add_dir.is_absolute(), add_dir)
        self.assertEqual(
            Path(os.path.realpath(self.wrapper_tmpdir)),
            Path(os.path.realpath(add_dir)).parent,
            "一時ディレクトリは TMPDIR の直下に作る",
        )

    def test_prompt_points_to_the_copied_request_without_its_body(self) -> None:
        result = self.run_wrapper()
        self.assert_succeeded(result, "既定の助言")
        args = self.recorded_args()
        add_dir = args[args.index("--add-dir") + 1]
        prompt = args[-1]
        snapshot = self.records / "add-dir"
        request_bytes = self.request.read_bytes()
        copies = [
            path.relative_to(snapshot).as_posix()
            for path in sorted(snapshot.rglob("*"))
            if path.is_file() and path.read_bytes() == request_bytes
        ]
        self.assertTrue(copies, "--add-dir に request のコピーが無い")
        self.assertTrue(
            any(f"{add_dir}/{copy}" in prompt for copy in copies),
            f"prompt にコピーした request の絶対パスが無い: {prompt!r}",
        )
        for arg in args:
            self.assertNotIn(
                "REQUEST-BODY-MARKER-4f1c", arg, "request の本文を引数に載せない"
            )

    def test_prompt_is_an_english_read_only_instruction(self) -> None:
        result = self.run_wrapper()
        self.assert_succeeded(result, "既定の助言")
        prompt = self.recorded_args()[-1]
        self.assertNotRegex(prompt, JAPANESE_CHARACTER, "prompt は英語の固定文")
        for keyword in ("read-only", "shell", "language"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, prompt.lower())

    def test_agy_runs_in_the_callers_cwd(self) -> None:
        result = self.run_wrapper(cwd=self.workdir)
        self.assert_succeeded(result, "既定の助言")
        self.assertEqual(os.path.realpath(self.workdir), self.recorded_cwd())

    def test_request_path_with_spaces_is_accepted(self) -> None:
        request = self.base / "dir with space" / "request file.md"
        request.parent.mkdir()
        request.write_text(DEFAULT_REQUEST, encoding="utf-8")
        result = self.run_wrapper([str(request)])
        self.assert_succeeded(result, "既定の助言")

    def test_symlink_in_parent_directory_is_accepted(self) -> None:
        linked_parent = self.base / "linked-parent"
        linked_parent.symlink_to(self.request.parent, target_is_directory=True)
        result = self.run_wrapper([str(linked_parent / self.request.name)])
        self.assert_succeeded(result, "既定の助言")


class WrapperTemporaryDirectoryTest(WrapperHarness):
    """wrapper の一時ディレクトリを成功・失敗のどちらでも残さない。"""

    def test_removed_after_success(self) -> None:
        result = self.run_wrapper()
        self.assert_succeeded(result, "既定の助言")
        args = self.recorded_args()
        add_dir = Path(args[args.index("--add-dir") + 1])
        self.assertFalse(add_dir.exists(), f"{add_dir} が残っている")
        self.assert_wrapper_tmpdir_is_empty()

    def test_removed_after_agy_failure(self) -> None:
        result = self.run_wrapper(agy_exit=9)
        self.assert_failed(result, 4, agy_invoked=True)
        self.assert_wrapper_tmpdir_is_empty()

    def test_removed_after_incomplete_response(self) -> None:
        result = self.run_wrapper(agy_stdout="not json")
        self.assert_failed(result, 6, agy_invoked=True)
        self.assert_wrapper_tmpdir_is_empty()

    def test_removed_after_invalid_model(self) -> None:
        result = self.run_wrapper(extra_env={"ANTIGRAVITY_ADVISOR_MODEL": "bad model"})
        self.assert_failed(result, 2, agy_invoked=False)
        self.assert_wrapper_tmpdir_is_empty()


class WrapperModelEnvironmentTest(WrapperHarness):
    """ANTIGRAVITY_ADVISOR_MODEL が非空で妥当なときだけ --model を渡す。"""

    def test_unset_model_passes_no_model_flag(self) -> None:
        result = self.run_wrapper()
        self.assert_succeeded(result, "既定の助言")
        self.assertNotIn("--model", self.recorded_args())

    def test_empty_model_passes_no_model_flag(self) -> None:
        result = self.run_wrapper(extra_env={"ANTIGRAVITY_ADVISOR_MODEL": ""})
        self.assert_succeeded(result, "既定の助言")
        self.assertNotIn("--model", self.recorded_args())

    def test_valid_model_is_passed_after_the_output_format(self) -> None:
        for model in ("gemini-3-pro", "Model_v1.5", "a"):
            with self.subTest(model=model):
                self.setUp()
                result = self.run_wrapper(
                    extra_env={"ANTIGRAVITY_ADVISOR_MODEL": model}
                )
                self.assert_succeeded(result, "既定の助言")
                args = self.recorded_args()
                self.assertEqual(
                    ["--mode", "plan", "--output-format", "json", "--model", model],
                    args[:6],
                )
                self.assertEqual(1, args.count("--model"))
                self.assertEqual("-p", args[-2])

    def test_invalid_model_is_rejected_without_starting_agy(self) -> None:
        for model in ("gemini pro", "a/b", "x;y", "$(id)", "model\nnext", "モデル"):
            with self.subTest(model=model):
                self.setUp()
                result = self.run_wrapper(
                    extra_env={"ANTIGRAVITY_ADVISOR_MODEL": model}
                )
                self.assert_failed(result, 2, agy_invoked=False)


class WrapperArgumentValidationTest(WrapperHarness):
    """引数がちょうど 1 つの、symlink でない空でない通常ファイルであることを要求する。"""

    def test_zero_arguments(self) -> None:
        result = self.run_wrapper([])
        self.assert_failed(result, 2, agy_invoked=False)

    def test_two_arguments(self) -> None:
        result = self.run_wrapper([str(self.request), str(self.request)])
        self.assert_failed(result, 2, agy_invoked=False)

    def test_missing_file(self) -> None:
        result = self.run_wrapper([str(self.base / "missing.md")])
        self.assert_failed(result, 2, agy_invoked=False)

    def test_empty_file(self) -> None:
        empty = self.base / "empty.md"
        empty.write_bytes(b"")
        result = self.run_wrapper([str(empty)])
        self.assert_failed(result, 2, agy_invoked=False)

    def test_symlink_to_a_regular_file(self) -> None:
        link = self.base / "link.md"
        link.symlink_to(self.request)
        result = self.run_wrapper([str(link)])
        self.assert_failed(result, 2, agy_invoked=False)

    def test_directory(self) -> None:
        result = self.run_wrapper([str(self.request.parent)])
        self.assert_failed(result, 2, agy_invoked=False)


class WrapperRequiredCommandTest(WrapperHarness):
    """agy と jq が PATH に無ければ exit 3 にする。"""

    def test_missing_agy(self) -> None:
        result = self.run_wrapper(with_agy=False)
        self.assert_failed(result, 3, agy_invoked=False)
        self.assertIn("agy", result.stderr)

    def test_missing_jq(self) -> None:
        result = self.run_wrapper(with_jq=False)
        self.assert_failed(result, 3, agy_invoked=False)
        self.assertIn("jq", result.stderr)

    def test_missing_agy_and_missing_jq_are_reported_differently(self) -> None:
        missing_agy = self.run_wrapper(with_agy=False)
        self.assertEqual(3, missing_agy.returncode, missing_agy.stderr)
        missing_jq = self.run_wrapper(with_jq=False)
        self.assertEqual(3, missing_jq.returncode, missing_jq.stderr)
        self.assertNotEqual(missing_agy.stderr, missing_jq.stderr)


class WrapperAgyFailureTest(WrapperHarness):
    """working tree が変わっていなければ、agy の非 0 終了は stdout の内容に依らず exit 4。"""

    def test_agy_failure_wins_over_incomplete_response(self) -> None:
        result = self.run_wrapper(agy_stdout="not json", agy_exit=9)
        self.assert_failed(result, 4, agy_invoked=True)

    def test_non_zero_agy_exit(self) -> None:
        for agy_exit in (1, 9, 124):
            with self.subTest(agy_exit=agy_exit):
                self.setUp()
                result = self.run_wrapper(
                    agy_stdout=success_payload("出してはいけない助言"),
                    agy_exit=agy_exit,
                    extra_env={"FAKE_AGY_STDERR": "fake agy failure"},
                )
                self.assert_failed(result, 4, agy_invoked=True)


class WrapperIncompleteResponseTest(WrapperHarness):
    """agy の stdout が完全な成功応答でなければ exit 6 にする。"""

    def test_incomplete_responses(self) -> None:
        cases: dict[str, str | dict | list] = {
            "not json": "not json",
            "empty stdout": "",
            "status is not SUCCESS": success_payload("途中の助言", status="ERROR"),
            "status is missing": {"response": "途中の助言"},
            "denied actions": success_payload(
                "途中で終わった助言",
                denied_actions=[{"action": "command", "display_name": "Bash"}],
            ),
            "empty response": success_payload(""),
            "missing response": {
                "conversation_id": "conversation-1",
                "status": "SUCCESS",
            },
            "null response": {**success_payload(""), "response": None},
            "json array": [],
            "json string": '"SUCCESS"',
        }
        for label, agy_stdout in cases.items():
            with self.subTest(case=label):
                self.setUp()
                result = self.run_wrapper(agy_stdout=agy_stdout)
                self.assert_failed(result, 6, agy_invoked=True)


class WrapperWorkspaceGuardTest(WrapperHarness):
    """git の work tree 内では、agy の実行前後の HEAD と working tree の変化を失敗にする。"""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "antigravity-advisor test")
        self.git("config", "user.email", "antigravity-advisor@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.git("commit", "--quiet", "-m", "initial")

    def git(self, *args: str) -> str:
        git = shutil.which("git")
        assert git is not None
        return subprocess.run(
            [git, *args],
            cwd=str(self.repo),
            env=git_environment(self.home),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            check=True,
        ).stdout

    def test_unchanged_clean_tree_succeeds(self) -> None:
        result = self.run_wrapper(cwd=self.repo)
        self.assert_succeeded(result, "既定の助言")
        self.assertEqual(os.path.realpath(self.repo), self.recorded_cwd())
        self.assertEqual(
            "", self.git("status", "--porcelain"), "wrapper は repo に書き込まない"
        )

    def test_unchanged_dirty_tree_succeeds(self) -> None:
        (self.repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        (self.repo / "tracked.txt").write_text("modified\n", encoding="utf-8")
        result = self.run_wrapper(cwd=self.repo)
        self.assert_succeeded(result, "既定の助言")

    def test_created_untracked_file_is_workspace_modified(self) -> None:
        result = self.run_wrapper(
            cwd=self.repo,
            extra_env={"FAKE_AGY_CREATE_FILE": str(self.repo / "created.txt")},
        )
        self.assert_failed(result, 5, agy_invoked=True)
        self.assert_wrapper_tmpdir_is_empty()

    def test_modified_tracked_file_is_workspace_modified(self) -> None:
        result = self.run_wrapper(
            cwd=self.repo,
            extra_env={"FAKE_AGY_APPEND_FILE": str(self.repo / "tracked.txt")},
        )
        self.assert_failed(result, 5, agy_invoked=True)

    def test_new_commit_is_workspace_modified(self) -> None:
        result = self.run_wrapper(cwd=self.repo, extra_env={"FAKE_AGY_COMMIT": "1"})
        self.assert_failed(result, 5, agy_invoked=True)

    def test_change_seen_from_a_subdirectory_cwd(self) -> None:
        subdirectory = self.repo / "sub"
        subdirectory.mkdir()
        (subdirectory / "kept.txt").write_text("kept\n", encoding="utf-8")
        self.git("add", "sub/kept.txt")
        self.git("commit", "--quiet", "-m", "add sub")
        result = self.run_wrapper(
            cwd=subdirectory,
            extra_env={"FAKE_AGY_CREATE_FILE": str(self.repo / "root-level.txt")},
        )
        self.assert_failed(result, 5, agy_invoked=True)

    def test_workspace_change_wins_over_agy_failure(self) -> None:
        result = self.run_wrapper(
            cwd=self.repo,
            agy_exit=9,
            extra_env={"FAKE_AGY_CREATE_FILE": str(self.repo / "created.txt")},
        )
        self.assert_failed(result, 5, agy_invoked=True)

    def test_workspace_change_wins_over_incomplete_response(self) -> None:
        result = self.run_wrapper(
            cwd=self.repo,
            agy_stdout="not json",
            extra_env={"FAKE_AGY_CREATE_FILE": str(self.repo / "created.txt")},
        )
        self.assert_failed(result, 5, agy_invoked=True)


class WrapperUnbornHeadTest(WrapperHarness):
    """commit の無い repo でも HEAD の取得失敗を失敗扱いにせず、working tree を比較する。"""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.base / "unborn-repo"
        self.repo.mkdir()
        git = shutil.which("git")
        assert git is not None
        environment = git_environment(self.home)
        for args in (
            ("init", "--quiet"),
            ("config", "user.name", "antigravity-advisor test"),
            ("config", "user.email", "antigravity-advisor@example.invalid"),
            ("config", "commit.gpgsign", "false"),
        ):
            subprocess.run(
                [git, *args],
                cwd=str(self.repo),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

    def test_unchanged_unborn_repo_succeeds(self) -> None:
        result = self.run_wrapper(cwd=self.repo)
        self.assert_succeeded(result, "既定の助言")

    def test_created_file_in_unborn_repo_is_workspace_modified(self) -> None:
        result = self.run_wrapper(
            cwd=self.repo,
            extra_env={"FAKE_AGY_CREATE_FILE": str(self.repo / "created.txt")},
        )
        self.assert_failed(result, 5, agy_invoked=True)

    def test_first_commit_in_unborn_repo_is_workspace_modified(self) -> None:
        result = self.run_wrapper(cwd=self.repo, extra_env={"FAKE_AGY_COMMIT": "1"})
        self.assert_failed(result, 5, agy_invoked=True)


class WrapperOutsideGitTest(WrapperHarness):
    """cwd が git の work tree の外なら、比較を行わずに相談する。"""

    def test_succeeds_outside_a_work_tree(self) -> None:
        result = self.run_wrapper(cwd=self.workdir)
        self.assert_succeeded(result, "既定の助言")
        self.assertEqual(os.path.realpath(self.workdir), self.recorded_cwd())

    def test_file_creation_outside_a_work_tree_is_not_compared(self) -> None:
        result = self.run_wrapper(
            cwd=self.workdir,
            extra_env={"FAKE_AGY_CREATE_FILE": str(self.workdir / "created.txt")},
        )
        self.assert_succeeded(result, "既定の助言")


class PluginMetadataContractTest(unittest.TestCase):
    """plugin.json・marketplace.json・2 つの README の version と記載が一致する。"""

    def test_plugin_manifest(self) -> None:
        manifest = load_json(PLUGIN_JSON)
        self.assertEqual(PLUGIN_NAME, manifest["name"])
        self.assertEqual(EXPECTED_VERSION, manifest["version"])
        self.assertEqual(EXPECTED_DESCRIPTION, manifest["description"])
        self.assertEqual("natsuume", manifest["author"]["name"])

    def test_marketplace_entry(self) -> None:
        entries = {
            entry["name"]: entry for entry in load_json(MARKETPLACE_JSON)["plugins"]
        }
        self.assertIn(PLUGIN_NAME, entries)
        entry = entries[PLUGIN_NAME]
        self.assertEqual(f"./plugins/{PLUGIN_NAME}", entry["source"])
        self.assertEqual(EXPECTED_VERSION, entry["version"])
        self.assertEqual(EXPECTED_DESCRIPTION, entry["description"])
        self.assertTrue(EXPECTED_KEYWORDS <= set(entry["keywords"]), entry["keywords"])

    def test_repository_readme_lists_the_plugin(self) -> None:
        readme = read_text(REPO_README)
        self.assertIn(
            f"| [{PLUGIN_NAME}](#{PLUGIN_NAME}) | {EXPECTED_VERSION} |", readme
        )
        self.assertIn(f"## {PLUGIN_NAME}", readme.splitlines())

    def test_plugin_readme_version_heading(self) -> None:
        lines = read_text(PLUGIN_README).splitlines()
        self.assertIn("## バージョン", lines)
        following = [
            line.strip()
            for line in lines[lines.index("## バージョン") + 1 :]
            if line.strip()
        ]
        self.assertTrue(following)
        self.assertEqual(f"v{EXPECTED_VERSION}", following[0])

    def test_plugin_readme_documents_the_permission_settings(self) -> None:
        readme = read_text(PLUGIN_README)
        self.assertIn(SETTINGS_ARGUMENT, readme)
        self.assertNotIn("mktemp", readme)

    def test_plugin_has_no_hooks_or_commands(self) -> None:
        self.assertFalse((PLUGIN_DIR / "hooks").exists(), "hook は持たない")
        self.assertFalse((PLUGIN_DIR / "commands").exists())

    def test_plugin_documents_have_no_issue_or_pr_references(self) -> None:
        for path in sorted(PLUGIN_DIR.rglob("*")):
            if not path.is_file():
                continue
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertIsNone(ISSUE_OR_PR_REFERENCE.search(read_text(path)))


class RunnerAgentContractTest(unittest.TestCase):
    """runner agent の frontmatter・手順・report の形式。"""

    def setUp(self) -> None:
        self.text = read_text(RUNNER_AGENT)
        self.fields = frontmatter_fields(self.text)
        self.body = body_after_frontmatter(self.text)

    def test_frontmatter(self) -> None:
        self.assertEqual("antigravity-advisor-runner", self.fields.get("name"))
        self.assertEqual("Bash, Write, Read", self.fields.get("tools"))
        self.assertEqual("sonnet", self.fields.get("model"))
        description = self.fields.get("description", "")
        self.assertIn("read-only", description)
        self.assertIn("1 回", description)

    def test_procedure(self) -> None:
        for literal in (
            f'subagent_type: "{RUNNER_SUBAGENT_TYPE}"',
            'model: "sonnet"',
            "session scratchpad",
            'bash "${CLAUDE_PLUGIN_ROOT}/scripts/run-antigravity-advisor.sh" '
            '"<request file の絶対 path>"',
            "1800000",
            FALLBACK_LOOKUP_COMMAND,
            "SubagentHandback",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, self.body)

    def test_procedure_does_not_create_its_own_temporary_directory(self) -> None:
        for literal in (
            "mktemp",
            "<dir>",
            '"$HOME"/.claude/plugins/cache/*/antigravity-advisor/',
        ):
            with self.subTest(literal=literal):
                self.assertNotIn(literal, self.text)

    def test_report_format(self) -> None:
        for literal in (
            "# Antigravity Advice",
            "Status: success",
            "Status: execution-failed",
            "Exit status: <数値 or unknown>",
            "Failure class: " + " | ".join(FAILURE_CLASSES),
            "Recovery direction: <概念的な次の一手>",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, self.body)

    def test_exit_code_to_failure_class_table(self) -> None:
        for exit_code, failure_class in FAILURE_CLASS_BY_EXIT.items():
            with self.subTest(exit_code=exit_code):
                self.assertRegex(
                    self.body,
                    rf"(?m)^\|\s*{exit_code}\s*\|\s*{re.escape(failure_class)}\s*\|",
                )


class ConsultSkillContractTest(unittest.TestCase):
    """consult skill の frontmatter・request の項目・runner の起動仕様。"""

    def setUp(self) -> None:
        self.text = read_text(CONSULT_SKILL)
        self.fields = frontmatter_fields(self.text)
        self.body = body_after_frontmatter(self.text)

    def test_frontmatter(self) -> None:
        self.assertEqual("consult", self.fields.get("name"))
        self.assertTrue(self.fields.get("description"))

    def test_runner_launch_declares_the_sonnet_model(self) -> None:
        pattern = (
            rf'subagent_type: "{re.escape(RUNNER_SUBAGENT_TYPE)}".{{0,60}}'
            r'model: "sonnet"'
        )
        self.assertRegex(self.body, pattern)

    def test_request_items(self) -> None:
        for item in (
            "目的",
            "制約・受入基準",
            "現在の方針",
            "集めた証拠",
            "問い",
            "期待する回答形式",
        ):
            with self.subTest(item=item):
                self.assertIn(item, self.body)
        self.assertIn("絶対パス", self.body)

    def test_failure_handling(self) -> None:
        self.assertIn("Status: execution-failed", self.body)
        for failure_class in FAILURE_CLASSES:
            with self.subTest(failure_class=failure_class):
                self.assertIn(failure_class, self.body)


class WrapperScriptStaticTest(unittest.TestCase):
    """wrapper の構文と、ヘッダに書く I/O 契約。"""

    def setUp(self) -> None:
        self.text = read_text(WRAPPER)
        self.header = "\n".join(
            line for line in self.text.splitlines() if line.startswith("#")
        )

    def test_bash_syntax(self) -> None:
        result = subprocess.run(
            [BASH, "-n", str(WRAPPER)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_header_documents_the_contract(self) -> None:
        for literal in (
            "ANTIGRAVITY_ADVISOR_MODEL",
            "^[A-Za-z0-9._-]+$",
            "TMPDIR",
            "--mode plan --output-format json",
            "--print-timeout 25m -p <prompt>",
            SETTINGS_ARGUMENT,
            "git rev-parse --verify -q HEAD",
            "git status --porcelain=v1 -z --untracked-files=all",
            "GIT_OPTIONAL_LOCKS=0",
            "LC_ALL=C",
            "stdout",
            "stderr",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, self.header)

    def test_exit_precedence_is_documented_in_header_readme_and_runner(self) -> None:
        for path, text in (
            (WRAPPER, self.header),
            (PLUGIN_README, read_text(PLUGIN_README)),
            (RUNNER_AGENT, read_text(RUNNER_AGENT)),
        ):
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertIn(EXIT_PRECEDENCE, text)

    def test_header_exit_code_table_matches_the_runner(self) -> None:
        for exit_code, failure_class in FAILURE_CLASS_BY_EXIT.items():
            with self.subTest(exit_code=exit_code):
                self.assertRegex(
                    self.header,
                    rf"(?m)^#\s*\|\s*{exit_code}\s*\|\s*{re.escape(failure_class)}\s*\|",
                )


if __name__ == "__main__":
    unittest.main()
