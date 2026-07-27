r"""cmd-parser.sh の double quote 内 escaped backslash パース bug に関する契約テスト
(issue #354)。

spec-first 2 段階の Phase A: このファイルは修正前の現行実装に対して書かれた
実行可能仕様であり、後続 Phase B (cmd-parser.sh 本体の修正) が満たすべき契約を
固定する。

## バグの内容

`plugins/pre-push-review/hooks/scripts/lib/cmd-parser.sh` の `split_command` /
`tokenize_segment` は、 double quote 内で `\` の次の 1 文字が escape 対象
(`$` / `` ` `` / `"` / `\`) かどうかを case 文で判定する:

    case "$nc" in
      '$'|'`'|'"'|'\\')
        segment+="$c$nc"; i=$((i+2)); continue ;;
    esac

`$nc` は `${cmd:$((i+1)):1}` で取得される 1 文字の変数だが、 `'\\'` は single
quote 内の 2 文字 literal (backslash 2 個) であり、 1 文字の `$nc` には決して
一致しない (case pattern の arity 不一致)。 同型の bug は `split_command` の
quote 外分岐 (単独の `'\\')` アーム) にも存在する。 影響箇所は 3 箇所:

  - `split_command` の in_dquote 分岐 (line 199 付近)
  - `split_command` の quote 外分岐 (line 216 付近)
  - `tokenize_segment` の in_dquote 分岐 (line 377 付近)

この不一致により、 double quote 内の `\\` (エスケープされた backslash) を
入力に含めると、 2 個目の `\` が後続の閉じ quote `"` を escape として誤消費し
quote 状態が漏れる (fail-open: 本来分割されるべき `;` 等の separator が
top-level と認識されず 1 segment に merge される)。 一方 quote 外の `\;` は
escape 消費が不発のまま separator 扱いされ、 本来 1 segment であるべきものが
2 segment に over-split される。 いずれも push 検出を外す bypass 経路になり得る
(block-pre-push.sh の end-to-end 回帰は本ファイル末尾のクラスで検証する)。

Phase B ではこれら 3 箇所の case pattern を 1 文字の `'\'` へ修正する想定。 本
テストは修正前は 1・2・3・5 が fail し、 4・5 の対照実験が pass することを確認済み。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CMD_PARSER_LIB = (
    ROOT
    / "plugins"
    / "pre-push-review"
    / "hooks"
    / "scripts"
    / "lib"
    / "cmd-parser.sh"
)
HOOK = (
    ROOT
    / "plugins"
    / "pre-push-review"
    / "hooks"
    / "scripts"
    / "block-pre-push.sh"
)


def _split_segments(lines: list[str]) -> tuple[list[str], list[str]]:
    """split_command の出力行から segment 本体と separator 種別を分離する。"""
    segments: list[str] = []
    seps: list[str] = []
    for line in lines:
        if line.startswith("SEP:"):
            seps.append(line[len("SEP:") :])
        else:
            segments.append(line)
    return segments, seps


class CmdParserHelperMixin:
    """split_command / tokenize_segment を subprocess 経由で直接呼び出す helper。

    入力文字列は Python の argv (位置引数) としてそのまま bash プロセスへ渡し、
    シェルの再クオートを経由させない (test_statusline_display_width.py の
    run_function と同じ方式)。 これにより backslash の個数がテストコードから
    bash 関数の引数まで変化せずに届く。
    """

    def split_command_lines(self, command: str) -> list[str]:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; split_command "$2"',
                "cmd-parser-test",
                str(CMD_PARSER_LIB),
                command,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))  # type: ignore[attr-defined]
        return result.stdout.decode("utf-8").splitlines()

    def tokenize_segment_tokens(self, segment: str) -> list[str]:
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; tokenize_segment "$2" _toks; '
                'printf "%s\\n" "${_toks[@]}"',
                "cmd-parser-test",
                str(CMD_PARSER_LIB),
                segment,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))  # type: ignore[attr-defined]
        return result.stdout.decode("utf-8").splitlines()


@unittest.skipUnless(shutil.which("bash"), "cmd-parser tests require bash")
class SplitCommandDoubleQuoteEscapedBackslashTest(
    CmdParserHelperMixin, unittest.TestCase
):
    r"""split_command の in_dquote 分岐 (cmd-parser.sh:199 付近) の bug。

    double quote 内の `\\` (2 文字 literal backslash) を入力に含めると、 2 個目の
    `\` が閉じ quote `"` を escape として誤消費し quote 状態が漏れる。 結果として
    後続の `;` が top-level separator と認識されず、 本来 2 segment になるべき
    ものが 1 segment に merge される。 修正前は本クラスのテストが fail する。
    """

    # "a\\" は 2 文字の literal backslash (parser は値の collapse をしない設計)。
    COMMAND = r'echo "a\\" ; echo SECOND'

    def test_separator_after_escaped_backslash_produces_two_segments(self) -> None:
        # 単一メソッドに 2 つの検証を続けて書く: 1 個目 (SEP:; の有無 / segment
        # 数) は quote 状態漏れの bug がある限り必ず fail し、2 個目 (backslash
        # 保持) は 1 個目が pass した後にのみ評価される。 2 個目の検証だけを
        # 独立メソッドに分けると、 バグにより 1 segment に merge された場合でも
        # その 1 segment がたまたま 2 個の backslash を含んだままになり
        # (parser は値を collapse しないため) 単体では pass してしまい、
        # 「item 1 は修正前 fail」という契約が method 単位で曖昧になる。
        lines = self.split_command_lines(self.COMMAND)
        segments, _seps = _split_segments(lines)

        self.assertIn(
            "SEP:;",
            lines,
            "in_dquote の quote 状態漏れにより ';' が top-level separator と"
            f" 認識されず 1 segment に merge された: {lines!r}",
        )
        self.assertEqual(
            len(segments),
            2,
            f"2 segment に分割されるべきだが {len(segments)} 個だった: {segments!r}",
        )
        self.assertEqual(
            segments[0].count("\\"),
            2,
            "parser は値の collapse をしない設計のため、入力にあった 2 個の"
            f" backslash がそのまま保持されるべき: {segments[0]!r}",
        )


@unittest.skipUnless(shutil.which("bash"), "cmd-parser tests require bash")
class SplitCommandQuoteOuterEscapedSeparatorTest(
    CmdParserHelperMixin, unittest.TestCase
):
    r"""split_command の quote 外分岐 (cmd-parser.sh:216 付近) の bug。

    同じ case pattern arity 不一致により、 quote 外の `\;` (escape された ';')
    の escape 消費が不発になる。 bash の実挙動では `\;` は escape されて 1 個の
    引数の一部になる (separator として機能しない) が、 修正前の実装はこれを
    top-level separator として誤って分割してしまう (over-split)。
    """

    # `\;` は 1 個の literal backslash + ';' (bash 実挙動では escape され引数になる)。
    COMMAND = r"echo a\; echo SECOND"

    def test_escaped_separator_does_not_split_the_command(self) -> None:
        lines = self.split_command_lines(self.COMMAND)
        segments, _seps = _split_segments(lines)

        self.assertNotIn(
            "SEP:;",
            lines,
            "quote 外の `\\;` の escape 消費が不発のため ';' が top-level"
            f" separator として誤って分割された: {lines!r}",
        )
        self.assertEqual(
            len(segments),
            1,
            f"1 segment のままであるべきだが {len(segments)} 個だった: {segments!r}",
        )


@unittest.skipUnless(shutil.which("bash"), "cmd-parser tests require bash")
class TokenizeSegmentDoubleQuoteEscapedBackslashTest(
    CmdParserHelperMixin, unittest.TestCase
):
    """tokenize_segment の in_dquote 分岐 (cmd-parser.sh:377 付近) の bug。

    split_command の line 199 と同型の case pattern arity 不一致を持つ。
    `-c key="x\\"` のような形で、 続く token (`push` 等) が quote 状態の漏れに
    より直前の quoted 値へ併呑され、 独立した token として得られなくなる。
    """

    SEGMENT = r'git -c core.foo="x\\" push origin master'

    def test_push_is_extracted_as_an_independent_token(self) -> None:
        tokens = self.tokenize_segment_tokens(self.SEGMENT)
        self.assertIn(
            "push",
            tokens,
            "in_dquote の quote 状態漏れにより push が独立 token にならず、"
            f" 直前の quoted 値へ併呑された: {tokens!r}",
        )

    def test_token_count_and_values_match_expected_split(self) -> None:
        tokens = self.tokenize_segment_tokens(self.SEGMENT)
        self.assertEqual(
            tokens,
            ["git", "-c", r'core.foo="x\\"', "push", "origin", "master"],
            f"tokenize 結果が期待する 6 token と一致しない: {tokens!r}",
        )


@unittest.skipUnless(shutil.which("bash"), "cmd-parser tests require bash")
class CmdParserRegressionSafetyTest(CmdParserHelperMixin, unittest.TestCase):
    """正常系の保全: バグ修正の前後どちらでも pass するべき契約。"""

    def test_plain_double_quote_splits_into_two_segments(self) -> None:
        lines = self.split_command_lines('echo "a" ; echo SECOND')
        segments, _seps = _split_segments(lines)

        self.assertIn("SEP:;", lines, lines)
        self.assertEqual(len(segments), 2, segments)

    def test_dollar_escape_target_inside_dquote_still_splits(self) -> None:
        # `$` は case pattern の 4 種の escape 対象 (`'$'|'`'|'"'|'\\'`) のうち
        # 1 文字の literal であり、 1 文字変数 `$nc` との arity 不一致の影響を
        # 受けない (現行実装でも正しく escape 消費される)。
        lines = self.split_command_lines(r'echo "a\$b" ; echo SECOND')
        segments, _seps = _split_segments(lines)

        self.assertIn("SEP:;", lines, lines)
        self.assertEqual(len(segments), 2, segments)

    def test_non_escape_target_backslash_inside_dquote_is_kept_literal(self) -> None:
        # `\-` は escape 対象 4 文字 (`$` / `` ` `` / `"` / `\`) のいずれでもない
        # ため、 現行実装でも修正後実装でも case にマッチせず backslash は
        # literal のまま segment に残る。
        lines = self.split_command_lines(r'echo "a\-b"')
        segments, _seps = _split_segments(lines)

        self.assertEqual(len(segments), 1, segments)
        self.assertIn("a\\-b", segments[0], segments[0])

    def test_simple_command_tokenizes_into_four_tokens(self) -> None:
        tokens = self.tokenize_segment_tokens("git push origin master")
        self.assertEqual(tokens, ["git", "push", "origin", "master"], tokens)


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("git") and shutil.which("jq"),
    "hook integration requires bash, git, and jq",
)
class BlockPrePushDquoteEscapeBypassTest(unittest.TestCase):
    """block-pre-push.sh の end-to-end bypass 回帰 (issue #354)。

    split_command の in_dquote bug により、 `;` の前に escaped backslash 2 個を
    含む double-quoted 文字列を混ぜると quote 状態が漏れ、 後続の `git push` が
    同一 segment に merge されて push 検出が外れ allow (空 stdout) されてしまう
    (fail-open な security gate bypass)。 修正後は正しく 2 segment に分割され、
    レビュー markers が未設定のため deny になるべき。
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary = Path(self.temporary.name)
        self.origin = temporary / "origin.git"
        self.repo = temporary / "main"
        self._git(temporary, "init", "--bare", str(self.origin))
        self._git(temporary, "init", str(self.repo))
        self._git(self.repo, "config", "user.name", "Marketplace Test")
        self._git(self.repo, "config", "user.email", "marketplace@example.invalid")
        (self.repo / "example.txt").write_text("base\n", encoding="utf-8")
        self._git(self.repo, "add", "example.txt")
        self._git(self.repo, "commit", "-m", "base")
        self._git(self.repo, "branch", "-M", "master")
        self._git(self.repo, "remote", "add", "origin", str(self.origin))
        self._git(self.repo, "push", "-u", "origin", "master")
        self._git(self.repo, "remote", "set-head", "origin", "master")
        self._git(self.repo, "switch", "-c", "feature")
        (self.repo / "example.txt").write_text("feature change\n", encoding="utf-8")
        self._git(self.repo, "add", "example.txt")
        self._git(self.repo, "commit", "-m", "feature change")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _run_hook(self, command: str) -> subprocess.CompletedProcess[bytes]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        return subprocess.run(
            ["bash", str(HOOK)],
            cwd=self.repo,
            input=json.dumps(payload).encode("utf-8"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_push_after_escaped_backslash_is_still_denied(self) -> None:
        # "a\\" は 2 文字の literal backslash。 修正前は quote 状態が漏れて
        # 後続の `git push origin feature` が同一 segment に merge され、
        # push 検出が外れて allow (空 stdout) になってしまう。
        command = r'echo "a\\" ; git push origin feature'
        result = self._run_hook(command)

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertNotEqual(
            result.stdout,
            b"",
            "push 検出が外れて allow (空 stdout) になった: in_dquote bug により"
            " push gate が bypass されている",
        )
        response = json.loads(result.stdout)
        output = response["hookSpecificOutput"]
        self.assertEqual(
            output["permissionDecision"],
            "deny",
            f"push は review markers 未設定のため deny されるべき: {output!r}",
        )

    def test_control_without_backslash_is_denied_on_current_implementation(
        self,
    ) -> None:
        # 対照実験 (テスト自体の妥当性確認): escaped backslash が無ければ現行実装
        # でも push 検出は正しく機能し deny される。 上のテストとの差分要因が
        # escaped backslash の有無であることをここで確認する。
        command = 'echo "a" ; git push origin feature'
        result = self._run_hook(command)

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertNotEqual(result.stdout, b"", "対照コマンドは deny されるはず")
        response = json.loads(result.stdout)
        output = response["hookSpecificOutput"]
        self.assertEqual(
            output["permissionDecision"],
            "deny",
            f"対照コマンドは deny されるべき: {output!r}",
        )


if __name__ == "__main__":
    unittest.main()
