#!/bin/bash
# code-format.sh
#
# Edit/Write/MultiEdit 実行後に対応する formatter / linter --fix を実行する。
# 失敗しても hook 全体は exit 0 で終わる。
#
# - JS/TS: eslint --fix → prettier --write
# - Python: ruff check --fix → ruff format

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)
FILE_PATH=$(extract_file_path "$INPUT") || exit 0
if [ -z "$FILE_PATH" ] || [ ! -f "$FILE_PATH" ]; then
  exit 0
fi

# 配列展開で起動するため、空白を含むパスでも安全。
run_in() {
  local dir="$1"
  shift
  (cd "$dir" && "$@" >/dev/null 2>&1) || true
}

if is_js_like "$FILE_PATH"; then
  ROOT_ESLINT=$(find_config_root "$FILE_PATH" eslint)
  if [ -n "$ROOT_ESLINT" ] && resolve_eslint "$ROOT_ESLINT"; then
    run_in "$ROOT_ESLINT" "${ESLINT_CMD[@]}" --fix "$FILE_PATH"
  fi
  ROOT_PRETTIER=$(find_config_root "$FILE_PATH" prettier)
  if [ -n "$ROOT_PRETTIER" ] && resolve_prettier "$ROOT_PRETTIER"; then
    run_in "$ROOT_PRETTIER" "${PRETTIER_CMD[@]}" --write "$FILE_PATH"
  fi
elif is_python "$FILE_PATH"; then
  ROOT_RUFF=$(find_config_root "$FILE_PATH" ruff)
  if [ -n "$ROOT_RUFF" ] && resolve_ruff; then
    run_in "$ROOT_RUFF" "${RUFF_CMD[@]}" check --fix "$FILE_PATH"
    run_in "$ROOT_RUFF" "${RUFF_CMD[@]}" format "$FILE_PATH"
  fi
fi

exit 0
