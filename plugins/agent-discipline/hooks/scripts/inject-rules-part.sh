#!/bin/bash
# inject-rules-part.sh
# UserPromptSubmit で発火し、常時適用ルールの part 2/3 または part 3/3
# (hooks/prompts/always-2.md / always-3.md) を additionalContext として配送する。
# セッションのモデルは判定せず、全モデルへ同じ内容を配送する。
#
# ## 背景
#
# inject-always.sh は SessionStart で part 1/3 (delivery-note + always-1.md) のみを注入する。
# 残りの part は 1 メッセージに収めると 8K 閾値を超えるため、本スクリプトが
# UserPromptSubmit の最初のプロンプト処理時に個別要素として配送する (at-most-once)。
#
# ## 呼び出し方
#
# 引数 1 個 (part 番号): `inject-rules-part.sh 2` / `inject-rules-part.sh 3` として
# hooks.json の UserPromptSubmit に 2 entries 登録する。引数が `2` / `3` 以外、または欠落の
# 場合は無音 exit 0 (誤登録時のフェイルセーフ)。
#
# ## 配送済みマーカー
#
# マーカー `${TMPDIR:-/tmp}/agent-discipline-state/delivered-rules-<n>-<session_id>` が
# 存在すれば即 exit 0 (毎プロンプトのオーバーヘッドをファイル存在チェック 1 回に抑える)。
# マーカー不在時は always-<n>.md の本文 (先頭の保守者向け HTML コメントと直後の空行を除く。
# lib/prompt-body.sh) を配送し、マーカーを書く。マーカーは
# inject-always.sh が SessionStart のたびに削除する。
#
# マーカーの書き込みは注入本文と出力 JSON の生成に成功した後に行う (先にマーカーを書くと、
# 本文読取失敗時に当該要素が session 中永久欠落する)。マーカー自体の書込も同一ディレクトリ内
# temp file → mv の atomic 書込にする。
#
# ## 出力 JSON 形状 (配送する場合のみ)
#
#   {
#     "hookSpecificOutput": {
#       "hookEventName": "<入力の hook_event_name をそのまま echo>",
#       "additionalContext": "<always-<n>.md の本文>"
#     }
#   }
#
# ## fail-open 条件
#
# - jq 不在 / 不正 stdin / hook_event_name・session_id が空
# - always-<n>.md が読めない (空文字列を含む)
# - 上記いずれも「無音 exit 0、マーカーは書かない」= 次プロンプトで再試行する

PART="$1"
case "$PART" in
  2|3) ;;
  *)
    exit 0
    ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

{ read -r HOOK_EVENT; read -r SESSION_ID; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.hook_event_name // ""),
    (.session_id // "")
  ' 2>/dev/null
)

if [ -z "$HOOK_EVENT" ] || [ -z "$SESSION_ID" ]; then
  exit 0
fi

SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9._-')
if [ -z "$SAFE_SESSION_ID" ]; then
  exit 0
fi

STATE_DIR="${TMPDIR:-/tmp}/agent-discipline-state"
MARKER="$STATE_DIR/delivered-rules-$PART-$SAFE_SESSION_ID"

if [ -f "$MARKER" ]; then
  exit 0
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
if [ -z "$SCRIPT_DIR" ] || [ ! -r "$SCRIPT_DIR/lib/prompt-body.sh" ]; then
  exit 0
fi
# shellcheck source=plugins/agent-discipline/hooks/scripts/lib/prompt-body.sh
source "$SCRIPT_DIR/lib/prompt-body.sh" || exit 0

PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)
CONTEXT=$(read_agent_discipline_prompt "$PROMPTS_DIR/always-$PART.md")
if [ -z "$CONTEXT" ]; then
  exit 0
fi

OUTPUT=$(jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}')
if [ -z "$OUTPUT" ]; then
  exit 0
fi
printf '%s\n' "$OUTPUT"

# 注入本文と出力 JSON の生成に成功した後にのみマーカーを atomic に書く。書込失敗は無視する
# (次プロンプトで再試行)。
if mkdir -p "$STATE_DIR" 2>/dev/null; then
  TMP_MARKER="$MARKER.tmp.$$"
  # 2>/dev/null は「>」より前に置く (bash の出力リダイレクト失敗は後置の 2>/dev/null では
  # 抑制できないため、無音 fail-open のため先に stderr を /dev/null へ向ける)。
  if : 2>/dev/null > "$TMP_MARKER"; then
    mv "$TMP_MARKER" "$MARKER" 2>/dev/null || rm -f "$TMP_MARKER" 2>/dev/null
  fi
fi

exit 0
