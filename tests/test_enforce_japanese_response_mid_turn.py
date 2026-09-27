"""enforce-japanese-response の tool 呼び出しの合間のメッセージの検知の受入テスト。

MessageDisplay hook (record-english-message.sh)・PostToolBatch hook
(request-japanese-rewrite.sh)・Stop hook (enforce-japanese-response.sh) を subprocess で
起動し、stdin に各イベントの JSON を渡して stdout・exit code・状態ディレクトリーの
ファイルを検証する。期待値は hook の公開契約であり、実装都合で変更しない。
英語の判定基準・除去処理・目標言語の解決順そのものは test_enforce_japanese_response.py
で Stop hook を通して検証し、ここでは MessageDisplay hook が同じ基準を使うことを
代表的な入力で確かめる。

## 対象 hook の契約

- 状態ディレクトリー: `${TMPDIR:-/tmp}/enforce-japanese-response/<session_id>/`。
  `pending` は英語のメッセージを表示したがまだ書き直しを指示していない印で、内容は
  英語と判定したときの入力の `prompt_id` (文字列でなければ空)。`buffers/<message_id>`
  はメッセージの `delta` を連結したバッファー。`session_id` と `message_id` が
  `^[A-Za-z0-9_-]{1,128}$` に一致しなければ状態を読み書きしない
- MessageDisplay: stdout には何も出さず、exit code は常に 0。`index` 0 でバッファーを
  作り直し、それ以外は追記する。`final` true でバッファー全体を本文として取り出して
  バッファーを消し、目標言語が日本語で本文が英語なら `pending` を作る。`agent_id` が
  空でない文字列・`delta` が文字列でない・`index` が 0 以上の整数でない・`final` が
  bool でない・jq が無い・入力が JSON object でない場合は何もしない
- PostToolBatch: `pending` があれば消し、`{"systemMessage": ..., "hookSpecificOutput":
  {"hookEventName": "PostToolBatch", "additionalContext": ...}}` を 1 つ出す。ただし
  `pending` の内容と入力の `prompt_id` がどちらも空でなく異なれば何も出さない。
  `agent_id` が空でない文字列なら何もせず `pending` も残す。exit code は常に 0
- Stop: 入力を JSON object として解析できたら、`stop_hook_active` や目標言語に依らず
  判定より前にその session の `pending` を消す。既存の block の契約は変えない

## 隔離

各テストは一時ディレクトリに HOME・プロジェクト (`cwd`)・TMPDIR を作り、`HOME` と
`TMPDIR` を env で、`cwd` を入力 JSON で渡す。実際の `~/.claude` と実際の一時
ディレクトリの状態は読み書きしない。実行環境の `CLAUDE_CONFIG_DIR` と
`CLAUDE_PROJECT_DIR` は引き継がず、`CLAUDE_PROJECT_DIR` はそれを検査するテストだけが
明示的に渡す。不正な id によるパス走査は、どの経路でもテストの一時ディレクトリの中に
留まる id だけを使い、一時ディレクトリ全体のファイル一覧が変わらないことで検証する。

## テストグループ

- MessageDisplayRecordTest: 1 回の呼び出しで届くメッセージの判定と pending の内容
- MessageDisplayBufferTest: 複数 batch の連結・バッファーの作り直し・削除
- MessageDisplayGuardTest: agent_id・目標言語・不正な id / index / final / delta
- MessageDisplayFailOpenTest: 壊れた入力・jq 不在
- PostToolBatchRewriteTest: 書き直し指示の出力・pending の消費・prompt_id の照合・
  agent_id・session の分離
- PostToolBatchFailOpenTest: 壊れた入力・jq 不在
- StopClearsPendingTest: Stop hook による pending の削除
- MidTurnFlowTest: MessageDisplay → PostToolBatch → Stop の一連の流れ
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


def _load_stop_hook_tests():
    """Stop hook のテストの fixture 関数と定数を読み込む。

    `import` 文で書くと「sys.path 操作より前に import 文が来る」という lint 制約
    (E402) に抵触するため、既存テストと同じ importlib 経由の明示 import にする。
    テストクラスはこのモジュールの名前空間に取り込まない (二重に実行されるため)。
    """
    return importlib.import_module("test_enforce_japanese_response")


_stop_hook_tests = _load_stop_hook_tests()

BASH = _stop_hook_tests.BASH
JQ_AVAILABLE = _stop_hook_tests.JQ_AVAILABLE
MISSING = _stop_hook_tests.MISSING
STOP_HOOK = _stop_hook_tests.HOOK
RECORD_HOOK = _stop_hook_tests.RECORD_HOOK
REWRITE_HOOK = _stop_hook_tests.REWRITE_HOOK
PATH_TOOLS_WITHOUT_JQ = _stop_hook_tests.PATH_TOOLS_WITHOUT_JQ
ENGLISH_MESSAGE = _stop_hook_tests.ENGLISH_MESSAGE
count_ascii_letters = _stop_hook_tests.count_ascii_letters
count_japanese = _stop_hook_tests.count_japanese
english_text = _stop_hook_tests.english_text
japanese_text = _stop_hook_tests.japanese_text
message_with_counts = _stop_hook_tests.message_with_counts

# jq を除いた PATH に置く外部コマンド。状態ディレクトリーの操作に使いうるものを足す。
PATH_TOOLS_FOR_STATE = (
    "mkdir",
    "mv",
    "cp",
    "touch",
    "ls",
    "rmdir",
    "chmod",
)

STATE_DIR_NAME = "enforce-japanese-response"

SESSION_ID = "session-A_1"
OTHER_SESSION_ID = "session-B_2"
MESSAGE_ID = "5b2a9c8e-1f63-4d8a-b7c4-9e0d2a6f1c3b"
OTHER_MESSAGE_ID = "0c9e6a2f-7d41-4f4e-9a15-3f4f7c2b8d10"
PROMPT_ID = "550e8400-e29b-41d4-a716-446655440000"
OTHER_PROMPT_ID = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"

# `^[A-Za-z0-9_-]{1,128}$` に一致しない文字列の id。パス走査を含む値は、状態
# ディレクトリー (TMPDIR/enforce-japanese-response/<id>/buffers/<id>) からどう解決しても
# テストの一時ディレクトリの中に留まるものだけを使う。
INVALID_STRING_IDS = (
    "../x",
    "../../x",
    "a/b",
    "/abs",
    ".",
    "..",
    "",
    "a b",
    "a.b",
    "abc\n",
    "../x\nabc",
    "abc\n../x",
    "日本語",
    "a" * 129,
)
# 文字列でない id。
NON_STRING_IDS = (None, 123, True, ["abc"], {"id": "abc"})

# PostToolBatch hook が出すユーザ向けの警告。
SYSTEM_MESSAGE = (
    "enforce-japanese-response: 英語のメッセージを検知したため、"
    "日本語での書き直しを指示しました。"
)
# PostToolBatch hook が additionalContext で Claude に渡す指示。
REWRITE_CONTEXT = (
    "直前に表示したメッセージは英語で書かれていました (settings の language は"
    "日本語です)。そのメッセージを内容を変えずに日本語で書き直して再掲してから"
    "作業を続け、以降のメッセージも日本語で書いてください。ユーザが英語での出力"
    " (翻訳・英文の文面等) を明示的に求めていた場合は、書き直さずにそのまま作業を"
    "続けてください。"
)
# additionalContext に含まれるべき趣旨 (直前のメッセージが英語であること / 内容を
# 変えずに日本語で書き直して再掲すること / 以降も日本語で書くこと / 英語での出力を
# 明示的に求められていた場合の扱い)。
REWRITE_REQUIRED_PHRASES = (
    "英語で書かれ",
    "内容を変えず",
    "日本語で書き直",
    "再掲",
    "以降",
    "英語での出力",
    "明示的に求め",
    "書き直さず",
)

# 日本語の文に英語の用語が多く混ざる本文 (英字は 40 字以上だが英語ではない)。
JAPANESE_MESSAGE_WITH_ENGLISH_TERMS = (
    "PR を draft で作成し、GitHub Actions の CI で lint と unit test が"
    " pass したことを確認しました。review の結果を待っています。"
)
JAPANESE_MESSAGE = message_with_counts(30, 0)


def is_english_by_judgement(text: str) -> bool:
    """fixture 検証用: code・URL を含まない本文を判定基準で英語とみなすか。"""
    letters = count_ascii_letters(text)
    japanese = count_japanese(text)
    return letters >= 40 and 20 * japanese < japanese + letters


class MidTurnHookTestCase(unittest.TestCase):
    """一時ディレクトリの HOME・プロジェクト・TMPDIR で hook を起動する基底クラス。

    既定では `$HOME/.claude/settings.json` の `language` を `日本語` にする。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.home = self.base / "home"
        self.project = self.base / "project"
        self.tmpdir = self.base / "tmp"
        self.home.mkdir()
        self.project.mkdir()
        self.tmpdir.mkdir()
        self.write_settings("user", {"language": "日本語"})

    # --- settings ---------------------------------------------------------

    def settings_path(self, scope: str) -> Path:
        if scope == "local":
            return self.project / ".claude" / "settings.local.json"
        if scope == "project":
            return self.project / ".claude" / "settings.json"
        if scope == "user":
            return self.home / ".claude" / "settings.json"
        raise ValueError(scope)

    def write_settings(self, scope: str, content: Any) -> None:
        path = self.settings_path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")

    def remove_settings(self, scope: str) -> None:
        self.settings_path(scope).unlink(missing_ok=True)

    # --- 状態ディレクトリー ---------------------------------------------------

    @property
    def state_root(self) -> Path:
        return self.tmpdir / STATE_DIR_NAME

    def pending_path(self, session_id: str = SESSION_ID) -> Path:
        return self.state_root / session_id / "pending"

    def buffer_path(
        self, message_id: str = MESSAGE_ID, session_id: str = SESSION_ID
    ) -> Path:
        return self.state_root / session_id / "buffers" / message_id

    def write_pending(self, content: str, session_id: str = SESSION_ID) -> None:
        path = self.pending_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def read_pending(self, session_id: str = SESSION_ID) -> str | None:
        """pending の内容を返す (末尾の改行は除く)。無ければ None。"""
        path = self.pending_path(session_id)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8").rstrip("\n")

    def read_buffer(
        self, message_id: str = MESSAGE_ID, session_id: str = SESSION_ID
    ) -> str | None:
        path = self.buffer_path(message_id, session_id)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def snapshot(self) -> dict[str, str | None]:
        """一時ディレクトリ全体のパスと内容の一覧を返す。

        ディレクトリは None、symlink (jq を除いた PATH の外部コマンド) はリンク先の
        パスを内容として記録する。
        """
        entries: dict[str, str | None] = {}
        for directory, dirnames, filenames in os.walk(self.base):
            for name in dirnames:
                entries[os.path.relpath(os.path.join(directory, name), self.base)] = (
                    None
                )
            for name in filenames:
                path = Path(directory) / name
                key = os.path.relpath(path, self.base)
                if path.is_symlink():
                    entries[key] = "symlink:" + os.readlink(path)
                else:
                    entries[key] = path.read_text(encoding="utf-8", errors="replace")
        return entries

    def path_without_jq(self) -> str:
        bin_dir = self.base / "bin-without-jq"
        bin_dir.mkdir(exist_ok=True)
        for tool in PATH_TOOLS_WITHOUT_JQ + PATH_TOOLS_FOR_STATE:
            found = shutil.which(tool)
            if found is not None and not (bin_dir / tool).exists():
                (bin_dir / tool).symlink_to(found)
        path = str(bin_dir)
        self.assertIsNone(shutil.which("jq", path=path))
        return path

    # --- 入力 -------------------------------------------------------------

    @staticmethod
    def _put(payload: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        for key, value in values.items():
            if value is not MISSING:
                payload[key] = value
        return payload

    def display_input(
        self,
        delta: Any,
        *,
        index: Any = 0,
        final: Any = True,
        session_id: Any = SESSION_ID,
        message_id: Any = MESSAGE_ID,
        prompt_id: Any = PROMPT_ID,
        agent_id: Any = MISSING,
        cwd: Any = None,
    ) -> dict[str, Any]:
        """MessageDisplay hook の入力 JSON を組み立てる。MISSING を渡したキーは省く。"""
        payload: dict[str, Any] = {
            "transcript_path": str(self.home / "transcript.jsonl"),
            "hook_event_name": "MessageDisplay",
            "turn_id": "7f8e9d0c-1b2a-4c3d-9e8f-0a1b2c3d4e5f",
        }
        return self._put(
            payload,
            {
                "session_id": session_id,
                "prompt_id": prompt_id,
                "cwd": str(self.project) if cwd is None else cwd,
                "message_id": message_id,
                "index": index,
                "final": final,
                "delta": delta,
                "agent_id": agent_id,
            },
        )

    def batch_input(
        self,
        *,
        session_id: Any = SESSION_ID,
        prompt_id: Any = PROMPT_ID,
        agent_id: Any = MISSING,
    ) -> dict[str, Any]:
        """PostToolBatch hook の入力 JSON を組み立てる。MISSING を渡したキーは省く。"""
        payload: dict[str, Any] = {
            "transcript_path": str(self.home / "transcript.jsonl"),
            "cwd": str(self.project),
            "permission_mode": "default",
            "hook_event_name": "PostToolBatch",
            "tool_calls": [
                {
                    "tool_name": "Read",
                    "tool_input": {"file_path": str(self.project / "a.py")},
                    "tool_use_id": "toolu_01",
                    "tool_response": "     1\tprint('hello')\n",
                }
            ],
        }
        return self._put(
            payload,
            {"session_id": session_id, "prompt_id": prompt_id, "agent_id": agent_id},
        )

    def stop_input(
        self,
        message: Any = ENGLISH_MESSAGE,
        *,
        stop_hook_active: Any = False,
        session_id: Any = SESSION_ID,
        prompt_id: Any = PROMPT_ID,
    ) -> dict[str, Any]:
        """Stop hook の入力 JSON を組み立てる。MISSING を渡したキーは省く。"""
        payload: dict[str, Any] = {
            "transcript_path": str(self.home / "transcript.jsonl"),
            "cwd": str(self.project),
            "permission_mode": "default",
            "hook_event_name": "Stop",
        }
        return self._put(
            payload,
            {
                "session_id": session_id,
                "prompt_id": prompt_id,
                "stop_hook_active": stop_hook_active,
                "last_assistant_message": message,
            },
        )

    # --- 起動 -------------------------------------------------------------

    def run_script(
        self,
        script: Path,
        payload: dict[str, Any] | str,
        *,
        path: str | None = None,
        project_dir_env: str | None = None,
    ) -> tuple[int, str]:
        """script を起動し (exit code, stdout) を返す。str の payload はそのまま渡す。"""
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["TMPDIR"] = str(self.tmpdir)
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.pop("CLAUDE_PROJECT_DIR", None)
        if project_dir_env is not None:
            env["CLAUDE_PROJECT_DIR"] = project_dir_env
        if path is not None:
            env["PATH"] = path
        stdin = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False)
        )
        result = subprocess.run(
            [BASH, str(script)],
            input=stdin.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(self.project),
            timeout=30,
        )
        return result.returncode, result.stdout.decode("utf-8")

    def run_display(self, payload: dict[str, Any] | str, **kwargs: Any) -> None:
        """MessageDisplay hook を起動し、無出力かつ exit 0 で終わることを確認する。"""
        returncode, stdout = self.run_script(RECORD_HOOK, payload, **kwargs)
        self.assertEqual(returncode, 0, stdout)
        self.assertEqual(stdout, "", f"MessageDisplay hook が出力した: {stdout!r}")

    def display_message(self, text: str, **kwargs: Any) -> None:
        """メッセージ全体を index 0・final true の 1 回の呼び出しで表示する。"""
        self.run_display(self.display_input(text, **kwargs))

    def display_in_batches(self, deltas: list[str], **kwargs: Any) -> None:
        """メッセージを複数の batch に分けて表示する (最後の batch が final)。"""
        for index, delta in enumerate(deltas):
            self.run_display(
                self.display_input(
                    delta, index=index, final=index == len(deltas) - 1, **kwargs
                )
            )

    def assert_rewrite_requested(
        self, payload: dict[str, Any] | str, **kwargs: Any
    ) -> dict[str, Any]:
        """PostToolBatch hook が書き直し指示の JSON を 1 つ出すことを確認して返す。"""
        returncode, stdout = self.run_script(REWRITE_HOOK, payload, **kwargs)
        self.assertEqual(returncode, 0, stdout)
        self.assertNotEqual(
            stdout.strip(), "", "書き直しの指示を期待したが無出力だった"
        )
        decoder = json.JSONDecoder()
        output, end = decoder.raw_decode(stdout.strip())
        self.assertEqual(end, len(stdout.strip()), stdout)
        self.assertIsInstance(output, dict, stdout)
        return output

    def assert_no_rewrite(self, payload: dict[str, Any] | str, **kwargs: Any) -> None:
        """PostToolBatch hook が無出力かつ exit 0 で終わることを確認する。"""
        returncode, stdout = self.run_script(REWRITE_HOOK, payload, **kwargs)
        self.assertEqual(returncode, 0, stdout)
        self.assertEqual(stdout, "", f"無出力を期待したが出力があった: {stdout!r}")

    def run_stop(self, payload: dict[str, Any] | str, **kwargs: Any) -> str:
        """Stop hook を起動し、exit 0 を確認して stdout を返す。"""
        returncode, stdout = self.run_script(STOP_HOOK, payload, **kwargs)
        self.assertEqual(returncode, 0, stdout)
        return stdout


