"""enforce-japanese-response hook (plugins/enforce-japanese-response) の受入テスト。

hook script を subprocess で起動し、stdin に Stop hook の JSON を渡して stdout と
exit code を検証する。期待値は hook の公開契約であり、実装都合で変更しない。

## 対象 hook の契約

- 入力: Stop hook の stdin JSON。`last_assistant_message` / `stop_hook_active` / `cwd`
  を使う
- 出力: 英語の応答と判定した場合のみ stdout に
  `{"decision": "block", "reason": "<日本語の reason>"}`。それ以外は無出力
- exit code: どの場合も 0
- 除去処理: fenced code block (``` から次の ``` まで。閉じが無ければ末尾まで)、
  インライン code (` から次の ` まで)、URL (`http://` / `https://` と、それに続く
  1 文字以上の URL 本体) をこの順に取り除く。URL 本体は印字可能な ASCII (0x21〜0x7E)
  のうち `(` `)` `<` `>` `[` `]` `"` を除いた文字の並びで、空白文字・印字可能な ASCII
  以外の文字 (日本語など)・上記 7 文字のいずれかの直前で終わる
- 判定: 除去後の本文の ASCII 英字数 L と、ひらがな・カタカナ・漢字の数 J について、
  `L >= 40` かつ `J / (J + L) < 0.05` なら英語の応答
- 目標言語: `<cwd>/.claude/settings.local.json` → `<cwd>/.claude/settings.json` →
  `$HOME/.claude/settings.json` の順に `language` キーを持つ最初のファイルの値を使う。
  存在しない・JSON object として解析できない・`language` を持たないファイルは飛ばす。
  `日本語` / `ja` / `ja-` 始まり / `japanese` (英字は大文字小文字を区別しない) を日本語と
  みなし、それ以外と未設定では判定しない
- fail-open: jq が無い・入力を解析できない等で判定できなければ無出力

## 隔離

各テストは一時ディレクトリに HOME とプロジェクト (`cwd`) を作り、`HOME` を env で、
`cwd` を入力 JSON で渡す。実際の `~/.claude` は読まない。

## テストグループ

- ThresholdTest: L と比率の境界
- StripTest: fenced code block / インライン code / URL の除去
- InputGuardTest: stop_hook_active・last_assistant_message・壊れた入力・jq 不在
- LanguageValueTest: language の各表記と日本語以外の値・未設定
- SettingsLookupTest: settings ファイルの探索順と解析できないファイルの扱い
- BlockOutputTest: block 時の出力形式と reason の内容
- PluginWiringTest: hooks.json への登録と script の実行権限
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "enforce-japanese-response"
HOOK = PLUGIN_DIR / "hooks" / "scripts" / "enforce-japanese-response.sh"
HOOKS_JSON = PLUGIN_DIR / "hooks" / "hooks.json"

BASH = shutil.which("bash") or "/bin/bash"
JQ_AVAILABLE = shutil.which("jq") is not None

# 入力 JSON からキーを省くことを表す印。
MISSING = object()

# reason に含まれるべき趣旨 (直前の応答が英語であること / 内容を変えずに日本語で
# 書き直すこと / 英語での出力を明示的に求められていた場合の扱い)。
REASON_REQUIRED_PHRASES = (
    "英語で書かれ",
    "内容を変えず",
    "日本語で書き直",
    "英語での出力",
    "明示的に求め",
    "書き直さず",
    "日本語 1 文",
)

# 英字だけから成る単語。english_text が英字数を正確に合わせるために使う。
ENGLISH_WORDS = (
    "the",
    "build",
    "finished",
    "and",
    "all",
    "checks",
    "passed",
    "without",
    "errors",
    "waiting",
    "for",
    "notification",
)
# ひらがなと漢字だけから成る文字列。japanese_text が J を正確に合わせるために使う。
JAPANESE_SOURCE = "日本語で書いた報告です"

# jq を除いた PATH を作るときに symlink する外部コマンド。
PATH_TOOLS_WITHOUT_JQ = (
    "bash",
    "sh",
    "cat",
    "env",
    "printf",
    "tr",
    "sed",
    "grep",
    "awk",
    "head",
    "tail",
    "wc",
    "cut",
    "dirname",
    "basename",
    "mktemp",
    "rm",
    "uname",
)


def is_japanese_char(char: str) -> bool:
    """fixture 検証用: ひらがな・カタカナ・CJK 統合漢字のブロックに入る文字か。"""
    code = ord(char)
    return (
        0x3041 <= code <= 0x309F or 0x30A1 <= code <= 0x30FA or 0x4E00 <= code <= 0x9FFF
    )


def count_ascii_letters(text: str) -> int:
    return sum(1 for char in text if "A" <= char <= "Z" or "a" <= char <= "z")


def count_japanese(text: str) -> int:
    return sum(1 for char in text if is_japanese_char(char))


def english_text(letters: int, *, separator: str = " ") -> str:
    """英字がちょうど `letters` 個の英文を返す (区切りと末尾の `.` は数に入らない)。"""
    words: list[str] = []
    remaining = letters
    index = 0
    while remaining > 0:
        word = ENGLISH_WORDS[index % len(ENGLISH_WORDS)][:remaining]
        words.append(word)
        remaining -= len(word)
        index += 1
    text = separator.join(words) + ("." if separator == " " and words else "")
    assert count_ascii_letters(text) == letters
    return text


def japanese_text(chars: int) -> str:
    """ひらがな・漢字がちょうど `chars` 個の文字列を返す。"""
    repeated = JAPANESE_SOURCE * (chars // len(JAPANESE_SOURCE) + 1)
    text = repeated[:chars]
    assert count_japanese(text) == chars
    assert count_ascii_letters(text) == 0
    return text


def message_with_counts(japanese: int, letters: int) -> str:
    """J = `japanese`、L = `letters` になる応答本文 (code・URL を含まない)。"""
    parts = []
    if japanese:
        parts.append(japanese_text(japanese) + "。")
    if letters:
        parts.append(english_text(letters))
    text = " ".join(parts)
    assert count_japanese(text) == japanese
    assert count_ascii_letters(text) == letters
    return text


# 典型的な英語の応答 (block される)。
ENGLISH_MESSAGE = message_with_counts(0, 200)


class HookTestCase(unittest.TestCase):
    """一時ディレクトリの HOME とプロジェクトで hook を起動する基底クラス。

    既定では `$HOME/.claude/settings.json` の `language` を `日本語` にする。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.home = base / "home"
        self.project = base / "project"
        self.home.mkdir()
        self.project.mkdir()
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
        """settings を書く。`content` が str ならそのまま、それ以外は JSON で書く。"""
        path = self.settings_path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")

    def remove_settings(self, scope: str) -> None:
        self.settings_path(scope).unlink(missing_ok=True)

    # --- 入力と起動 ---------------------------------------------------------

    def stop_input(
        self,
        message: Any = ENGLISH_MESSAGE,
        *,
        stop_hook_active: Any = False,
        cwd: Any = None,
    ) -> dict[str, Any]:
        """Stop hook の入力 JSON を組み立てる。MISSING を渡したキーは省く。"""
        payload: dict[str, Any] = {
            "session_id": "test-session",
            "transcript_path": str(self.home / "transcript.jsonl"),
            "hook_event_name": "Stop",
            "permission_mode": "default",
        }
        values = {
            "cwd": str(self.project) if cwd is None else cwd,
            "stop_hook_active": stop_hook_active,
            "last_assistant_message": message,
        }
        for key, value in values.items():
            if value is not MISSING:
                payload[key] = value
        return payload

    def run_hook(
        self, payload: dict[str, Any] | str, *, path: str | None = None
    ) -> tuple[int, str]:
        """hook を起動し (exit code, stdout) を返す。str の payload はそのまま stdin に渡す。"""
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env.pop("CLAUDE_CONFIG_DIR", None)
        if path is not None:
            env["PATH"] = path
        stdin = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False)
        )
        result = subprocess.run(
            [BASH, str(HOOK)],
            input=stdin.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(self.project),
            timeout=30,
        )
        return result.returncode, result.stdout.decode("utf-8")

    # --- 期待値 -------------------------------------------------------------

    def assert_block(self, payload: dict[str, Any] | str, **kwargs: Any) -> dict:
        """block JSON を出し exit 0 で終わることを確認し、解析した出力を返す。"""
        returncode, stdout = self.run_hook(payload, **kwargs)
        self.assertEqual(returncode, 0, stdout)
        self.assertNotEqual(
            stdout.strip(), "", "英語の応答として block を期待したが無出力だった"
        )
        output = json.loads(stdout)
        self.assertIsInstance(output, dict, stdout)
        self.assertEqual(set(output), {"decision", "reason"}, stdout)
        self.assertEqual(output["decision"], "block", stdout)
        self.assertIsInstance(output["reason"], str, stdout)
        return output

    def assert_no_output(self, payload: dict[str, Any] | str, **kwargs: Any) -> None:
        """無出力かつ exit 0 で終わることを確認する。"""
        returncode, stdout = self.run_hook(payload, **kwargs)
        self.assertEqual(returncode, 0, stdout)
        self.assertEqual(stdout, "", f"無出力を期待したが出力があった: {stdout!r}")


