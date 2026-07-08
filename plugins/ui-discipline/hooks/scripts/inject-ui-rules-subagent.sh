#!/bin/sh
# inject-ui-rules-subagent.sh — ui-discipline plugin の SubagentStart hook スクリプト
#
# I/O 契約:
#   stdin  : SubagentStart hook input JSON (本 plugin では内容を使用しない)
#   stdout : {"hookSpecificOutput": {"hookEventName": "SubagentStart",
#             "additionalContext": "<ui-rules-subagent-preamble.md (プレースホルダ置換済み) + 空行 + ui-rules.md>"}}
#   exit   : 常に 0 (jq 欠落・ファイル欠落・置換失敗時は注入をスキップして exit 0。
#            fail-open でセッションを壊さない。inject-ui-rules.sh と同方針)
#
# 設計:
#   - 本体ルールは SessionStart (inject-ui-rules.sh) と同一の hooks/prompts/ui-rules.md を
#     単一ソースとして共有し、subagent 向けの差分は前置き注記
#     (hooks/prompts/ui-rules-subagent-preamble.md) のみとする (2 ファイル間の drift を構造的に排除)
#   - agent_type による条件分岐を持たない (codex-advisor の SubagentStart 注入と同方針)
#   - 前置き注記中の {{UI_PATTERNS_SKILL_PATH}} は skills/ui-patterns/SKILL.md の絶対パスへ
#     jq の gsub で置換する。--arg で渡した値は置換値として literal に扱われるため、パス中の
#     メタ文字で置換が壊れない (sed を使わないのは codex-advisor の
#     inject-advisor-rules-subagent.sh と同じ理由)
#   - 前置き注記・本体・SKILL.md のいずれかが欠けた場合は全体を注入しない (読み替え規則を
#     欠いたまま rule:visual-direction を subagent に配送しないための部分注入の禁止)
#
# 制約:
#   - Linux (WSL2) / macOS (bash 3.2 環境の /bin/sh) の両方で動作すること
#   - SubagentStart hook は Claude Code 2.0.43 以降で発火する。それ未満では本スクリプトは
#     呼ばれず、SessionStart 注入のみ有効

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# prompt ファイルは hooks/scripts/../prompts/ に配置されている
# (inject-ui-rules.sh と同じパス解決方式)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)

PREAMBLE=$(cat "$PROMPTS_DIR/ui-rules-subagent-preamble.md" 2>/dev/null)
RULES=$(cat "$PROMPTS_DIR/ui-rules.md" 2>/dev/null)

# どちらかが読めない・空なら全体を注入しない (ヘッダの部分注入禁止の契約)。
if [ -z "$PREAMBLE" ] || [ -z "$RULES" ]; then
  exit 0
fi

# ui-patterns skill の SKILL.md 絶対パスを script 自身の位置から解決する。
# hooks/scripts/ から見て ../../skills/ui-patterns/SKILL.md。
SKILL_DIR=$(cd "$(dirname "$0")/../../skills/ui-patterns" 2>/dev/null && pwd)

if [ -z "$SKILL_DIR" ] || [ ! -f "$SKILL_DIR/SKILL.md" ]; then
  exit 0
fi

# プレースホルダ置換・前置き注記と本体の連結・JSON 出力を jq 1 回で行う。
# jq が万一失敗しても header の「exit 常に 0」契約を守る (fail-open)。
jq -n --arg pre "$PREAMBLE" --arg rules "$RULES" --arg skill "$SKILL_DIR/SKILL.md" '{
  hookSpecificOutput: {
    hookEventName: "SubagentStart",
    additionalContext: (($pre | gsub("\\{\\{UI_PATTERNS_SKILL_PATH\\}\\}"; $skill)) + "\n\n" + $rules)
  }
}' || exit 0
