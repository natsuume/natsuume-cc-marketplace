#!/bin/bash
# find-config-root.sh
#
# 編集対象ファイルから上向きに最寄りの linter 設定ファイルを探し、その所在
# ディレクトリ (絶対パス) を stdout に出力する。見つからなければ何も出力せず
# exit 0 で終了する。git ルートに到達したら打ち切る。
#
# Usage:
#   find-config-root.sh <file_path> <linter_type>
#
# linter_type: eslint | prettier | ruff

FILE_PATH="${1:-}"
LINTER="${2:-}"

if [ -z "$FILE_PATH" ] || [ -z "$LINTER" ]; then
  exit 0
fi

# `realpath -m` は GNU coreutils 限定なので、可搬性のため python3 で
# 同等の正規化 (os.path.abspath) を行う。ただし呼び出し側 (common.sh の
# extract_file_path) で既に絶対パスに正規化されているため、ここでは
# dirname の結果を再度正規化するだけで通常は何も変化しない。
PARENT_DIR="$(dirname "$FILE_PATH")"
if command -v python3 >/dev/null 2>&1; then
  DIR=$(python3 -c 'import os, sys; sys.stdout.write(os.path.abspath(sys.argv[1]))' "$PARENT_DIR" 2>/dev/null)
else
  DIR="$PARENT_DIR"
fi
if [ -z "$DIR" ]; then
  exit 0
fi

case "$LINTER" in
  eslint)
    # flat config の TS 変種 (.mts/.cts) も含める (#65)。
    MARKERS=(
      "eslint.config.js" "eslint.config.mjs" "eslint.config.cjs"
      "eslint.config.ts" "eslint.config.mts" "eslint.config.cts"
      ".eslintrc.js" ".eslintrc.cjs" ".eslintrc.json" ".eslintrc.yml" ".eslintrc.yaml"
    )
    ;;
  prettier)
    # 新しめの形式 (.json5 / .ts / prettier.config.{ts,mts,cts}) も含める (#65)。
    MARKERS=(
      ".prettierrc" ".prettierrc.json" ".prettierrc.json5" ".prettierrc.yml" ".prettierrc.yaml"
      ".prettierrc.js" ".prettierrc.cjs" ".prettierrc.mjs" ".prettierrc.ts" ".prettierrc.toml"
      "prettier.config.js" "prettier.config.cjs" "prettier.config.mjs"
      "prettier.config.ts" "prettier.config.mts" "prettier.config.cts"
    )
    ;;
  ruff)
    MARKERS=("ruff.toml" ".ruff.toml")
    ;;
  *)
    exit 0
    ;;
esac

while true; do
  if [ -d "$DIR" ]; then
    for m in "${MARKERS[@]}"; do
      if [ -f "$DIR/$m" ]; then
        echo "$DIR"
        exit 0
      fi
    done

    case "$LINTER" in
      eslint)
        if [ -f "$DIR/package.json" ] \
          && command -v jq >/dev/null 2>&1 \
          && jq -e '.eslintConfig // empty' "$DIR/package.json" >/dev/null 2>&1; then
          echo "$DIR"
          exit 0
        fi
        ;;
      prettier)
        if [ -f "$DIR/package.json" ] \
          && command -v jq >/dev/null 2>&1 \
          && jq -e '.prettier // empty' "$DIR/package.json" >/dev/null 2>&1; then
          echo "$DIR"
          exit 0
        fi
        ;;
      ruff)
        if [ -f "$DIR/pyproject.toml" ] \
          && grep -qE '^\[tool\.ruff' "$DIR/pyproject.toml" 2>/dev/null; then
          echo "$DIR"
          exit 0
        fi
        ;;
    esac

    # git ルートで打ち切り (.git はディレクトリまたは worktree のファイル)
    if [ -e "$DIR/.git" ]; then
      break
    fi
  fi

  PARENT=$(dirname "$DIR")
  if [ "$PARENT" = "$DIR" ]; then
    break
  fi
  DIR="$PARENT"
done

exit 0