@unittest.skipUnless(JQ_AVAILABLE, "hook の判定には jq が必要")
class ThresholdTest(HookTestCase):
    """L >= 40 と J / (J + L) < 0.05 の境界。"""

    def test_39_letters_without_japanese_is_not_english(self) -> None:
        self.assert_no_output(self.stop_input(message_with_counts(0, 39)))

    def test_40_letters_without_japanese_is_english(self) -> None:
        self.assert_block(self.stop_input(message_with_counts(0, 40)))

    def test_long_english_message_is_english(self) -> None:
        self.assert_block(self.stop_input(ENGLISH_MESSAGE))

    def test_ratio_below_005_is_english(self) -> None:
        # J=3, L=58: 3 / 61 = 0.0492
        self.assert_block(self.stop_input(message_with_counts(3, 58)))

    def test_ratio_equal_to_005_is_not_english(self) -> None:
        # J=3, L=57: 3 / 60 = 0.05
        self.assert_no_output(self.stop_input(message_with_counts(3, 57)))

    def test_ratio_above_005_is_not_english(self) -> None:
        # J=3, L=56: 3 / 59 = 0.0508
        self.assert_no_output(self.stop_input(message_with_counts(3, 56)))

    def test_ratio_boundary_with_other_counts(self) -> None:
        # J=4, L=76: 4 / 80 = 0.05
        self.assert_no_output(self.stop_input(message_with_counts(4, 76)))
        # J=4, L=77: 4 / 81 = 0.0494
        self.assert_block(self.stop_input(message_with_counts(4, 77)))

    def test_digits_and_symbols_are_not_counted_as_letters(self) -> None:
        message = english_text(39) + " 0123456789 !?#%&*()[]{} 42."
        self.assert_no_output(self.stop_input(message))

    def test_japanese_message_with_english_terms_is_not_english(self) -> None:
        message = (
            "PR を draft で作成し、GitHub Actions の CI で lint と unit test が"
            " pass したことを確認しました。review の結果を待っています。"
        )
        # 英字は 40 字以上あり、比率の条件だけで英語の応答から外れる
        self.assertGreaterEqual(count_ascii_letters(message), 40)
        self.assert_no_output(self.stop_input(message))

    def test_katakana_is_counted_as_japanese(self) -> None:
        # カタカナ 3 字 + L=57 で比率 0.05 ちょうど (block しない)
        message = "テスト " + english_text(57)
        self.assertEqual(count_japanese(message), 3)
        self.assert_no_output(self.stop_input(message))


