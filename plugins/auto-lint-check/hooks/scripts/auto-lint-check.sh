#!/bin/bash
# auto-lint-check.sh
#
# Edit/Write/MultiEdit ツール実行直前に、編集後の予測内容を生成して linter
# (ESLint / Ruff) に stdin で流す。lint がエラーを返したらツール実行を deny する。
#
# linter 設定ファイルが見つからなければ静かにスキップ。
# linter バイナリが見つからなければ警告だけ出してスキップ。

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

FILE_PATH=$(extract_file_path "$INPUT") || exit 0
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# 編集後の予測内容を構築する (Edit/MultiEdit は実ファイル + 置換適用)。
PREDICTED=$(printf '%s' "$INPUT" | python3 "$AUTO_LINT_CHECK_LIB_DIR/predict-content.py")
if [ $? -ne 0 ]; then
  # 予測できないケース (新規 Write 以外で実ファイルが無い等) はスキップ
  exit 0
fi

if is_js_like "$FILE_PATH"; then
  if ! run_eslint_stdin "$FILE_PATH" "$PREDICTED"; then
    REASON=$(printf '%s\n' \
      "ESLint がエラーを検出しました。修正してから再度編集してください。" \
      "" \
      "$LINTER_OUTPUT")
    emit_deny "$REASON"
  fi
elif is_python "$FILE_PATH"; then
  if ! run_ruff_check_stdin "$FILE_PATH" "$PREDICTED"; then
    REASON=$(printf '%s\n' \
      "Ruff がエラーを検出しました。修正してから再度編集してください。" \
      "" \
      "$LINTER_OUTPUT")
    emit_deny "$REASON"
  fi
fi

exit 0
