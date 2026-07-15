#!/bin/bash
# codex-auto-mark.sh - Codex SubagentStop から review marker を自動更新する。
#
# Claude Code も同じ hooks.json を読むため、Codex SubagentStop input で required な
# Codex extension `turn_id` と event/agent fields が揃うときだけ動作する。Claude の
# namespaced agent は matcher 自体が異なり、script が直接呼ばれても turn_id guard で no-op。
# Codex 側でも検証・hash 計算・IO の失敗は marker を書かない側へ倒し、subagent の停止
# 自体は妨げない。push 時の block-pre-push.sh が marker 不足を fail-closed で deny する。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INPUT=$(cat)
case "$INPUT" in
  *'"hook_event_name"'*) ;;
  *) exit 0 ;;
esac
case "$INPUT" in
  *'"SubagentStop"'*) ;;
  *) exit 0 ;;
esac
case "$INPUT" in
  *'"agent_type"'*) ;;
  *) exit 0 ;;
esac
# Codex 0.144.4 の SubagentStop schema では turn_id が required の Codex extension。
# Claude Code payload はこの field を持たないため、jq 起動前の明示 runtime guard にする。
case "$INPUT" in
  *'"turn_id"'*) ;;
  *) exit 0 ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' "[pre-push-review] jq が無いため Codex review marker を更新できません。" >&2
  exit 0
fi

# Codex 0.144 系の SubagentStop command input で runtime が供給するフィールドを検証する。
# stop_hook_active=true は別 hook が停止を差し戻した再入時なので、同じ完了を再発行しない。
if ! printf '%s' "$INPUT" | jq -e '
  .hook_event_name == "SubagentStop" and
  (.agent_id | type == "string" and length > 0) and
  (.agent_type | type == "string" and length > 0) and
  (.cwd | type == "string" and length > 0) and
  (.turn_id | type == "string" and length > 0) and
  (.model | type == "string" and length > 0) and
  (.permission_mode == "default" or
   .permission_mode == "acceptEdits" or
   .permission_mode == "plan" or
   .permission_mode == "dontAsk" or
   .permission_mode == "bypassPermissions") and
  (.last_assistant_message | type == "string" and length > 0) and
  has("agent_transcript_path") and
  ((.agent_transcript_path | type) == "string" or .agent_transcript_path == null) and
  has("transcript_path") and
  ((.transcript_path | type) == "string" or .transcript_path == null) and
  .stop_hook_active == false
' >/dev/null 2>&1; then
  exit 0
fi

AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.agent_type')
AGENT_ID=$(printf '%s' "$INPUT" | jq -r '.agent_id')
HOOK_CWD=$(printf '%s' "$INPUT" | jq -r '.cwd')
LAST_MESSAGE=$(printf '%s' "$INPUT" | jq -r '.last_assistant_message')

case "$AGENT_TYPE" in
  pre-push-correctness-reviewer)
    ROLE="correctness"
    EXPECTED_HEADING="# Correctness Review"
    MARKER_FN="code_reviewed_marker_path"
    ;;
  pre-push-independent-reviewer)
    ROLE="independent"
    EXPECTED_HEADING="# Independent Review"
    MARKER_FN="codex_marker_path"
    ;;
  pre-push-security-reviewer)
    ROLE="security"
    EXPECTED_HEADING="# Security Review"
    MARKER_FN="security_marker_path"
    ;;
  *)
    exit 0
    ;;
esac

FIRST_NONEMPTY=$(printf '%s\n' "$LAST_MESSAGE" | awk 'NF { sub(/\r$/, ""); print; exit }')
LAST_NONEMPTY=$(printf '%s\n' "$LAST_MESSAGE" | awk 'NF { line=$0 } END { sub(/\r$/, "", line); print line }')
EXPECTED_FOOTER="<!-- pre-push-review:completed $ROLE -->"
if [ "$FIRST_NONEMPTY" != "$EXPECTED_HEADING" ] || [ "$LAST_NONEMPTY" != "$EXPECTED_FOOTER" ]; then
  printf '[pre-push-review] %s (%s) の完了 footer を検証できず marker を更新しません。\n' \
    "$AGENT_TYPE" "$AGENT_ID" >&2
  exit 0
fi

if ! cd "$HOOK_CWD" 2>/dev/null; then
  printf '[pre-push-review] SubagentStop cwd を開けず marker を更新しません: %s\n' "$HOOK_CWD" >&2
  exit 0
fi

# shellcheck source=lib/diff-hash.sh
source "$SCRIPT_DIR/lib/diff-hash.sh"
# shellcheck source=lib/markers.sh
source "$SCRIPT_DIR/lib/markers.sh"

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0
# The hook intentionally asks the helper to auto-detect its base branch.
# shellcheck disable=SC2119
BASE=$(detect_base_branch) || exit 0
BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null) || exit 0
case "$BRANCH" in
  master|main) exit 0 ;;
esac
HASH=$(compute_review_hash "$BASE") || exit 0
MARKER_PATH=$("$MARKER_FN" "$GIT_DIR") || exit 0

TEMP_MARKER="${MARKER_PATH}.tmp.$$"
# Invoked indirectly by the EXIT trap below.
# shellcheck disable=SC2317
cleanup() {
  if [ -n "$TEMP_MARKER" ]; then
    rm -f "$TEMP_MARKER"
  fi
}
trap cleanup EXIT
umask 077
if ! printf '%s' "$HASH" > "$TEMP_MARKER"; then
  exit 0
fi
if ! mv "$TEMP_MARKER" "$MARKER_PATH"; then
  exit 0
fi
TEMP_MARKER=""

printf '[pre-push-review] Codex %s marker updated from SubagentStop: %s (agent_id=%s)\n' \
  "$ROLE" "$MARKER_PATH" "$AGENT_ID" >&2
exit 0
