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

# git の -z 出力 (NUL 区切りのパス列) を read で受け、source ラベルを前置した
# NUL 区切り `<source>\t<rel_path>\0` レコードに変換して stdout に書き出す。
# `build-lint-plan.py` の入力形式と一致する。block-commit-lint.sh と
# post-commit-lint.sh が同じ実装を持っていたため共通化。
# Usage: <git -z output> | prepend_source_label <source-name> >> "$TMP_INPUT"
prepend_source_label() {
  local source="$1"
  while IFS= read -r -d '' f; do
    printf '%s\t%s\0' "$source" "$f"
  done
}

# --- 異常終了の可視化用 EXIT trap -------------------------------------------
# hook が想定外に「非ゼロ」で終了 (シェル展開の想定外失敗 / 一部の signal 等) した
# 場合に、ユーザの stderr に「hook が壊れた」ことを通知する。sibling の
# pre-push-review (lib/exit-trap.sh) と同型の可視化機構。
#
# 重要 (fail-closed ではない): block-commit-lint の deny セマンティクスは stdout の
# JSON が担い exit code ではないため、deny JSON を出す前にプロセスが異常死すると
# commit は allow される (= fail-open)。さらに SIGKILL / OOM では trap 自体が走らない
# ため、EXIT trap で「真の fail-closed」は達成できない。本 trap はあくまで「hook が
# 異常終了したことに気づけるようにする」可視化に限定する (#68)。真に fail-closed 化
# するには trap 内で『まだ deny を出していなければ安全側 deny を emit する』設計が要る
# が、SIGKILL/OOM は捕捉不能 + crash 経路は極めて低頻度のため過剰実装と判断している。
#
# tmpfile 掃除も同じ EXIT trap に集約する (bash の EXIT trap は 1 つしか持てず、後から
# trap を上書きすると tmpfile 掃除が消えるため)。掃除対象は AUTO_LINT_CHECK_CLEANUP_FILE
# に登録する (trap install 後に代入してよい; 空なら掃除しない)。
AUTO_LINT_CHECK_CLEANUP_FILE=""

# install_auto_lint_exit_trap が eval 経由で trap に焼き込む共通ハンドラ本体。
_auto_lint_check_exit_handler() {
  local exit_code=$?
  local tag="$1"
  local impact="$2"
  if [ -n "${AUTO_LINT_CHECK_CLEANUP_FILE:-}" ]; then
    rm -f "$AUTO_LINT_CHECK_CLEANUP_FILE"
  fi
  if [ "$exit_code" -ne 0 ]; then
    printf '[auto-lint-check/%s] 予期せぬエラーで hook が exit %s で終了しました。\n' \
      "$tag" "$exit_code" >&2
    printf '[auto-lint-check/%s] %s marketplace https://github.com/natsuume/natsuume-cc-marketplace に hook 実装の bug として報告してください。\n' \
      "$tag" "$impact" >&2
  fi
}

# install_auto_lint_exit_trap <tag> <impact>
#   <tag>    : hook を特定する短いラベル (stderr の `[auto-lint-check/<tag>]` に使う)
#   <impact> : 非ゼロ終了が起きた場合に何が壊れるかの 1 文
# trap の command 引数は trap 発火時に新規解釈される文字列なので、install 時の引数を
# `printf '%q'` で安全に quote してから trap command に焼き込む。caller の冒頭 (tmpfile
# 作成前) で 1 度だけ呼ぶこと。
install_auto_lint_exit_trap() {
  local tag_q impact_q
  tag_q=$(printf '%q' "$1")
  impact_q=$(printf '%q' "$2")
  # shellcheck disable=SC2064
  trap "_auto_lint_check_exit_handler $tag_q $impact_q" EXIT
}

