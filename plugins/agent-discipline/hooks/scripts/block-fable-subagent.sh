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
#      b. env 不在: inject-always.sh が SessionStart で記録した session model state が
#         fable の場合のみ deny
#      c. env 不在 + state 不明 (#200 で実装済み): pending マーカー
#         `${TMPDIR:-/tmp}/agent-discipline-state/pending-model-<session_id>` が存在する場合は
#         deny する。判定不能セッションの実体が Fable のとき、未指定継承は継承先が Fable に
#         なり、この時点では state も未確定のため他の防御が効かない (PR #199 codex P2)。
#         deny メッセージには「モデル確定 (one-shot 補正) までは model に非 Fable (sonnet 等)
#         を明示して再実行する」自己修復誘導を含める。明示非 Fable 指定は Step 2 で
#         allow 済みのため、pending 中でも明示指定の委任は妨げない
#      d. env 不在 + state 不明 + pending マーカーも無し → allow (真の情報ゼロは従来どおり
#         fail-open を維持)
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
#   - セッション途中の /model 切替は検知できない (model を含む hook 入力は SessionStart のみで、
#     $CLAUDE_MODEL 環境変数も存在しない)。env 不在時は state file が次の SessionStart まで
#     stale になり、fable への切替は素通り (Step 3b が旧 state で allow)、fable からの切替は
#     誤 deny になる (deny メッセージの model 明示誘導で自己修復可能)。README の既知の制約参照
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
  deny "agent-discipline: CLAUDE_CODE_SUBAGENT_MODEL が fable を指しており、model の明示指定より優先されて全サブエージェントが Fable で実行されます。この env はセッションを超える設定のため独断で書き換えず、この状態をユーザに報告して、settings.json 等の env 設定を sonnet / opus へ修正するよう依頼してください。"
fi

# 1. fable の明示指定は deny
if printf '%s' "$TOOL_MODEL" | grep -qi 'fable'; then
  deny "agent-discipline: サブエージェントに Fable を指定しないでください。model に sonnet / opus (機械的作業なら haiku) を明示して再実行してください。CLAUDE_CODE_SUBAGENT_MODEL が設定された環境では、ここで指定した値もどのみち env 値に上書きされ、意図したモデルでは実行されません。"
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

STATE_FILE="${TMPDIR:-/tmp}/agent-discipline-state/model-$SAFE_SESSION_ID"
if [ ! -r "$STATE_FILE" ]; then
  # 3c/3d (#200 で実装済み): state 不明。pending マーカーが存在する場合、このセッションは
  # モデル判定不能期間中であり、実体が Fable なら未指定継承の継承先が Fable になる
  # (PR #199 codex P2)。pending マーカーも無い真の情報ゼロの場合のみ従来どおり fail-open。
  PENDING_MARKER="${TMPDIR:-/tmp}/agent-discipline-state/pending-model-$SAFE_SESSION_ID"
  if [ -e "$PENDING_MARKER" ]; then
    deny "agent-discipline: このセッションはモデル判定不能期間 (pending) のため、model 未指定 (継承) のサブエージェント起動を一時的に deny しています。継承先が Fable になる可能性があり、この期間は state が未確定で検知できません。model に非 Fable モデル (例: sonnet) を明示して再実行するか、会話を 1 turn 進めて one-shot 補正でモデルが確定するのを待ってから再実行してください。"
  fi
  exit 0
fi

SESSION_MODEL=$(cat "$STATE_FILE" 2>/dev/null)
if printf '%s' "$SESSION_MODEL" | grep -qi 'fable'; then
  deny "agent-discipline: model 未指定のサブエージェントはメインセッション (Fable) のモデルを継承します。model に sonnet / opus (機械的作業なら haiku) を明示して再実行してください。この deny が出た時点で CLAUDE_CODE_SUBAGENT_MODEL は未設定 (または inherit) のため、主防御である env の設定 (sonnet 等) をユーザに提案するのも有効です。"
fi

exit 0
