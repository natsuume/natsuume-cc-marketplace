#!/bin/sh
# inject-review-cadence-rules.sh — pre-push-codex-review plugin の SessionStart hook スクリプト
# (skeleton — 注入本体は Phase B で実装する)
#
# I/O 契約:
#   stdin  : SessionStart hook input JSON (本 plugin では内容を使用しない)
#   stdout : {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<review-cadence-rules.md 全文>"}}
#   exit   : 常に 0 (prompt ファイル欠落・jq 不在等の失敗時は注入をスキップして exit 0。
#            fail-open でセッションを壊さない)
#
# 制約:
#   - Linux (WSL2) / macOS の両方で動作すること
#   - モデル判定・permission_mode 判定等の条件分岐を持たない (常に同一内容を注入する)
#
# 骨格段階の挙動: 注入本体は未実装のため、何も出力せず exit 0 だけを行う安全な no-op。

exit 0
