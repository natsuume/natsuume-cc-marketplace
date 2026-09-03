#!/bin/sh
# inject-merge-order-rules.sh — pre-merge-codex-review plugin の SessionStart hook スクリプト
#
# 役割:
#   `gh pr merge` を試行する前に codex-reviewer subagent を起動する規律を、
#   SessionStart の additionalContext としてセッションへ注入する。
#
# I/O 契約:
#   stdin  : SessionStart hook input JSON (本 plugin では内容を使用しない)
#   stdout : {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<hooks/prompts/merge-order-rules.md の全文 (末尾改行を除去)>"}}
#            jq で生成する。$(cat file) によるコマンド置換の末尾改行除去はそのまま利用する
#   exit   : 常に 0
#
# fail-open 方針:
#   jq が見つからない場合、または prompt ファイル
#   (`$(dirname "$0")/../prompts/merge-order-rules.md`) が存在しない・空・
#   読み取り不能のいずれの場合も、注入をスキップして無出力のまま exit 0 とする
#   (壊れた・欠けた注入で誤誘導するより、注入しない方が安全)。
#
# 制約:
#   - Linux (WSL2) / macOS の両方で動作する POSIX sh で書くこと (bash 拡張を使わない)
#   - permission mode・モデル等による条件分岐を持たない (常に同一内容を注入する)

exit 0
