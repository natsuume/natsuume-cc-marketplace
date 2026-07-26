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
    転換する。

    分類は「canonical token 値」(single/double quote 除去・backslash escape
    解決・fragment 連結を行った shell word の静的な値) に対して行い、head token
    のみさらに basename 正規化する。動的展開 (`$VAR` 等) を含み canonical 値を
    静的に決定できない head は解析不能として保守的に実行形とする。

    実行形と分類するのは (a) head の basename が wrapper 名そのもの (b) shell
    interpreter (bash/sh/dash/zsh/ksh) / exec-capable builtin (source / `.` /
    exec / eval) が head で wrapper substring と共存 (option の精密解析はせず
    保守的に実行形) (c) launcher prefix (command / builtin / env / timeout /
    nohup / nice / setsid / stdbuf / sudo / doas / time / `!`) は反復的に剥がして
    残りを再分類する。剥がすたびに代入 slot (NAME=VALUE 列) を再評価して skip し、
    代入値に wrapper substring があれば実行形 (GIT_EXTERNAL_DIFF 型)。timeout の
    duration operand (canonical 値が数値 + 任意の s/m/h/d suffix) は消費してから
    再分類する。`-` 始まりの canonical token が現れたら解析不能として実行形
    (d) script/対話内実行面を持つ
    sed/awk/xargs/less/more/parallel (e) option-aware: find の
    -exec/-execdir/-ok/-okdir、rg の --pre / --pre=*、sort の
    --compress-program / --compress-program=* (canonical 値で判定。値を取る
    option の literal 引数 (`rg -e '--pre'`) も保守的 superset として deny し、
    false positive を受容する) (f) git は現行特例 (縮小 subcommand 集合 +
    --ext-diff/--textconv 検査、いずれも canonical 値で判定) を維持 (g) segment
    先頭の leading 代入列と launcher 剥がし中に skip する代入列の値に wrapper
    substring を含む場合、および head token が無い segment (assignment-only)。
    引数位置の NAME=VALUE 形 (shell は env 代入と解釈しない) は検査しない。

    それ以外の引数・quoted 文字列としての出現は言及扱いで allow する (fail-open
    への意図的転換)。本 hook は cooperative 前提の補助 gate であり (真の push
    gate は block-pre-push.sh)、任意の未知 launcher・動的構築コマンドへの完全性は
    保証しない。言及扱い segment の pipe chain 検査 (neighbor は従来の read-only
    allowlist を exact 判定のまま維持し、basename 正規化を適用しない) と
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


if __name__ == "__main__":
    unittest.main()