@unittest.skipUnless(JQ_AVAILABLE, "hook の判定には jq が必要")
class MessageDisplayRecordTest(MidTurnHookTestCase):
    """1 回の呼び出し (index 0・final true) で届くメッセージの判定と pending の内容。"""

    def test_english_message_creates_pending(self) -> None:
        self.display_message(ENGLISH_MESSAGE)
        self.assertIsNotNone(self.read_pending(), "英語のメッセージで pending が無い")

    def test_forty_letter_english_message_creates_pending(self) -> None:
        self.display_message(message_with_counts(0, 40))
        self.assertIsNotNone(self.read_pending())

    def test_japanese_message_does_not_create_pending(self) -> None:
        self.display_message(JAPANESE_MESSAGE)
        self.assertIsNone(self.read_pending())

    def test_japanese_message_with_english_terms_does_not_create_pending(self) -> None:
        self.assertGreaterEqual(
            count_ascii_letters(JAPANESE_MESSAGE_WITH_ENGLISH_TERMS), 40
        )
        self.display_message(JAPANESE_MESSAGE_WITH_ENGLISH_TERMS)
        self.assertIsNone(self.read_pending())

    def test_short_english_message_does_not_create_pending(self) -> None:
        self.display_message(message_with_counts(0, 39))
        self.assertIsNone(self.read_pending())

    def test_ratio_boundary_matches_the_stop_hook(self) -> None:
        # J=3, L=57: 3 / 60 = 0.05 (英語でない)
        self.display_message(message_with_counts(3, 57))
        self.assertIsNone(self.read_pending())
        # J=3, L=58: 3 / 61 = 0.0492 (英語)
        self.display_message(message_with_counts(3, 58))
        self.assertIsNotNone(self.read_pending())

    def test_code_and_url_are_stripped_before_judgement(self) -> None:
        message = (
            japanese_text(3)
            + "。\n```sh\n"
            + english_text(200)
            + "\n```\n`"
            + english_text(60)
            + "` https://example.com/"
            + english_text(60, separator="/")
        )
        self.display_message(message)
        self.assertIsNone(self.read_pending())

    def test_pending_content_is_the_prompt_id(self) -> None:
        self.display_message(ENGLISH_MESSAGE, prompt_id=PROMPT_ID)
        self.assertEqual(self.read_pending(), PROMPT_ID)

    def test_pending_content_is_empty_without_string_prompt_id(self) -> None:
        for prompt_id in (MISSING, None, 123, ["x"]):
            with self.subTest(prompt_id=prompt_id):
                self.pending_path().unlink(missing_ok=True)
                self.display_message(ENGLISH_MESSAGE, prompt_id=prompt_id)
                self.assertEqual(self.read_pending(), "")

    def test_pending_is_created_under_the_session_directory(self) -> None:
        self.display_message(ENGLISH_MESSAGE, session_id=OTHER_SESSION_ID)
        self.assertIsNotNone(self.read_pending(OTHER_SESSION_ID))
        self.assertIsNone(self.read_pending(SESSION_ID))

    def test_longest_valid_ids_are_accepted(self) -> None:
        session_id = "S" * 128
        message_id = "a-Z_9" * 25 + "abc"
        self.assertEqual(len(message_id), 128)
        self.display_message(
            ENGLISH_MESSAGE, session_id=session_id, message_id=message_id
        )
        self.assertIsNotNone(self.read_pending(session_id))

    def test_stdout_is_always_empty(self) -> None:
        # run_display が各呼び出しの無出力と exit 0 を確認する
        self.display_message(ENGLISH_MESSAGE)
        self.display_message(JAPANESE_MESSAGE)
        self.run_display(self.display_input(ENGLISH_MESSAGE, index=0, final=False))
        self.run_display(self.display_input(ENGLISH_MESSAGE, index=1, final=True))


