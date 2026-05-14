#!/bin/bash
# common.sh - auto-lint-check 用の共通ヘルパー。各 hook スクリプトから source 経由で利用する。

AUTO_LINT_CHECK_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# stderr へ警告メッセージを出力する。verbose 抑制は呼び出し側で制御する想定。
log_warn() {
  echo "[auto-lint-check] $*" >&2
}

# hook 入力 JSON から tool_name を取り出す。
extract_tool_name() {
  local input="$1"
  printf '%s' "$input" | jq -r '.tool_name // empty'
}

# パスを絶対パスへ正規化する。`realpath -m` の代替として python3 の
# os.path.abspath を使う (BSD/macOS の realpath には -m が無いため可搬性目的)。
# 失敗時は何も出力せず非 0 を返す。
normalize_path() {
  local path="$1"
  if [ -z "$path" ]; then
    return 1
  fi
  python3 -c 'import os, sys; sys.stdout.write(os.path.abspath(sys.argv[1]))' "$path" 2>/dev/null
}

# hook 入力 JSON から file_path を取り出して絶対パスに正規化する。
# 取得できなければ非 0 を返す。
extract_file_path() {
  local input="$1"
  local fp
  fp=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
  if [ -z "$fp" ]; then
    return 1
  fi
  normalize_path "$fp"
}

# 拡張子から JS/TS ファイルかを判定する。
is_js_like() {
  case "$1" in
    *.js|*.jsx|*.ts|*.tsx|*.mjs|*.cjs) return 0 ;;
    *) return 1 ;;
  esac
}

# 拡張子から Python ファイルかを判定する。
is_python() {
  case "$1" in
    *.py) return 0 ;;
    *) return 1 ;;
  esac
}

# 設定ファイル探索を find-config-root.sh に委譲する。
# Usage: find_config_root <file> <linter>
find_config_root() {
  bash "$AUTO_LINT_CHECK_LIB_DIR/find-config-root.sh" "$1" "$2"
}

# 解決された linter の起動コマンドを格納するグローバル配列。空白を含むパスでも
# 安全に扱えるよう、文字列ではなく配列で返す。
ESLINT_CMD=()
PRETTIER_CMD=()
RUFF_CMD=()

# eslint バイナリ呼び出し方法を解決して ESLINT_CMD 配列に格納する。検出順は:
#   1. <root>/node_modules/.bin/eslint
#   2. pnpm exec eslint
#   3. npx --no-install eslint
#   4. グローバル PATH の eslint
# 見つからなければ非 0。
resolve_eslint() {
  local root="$1"
  ESLINT_CMD=()
  if [ -x "$root/node_modules/.bin/eslint" ]; then
    ESLINT_CMD=("$root/node_modules/.bin/eslint")
    return 0
  fi
  if command -v pnpm >/dev/null 2>&1 \
    && (cd "$root" && pnpm exec eslint --version) >/dev/null 2>&1; then
    ESLINT_CMD=(pnpm exec eslint)
    return 0
  fi
  if command -v npx >/dev/null 2>&1 \
    && (cd "$root" && npx --no-install eslint --version) >/dev/null 2>&1; then
    ESLINT_CMD=(npx --no-install eslint)
    return 0
  fi
  if command -v eslint >/dev/null 2>&1; then
    ESLINT_CMD=(eslint)
    return 0
  fi
  return 1
}

# prettier の起動コマンドを PRETTIER_CMD に解決する。eslint と同等のロジック。
resolve_prettier() {
  local root="$1"
  PRETTIER_CMD=()
  if [ -x "$root/node_modules/.bin/prettier" ]; then
    PRETTIER_CMD=("$root/node_modules/.bin/prettier")
    return 0
  fi
  if command -v pnpm >/dev/null 2>&1 \
    && (cd "$root" && pnpm exec prettier --version) >/dev/null 2>&1; then
    PRETTIER_CMD=(pnpm exec prettier)
    return 0
  fi
  if command -v npx >/dev/null 2>&1 \
    && (cd "$root" && npx --no-install prettier --version) >/dev/null 2>&1; then
    PRETTIER_CMD=(npx --no-install prettier)
    return 0
  fi
  if command -v prettier >/dev/null 2>&1; then
    PRETTIER_CMD=(prettier)
    return 0
  fi
  return 1
}

# ruff の起動コマンドを RUFF_CMD に解決する。uvx ruff > PATH ruff の優先順位。
# uvx が PATH にあるだけでは不十分 (オフライン等で実行できないケースがある)
# ため、`uvx ruff --version` で実際に起動できるかを検証してから採用する。
# 失敗したら PATH の ruff にフォールバックする。
resolve_ruff() {
  RUFF_CMD=()
  if command -v uvx >/dev/null 2>&1 \
    && uvx ruff --version >/dev/null 2>&1; then
    RUFF_CMD=(uvx ruff)
    return 0
  fi
  if command -v ruff >/dev/null 2>&1; then
    RUFF_CMD=(ruff)
    return 0
  fi
  return 1
}

# 予測内容を ESLint stdin に流して lint する。
# 返り値:
#   0  lint 通過 / 設定ファイル未検出 / バイナリ未検出 (いずれもスキップ扱い)
#   1  lint がエラーを検出 ($LINTER_OUTPUT に詳細)
LINTER_OUTPUT=""
run_eslint_stdin() {
  local file="$1"
  local content="$2"
  local root rc
  root=$(find_config_root "$file" eslint)
  if [ -z "$root" ]; then
    return 0
  fi
  if ! resolve_eslint "$root"; then
    log_warn "eslint config が $root にあるが eslint バイナリが見つからない。skip"
    return 0
  fi
  LINTER_OUTPUT=$( (cd "$root" && printf '%s' "$content" | "${ESLINT_CMD[@]}" --stdin --stdin-filename "$file") 2>&1 )
  rc=$?
  return $rc
}

# 予測内容を Ruff の check に流して lint する。
run_ruff_check_stdin() {
  local file="$1"
  local content="$2"
  local root rc
  root=$(find_config_root "$file" ruff)
  if [ -z "$root" ]; then
    return 0
  fi
  if ! resolve_ruff; then
    log_warn "ruff config が $root にあるが ruff バイナリが見つからない。skip"
    return 0
  fi
  LINTER_OUTPUT=$( (cd "$root" && printf '%s' "$content" | "${RUFF_CMD[@]}" check --stdin-filename "$file" -) 2>&1 )
  rc=$?
  return $rc
}

# PreToolUse の deny レスポンスを stdout に出力して exit 0 で終了する。
# Usage: emit_deny "拒否理由のテキスト"
emit_deny() {
  local reason="$1"
  jq -n --arg reason "$reason" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
}

