"""codex companion の古い broker を止める guard (`stale-broker-guard.mjs`) の契約テスト。

対象はコピー元の `plugins/pre-push-codex-review/hooks/scripts/lib/stale-broker-guard.mjs`
(コピーとの byte-identical は `test_shared_lib_copies.py` / `test_pre_merge_lib_copies.py` が
検査する)。次の 3 つを固定する。

- 判定に使う純粋関数 (`parseElapsedSeconds` / `findAppServerExecutables` /
  `isCompanionBrokerProcess` / `resolveExecutablePath` / `isBrokerStale`)。
  `node --input-type=module -e` で module を import し、結果を JSON で受け取って検査する
- CLI の動作。一時ディレクトリに偽の companion (`scripts/codex-companion.mjs` と
  `scripts/lib/broker-lifecycle.mjs`) を置き、偽の broker (`app-server-broker.mjs` という名前の
  node スクリプト。偽の codex を `node <tmp>/bin/codex app-server` として子プロセスで動かす) を
  相手に、shutdown・後片付け・記録の削除の有無と順序、stderr、stdout が空で
  あること、終了コード 0 を検査する。偽の broker-lifecycle は、環境変数で渡したファイルから
  broker の記録を読み、呼び出された関数名と引数を別のログファイルに追記する
- 4 本の wrapper が companion の `review` / `adversarial-review` / `task` を起動する前に guard を
  呼ぶこと (静的検査)。行の前後関係と `run-codex-job.sh` の分岐の範囲だけを見て、呼び出し方の
  書式 (直接呼ぶか、shell 関数や変数を経由するか) には結合しない

テストが起動したプロセスは各テストの終了時に process group ごと止める。実際の broker には
触れない (偽の companion の path だけを guard に渡す)。
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD = (
    ROOT / "plugins" / "pre-push-codex-review" / "hooks" / "scripts" / "lib"
    / "stale-broker-guard.mjs"
)

WRAPPER_PRE_PUSH = (
    ROOT / "plugins" / "pre-push-codex-review" / "hooks" / "scripts"
    / "run-pre-push-codex-review.sh"
)
WRAPPER_PRE_MERGE = (
    ROOT / "plugins" / "pre-merge-cross-review" / "hooks" / "scripts"
    / "run-pre-merge-codex-review.sh"
)
WRAPPER_CODEX_JOB = (
    ROOT / "plugins" / "cross-model-advisor" / "scripts" / "run-codex-job.sh"
)
WRAPPER_CODEX_ADVISOR = (
    ROOT / "plugins" / "cross-model-advisor" / "scripts" / "run-codex-advisor.sh"
)

ENDPOINT_ENV = "CODEX_COMPANION_APP_SERVER_ENDPOINT"
FAKE_SESSION_FILE_ENV = "FAKE_BROKER_SESSION_FILE"
FAKE_CALL_LOG_ENV = "FAKE_BROKER_CALL_LOG"
FAKE_REPLACEMENT_SESSION_ENV = "FAKE_BROKER_REPLACEMENT_SESSION"

STDERR_PREFIX = "[stale-broker-guard] "
WARNING_PREFIX = "[stale-broker-guard] warning: "

# etime の解像度は 1 秒。broker の起動と実行ファイルの更新の間をこれだけ空ければ、
# 秒への切り捨てがあっても前後関係が逆転しない。
TIMESTAMP_GAP_SECONDS = 2.5
PROCESS_WAIT_TIMEOUT_SECONDS = 10
GUARD_TIMEOUT_SECONDS = 60

# module を import して 1 つの公開関数を呼び、戻り値を JSON で返す。入力は stdin の JSON。
PURE_CALL_SCRIPT = """
import { pathToFileURL } from "node:url";
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const { modulePath, name, args } = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const mod = await import(pathToFileURL(modulePath).href);
const value = await mod[name](...args);
// NaN などは JSON で null になるため、JSON 化の前の型も返す。
const type = value === null ? "null" : Number.isNaN(value) ? "NaN" : typeof value;
process.stdout.write(JSON.stringify({ type, value: value ?? null }));
"""

# 偽の broker-lifecycle (teardownBrokerSession を除く部分)。broker の記録は
# FAKE_BROKER_SESSION_FILE から読み、呼び出しは FAKE_BROKER_CALL_LOG に「関数名<TAB>引数」の
# 形で 1 行ずつ追記する。sendBrokerShutdown は記録の pid に SIGTERM を送る。
# FAKE_BROKER_REPLACEMENT_SESSION が設定されていれば、SIGTERM を送った直後にその JSON で記録を
# 書き換える (並行する companion が新しい broker を記録した状態を再現する)。
FAKE_LIFECYCLE_WITHOUT_TEARDOWN_SOURCE = """
import fs from "node:fs";
import process from "node:process";

const record = (name, arg) => {
  const logFile = process.env.FAKE_BROKER_CALL_LOG;
  if (logFile) {
    fs.appendFileSync(logFile, `${name}\\t${arg ?? ""}\\n`);
  }
};

const readSession = () => {
  const sessionFile = process.env.FAKE_BROKER_SESSION_FILE;
  if (!sessionFile || !fs.existsSync(sessionFile)) {
    return null;
  }
  return JSON.parse(fs.readFileSync(sessionFile, "utf8"));
};

export function loadBrokerSession(cwd) {
  record("loadBrokerSession", cwd);
  return readSession();
}