@unittest.skipUnless(JQ_AVAILABLE, "hook の判定には jq が必要")
class StripTest(HookTestCase):
    """fenced code block / インライン code / URL を除いてから数える。"""

    def test_fenced_code_block_only_message_is_not_english(self) -> None:
        message = "```python\n" + english_text(300) + "\n```"
        self.assert_no_output(self.stop_input(message))

    def test_multiple_fenced_code_blocks_only_message_is_not_english(self) -> None:
        block = "```\n" + english_text(100) + "\n```"
        message = block + "\n\n" + block
        self.assert_no_output(self.stop_input(message))

    def test_english_inside_fenced_code_block_is_not_counted(self) -> None:
        # 除去しなければ L=220, J=5 (5 / 225 < 0.05) で block になる
        message = (
            japanese_text(5)
            + "。\n```sh\n"
            + english_text(200)
            + "\n```\n"
            + english_text(20)
        )
        self.assert_no_output(self.stop_input(message))

    def test_japanese_inside_fenced_code_block_is_not_counted(self) -> None:
        # 除去しなければ J=30 で block されない。除去後は J=0, L=40
        message = english_text(40) + "\n```\n" + japanese_text(30) + "\n```\n"
        self.assert_block(self.stop_input(message))

    def test_unclosed_fenced_code_block_is_stripped_to_the_end(self) -> None:
        message = japanese_text(10) + "。\n```\n" + english_text(200)
        self.assert_no_output(self.stop_input(message))

    def test_english_inside_inline_code_is_not_counted(self) -> None:
        # 除去しなければ L=100, J=3 (3 / 103 < 0.05) で block になる
        message = japanese_text(3) + " `" + english_text(100) + "` 。"
        self.assert_no_output(self.stop_input(message))

    def test_japanese_inside_inline_code_is_not_counted(self) -> None:
        message = english_text(40) + " `" + japanese_text(10) + "`"
        self.assert_block(self.stop_input(message))

    def test_english_inside_https_url_is_not_counted(self) -> None:
        url = "https://example.com/" + english_text(100, separator="/")
        message = japanese_text(3) + " " + url
        self.assert_no_output(self.stop_input(message))

    def test_english_inside_http_url_is_not_counted(self) -> None:
        url = "http://example.com/" + english_text(100, separator="-")
        message = japanese_text(3) + " " + url + "\n"
        self.assert_no_output(self.stop_input(message))

    def test_japanese_right_after_url_path_is_counted(self) -> None:
        # URL は日本語の直前で終わるため、URL に続く日本語 (J=20) は数えられる
        # (J=20, L=40 で 20 / 60 >= 0.05)
        message = (
            english_text(40) + " https://ja.wikipedia.org/wiki/" + japanese_text(20)
        )
        self.assert_no_output(self.stop_input(message))

    def test_japanese_sentence_right_after_url_is_counted(self) -> None:
        # URL の直後に空白なしで続く日本語 (J=10) は数えられる (10 / 70 >= 0.05)。
        # 日本語まで除去すると J=0, L=60 で block になる
        url = "https://github.com/example/" + english_text(60, separator="/")
        message = url + japanese_text(10) + "。 " + english_text(60)
        self.assert_no_output(self.stop_input(message))

    def test_markdown_link_with_english_text_is_not_english(self) -> None:
        # リンク文字 (L=50) と、閉じ括弧の直後の日本語 (J=7) を数える (7 / 57 >= 0.05)。
        # 閉じ括弧以降まで URL として除去すると J=0, L=50 で block になる
        tail = "を作成しました。"
        self.assertEqual(count_japanese(tail), 7)
        message = "[" + english_text(50) + "](https://github.com/o/r/pull/1)" + tail
        self.assert_no_output(self.stop_input(message))

    def test_url_ends_before_delimiter_characters(self) -> None:
        # URL は区切り文字の直前で終わり、区切り文字の後ろの英字 (L=40) は数えられる
        # (J=1, L=40 で block)。区切り文字以降まで除去すると L=0 で block しない
        for delimiter in (")", "]", ">", '"', "(", "<", "["):
            with self.subTest(delimiter=delimiter):
                message = (
                    japanese_text(1)
                    + " https://example.com/path"
                    + delimiter
                    + english_text(40, separator="-")
                )
                self.assert_block(self.stop_input(message))

    def test_url_keeps_other_ascii_punctuation(self) -> None:
        # クエリ・フラグメント等の ASCII 記号は URL 本体に含まれ、英字ごと除去される
        url = (
            "https://example.com/search?q="
            + english_text(100, separator="+")
            + "&lang=en#top,v1.0;x='y'"
        )
        message = japanese_text(3) + " " + url
        self.assert_no_output(self.stop_input(message))

    def test_url_ends_at_whitespace(self) -> None:
        # URL の後ろの英文 (L=40) は除去されずに数えられる
        message = japanese_text(1) + " https://example.com/path " + english_text(40)
        self.assert_block(self.stop_input(message))

    def test_code_and_url_mixed_message(self) -> None:
        message = (
            "修正しました。`"
            + english_text(60)
            + "` を参照してください https://example.com/"
            + english_text(60, separator="/")
            + "\n```\n"
            + english_text(200)
            + "\n```\n"
        )
        self.assert_no_output(self.stop_input(message))


