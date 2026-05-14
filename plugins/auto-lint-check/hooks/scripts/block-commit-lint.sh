#!/bin/bash
# block-commit-lint.sh
#
# Bash 経由で `git commit` が実行される直前に PreToolUse でフックし、staged
# ファイルを lint してエラーがあれば deny する。
#
# 旧 auto-lint-check.sh は PreToolUse の Edit/Write/MultiEdit 単位で発火する
# ため、「中間状態が lint clean にならない一連の編集」が deny で stuck する
# 問題があった (README v0.1.1 の編集単位の制約を参照)。本フックは粒度を
# commit 単位に上げることでこの制約を解消する。代償としてフィードバックは
# commit 時まで遅延する。
#
# 検出対象:
#   - カレントブランチで実行される `git commit` 系コマンド
#   - `git -C dir commit ...` や `cd /other && git commit` のような cwd を
#     切り替える形式は対象外 (cwd の git に対してのみ lint する。invocation の
#     検出には引っかかるが、その場合も lint は cwd 基準で行う)
#
# Lint 対象は staged 内容 (`git show :path`)。working tree の未 staged 変更は
# 巻き込まない。これは `git commit -a` で working tree から取り込まれる変更が
# まだ staged になっていないタイミングで本フックが発火するため、`-a` でだけ
# untracked な lint 抜けが起きる可能性がある (README に edge case として記載)。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

INPUT=$(cat)

# 高速パス: jq 起動前に粗フィルタで抜ける。
case "$INPUT" in
  *commit*) ;;
  *) exit 0 ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

TOOL_NAME=$(extract_tool_name "$INPUT")
[ "$TOOL_NAME" = "Bash" ] || exit 0

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -n "$COMMAND" ] || exit 0

# 行継続 `\<改行>` を空白に、real newline を `;` に正規化する。
# block-default-branch-commit.sh と同じ前処理。
COMMAND="${COMMAND//$'\\\n'/ }"
COMMAND="${COMMAND//$'\n'/;}"

# `git commit` invocation を正規表現で検出する。
# block-default-branch-commit.sh の COMMIT_INVOCATION_REGEX と同じ構造。
OPT='-[^[:space:];&|]+'
OPT_ARG='([[:space:]]+[^-[:space:];&|][^[:space:];&|]*)?'
ENV_VAR_PREFIX='([A-Za-z_][A-Za-z0-9_]*=[^[:space:];&|]*[[:space:]]+)*'
COMMIT_INVOCATION_REGEX="(^|[;&|])[[:space:]]*${ENV_VAR_PREFIX}git[[:space:]]+(${OPT}${OPT_ARG}[[:space:]]+)*commit([[:space:]]|\$)"

if ! [[ "$COMMAND" =~ $COMMIT_INVOCATION_REGEX ]]; then
  exit 0
fi

# git リポジトリ内でなければ何もせずに通す。`git diff --cached --name-only` は
# repo root 相対のパスを返すため、find_config_root / linter 起動の前提を揃える
# べく以降の処理を repo root を cwd として実行する (sub-directory で commit を
# 実行している場合の path 解釈ズレを避ける)。
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi
cd "$REPO_ROOT" || exit 0

# staged ファイルのリストを取得。D (削除) は対象外、R (rename) は新名で取得。
STAGED_FILES=()
while IFS= read -r -d '' f; do
  STAGED_FILES+=("$f")
done < <(git diff --cached --name-only --diff-filter=ACMR -z 2>/dev/null)

if [ ${#STAGED_FILES[@]} -eq 0 ]; then
  exit 0
fi

HAS_ERROR=0
COMBINED_OUTPUT=""

# 1 ファイルずつ staged 内容を取り出して lint。`git show :path` は staged
# blob の内容を stdout に返す。`run_*_stdin` ヘルパーは setting ファイル探索と
# linter 解決を内部で行う。設定ファイルが見つからなければスキップ扱い (返り値 0)
# になるため、JS/TS or Python だが lint 設定が無いリポジトリでは何も起きない。
for REL_PATH in "${STAGED_FILES[@]}"; do
  if is_js_like "$REL_PATH"; then
    LINTER_NAME="ESLint"
  elif is_python "$REL_PATH"; then
    LINTER_NAME="Ruff"
  else
    continue
  fi

  # `git show :path` は末尾改行を保持する。&& printf X は付けない (現物が
  # 末尾改行で終わっていればそのまま eol-last をクリアする)。
  STAGED_CONTENT=$(git show ":$REL_PATH" 2>/dev/null)
  GIT_SHOW_RC=$?
  if [ $GIT_SHOW_RC -ne 0 ]; then
    continue
  fi

  ABS_PATH=$(normalize_path "$REL_PATH") || continue

  if [ "$LINTER_NAME" = "ESLint" ]; then
    if ! run_eslint_stdin "$ABS_PATH" "$STAGED_CONTENT"; then
      HAS_ERROR=1
      COMBINED_OUTPUT+=$'--- '"$REL_PATH"$' (ESLint) ---\n'"$LINTER_OUTPUT"$'\n\n'
    fi
  else
    if ! run_ruff_check_stdin "$ABS_PATH" "$STAGED_CONTENT"; then
      HAS_ERROR=1
      COMBINED_OUTPUT+=$'--- '"$REL_PATH"$' (Ruff) ---\n'"$LINTER_OUTPUT"$'\n\n'
    fi
  fi
done

if [ "$HAS_ERROR" -eq 1 ]; then
  REASON=$(printf '%s\n' \
    "git commit を中断しました。staged ファイルに lint エラーがあります。" \
    "本体のコードを修正してから再度 commit してください。" \
    "" \
    "$COMBINED_OUTPUT")
  emit_deny "$REASON"
fi

exit 0
