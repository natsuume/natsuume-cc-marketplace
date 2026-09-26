#!/bin/bash
# inject-discipline.sh
# UserPromptSubmit で発火し、分業規律 (hooks/prompts/discipline.md) を additionalContext
# として配送する。常時ルールと分業規律を同一 additionalContext に連結すると合計文字数が
# inline 配送閾値を超えるため、本スクリプトが独立した要素として UserPromptSubmit 側で配送する。
# セッションのモデルは判定せず、全モデルへ同じ内容を配送する。
#
# ## 配送済みマーカー
#
# マーカー `${TMPDIR:-/tmp}/agent-discipline-state/delivered-discipline-<session_id>` が
# 存在すれば (内容は問わない) 即 exit 0。マーカー不在時は見出し
# `# agent-discipline: 分業規律` + 空行 + discipline.md の本文 (先頭の保守者向け HTML コメントと
# 直後の空行を除く。lib/prompt-body.sh) を配送し、マーカー (内容
# `delivered`) を書く。マーカーは inject-always.sh が SessionStart のたびに削除する。
#
# マーカーの書き込みは注入本文と出力 JSON の生成に成功した後に行う (先にマーカーを書くと、
# 本文読取失敗時に分業規律が session 中永久欠落する)。マーカー自体の書込も同一ディレクトリ内
# temp file → mv の atomic 書込にする。
#
# ## 出力 JSON 形状 (配送する場合のみ)
#
#   {
#     "hookSpecificOutput": {
#       "hookEventName": "<入力の hook_event_name をそのまま echo>",
#       "additionalContext": "# agent-discipline: 分業規律\n\n<discipline.md の本文>"
#     }
#   }
#
# ## fail-open 条件
#
# - jq 不在 / 不正 stdin / hook_event_name・session_id が空
# - discipline.md が読めない (空文字列を含む)
# - 上記いずれも「無音 exit 0、マーカーは書かない」= 次プロンプトで再試行する

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
MARKER="$STATE_DIR/delivered-discipline-$SAFE_SESSION_ID"

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
BODY=$(read_agent_discipline_prompt "$PROMPTS_DIR/discipline.md")
if [ -z "$BODY" ]; then
  exit 0
fi

CONTEXT="# agent-discipline: 分業規律

$BODY"

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
  if printf 'delivered' 2>/dev/null > "$TMP_MARKER"; then
    mv "$TMP_MARKER" "$MARKER" 2>/dev/null || rm -f "$TMP_MARKER" 2>/dev/null
  else
    rm -f "$TMP_MARKER" 2>/dev/null
  fi
fi

exit 0
