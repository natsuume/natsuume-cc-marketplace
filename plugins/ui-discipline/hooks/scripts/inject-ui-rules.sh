#!/bin/sh
# inject-ui-rules.sh — ui-discipline plugin の SessionStart hook スクリプト
#
# I/O 契約:
#   stdin  : SessionStart hook input JSON (本 plugin では内容を使用しない)
#   stdout : {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<ui-rules.md の本文>"}}
#            本文は ui-rules.md から先頭の保守者向け HTML コメントと直後の空行を除いたもの
#            (lib/prompt-body.sh の read_ui_discipline_prompt)
#   exit   : 常に 0 (jq 欠落・helper 欠落・prompt ファイル欠落時は注入をスキップして exit 0。
#            fail-open でセッションを壊さない)
#
# 制約:
#   - Linux (WSL2) / macOS の両方で動作すること
#   - モデル判定・permission_mode 判定等の条件分岐を持たない (常に同一内容を注入する)

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# helper は hooks/scripts/lib/prompt-body.sh に配置されている。
SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
if [ -z "$SCRIPT_DIR" ] || [ ! -r "$SCRIPT_DIR/lib/prompt-body.sh" ]; then
  exit 0
fi
# shellcheck source=plugins/ui-discipline/hooks/scripts/lib/prompt-body.sh
. "$SCRIPT_DIR/lib/prompt-body.sh"

# prompt ファイルは hooks/scripts/../prompts/ui-rules.md に配置されている
# (agent-discipline/hooks/scripts/inject-always.sh と同じパス解決方式)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)

# 注入本文を読み込む。読めない場合は fail-open で無音終了する
# (壊れた・欠けた注入で誤誘導するより注入しない方が安全)。
CONTEXT=$(read_ui_discipline_prompt "$PROMPTS_DIR/ui-rules.md")

if [ -z "$CONTEXT" ]; then
  exit 0
fi

jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}'
