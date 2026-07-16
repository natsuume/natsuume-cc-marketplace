#!/bin/sh

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" 2>/dev/null && pwd)
if [ -z "$SCRIPT_DIR" ]; then
  exit 0
fi

SUBAGENT_PROMPT=$(cat "$SCRIPT_DIR/../prompts/subagent.md" 2>/dev/null)
SESSION_PROMPT=$(cat "$SCRIPT_DIR/../prompts/session.md" 2>/dev/null)
if [ -z "$SUBAGENT_PROMPT" ] || [ -z "$SESSION_PROMPT" ]; then
  exit 0
fi

CONTEXT="$SESSION_PROMPT

$SUBAGENT_PROMPT"

jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "SubagentStart",
    additionalContext: $ctx
  }
}' || exit 0
