#!/bin/bash
# block-fable-subagent.sh
# PreToolUse (matcher: Agent|Task) で、サブエージェントが Fable で実行される経路を deny する防波堤。
#
# 判定順序は Claude Code のモデル解決順序 (CLAUDE_CODE_SUBAGENT_MODEL env > tool_input.model
# 明示指定 > agent frontmatter > メインセッション継承) と一致させる。すべて deterministic な
# 文字列判定で LLM 評価は使わない。判定不能時は fail-open = allow。
#
#   0. env が fable を指す → tool_input.model の値に依らず無条件 deny
#      (env は明示指定より優先されるため、明示 sonnet/opus でも実行モデルは fable になる)
#   1. tool_input.model に fable が明示指定されている → deny
#   2. tool_input.model が非 fable の具体指定 → allow (Step 0 より env は非 fable 確定)
#   3. tool_input.model 未指定 (= 継承経路):
#      a. env が非空 → allow (env が継承を非 fable モデルへ上書きするため安全)
#      b. env 不在: inject-fable-role.sh が SessionStart で記録した session model state が
#         fable の場合のみ deny。state 不明なら allow (fail-open)
#
# 正規化ポリシー:
#   - env / tool_input.model とも前後空白を trim し、"inherit" (case-insensitive) は
#     「未指定」に正規化する (inherit は継承の別表記であり具体的なモデル選択ではないため)
#   - 非空・非 inherit の env 値は、fable を含まない限り authoritative な非 fable 値として
#     信頼する (env の妥当性検証は Claude Code 本体と利用者の責務で、hook の確信境界の外)
#
# 既知の制約:
#   - agent 定義 frontmatter の model は tool_input に現れないため判定できない。env 不在 +
#     model 未指定 + frontmatter が fable を指す構成は本 hook では捕捉不能 (env 側でカバー)
#   - fork subagent (model 指定を無視して親モデルを継承する型) は deny しない
#     (誘導層の「原則使用しない」文言のみで運用する設計判断)
#   - Workflow ツール内部の agent() 呼び出しは PreToolUse では捕捉できない (env 側でカバー)
#
# 主防御はあくまで CLAUDE_CODE_SUBAGENT_MODEL env 設定 (Agent の明示指定・agent frontmatter・
# Workflow 内部の agent() すべてより優先されることを実測検証済み)。本 hook は env 設定が
# 誤っている / 外れた場合の defense-in-depth + deny メッセージによる自己修正誘導が役割。

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

trim() {
  printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

TOOL_MODEL=$(trim "$TOOL_MODEL")
ENV_SUB=$(trim "${CLAUDE_CODE_SUBAGENT_MODEL:-}")

# "inherit" (case-insensitive) は「未指定 = 継承」の別表記として正規化する
if printf '%s' "$TOOL_MODEL" | grep -qix 'inherit'; then
  TOOL_MODEL=""
fi
if printf '%s' "$ENV_SUB" | grep -qix 'inherit'; then
  ENV_SUB=""
fi

# 0. env が fable を強制していれば、モデル解決の最上位で fable が確定するため無条件 deny
if [ -n "$ENV_SUB" ] && printf '%s' "$ENV_SUB" | grep -qi 'fable'; then
  deny "fable-discipline: CLAUDE_CODE_SUBAGENT_MODEL が fable を指しており、model の明示指定より優先されて全サブエージェントが Fable で実行されます。settings.json 等の env 設定自体を sonnet / opus に修正してください。"
fi

# 1. fable の明示指定は deny
if printf '%s' "$TOOL_MODEL" | grep -qi 'fable'; then
  deny "fable-discipline: サブエージェントに Fable を指定しないでください。model に sonnet / opus (機械的作業なら haiku) を明示して再実行してください。CLAUDE_CODE_SUBAGENT_MODEL が設定された環境では fable 指定はどのみち env 値に上書きされ、意図したモデルでは実行されません。"
fi

# 2. 非 fable の具体指定は allow (Step 0 より env は非 fable 確定なので上書きされても安全)
if [ -n "$TOOL_MODEL" ]; then
  exit 0
fi

# 3. model 未指定 = メインセッション継承経路
if [ -n "$ENV_SUB" ]; then
  # 非空・非 inherit の env は authoritative な非 fable 値として信頼する (正規化ポリシー参照)
  exit 0
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