@unittest.skipUnless(JQ_AVAILABLE, "hook の判定には jq が必要")
class MessageDisplayBufferTest(MidTurnHookTestCase):
    """複数 batch に分けて届くメッセージの連結・バッファーの作り直し・削除。"""

    SHORT_ENGLISH_BATCHES = [
        english_text(25) + "\n",
        english_text(25) + "\n",
        english_text(20),
    ]

    def test_each_short_batch_alone_is_not_english(self) -> None:
        for number, delta in enumerate(self.SHORT_ENGLISH_BATCHES):
            with self.subTest(batch=number):
                self.assertLess(count_ascii_letters(delta), 40)
                session_id = f"single-{number}"
                self.display_message(delta, session_id=session_id)
                self.assertIsNone(self.read_pending(session_id))

    def test_short_english_batches_are_judged_as_a_whole(self) -> None:
        self.display_in_batches(self.SHORT_ENGLISH_BATCHES)
        self.assertIsNotNone(
            self.read_pending(), "連結すると英語になるメッセージで pending が無い"
        )

    def test_final_batch_with_empty_delta_judges_the_buffer(self) -> None:
        # 対話モードでは、メッセージが改行で終わると final の delta は空になる
        self.display_in_batches([english_text(30) + "\n", english_text(30) + "\n", ""])
        self.assertIsNotNone(self.read_pending())

    def test_japanese_in_a_later_batch_counts_for_the_whole_message(self) -> None:
        # J=10, L=40: 10 / 50 >= 0.05 (英語でない)
        self.display_in_batches(
            [english_text(30) + "\n", japanese_text(10) + "。\n", english_text(10)]
        )
        self.assertIsNone(self.read_pending())

    def test_non_final_batch_does_not_judge(self) -> None:
        self.run_display(self.display_input(ENGLISH_MESSAGE, index=0, final=False))
        self.assertIsNone(self.read_pending())

    def test_index_zero_creates_the_buffer_with_delta(self) -> None:
        self.run_display(self.display_input("first line\n", index=0, final=False))
        self.assertEqual(self.read_buffer(), "first line\n")

    def test_later_batches_append_to_the_buffer(self) -> None:
        self.run_display(self.display_input("first\n", index=0, final=False))
        self.run_display(self.display_input("second\n", index=1, final=False))
        self.run_display(self.display_input("third\n", index=2, final=False))
        self.assertEqual(self.read_buffer(), "first\nsecond\nthird\n")

    def test_index_zero_recreates_the_buffer(self) -> None:
        self.run_display(self.display_input("old one\n", index=0, final=False))
        self.run_display(self.display_input("old two\n", index=1, final=False))
        self.run_display(self.display_input("new\n", index=0, final=False))
        self.assertEqual(self.read_buffer(), "new\n")

    def test_index_zero_discards_previous_english_text(self) -> None:
        self.run_display(
            self.display_input(ENGLISH_MESSAGE + "\n", index=0, final=False)
        )
        self.run_display(self.display_input(JAPANESE_MESSAGE, index=0, final=True))
        self.assertIsNone(self.read_pending())

    def test_final_batch_removes_the_buffer(self) -> None:
        for deltas in (
            self.SHORT_ENGLISH_BATCHES,
            [JAPANESE_MESSAGE + "\n", JAPANESE_MESSAGE],
        ):
            with self.subTest(deltas=deltas):
                self.display_in_batches(deltas)
                self.assertFalse(self.buffer_path().exists())

    def test_single_call_leaves_no_buffer(self) -> None:
        self.display_message(ENGLISH_MESSAGE)
        self.assertFalse(self.buffer_path().exists())

    def test_final_batch_removes_the_buffer_when_language_is_not_japanese(
        self,
    ) -> None:
        self.write_settings("user", {"language": "English"})
        self.display_in_batches(self.SHORT_ENGLISH_BATCHES)
        self.assertFalse(self.buffer_path().exists())
        self.assertIsNone(self.read_pending())

    def test_buffers_are_separate_per_message_id(self) -> None:
        self.run_display(
            self.display_input("a1\n", index=0, final=False, message_id=MESSAGE_ID)
        )
        self.run_display(
            self.display_input(
                "b1\n", index=0, final=False, message_id=OTHER_MESSAGE_ID
            )
        )
        self.run_display(
            self.display_input("a2\n", index=1, final=False, message_id=MESSAGE_ID)
        )
        self.assertEqual(self.read_buffer(MESSAGE_ID), "a1\na2\n")
        self.assertEqual(self.read_buffer(OTHER_MESSAGE_ID), "b1\n")

    def test_japanese_message_does_not_hide_english_in_another_message(self) -> None:
        self.run_display(
            self.display_input(
                english_text(30) + "\n", index=0, final=False, message_id=MESSAGE_ID
            )
        )
        self.run_display(
            self.display_input(
                JAPANESE_MESSAGE, index=0, final=True, message_id=OTHER_MESSAGE_ID
            )
        )
        self.assertIsNone(self.read_pending())
        self.run_display(
            self.display_input(
                english_text(30), index=1, final=True, message_id=MESSAGE_ID
            )
        )
        self.assertIsNotNone(self.read_pending())


