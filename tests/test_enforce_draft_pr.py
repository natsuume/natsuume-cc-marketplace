"""enforce-draft-pr hook (plugins/enforce-draft-pr) の受入テスト。

spec-first Phase A: これから修正する issue #144 / #143 の新仕様を「契約」として固定する
テストである。フックの実装 (hook 本体) はまだ新仕様に対応していないため、下記グループ H /
L / P の一部は現行実装では **fail するのが正しい (red)**。fail するテストの期待値は
新仕様が正であり、現行挙動に合わせて書き換えない。

## 対象 hook のプロトコル

- `bash <script>` に stdin で JSON `{"tool_input": {"command": "<bash コマンド文字列>"}}`
  を渡して起動する。
- stdout の契約:
  - 不介入: 空出力、exit 0
  - 書き換え: JSON。`.hookSpecificOutput.permissionDecision == "allow"` かつ
    `.hookSpecificOutput.updatedInput.command` に書き換え後コマンド
  - deny: JSON。`.hookSpecificOutput.permissionDecision == "deny"`、`updatedInput` なし
- 挿入規則 (新仕様でも不変): 対象 `gh ... pr create` invocation の `create` トークン末尾
  直後に ` --draft` (半角スペース + `--draft`) を挿入する。末尾 LF を含めて挿入以外は
  1 バイトも変更しない。

## 新仕様の契約 (heredoc / 行継続 / 性能)

1. **heredoc 認識**: quote 外で `<<` を検出したら delimiter を読み取り pending キューに
   積む。次の生改行から heredoc 本文モードに入り、delimiter 単独行 (`<<-` は行頭タブ除去後
   に完全一致) が来るまで本文全体を不透明データとして扱う (トークン化・command-start 設定・
   引数スキャンの対象にしない)。同一行に複数 heredoc があれば出現順に本文を消費する。fd
   番号付き `3<<EOF` も heredoc。`<<<` (herestring) は heredoc ではない。unquoted
   delimiter の本文では、行末の連続 backslash が奇数個の行の直後行は terminator と
   判定しない (bash の行継続結合が terminator 判定に先行する)。偶数個なら terminator
   は有効。quote 外で語頭 (command-start または空白直後) の `#` から行末まではコメント
   であり、コメント内の `<<` は heredoc として扱わない。heredoc 本文モードの開始は
   quote 外の生改行に限る。quote 内の改行はデータであり本文モードを開始しない。
   pending 登録後も quote 外の生改行までは宣言行の引数走査を継続する。
2. **対応 delimiter 形式**: `<<WORD` / `<< WORD` / `<<-WORD` / `<<'WORD'` / `<<"WORD"` /
   `<<\\WORD` (WORD は `[A-Za-z0-9_]+`)。該当しない構文を検出したら opaque fallback: それ
   以降のコマンド文字列を一切解析しない。fallback 発動後は、発動位置 (対象 invocation
   (`gh pr create`) の引数領域内か、別コマンドの領域か) に依らず **常に deny** する。
   fallback 以降は解析不能 (opaque) であり、残余文字列への部分文字列判定による救済は、
   行継続 (`cre\\` + 改行 + `ate`) や quote 分断 (`cre""ate`) といった bash の語結合を
   使う難読化で bypass できるため、解析できない領域が生じた時点で fail-closed に倒す。
   冒頭の粗フィルタ (`*gh*pr*create*`) を通過した入力のみがスキャナに到達するため、
   `gh pr create` 風文字列を全く含まない無関係コマンドがこの deny に到達することはない。
   fallback より前に separator で完結した invocation への ` --draft` 挿入も行わない
   (deny が全体に優先する)。
   `<<'WORD'` / `<<"WORD"` / `<<\\WORD` はいずれも quoted delimiter であり、本文で
   行継続処理を行わない。
3. **算術式スキップ**: `$((...))` / コマンド位置の `((...))` は対応する `))` まで (内部括弧
   の深度追跡込み) を不透明に取り込み、内部の `<<` (ビットシフト) を heredoc と誤認しない。
4. **行継続のスキャナ内処理**: 解析前の一括削除 (normalize) を廃止し、スキャナが文脈依存で
   処理する。quote 外・double quote 内の `\\<LF>` は行継続 (トークンを連結し、行境界にしな
   い)。single quote 内・quoted heredoc 本文内の `\\<LF>` は literal データ。出力コマンド
   文字列はどのケースでも原文保持 (挿入のみ)。double quote 内の行継続はトークン値の解釈にも
   適用され、`--draft=` 値の falsy 判定は行継続除去後の値で行う。
5. **性能**: 数十 KB の body を持つコマンドは、入力パターン (通常 quoted body / escape
   密集) に依らず 5 秒以内に完了する。

## テストグループ

- グループ R: 既存挙動 regression (現行実装で pass するはず)
- グループ H: heredoc 新仕様 (現行実装で fail するはず。一部は現行でも pass しうる)
- グループ A: 算術式 (A01〜A03 は現行実装で pass するはず。A04 は heredoc との複合
  パターンのため現行実装では fail (red) するはず)
- グループ L: 行継続の原文保持 (L01〜L08 は現行実装で fail するはず。L09 は現行でも
  pass する退行防止ケース)
- グループ B: 末尾 LF を含むバイト保持契約 (現行実装で fail するはず)
- グループ P: 性能 (現行実装で fail するはず)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = (
    ROOT
    / "plugins"
    / "enforce-draft-pr"
    / "hooks"
    / "scripts"
    / "enforce-draft-pr.sh"
)

NL = "\n"
TAB = "\t"
BS = "\\"


def run_hook(command: str, timeout: float = 30.0) -> tuple[int, str]:
    """enforce-draft-pr.sh を起動し (returncode, stdout) を返す。"""
    payload = json.dumps({"tool_input": {"command": command}})
    result = subprocess.run(
        ["bash", str(HOOK)],
        input=payload.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    return result.returncode, result.stdout.decode("utf-8")


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class EnforceDraftPrTest(unittest.TestCase):
    def assert_rewrite(self, command: str, expected_command: str) -> None:
        """allow で書き換えられ、updatedInput.command が期待値と完全一致することを確認する。"""
        returncode, stdout = run_hook(command)
        self.assertEqual(returncode, 0, stdout)
        self.assertNotEqual(stdout, "", "書き換え (allow) を期待したが hook は不介入 (空出力) だった")
        payload = json.loads(stdout)
        hook_output = payload["hookSpecificOutput"]
        self.assertEqual(hook_output["permissionDecision"], "allow", stdout)
        self.assertEqual(
            hook_output["updatedInput"]["command"], expected_command, stdout
        )

    def assert_no_intervention(self, command: str) -> None:
        """不介入 (空出力、exit 0) であることを確認する。"""
        returncode, stdout = run_hook(command)
        self.assertEqual(returncode, 0, stdout)
        self.assertEqual(stdout, "")

    def assert_denied(self, command: str) -> None:
        """deny (permissionDecision == "deny"、updatedInput なし) であることを確認する。"""
        returncode, stdout = run_hook(command)
        self.assertEqual(returncode, 0, stdout)
        self.assertNotEqual(stdout, "", "deny を期待したが hook は不介入 (空出力) だった")
        payload = json.loads(stdout)
        hook_output = payload["hookSpecificOutput"]
        self.assertEqual(hook_output["permissionDecision"], "deny", stdout)
        self.assertNotIn("updatedInput", hook_output)

    # ------------------------------------------------------------------
    # グループ R: 既存挙動 regression (現行実装で pass するはず)
    # ------------------------------------------------------------------

    def test_r01_normal_insertion(self) -> None:
        command = 'gh pr create --title "t" --body "b"'
        expected = 'gh pr create --draft --title "t" --body "b"'
        self.assert_rewrite(command, expected)

    def test_r02_existing_draft_passthrough(self) -> None:
        command = 'gh pr create --draft --title "t" --body "b"'
        self.assert_no_intervention(command)

    def test_r03_short_flag_passthrough(self) -> None:
        command = 'gh pr create -d --title "t" --body "b"'
        self.assert_no_intervention(command)

    def test_r04_explicit_non_draft_denied(self) -> None:
        command = 'gh pr create --draft=false --title "t" --body "b"'
        self.assert_denied(command)

    def test_r05_quoted_falsy_value_denied(self) -> None:
        command = 'gh pr create --draft="false" --title "t" --body "b"'
        self.assert_denied(command)

    def test_r06_multiple_invocations_each_inserted(self) -> None:
        command = (
            'gh pr create --title "a" --body "b"; '
            'gh pr create --title "c" --body "d"'
        )
        expected = (
            'gh pr create --draft --title "a" --body "b"; '
            'gh pr create --draft --title "c" --body "d"'
        )
        self.assert_rewrite(command, expected)

    def test_r07_quoted_body_contains_gh_pr_create_not_intervened(self) -> None:
        command = (
            'gh pr create --title "t" '
            '--body "see: gh pr create --draft=false example"'
        )
        expected = (
            'gh pr create --draft --title "t" '
            '--body "see: gh pr create --draft=false example"'
        )
        self.assert_rewrite(command, expected)

    def test_r08_env_prefix(self) -> None:
        command = 'GH_TOKEN=x gh pr create --title "t" --body "b"'
        expected = 'GH_TOKEN=x gh pr create --draft --title "t" --body "b"'
        self.assert_rewrite(command, expected)

    def test_r09_global_repo_option(self) -> None:
        command = 'gh -R owner/repo pr create --title "t" --body "b"'
        expected = 'gh -R owner/repo pr create --draft --title "t" --body "b"'
        self.assert_rewrite(command, expected)

    def test_r10_cd_chain(self) -> None:
        command = 'cd repo && gh pr create --title "t" --body "b"'
        expected = 'cd repo && gh pr create --draft --title "t" --body "b"'
        self.assert_rewrite(command, expected)

    def test_r11_non_target_command(self) -> None:
        command = "echo hello"
        self.assert_no_intervention(command)

    def test_r12_pipe_right_hand_side(self) -> None:
        command = 'cat notes.md | gh pr create --title "t" --body-file -'
        expected = 'cat notes.md | gh pr create --draft --title "t" --body-file -'
        self.assert_rewrite(command, expected)

    def test_r13_leading_comment_line(self) -> None:
        command = "# prepare" + NL + 'gh pr create --title "t" --body "b"'
        expected = (
            "# prepare" + NL + 'gh pr create --draft --title "t" --body "b"'
        )
        self.assert_rewrite(command, expected)

    def test_r14_draft_state_reset_between_invocations(self) -> None:
        # truthy 状態を invocation 間でリセットしない誤実装は 2 個目への挿入
        # を欠落させ、非 draft PR が作られるため fail する。
        command = (
            'gh pr create --draft --title "a" --body "b"; '
            'gh pr create --title "c" --body "d"'
        )
        expected = (
            'gh pr create --draft --title "a" --body "b"; '
            'gh pr create --draft --title "c" --body "d"'
        )
        self.assert_rewrite(command, expected)

    def test_r15_flag_shaped_value_not_treated_as_flag(self) -> None:
        # 値取りオプションの値は flag 判定から除外される。値を flag と誤認する
        # 実装は「draft 指定あり」として不介入になり、非 draft PR が作られる
        # ため fail する。
        command = 'gh pr create --title --draft --body "b"'
        expected = 'gh pr create --draft --title --draft --body "b"'
        self.assert_rewrite(command, expected)

    def test_r16_falsy_after_truthy_denied(self) -> None:
        # falsy は truthy より優先して deny する (出現順に依らない)。truthy を
        # 見た時点で flag 追跡を打ち切る実装は後着の --draft=false を見逃して
        # 素通しし、実行時に gh が後着 falsy を優先して非 draft PR が作られる
        # ため fail する。
        command = 'gh pr create --draft --draft=false --title "t" --body "b"'
        self.assert_denied(command)

    # ------------------------------------------------------------------
    # グループ H: heredoc 新仕様
    # (現行実装で fail するはず — H5/H6/H9 など一部は現行でも pass しうる)
    # ------------------------------------------------------------------

    def test_h01_heredoc_body_separators_not_intervened(self) -> None:
        # 対応範囲は scanner が command-start に影響させる現行 5 種
        # (`;` `&&` `||` `|` `&`)。bash 全 control operator の網羅ではない。
        separators = (";", " &&", " ||", " |", " &")
        for separator in separators:
            with self.subTest(separator=separator):
                body_line = (
                    "Run tests first"
                    + separator
                    + " gh pr create --title x --body y is the old way"
                )
                command = (
                    'gh pr create --title "t" --body-file - <<EOF'
                    + NL
                    + body_line
                    + NL
                    + "EOF"
                )
                expected = (
                    'gh pr create --draft --title "t" --body-file - <<EOF'
                    + NL
                    + body_line
                    + NL
                    + "EOF"
                )
                self.assert_rewrite(command, expected)

    def test_h01b_heredoc_body_parenthesis_not_intervened(self) -> None:
        command = (
            'gh pr create --title "t" --body-file - <<EOF'
            + NL
            + "see (gh pr create --title x --body y) usage"
            + NL
            + "EOF"
        )
        expected = (
            'gh pr create --draft --title "t" --body-file - <<EOF'
            + NL
            + "see (gh pr create --title x --body y) usage"
            + NL
            + "EOF"
        )
        self.assert_rewrite(command, expected)

    def test_h02_heredoc_body_draft_false_not_denied(self) -> None:
        command = (
            'gh pr create --title "t" --body-file - <<EOF'
            + NL
            + "note; gh pr create --draft=false was the old way"
            + NL
            + "EOF"
        )
        expected = (
            'gh pr create --draft --title "t" --body-file - <<EOF'
            + NL
            + "note; gh pr create --draft=false was the old way"
            + NL
            + "EOF"
        )
        self.assert_rewrite(command, expected)

    def test_h03a_single_quoted_delimiter(self) -> None:
        command = (
            "gh pr create --title \"t\" --body-file - <<'EOF'"
            + NL
            + "Run tests first; gh pr create --title x --body y is the old way"
            + NL
            + "EOF"
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        expected = (
            "gh pr create --draft --title \"t\" --body-file - <<'EOF'"
            + NL
            + "Run tests first; gh pr create --title x --body y is the old way"
            + NL
            + "EOF"
            + NL
            + ':; gh pr create --draft --title "t2" --body "b2"'
        )
        self.assert_rewrite(command, expected)

    def test_h03b_double_quoted_delimiter(self) -> None:
        command = (
            'gh pr create --title "t" --body-file - <<"EOF"'
            + NL
            + "Run tests first; gh pr create --title x --body y is the old way"
            + NL
            + "EOF"
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        expected = (
            'gh pr create --draft --title "t" --body-file - <<"EOF"'
            + NL
            + "Run tests first; gh pr create --title x --body y is the old way"
            + NL
            + "EOF"
            + NL
            + ':; gh pr create --draft --title "t2" --body "b2"'
        )
        self.assert_rewrite(command, expected)

    def test_h03c_backslash_escaped_delimiter(self) -> None:
        command = (
            'gh pr create --title "t" --body-file - <<'
            + BS
            + "EOF"
            + NL
            + "Run tests first; gh pr create --title x --body y is the old way"
            + NL
            + "EOF"
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        expected = (
            'gh pr create --draft --title "t" --body-file - <<'
            + BS
            + "EOF"
            + NL
            + "Run tests first; gh pr create --title x --body y is the old way"
            + NL
            + "EOF"
            + NL
            + ':; gh pr create --draft --title "t2" --body "b2"'
        )
        self.assert_rewrite(command, expected)

    def test_h03d_space_separated_delimiter(self) -> None:
        command = (
            'gh pr create --title "t" --body-file - << EOF'
            + NL
            + "Run tests first; gh pr create --title x --body y is the old way"
            + NL
            + "EOF"
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        expected = (
            'gh pr create --draft --title "t" --body-file - << EOF'
            + NL
            + "Run tests first; gh pr create --title x --body y is the old way"
            + NL
            + "EOF"
            + NL
            + ':; gh pr create --draft --title "t2" --body "b2"'
        )
        self.assert_rewrite(command, expected)

    def test_h04_dash_heredoc_strips_leading_tabs(self) -> None:
        command = (
            'gh pr create --title "t" --body-file - <<-EOF'
            + NL
            + TAB
            + "body; gh pr create fake"
            + NL
            + TAB
            + "EOF"
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        expected = (
            'gh pr create --draft --title "t" --body-file - <<-EOF'
            + NL
            + TAB
            + "body; gh pr create fake"
            + NL
            + TAB
            + "EOF"
            + NL
            + ':; gh pr create --draft --title "t2" --body "b2"'
        )
        self.assert_rewrite(command, expected)

    def test_h05_multiple_heredocs_same_line(self) -> None:
        # bodyA の先頭に delimiter B と同じテキストの行を交差させる。B を先に
        # 探す (出現順を無視する) 誤実装はこの行で B 本文を終端したと誤認し、
        # 後続の `bodyA; gh pr create fakeA` 行がコマンド行化して fakeA への
        # 挿入で fail する。
        command = (
            'cat <<A <<B; gh pr create --title "t" --body "b"'
            + NL
            + "B"
            + NL
            + "bodyA; gh pr create fakeA"
            + NL
            + "A"
            + NL
            + "bodyB; gh pr create fakeB"
            + NL
            + "B"
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        expected = (
            'cat <<A <<B; gh pr create --draft --title "t" --body "b"'
            + NL
            + "B"
            + NL
            + "bodyA; gh pr create fakeA"
            + NL
            + "A"
            + NL
            + "bodyB; gh pr create fakeB"
            + NL
            + "B"
            + NL
            + ':; gh pr create --draft --title "t2" --body "b2"'
        )
        self.assert_rewrite(command, expected)

    def test_h06_fd_numbered_heredoc(self) -> None:
        command = (
            'exec 3<<EOF; gh pr create --title "t" --body "b"'
            + NL
            + "fd body; gh pr create fake"
            + NL
            + "EOF"
        )
        expected = (
            'exec 3<<EOF; gh pr create --draft --title "t" --body "b"'
            + NL
            + "fd body; gh pr create fake"
            + NL
            + "EOF"
        )
        self.assert_rewrite(command, expected)

    def test_h07_heredoc_followed_by_pipe_same_line(self) -> None:
        command = (
            'cat <<EOF | gh pr create --title "t" --body-file -'
            + NL
            + "body text; gh pr create fake"
            + NL
            + "EOF"
        )
        expected = (
            'cat <<EOF | gh pr create --draft --title "t" --body-file -'
            + NL
            + "body text; gh pr create fake"
            + NL
            + "EOF"
        )
        self.assert_rewrite(command, expected)

    def test_h08_unterminated_heredoc(self) -> None:
        command = (
            'gh pr create --title "t" --body-file - <<EOF'
            + NL
            + "unterminated body; gh pr create fake"
        )
        expected = (
            'gh pr create --draft --title "t" --body-file - <<EOF'
            + NL
            + "unterminated body; gh pr create fake"
        )
        self.assert_rewrite(command, expected)

    def test_h09_herestring_not_misdetected_as_heredoc(self) -> None:
        command = (
            'grep x <<<"data here"; gh pr create --title "t" --body "b"'
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        expected = (
            'grep x <<<"data here"; gh pr create --draft --title "t" --body "b"'
            + NL
            + ':; gh pr create --draft --title "t2" --body "b2"'
        )
        self.assert_rewrite(command, expected)

    def test_h10a_opaque_fallback_other_region_denied(self) -> None:
        # opaque fallback は宣言以降全域で解析を停止する。新契約では発動位置
        # (対象 invocation の引数領域内か、別コマンドの領域か) に依らず常に
        # deny する。この invocation は fallback を起こす `cat` と別 command
        # (`;` 区切り) であり、`cat <<E"OF"` より後ろの opaque 領域内にある
        # (= 旧契約なら不介入だった) が、opaque 領域内には検出不能な
        # invocation が隠れうるため fail-closed で deny する。旧契約はここが
        # 不介入となり、bash 実行時には opaque 領域内の後続コマンドがそのまま
        # 実行される bypass の穴だった (H28 参照)。宣言行のみ抑制する誤実装は
        # 本文の fake に、EOF 後に解析を再開する誤実装は後続行に挿入して
        # allow になり fail する。本文先頭に unsupported delimiter `E"OF"` の
        # prefix `E` に見える単独行を追加する: delimiter の prefix 一致で本文
        # モードを早期 resume する誤実装は以降の fake に挿入して allow になり
        # fail する。
        command = (
            'cat <<E"OF"; gh pr create --title "t" --body "b"'
            + NL
            + "E"
            + NL
            + "body; gh pr create fake"
            + NL
            + "EOF"
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        self.assert_denied(command)

    def test_h10b_opaque_fallback_confirmed_tokens_judged(self) -> None:
        # opaque fallback は `<<E"OF"` 以降を一切解析しない。この invocation
        # (宣言行の `gh pr create`) 自身の draft 状態は fallback 発動時点で
        # 未確定 (宣言行に `--draft` も `--draft=false` も無い) であり、新契約
        # (未確定 → deny) により deny する。fallback 前に確定した情報が無い
        # まま素通しで挿入する実装は、宣言行残余に隠れた `--draft=false` に
        # よる bypass (H20) を防げず fail する。
        command = (
            'gh pr create --title "t" --body-file - <<E"OF"'
            + NL
            + "body; gh pr create fake"
            + NL
            + "EOF"
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        self.assert_denied(command)

    def test_h11_near_miss_terminator_lines_do_not_end_heredoc_body(
        self,
    ) -> None:
        near_miss_lines = ("EOF ", " EOF", "EOFX")
        for near_miss in near_miss_lines:
            with self.subTest(near_miss=near_miss):
                command = (
                    'gh pr create --title "t" --body-file - <<EOF'
                    + NL
                    + near_miss
                    + NL
                    + "note; gh pr create fake"
                    + NL
                    + "EOF"
                )
                expected = (
                    'gh pr create --draft --title "t" --body-file - <<EOF'
                    + NL
                    + near_miss
                    + NL
                    + "note; gh pr create fake"
                    + NL
                    + "EOF"
                )
                self.assert_rewrite(command, expected)

    def test_h12_dash_heredoc_strips_only_tabs_not_spaces(self) -> None:
        command = (
            'gh pr create --title "t" --body-file - <<-EOF'
            + NL
            + " EOF"
            + NL
            + "note; gh pr create fake"
            + NL
            + TAB
            + "EOF"
        )
        expected = (
            'gh pr create --draft --title "t" --body-file - <<-EOF'
            + NL
            + " EOF"
            + NL
            + "note; gh pr create fake"
            + NL
            + TAB
            + "EOF"
        )
        self.assert_rewrite(command, expected)

    def test_h13_heredoc_body_line_starting_with_invocation(self) -> None:
        command = (
            'gh pr create --title "t" --body-file - <<EOF'
            + NL
            + "gh pr create --title x --body y"
            + NL
            + "EOF"
        )
        expected = (
            'gh pr create --draft --title "t" --body-file - <<EOF'
            + NL
            + "gh pr create --title x --body y"
            + NL
            + "EOF"
        )
        self.assert_rewrite(command, expected)

    def test_h14_quoted_literal_double_less_not_heredoc(self) -> None:
        cases = (
            (
                "echo 'a << b'; gh pr create --title \"t\" --body \"b\""
                + NL
                + ':; gh pr create --title "t2" --body "b2"',
                "echo 'a << b'; gh pr create --draft --title \"t\" --body \"b\""
                + NL
                + ':; gh pr create --draft --title "t2" --body "b2"',
            ),
            (
                'echo "shift << here"; gh pr create --title "t" --body "b"'
                + NL
                + ':; gh pr create --title "t2" --body "b2"',
                'echo "shift << here"; gh pr create --draft --title "t" --body "b"'
                + NL
                + ':; gh pr create --draft --title "t2" --body "b2"',
            ),
        )
        for command, expected in cases:
            with self.subTest(command=command):
                self.assert_rewrite(command, expected)

    def test_h15_opaque_fallback_with_prior_draft_false_denied(self) -> None:
        # opaque fallback でも fallback 前に確定したトークンの falsy 判定は
        # 生きる。fallback 時に確定済みトークンの判定を省く実装は非 draft PR
        # を素通しする (enforce 迂回)。H10b の deny 版 witness。
        command = (
            'gh pr create --draft=false --title "t" --body-file - <<E"OF"'
            + NL
            + "body"
            + NL
            + "EOF"
        )
        self.assert_denied(command)

    def test_h16_plain_heredoc_tab_indented_terminator_not_end(self) -> None:
        # H12 の対になる negative control。ダッシュ形式 (`<<-`) 専用のタブ
        # 除去を全形式に適用する誤実装は fake への挿入で fail する。
        command = (
            'gh pr create --title "t" --body-file - <<EOF'
            + NL
            + TAB
            + "EOF"
            + NL
            + "note; gh pr create fake"
            + NL
            + "EOF"
        )
        expected = (
            'gh pr create --draft --title "t" --body-file - <<EOF'
            + NL
            + TAB
            + "EOF"
            + NL
            + "note; gh pr create fake"
            + NL
            + "EOF"
        )
        self.assert_rewrite(command, expected)

    def test_h17_mixed_mode_heredocs_per_entry_state(self) -> None:
        # 全 entry に単一 mode を共有する誤実装 (例: 全部 quoted 扱い / 全部
        # タブ除去なし) では B の終端を誤り、後続行の挿入欠落または fake への
        # 挿入で fail する。さらに A 本文末尾に行末 backslash の行を追加する:
        # quoted delimiter A は行継続を処理しないため `linecont\` の直後の `A`
        # 行は terminator として有効 (期待値の終端位置は変わらない)。mode を
        # 単一変数で保持し後続 entry B (unquoted・tab-strip) が上書きする実装
        # では、A 本文の行継続が結合されて terminator `A` を見失い、以降の
        # 解釈がずれて後続行の挿入欠落で fail する。
        command = (
            "cat <<'A' <<-B; gh pr create --title \"t\" --body \"b\""
            + NL
            + "bodyA; gh pr create fakeA"
            + NL
            + "linecont"
            + BS
            + NL
            + "A"
            + NL
            + TAB
            + "bodyB; gh pr create fakeB"
            + NL
            + TAB
            + "B"
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        expected = (
            "cat <<'A' <<-B; gh pr create --draft --title \"t\" --body \"b\""
            + NL
            + "bodyA; gh pr create fakeA"
            + NL
            + "linecont"
            + BS
            + NL
            + "A"
            + NL
            + TAB
            + "bodyB; gh pr create fakeB"
            + NL
            + TAB
            + "B"
            + NL
            + ':; gh pr create --draft --title "t2" --body "b2"'
        )
        self.assert_rewrite(command, expected)

    def test_h18_delimiter_with_digit_and_underscore(self) -> None:
        # 契約の文字クラスは [A-Za-z0-9_]+ で先頭位置の制限は無い。先頭を英字
        # や識別子開始文字に限定する誤実装は fallback に落ちて後続の挿入欠落
        # で fail する。
        delimiters = ("EOF_1", "1EOF", "_EOF")
        for delimiter in delimiters:
            with self.subTest(delimiter=delimiter):
                command = (
                    'gh pr create --title "t" --body-file - <<'
                    + delimiter
                    + NL
                    + "body; gh pr create fake"
                    + NL
                    + delimiter
                    + NL
                    + ':; gh pr create --title "t2" --body "b2"'
                )
                expected = (
                    'gh pr create --draft --title "t" --body-file - <<'
                    + delimiter
                    + NL
                    + "body; gh pr create fake"
                    + NL
                    + delimiter
                    + NL
                    + ':; gh pr create --draft --title "t2" --body "b2"'
                )
                self.assert_rewrite(command, expected)

    def test_h19_opaque_fallback_suppresses_flag_scan(self) -> None:
        # 宣言行残余の `--draft` (truthy) は opaque 領域内で hook から不可視。
        # fallback 後も宣言行のスキャンを継続する誤実装は宣言行上の truthy
        # `--draft` を見て素通しするため fail する (正実装は不可視 → 未確定
        # → deny)。物理行境界を尊重しつつ宣言行だけスキャン継続する誤実装
        # との識別のため、truthy は次行ではなく宣言行上に置く。
        command = (
            'gh pr create --title "t" --body-file - <<E"OF" --draft'
            + NL
            + "body"
            + NL
            + "EOF"
        )
        self.assert_denied(command)

    def test_h20_declaration_tail_draft_false_after_fallback_denied(
        self,
    ) -> None:
        # bypass 経路の直接 witness: 宣言行残余の `--draft=false` は opaque
        # 領域内で hook から不可視だが gh の実引数としては有効なため、
        # fallback 前のトークンにのみ挿入すると後着の false が勝ち非 draft
        # PR が作られてしまう。未確定として deny することでこの bypass を
        # 塞ぐ。
        command = (
            'gh pr create --title "t" --body-file - <<E"OF" --draft=false'
            + NL
            + "body"
            + NL
            + "EOF"
        )
        self.assert_denied(command)

    def test_h21_confirmed_truthy_before_fallback_denied(self) -> None:
        # 第 8 巡での訂正: fallback 前に truthy (--draft) が確定していても、
        # 不可視の宣言行残余 (opaque 領域) に conflicting な後着 --draft=false
        # が隠れうるため、確認済み truthy も信頼できない。fallback が対象
        # invocation の引数領域内で発動した以上、draft 指定の確定状態に関係
        # なく常に deny する。
        command = (
            'gh pr create --draft --title "t" --body-file - <<E"OF"'
            + NL
            + "body"
            + NL
            + "EOF"
        )
        self.assert_denied(command)

    def test_h23_truthy_then_declaration_tail_falsy_denied(self) -> None:
        # 第 8 巡指摘の迂回経路 (truthy 素通し + 宣言行残余の後着 falsy) の
        # 直接 witness。truthy `--draft` 確認後に opaque 領域へ入り、宣言行
        # 残余に `--draft=false` が隠れている。素通しする実装は gh が後着
        # falsy を優先し非 draft PR を作ってしまう bypass を再現できず fail
        # する。
        command = (
            'gh pr create --draft --title "t" --body-file - <<E"OF" --draft=false'
            + NL
            + "body"
            + NL
            + "EOF"
        )
        self.assert_denied(command)

    def test_h22_completed_invocation_before_fallback_denied(
        self,
    ) -> None:
        # fallback より前に separator (`;`) で完結した invocation があっても、
        # 新契約では opaque fallback の deny が全体に優先し、完結済み
        # invocation への ` --draft` 挿入は行わない。fallback 発動位置に
        # 依らず常に deny する契約 (H10a と対) の直接 witness。
        command = (
            'gh pr create --title "t" --body "b"; cat <<E"OF"'
            + NL
            + "body; gh pr create fake"
            + NL
            + "EOF"
        )
        self.assert_denied(command)

    def test_h24_supported_heredoc_declaration_tail_falsy_denied(self) -> None:
        # supported delimiter では宣言行の後続 flag はスキャナから可視であり
        # falsy 判定が働く (宣言行スキャンを heredoc 演算子で打ち切る誤実装は
        # falsy を見逃して挿入し、実行時に後着 falsy が勝つ bypass になる)。
        command = (
            'gh pr create --title "t" --body-file - <<EOF --draft=false'
            + NL
            + "body"
            + NL
            + "EOF"
        )
        self.assert_denied(command)

    def test_h25_insertion_then_unsafe_fallback_denies_whole_command(
        self,
    ) -> None:
        # 後続 invocation の引数領域内 fallback による deny は先行 invocation
        # の書き換えより優先する。1 個目の rewrite を先に返して 2 個目の
        # unsafe を無視する誤実装は fail する (H22 と同じく、fallback が
        # どの位置で発動しても deny が全体に優先する契約の別 witness)。
        command = (
            'gh pr create --title "a" --body "b"; '
            'gh pr create --title "c" --body-file - <<E"OF"'
            + NL
            + "body"
            + NL
            + "EOF"
        )
        self.assert_denied(command)

    def test_h26_heredoc_marker_in_comment_ignored(self) -> None:
        # コメント内の <<EOF を heredoc 登録する誤実装は 2 行目を本文扱いし、
        # 2 個目の挿入欠落で fail する。
        command = (
            'gh pr create --title "t" --body "b" # see <<EOF usage'
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        expected = (
            'gh pr create --draft --title "t" --body "b" # see <<EOF usage'
            + NL
            + ':; gh pr create --draft --title "t2" --body "b2"'
        )
        self.assert_rewrite(command, expected)

    def test_h27_quoted_newline_after_pending_heredoc_not_body_start(
        self,
    ) -> None:
        # heredoc pending 登録後も、quote 外の生改行までは宣言行の引数走査が
        # 継続する。quote 内改行を本文モード開始と誤認する実装は falsy を
        # 本文扱いして見逃し、allow + 挿入を返して fail する (H24 と L10 の
        # 合成 witness)。
        command = (
            'gh pr create --body-file - <<EOF --title "multi'
            + NL
            + 'line" --draft=false'
            + NL
            + "body"
            + NL
            + "EOF"
        )
        self.assert_denied(command)

    def test_h28_falsy_invocation_after_foreign_fallback_denied(self) -> None:
        # security P1 の再現 witness。旧 2 分岐契約 (fallback が別コマンド
        # (`cat`) の領域で発動 → 不介入) では、この invocation は不介入と
        # なる。しかし bash 実行時には `<<E"OF"` は delimiter `EOF` として
        # 解釈され、後続の明示的 falsy invocation (`gh pr create
        # --draft=false ...`) がそのまま実行されてしまう bypass になっていた
        # (旧実装 master では deny)。新契約はこれを opaque fallback 発動時の
        # 常時 deny で塞ぐ。
        command = (
            'cat <<E"OF"; gh pr create --draft=false --title t --body b'
            + NL
            + "body"
            + NL
            + "EOF"
        )
        self.assert_denied(command)

    def test_h29_keyword_split_in_opaque_region_still_denied(self) -> None:
        # 難読化耐性の witness。opaque 領域の残余文字列への部分文字列判定で
        # deny を決める誤実装は、`cre\` + 改行 + `ate` という bash の行継続
        # による keyword 分断 (bash は行継続を除去して `gh pr create
        # --draft=false` を実行する) を素通しして fail する。粗フィルタは
        # 先頭の `: 'gh pr create'` (quoted literal) で通過するため、この
        # コマンドはそもそもスキャナに到達する。
        command = (
            ": 'gh pr create'; cat <<E\"OF\"; gh pr cre\\"
            + NL
            + "ate --draft=false --title t --body b"
            + NL
            + "EOF"
        )
        self.assert_denied(command)

    # ------------------------------------------------------------------
    # グループ A: 算術式 (A01〜A03 は現行実装で pass するはず。A04 は heredoc との
    # 複合パターンのため現行実装では fail (red) するはず — `<<` 内の `1` を heredoc
    # delimiter として誤登録すると pending キューが崩れ、最終行の挿入が欠落する)
    # ------------------------------------------------------------------

    def test_a01_dollar_double_paren_arithmetic(self) -> None:
        command = 's=$((x << 1)); gh pr create --title "t" --body "b"'
        expected = 's=$((x << 1)); gh pr create --draft --title "t" --body "b"'
        self.assert_rewrite(command, expected)

    def test_a02_command_position_double_paren_arithmetic(self) -> None:
        # `<< 2` を heredoc delimiter `2` として誤登録する実装では、2 行目が
        # heredoc 本文扱いになり挿入が欠落して fail する。
        command = (
            '(( n << 2 )); gh pr create --title "t" --body "b"'
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        expected = (
            '(( n << 2 )); gh pr create --draft --title "t" --body "b"'
            + NL
            + ':; gh pr create --draft --title "t2" --body "b2"'
        )
        self.assert_rewrite(command, expected)

    def test_a03_nested_parens_inside_arithmetic(self) -> None:
        # 最初の `))` で算術式を終了する深度非追跡実装では、内側 `((a+b))` の
        # 直後の `))` で `$((...))` が閉じたと誤認し、残る `<< 1 ))` の `<< 1` を
        # heredoc delimiter `1` として誤登録して 2 行目への挿入が欠落し fail する。
        # 式は bash 実測で valid ($(( ((a+b)) << 1 )) は a=1, b=2 で 6)。
        command = (
            'v=$(( ((a+b)) << 1 )); gh pr create --title "t" --body "b"'
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        expected = (
            'v=$(( ((a+b)) << 1 )); gh pr create --draft --title "t" --body "b"'
            + NL
            + ':; gh pr create --draft --title "t2" --body "b2"'
        )
        self.assert_rewrite(command, expected)

    def test_a04_arithmetic_heredoc_composite_trailing_invocation(
        self,
    ) -> None:
        # 算術式内の `<< 1` を heredoc delimiter `1` として誤登録する実装では、
        # pending キューが `[1, EOF]` のまま崩れ、実終端 `EOF` 行で本文モードが
        # 解除されず最終行の `gh pr create` への挿入が欠落する。最終行の `gh` は
        # `:;` の後 (行頭ではない) なので「2 行目以降の行頭コマンドは検出対象外」
        # という既存 limitation とは無関係。
        command = (
            'v=$(( (a+b) << 1 )); gh pr create --title "t" --body-file - <<EOF'
            + NL
            + "body; gh pr create fake"
            + NL
            + "EOF"
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        expected = (
            'v=$(( (a+b) << 1 )); gh pr create --draft --title "t" '
            '--body-file - <<EOF'
            + NL
            + "body; gh pr create fake"
            + NL
            + "EOF"
            + NL
            + ':; gh pr create --draft --title "t2" --body "b2"'
        )
        self.assert_rewrite(command, expected)

    # ------------------------------------------------------------------
    # グループ L: 行継続の原文保持 (現行実装で fail するはず)
    # ------------------------------------------------------------------

    def test_l01_token_boundary_line_continuation_preserved(self) -> None:
        # `pr` の直後に空白を置いた「トークン間」の行継続。bash はこれを
        # `gh pr create` と等価に扱う (空白なしの `pr\<LF>create` は `prcreate`
        # に連結されるため invocation にならず、このテストの対象外)。
        command = "gh pr " + BS + NL + 'create --title "t" --body "b"'
        expected = (
            "gh pr " + BS + NL + 'create --draft --title "t" --body "b"'
        )
        self.assert_rewrite(command, expected)

    def test_l02_single_quote_backslash_newline_literal_preserved(self) -> None:
        command = (
            'gh pr create --title "t" --body \'keep '
            + BS
            + NL
            + " literal'"
        )
        expected = (
            'gh pr create --draft --title "t" --body \'keep '
            + BS
            + NL
            + " literal'"
        )
        self.assert_rewrite(command, expected)

    def test_l03_double_quote_backslash_newline_preserved(self) -> None:
        command = (
            'gh pr create --title "t" --body "wrapped ' + BS + NL + 'line"'
        )
        expected = (
            'gh pr create --draft --title "t" --body "wrapped '
            + BS
            + NL
            + 'line"'
        )
        self.assert_rewrite(command, expected)

    def test_l04_quoted_heredoc_body_backslash_newline_preserved(self) -> None:
        command = (
            "gh pr create --title \"t\" --body-file - <<'EOF'"
            + NL
            + "line with "
            + BS
            + NL
            + "continuation"
            + NL
            + "EOF"
        )
        expected = (
            "gh pr create --draft --title \"t\" --body-file - <<'EOF'"
            + NL
            + "line with "
            + BS
            + NL
            + "continuation"
            + NL
            + "EOF"
        )
        self.assert_rewrite(command, expected)

    def test_l05_unquoted_heredoc_odd_backslash_defers_terminator(
        self,
    ) -> None:
        command = (
            'gh pr create --title "t" --body-file - <<EOF'
            + NL
            + "line1"
            + BS
            + NL
            + "EOF"
            + NL
            + "still body; gh pr create fake"
            + NL
            + "EOF"
        )
        expected = (
            'gh pr create --draft --title "t" --body-file - <<EOF'
            + NL
            + "line1"
            + BS
            + NL
            + "EOF"
            + NL
            + "still body; gh pr create fake"
            + NL
            + "EOF"
        )
        self.assert_rewrite(command, expected)

    def test_l06_quoted_heredoc_backslash_line_terminator_still_ends(
        self,
    ) -> None:
        declarations = ("<<'EOF'", '<<"EOF"', "<<" + BS + "EOF")
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                command = (
                    'gh pr create --title "t" --body-file - '
                    + declaration
                    + NL
                    + "line1"
                    + BS
                    + NL
                    + "EOF"
                    + NL
                    + ':; gh pr create --title "t2" --body "b2"'
                )
                expected = (
                    'gh pr create --draft --title "t" --body-file - '
                    + declaration
                    + NL
                    + "line1"
                    + BS
                    + NL
                    + "EOF"
                    + NL
                    + ':; gh pr create --draft --title "t2" --body "b2"'
                )
                self.assert_rewrite(command, expected)

    def test_l07_unquoted_heredoc_even_backslash_terminator_ends(
        self,
    ) -> None:
        command = (
            'gh pr create --title "t" --body-file - <<EOF'
            + NL
            + "line1"
            + BS
            + BS
            + NL
            + "EOF"
            + NL
            + ':; gh pr create --title "t2" --body "b2"'
        )
        expected = (
            'gh pr create --draft --title "t" --body-file - <<EOF'
            + NL
            + "line1"
            + BS
            + BS
            + NL
            + "EOF"
            + NL
            + ':; gh pr create --draft --title "t2" --body "b2"'
        )
        self.assert_rewrite(command, expected)

    def test_l08_line_continuation_in_heredoc_declaration(self) -> None:
        # bash 実測 (5.2.21): 行継続で結合された論理行上の `EOF` トークンは
        # terminator ではなくコマンド引数となり、本文は論理行終端 (実改行) から
        # 始まる。エスケープ改行を本文モード開始と誤認する実装は 2 行目 `EOF` を
        # terminator と誤認し、fake への挿入で fail する。
        command = (
            'gh pr create --title "t" --body-file - <<EOF '
            + BS
            + NL
            + "EOF"
            + NL
            + "still body; gh pr create fake"
            + NL
            + "EOF"
        )
        expected = (
            'gh pr create --draft --title "t" --body-file - <<EOF '
            + BS
            + NL
            + "EOF"
            + NL
            + "still body; gh pr create fake"
            + NL
            + "EOF"
        )
        self.assert_rewrite(command, expected)

    def test_l09_double_quoted_continuation_in_draft_value_denied(
        self,
    ) -> None:
        # このケースは現行実装でも pass する (normalize が行継続を一括削除する
        # ため)。red ではなく「normalize 廃止後の新スキャナが dq 内行継続を
        # トークン値の解釈で除去し、falsy 判定を維持すること」の退行防止テスト。
        command = (
            'gh pr create --draft="fal' + BS + NL + 'se" --title "t" --body "b"'
        )
        self.assert_denied(command)

    def test_l10_single_quote_newline_then_draft_false_denied(self) -> None:
        # single quote 内の生改行を物理行境界 (TNL) と誤解釈すると引数走査が
        # 停止し、後続の --draft=false を見逃して deny 漏れになる (enforce
        # 迂回)。その経路の固定。
        command = (
            "gh pr create --title \"t\" --body 'multi"
            + NL
            + "line' --draft=false"
        )
        self.assert_denied(command)

    def test_l11_unquoted_continuation_in_draft_value_denied(self) -> None:
        # quote 外の行継続はトークン連結され、falsy 判定は連結後の値
        # (--draft=false) で行う (L09 の quote 外対称ケース)。
        command = (
            "gh pr create --draft=fal" + BS + NL + 'se --title "t" --body "b"'
        )
        self.assert_denied(command)

    # ------------------------------------------------------------------
    # グループ B: 末尾 LF を含むバイト保持契約 (現行実装で fail するはず)
    # ------------------------------------------------------------------

    def test_b01_trailing_newline_preserved(self) -> None:
        # 現行実装は `COMMAND=$(... | jq -r ...)` の `$()` が末尾改行を trim するため、
        # 単純な末尾 LF (直前が backslash でない) は復元されず fail する。「挿入以外は
        # 1 バイトも変更しない」契約は末尾 LF を含む。
        command = 'gh pr create --title "t" --body "b"' + NL
        expected = 'gh pr create --draft --title "t" --body "b"' + NL
        self.assert_rewrite(command, expected)

    def test_b02_multiple_trailing_newlines_preserved(self) -> None:
        command = 'gh pr create --title "t" --body "b"' + NL + NL
        expected = 'gh pr create --draft --title "t" --body "b"' + NL + NL
        self.assert_rewrite(command, expected)

    # ------------------------------------------------------------------
    # グループ P: 性能 (現行実装で fail するはず)
    # ------------------------------------------------------------------

    def test_p01_fifty_kb_quoted_body_performance(self) -> None:
        body = "a" * 50_000
        command = f'gh pr create --title "t" --body "{body}"'
        expected = f'gh pr create --draft --title "t" --body "{body}"'
        start = time.monotonic()
        returncode, stdout = run_hook(command)
        elapsed = time.monotonic() - start
        self.assertEqual(returncode, 0, stdout)
        payload = json.loads(stdout)
        hook_output = payload["hookSpecificOutput"]
        self.assertEqual(hook_output["permissionDecision"], "allow", stdout)
        self.assertEqual(
            hook_output["updatedInput"]["command"], expected, stdout
        )
        self.assertLess(elapsed, 5.0)

    def test_p02_backslash_dense_twenty_kb_performance(self) -> None:
        body = (BS + "a") * 10_000
        command = f'gh pr create --title "t" --body "{body}"'
        expected = f'gh pr create --draft --title "t" --body "{body}"'
        start = time.monotonic()
        returncode, stdout = run_hook(command)
        elapsed = time.monotonic() - start
        self.assertEqual(returncode, 0, stdout)
        payload = json.loads(stdout)
        hook_output = payload["hookSpecificOutput"]
        self.assertEqual(hook_output["permissionDecision"], "allow", stdout)
        self.assertEqual(
            hook_output["updatedInput"]["command"], expected, stdout
        )
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
