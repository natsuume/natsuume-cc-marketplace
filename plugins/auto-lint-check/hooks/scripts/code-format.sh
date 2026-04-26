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

run_in() {
  local dir="$1"
  shift
  (cd "$dir" && "$@" >/dev/null 2>&1) || true
}

if is_js_like "$FILE_PATH"; then
  ROOT_ESLINT=$(find_config_root "$FILE_PATH" eslint)
  if [ -n "$ROOT_ESLINT" ]; then
    BIN=$(resolve_eslint "$ROOT_ESLINT")
    if [ -n "$BIN" ]; then
      # shellcheck disable=SC2086
      run_in "$ROOT_ESLINT" $BIN --fix "$FILE_PATH"
    fi
  fi
  ROOT_PRETTIER=$(find_config_root "$FILE_PATH" prettier)
  if [ -n "$ROOT_PRETTIER" ]; then
    BIN=$(resolve_prettier "$ROOT_PRETTIER")
    if [ -n "$BIN" ]; then
      # shellcheck disable=SC2086
      run_in "$ROOT_PRETTIER" $BIN --write "$FILE_PATH"
    fi
  fi
elif is_python "$FILE_PATH"; then
  ROOT_RUFF=$(find_config_root "$FILE_PATH" ruff)
  if [ -n "$ROOT_RUFF" ]; then
    BIN=$(resolve_ruff)
    if [ -n "$BIN" ]; then
      # shellcheck disable=SC2086
      run_in "$ROOT_RUFF" $BIN check --fix "$FILE_PATH"
      # shellcheck disable=SC2086
      run_in "$ROOT_RUFF" $BIN format "$FILE_PATH"
    fi
  fi
fi

exit 0
