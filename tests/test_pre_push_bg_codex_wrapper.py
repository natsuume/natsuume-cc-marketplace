"""block-bg-codex-wrapper.sh の agent_type 検証 gate (fail-closed) 契約テスト
(issue #267)。

契約: agent_type 検証 gate (fail-closed): command に run-codex-review.sh を含む Bash
実行は、hook payload のトップレベル agent_type が pre-push-review:codex-reviewer に
完全一致する場合のみ許可。欠落 (= メインセッション or agent_type 未対応の旧 Claude
Code)・不一致はいずれも deny。既存の bg / pipeline deny は agent_type 一致時も維持。
jq 不在等の環境失敗は既存どおり fail-open (本テストの対象外)。

本テストは TDD Phase A で gate 実装前の red テストとして追加され、Phase B の実装後は
green になる。現在は実装済みの regression テストとして契約を固定する。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = (
    ROOT
    / "plugins"
    / "pre-push-review"
    / "hooks"
    / "scripts"
    / "block-bg-codex-wrapper.sh"
)

WRAPPER_COMMAND = (
    "bash /opt/claude/plugins/pre-push-review/hooks/scripts/run-codex-review.sh"
)
CODEX_REVIEWER_AGENT_TYPE = "pre-push-review:codex-reviewer"


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class BlockBgCodexWrapperAgentTypeGateTest(unittest.TestCase):
    def run_hook(
        self, payload: dict[str, object], cwd: Path
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps(payload).encode("utf-8"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
        )

    def assert_denied(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotEqual(result.stdout, b"")
        response = json.loads(result.stdout)
        self.assertEqual(
            response["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def assert_allowed(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_agent_type_missing_foreground_wrapper_is_denied_with_reviewer_reason(
        self,
    ) -> None:
        # メインセッションでは agent_type が欠落するため、foreground でも deny する。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": WRAPPER_COMMAND},
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)
            response = json.loads(result.stdout)
            reason = response["hookSpecificOutput"]["permissionDecisionReason"]
            self.assertIn(CODEX_REVIEWER_AGENT_TYPE, reason)

    def test_agent_type_mismatch_foreground_wrapper_is_denied(self) -> None:
        # built-in subagent 等の別 agent_type からの起動も deny する。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": WRAPPER_COMMAND},
                "agent_type": "general-purpose",
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_agent_type_match_foreground_wrapper_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": WRAPPER_COMMAND},
                "agent_type": CODEX_REVIEWER_AGENT_TYPE,
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_agent_type_match_background_flag_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": WRAPPER_COMMAND,
                    "run_in_background": True,
                },
                "agent_type": CODEX_REVIEWER_AGENT_TYPE,
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_agent_type_match_pipeline_command_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"{WRAPPER_COMMAND} | tee /tmp/log.txt",
                },
                "agent_type": CODEX_REVIEWER_AGENT_TYPE,
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_unrelated_command_with_missing_agent_type_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "echo hello"},
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class BlockBgCodexWrapperExecFormClassificationTest(unittest.TestCase):
    """実行形 segment 分類 (codex review P2 指摘の regression 修正) のテスト。

    substring `run-codex-review.sh` を含んでいても、 wrapper を実行せず言及する
    だけの read-only コマンド (cat / git diff / grep 等) は agent_type gate の対象外
    (allow) とし、 interpreter 起動・コマンド置換を含む形・不明コマンドのみを実行形
    として gate する契約を固定する。
    """

    def run_hook(
        self, payload: dict[str, object], cwd: Path
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps(payload).encode("utf-8"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
        )

    def assert_denied(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotEqual(result.stdout, b"")
        response = json.loads(result.stdout)
        self.assertEqual(
            response["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def assert_allowed(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_cat_mention_with_missing_agent_type_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "cat plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_git_diff_mention_with_missing_agent_type_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "git diff -- plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_grep_piped_to_head_mention_with_missing_agent_type_is_allowed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "grep -n marker plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh | head -5"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_sed_mention_with_missing_agent_type_is_denied(self) -> None:
        # sed は allowlist 外 (GNU sed の e コマンドという子プロセス実行面を持つため)。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "sed -n '1,50p' plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_find_exec_with_missing_agent_type_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        r"find . -name run-codex-review.sh -exec bash {} \;"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_git_global_option_with_missing_agent_type_is_denied(self) -> None:
        # 直後 token が `-c` (global option) のため git 特例の言及扱いにならない。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "git -c core.pager=x diff run-codex-review.sh"
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_command_substitution_fallback_missing_agent_type_is_denied(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        'WRAPPER=$(find "$HOME/.claude/plugins/cache" '
                        "-path '*run-codex-review.sh' -type f | head -1) "
                        '&& bash "$WRAPPER"'
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_command_substitution_fallback_agent_type_match_is_allowed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        'WRAPPER=$(find "$HOME/.claude/plugins/cache" '
                        "-path '*run-codex-review.sh' -type f | head -1) "
                        '&& bash "$WRAPPER"'
                    )
                },
                "agent_type": CODEX_REVIEWER_AGENT_TYPE,
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_command_substitution_fallback_with_trailing_pipe_is_denied(
        self,
    ) -> None:
        # INDIRECTION フラグ + 位置を問わない単独 `|` の存在で、 agent_type 一致でも
        # deny する (末尾の `| tee` は wrapper 起動 segment に直接隣接していない)。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        'WRAPPER=$(find "$HOME/.claude/plugins/cache" '
                        "-path '*run-codex-review.sh' -type f | head -1) "
                        '&& bash "$WRAPPER" | tee /tmp/log.txt'
                    )
                },
                "agent_type": CODEX_REVIEWER_AGENT_TYPE,
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_allowlist_command_with_command_substitution_is_denied(self) -> None:
        # allowlist コマンド (cat) でもコマンド置換を含む場合は規則 1 が優先し実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "cat $(bash run-codex-review.sh)"},
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_single_quoted_dollar_paren_literal_is_allowed(self) -> None:
        # codex review High 指摘の regression 修正確認: 引用符内の literal な `$(` を
        # indirection と誤分類しない (wrapper のコマンド置換使用箇所を監査する grep)。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "grep -n '$(' plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_double_quoted_escaped_dollar_paren_literal_is_allowed(self) -> None:
        # double quote 内でも `\$(` は escape されているため indirection ではない。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        'grep -n "\\$(" plugins/pre-push-review/hooks/scripts/'
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_single_quoted_backtick_literal_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "grep -n '`' plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_single_quoted_process_substitution_literal_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "grep -n '<(' plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_double_quoted_real_command_substitution_is_denied(self) -> None:
        # regression guard: double quote 内の実 command 置換は quote-aware 判定後も
        # 引き続き indirection として deny される。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        'cat "$(bash plugins/pre-push-review/hooks/scripts/'
                        'run-codex-review.sh)"'
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_cat_piped_to_bash_with_missing_agent_type_is_denied(self) -> None:
        # 修正 1 の代表攻撃形: mention 扱い (cat) segment に隣接する pipe 接続先が
        # allowlist 外の bash であり、 stdin 経由で wrapper の内容を実行できる。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "cat plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh | bash"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_cat_head_piped_to_bash_with_missing_agent_type_is_denied(
        self,
    ) -> None:
        # transitive chain: allowlist コマンド (head) を 1 段挟んでも、 pipe chain
        # 全体を検査するため末尾の bash が検出され deny される。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "cat plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh | head -100 | bash"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_bash_piped_to_grep_mention_with_missing_agent_type_is_denied(
        self,
    ) -> None:
        # 上流側 neighbor (bash gen-pattern.sh) が allowlist 外のため、 下流の grep
        # mention segment を含む pipe chain 全体が実行形として deny される。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "bash gen-pattern.sh | grep -n -f - "
                        "plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_full_mention_chain_missing_agent_type_is_allowed(self) -> None:
        # pipe chain 内の全 segment が mention-safe (cat / grep / head) なら allow の
        # まま維持される。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "cat plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh | grep -n marker | head -5"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_grep_piped_from_command_substitution_neighbor_is_denied(
        self,
    ) -> None:
        # neighbor の indirection: allowlist head (grep) でもコマンド置換の内側
        # (bash) が pipe の stdin (= wrapper 内容) を読んで実行できるため deny する。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "cat plugins/pre-push-review/hooks/scripts/"
                        'run-codex-review.sh | grep "$(bash)"'
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_git_grep_mention_with_missing_agent_type_is_denied(self) -> None:
        # 修正 2: git 特例の subcommand 集合から grep を除外した (git grep の
        # --open-files-in-pager / -O<cmd> option が外部プログラムを起動できるため)。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "git grep -n marker -- "
                        "plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_git_log_piped_to_head_mention_missing_agent_type_is_allowed(
        self,
    ) -> None:
        # git 特例 (log) の mention segment + allowlist neighbor (head) の chain は
        # 引き続き allow される。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "git log --oneline -- "
                        "plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh | head -5"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)


@unittest.skipUnless(shutil.which("jq"), "hook integration requires jq")
class BlockBgCodexWrapperExecPositionClassificationTest(unittest.TestCase):
    """issue #339: executable 位置分類の契約テスト。

    wrapper 名 (`run-codex-review.sh`) を含む segment の分類を、read-only
    allowlist 方式 (不明コマンド = fail-closed で実行形) から executable 位置方式へ
    転換する。分類は「canonical token 値」(single/double quote 除去・backslash
    escape 解決・fragment 連結を行った shell word の静的な値) に対して行い、
    head token のみさらに basename 正規化する。canonical 化は本 hook 内の
    専用 helper に閉じ、共有 parser (cmd-parser.sh) や push gate
    (block-pre-push.sh) の token 判定は変更しない。

    分類は次の順序付き決定表で行う。各 step は上から順に評価し、最初に
    確定した判定を採用する:

    1. segment が indirection (quote-aware のコマンド置換 / バッククォート /
       プロセス置換) を含む → 実行形 (INDIRECTION フラグ連動。現行規則 1 の
       まま)
    2. leading 代入列 (segment 先頭の NAME=VALUE 連鎖) を処理する: 代入値に
       wrapper substring があれば実行形 (GIT_EXTERNAL_DIFF 型)。処理後に
       head token が無い (assignment-only / 空) → 実行形
    3. head token の canonical 化 (single/double quote 除去・backslash
       escape 解決・fragment 連結。hook 専用 helper で行い共有 parser は
       変更しない) が失敗する (動的展開 `$VAR` 等を含む) → 実行形
    4. 無害 builtin 例外: canonical head が echo / test / `[` / true /
       false / pwd / type に完全一致 (basename 適用なし。
       word-shape・keyword・builtin superset 検査より先に評価するため `[`
       も到達可能) → mention 候補 (step 12 へ)
    5. launcher prefix 例外 (word-shape / keyword / builtin superset 検査
       より先に評価する明示的第二例外): canonical head が command /
       builtin / env / timeout / nohup / nice / setsid / stdbuf / sudo /
       doas / time / `!` に完全一致 (basename 適用なし) → 限定解析で
       剥がす: 直後の代入 slot (NAME=VALUE 列) を再評価して skip し代入値に
       wrapper substring があれば実行形。timeout は数値 + 任意の s/m/h/d
       suffix の単純形 duration operand のみ消費。`-` 始まりまたは認識
       できない operand が現れたら実行形。剥がし後の残り token 列を
       step 3 から再評価する
    6. canonical head が通常の外部コマンド word の形 (英数字・`_`・`/` の
       いずれかで始まり `[A-Za-z0-9_/.+-]` のみで構成) でない (redirection
       演算子・記号構文等) → 実行形
    7. canonical head が bash keyword 静的 superset (if / then / else /
       elif / fi / case / esac / for / select / while / until / do /
       done / in / function / coproc / `{` / `}` / `[[` / `]]`。time と
       `!` は step 5 で処理済み) に該当 → 実行形
    8. canonical head が shell builtin 静的 superset (対応 bash 世代の
       compgen -b 相当の全集合。source / `.` / exec / eval / trap /
       export / declare / readonly / typeset / local / set / unset /
       shopt / bind / complete / mapfile / read / printf / kill / wait /
       alias / cd / pushd / popd / return / break / continue / shift /
       exit / let / eval 等を含む。step 4 の無害 builtin と step 5 の
       launcher は処理済みのため到達しない) に該当 → 実行形 (declaration
       builtin の代入形引数の値検査を含む: 値に wrapper substring が
       あれば当然実行形だが、builtin superset 該当時点で実行形のためこの
       検査は宣言的な補強である)
    9. head の basename (step 9 以降でのみ basename を適用する) が
       wrapper 名 (run-codex-review.sh) に完全一致 → 実行形。shell
       interpreter (bash/sh/dash/zsh/ksh) が head で wrapper substring と
       共存 → 実行形 (option の精密解析はしない)
    10. script/対話内実行面を持つ sed/awk/xargs/less/more/parallel →
        実行形。option-aware: find の -exec/-execdir/-ok/-okdir、rg の
        --pre / --pre=*、sort の --compress-program (--com 以上の
        prefix 一致、= 付き含む) が canonical token として存在 → 実行形
        (値を取る option の literal 引数も保守的 superset として deny し
        false positive を受容する。危険 option の列挙は受容境界であり、
        列挙外の value-taking option は残余ギャップとして受容する)
    11. git 特例: 縮小 subcommand 集合 (diff / log / show / status /
        ls-files / rev-parse / cat-file) に直後 token が一致し、
        --ext-diff / --textconv (canonical 値・prefix でなく完全一致) が
        無い場合のみ mention 候補 (step 12 へ)。特例に一致しない git は
        すべて実行形 (fall-through は実行形側であり mention 側へ落ちない)
    12. ここまで実行形と確定しなかった head (無害 builtin・git 特例・
        外部コマンド形の不明 head) は mention 候補: 従来の pipe chain
        検査 (neighbor は read-only allowlist の exact 判定のまま、
        basename 正規化なし — 中心 segment との非対称は受容境界) を
        通過すれば mention (allow)、通過しなければ実行形

    wrapper 名の判定は exact basename 一致であり、変則 path (basename が
    wrapper 名を部分包含する別名ファイル) は cooperative 境界として対象外
    (ヘッダの wrapper alias 対象外宣言と同じ受容範囲)。redirection 演算子の
    うち事前正規化 sed が空白置換する形 (`2>&1` / `&>` / `<<<` 等) は分類前に
    除去されるため、step 6 の主張は正規化後の segment に対して適用される
    (正規化前 head の評価は別 issue で扱う)。

    以上のどの実行形判定にも該当しない「外部コマンド形の不明 head」による
    引数・quoted 文字列としての出現のみ、言及扱いで allow する (fail-open は
    この 1 経路に限定する。issue #339 の意図的転換)。本 hook は cooperative
    前提の補助 gate であり (真の push gate は block-pre-push.sh)、外部
    コマンド形の未知 launcher (`frobnicate <wrapper>` 等) への完全性は
    保証しない。言及扱い segment の pipe chain 検査 (neighbor は従来の
    read-only allowlist を exact 判定のまま維持し、basename 正規化を適用
    しない — 中心 segment との判定非対称は受容境界として明示する) と
    indirection 規則 1 は現行のまま維持する。
    """

    def run_hook(
        self, payload: dict[str, object], cwd: Path
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps(payload).encode("utf-8"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
        )

    def assert_denied(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotEqual(result.stdout, b"")
        response = json.loads(result.stdout)
        self.assertEqual(
            response["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def assert_allowed(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_rg_pattern_reference_is_allowed(self) -> None:
        # rg の pattern 引数 (issue 実測の誤検知形)。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "rg -n 'run-codex-review.sh' "
                        "plugins/pre-push-review/hooks/scripts"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_rg_file_argument_is_allowed(self) -> None:
        # rg のファイル引数。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "rg -n marker plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_unknown_command_argument_is_allowed(self) -> None:
        # 不明コマンドの引数参照は fail-open へ転換する。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "echo run-codex-review.sh"},
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_find_without_exec_option_is_allowed(self) -> None:
        # find は -exec 系 option がなければ言及扱い。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "find . -name run-codex-review.sh"},
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_sort_without_compress_program_is_allowed(self) -> None:
        # sort は --compress-program がなければ言及扱い。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "sort plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_rg_mention_piped_to_safe_neighbor_is_allowed(self) -> None:
        # mention 扱い segment + allowlist neighbor (head) の chain は allow。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "rg -n marker plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh | head -5"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_rg_quoted_pre_literal_pattern_is_denied(self) -> None:
        # canonical 値が --pre に一致するため保守的 superset として deny
        # (意図的 false positive の契約固定。-e の値の literal pattern だが
        # arity 解析は行わない)。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "rg -e '--pre' plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_rg_pre_option_is_denied(self) -> None:
        # rg --pre は子プロセス実行面を持つ option-aware 検査対象。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "rg --pre bash run-codex-review.sh"},
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_rg_pre_equals_option_is_denied(self) -> None:
        # rg --pre=* も同様に option-aware 検査対象。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "rg --pre=bash marker run-codex-review.sh"
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_sort_compress_program_option_is_denied(self) -> None:
        # sort --compress-program=* も option-aware 検査対象。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "sort --compress-program=gzip run-codex-review.sh"
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_find_execdir_option_is_denied(self) -> None:
        # find -execdir も -exec と同じく option-aware 検査対象。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        r"find . -name run-codex-review.sh -execdir bash {} \;"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_source_builtin_is_denied(self) -> None:
        # source builtin は shell builtin として実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "source plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_dot_builtin_is_denied(self) -> None:
        # `.` builtin も source と同じく実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        ". plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_exec_builtin_is_denied(self) -> None:
        # exec builtin も shell builtin として実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "exec bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_bash_dash_c_with_wrapper_substring_is_denied(self) -> None:
        # bash -c は shell interpreter が先頭で wrapper substring と共存。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "bash -c 'bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh'"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_env_prefix_launch_is_denied(self) -> None:
        # env launcher prefix (単純形) を剥がして再分類しても実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "env FOO=1 bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_timeout_prefix_launch_is_denied(self) -> None:
        # timeout launcher prefix (単純形) を剥がして再分類しても実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "timeout 60 bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_timeout_with_flag_launch_is_denied(self) -> None:
        # launcher prefix に flag が付くと解析不能として保守的に実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "timeout -s TERM 60 bash "
                        "plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_nice_prefix_launch_is_denied(self) -> None:
        # nice launcher prefix (単純形) を剥がして再分類しても実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "nice bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_nohup_prefix_launch_is_denied(self) -> None:
        # nohup launcher prefix (単純形) を剥がして再分類しても実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "nohup bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_command_prefix_launch_is_denied(self) -> None:
        # command launcher prefix (単純形) を剥がして再分類しても実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "command bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_assignment_only_wrapper_path_is_denied(self) -> None:
        # 先頭コマンド token が無い assignment-only segment は fail-closed。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "WRAPPER=plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_git_external_diff_env_assignment_is_denied(self) -> None:
        # env 代入値経由の実行面 (GIT_EXTERNAL_DIFF 型)。現行実装は git 特例に
        # 一致して allow される既知の穴の修正契約。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "GIT_EXTERNAL_DIFF=./run-codex-review.sh git diff"
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_direct_path_execution_is_denied(self) -> None:
        # 先頭 token の basename が wrapper 名そのもの。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_zsh_interpreter_launch_is_denied(self) -> None:
        # zsh interpreter が先頭で wrapper substring と共存。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "zsh plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_rg_mention_piped_to_bash_is_denied(self) -> None:
        # mention でも neighbor (bash) が非 mention-safe なら chain 検査で実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "rg -l marker plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh | bash"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_echo_piped_to_xargs_bash_is_denied(self) -> None:
        # neighbor の xargs は非 mention-safe。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "echo run-codex-review.sh | xargs bash"},
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_rg_quoted_active_pre_option_is_denied(self) -> None:
        # quoted でも canonical 値は --pre で実際に外部実行 option。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "rg '--pre' bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_rg_fragmented_pre_option_is_denied(self) -> None:
        # fragment 連結後に --pre になる canonical 値。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "rg --'pre' foo plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_git_quoted_ext_diff_option_is_denied(self) -> None:
        # git option も canonical 値で判定する。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "git diff --'ext-diff' "
                        "plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_find_quoted_exec_option_is_denied(self) -> None:
        # find の -exec option 判定も canonical 値 (quote 除去後) で行う。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "find . -name run-codex-review.sh '-exec' bash '{}' ';'"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_eval_builtin_is_denied(self) -> None:
        # eval は exec-capable builtin。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "eval 'bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh'"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_command_eval_is_denied(self) -> None:
        # launcher (command) 剥がし後に eval が残る。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "command eval 'bash plugins/pre-push-review/hooks/"
                        "scripts/run-codex-review.sh'"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_builtin_eval_is_denied(self) -> None:
        # builtin も launcher prefix として剥がされ、残りに eval が現れる。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "builtin eval 'bash plugins/pre-push-review/hooks/"
                        "scripts/run-codex-review.sh'"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_time_keyword_launch_is_denied(self) -> None:
        # time prefix + path 修飾 interpreter (basename 正規化で bash と一致)。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "time /bin/bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_bang_keyword_launch_is_denied(self) -> None:
        # `!` も launcher prefix として剥がして再分類する。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "! bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_path_qualified_bash_is_denied(self) -> None:
        # head は basename 正規化されるため path 修飾でも interpreter 一致。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "/bin/bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_path_qualified_rg_with_pre_is_denied(self) -> None:
        # option-aware コマンドの basename 正規化 (path 修飾された rg --pre)。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "/usr/bin/rg --pre bash "
                        "plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_path_qualified_sudo_launch_is_denied(self) -> None:
        # sudo launcher も path 修飾かつ basename 正規化で検出する。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "/usr/bin/sudo bash "
                        "plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_sudo_launch_is_denied(self) -> None:
        # sudo は launcher prefix に追加された。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "sudo bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_sudo_nested_assignment_is_denied(self) -> None:
        # launcher (sudo) 剥がし後に代入 slot を再評価する。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "sudo GIT_EXTERNAL_DIFF=./run-codex-review.sh git diff"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_argument_position_assignment_shape_is_allowed(self) -> None:
        # 引数位置の NAME=VALUE 形は env 代入ではないため検査しない。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "echo X=run-codex-review.sh"},
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_unknown_launcher_argument_is_allowed(self) -> None:
        # unknown head の fail-open を明示的に固定する。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "frobnicate plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_dynamic_head_token_is_denied(self) -> None:
        # canonical 値を静的決定できない head は保守的に実行形とする。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        '"$CMD" plugins/pre-push-review/hooks/scripts/'
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_subshell_group_launch_is_denied(self) -> None:
        # subshell に接着した head は解析不能として保守的に実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "(cd /tmp && bash plugins/pre-push-review/hooks/"
                        "scripts/run-codex-review.sh)"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_brace_group_launch_is_denied(self) -> None:
        # brace group に接着した head も subshell と同様に実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "{ bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh; }"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_shell_keyword_head_launch_is_denied(self) -> None:
        # shell keyword (if) が head の場合もグループ内は解析せず実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "if true; then bash plugins/pre-push-review/hooks/"
                        "scripts/run-codex-review.sh; fi"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_export_wrapper_path_assignment_is_denied(self) -> None:
        # declaration builtin (export) の代入値に wrapper substring。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "export WRP=plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_declare_wrapper_path_assignment_is_denied(self) -> None:
        # declare + flag 形でも declaration builtin の代入値検査は維持する。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "declare -x WRP=plugins/pre-push-review/hooks/"
                        "scripts/run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_coproc_keyword_launch_is_denied(self) -> None:
        # coproc は bash keyword の静的 superset に該当し実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "coproc bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_trap_callback_builtin_is_denied(self) -> None:
        # trap は shell builtin superset の callback 面を持つため実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "trap 'bash plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh' EXIT"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_redirection_head_launch_is_denied(self) -> None:
        # redirection 演算子が head に来る非コマンド形は解析不能として実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        ">codex.log bash plugins/pre-push-review/hooks/"
                        "scripts/run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_timeout_unrecognized_operand_is_denied(self) -> None:
        # 認識できない duration operand (1,5) は解析不能として実行形
        # (grep 自体は read-only だが operand 解析不能のため実行形に落ちる)。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "timeout 1,5 grep -n marker plugins/pre-push-review/"
                        "hooks/scripts/run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_sort_abbreviated_compress_option_is_denied(self) -> None:
        # --compress-prog は危険 option の保守的 prefix 一致で deny。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "sort --compress-prog=gzip plugins/pre-push-review/"
                        "hooks/scripts/run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_type_builtin_mention_is_allowed(self) -> None:
        # type は無害 builtin allowlist に pin (現行実装では deny の見込み)。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "type run-codex-review.sh"},
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_mention_piped_to_non_allowlist_neighbor_is_denied(self) -> None:
        # neighbor (jq) が非 mention-safe allowlist のため chain 全体が実行形。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "rg -l marker plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh | jq ."
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)

    def test_command_prefixed_mention_head_is_allowed(self) -> None:
        # step 5 の剥がし後に外部コマンド形 head (grep) へ到達する pin。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "command grep -n marker plugins/pre-push-review/"
                        "hooks/scripts/run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_bracket_builtin_mention_is_allowed(self) -> None:
        # step 4 (無害 builtin) が step 6 (word-shape) に先行する pin。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "[ -f plugins/pre-push-review/hooks/scripts/"
                        "run-codex-review.sh ]"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_allowed(result)

    def test_git_unlisted_subcommand_is_denied(self) -> None:
        # step 11 の git 特例 fall-through は実行形側に落ちる pin。
        with tempfile.TemporaryDirectory() as name:
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "git difftool plugins/pre-push-review/hooks/"
                        "scripts/run-codex-review.sh"
                    )
                },
            }
            result = self.run_hook(payload, Path(name))
            self.assert_denied(result)


if __name__ == "__main__":
    unittest.main()