@unittest.skipUnless(JQ_AVAILABLE, "hook の判定には jq が必要")
class MessageDisplayGuardTest(MidTurnHookTestCase):
    """何もしない入力 (無出力・exit 0 で、状態を読み書きしない)。"""

    def assert_display_changes_nothing(self, payload: dict[str, Any]) -> None:
        before = self.snapshot()
        self.run_display(payload)
        self.assertEqual(self.snapshot(), before)

    def test_agent_id_skips_the_message(self) -> None:
        for final in (True, False):
            with self.subTest(final=final):
                self.assert_display_changes_nothing(
                    self.display_input(ENGLISH_MESSAGE, final=final, agent_id="a1b2c3")
                )

    def test_empty_agent_id_is_treated_as_the_main_thread(self) -> None:
        self.display_message(ENGLISH_MESSAGE, agent_id="")
        self.assertIsNotNone(self.read_pending())

    def test_non_japanese_target_language_does_not_create_pending(self) -> None:
        for language in ("English", "en", "ja_JP", 123):
            with self.subTest(language=language):
                self.write_settings("user", {"language": language})
                self.display_message(ENGLISH_MESSAGE)
                self.assertIsNone(self.read_pending())

    def test_unset_target_language_does_not_create_pending(self) -> None:
        self.remove_settings("user")
        self.display_message(ENGLISH_MESSAGE)
        self.assertIsNone(self.read_pending())

    def test_project_settings_are_read_from_input_cwd(self) -> None:
        self.write_settings("project", {"language": "English"})
        self.display_message(ENGLISH_MESSAGE)
        self.assertIsNone(self.read_pending())

    def test_project_dir_env_wins_over_cwd(self) -> None:
        root = self.base / "env-project"
        (root / ".claude").mkdir(parents=True)
        (root / ".claude" / "settings.json").write_text(
            json.dumps({"language": "ja"}), encoding="utf-8"
        )
        self.write_settings("project", {"language": "English"})
        self.write_settings("user", {"language": "English"})
        self.run_display(self.display_input(ENGLISH_MESSAGE), project_dir_env=str(root))
        self.assertIsNotNone(self.read_pending())

    def test_invalid_session_id_writes_nothing(self) -> None:
        for session_id in INVALID_STRING_IDS + NON_STRING_IDS + (MISSING,):
            for final in (True, False):
                with self.subTest(session_id=session_id, final=final):
                    self.assert_display_changes_nothing(
                        self.display_input(
                            ENGLISH_MESSAGE, final=final, session_id=session_id
                        )
                    )

    def test_invalid_message_id_writes_nothing(self) -> None:
        for message_id in INVALID_STRING_IDS + NON_STRING_IDS + (MISSING,):
            for final in (True, False):
                with self.subTest(message_id=message_id, final=final):
                    self.assert_display_changes_nothing(
                        self.display_input(
                            ENGLISH_MESSAGE, final=final, message_id=message_id
                        )
                    )

    def test_invalid_index_does_nothing(self) -> None:
        for index in (-1, 1.5, "0", None, True, [0], MISSING):
            with self.subTest(index=index):
                self.assert_display_changes_nothing(
                    self.display_input(ENGLISH_MESSAGE, index=index, final=True)
                )

    def test_invalid_final_does_nothing(self) -> None:
        for final in ("true", 1, 0, None, MISSING):
            with self.subTest(final=final):
                self.assert_display_changes_nothing(
                    self.display_input(ENGLISH_MESSAGE, index=0, final=final)
                )

    def test_invalid_delta_does_nothing(self) -> None:
        for delta in (MISSING, None, 123, [ENGLISH_MESSAGE], {"text": "x"}):
            with self.subTest(delta=delta):
                self.assert_display_changes_nothing(
                    self.display_input(delta, index=0, final=True)
                )

    def test_invalid_batch_keeps_the_existing_buffer(self) -> None:
        self.run_display(
            self.display_input(english_text(30) + "\n", index=0, final=False)
        )
        self.assertEqual(self.read_buffer(), english_text(30) + "\n")
        self.assert_display_changes_nothing(
            self.display_input(english_text(30), index="1", final=True)
        )
        self.assertEqual(self.read_buffer(), english_text(30) + "\n")
        self.assertIsNone(self.read_pending())


