#!/bin/bash
# Codex は hooks.json の type=agent handler を parse 後に skip するため、同じ inline prompt を
# read-only / ephemeral な独立 codex exec で評価し、結果を PreToolUse allow/deny に変換する。
# Claude Code の hook input には Codex 固有 turn_id が無いため、従来の type=agent handler
# だけが動く。

# 再帰 process では hooks を無効化するが、env guard も defense-in-depth として置く。
if [ "${AGENT_DISCIPLINE_CODEX_VALIDATOR_ACTIVE:-}" = "1" ]; then
  exit 0
fi

INPUT=$(cat)

if ! command -v jq >/dev/null 2>&1; then
  # jq 無しでは対象 command を安全に同定できない。全 Bash を止めるのは blast radius が広すぎる
  # ため、既存 command hooks と同じく無音終了する。この依存は README の保証差に明記する。
  exit 0
fi

# command/body と cwd は改行を含みうるため、line delimiter ではなく NUL delimiter で読む。
# jq -j + read -d '' は Linux / macOS 標準 bash 3.2 の双方で利用できる。
{
  IFS= read -r -d '' TURN_ID
  IFS= read -r -d '' HOOK_EVENT
  IFS= read -r -d '' COMMAND
  IFS= read -r -d '' CWD
} < <(
  printf '%s' "$INPUT" | jq -j '
    (.turn_id // ""), "\u0000",
    (.hook_event_name // ""), "\u0000",
    (.tool_input.command // ""), "\u0000",
    (.cwd // ""), "\u0000"
  ' 2>/dev/null
)

# turn_id は Codex の turn-scoped hook input で必須の Codex extension。Claude Code の
# PreToolUse input には無いため、ここを runtime guard とする。
if [ -z "$TURN_ID" ] || [ "$HOOK_EVENT" != "PreToolUse" ] || [ -z "$COMMAND" ]; then
  exit 0
fi

# Claude の 4 本の if filter と同じ narrow scope。prompt の Step 0 と同様、literal head
# だけを対象にし、env prefix / cd prefix / compound command は対象外として通す。
case "$COMMAND" in
  'gh issue create'|'gh issue create'[[:space:]]*) AGENT_IF='Bash(gh issue create:*)' ;;
  'gh issue edit'|'gh issue edit'[[:space:]]*) AGENT_IF='Bash(gh issue edit:*)' ;;
  'gh pr create'|'gh pr create'[[:space:]]*) AGENT_IF='Bash(gh pr create:*)' ;;
  'gh pr edit'|'gh pr edit'[[:space:]]*) AGENT_IF='Bash(gh pr edit:*)' ;;
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
  exit 0
}

allow() {
  # Codex の PreToolUse permissionDecision=allow は updatedInput を返す書換え hook 専用。
  # semantic pass で出すと unsupported 扱いになり、通常の approval policy も短絡しかねないため、
  # stdout 無出力の成功 (= tool call を通常経路へ継続) に変換する。
  exit 0
}

if [ -z "$CWD" ] || [ ! -d "$CWD" ]; then
  deny "agent-discipline: Codex semantic validator が hook input の cwd を確認できませんでした。検証不能のため対象の gh issue/pr 操作を fail-closed でブロックしました。"
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
HOOKS_FILE="$SCRIPT_DIR/../hooks.json"
SCHEMA_FILE="$SCRIPT_DIR/../schemas/codex-semantic-validator-output.schema.json"
OPT_IN_LIBRARY="$SCRIPT_DIR/lib/codex-semantic-opt-in.sh"
SETUP_HELPER="$SCRIPT_DIR/../../scripts/setup-codex-semantic-validator.sh"

if [ ! -r "$HOOKS_FILE" ] || [ ! -r "$SCHEMA_FILE" ] || [ ! -r "$OPT_IN_LIBRARY" ]; then
  deny "agent-discipline: Codex semantic validator の hooks.json、output schema、または opt-in state library を読み込めませんでした。検証不能のため対象操作を fail-closed でブロックしました。"
fi

# Provider/privacy boundary: nested codex exec は親 session と異なる default
# provider/model を選びうる。repo/worktree scoped の明示同意が有効な場合に限って、この先で
# canonical prompt や --body-file を nested process に渡す。無効・不正 state では codex CLI
# の存在確認すら行わず、対象 command だけを fail-closed で止める。
# shellcheck source=lib/codex-semantic-opt-in.sh disable=SC1091
. "$OPT_IN_LIBRARY"
if ! command -v git >/dev/null 2>&1 || ! agent_discipline_codex_opt_in_resolve "$CWD"; then
  deny "agent-discipline: この cwd を Git worktree として解決できず、Codex semantic validator の provider/privacy opt-in を確認できません。nested codex は起動せず対象操作をブロックしました。$SETUP_HELPER inspect --repo <path> で状態を確認してください。"
