#!/bin/sh
# inject-ui-rules-subagent.sh — ui-discipline plugin の SubagentStart hook スクリプト
#
# I/O 契約:
#   stdin  : SubagentStart hook input JSON (agent_type だけを使う)
#   stdout : agent_type が除外リスト (is_excluded_agent_type) にある場合は何も出力しない。
#            それ以外は
#            {"hookSpecificOutput": {"hookEventName": "SubagentStart",
#             "additionalContext": "<ui-rules-subagent-preamble.md の本文 (プレースホルダ置換済み) + 空行 + ui-rules.md の本文>"}}
#            本文は各 prompt ファイルから先頭の保守者向け HTML コメントと直後の空行を除いたもの
#            (lib/prompt-body.sh の read_ui_discipline_prompt)
#   exit   : 常に 0 (jq 欠落・helper 欠落・ファイル欠落・置換失敗時は注入をスキップして exit 0。
#            fail-open でセッションを壊さない。inject-ui-rules.sh と同方針)
#
# 設計:
#   - UI を実装しない agent (reviewer・runner・読み取り専用の組み込み agent) には注入しない。
#     除外リストは is_excluded_agent_type の 1 箇所で定義し、agent_type と完全一致
#     (大文字・小文字を区別する) で判定する。判定は prompt ファイルを読む前に行う
#   - agent_type が無い・空・null・文字列でない場合と、入力が JSON として読めない場合は、
#     除外を判定できないため注入する
#   - 本体ルールは SessionStart (inject-ui-rules.sh) と同一の hooks/prompts/ui-rules.md を
#     単一ソースとして共有し、subagent 向けの差分は前置き注記
#     (hooks/prompts/ui-rules-subagent-preamble.md) のみとする (2 ファイル間の drift を構造的に排除)
#   - 前置き注記中の {{UI_PATTERNS_SKILL_PATH}} は skills/ui-patterns/SKILL.md の絶対パスへ
#     jq の gsub で置換する。--arg で渡した値は置換値として literal に扱われるため、パス中の
#     メタ文字で置換が壊れない (sed を使わないのは cross-model-advisor の
#     inject-advisor-rules-subagent.sh と同じ理由)
#   - 前置き注記・本体・SKILL.md のいずれかが欠けた場合は全体を注入しない (読み替え規則を
#     欠いたまま rule:visual-direction を subagent に配送しないための部分注入の禁止)
#
# 制約:
#   - Linux (WSL2) / macOS (bash 3.2 環境の /bin/sh) の両方で動作すること
#   - SubagentStart hook は Claude Code 2.0.43 以降で発火する。それ未満では本スクリプトは
#     呼ばれず、SessionStart 注入のみ有効

# UI を実装しない agent_type なら 0 を返す。除外リストの定義はここだけに置き、
# plugin README の除外リストと一致させる。
is_excluded_agent_type() {
  case "$1" in
    pre-push-review:code-reviewer | \
      pre-push-review:security-reviewer | \
      pre-push-codex-review:codex-reviewer | \
      pre-merge-cross-review:codex-reviewer | \
      cross-model-advisor:codex-advisor-runner | \
      cross-model-advisor:codex-rescue-runner | \
      cross-model-advisor:codex-review-runner | \
      Explore | \
      claude-code-guide | \
      statusline-setup)
      return 0
      ;;
  esac
  return 1
}

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# agent_type を取り出す。JSON として読めない入力や、agent_type が文字列でない入力では
# 空文字になり、除外せずに注入する。
INPUT=$(cat)
AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '
  if type == "object" and (.agent_type | type) == "string" then .agent_type else "" end
' 2>/dev/null)

if [ -n "$AGENT_TYPE" ] && is_excluded_agent_type "$AGENT_TYPE"; then
  exit 0
fi

# helper は hooks/scripts/lib/prompt-body.sh に配置されている。
SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
if [ -z "$SCRIPT_DIR" ] || [ ! -r "$SCRIPT_DIR/lib/prompt-body.sh" ]; then
  exit 0
fi
# shellcheck source=plugins/ui-discipline/hooks/scripts/lib/prompt-body.sh
. "$SCRIPT_DIR/lib/prompt-body.sh"

# prompt ファイルは hooks/scripts/../prompts/ に配置されている
# (inject-ui-rules.sh と同じパス解決方式)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)

PREAMBLE=$(read_ui_discipline_prompt "$PROMPTS_DIR/ui-rules-subagent-preamble.md")
RULES=$(read_ui_discipline_prompt "$PROMPTS_DIR/ui-rules.md")

# どちらかが読めない・空なら全体を注入しない (部分注入の禁止)。
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