export async function sendBrokerShutdown(endpoint) {
  record("sendBrokerShutdown", endpoint);
  const session = readSession();
  if (session && Number.isInteger(session.pid)) {
    try {
      process.kill(session.pid, "SIGTERM");
    } catch {
      // 既に終了している broker は無視する。
    }
  }
  const replacement = process.env.FAKE_BROKER_REPLACEMENT_SESSION;
  const sessionFile = process.env.FAKE_BROKER_SESSION_FILE;
  if (replacement && sessionFile) {
    fs.writeFileSync(sessionFile, replacement);
  }
}

export function clearBrokerSession(cwd) {
  record("clearBrokerSession", cwd);
  const sessionFile = process.env.FAKE_BROKER_SESSION_FILE;
  if (sessionFile) {
    fs.rmSync(sessionFile, { force: true });
  }
}
"""

# teardownBrokerSession は受け取った引数を JSON にして記録する (関数の値は JSON に現れない
# ため、`killProcess` が null で渡されたことをキーの有無と値で確かめられる)。
FAKE_TEARDOWN_EXPORT_SOURCE = """
export function teardownBrokerSession(options) {
  record("teardownBrokerSession", JSON.stringify(options));
}
"""

FAKE_LIFECYCLE_SOURCE = (
    FAKE_LIFECYCLE_WITHOUT_TEARDOWN_SOURCE + FAKE_TEARDOWN_EXPORT_SOURCE
)

# loadBrokerSession だけを export する偽の broker-lifecycle。
FAKE_LIFECYCLE_LOAD_ONLY_SOURCE = """
import fs from "node:fs";
import process from "node:process";

export function loadBrokerSession(cwd) {
  const logFile = process.env.FAKE_BROKER_CALL_LOG;
  if (logFile) {
    fs.appendFileSync(logFile, `loadBrokerSession\\t${cwd}\\n`);
  }
  const sessionFile = process.env.FAKE_BROKER_SESSION_FILE;
  if (!sessionFile || !fs.existsSync(sessionFile)) {
    return null;
  }
  return JSON.parse(fs.readFileSync(sessionFile, "utf8"));
}
"""

FAKE_APP_SERVER_SOURCE = "setInterval(() => {}, 1000);\n"
FAKE_APP_SERVER_UPDATED_SOURCE = "// updated\nsetInterval(() => {}, 1000);\n"

# 偽の broker (および broker でないプロセス) の本体。環境変数 FAKE_BROKER_CHILD_EXECUTABLE の
# JS ファイルを `node <path> app-server` として子プロセスで起動し、自分も動き続ける。
FAKE_CHILD_EXECUTABLE_ENV = "FAKE_BROKER_CHILD_EXECUTABLE"
FAKE_BROKER_SCRIPT_SOURCE = """
import { spawn } from "node:child_process";
import process from "node:process";