fi
agent_discipline_codex_opt_in_classify
if [ "$AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS" != 'enabled' ]; then
  deny "agent-discipline: Codex semantic validator はこの Git worktree で有効化されていません (status: $AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS)。nested codex は起動せず対象操作をブロックしました。\$agent-discipline:setup-codex-semantic-validator または $SETUP_HELPER inspect --repo '$AGENT_DISCIPLINE_CODEX_REPOSITORY' で、送信先と payload disclosure を確認してから明示的に enable してください。symlink・非 regular・owner/mode/content 不正 state は helper も変更しません。"
fi

PROMPT_COUNT=$(jq --arg agent_if "$AGENT_IF" '
  [.hooks.PreToolUse[]
    | select(.matcher == "Bash")
    | .hooks[]
    | select(.type == "agent" and .if == $agent_if and (.prompt | type) == "string" and (.prompt | length) > 0)]
  | length
' "$HOOKS_FILE" 2>/dev/null)

if [ "$PROMPT_COUNT" != "1" ]; then
  deny "agent-discipline: 対応する Claude type=agent inline prompt を一意に抽出できませんでした。正本との drift の可能性があるため対象操作を fail-closed でブロックしました。"
fi

PROMPT=$(jq -r --arg agent_if "$AGENT_IF" '
  .hooks.PreToolUse[]
  | select(.matcher == "Bash")
  | .hooks[]
  | select(.type == "agent" and .if == $agent_if)
  | .prompt
' "$HOOKS_FILE" 2>/dev/null)

# Claude runtime が末尾の placeholder に hook payload を interpolate する挙動を再現する。
# prompt 本文中にも説明用の `$ARGUMENTS` が現れるため、末尾の placeholder だけを置換する。
# shellcheck disable=SC2016 # literal placeholder を意図的に single-quote する
case "$PROMPT" in
  *'$ARGUMENTS') ;;
  *) deny "agent-discipline: Claude inline prompt 末尾の \$ARGUMENTS placeholder が見つかりません。正本との drift の可能性があるため対象操作を fail-closed でブロックしました。" ;;
esac
# shellcheck disable=SC2016 # literal placeholder を意図的に single-quote する
PROMPT_PREFIX=${PROMPT%'$ARGUMENTS'}
# shellcheck disable=SC2016 # JSON/backtick examples and canonical placeholder are literal
CODEX_OUTPUT_NOTE='

## Codex adapter output transport

上記の canonical 判定を維持したまま、Codex Structured Outputs の transport 制約として最終 JSON は `ok` と `reason` の両 property を必ず含めること。canonical の `{"ok": true}` は `{"ok": true, "reason": null}` として返し、`ok:false` の場合は canonical 指示どおり非空の reason 文字列を返す。この nullable reason は transport 上の追加 field であり、判定基準を変更しない。'
VALIDATOR_PROMPT="$PROMPT_PREFIX$INPUT$CODEX_OUTPUT_NOTE"

if ! command -v codex >/dev/null 2>&1; then
  deny "agent-discipline: Codex semantic validator を実行する codex CLI が見つかりません。codex CLI の導入とログインを確認してください。検証不能のため対象操作を fail-closed でブロックしました。"
fi

WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/agent-discipline-codex-validator.XXXXXX" 2>/dev/null)
if [ -z "$WORK_DIR" ] || [ ! -d "$WORK_DIR" ]; then
  deny "agent-discipline: Codex semantic validator の一時ディレクトリを作成できませんでした。検証不能のため対象操作を fail-closed でブロックしました。"
fi

CODEX_PID=""
CODEX_PGID=""
CODEX_MONITOR_WAS_ENABLED=0

codex_group_alive() {
  [ -n "$CODEX_PGID" ] && kill -0 -- "-$CODEX_PGID" 2>/dev/null
}

signal_codex_group_term() {
  if codex_group_alive; then
    kill -TERM -- "-$CODEX_PGID" 2>/dev/null || true
  fi
}

signal_codex_group_kill() {
  if codex_group_alive; then
    kill -KILL -- "-$CODEX_PGID" 2>/dev/null || true
  fi
}

cleanup_codex_process() {
  signal_codex_group_term
  signal_codex_group_kill
  if [ -n "$CODEX_PID" ]; then
    wait "$CODEX_PID" 2>/dev/null || true
  fi
  CODEX_PID=""
  CODEX_PGID=""
}

