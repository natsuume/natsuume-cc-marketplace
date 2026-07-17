#!/bin/sh
# inject-advisor-rules-subagent.sh — codex-advisor plugin の SubagentStart hook スクリプト
#
# I/O 契約 (issue #219):
#   stdin  : SubagentStart hook input JSON (本 plugin では内容を使用しない)
#   stdout : {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": "<advisor-rules-subagent.md 全文>"}}
#   exit   : 常に 0 (テンプレート欠落時は注入をスキップして exit 0。fail-open でセッションを壊さない)
#
# 制約 (issue #219):
#   - Linux (WSL2) / macOS (bash 3.2 環境の /bin/sh) の両方で動作すること
#   - agent_type による条件分岐を持たない (常に同一内容を注入する)
#
# v1.0.0 以降、通常 subagent は wrapper を直接実行せず相談 request を親へ返すため、
# install path の template 置換は不要になった。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# prompt テンプレートは hooks/scripts/../prompts/ に配置されている
# (inject-advisor-rules.sh と同じパス解決方式)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)

# テンプレート本文を読み込む。読めない・空の場合は fail-open で無音終了する
# (inject-advisor-rules.sh と同じ方針。壊れた・欠けた注入で誤誘導するより
# 注入しない方が安全)。
TEMPLATE=$(cat "$PROMPTS_DIR/advisor-rules-subagent.md" 2>/dev/null)

if [ -z "$TEMPLATE" ]; then
  exit 0
fi

# JSON 出力を jq 1 回で行う。jq が万一失敗しても header の「exit 常に 0」契約を
# 守る (fail-open)。
jq -n --arg tpl "$TEMPLATE" '{
  hookSpecificOutput: {
    hookEventName: "SubagentStart",
    additionalContext: $tpl
  }
}' || exit 0
