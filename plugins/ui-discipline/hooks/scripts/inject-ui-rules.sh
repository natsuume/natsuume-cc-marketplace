#!/bin/sh
# inject-ui-rules.sh — ui-discipline plugin の SessionStart hook スクリプト
#
# I/O 契約 (issue #197):
#   stdin  : SessionStart hook input JSON (本 plugin では内容を使用しない)
#   stdout : {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<ui-rules.md 全文>"}}
#   exit   : 常に 0 (prompt ファイル欠落時は注入をスキップして exit 0。fail-open でセッションを壊さない)
#
# 制約 (issue #197):
#   - Linux (WSL2) / macOS の両方で動作すること
#   - モデル判定・permission_mode 判定等の条件分岐を持たない (常に同一内容を注入する)
#
# Phase B で実装本体を追加する。
exit 0
