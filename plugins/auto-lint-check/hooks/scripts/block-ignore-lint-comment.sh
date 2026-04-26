#!/bin/bash
# block-ignore-lint-comment.sh
#
# Edit/Write/MultiEdit が「新規挿入する」内容に linter/formatter の ignore
# コメントが含まれていたらツール実行を deny する。既に old_string や既存
# ファイルに含まれていた ignore コメントを保持するだけの編集は許可する
# (検出は detect-new-ignores.py で多重集合差分により実装)。
#
# 対象は eslint / prettier / ruff の代表的な ignore 構文。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi
if ! command -v python3 >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)
TOOL_NAME=$(extract_tool_name "$INPUT")

case "$TOOL_NAME" in
  Write|Edit|MultiEdit) ;;
  *) exit 0 ;;
esac

# 新規挿入された ignore コメントだけを Python ヘルパーで列挙する。
# exit code:
#   0  追加なし  → そのまま許可
#   2  追加あり  → DETECT_OUTPUT に検出箇所、deny する
#   その他      → 入力不正等。安全側で許可してスキップ
DETECT_OUTPUT=$(printf '%s' "$INPUT" | python3 "$AUTO_LINT_CHECK_LIB_DIR/detect-new-ignores.py")
DETECT_RC=$?

if [ "$DETECT_RC" = "2" ]; then
  REASON=$(printf '%s\n' \
    "lint/formatter の ignore コメント挿入は禁止されています。" \
    "lint 警告は本体のコードを修正することで解決してください。" \
    "" \
    "新規挿入された ignore コメント (最初の3件):" \
    "$DETECT_OUTPUT")
  emit_deny "$REASON"
fi

exit 0
