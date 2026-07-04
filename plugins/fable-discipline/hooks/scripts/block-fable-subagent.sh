#!/bin/bash
# block-fable-subagent.sh
# PreToolUse (matcher: Agent|Task) で、サブエージェントが Fable で実行される経路を deny する防波堤。
#
# 判定 (すべて deterministic な文字列判定。LLM 評価は使わない。判定不能時は fail-open = allow):
#   1. tool_input.model に fable が明示指定されている → 無条件 deny
#      (CLAUDE_CODE_SUBAGENT_MODEL が設定済みの環境でも、fable 指定は「意図したモデル」と
#      「実際に走るモデル」が乖離するため止めて明示し直させる)
#   2. tool_input.model が非 fable の明示指定 → allow
#   3. tool_input.model 未指定 (= メインセッションのモデルを継承する経路):
#      a. CLAUDE_CODE_SUBAGENT_MODEL が非 fable 値に設定済み → allow
#         (env は明示指定・frontmatter より優先して継承を上書きするため安全)
#      b. それ以外 (env 未設定 / "inherit"): inject-fable-role.sh が SessionStart で記録した
#         session model state が fable の場合のみ deny。state 不明なら allow (fail-open)
#
# fork subagent (model 指定を無視して親モデルを継承する型) は本 hook では deny しない
# (誘導層の「原則使用しない」文言のみで運用する設計判断)。
#
# 主防御はあくまで CLAUDE_CODE_SUBAGENT_MODEL env 設定 (Agent の明示指定・agent frontmatter・
# Workflow 内部の agent() すべてより優先されることを実測検証済み)。本 hook は env 設定が
# 外れた場合の defense-in-depth + deny メッセージによる自己修正誘導が役割。
# Workflow ツール内部の agent() 呼び出しは PreToolUse では捕捉できない (既知の制約、env 側でカバー)。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

{ read -r HOOK_EVENT; read -r TOOL_MODEL; read -r SESSION_ID; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.hook_event_name // ""),
    (.tool_input.model // ""),
    (.session_id // "")
  ' 2>/dev/null
)

# PreToolUse 以外 (入力不正含む) では何もしない。deny JSON の hookEventName は
# PreToolUse 固定で返すため、イベントが確認できない入力には応答しない。
if [ "$HOOK_EVENT" != "PreToolUse" ]; then
  exit 0
fi

deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
}

# 1. fable の明示指定は無条件 deny
if printf '%s' "$TOOL_MODEL" | grep -qi 'fable'; then
  deny "fable-discipline: サブエージェントに Fable を指定しないでください。model に sonnet / opus (機械的作業なら haiku) を明示して再実行してください。CLAUDE_CODE_SUBAGENT_MODEL が設定された環境では fable 指定はどのみち env 値に上書きされ、意図したモデルでは実行されません。"
fi

# 2. 非 fable の明示指定は allow
if [ -n "$TOOL_MODEL" ]; then
  exit 0
fi

# 3. model 未指定 = メインセッション継承経路
ENV_SUB="${CLAUDE_CODE_SUBAGENT_MODEL:-}"
if [ -n "$ENV_SUB" ] && [ "$ENV_SUB" != "inherit" ]; then
  if ! printf '%s' "$ENV_SUB" | grep -qi 'fable'; then
    # env が非 fable モデルへ強制するため継承は発生しない
    exit 0
  fi
fi

SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9._-')
if [ -z "$SAFE_SESSION_ID" ]; then
  exit 0
fi

STATE_FILE="${TMPDIR:-/tmp}/fable-discipline-state/model-$SAFE_SESSION_ID"
if [ ! -r "$STATE_FILE" ]; then
  exit 0
fi

SESSION_MODEL=$(cat "$STATE_FILE" 2>/dev/null)
if printf '%s' "$SESSION_MODEL" | grep -qi 'fable'; then
  deny "fable-discipline: model 未指定のサブエージェントはメインセッション (Fable) のモデルを継承します。model に sonnet / opus を明示して再実行してください。"
fi

exit 0