spawn(process.execPath, [process.env.FAKE_BROKER_CHILD_EXECUTABLE, "app-server"], {
  stdio: "ignore",
});
setInterval(() => {}, 1000);
"""
BROKER_SCRIPT_NAME = "app-server-broker.mjs"
NON_BROKER_SCRIPT_NAME = "worker.mjs"


def call_guard_function(name: str, *args: object) -> tuple[str, object]:
    """guard module の公開関数 `name` を呼び、(JSON 化する前の戻り値の型, 戻り値) を返す。

    型は `typeof` の結果で、null は `"null"`、NaN は `"NaN"` とする。

    node が非ゼロで終了したら AssertionError を投げる (stderr を含める)。
    """
    payload = json.dumps(
        {"modulePath": str(GUARD), "name": name, "args": list(args)}
    )
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", PURE_CALL_SCRIPT],
        input=payload,
        capture_output=True,
        text=True,
        timeout=GUARD_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"{name} の呼び出しが失敗しました (exit {completed.returncode}):\n"
            f"{completed.stderr}"
        )
    result = json.loads(completed.stdout)
    return result["type"], result["value"]


def ps_lines(*rows: str) -> str:
    """`ps -A -o pid= -o ppid= -o args=` の出力を模した文字列 (pid を右寄せした行) を作る。"""
    return "".join(f"{row}\n" for row in rows)


class ParseElapsedSecondsTest(unittest.TestCase):
    def assert_parsed(self, text: str, expected: int | None) -> None:
        value_type, value = call_guard_function("parseElapsedSeconds", text)
        expected_type = "null" if expected is None else "number"
        self.assertEqual(expected_type, value_type, f"{text!r} の戻り値の型")
        self.assertEqual(expected, value, f"{text!r} の解釈結果")

    def test_minutes_and_seconds(self) -> None:
        self.assert_parsed("00:07", 7)

    def test_hours_minutes_and_seconds(self) -> None:
        self.assert_parsed("01:02:03", 3723)

    def test_days_hours_minutes_and_seconds(self) -> None:
        self.assert_parsed("2-01:02:03", 176523)

    def test_surrounding_whitespace_and_newline_are_allowed(self) -> None:
        self.assert_parsed("  12:34\n", 754)

    def test_unparsable_text_returns_null(self) -> None:
        for text in ("", "abc", "1:2:3:4"):
            with self.subTest(text=text):
                self.assert_parsed(text, None)


class FindAppServerExecutablesTest(unittest.TestCase):
    BROKER_PID = 100

    def find(self, ps_output: str) -> list[str]:
        value_type, value = call_guard_function(
            "findAppServerExecutables", ps_output, self.BROKER_PID
        )
        self.assertEqual("object", value_type, "戻り値の型")
        self.assertIsInstance(value, list)
        return value

    def test_collects_from_child_and_grandchild(self) -> None:
        output = ps_lines(
            "    1     0 /sbin/init",
            "  100     1 node /opt/companion/scripts/app-server-broker.mjs serve",
            "  101   100 node /opt/codex/bin/codex app-server",
            "  102   101 /opt/codex/vendor/bin/codex app-server --flag",
        )
        self.assertEqual(
            ["/opt/codex/bin/codex", "/opt/codex/vendor/bin/codex"],
            self.find(output),
        )

    def test_excludes_app_server_of_unrelated_processes(self) -> None:
        output = ps_lines(
            "  100     1 node /opt/companion/scripts/app-server-broker.mjs serve",
            "  101   100 node /opt/codex/bin/codex app-server",
            "  200     1 node /other/bin/codex app-server",
            "  201   200 /other/vendor/codex app-server",
        )
        self.assertEqual(["/opt/codex/bin/codex"], self.find(output))

    def test_excludes_broker_itself(self) -> None:
        output = ps_lines(
            "  100     1 /opt/broker/bin/codex app-server",
            "  101   100 node /opt/codex/bin/codex app-server",
        )
        self.assertEqual(["/opt/codex/bin/codex"], self.find(output))

    def test_ignores_partial_match_such_as_app_server_broker(self) -> None:
        output = ps_lines(
            "  100     1 node /opt/companion/scripts/app-server-broker.mjs serve",
            "  101   100 node /opt/companion/scripts/app-server-broker.mjs serve",
            "  102   100 node /opt/codex/bin/codex app-server",
        )
        self.assertEqual(["/opt/codex/bin/codex"], self.find(output))

    def test_excludes_process_whose_first_token_is_app_server(self) -> None:
        output = ps_lines(
            "  100     1 node /opt/companion/scripts/app-server-broker.mjs serve",
            "  101   100 app-server --listen stdio",
            "  102   100 node /opt/codex/bin/codex app-server",
        )
        self.assertEqual(["/opt/codex/bin/codex"], self.find(output))

    def test_ignores_empty_and_non_numeric_lines(self) -> None:
        output = ps_lines(
            "",
            "   ",
            "  PID  PPID ARGS",
            "  100     1 node /opt/companion/scripts/app-server-broker.mjs serve",
            "  abc   100 node /bad/pid/codex app-server",
            "  103   xyz node /bad/ppid/codex app-server",
            "  101   100 node /opt/codex/bin/codex app-server",
        )
        self.assertEqual(["/opt/codex/bin/codex"], self.find(output))

    def test_removes_duplicates(self) -> None:
        output = ps_lines(
            "  100     1 node /opt/companion/scripts/app-server-broker.mjs serve",
            "  101   100 node /opt/codex/bin/codex app-server",
            "  102   100 node /opt/codex/bin/codex app-server",
            "  103   101 /opt/codex/vendor/bin/codex app-server",
            "  104   102 /opt/codex/vendor/bin/codex app-server",
        )
        self.assertEqual(
            ["/opt/codex/bin/codex", "/opt/codex/vendor/bin/codex"],
            self.find(output),
        )

    def test_ignores_tokens_whose_basename_is_not_codex(self) -> None:
        output = ps_lines(
            "  100     1 node /opt/companion/scripts/app-server-broker.mjs serve",
            "  101   100 node /opt/codex/bin/codex app-server",
            "  102   101 grep -rn app-server src",
            "  103   101 /usr/bin/node app-server",
            "  104   101 /opt/tools/codex-helper app-server",
        )
        self.assertEqual(["/opt/codex/bin/codex"], self.find(output))

    def test_only_non_codex_tokens_yield_empty_list(self) -> None:
        output = ps_lines(
            "  100     1 node /opt/companion/scripts/app-server-broker.mjs serve",
            "  101   100 node /opt/tools/other app-server",
            "  102   100 grep app-server file",
        )
        self.assertEqual([], self.find(output))

    def test_node_script_token_is_the_codex_path(self) -> None:
        output = ps_lines(
            "  100     1 node /x/scripts/app-server-broker.mjs serve",
            "  101   100 node /x/bin/codex app-server",
        )
        self.assertEqual(["/x/bin/codex"], self.find(output))

    def test_bare_codex_command_token_is_returned_as_is(self) -> None:
        output = ps_lines(
            "  100     1 node /x/scripts/app-server-broker.mjs serve",
            "  101   100 codex app-server",
        )
        self.assertEqual(["codex"], self.find(output))


class IsCompanionBrokerProcessTest(unittest.TestCase):
    BROKER_PID = 100

    def check(self, ps_output: str) -> bool:
        value_type, value = call_guard_function(
            "isCompanionBrokerProcess", ps_output, self.BROKER_PID
        )
        self.assertEqual("boolean", value_type, "戻り値の型")
        return value

    def test_broker_script_in_args_is_true(self) -> None:
        output = ps_lines(
            "    1     0 /sbin/init",
            "  100     1 /usr/bin/node /x/scripts/app-server-broker.mjs serve",
        )
        self.assertIs(True, self.check(output))

    def test_args_without_broker_script_is_false(self) -> None:
        output = ps_lines(
            "  100     1 node /x/bin/codex app-server",
            "  101   100 node /x/scripts/app-server-broker.mjs serve",
        )
        self.assertIs(False, self.check(output))

    def test_missing_pid_line_is_false(self) -> None:
        output = ps_lines(
            "  101     1 node /x/scripts/app-server-broker.mjs serve",
        )
        self.assertIs(False, self.check(output))

    def test_partial_match_of_broker_script_is_false(self) -> None:
        output = ps_lines(
            "  100     1 node /x/scripts/my-app-server-broker.mjs serve",
        )
        self.assertIs(False, self.check(output))


class ResolveExecutablePathTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(os.path.realpath(temporary.name))

    def make_dir(self, name: str) -> Path:
        directory = self.base / name
        directory.mkdir()
        return directory

    def make_file(self, path: Path, executable: bool) -> Path:
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755 if executable else 0o644)
        return path

    def resolve(self, token: str, path_env: str) -> str | None:
        value_type, value = call_guard_function(
            "resolveExecutablePath", token, path_env
        )
        self.assertIn(value_type, ("string", "null"), "戻り値の型")
        return value

    def path_env(self, *directories: Path) -> str:
        return os.pathsep.join(str(directory) for directory in directories)

    def test_absolute_path_is_returned_as_is(self) -> None:
        directory = self.make_dir("bin")
        executable = self.make_file(directory / "codex", executable=True)
        self.assertEqual(
            str(executable), self.resolve(str(executable), self.path_env(directory))
        )

    def test_relative_path_returns_null(self) -> None:
        directory = self.make_dir("bin")
        self.make_file(directory / "x", executable=True)
        (directory / "a").mkdir()
        self.make_file(directory / "a" / "b", executable=True)
        for token in ("./x", "a/b"):
            with self.subTest(token=token):
                self.assertIsNone(self.resolve(token, self.path_env(directory)))

    def test_finds_executable_on_path(self) -> None:
        empty = self.make_dir("empty")
        directory = self.make_dir("bin")
        executable = self.make_file(directory / "codex", executable=True)
        self.assertEqual(
            str(executable), self.resolve("codex", self.path_env(empty, directory))
        )

    def test_first_directory_on_path_wins(self) -> None:
        first = self.make_dir("first")
        second = self.make_dir("second")
        preferred = self.make_file(first / "codex", executable=True)
        self.make_file(second / "codex", executable=True)
        self.assertEqual(
            str(preferred), self.resolve("codex", self.path_env(first, second))
        )

    def test_skips_non_executable_file_and_directory(self) -> None:
        not_executable = self.make_dir("not-executable")
        self.make_file(not_executable / "codex", executable=False)
        is_directory = self.make_dir("is-directory")
        (is_directory / "codex").mkdir()
        usable = self.make_dir("usable")
        executable = self.make_file(usable / "codex", executable=True)
        self.assertEqual(
            str(executable),
            self.resolve(
                "codex", self.path_env(not_executable, is_directory, usable)
            ),
        )

    def test_symlink_is_judged_by_its_target(self) -> None:
        targets = self.make_dir("targets")
        target = self.make_file(targets / "codex-real", executable=True)
        links = self.make_dir("links")
        link = links / "codex"
        link.symlink_to(target)
        self.assertEqual(str(link), self.resolve("codex", self.path_env(links)))

    def test_not_found_returns_null(self) -> None:
        directory = self.make_dir("bin")
        self.make_file(directory / "other", executable=True)
        self.assertIsNone(self.resolve("codex", self.path_env(directory)))

    def test_empty_path_returns_null(self) -> None:
        self.assertIsNone(self.resolve("codex", ""))


class IsBrokerStaleTest(unittest.TestCase):
    def assert_stale(
        self, broker_start_ms: int, times_ms: list[int], expected: bool
    ) -> None:
        value_type, value = call_guard_function(
            "isBrokerStale", broker_start_ms, times_ms
        )
        self.assertEqual("boolean", value_type, "戻り値の型")
        self.assertIs(expected, value)

    def test_newer_executable_is_stale(self) -> None:
        self.assert_stale(1_000, [1_001], True)

    def test_equal_time_is_not_stale(self) -> None:
        self.assert_stale(1_000, [1_000], False)

    def test_empty_list_is_not_stale(self) -> None:
        self.assert_stale(1_000, [], False)

    def test_one_newer_among_many_is_stale(self) -> None:
        self.assert_stale(1_000, [500, 900, 1_500, 999], True)


class GuardCliTest(unittest.TestCase):
    """偽の companion と偽の broker を相手に CLI を起動する結合テスト。"""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(os.path.realpath(temporary.name))
        self.workspace = base / "workspace"
        self.workspace.mkdir()
        self.companion_scripts = base / "companion" / "scripts"
        self.companion_scripts.mkdir(parents=True)
        self.companion = self.companion_scripts / "codex-companion.mjs"
        self.companion.write_text("", encoding="utf-8")
        self.session_file = base / "broker-session.json"
        self.call_log = base / "calls.log"
        self.fake_app_server = base / "bin" / "codex"
        self.fake_app_server.parent.mkdir()
        self.fake_other_executable = base / "bin" / "other"
        self.broker_scripts = base / "broker-scripts"
        self.broker_scripts.mkdir()

    # --- 偽の companion ---

    def install_lifecycle(self, source: str = FAKE_LIFECYCLE_SOURCE) -> None:
        lib = self.companion_scripts / "lib"
        lib.mkdir(exist_ok=True)
        (lib / "broker-lifecycle.mjs").write_text(source, encoding="utf-8")

    def session_record(self, pid: int, name: str = "fake-broker") -> dict[str, object]:
        session_dir = self.workspace / f"{name}-session"
        return {
            "endpoint": f"unix:{session_dir / 'broker.sock'}",
            "pidFile": str(session_dir / "broker.pid"),
            "logFile": str(session_dir / "broker.log"),
            "sessionDir": str(session_dir),
            "pid": pid,
        }

    def write_session(self, pid: int) -> dict[str, object]:
        session = self.session_record(pid)
        self.session_file.write_text(json.dumps(session), encoding="utf-8")
        return session

    def recorded_calls(self) -> list[tuple[str, str]]:
        if not self.call_log.exists():
            return []
        calls = []
        for line in self.call_log.read_text(encoding="utf-8").splitlines():
            name, _, arg = line.partition("\t")
            calls.append((name, arg))
        return calls

    def called_names(self) -> list[str]:
        return [name for name, _ in self.recorded_calls()]

    # --- 偽の broker ---

    def write_fake_app_server(self, source: str = FAKE_APP_SERVER_SOURCE) -> None:
        self.fake_app_server.write_text(source, encoding="utf-8")

    def start_broker(
        self,
        script_name: str = BROKER_SCRIPT_NAME,
        child_executable: Path | None = None,
    ) -> subprocess.Popen[bytes]:
        """`node <tmp>/broker-scripts/<script_name>` を新しい session で起動する。

        script は `node <child_executable> app-server` を子プロセスで起動して動き続ける。
        child_executable の既定は偽の codex (`<tmp>/bin/codex`)。broker が終了したらすぐ
        回収されるよう、wait する thread を付ける (回収されない zombie は `kill -0` で生きて
        いるように見えるため)。後始末は process group ごと行う。
        """
        script = self.broker_scripts / script_name
        script.write_text(FAKE_BROKER_SCRIPT_SOURCE, encoding="utf-8")
        executable = child_executable or self.fake_app_server
        env = os.environ.copy()
        env[FAKE_CHILD_EXECUTABLE_ENV] = str(executable)
        broker = subprocess.Popen(
            ["node", str(script), "serve"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        reaper = threading.Thread(target=broker.wait, daemon=True)
        reaper.start()
        self.addCleanup(self.stop_broker_group, broker, reaper)
        return broker

    def stop_broker_group(
        self, broker: subprocess.Popen[bytes], reaper: threading.Thread
    ) -> None:
        try:
            os.killpg(broker.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        reaper.join(timeout=PROCESS_WAIT_TIMEOUT_SECONDS)

    def wait_for_app_server_child(
        self, broker_pid: int, child_executable: Path | None = None
    ) -> None:
        executable = str(child_executable or self.fake_app_server)
        deadline = time.monotonic() + PROCESS_WAIT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            listing = subprocess.run(
                ["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "args="],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            for line in listing.splitlines():
                fields = line.split(None, 2)
                if len(fields) < 3 or fields[1] != str(broker_pid):
                    continue
                tokens = fields[2].split()
                if "app-server" in tokens and executable in tokens:
                    return
            time.sleep(0.1)
        self.fail(f"偽の broker (pid={broker_pid}) の app-server 子プロセスが現れません")

    def start_fresh_broker(self) -> subprocess.Popen[bytes]:
        """実行ファイルの更新より後に起動した (古くない) broker。"""
        self.write_fake_app_server()
        time.sleep(TIMESTAMP_GAP_SECONDS)
        broker = self.start_broker()
        self.wait_for_app_server_child(broker.pid)
        return broker

    def start_stale_broker(
        self, script_name: str = BROKER_SCRIPT_NAME
    ) -> subprocess.Popen[bytes]:
        """起動後に実行ファイルが書き直された (古い) broker。"""
        self.write_fake_app_server()
        broker = self.start_broker(script_name)
        self.wait_for_app_server_child(broker.pid)
        time.sleep(TIMESTAMP_GAP_SECONDS)
        self.write_fake_app_server(FAKE_APP_SERVER_UPDATED_SOURCE)
        return broker

    def dead_pid(self) -> int:
        finished = subprocess.Popen(["sh", "-c", ":"])
        finished.wait()
        return finished.pid

    # --- guard の起動と検査 ---

    def run_guard(
        self, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        if not GUARD.is_file():
            self.fail(f"guard module が見つかりません: {GUARD}")
        env = os.environ.copy()
        env.pop(ENDPOINT_ENV, None)
        env[FAKE_SESSION_FILE_ENV] = str(self.session_file)
        env[FAKE_CALL_LOG_ENV] = str(self.call_log)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["node", str(GUARD), str(self.companion)],
            cwd=self.workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=GUARD_TIMEOUT_SECONDS,
        )

    def assert_common_contract(
        self, completed: subprocess.CompletedProcess[str]
    ) -> None:
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("", completed.stdout)
        for line in completed.stderr.splitlines():
            self.assertTrue(
                line.startswith(STDERR_PREFIX),
                f"stderr の行が {STDERR_PREFIX!r} で始まりません: {line!r}",
            )

    def assert_silent_without_shutdown(
        self, completed: subprocess.CompletedProcess[str]
    ) -> None:
        self.assert_common_contract(completed)
        self.assertEqual("", completed.stderr)
        self.assertNotIn("sendBrokerShutdown", self.called_names())
        self.assertNotIn("clearBrokerSession", self.called_names())

    def assert_single_warning(
        self, completed: subprocess.CompletedProcess[str]
    ) -> None:
        self.assert_common_contract(completed)
        lines = completed.stderr.splitlines()
        self.assertEqual(1, len(lines), f"stderr: {completed.stderr!r}")
        self.assertTrue(
            lines[0].startswith(WARNING_PREFIX),
            f"警告の行が {WARNING_PREFIX!r} で始まりません: {lines[0]!r}",
        )
        self.assertNotIn("sendBrokerShutdown", self.called_names())

    def assert_broker_alive(self, broker: subprocess.Popen[bytes]) -> None:
        self.assertIsNone(broker.poll(), "broker が終了しています")

    def assert_broker_ended(self, broker: subprocess.Popen[bytes]) -> None:
        try:
            broker.wait(timeout=PROCESS_WAIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self.fail(f"broker (pid={broker.pid}) が終了していません")

    def assert_single_stop_notice(
        self, completed: subprocess.CompletedProcess[str], pid: int
    ) -> None:
        """stderr が broker を止めた旨の 1 行 (警告ではなく `pid=<pid>` を含む) だけであること。"""
        self.assert_common_contract(completed)
        lines = completed.stderr.splitlines()
        self.assertEqual(1, len(lines), f"stderr: {completed.stderr!r}")
        self.assertFalse(lines[0].startswith(WARNING_PREFIX), lines[0])
        pid_pattern = re.compile(rf"\bpid={pid}\b")
        self.assertEqual(
            1,
            sum(1 for line in lines if pid_pattern.search(line)),
            f"stderr: {completed.stderr!r}",
        )

    def assert_cwd_arguments(self) -> None:
        workspace = str(self.workspace)
        for name, arg in self.recorded_calls():
            if name in ("loadBrokerSession", "clearBrokerSession"):
                self.assertEqual(workspace, arg, f"{name} に渡された cwd")

    def teardown_arguments(self) -> list[dict[str, object]]:
        return [
            json.loads(arg)
            for name, arg in self.recorded_calls()
            if name == "teardownBrokerSession"
        ]

    # --- 何もしない場合 ---

    def test_no_broker_record_does_nothing(self) -> None:
        self.install_lifecycle()
        self.assert_silent_without_shutdown(self.run_guard())

    def test_recorded_pid_that_does_not_exist_does_nothing(self) -> None:
        self.install_lifecycle()
        self.write_session(self.dead_pid())
        self.assert_silent_without_shutdown(self.run_guard())

    def test_endpoint_env_skips_even_stale_broker(self) -> None:
        self.install_lifecycle()
        broker = self.start_stale_broker()
        self.write_session(broker.pid)
        completed = self.run_guard(
            {ENDPOINT_ENV: f"unix:{self.workspace / 'direct.sock'}"}
        )
        self.assert_silent_without_shutdown(completed)
        self.assert_broker_alive(broker)

    def test_empty_endpoint_env_does_not_skip_stale_broker(self) -> None:
        # companion は空文字列の endpoint を未設定と同じに扱い、broker の記録を使う。
        self.install_lifecycle()
        broker = self.start_stale_broker()
        self.write_session(broker.pid)
        completed = self.run_guard({ENDPOINT_ENV: ""})
        self.assert_common_contract(completed)
        self.assertIn("sendBrokerShutdown", self.called_names())

    def test_fresh_broker_is_left_running(self) -> None:
        self.install_lifecycle()
        broker = self.start_fresh_broker()
        self.write_session(broker.pid)
        self.assert_silent_without_shutdown(self.run_guard())
        self.assert_broker_alive(broker)

    # --- 古い broker を止める場合 ---

    def test_stale_broker_is_stopped_and_record_cleared(self) -> None:
        self.install_lifecycle()
        broker = self.start_stale_broker()
        session = self.write_session(broker.pid)
        completed = self.run_guard()
        self.assert_common_contract(completed)

        names = self.called_names()
        for name in (
            "sendBrokerShutdown", "teardownBrokerSession", "clearBrokerSession"
        ):
            self.assertIn(name, names)
        self.assertLess(
            names.index("sendBrokerShutdown"), names.index("teardownBrokerSession")
        )
        self.assertLess(
            names.index("teardownBrokerSession"), names.index("clearBrokerSession")
        )
        self.assert_cwd_arguments()

        teardowns = self.teardown_arguments()
        self.assertEqual(1, len(teardowns), "teardownBrokerSession の呼び出し回数")
        teardown = teardowns[0]
        self.assertIn("killProcess", teardown, "killProcess が渡されていません")
        self.assertIsNone(teardown["killProcess"], "killProcess は null で渡す")
        for key in ("endpoint", "pidFile", "logFile", "sessionDir", "pid"):
            self.assertEqual(session[key], teardown.get(key), f"teardown の {key}")

        self.assert_broker_ended(broker)
        self.assertFalse(self.session_file.exists(), "記録が消えていません")
        self.assert_single_stop_notice(completed, broker.pid)

    def test_record_rewritten_during_shutdown_is_left_untouched(self) -> None:
        self.install_lifecycle()
        broker = self.start_stale_broker()
        self.write_session(broker.pid)
        replacement = self.session_record(os.getpid(), name="replacement-broker")
        completed = self.run_guard(
            {FAKE_REPLACEMENT_SESSION_ENV: json.dumps(replacement)}
        )
        self.assert_common_contract(completed)

        names = self.called_names()
        self.assertIn("sendBrokerShutdown", names)
        self.assertNotIn("teardownBrokerSession", names)
        self.assertNotIn("clearBrokerSession", names)
        self.assertEqual(
            replacement,
            json.loads(self.session_file.read_text(encoding="utf-8")),
            "書き直された記録が変更されています",
        )
        self.assert_broker_ended(broker)
        self.assert_single_stop_notice(completed, broker.pid)

    def test_stale_broker_is_stopped_without_teardown_export(self) -> None:
        self.install_lifecycle(FAKE_LIFECYCLE_WITHOUT_TEARDOWN_SOURCE)
        broker = self.start_stale_broker()
        self.write_session(broker.pid)
        completed = self.run_guard()
        self.assert_common_contract(completed)

        names = self.called_names()
        self.assertIn("sendBrokerShutdown", names)
        self.assertIn("clearBrokerSession", names)
        self.assertLess(
            names.index("sendBrokerShutdown"), names.index("clearBrokerSession")
        )
        self.assert_cwd_arguments()
        self.assert_broker_ended(broker)
        self.assertFalse(self.session_file.exists(), "記録が消えていません")
        self.assert_single_stop_notice(completed, broker.pid)

    def test_broker_whose_executable_was_removed_is_stopped(self) -> None:
        self.install_lifecycle()
        self.write_fake_app_server()
        time.sleep(TIMESTAMP_GAP_SECONDS)
        broker = self.start_broker()
        self.wait_for_app_server_child(broker.pid)
        self.fake_app_server.unlink()
        self.write_session(broker.pid)
        completed = self.run_guard()
        self.assert_common_contract(completed)

        names = self.called_names()
        self.assertIn("sendBrokerShutdown", names)
        self.assertIn("clearBrokerSession", names)
        self.assert_broker_ended(broker)
        self.assertFalse(self.session_file.exists(), "記録が消えていません")
        self.assert_single_stop_notice(completed, broker.pid)

    # --- 失敗した場合 (fail-open) ---

    def test_missing_lifecycle_module_warns_once(self) -> None:
        self.assert_single_warning(self.run_guard())

    def test_lifecycle_without_required_functions_warns_once(self) -> None:
        self.install_lifecycle(FAKE_LIFECYCLE_LOAD_ONLY_SOURCE)
        self.assert_single_warning(self.run_guard())

    def test_recorded_pid_that_is_not_a_broker_warns_without_signal(self) -> None:
        self.install_lifecycle()
        not_broker = self.start_stale_broker(NON_BROKER_SCRIPT_NAME)
        self.write_session(not_broker.pid)
        completed = self.run_guard()
        self.assert_single_warning(completed)
        self.assertNotIn("clearBrokerSession", self.called_names())
        self.assert_broker_alive(not_broker)

    def test_broker_with_only_non_codex_descendants_warns(self) -> None:
        self.install_lifecycle()
        self.fake_other_executable.write_text(
            FAKE_APP_SERVER_SOURCE, encoding="utf-8"
        )
        broker = self.start_broker(child_executable=self.fake_other_executable)
        self.wait_for_app_server_child(broker.pid, self.fake_other_executable)
        self.write_session(broker.pid)
        completed = self.run_guard()
        self.assert_single_warning(completed)
        self.assertNotIn("clearBrokerSession", self.called_names())
        self.assert_broker_alive(broker)


# --- wrapper の静的検査 ---

GUARD_LITERAL = "stale-broker-guard.mjs"
COMPANION_LAUNCH = re.compile(
    r'\bnode\s+"?\$\{?COMPANION\}?"?\s+(?:review|adversarial-review|task)(?![\w-])'
)
FUNCTION_START = re.compile(
    r"^(\s*)(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{(.*)$"
)
VARIABLE_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+|local\s+|readonly\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$"
)
CODEX_JOB_MODES = (
    "rescue", "advisor", "review", "snapshot", "status", "result", "cancel"
)
CODEX_JOB_GUARDED_MODES = ("rescue", "advisor", "review")


def is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def is_outside_quotes(line: str, position: int) -> bool:
    """line の position が引用符 (`'` / `"`) の外にあるか。"""
    in_single = False
    in_double = False
    index = 0
    while index < position:
        char = line[index]
        if char == "\\" and not in_single:
            index += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        index += 1
    return not (in_single or in_double)


def function_ranges(lines: list[str]) -> dict[str, tuple[int, int]]:
    """shell 関数の名前 → 定義の (開始行, 終了行) (両端を含む 0 始まりの行番号)。"""
    ranges: dict[str, tuple[int, int]] = {}
    for start, line in enumerate(lines):
        match = FUNCTION_START.match(line)
        if match is None or is_comment(line):
            continue
        indent, name, rest = match.groups()
        if rest.rstrip().endswith("}"):
            ranges[name] = (start, start)
            continue
        closing = re.compile(rf"^{re.escape(indent)}\}}\s*$")
        for end in range(start + 1, len(lines)):
            if closing.match(lines[end]):
                ranges[name] = (start, end)
                break
    return ranges


def guard_call_lines(lines: list[str]) -> list[int]:
    """guard を呼ぶ行 (0 始まり) の一覧。

    `stale-broker-guard.mjs` を含む行に加え、それを値に持つ変数の参照と、それを本体で
    呼ぶ shell 関数の呼び出しも guard の呼び出しとして扱う (関数の定義の中の行は、
    その関数の呼び出し側で数える)。
    """
    functions = function_ranges(lines)

    def enclosing_function(index: int) -> str | None:
        for name, (start, end) in functions.items():
            if start <= index <= end:
                return name
        return None

    def is_variable_definition(line: str) -> str | None:
        match = VARIABLE_ASSIGNMENT.match(line)
        if match is None:
            return None
        name, value = match.groups()
        if "$(" in value or "`" in value:
            return None
        return name

    references = [re.compile(re.escape(GUARD_LITERAL))]
    known: set[str] = set()
    changed = True
    while changed:
        changed = False
        for index, line in enumerate(lines):
            if is_comment(line) or not any(ref.search(line) for ref in references):
                continue
            variable = is_variable_definition(line)
            if variable is not None and f"var:{variable}" not in known:
                known.add(f"var:{variable}")
                references.append(
                    re.compile(rf"\$(?:{variable}\b|\{{{variable}\}})")
                )
                changed = True
            function = enclosing_function(index)
            if function is not None and f"fn:{function}" not in known:
                known.add(f"fn:{function}")
                references.append(re.compile(rf"(?<![\w$-]){function}(?![\w-])"))
                changed = True

    calls = []
    for index, line in enumerate(lines):
        if is_comment(line) or enclosing_function(index) is not None:
            continue
        if is_variable_definition(line) is not None:
            continue
        if any(ref.search(line) for ref in references):
            calls.append(index)
    return calls


def companion_launch_lines(lines: list[str]) -> list[int]:
    """companion の review / adversarial-review / task を起動する行 (0 始まり) の一覧。

    進捗メッセージの文字列の中に現れる `node ${COMPANION} review` は起動ではないため除く。
    """
    launches = []
    for index, line in enumerate(lines):
        if is_comment(line):
            continue
        match = COMPANION_LAUNCH.search(line)
        if match is not None and is_outside_quotes(line, match.start()):
            launches.append(index)
    return launches


def codex_job_mode_ranges(lines: list[str]) -> dict[str, tuple[int, int]]:
    """`run-codex-job.sh` のサブコマンドの分岐 → (開始行, 終了行) (両端を含む)。"""
    arm = re.compile(rf"^(\s*)({'|'.join(CODEX_JOB_MODES)})\)\s*$")
    starts: list[tuple[int, str, int]] = []
    for index, line in enumerate(lines):
        match = arm.match(line)
        if match is not None:
            starts.append((index, match.group(2), len(match.group(1))))
    ranges: dict[str, tuple[int, int]] = {}
    for position, (start, mode, indent) in enumerate(starts):
        if position + 1 < len(starts):
            ranges[mode] = (start, starts[position + 1][0] - 1)
            continue
        end = len(lines) - 1
        for index in range(start + 1, len(lines)):
            stripped = lines[index].strip()
            current_indent = len(lines[index]) - len(lines[index].lstrip())
            if current_indent <= indent and (
                stripped == "*)" or re.match(r"^esac\b", stripped)
            ):
                end = index - 1
                break
        ranges[mode] = (start, end)
    return ranges


class WrapperGuardPlacementTest(unittest.TestCase):
    def read_lines(self, path: Path) -> list[str]:
        self.assertTrue(path.is_file(), f"wrapper が見つかりません: {path}")
        return path.read_text(encoding="utf-8").splitlines()

    def assert_guard_before_every_launch(self, path: Path) -> None:
        lines = self.read_lines(path)
        launches = companion_launch_lines(lines)
        self.assertTrue(launches, f"{path.name} に companion の起動行がありません")
        calls = guard_call_lines(lines)
        self.assertTrue(calls, f"{path.name} が guard を呼んでいません")
        for launch in launches:
            with self.subTest(wrapper=path.name, launch_line=launch + 1):
                self.assertTrue(
                    any(call < launch for call in calls),
                    f"{path.name} の {launch + 1} 行目の companion 起動より前に "
                    "guard の呼び出しがありません",
                )

    def test_pre_push_wrapper_runs_guard_before_companion(self) -> None:
        self.assert_guard_before_every_launch(WRAPPER_PRE_PUSH)

    def test_pre_merge_wrapper_runs_guard_before_companion(self) -> None:
        self.assert_guard_before_every_launch(WRAPPER_PRE_MERGE)

    def test_codex_advisor_wrapper_runs_guard_before_companion(self) -> None:
        self.assert_guard_before_every_launch(WRAPPER_CODEX_ADVISOR)

    def test_codex_job_wrapper_runs_guard_only_in_launching_modes(self) -> None:
        lines = self.read_lines(WRAPPER_CODEX_JOB)
        ranges = codex_job_mode_ranges(lines)
        self.assertEqual(set(CODEX_JOB_MODES), set(ranges), "サブコマンドの分岐")
        calls = guard_call_lines(lines)
        launches = companion_launch_lines(lines)

        for mode in CODEX_JOB_GUARDED_MODES:
            start, end = ranges[mode]
            mode_launches = [line for line in launches if start <= line <= end]
            mode_calls = [line for line in calls if start <= line <= end]
            with self.subTest(mode=mode):
                self.assertTrue(
                    mode_launches, f"{mode} の分岐に companion の起動行がありません"
                )
                for launch in mode_launches:
                    self.assertTrue(
                        any(call < launch for call in mode_calls),
                        f"{mode} の分岐の {launch + 1} 行目の companion 起動より前に "
                        "guard の呼び出しがありません",
                    )

        guarded = [ranges[mode] for mode in CODEX_JOB_GUARDED_MODES]
        for call in calls:
            with self.subTest(guard_call_line=call + 1):
                self.assertTrue(
                    any(start <= call <= end for start, end in guarded),
                    f"{call + 1} 行目の guard の呼び出しが rescue / advisor / review "
                    "の分岐の外にあります (snapshot / status / result / cancel では"
                    "呼ばない)",
                )


if __name__ == "__main__":
    unittest.main()
