#!/bin/bash
# inject-fable-role.sh
# SessionStart で Fable セッション向けの分業規律 (誘導層) を additionalContext として注入する。
# 注入テキスト本体は hooks/prompts/ の md ファイル (preamble-fable.md / preamble-self-gate.md /
# discipline-body.md) に定義し、本スクリプトは判定と組み立てのみを担う。
#
# 注入判定 (ハイブリッド方式):
#   - stdin の model フィールドが fable → 無条件文を注入
#   - model フィールドが取得できない (/clear 直後や会話復元時に欠落しうる、公式仕様) →
#     自己ゲート文付きで注入 (モデルは自身の system prompt で自分が Fable か判別できるため、
#     ゲートは受信側で確実に機能する)
#   - model フィールドが fable 以外 → 何もしない (非 Fable セッションを汚さない)
#
# 併せて、判定できた model 値を session_id キーの state file に書き出す
# (block-fable-subagent.sh が「model 未指定 = メインセッション継承」経路の判定に使う)。
#
# jq が無い環境では何もしない (fail-open)。SessionStart は startup / resume / clear / compact
# の全 source で発火するため、長いセッションでも compact 後に規律が再注入される。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# hook_event_name / model / session_id を 1 回の jq 呼び出しで取得する。
# INPUT が不正な JSON の場合の parse error は 2>/dev/null で抑制し、
# HOOK_EVENT 空判定でフォールバックさせる (hook の stderr は利用者に見えるため)。
{ read -r HOOK_EVENT; read -r MODEL; read -r SESSION_ID; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.hook_event_name // ""),
    (.model // ""),
    (.session_id // "")
  ' 2>/dev/null
)

# hook_event_name が取れなければイベント名を正しくエコーできないので無音終了する
# (誤った既定値で hookSpecificOutput.hookEventName を返すと別 event の文脈に誘導する恐れがある)。
if [ -z "$HOOK_EVENT" ]; then
  exit 0
fi

# state file: block-fable-subagent.sh との共有。session_id はパス文字を除去してから使う。
# 書き込み失敗は無視する (state はあくまで防波堤 hook の補助情報で、無ければ fail-open)。
if [ -n "$MODEL" ] && [ -n "$SESSION_ID" ]; then
  SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9._-')
  if [ -n "$SAFE_SESSION_ID" ]; then
    STATE_DIR="${TMPDIR:-/tmp}/fable-discipline-state"
    if mkdir -p "$STATE_DIR" 2>/dev/null; then
      printf '%s' "$MODEL" > "$STATE_DIR/model-$SAFE_SESSION_ID" 2>/dev/null
    fi
  fi
fi

IS_FABLE=0
if printf '%s' "$MODEL" | grep -qi 'fable'; then
  IS_FABLE=1
fi

# 非 Fable と判定できたセッションには注入しない
if [ -n "$MODEL" ] && [ "$IS_FABLE" -eq 0 ]; then
  exit 0
fi

# 注入テキストは hooks/prompts/ に定義する (プロンプトを sh に直接埋め込むと視認性・
# メンテナンス性が下がるため分離): preamble-fable.md = 無条件適用の前置き /
# preamble-self-gate.md = 自己ゲート文 / discipline-body.md = 分業規律の本文 (セクション 1〜4)。
# 読めない場合は fail-open で無音終了する (壊れた・欠けた注入で誤誘導するより注入しない方が安全)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)
if [ "$IS_FABLE" -eq 1 ]; then
  PREAMBLE=$(cat "$PROMPTS_DIR/preamble-fable.md" 2>/dev/null)
else
  PREAMBLE=$(cat "$PROMPTS_DIR/preamble-self-gate.md" 2>/dev/null)
fi

BODY=$(cat "$PROMPTS_DIR/discipline-body.md" 2>/dev/null)
if [ -z "$PREAMBLE" ] || [ -z "$BODY" ]; then
  exit 0
fi

CONTEXT="# fable-discipline: Fable セッションの分業規律

$PREAMBLE

$BODY"

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'
