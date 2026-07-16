#!/bin/sh

# Codex SessionStart injector for natsuume-writing.
# Missing dependencies or prompt files fail open so the plugin does not break a session.

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" 2>/dev/null && pwd)
if [ -z "$SCRIPT_DIR" ]; then
  exit 0
fi

PROMPT_FILE="$SCRIPT_DIR/../prompts/session.md"
CONTEXT=$(cat "$PROMPT_FILE" 2>/dev/null)
if [ -z "$CONTEXT" ]; then
  exit 0
fi

jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}' || exit 0
