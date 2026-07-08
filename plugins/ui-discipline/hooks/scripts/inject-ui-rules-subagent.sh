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

# (Phase B で実装: 上記契約に従い、前置き注記 + ui-rules.md を連結して JSON 出力する)
exit 0
