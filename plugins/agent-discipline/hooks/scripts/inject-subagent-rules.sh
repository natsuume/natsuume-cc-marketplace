#!/bin/sh
# inject-subagent-rules.sh — agent-discipline plugin の SubagentStart hook スクリプト (issue #221)
#
# I/O 契約:
#   stdin  : SubagentStart hook input JSON (本 plugin では内容を使用しない)
#   stdout : {"hookSpecificOutput": {"hookEventName": "SubagentStart",
#             "additionalContext": "<subagent-rules.md の本文>"}}
#            本文は先頭の保守者向け HTML コメントと直後の空行を除いたもの
#            (lib/prompt-body.sh)
#   exit   : 常に 0 (jq 欠落・prompt ファイル欠落・空ファイル時は注入をスキップして exit 0。
#            fail-open でセッションを壊さない。inject-always.sh / cross-model-advisor の
#            inject-advisor-rules-subagent.sh と同方針)
#
# 設計:
#   - モデル判定を持たない: subagent のモデルは起動経路 (明示 model・frontmatter・env・
#     継承・fork) ごとに決まり、fork / frontmatter 経路では Fable になりうる
#     (block-fable-subagent.sh が捕捉できるのは明示 model・env・継承の各経路に限る)。
#     加えて SubagentStart hook input のモデル情報にも保証がないため、どのモデルで実行
#     されても成り立つ単一テンプレートを常に注入する
#   - agent_type による条件分岐も持たない (cross-model-advisor / ui-discipline の SubagentStart
#     注入と同方針)
#   - プレースホルダ置換なし (静的注入)。パス解決は script 自身の位置基準
#     (hooks/scripts/ から見て ../prompts/subagent-rules.md)
#
# 制約:
#   - Linux (WSL2) / macOS (bash 3.2 環境の /bin/sh) の両方で動作すること
#   - SubagentStart hook は Claude Code 2.0.43 以降で発火する。それ未満では本スクリプトは
#     呼ばれず、メインセッション向けの SessionStart / UserPromptSubmit 配送のみ有効

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# 先頭の保守者向け HTML コメントを除いて prompt ファイルを読む helper を読み込む。
SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
if [ -z "$SCRIPT_DIR" ] || [ ! -r "$SCRIPT_DIR/lib/prompt-body.sh" ]; then
  exit 0
fi
# shellcheck source=plugins/agent-discipline/hooks/scripts/lib/prompt-body.sh
. "$SCRIPT_DIR/lib/prompt-body.sh" || exit 0

# prompt ファイルは hooks/scripts/../prompts/ に配置されている
# (inject-always.sh と同じパス解決方式)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)

# 注入本文を読み込む。読めない・空の場合は fail-open で無音終了する
# (inject-always.sh と同じ方針。壊れた・欠けた注入で誤誘導するより注入しない方が安全)。
CONTEXT=$(read_agent_discipline_prompt "$PROMPTS_DIR/subagent-rules.md")

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
