#!/bin/sh
# inject-core.sh — natsuume-writing plugin の SessionStart hook スクリプト
#
# I/O 契約:
#   stdin  : SessionStart hook input JSON (本 plugin では内容を使用しない)
#   stdout : {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<本文>"}}
#            本文 = core-summary.md 全文 + 空行 + `(参照パス) <rules ディレクトリの絶対パス>`
#   exit   : 常に 0 (core-summary.md 欠落時は注入をスキップして exit 0。fail-open でセッションを壊さない)
#
# `(参照パス)` 行は、skill の外 (PR 説明やドキュメントを書く場面等) で
# general-writing.md / expression-watchlist.md / writing-rules.md を読めるようにするためのもの。
# skill の外では ${CLAUDE_PLUGIN_ROOT} が展開されないため、実行時に解決した絶対パスを渡す。
#
# 制約:
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

PATH_LINE="(参照パス) $RULES_DIR"

jq -n --arg ctx "$CONTEXT" --arg path_line "$PATH_LINE" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: ($ctx + "\n\n" + $path_line)
  }
}'