class InputGuardTest(HookTestCase):
    """block しない入力 (いずれも無出力で exit 0)。"""

    @unittest.skipUnless(JQ_AVAILABLE, "hook の判定には jq が必要")
    def test_stop_hook_active_true_is_not_blocked(self) -> None:
        self.assert_no_output(self.stop_input(ENGLISH_MESSAGE, stop_hook_active=True))

    @unittest.skipUnless(JQ_AVAILABLE, "hook の判定には jq が必要")
    def test_stop_hook_active_false_is_judged(self) -> None:
        self.assert_block(self.stop_input(ENGLISH_MESSAGE, stop_hook_active=False))

    @unittest.skipUnless(JQ_AVAILABLE, "hook の判定には jq が必要")
    def test_stop_hook_active_missing_is_judged(self) -> None:
        self.assert_block(self.stop_input(ENGLISH_MESSAGE, stop_hook_active=MISSING))

    def test_last_assistant_message_missing(self) -> None:
        self.assert_no_output(self.stop_input(MISSING))

    def test_last_assistant_message_empty(self) -> None:
        self.assert_no_output(self.stop_input(""))

    def test_last_assistant_message_not_a_string(self) -> None:
        for value in (
            None,
            123,
            True,
            [ENGLISH_MESSAGE],
            {"text": ENGLISH_MESSAGE},
        ):
            with self.subTest(value=value):
                self.assert_no_output(self.stop_input(value))

    def test_broken_input_json(self) -> None:
        broken = json.dumps(self.stop_input(ENGLISH_MESSAGE))[:-1]
        for raw in (broken, "{", "not json", ""):
            with self.subTest(raw=raw[:40]):
                self.assert_no_output(raw)

    def test_input_json_that_is_not_an_object(self) -> None:
        for raw in ("[]", "null", '"' + ENGLISH_MESSAGE + '"', "42"):
            with self.subTest(raw=raw[:40]):
                self.assert_no_output(raw)

    def test_jq_missing_is_fail_open(self) -> None:
        bin_dir = Path(self._tmp.name) / "bin-without-jq"
        bin_dir.mkdir()
        for tool in PATH_TOOLS_WITHOUT_JQ:
            found = shutil.which(tool)
            if found is not None:
                (bin_dir / tool).symlink_to(found)
        path = str(bin_dir)
        self.assertIsNone(shutil.which("jq", path=path))
        self.assert_no_output(self.stop_input(ENGLISH_MESSAGE), path=path)


