#!/bin/sh
# inject-subagent-rules.sh — agent-discipline plugin の SubagentStart hook スクリプト (issue #221)
#
# I/O 契約:
#   stdin  : SubagentStart hook input JSON (本 plugin では内容を使用しない)
#   stdout : {"hookSpecificOutput": {"hookEventName": "SubagentStart",
#             "additionalContext": "<subagent-rules.md 全文>"}}
#   exit   : 常に 0 (jq 欠落・prompt ファイル欠落・空ファイル時は注入をスキップして exit 0。
#            fail-open でセッションを壊さない。inject-always.sh / codex-advisor の
#            inject-advisor-rules-subagent.sh と同方針)
#
# 設計:
#   - モデル判定 (Fable/Sonnet 分岐) を持たない: subagent は原則 Sonnet / Opus であり、
#     Fable は Fable 週次枠の使用率が閾値以下のときに限り専用 agent (fable-low-worker /
#     fable-low-explorer) への委任として許可される (block-fable-subagent.sh の判定)。
#     どちらの場合も SubagentStart hook input のモデル情報には保証がないため、Sonnet 向け
#     書式の単一テンプレートを常に注入する
#   - agent_type による条件分岐も持たない (codex-advisor / ui-discipline の SubagentStart
#     注入と同方針)
#   - プレースホルダ置換なし (静的全文注入)。パス解決は script 自身の位置基準
#     (hooks/scripts/ から見て ../prompts/subagent-rules.md)
#
# 制約:
#   - Linux (WSL2) / macOS (bash 3.2 環境の /bin/sh) の両方で動作すること
#   - SubagentStart hook は Claude Code 2.0.43 以降で発火する。それ未満では本スクリプトは
#     呼ばれず、メインセッション向けの SessionStart / UserPromptSubmit 配送のみ有効

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# prompt ファイルは hooks/scripts/../prompts/ に配置されている
# (inject-always.sh と同じパス解決方式)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)

# 注入本文を読み込む。読めない・空の場合は fail-open で無音終了する
# (inject-always.sh と同じ方針。壊れた・欠けた注入で誤誘導するより注入しない方が安全)。
CONTEXT=$(cat "$PROMPTS_DIR/subagent-rules.md" 2>/dev/null)

if [ -z "$CONTEXT" ]; then
  exit 0
fi

# jq が万一失敗しても header の「exit 常に 0」契約を守る (fail-open)。
jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "SubagentStart",
    additionalContext: $ctx
  }
}' || exit 0
