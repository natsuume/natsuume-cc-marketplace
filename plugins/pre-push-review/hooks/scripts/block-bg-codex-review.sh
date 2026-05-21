#!/bin/bash
# block-bg-codex-review.sh
# `/codex:review` を background で起動しようとする操作を deny する PreToolUse フック。
#
# ## なぜ必要か
#
# auto-mark.sh は background 起動 (= Bash tool の `run_in_background: true`、 もしくは
# codex companion の `--background` フラグ) を marker 書き込み対象から **silently skip**
# する。 silently skip すると Claude には失敗が見えず、 後で `git push` を試みたときに
# block-pre-push.sh の marker 検証で deny されて初めて気付くため、 review 1 サイクルが
# 丸ごと無駄になる。
#
# PreToolUse で起動自体を deny すれば、 やり直しコストが「review 1 サイクル」 →
# 「コマンドから background 指定を外して再実行」 に圧縮される。
#
# ## 2 種類の "background" を両方止める
#
# 1. **Bash tool option `run_in_background: true`**:
#    Claude が Bash tool 呼び出し時に option として指定するケース。 PostToolUse は Bash
#    launch 完了時点で発火するため、 background 起動だと codex review 本体は未完了。
#
# 2. **codex companion の `--background` フラグ**:
#    companion 自体が job を queue して即 return するケース。 Bash tool option が false
#    でも companion の return 時点で review 本体は未完了。
#
# どちらも「PostToolUse 発火時点で review 未完了」 という同じ問題を起こすため、 同じ
# hook で両方 deny する。
#
# ## block-pre-push.sh との関係
#
# 両者とも PreToolUse + matcher: Bash で登録される。 hooks.json の hooks 配列に並列追加
# すれば、 どちらか一方が deny を返した時点で tool 実行が止まる仕組み (Claude Code の
# PreToolUse hook semantics)。 関心が異なる (git push gate vs review 起動 gate) ため
# script は分離する。

# 予期せぬエラー時の診断 trap を install (実装は lib/exit-trap.sh)。
# 本 hook は「対象でない → exit 0」「background 起動 → deny JSON 出力 → exit 0」 のいずれも
# exit 0 で抜ける設計。 想定外の非ゼロ終了が発生した場合のみ stderr に診断ログを出す。
_PRE_PUSH_REVIEW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/exit-trap.sh
source "$_PRE_PUSH_REVIEW_SCRIPT_DIR/lib/exit-trap.sh"
install_exit_trap "block-bg-codex-review" "codex review の background 起動 deny が機能していない可能性があり、 \`--background\` 経由の review が silent failure する経路に戻っているかもしれません。"

INPUT=$(cat)

# 粗フィルタは設けない。 line continuation `\<改行>` で companion path 自体が split された
# 場合 (例: `codex-compa\<LF>nion.mjs`)、 INPUT 内では JSON escape (`\\\n`) として現れて
# `*codex-companion*` glob にも `codex-companion` substring 検査にも一致しなくなる。
# 粗フィルタを通過した後の normalize で連結する設計だと、 「粗フィルタ通過以前」 の段階で
# bypass が成立してしまう。 厳密検知 (`is_codex_review_invocation`) が security boundary を
# 担う設計に統一し、 粗フィルタは optimize としても廃止する (jq 2 fork の hot path コストは
# 全 Bash 呼び出しで走る重さよりも、 background 起動の silent failure を確実に防ぐ価値の方が
# 高い)。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

SCRIPT_DIR="$_PRE_PUSH_REVIEW_SCRIPT_DIR"
# 本 script は L80 付近で `normalize_line_continuations` を直接呼ぶため cmd-parser.sh
# を source する。 codex-review-detect.sh は同関数を提供しないため (v0.8.0 で
# cmd-parser.sh に移動) cmd-parser.sh を独立に source する必要がある。
# shellcheck source=lib/cmd-parser.sh
source "$SCRIPT_DIR/lib/cmd-parser.sh"
# shellcheck source=lib/codex-review-detect.sh
source "$SCRIPT_DIR/lib/codex-review-detect.sh"

# command と run_in_background を別々の jq 呼び出しで取得する。 `@tsv` で 1 回 jq に
# まとめる方法もあるが、 jq の TSV エンコードが LF を `\n` (literal 2 文字) にエスケープ
# するため、 line continuation `\<改行>` (literal backslash + LF) が hook の normalize 対象
# として失われる。 line continuation bypass 防止が成立する形に揃えるため、 jq 2 回呼び
# 出しのまま保つ。
COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -n "$COMMAND" ] || exit 0

# bash `$(...)` の trailing-LF trim で消えた `\<LF>` を復元 (詳細は cmd-parser.sh の
# 「末尾 `\<LF>` 復元の caller 側 inline パターン」 セクション)。
case "$COMMAND" in *\\) COMMAND="${COMMAND}"$'\n' ;; esac

# 行継続 `\<改行>` を **削除** して隣接 token を連結する (bash 実挙動と一致)。 これを
# やらないと `--back\<newline>ground` のような書き方で `--background` flag 検知 (および
# codex review 検知の `review` token) を bypass できる経路が残る。
COMMAND=$(normalize_line_continuations "$COMMAND")

RUN_IN_BG=$(printf '%s' "$INPUT" | jq -r '.tool_input.run_in_background // false')

# codex review 起動でなければ対象外。 検知ロジックは lib/codex-review-detect.sh に集約
# されているため、 auto-mark.sh と drift しない。
is_codex_review_invocation "$COMMAND" || exit 0

deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
}

# Bash tool option `run_in_background: true` 経路
if [ "$RUN_IN_BG" = "true" ]; then
  REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 Bash tool の `run_in_background: true` で `/codex:review` を起動することはできません。

理由: PostToolUse hook (auto-mark.sh) は Bash 呼び出しの完了時点で発火しますが、 `run_in_background: true` だと Bash 呼び出しは codex review の「起動」 だけで完了扱いになり、 review 本体は別 process で継続中です。 この状態で marker を書くと未完了 review に対して marker が立ち、 push gate が素通りして未レビュー commit が remote に到達する経路を作るため、 auto-mark.sh は marker を書きません。 結果として後で `git push` を試みたときに deny され、 review をやり直す必要があります。

対応: Bash tool 呼び出しから `run_in_background: true` を **外して** (デフォルト false で) 再実行してください。 review 完了まで Bash 呼び出し自体が block しますが、 これが本プラグインの想定する正しい使い方です (= `--wait --scope branch` の foreground 実行)。
EOF
)
  deny "$REASON"
  exit 0
fi

# codex companion `--background` フラグ経路
# `--background` / `--background=...` 形式を match させつつ、 `--backgroundX` 等の suffix
# bypass を拒否する (英数字で続く場合は不一致)。
_CODEX_BG_FLAG_RE='--background([^A-Za-z0-9]|$)'
if [[ "$COMMAND" =~ $_CODEX_BG_FLAG_RE ]]; then
  REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 `/codex:review --background` で codex review を起動することはできません。

理由: `--background` だと codex companion 自身が job を queue して即 return します。 Bash tool option `run_in_background: false` で起動しても、 companion 終了時点で review 本体は別 process で継続中です。 この状態で marker を書くと未完了 review に対して marker が立ち、 push gate が素通りする経路になるため、 auto-mark.sh は marker を書きません。 結果として後で `git push` を試みたときに deny され、 review をやり直す必要があります。

対応: `--background` を `--wait` に置き換えて再実行してください。 本プラグインは `--wait --scope branch` のみ marker 対象として扱います。
EOF
)
  deny "$REASON"
  exit 0
fi

exit 0