@unittest.skipUnless(JQ_AVAILABLE, "hook の判定には jq が必要")
class LanguageValueTest(HookTestCase):
    """language の値による目標言語の判定 (user settings に書いて確認する)。"""

    def test_japanese_notations_enable_judgement(self) -> None:
        for value in (
            "日本語",
            "ja",
            "JA",
            "ja-JP",
            "ja-jp",
            "JA-JP",
            "Japanese",
            "JAPANESE",
            "japanese",
        ):
            with self.subTest(language=value):
                self.write_settings("user", {"language": value})
                self.assert_block(self.stop_input(ENGLISH_MESSAGE))

    def test_non_japanese_values_disable_judgement(self) -> None:
        for value in (
            "English",
            "english",
            "en",
            "en-US",
            "ja_JP",
            "jap",
            "japanese-ish",
            "",
            123,
            ["ja"],
        ):
            with self.subTest(language=value):
                self.write_settings("user", {"language": value})
                self.assert_no_output(self.stop_input(ENGLISH_MESSAGE))

    def test_no_settings_file_disables_judgement(self) -> None:
        self.remove_settings("user")
        self.assert_no_output(self.stop_input(ENGLISH_MESSAGE))

    def test_settings_without_language_disables_judgement(self) -> None:
        self.write_settings("user", {"model": "opus"})
        self.assert_no_output(self.stop_input(ENGLISH_MESSAGE))

    def test_language_null_is_treated_as_unset(self) -> None:
        self.write_settings("user", {"language": None})
        self.assert_no_output(self.stop_input(ENGLISH_MESSAGE))


