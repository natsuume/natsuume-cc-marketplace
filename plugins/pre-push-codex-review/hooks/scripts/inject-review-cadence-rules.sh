#!/bin/sh
# inject-review-cadence-rules.sh — pre-push-codex-review plugin の SessionStart hook スクリプト
#
# I/O 契約:
#   stdin  : SessionStart hook input JSON (本 plugin では内容を使用しない)
#   stdout : {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<review-cadence-rules.md 全文>"}}
#   exit   : 常に 0 (prompt ファイル欠落・jq 不在等の失敗時は注入をスキップして exit 0。
#            fail-open でセッションを壊さない)
#
# 制約:
#   - Linux (WSL2) / macOS の両方で動作すること
#   - モデル判定・permission_mode 判定等の条件分岐を持たない (常に同一内容を注入する)

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# prompt ファイルは hooks/scripts/../prompts/ に配置されている
# (codex-advisor/hooks/scripts/inject-advisor-rules.sh と同じパス解決方式)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)

# 注入本文を読み込む。読めない場合は fail-open で無音終了する
# (codex-advisor/inject-advisor-rules.sh と同じ方針。壊れた・欠けた注入で誤誘導するより
# 注入しない方が安全)。
CONTEXT=$(cat "$PROMPTS_DIR/review-cadence-rules.md" 2>/dev/null)

if [ -z "$CONTEXT" ]; then
  exit 0
fi

# jq が万一失敗しても header の「exit 常に 0」契約を守る (fail-open)。
jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}' || exit 0
