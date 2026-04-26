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

# hook 入力 JSON から file_path を取り出して realpath -m で正規化する。
# 取得できなければ非 0 を返す。
extract_file_path() {
  local input="$1"
  local fp
  fp=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
  if [ -z "$fp" ]; then
    return 1
  fi
  realpath -m "$fp" 2>/dev/null
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

# eslint バイナリ呼び出し方法を解決する。検出順は以下の通り:
#   1. <root>/node_modules/.bin/eslint
#   2. pnpm exec eslint
#   3. npx --no-install eslint
#   4. グローバル PATH の eslint
# 見つかった起動コマンドを stdout に出力 (空白区切り)。見つからなければ何も出力せず非 0。
resolve_eslint() {
  local root="$1"
  if [ -x "$root/node_modules/.bin/eslint" ]; then
    echo "$root/node_modules/.bin/eslint"
    return 0
  fi
  if command -v pnpm >/dev/null 2>&1 \
    && (cd "$root" && pnpm exec eslint --version) >/dev/null 2>&1; then
    echo "pnpm exec eslint"
    return 0
  fi
  if command -v npx >/dev/null 2>&1 \
    && (cd "$root" && npx --no-install eslint --version) >/dev/null 2>&1; then
    echo "npx --no-install eslint"
    return 0
  fi
  if command -v eslint >/dev/null 2>&1; then
    echo "eslint"
    return 0
  fi
  return 1
}

# prettier の起動コマンドを解決する。eslint と同等のロジック。
resolve_prettier() {
  local root="$1"
  if [ -x "$root/node_modules/.bin/prettier" ]; then
    echo "$root/node_modules/.bin/prettier"
    return 0
  fi
  if command -v pnpm >/dev/null 2>&1 \
    && (cd "$root" && pnpm exec prettier --version) >/dev/null 2>&1; then
    echo "pnpm exec prettier"
    return 0
  fi
  if command -v npx >/dev/null 2>&1 \
    && (cd "$root" && npx --no-install prettier --version) >/dev/null 2>&1; then
    echo "npx --no-install prettier"
    return 0
  fi
  if command -v prettier >/dev/null 2>&1; then
    echo "prettier"
    return 0
  fi
  return 1
}

# ruff の起動コマンドを解決する。uvx ruff > PATH ruff の優先順位。
resolve_ruff() {
  if command -v uvx >/dev/null 2>&1; then
    echo "uvx ruff"
    return 0
  fi
  if command -v ruff >/dev/null 2>&1; then
    echo "ruff"
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
  local root bin rc
  root=$(find_config_root "$file" eslint)
  if [ -z "$root" ]; then
    return 0
  fi
  bin=$(resolve_eslint "$root")
  if [ -z "$bin" ]; then
    log_warn "eslint config が $root にあるが eslint バイナリが見つからない。skip"
    return 0
  fi
  LINTER_OUTPUT=$( (cd "$root" && printf '%s' "$content" | $bin --stdin --stdin-filename "$file") 2>&1 )
  rc=$?
  return $rc
}

# 予測内容を Ruff の check に流して lint する。
run_ruff_check_stdin() {
  local file="$1"
  local content="$2"
  local root bin rc
  root=$(find_config_root "$file" ruff)
  if [ -z "$root" ]; then
    return 0
  fi
  bin=$(resolve_ruff)
  if [ -z "$bin" ]; then
    log_warn "ruff config が $root にあるが ruff バイナリが見つからない。skip"
    return 0
  fi
  LINTER_OUTPUT=$( (cd "$root" && printf '%s' "$content" | $bin check --stdin-filename "$file" -) 2>&1 )
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