@unittest.skipUnless(JQ_AVAILABLE, "hook の判定には jq が必要")
class SettingsLookupTest(HookTestCase):
    """local → project → user の順に language を探す。"""

    def test_local_wins_over_project_and_user(self) -> None:
        self.write_settings("local", {"language": "English"})
        self.write_settings("project", {"language": "日本語"})
        self.write_settings("user", {"language": "日本語"})
        self.assert_no_output(self.stop_input(ENGLISH_MESSAGE))

    def test_local_japanese_wins_over_project_english(self) -> None:
        self.write_settings("local", {"language": "ja"})
        self.write_settings("project", {"language": "English"})
        self.write_settings("user", {"language": "English"})
        self.assert_block(self.stop_input(ENGLISH_MESSAGE))

    def test_project_wins_over_user(self) -> None:
        self.write_settings("project", {"language": "English"})
        self.write_settings("user", {"language": "日本語"})
        self.assert_no_output(self.stop_input(ENGLISH_MESSAGE))

    def test_project_japanese_wins_over_user_english(self) -> None:
        self.write_settings("project", {"language": "Japanese"})
        self.write_settings("user", {"language": "English"})
        self.assert_block(self.stop_input(ENGLISH_MESSAGE))

    def test_user_is_used_when_project_files_are_absent(self) -> None:
        self.write_settings("user", {"language": "日本語"})
        self.assert_block(self.stop_input(ENGLISH_MESSAGE))

    def test_file_without_language_is_skipped(self) -> None:
        self.write_settings("local", {"permissions": {"allow": []}})
        self.write_settings("project", {"language": "English"})
        self.write_settings("user", {"language": "日本語"})
        self.assert_no_output(self.stop_input(ENGLISH_MESSAGE))

    def test_file_without_language_is_skipped_until_japanese(self) -> None:
        self.write_settings("local", {"permissions": {"allow": []}})
        self.write_settings("project", {"model": "opus"})
        self.write_settings("user", {"language": "日本語"})
        self.assert_block(self.stop_input(ENGLISH_MESSAGE))

    def test_unparsable_local_is_skipped(self) -> None:
        self.write_settings("local", '{"language": "English",')
        self.write_settings("project", {"language": "日本語"})
        self.write_settings("user", {"language": "English"})
        self.assert_block(self.stop_input(ENGLISH_MESSAGE))

    def test_unparsable_local_is_skipped_until_project_english(self) -> None:
        self.write_settings("local", "not json")
        self.write_settings("project", {"language": "English"})
        self.write_settings("user", {"language": "日本語"})
        self.assert_no_output(self.stop_input(ENGLISH_MESSAGE))

    def test_unparsable_local_and_project_fall_back_to_user(self) -> None:
        self.write_settings("local", "{")
        self.write_settings("project", "")
        self.write_settings("user", {"language": "日本語"})
        self.assert_block(self.stop_input(ENGLISH_MESSAGE))

    def test_non_object_json_file_is_skipped(self) -> None:
        self.write_settings("local", '["language", "English"]')
        self.write_settings("project", '"English"')
        self.write_settings("user", {"language": "日本語"})
        self.assert_block(self.stop_input(ENGLISH_MESSAGE))

    def test_unparsable_user_with_no_other_files_disables_judgement(self) -> None:
        self.write_settings("user", '{"language": "日本語"')
        self.assert_no_output(self.stop_input(ENGLISH_MESSAGE))

    def test_project_settings_are_read_from_input_cwd(self) -> None:
        # hook プロセスの作業ディレクトリではなく、入力 JSON の cwd を基準にする
        other = Path(self._tmp.name) / "other-project"
        (other / ".claude").mkdir(parents=True)
        (other / ".claude" / "settings.json").write_text(
            json.dumps({"language": "English"}), encoding="utf-8"
        )
        self.write_settings("project", {"language": "日本語"})
        self.write_settings("user", {"language": "日本語"})
        self.assert_no_output(self.stop_input(ENGLISH_MESSAGE, cwd=str(other)))

    def test_missing_cwd_uses_user_settings_only(self) -> None:
        self.write_settings("project", {"language": "English"})
        self.write_settings("user", {"language": "日本語"})
        self.assert_block(self.stop_input(ENGLISH_MESSAGE, cwd=MISSING))


