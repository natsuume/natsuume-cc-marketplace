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
# 設計 (issue #221 の確定設計):
#   - モデル判定 (Fable/Sonnet 分岐) を持たない: subagent は Fable になり得ず
#     (block-fable-subagent.sh が deny)、SubagentStart hook input のモデル情報にも保証が
#     ないため、Sonnet 向け書式の単一テンプレートを常に注入する
#   - agent_type による条件分岐も持たない (codex-advisor / ui-discipline の SubagentStart
#     注入と同方針)
#   - プレースホルダ置換なし (静的全文注入)。パス解決は script 自身の位置基準
#     (hooks/scripts/ から見て ../prompts/subagent-rules.md)
#
# 制約:
#   - Linux (WSL2) / macOS (bash 3.2 環境の /bin/sh) の両方で動作すること
#   - SubagentStart hook は Claude Code 2.0.43 以降で発火する。それ未満では本スクリプトは
#     呼ばれず、メインセッション向けの SessionStart / UserPromptSubmit 配送のみ有効

# (Phase B で実装: 上記契約に従い、subagent-rules.md 全文を JSON 出力する)
exit 0