cleanup() {
  # shellcheck disable=SC2317 # EXIT/signal trap から間接的に呼ばれる
  cleanup_codex_process
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT
trap 'trap - EXIT HUP INT TERM; cleanup; exit 130' HUP INT TERM

PROMPT_FILE="$WORK_DIR/prompt.txt"
RESULT_FILE="$WORK_DIR/result.json"
STDOUT_FILE="$WORK_DIR/stdout.log"
STDERR_FILE="$WORK_DIR/stderr.log"

if ! printf '%s' "$VALIDATOR_PROMPT" > "$PROMPT_FILE"; then
  deny "agent-discipline: Codex semantic validator の入力を準備できませんでした。検証不能のため対象操作を fail-closed でブロックしました。"
fi

TIMEOUT_SECONDS=${AGENT_DISCIPLINE_CODEX_TIMEOUT_SECONDS:-55}
KILL_GRACE_SECONDS=${AGENT_DISCIPLINE_CODEX_KILL_GRACE_SECONDS:-2}
case "$TIMEOUT_SECONDS" in
  ''|*[!0-9]*|0) TIMEOUT_SECONDS=55 ;;
esac
case "$KILL_GRACE_SECONDS" in
  ''|*[!0-9]*) KILL_GRACE_SECONDS=2 ;;
esac

# macOS に GNU timeout/setsid は標準搭載されないため、non-interactive Bash の monitor mode で
# background subshell を専用 process group にする。leader と、nested Codex が起動した sandbox
# helper 等の descendant 全体を TERM -> grace -> KILL し、最後に leader を wait して回収する。
case $- in
  *m*) CODEX_MONITOR_WAS_ENABLED=1 ;;
  *)
    CODEX_MONITOR_WAS_ENABLED=0
    set -m
    ;;
esac
(
  export AGENT_DISCIPLINE_CODEX_VALIDATOR_ACTIVE=1
  exec codex exec --sandbox read-only --ephemeral --disable hooks --ignore-user-config \
    --ignore-rules --color never \
    --output-schema "$SCHEMA_FILE" --output-last-message "$RESULT_FILE" \
    --cd "$CWD" -
) < "$PROMPT_FILE" > "$STDOUT_FILE" 2> "$STDERR_FILE" &
CODEX_PID=$!
CODEX_PGID=$CODEX_PID
if [ "$CODEX_MONITOR_WAS_ENABLED" -eq 0 ]; then
  set +m
fi

STARTED_AT=$(date +%s)
TIMED_OUT=0
while kill -0 "$CODEX_PID" 2>/dev/null; do
  NOW=$(date +%s)
  if [ $((NOW - STARTED_AT)) -ge "$TIMEOUT_SECONDS" ]; then
    TIMED_OUT=1
    signal_codex_group_term
    GRACE_DEADLINE=$((NOW + KILL_GRACE_SECONDS))
    while codex_group_alive; do
      NOW=$(date +%s)
      if [ "$NOW" -ge "$GRACE_DEADLINE" ]; then
        break
      fi
      sleep 1
    done
    signal_codex_group_kill
    break
  fi
  sleep 1
done
if wait "$CODEX_PID"; then
  CODEX_STATUS=0
else
  CODEX_STATUS=$?
fi
# leader が先に終了して descendant だけを残す経路も閉じる。
signal_codex_group_term
signal_codex_group_kill
CODEX_PID=""
CODEX_PGID=""

if [ "$TIMED_OUT" -eq 1 ]; then
  deny "agent-discipline: Codex semantic validator が ${TIMEOUT_SECONDS} 秒で timeout しました。検証不能のため対象操作を fail-closed でブロックしました。"
fi

if [ "$CODEX_STATUS" -ne 0 ]; then
  deny "agent-discipline: Codex semantic validator の実行に失敗しました (exit ${CODEX_STATUS})。codex login・model・rate limit を確認してください。検証不能のため対象操作を fail-closed でブロックしました。"
fi

if [ ! -s "$RESULT_FILE" ] || ! jq -e '
  type == "object"
  and ((keys | sort) == (["ok", "reason"] | sort))
  and (.ok | type) == "boolean"
  and (if .ok then .reason == null
       else ((.reason | type) == "string" and (.reason | length) > 0 and (.reason | length) <= 2000)
       end)
' "$RESULT_FILE" >/dev/null 2>&1; then
  deny "agent-discipline: Codex semantic validator が schema に適合する {ok, reason} JSON を返しませんでした。検証不能のため対象操作を fail-closed でブロックしました。"
fi

OK=$(jq -r '.ok' "$RESULT_FILE")
if [ "$OK" = "true" ]; then
  allow
fi

REASON=$(jq -r '.reason' "$RESULT_FILE")
deny "agent-discipline: Codex semantic validator: $REASON"
