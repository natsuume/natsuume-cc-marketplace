#!/bin/bash
# block-ignore-lint-comment.sh
#
# Edit/Write/MultiEdit が「新規挿入する」内容に linter/formatter の ignore
# コメントが含まれていたらツール実行を deny する。
#
# 対象は eslint / prettier / ruff の代表的な ignore 構文。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)
TOOL_NAME=$(extract_tool_name "$INPUT")

case "$TOOL_NAME" in
  Write|Edit|MultiEdit) ;;
  *) exit 0 ;;
esac

# ツール別に新規挿入分を抽出する (MultiEdit は edits[].new_string を改行区切りで結合)。
case "$TOOL_NAME" in
  Write)     INSERTED=$(printf '%s' "$INPUT" | jq -r '.tool_input.content // empty') ;;
  Edit)      INSERTED=$(printf '%s' "$INPUT" | jq -r '.tool_input.new_string // empty') ;;
  MultiEdit) INSERTED=$(printf '%s' "$INPUT" | jq -r '.tool_input.edits[]?.new_string // empty') ;;
esac

if [ -z "$INSERTED" ]; then
  exit 0
fi

# ESLint / Prettier / Ruff の代表的な ignore コメント構文を一気にマッチ。
PATTERN='//[[:space:]]*eslint-(disable|enable|disable-line|disable-next-line)'
PATTERN+='|/\*[[:space:]]*eslint-(disable|enable|disable-next-line)'
PATTERN+='|//[[:space:]]*prettier-ignore'
PATTERN+='|/\*[[:space:]]*prettier-ignore[[:space:]]*\*/'
PATTERN+='|<!--[[:space:]]*prettier-ignore[[:space:]]*-->'
PATTERN+='|#[[:space:]]*noqa([[:space:]]|$|:)'
PATTERN+='|#[[:space:]]*ruff:[[:space:]]*noqa'
PATTERN+='|#[[:space:]]*fmt:[[:space:]]*(off|on|skip)'

MATCH=$(printf '%s' "$INSERTED" | grep -nE "$PATTERN" | head -3)

if [ -n "$MATCH" ]; then
  REASON=$(printf '%s\n' \
    "lint/formatter の ignore コメント挿入は禁止されています。" \
    "lint 警告は本体のコードを修正することで解決してください。" \
    "" \
    "検出箇所 (新規挿入分の最初の3件):" \
    "$MATCH")
  emit_deny "$REASON"
fi

exit 0
