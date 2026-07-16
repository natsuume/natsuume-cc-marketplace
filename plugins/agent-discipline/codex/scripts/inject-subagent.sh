#!/bin/sh
# Inject the Codex-specific subagent contract. Keep this POSIX sh compatible for
# Linux/WSL2 and macOS. Missing jq or prompt files fail open.

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

CODEX_DIR=$(CDPATH= cd "$(dirname "$0")/.." 2>/dev/null && pwd)
if [ -z "$CODEX_DIR" ]; then
  exit 0
fi

CONTEXT=$(cat "$CODEX_DIR/prompts/subagent.md" 2>/dev/null)
if [ -z "$CONTEXT" ]; then
  exit 0
fi

jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "SubagentStart",
    additionalContext: $ctx
  }
}' || exit 0
