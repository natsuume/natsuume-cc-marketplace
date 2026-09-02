#!/bin/bash
# inject-rules-part.sh
# UserPromptSubmit で発火し、常時適用ルール (Sonnet 版) の part 2/3 または part 3/3
# (hooks/prompts/always-sonnet-2.md / always-sonnet-3.md) を additionalContext として
# 配送する (issue #236、注入ペイロード分割の設計契約 §4.2)。
#
# ## 背景
#
# inject-always.sh は SessionStart で part 1/3 (delivery-note + always-sonnet-1.md 等) のみを
# 注入する。残りの part は 1 メッセージに収めると 8K 閾値を超えるため、本スクリプトが
# UserPromptSubmit の最初のプロンプト処理時に個別要素として配送する (at-most-once)。
#
# ## 呼び出し方
#
# 引数 1 個 (part 番号): `inject-rules-part.sh 2` / `inject-rules-part.sh 3` として
# hooks.json の UserPromptSubmit に 2 entries 登録する。引数が `2` / `3` 以外、または欠落の
# 場合は無音 exit 0 (誤登録時のフェイルセーフ)。
#
# ## マーカーと優先規則
#
# マーカー `delivered-rules-<n>-<session_id>` が存在すれば即 exit 0 (毎プロンプトの
# オーバーヘッドをファイル存在チェック 1 回に抑える)。マーカー不在時は以下の優先規則で分岐する
# (優先規則の詳細は設計契約 §5: pending が存在する間は state を信頼しない):
#
# - pending (`pending-model-<session_id>`) あり → 自己ゲート行 (part-self-gate.md) +
#   always-sonnet-<n>.md を注入し、マーカーを書く (state の有無・内容は見ない)
# - pending 無し + state (`model-<session_id>`) が fable を含む → 配送不要
#   (fable は part 1 = always-fable.md 全文で完結)。マーカーのみ書く
# - pending 無し + state が非 fable → always-sonnet-<n>.md を注入し、マーカーを書く
# - pending も state も無い (SessionStart hook が失敗した異常系) → 判定不能と同じ自己ゲート
#   付き配送にフォールバックし、マーカーを書く (規律が届かないまま session が進む方が危険、
#   という保守側の倒し方)
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
#       "additionalContext": "<(自己ゲート付きの場合) part-self-gate.md + always-sonnet-<n>.md>"
#     }
#   }
#
# ## fail-open 条件
#
# - jq 不在 / 不正 stdin / hook_event_name・session_id が空
# - always-sonnet-<n>.md が読めない (空文字列を含む)
# - 自己ゲート付き配送時に part-self-gate.md が読めない (空文字列を含む)
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

PENDING_FILE="$STATE_DIR/pending-model-$SAFE_SESSION_ID"
STATE_FILE="$STATE_DIR/model-$SAFE_SESSION_ID"

PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)
RULES_BODY=$(cat "$PROMPTS_DIR/always-sonnet-$PART.md" 2>/dev/null)
if [ -z "$RULES_BODY" ]; then
  exit 0
fi

# 優先規則 (設計契約 §5): pending が存在する間は state を信頼しない。
USE_SELF_GATE=0
DELIVER=1

if [ -f "$PENDING_FILE" ]; then
  USE_SELF_GATE=1
elif [ -f "$STATE_FILE" ]; then
  MODEL=$(cat "$STATE_FILE" 2>/dev/null)
  if printf '%s' "$MODEL" | grep -qi 'fable'; then
    DELIVER=0
  fi
else
  # pending も state も無い異常系: 判定不能と同じ自己ゲート付き配送にフォールバックする。
  USE_SELF_GATE=1
fi

if [ "$DELIVER" -eq 1 ]; then
  if [ "$USE_SELF_GATE" -eq 1 ]; then
    GATE=$(cat "$PROMPTS_DIR/part-self-gate.md" 2>/dev/null)
    if [ -z "$GATE" ]; then
      exit 0
    fi
    CONTEXT="$GATE

$RULES_BODY"
  else
    CONTEXT="$RULES_BODY"
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
fi

# 注入本文と出力 JSON の生成に成功した後 (または fable で配送不要と判定した後) にのみ
# マーカーを atomic に書く。書込失敗は無視する (次プロンプトで再試行)。
if mkdir -p "$STATE_DIR" 2>/dev/null; then
  TMP_MARKER="$MARKER.tmp.$$"
  # 2>/dev/null は「>」より前に置く (bash の出力リダイレクト失敗は後置の 2>/dev/null では
  # 抑制できないため、無音 fail-open のため先に stderr を /dev/null へ向ける)。
  if : 2>/dev/null > "$TMP_MARKER"; then
    mv "$TMP_MARKER" "$MARKER" 2>/dev/null || rm -f "$TMP_MARKER" 2>/dev/null
  fi
fi

exit 0
