#!/bin/sh

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" 2>/dev/null && pwd)
if [ -z "$SCRIPT_DIR" ]; then
  exit 0
fi

PROMPT=$(cat "$SCRIPT_DIR/../prompts/session.md" 2>/dev/null)
if [ -z "$PROMPT" ]; then
  exit 0
fi

jq -n --arg ctx "$PROMPT" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}' || exit 0