@unittest.skipUnless(JQ_AVAILABLE, "hook の判定には jq が必要")
class BlockOutputTest(HookTestCase):
    """block 時の出力形式と reason の内容。"""

    def test_block_output_has_decision_and_reason_only(self) -> None:
        output = self.assert_block(self.stop_input(ENGLISH_MESSAGE))
        self.assertEqual(output["decision"], "block")

    def test_reason_states_the_rewrite_instruction(self) -> None:
        reason = self.assert_block(self.stop_input(ENGLISH_MESSAGE))["reason"]
        for phrase in REASON_REQUIRED_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, reason)

    def test_reason_is_written_in_japanese(self) -> None:
        reason = self.assert_block(self.stop_input(ENGLISH_MESSAGE))["reason"]
        japanese = count_japanese(reason)
        letters = count_ascii_letters(reason)
        self.assertGreaterEqual(japanese * 20, japanese + letters, reason)

    def test_stdout_is_a_single_json_document(self) -> None:
        returncode, stdout = self.run_hook(self.stop_input(ENGLISH_MESSAGE))
        self.assertEqual(returncode, 0)
        self.assertNotEqual(stdout.strip(), "", "block を期待したが無出力だった")
        decoder = json.JSONDecoder()
        _, end = decoder.raw_decode(stdout.strip())
        self.assertEqual(end, len(stdout.strip()), stdout)


class PluginWiringTest(unittest.TestCase):
    """hooks.json が Stop に script を exec form で 1 つだけ登録している。"""

    def test_stop_event_registers_the_script(self) -> None:
        manifest = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["hooks"]), {"Stop"})
        groups = manifest["hooks"]["Stop"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(
            groups[0]["hooks"],
            [
                {
                    "type": "command",
                    "command": "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/"
                    "enforce-japanese-response.sh",
                    "args": [],
                }
            ],
        )

    def test_script_is_executable(self) -> None:
        self.assertTrue(HOOK.is_file())
        self.assertTrue(os.access(HOOK, os.X_OK))


if __name__ == "__main__":
    unittest.main()
