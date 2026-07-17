#!/bin/bash
# code-format.sh
#
# Edit/Write/MultiEdit/apply_patch 実行後に対応する formatter / linter --fix を実行する。
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
if ! command -v python3 >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# 配列展開で起動するため、空白を含むパスでも安全。
run_in() {
  local dir="$1"
  shift
  (cd "$dir" && "$@" >/dev/null 2>&1) || true
}

format_file() {
  local file_path="$1"
  local file_dir root_eslint root_prettier root_ruff

  if [ -z "$file_path" ] || [ ! -f "$file_path" ]; then
    return 0
  fi

  # repository 外の scratch file では祖先の無関係な config を拾って formatter や
  # package runner を起動しない。repository 内なら untracked file も従来どおり対象に
  # するため、git ls-files ではなく worktree membership だけを確認する。
  file_dir=$(dirname "$file_path")
  if ! git -C "$file_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    return 0
  fi

  if is_js_like "$file_path"; then
    root_eslint=$(find_config_root "$file_path" eslint)
    if [ -n "$root_eslint" ] && resolve_eslint "$root_eslint"; then
      run_in "$root_eslint" "${ESLINT_CMD[@]}" --fix "$file_path"
    fi
    root_prettier=$(find_config_root "$file_path" prettier)
    if [ -n "$root_prettier" ] && resolve_prettier "$root_prettier"; then
      run_in "$root_prettier" "${PRETTIER_CMD[@]}" --write "$file_path"
    fi
  elif is_python "$file_path"; then
    root_ruff=$(find_config_root "$file_path" ruff)
    if [ -n "$root_ruff" ] && resolve_ruff; then
      run_in "$root_ruff" "${RUFF_CMD[@]}" check --fix "$file_path"
      run_in "$root_ruff" "${RUFF_CMD[@]}" format "$file_path"
    fi
  fi
}

# Claude は file_path を 1 件、Codex は apply_patch command に複数 path を持つ。
# NUL 区切りに正規化して、空白や改行を含む path でも 1 件ずつ安全に処理する。
while IFS= read -r -d '' FILE_PATH; do
  format_file "$FILE_PATH"
done < <(
  printf '%s' "$INPUT" \
    | python3 "$AUTO_LINT_CHECK_LIB_DIR/extract-edit-paths.py" 2>/dev/null
)

exit 0
