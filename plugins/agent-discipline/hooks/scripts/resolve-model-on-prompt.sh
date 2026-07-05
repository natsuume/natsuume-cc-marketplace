#!/bin/bash
# resolve-model-on-prompt.sh
# UserPromptSubmit で発火する one-shot 補正 (#175)。SessionStart 時点でモデル判定不能だった
# session (inject-always.sh が判定不能分岐で作成した pending マーカーが残っている session) に
# 対し、会話が進んで transcript に main-chain assistant 行が現れた最初のタイミングでモデルを
# 確定し、確定版プロンプトを 1 度だけ再送する。
#
# ## 発火条件
#
# pending マーカー `${TMPDIR:-/tmp}/agent-discipline-state/pending-model-<session_id>`
# (session_id は inject-always.sh と同じ sanitize 方式 `tr -cd 'A-Za-z0-9._-'`) が存在する
# session に限る。マーカーが無ければ即 exit 0 (通常時のオーバーヘッドをマーカー存在チェック
# 1 回に抑える)。
#
# ## transcript 解析
#
# pending マーカーが存在する場合のみ、hook input の `.transcript_path` に対し
#   jq -r 'select(.type=="assistant" and .isSidechain != true) | .message.model // empty' \
#     | tail -n 1
# を実行し、最後の main-chain assistant 行のモデル ID を取得する。
#
# ## 分岐
#
# - pending マーカーなし → 即 exit 0
# - pending マーカーあり + transcript にまだ main-chain assistant 行が無い (上記コマンドの
#   結果が空) → 何もしない (pending マーカーは残したまま exit 0。次回 UserPromptSubmit で再試行)
# - pending マーカーあり + assistant 行あり → モデルを確定し:
#   - モデル ID (小文字化) が `fable` を含む → 確定版 (hooks/prompts/always-fable.md) を
#     「以後この確定版を優先し、セッション冒頭の自己ゲート付き注入は破棄する」という前置きと
#     ともに additionalContext で 1 度だけ注入する
#   - それ以外 (sonnet / opus / haiku 等。自己ゲート時に always-sonnet.md を注入済みと同内容) →
#     再注入しない (出力なしで exit 0)
#   - いずれの場合も: state file
#     `${TMPDIR:-/tmp}/agent-discipline-state/model-<session_id>` に確定値を書き込んだ後で
#     pending マーカーを削除する (state file 書込 → pending マーカー削除の順で行い、TOCTOU の
#     隙間を作らない。#155 の教訓)
#
# ## 出力 JSON 形状 (再注入する場合のみ)
#
#   {
#     "hookSpecificOutput": {
#       "hookEventName": "<入力の hook_event_name をそのまま echo>",
#       "additionalContext": "<前置き + always-fable.md 本文>"
#     }
#   }
#
# 再注入しない分岐 (pending マーカーなし / assistant 行なし / 確定版が always-sonnet.md) では
# 何も出力せず exit 0 する。
#
# ## fail-open 条件
#
# - jq 不在
# - stdin が不正 JSON / hook_event_name が空
# - transcript_path が読めない
# - always-fable.md が読めない (空文字列を含む)
# - state file / pending マーカーの読み書き失敗

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

{ read -r HOOK_EVENT; read -r SESSION_ID; read -r TRANSCRIPT_PATH; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.hook_event_name // ""),
    (.session_id // ""),
    (.transcript_path // "")
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
PENDING_FILE="$STATE_DIR/pending-model-$SAFE_SESSION_ID"

# pending マーカーが無ければ即 exit 0 (通常時のオーバーヘッドをこのファイル存在チェック
# 1 回に抑え、transcript 解析はこの後に限定する)。
if [ ! -f "$PENDING_FILE" ]; then
  exit 0
fi

if [ -z "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

MODEL=$(jq -r 'select(.type=="assistant" and .isSidechain != true) | .message.model // empty' "$TRANSCRIPT_PATH" 2>/dev/null | tail -n 1)

# transcript にまだ main-chain assistant 行が無ければ何もしない (pending マーカーは
# 残したまま終了し、次回の UserPromptSubmit で再試行する)。
if [ -z "$MODEL" ]; then
  exit 0
fi

# モデルが確定した。state file 書込 → pending マーカー削除の順で行い、
# TOCTOU の隙間を作らない (#155 の教訓)。
mkdir -p "$STATE_DIR" 2>/dev/null
printf '%s' "$MODEL" > "$STATE_DIR/model-$SAFE_SESSION_ID" 2>/dev/null
rm -f "$PENDING_FILE" 2>/dev/null

# 確定版が always-sonnet.md の場合 (sonnet を含む、または非空でそのいずれでもない場合)、
# 自己ゲート時に注入済みと同内容のため再注入しない。
if ! printf '%s' "$MODEL" | grep -qi 'fable'; then
  exit 0
fi

PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)
BODY=$(cat "$PROMPTS_DIR/always-fable.md" 2>/dev/null)
if [ -z "$BODY" ]; then
  exit 0
fi

PREFIX="(one-shot 補正) セッション開始時点ではモデルを判定できず、自己ゲート付きで SONNET 向けの常時適用ルールを暫定注入していた。会話の進行によりこのセッションのモデルが Fable であると確定したため、以後は本メッセージ以下の確定版を優先し、セッション冒頭の自己ゲート付き注入は破棄すること。"

CONTEXT="$PREFIX

$BODY"

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'