class MessageDisplayFailOpenTest(MidTurnHookTestCase):
    """壊れた入力・jq 不在では何もしない (無出力・exit 0・状態の変化なし)。"""

    def test_broken_input_json(self) -> None:
        broken = json.dumps(self.display_input(ENGLISH_MESSAGE))[:-1]
        for raw in (broken, "{", "not json", ""):
            with self.subTest(raw=raw[:40]):
                before = self.snapshot()
                self.run_display(raw)
                self.assertEqual(self.snapshot(), before)

    def test_input_json_that_is_not_an_object(self) -> None:
        for raw in ("[]", "null", json.dumps(ENGLISH_MESSAGE), "42"):
            with self.subTest(raw=raw[:40]):
                before = self.snapshot()
                self.run_display(raw)
                self.assertEqual(self.snapshot(), before)

    def test_jq_missing_is_fail_open(self) -> None:
        path = self.path_without_jq()
        before = self.snapshot()
        self.run_display(self.display_input(ENGLISH_MESSAGE), path=path)
        self.run_display(
            self.display_input(ENGLISH_MESSAGE, index=0, final=False), path=path
        )
        self.assertEqual(self.snapshot(), before)


@unittest.skipUnless(JQ_AVAILABLE, "hook の出力には jq が必要")
class PostToolBatchRewriteTest(MidTurnHookTestCase):
    """pending があれば書き直しを指示し、pending を消費する。"""

    def test_no_pending_means_no_output(self) -> None:
        self.assert_no_rewrite(self.batch_input())

    def test_pending_leads_to_the_rewrite_request(self) -> None:
        self.write_pending(PROMPT_ID)
        output = self.assert_rewrite_requested(self.batch_input())
        self.assertEqual(
            output,
            {
                "systemMessage": SYSTEM_MESSAGE,
                "hookSpecificOutput": {
                    "hookEventName": "PostToolBatch",
                    "additionalContext": REWRITE_CONTEXT,
                },
            },
        )

    def test_pending_is_removed_after_the_request(self) -> None:
        self.write_pending(PROMPT_ID)
        self.assert_rewrite_requested(self.batch_input())
        self.assertIsNone(self.read_pending())

    def test_second_call_has_no_output(self) -> None:
        self.write_pending(PROMPT_ID)
        self.assert_rewrite_requested(self.batch_input())
        self.assert_no_rewrite(self.batch_input())

    def test_output_keys(self) -> None:
        self.write_pending(PROMPT_ID)
        output = self.assert_rewrite_requested(self.batch_input())
        self.assertEqual(set(output), {"systemMessage", "hookSpecificOutput"})
        self.assertEqual(
            set(output["hookSpecificOutput"]), {"hookEventName", "additionalContext"}
        )
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PostToolBatch")

    def test_system_message_text(self) -> None:
        self.write_pending(PROMPT_ID)
        output = self.assert_rewrite_requested(self.batch_input())
        self.assertEqual(output["systemMessage"], SYSTEM_MESSAGE)

    def test_additional_context_text(self) -> None:
        self.write_pending(PROMPT_ID)
        output = self.assert_rewrite_requested(self.batch_input())
        self.assertEqual(
            output["hookSpecificOutput"]["additionalContext"], REWRITE_CONTEXT
        )

    def test_additional_context_states_the_rewrite_instruction(self) -> None:
        for phrase in REWRITE_REQUIRED_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, REWRITE_CONTEXT)
        self.write_pending(PROMPT_ID)
        context = self.assert_rewrite_requested(self.batch_input())[
            "hookSpecificOutput"
        ]["additionalContext"]
        for phrase in REWRITE_REQUIRED_PHRASES:
            with self.subTest(phrase=phrase, source="output"):
                self.assertIn(phrase, context)

    def test_texts_are_not_english_by_the_judgement(self) -> None:
        for text in (SYSTEM_MESSAGE, REWRITE_CONTEXT):
            with self.subTest(text=text[:20]):
                self.assertFalse(is_english_by_judgement(text), text)
        self.write_pending(PROMPT_ID)
        output = self.assert_rewrite_requested(self.batch_input())
        for text in (
            output["systemMessage"],
            output["hookSpecificOutput"]["additionalContext"],
        ):
            with self.subTest(text=text[:20], source="output"):
                self.assertFalse(is_english_by_judgement(text), text)

    def test_different_prompt_ids_mean_no_output(self) -> None:
        self.write_pending(PROMPT_ID)
        self.assert_no_rewrite(self.batch_input(prompt_id=OTHER_PROMPT_ID))
        self.assertIsNone(self.read_pending(), "持ち越した pending が消えていない")

    def test_same_prompt_id_outputs(self) -> None:
        self.write_pending(PROMPT_ID)
        self.assert_rewrite_requested(self.batch_input(prompt_id=PROMPT_ID))

    def test_empty_pending_content_outputs(self) -> None:
        self.write_pending("")
        self.assert_rewrite_requested(self.batch_input(prompt_id=PROMPT_ID))
        self.assertIsNone(self.read_pending())

    def test_missing_or_non_string_input_prompt_id_outputs(self) -> None:
        for prompt_id in (MISSING, None, 123, ""):
            with self.subTest(prompt_id=prompt_id):
                self.write_pending(PROMPT_ID)
                self.assert_rewrite_requested(self.batch_input(prompt_id=prompt_id))
                self.assertIsNone(self.read_pending())

    def test_both_prompt_ids_empty_outputs(self) -> None:
        self.write_pending("")
        self.assert_rewrite_requested(self.batch_input(prompt_id=MISSING))

    def test_agent_id_skips_and_keeps_pending(self) -> None:
        self.write_pending(PROMPT_ID)
        self.assert_no_rewrite(self.batch_input(agent_id="a1b2c3"))
        self.assertEqual(self.read_pending(), PROMPT_ID)

    def test_empty_agent_id_is_treated_as_the_main_thread(self) -> None:
        self.write_pending(PROMPT_ID)
        self.assert_rewrite_requested(self.batch_input(agent_id=""))
        self.assertIsNone(self.read_pending())

    def test_pending_of_another_session_is_ignored(self) -> None:
        self.write_pending(PROMPT_ID, session_id=OTHER_SESSION_ID)
        self.assert_no_rewrite(self.batch_input(session_id=SESSION_ID))
        self.assertEqual(self.read_pending(OTHER_SESSION_ID), PROMPT_ID)

    def test_request_consumes_only_its_own_session(self) -> None:
        self.write_pending(PROMPT_ID, session_id=SESSION_ID)
        self.write_pending(PROMPT_ID, session_id=OTHER_SESSION_ID)
        self.assert_rewrite_requested(self.batch_input(session_id=SESSION_ID))
        self.assertIsNone(self.read_pending(SESSION_ID))
        self.assertEqual(self.read_pending(OTHER_SESSION_ID), PROMPT_ID)

    def test_invalid_session_id_touches_nothing(self) -> None:
        # `../x` をそのままパスに使うと読まれてしまう位置に pending を置いておく
        traversal_pending = self.tmpdir / "x" / "pending"
        traversal_pending.parent.mkdir(parents=True)
        traversal_pending.write_text("", encoding="utf-8")
        for session_id in INVALID_STRING_IDS + NON_STRING_IDS + (MISSING,):
            with self.subTest(session_id=session_id):
                before = self.snapshot()
                self.assert_no_rewrite(self.batch_input(session_id=session_id))
                self.assertEqual(self.snapshot(), before)


