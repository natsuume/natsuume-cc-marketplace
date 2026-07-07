#!/bin/sh
# inject-core.sh — natsuume-writing plugin の SessionStart hook スクリプト
#
# I/O 契約 (issue #207):
#   stdin  : SessionStart hook input JSON (本 plugin では内容を使用しない)
#   stdout : {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<core-summary.md 全文>"}}
#   exit   : 常に 0 (core-summary.md 欠落時は注入をスキップして exit 0。fail-open でセッションを壊さない)
#
# 制約 (issue #207):
#   - Linux (WSL2) / macOS の両方で動作すること
#   - モデル判定・permission_mode 判定等の条件分岐を持たない (常に同一内容を注入する)

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# core-summary.md は hooks/scripts/../../rules/ に配置されている
# (ui-discipline/hooks/scripts/inject-ui-rules.sh と同じパス解決方式)。
RULES_DIR=$(cd "$(dirname "$0")/../../rules" 2>/dev/null && pwd)

# 注入本文を読み込む。読めない場合は fail-open で無音終了する
# (ui-discipline/inject-ui-rules.sh と同じ方針。壊れた・欠けた注入で誤誘導するより
# 注入しない方が安全)。
CONTEXT=$(cat "$RULES_DIR/core-summary.md" 2>/dev/null)

if [ -z "$CONTEXT" ]; then
  exit 0
fi

jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}'
