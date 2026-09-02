#!/bin/bash
# block-bg-codex-wrapper.sh
# codex review wrapper (`run-pre-merge-codex-review.sh`) の起動を検証する PreToolUse フック。
# `pre-merge-codex-review:codex-reviewer` subagent 以外からの起動と、 background / pipeline
# 経由の起動を deny する。
#
# policy: 環境失敗 (jq 不在・command 不在) のみ fail-open で抜け、 それ以外の判定は
#   fail-closed。 wrapper の起動形が判定できない場合は deny 側に倒す。
#   本 hook は merge gate の補助であり、 未レビュー merge を通さない保証そのものは
#   block-pre-merge.sh の PR レビューコメント照合が担う。
#
# ## なぜ必要か
#
# wrapper は codex review を foreground で実行し、 完了時にレビュー記録をローカルに書く。
# これを Bash tool の `run_in_background: true` や shell-level の `&` / `|` で起動すると、
# codex-reviewer subagent が wrapper の stdout / stderr を観察できないまま完了しうるため、
# parent-safe report を正規化できない (レビュー結果が親 session に正しく配送されない)。
# また、 メインセッションが wrapper を直接 Bash 実行すると、 subagent が持つ context
# isolation (詳細出力を subagent context に閉じ込め、 親には report だけを返す設計) が
# 毀損される。
#
# ## 検知ロジック
#
# 1. **関与条件**: Bash コマンド文字列が wrapper の basename (`run-pre-merge-codex-review.sh`)
#    を含む場合のみ関与する。 判定は粗い substring 検出で、 実行形と単なる言及
#    (`cat <wrapper>` のような read-only な参照) を区別しない。 誤爆した場合は wrapper path
#    を含まない形にコマンドを言い換えて回避する (cooperative 利用前提)。
#    wrapper の basename を `pre-push-codex-review` の wrapper と別名にしているのは、 両
#    plugin が併存する環境で互いの basename ベース検出が相手の wrapper 起動を deny し合う
#    干渉を避けるため。
# 2. **agent_type gate**: hook payload のトップレベル `agent_type` が
#    `pre-merge-codex-review:codex-reviewer` に完全一致しなければ deny する (欠落・別値
#    いずれも deny)。 メインセッションの Bash では `agent_type` がペイロードに含まれない。
# 3. **background / pipeline**: Bash tool option `tool_input.run_in_background` が true の
#    場合、 またはコマンドが単独の `&` (background) / `|` (pipeline) を含む場合は deny する
#    (`&&` / `||` は逐次実行なので許容)。

WRAPPER_BASENAME="run-pre-merge-codex-review.sh"
REVIEWER_AGENT_TYPE="pre-merge-codex-review:codex-reviewer"

INPUT=$(cat)

# jq 不在は環境失敗として fail-open (合法な wrapper 起動を環境要因で deny しない)。
if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -n "$COMMAND" ] || exit 0

# 行継続 `\<改行>` を **削除** して隣接 token を連結する (bash 実挙動と一致)。 これをやらないと
# basename の途中で改行を挟む書き方で substring 検出を素通りできる。
COMMAND="${COMMAND//\\$'\n'/}"

case "$COMMAND" in
  *"$WRAPPER_BASENAME"*) ;;
  *) exit 0 ;;
esac

deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
}

AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.agent_type // empty' 2>/dev/null) || AGENT_TYPE=""
if [ "$AGENT_TYPE" != "$REVIEWER_AGENT_TYPE" ]; then
  deny "codex review wrapper の起動をブロックしました。 wrapper (\`${WRAPPER_BASENAME}\`) は \`${REVIEWER_AGENT_TYPE}\` subagent からのみ起動できます。

Agent / Task tool で subagent_type=\"${REVIEWER_AGENT_TYPE}\", model=\"sonnet\" を foreground 起動してください。 subagent が wrapper を実行し、 結果を parent-safe な markdown report として返します。

wrapper の出力 (codex review の生の findings) を親 session の context に直接流し込まないための境界です。"
  exit 0
fi

RUN_IN_BACKGROUND=$(printf '%s' "$INPUT" | jq -r '.tool_input.run_in_background // false' 2>/dev/null) || RUN_IN_BACKGROUND="false"
if [ "$RUN_IN_BACKGROUND" = "true" ]; then
  deny "codex review wrapper の background 起動をブロックしました。 wrapper は foreground (\`run_in_background: false\`) で 1 回だけ起動してください。

background 起動では wrapper の stdout / stderr を subagent が観察できないまま Agent が完了しうるため、 codex review の結果を parent-safe report として正規化できません。"
  exit 0
fi

# `&` を含む shell redirection (`2>&1` / `&>file` 等) を空白に置換してから separator を
# 判定する (redirection 内の `&` を background separator と誤認しないため)。 置換対象を
# filename-safe な文字だけに限る positive-list を取り、 想定外の shape は残して後段の
# separator 判定で deny 側に倒す。
STRIPPED=$(printf '%s' "$COMMAND" \
  | sed -E 's/[0-9]?(&>>|&>|>>|>\&|<\&|<<<|<<|<>)[[:space:]]*[A-Za-z0-9_./=+@:-]*/ /g')

# has_parallel_separator <command>
# 戻り値: 0 = 単独の `&` (background) または `|` (pipeline) を含む、 1 = 含まない。
# `&&` / `||` は逐次実行なので separator として数えない。 quote 状態は追跡しない粗い判定で、
# quoted な `|` / `&` の言及も検出する (誤爆時はコマンドを言い換えて回避する前提)。
has_parallel_separator() {
  local s="$1"
  local i=0 len=${#s}
  local c nc
  while [ "$i" -lt "$len" ]; do
    c="${s:$i:1}"
    nc="${s:$((i+1)):1}"
    case "$c" in
      '|')
        if [ "$nc" = '|' ]; then i=$((i+2)); continue; fi
        return 0
        ;;
      '&')
        if [ "$nc" = '&' ]; then i=$((i+2)); continue; fi
        return 0
        ;;
    esac
    i=$((i+1))
  done
  return 1
}

if has_parallel_separator "$STRIPPED"; then
  deny "codex review wrapper の起動をブロックしました。 単独の \`&\` (background) や \`|\` (pipeline) で wrapper を連結する形式はサポート外です。

これらの区切りでは bash が両側を同時に起動するため、 subagent が wrapper の完了出力を観察する前に Agent が完了しうる経路になります。

wrapper は単独の foreground コマンドとして起動してください (出力を記録したい場合は file redirection \`> codex.log 2>&1\` を使うか、 完了後に別の Bash 呼び出しでファイルを処理してください)。"
  exit 0
fi

exit 0