class PostToolBatchFailOpenTest(MidTurnHookTestCase):
    """壊れた入力・jq 不在では何もしない (無出力・exit 0・pending を残す)。"""

    def test_broken_input_json_keeps_pending(self) -> None:
        self.write_pending(PROMPT_ID)
        broken = json.dumps(self.batch_input())[:-1]
        for raw in (broken, "{", "not json", ""):
            with self.subTest(raw=raw[:40]):
                self.assert_no_rewrite(raw)
                self.assertEqual(self.read_pending(), PROMPT_ID)

    def test_input_json_that_is_not_an_object_keeps_pending(self) -> None:
        self.write_pending(PROMPT_ID)
        for raw in ("[]", "null", json.dumps(SESSION_ID), "42"):
            with self.subTest(raw=raw[:40]):
                self.assert_no_rewrite(raw)
                self.assertEqual(self.read_pending(), PROMPT_ID)

    def test_jq_missing_is_fail_open(self) -> None:
        self.write_pending(PROMPT_ID)
        self.assert_no_rewrite(self.batch_input(), path=self.path_without_jq())
        self.assertEqual(self.read_pending(), PROMPT_ID)


@unittest.skipUnless(JQ_AVAILABLE, "hook の判定には jq が必要")
class StopClearsPendingTest(MidTurnHookTestCase):
    """Stop hook は判定より前にその session の pending を消す。"""

    def assert_block_output(self, stdout: str) -> None:
        self.assertNotEqual(stdout.strip(), "", "block を期待したが無出力だった")
        output = json.loads(stdout)
        self.assertEqual(set(output), {"decision", "reason"}, stdout)
        self.assertEqual(output["decision"], "block", stdout)

    def test_english_message_blocks_and_clears_pending(self) -> None:
        self.write_pending(PROMPT_ID)
        stdout = self.run_stop(self.stop_input(ENGLISH_MESSAGE))
        self.assert_block_output(stdout)
        self.assertIsNone(self.read_pending())

    def test_japanese_message_clears_pending(self) -> None:
        self.write_pending(PROMPT_ID)
        self.assertEqual(self.run_stop(self.stop_input(JAPANESE_MESSAGE)), "")
        self.assertIsNone(self.read_pending())

    def test_stop_hook_active_clears_pending(self) -> None:
        self.write_pending(PROMPT_ID)
        stdout = self.run_stop(self.stop_input(ENGLISH_MESSAGE, stop_hook_active=True))
        self.assertEqual(stdout, "")
        self.assertIsNone(self.read_pending())

    def test_unset_language_clears_pending(self) -> None:
        self.remove_settings("user")
        self.write_pending(PROMPT_ID)
        self.assertEqual(self.run_stop(self.stop_input(ENGLISH_MESSAGE)), "")
        self.assertIsNone(self.read_pending())

    def test_non_japanese_language_clears_pending(self) -> None:
        self.write_settings("user", {"language": "English"})
        self.write_pending(PROMPT_ID)
        self.assertEqual(self.run_stop(self.stop_input(ENGLISH_MESSAGE)), "")
        self.assertIsNone(self.read_pending())

    def test_missing_last_assistant_message_clears_pending(self) -> None:
        self.write_pending(PROMPT_ID)
        self.assertEqual(self.run_stop(self.stop_input(MISSING)), "")
        self.assertIsNone(self.read_pending())

    def test_pending_of_another_session_remains(self) -> None:
        self.write_pending(PROMPT_ID, session_id=OTHER_SESSION_ID)
        self.run_stop(self.stop_input(JAPANESE_MESSAGE, session_id=SESSION_ID))
        self.assertEqual(self.read_pending(OTHER_SESSION_ID), PROMPT_ID)

    def test_invalid_session_id_touches_nothing_but_still_judges(self) -> None:
        traversal_pending = self.tmpdir / "x" / "pending"
        traversal_pending.parent.mkdir(parents=True)
        traversal_pending.write_text(PROMPT_ID, encoding="utf-8")
        for session_id in ("../x", "a/b", "", 123, MISSING):
            with self.subTest(session_id=session_id):
                before = self.snapshot()
                stdout = self.run_stop(
                    self.stop_input(ENGLISH_MESSAGE, session_id=session_id)
                )
                self.assert_block_output(stdout)
                self.assertEqual(self.snapshot(), before)

    def test_broken_input_keeps_pending(self) -> None:
        self.write_pending(PROMPT_ID)
        broken = json.dumps(self.stop_input(ENGLISH_MESSAGE))[:-1]
        for raw in (broken, "not json", "[]"):
            with self.subTest(raw=raw[:40]):
                self.assertEqual(self.run_stop(raw), "")
                self.assertEqual(self.read_pending(), PROMPT_ID)

    def test_jq_missing_keeps_pending(self) -> None:
        self.write_pending(PROMPT_ID)
        stdout = self.run_stop(
            self.stop_input(ENGLISH_MESSAGE), path=self.path_without_jq()
        )
        self.assertEqual(stdout, "")
        self.assertEqual(self.read_pending(), PROMPT_ID)


