#!/bin/bash
# block-bg-codex-wrapper.sh
# `run-codex-review.sh` wrapper を background で起動しようとする操作を deny する PreToolUse フック。
#
# policy: fail-open (PreToolUse / defense-in-depth 補助)
#   本 hook は補助的な regression 防御で、 真の push gate は block-pre-push.sh (= fail-closed)
#   が担う。 そのため jq 不在等の環境失敗時は silent に exit 0 で抜けて allow に倒す
#   (= 環境失敗で「合法な wrapper 起動」 が deny される false positive を避ける)。
#   検知ロジックに該当した場合のみ deny を返す。 真の保証は block-pre-push.sh が
#   marker hash check で行うため、 本 hook が抜けても未レビュー push は通らない。
#
# ## なぜ必要か
#
# v1.1.0 で codex review は wrapper script (run-codex-review.sh) 経由に切替え、 wrapper 自身が
# 完了時に codex marker を書く設計に統一した。 これは silent failure 経路を排除する意図だが、
# **wrapper を Bash tool の `run_in_background: true` で起動すると新たな regression が発生する**:
#   - wrapper 内部の `node codex-companion.mjs review --wait --scope branch` は foreground で
#     完走するため codex review 自体は正しく実行される
#   - wrapper 完了時に codex marker は書き込まれる (= block-pre-push.sh の hash check は通る)
#   - **しかし主 Claude session は wrapper の stdout / stderr (= codex review の verdict /
#     findings) を観察しない**。 Bash tool は bg 起動の場合 `BashOutput` で後追い取得する
#     必要があるが、 push gate は marker の存在だけ確認するため、 主 session は review 結果を
#     見ずに push に進める経路ができる。 結果として review 指摘が修正されないまま push が
#     通過する **foreground review 要件の regression**
#
# v1.0.0 までは PreToolUse の `block-bg-codex-review.sh` が `run_in_background: true` を deny
# して同類の問題を防いでいたが、 v1.1.0 で Skill 経由 `/codex:review` 廃止に伴い不要として
# 削除した。 しかし wrapper を bg で起動するという新経路に対する gate が欠如していたため、
# 本 hook を再導入する。
#
# ## 検知ロジック
#
# Bash tool の `command` 文字列に `run-codex-review.sh` substring を含み、 かつ `tool_input
# .run_in_background == true` の場合に deny する。 wrapper の起動は通常 `bash <abs-path>/run
# -codex-review.sh` の形 (deny メッセージで案内) なので、 path のどこかに `run-codex-review.sh`
# が現れる前提。 substring match なので、 ユーザ独自の wrapper alias (例: `bash my-codex.sh`)
# は対象外 (= cooperative 利用前提)。

# 予期せぬエラー時の診断 trap を install (実装は lib/exit-trap.sh)。
_PRE_PUSH_REVIEW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/exit-trap.sh
source "$_PRE_PUSH_REVIEW_SCRIPT_DIR/lib/exit-trap.sh"
# cmd-parser.sh は line continuation 正規化 (`normalize_line_continuations`) のため source する。
# substring match の前に `\<LF>` を削除して隣接 token を連結することで、 `bash .../run-codex-revie
# w.\<LF>sh` のような line continuation 経由の検知 bypass を塞ぐ (= block-pre-push.sh も同じ
# 防御を行っている)。
# shellcheck source=lib/cmd-parser.sh
source "$_PRE_PUSH_REVIEW_SCRIPT_DIR/lib/cmd-parser.sh"
install_exit_trap "block-bg-codex-wrapper" "run-codex-review wrapper の background 起動 deny が機能していない可能性があり、 wrapper を bg で起動した際に marker が書かれて review 結果未観察のまま push が通る経路に戻っているかもしれません。"

INPUT=$(cat)

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -n "$COMMAND" ] || exit 0

# bash `$(...)` の trailing-LF trim で消えた `\<LF>` を復元 (詳細は cmd-parser.sh の
# 「末尾 `\<LF>` 復元の caller 側 inline パターン」 セクション)。
case "$COMMAND" in *\\) COMMAND="${COMMAND}"$'\n' ;; esac

# 行継続 `\<改行>` を **削除** して隣接 token を連結する (bash 実挙動と一致)。 これを
# やらないと `bash .../run-codex-revie\<LF>w.sh` のような書き方で substring match
# (`run-codex-review.sh`) を bypass される経路が残る。 fast-path で line continuation を
# 含まない 99% の入力は `$(...)` fork を回避する (cmd-parser.sh 関数内に fast-path あり)。
case "$COMMAND" in
  *\\$'\n'*) COMMAND=$(normalize_line_continuations "$COMMAND") ;;
esac

# 粗フィルタ: command 文字列に `run-codex-review.sh` が含まれなければ即抜け (fork なし)。
case "$COMMAND" in
  *run-codex-review.sh*) ;;
  *) exit 0 ;;
esac

RUN_IN_BG=$(printf '%s' "$INPUT" | jq -r '.tool_input.run_in_background // false')
if [ "$RUN_IN_BG" != "true" ]; then
  exit 0
fi

REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 `run-codex-review.sh` wrapper を `run_in_background: true` で起動することはできません。

理由: wrapper 自身は foreground で codex review を実行して marker を書きますが、 Bash tool の `run_in_background: true` で起動すると **主 Claude session は wrapper の stdout / stderr (= codex review の verdict / findings) を観察しません**。 主 session は marker の存在だけで push gate を通過してしまうため、 review 指摘が修正されないまま push が成立する **foreground review 要件の regression** になります。

対応: Bash tool 呼び出しから `run_in_background: true` を **外して** (デフォルト false で) 再実行してください。 wrapper は内部で codex companion を `--wait` で foreground 起動するため、 Bash 呼び出し自体が review 完了まで block しますが、 これが本プラグインの想定する正しい使い方です (= review 結果を主 session で観察してから push 判断する)。
EOF
)

jq -n --arg reason "$REASON" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason: $reason
  }
}'

exit 0
