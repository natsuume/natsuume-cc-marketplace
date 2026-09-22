#!/bin/bash
# block-fable-subagent.sh
# PreToolUse (matcher: Agent|Task) で、サブエージェントが Fable で実行される経路を deny する
# 二重防御の hook。主防御は利用者の settings に置く permission rule
# (`permissions.deny` の `Agent(model:fable)` / `Agent(fork)`) であり、本 hook は permission
# rule が捕捉しない経路 (full model ID の明示、agent 定義 frontmatter、メインセッション継承、
# env による上書き) の検知と、deny メッセージによる自己修正誘導を担う。すべて deterministic な
# 文字列判定で LLM 評価は使わない。判定不能時は fail-open = allow。
#
# Claude Code のモデル解決順序:
#   明示 model > agent 定義の frontmatter > CLAUDE_CODE_SUBAGENT_MODEL > メインセッション継承。
#   CLAUDE_CODE_SUBAGENT_MODEL_FORCE (1 / true、大文字小文字は区別しない) が設定されている
#   場合のみ、env (未設定ならメインセッションのモデル) がこの順序の全てを上書きする。
#   subagent_type が fork のサブエージェントは、model 指定にも env にも依らずメインセッションの
#   モデルを継承する。
#
# 判定順序 (上から評価し、最初に該当した結果を返す):
#   1. fork (subagent_type が fork) → メインセッションのモデルで判定する (継承経路と同じ扱い)
#   2. CLAUDE_CODE_SUBAGENT_MODEL_FORCE が有効 → 実効モデルは env (非空ならその値、空なら
#      メインセッションのモデル)。fable なら model の明示に依らず deny し、model の明示では
#      直せないことを deny 理由に書く。非 fable なら model が fable でも allow
#   3. tool_input.model に fable が明示指定されている → deny
#   4. tool_input.model が非 fable の具体指定 → allow (明示は env より優先されるため)
#   5. tool_input.model 未指定 (= メインセッション継承経路):
#      - env が非空: fable なら deny、それ以外は allow
#      - env 不在: session model state (`${TMPDIR:-/tmp}/agent-discipline-state/model-<session_id>`、
#        inject-always.sh が SessionStart で記録し update-model-on-switch.sh が /model 切替で
#        更新する) が fable の場合のみ deny
#      - env 不在 + state 不明: pending マーカー
#        (`${TMPDIR:-/tmp}/agent-discipline-state/pending-model-<session_id>`) が存在すれば deny
#        する。モデル判定不能期間は継承先が Fable でも state から検知できないため。マーカーも
#        無い真の情報ゼロの場合は fail-open (allow)
#
# 正規化ポリシー:
#   - env / tool_input.model とも前後空白を trim し、"inherit" (case-insensitive) は
#     「未指定」に正規化する (inherit は継承の別表記であり具体的なモデル選択ではないため)
#   - 非空・非 inherit の env 値は、fable を含まない限り authoritative な非 fable 値として
#     信頼する (env の妥当性検証は Claude Code 本体と利用者の責務で、hook の確信境界の外)
#
# 既知の制約:
#   - agent 定義 frontmatter の model は tool_input に現れないため判定できない。model 未指定 +
#     frontmatter が fable を指す構成は、FORCE 併用時を除いて本 hook では捕捉不能
#   - Workflow ツール内部の agent() 呼び出しは PreToolUse では捕捉できない
#   - jq 不在時は何もせず exit 0 (jq は plugin 全体の前提であり、本 hook 単独では fail-closed に
#     しない)。hook_event_name が PreToolUse 以外の入力にも応答しない

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# 各フィールドは 1 行 1 値で読むため、値に含まれる改行 (CR / LF) は空白に置き換えて欄ずれを防ぐ
# (subagent_type に改行を含めても、後続の session_id 等が別の欄に読み込まれない)。
{ read -r HOOK_EVENT; read -r TOOL_MODEL; read -r SUBAGENT_TYPE; read -r SESSION_ID; } < <(
  printf '%s' "$INPUT" | jq -r '
    [ (.hook_event_name // ""),
      (.tool_input.model // ""),
      (.tool_input.subagent_type // ""),
      (.session_id // "") ]
    | map(tostring | gsub("[\r\n]"; " "))
    | .[]
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

# モデル名が Fable を指すか (alias `fable` / full ID `claude-fable-5-1` の双方を含む部分一致)。
is_fable() {
  [ -n "$1" ] && printf '%s' "$1" | grep -qi 'fable'
}

TOOL_MODEL=$(trim "$TOOL_MODEL")
SUBAGENT_TYPE=$(trim "$SUBAGENT_TYPE")
ENV_SUB=$(trim "${CLAUDE_CODE_SUBAGENT_MODEL:-}")
FORCE_RAW=$(trim "${CLAUDE_CODE_SUBAGENT_MODEL_FORCE:-}")

# "inherit" (case-insensitive) は「未指定 = 継承」の別表記として正規化する
if printf '%s' "$TOOL_MODEL" | grep -qix 'inherit'; then
  TOOL_MODEL=""
fi
if printf '%s' "$ENV_SUB" | grep -qix 'inherit'; then
  ENV_SUB=""
fi

# FORCE の真値は 1 / true のみ (大文字小文字は区別しない)。それ以外の値・空・未設定は無効。
FORCE_ENABLED=0
case "$(printf '%s' "$FORCE_RAW" | tr '[:upper:]' '[:lower:]')" in
  1|true) FORCE_ENABLED=1 ;;
esac

# session model state と pending マーカーを読む (メインセッションのモデル判定に使う)。
SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9._-')
STATE_DIR="${TMPDIR:-/tmp}/agent-discipline-state"
SESSION_MODEL=""
SESSION_MODEL_KNOWN=0
PENDING_MODEL=0
if [ -n "$SAFE_SESSION_ID" ]; then
  STATE_FILE="$STATE_DIR/model-$SAFE_SESSION_ID"
  if [ -r "$STATE_FILE" ]; then
    SESSION_MODEL=$(cat "$STATE_FILE" 2>/dev/null)
    SESSION_MODEL_KNOWN=1
  elif [ -e "$STATE_DIR/pending-model-$SAFE_SESSION_ID" ]; then
    PENDING_MODEL=1
  fi
fi

PENDING_DENY_REASON="agent-discipline: このセッションはモデル判定不能期間 (pending) のため、メインセッションのモデルを継承する起動 (model 未指定 / fork) を一時的に deny しています。継承先が Fable になる可能性があり、この期間は state が未確定で検知できません。model に非 Fable モデル (例: sonnet) を明示した新規起動に切り替えるか、会話を 1 turn 進めて one-shot 補正でモデルが確定するのを待ってから再実行してください。"

# メインセッションのモデルで allow / deny を決める (継承経路と fork で共有する)。
# $1 = メインセッションが Fable と判明した場合の deny 理由。
decide_by_session_model() {
  if [ "$SESSION_MODEL_KNOWN" -eq 1 ]; then
    if is_fable "$SESSION_MODEL"; then
      deny "$1"
    fi
    exit 0
  fi
  if [ "$PENDING_MODEL" -eq 1 ]; then
    deny "$PENDING_DENY_REASON"
  fi
  exit 0
}

# 1. fork は model 指定も env も無視してメインセッションのモデルを継承する
if [ "$SUBAGENT_TYPE" = "fork" ]; then
  decide_by_session_model "agent-discipline: fork のサブエージェントは model 指定にも env にも依らずメインセッション (Fable) のモデルを継承します。fork をやめ、必要な文脈を指示文に埋め込んだ新規起動で model に sonnet / opus (機械的作業なら haiku) を明示して再実行してください。"
fi

# 2. FORCE が有効なら実効モデルは env (非空ならその値、空ならメインセッションのモデル)
if [ "$FORCE_ENABLED" -eq 1 ]; then
  if [ -n "$ENV_SUB" ]; then
    if is_fable "$ENV_SUB"; then
      deny "agent-discipline: CLAUDE_CODE_SUBAGENT_MODEL_FORCE が有効で CLAUDE_CODE_SUBAGENT_MODEL が fable を指すため、全サブエージェントが Fable で実行されます。model を明示しても実効モデルは変わらないため、FORCE の解除または env の sonnet / opus への修正が必要です。どちらもセッションを超える設定のため独断で書き換えず、この状態をユーザに報告して修正を依頼してください。"
    fi
    exit 0
  fi
  decide_by_session_model "agent-discipline: CLAUDE_CODE_SUBAGENT_MODEL_FORCE が有効で CLAUDE_CODE_SUBAGENT_MODEL が未設定のため、全サブエージェントがメインセッション (Fable) のモデルで実行されます。model を明示しても実効モデルは変わらないため、FORCE の解除または CLAUDE_CODE_SUBAGENT_MODEL への sonnet / opus の設定が必要です。どちらもセッションを超える設定のため独断で書き換えず、この状態をユーザに報告して修正を依頼してください。"
fi

# 3. fable の明示指定は deny
if is_fable "$TOOL_MODEL"; then
  deny "agent-discipline: サブエージェントに Fable を指定しないでください。model に sonnet / opus (機械的作業なら haiku) を明示して再実行してください。"
fi

# 4. 非 fable の具体指定は allow (明示が env より優先されるため env の値に依らない)
if [ -n "$TOOL_MODEL" ]; then
  exit 0
fi

# 5. model 未指定 = メインセッション継承経路。env が非空ならその値が実効モデルになる。
if [ -n "$ENV_SUB" ]; then
  if is_fable "$ENV_SUB"; then
    deny "agent-discipline: model 未指定のサブエージェントは CLAUDE_CODE_SUBAGENT_MODEL の値 (fable) で実行されます。model に sonnet / opus (機械的作業なら haiku) を明示して再実行してください。env 自体を sonnet / opus へ直す場合は、セッションを超える設定のため独断で書き換えず、ユーザに依頼してください。"
  fi
  exit 0
fi

decide_by_session_model "agent-discipline: model 未指定のサブエージェントはメインセッション (Fable) のモデルを継承します。model に sonnet / opus (機械的作業なら haiku) を明示して再実行してください。"