@unittest.skipUnless(JQ_AVAILABLE, "hook の判定には jq が必要")
class MidTurnFlowTest(MidTurnHookTestCase):
    """MessageDisplay → PostToolBatch → Stop の一連の流れ。"""

    def test_english_mid_turn_message_leads_to_the_rewrite_request(self) -> None:
        self.display_in_batches([english_text(60) + "\n", english_text(60) + "\n", ""])
        output = self.assert_rewrite_requested(self.batch_input())
        self.assertEqual(
            output["hookSpecificOutput"]["additionalContext"], REWRITE_CONTEXT
        )
        self.assert_no_rewrite(self.batch_input())

    def test_single_call_english_message_leads_to_the_rewrite_request(self) -> None:
        # `claude -p` 等の非対話の実行
        self.display_message(ENGLISH_MESSAGE)
        self.assert_rewrite_requested(self.batch_input())

    def test_japanese_mid_turn_message_leads_to_no_request(self) -> None:
        self.display_in_batches([JAPANESE_MESSAGE + "\n", JAPANESE_MESSAGE])
        self.assert_no_rewrite(self.batch_input())

    def test_rewritten_japanese_message_leads_to_no_further_request(self) -> None:
        self.display_message(ENGLISH_MESSAGE)
        self.assert_rewrite_requested(self.batch_input())
        self.display_message(JAPANESE_MESSAGE, message_id=OTHER_MESSAGE_ID)
        self.assert_no_rewrite(self.batch_input())

    def test_stop_prevents_carry_over_to_the_next_turn(self) -> None:
        self.display_message(ENGLISH_MESSAGE, prompt_id=MISSING)
        self.run_stop(self.stop_input(JAPANESE_MESSAGE, prompt_id=MISSING))
        self.assert_no_rewrite(self.batch_input(prompt_id=MISSING))

    def test_new_prompt_does_not_inherit_the_previous_pending(self) -> None:
        self.display_message(ENGLISH_MESSAGE, prompt_id=PROMPT_ID)
        self.assert_no_rewrite(self.batch_input(prompt_id=OTHER_PROMPT_ID))
        self.assert_no_rewrite(self.batch_input(prompt_id=OTHER_PROMPT_ID))

    def test_subagent_batch_does_not_consume_the_main_pending(self) -> None:
        self.display_message(ENGLISH_MESSAGE)
        self.assert_no_rewrite(self.batch_input(agent_id="a1b2c3"))
        self.assert_rewrite_requested(self.batch_input())

    def test_subagent_message_leads_to_no_request(self) -> None:
        self.display_message(ENGLISH_MESSAGE, agent_id="a1b2c3")
        self.assert_no_rewrite(self.batch_input())

    def test_non_japanese_target_language_leads_to_no_request(self) -> None:
        self.write_settings("user", {"language": "English"})
        self.display_message(ENGLISH_MESSAGE)
        self.assert_no_rewrite(self.batch_input())


if __name__ == "__main__":
    unittest.main()
