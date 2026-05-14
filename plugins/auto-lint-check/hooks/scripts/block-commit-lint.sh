#!/bin/bash
# block-commit-lint.sh
#
# PreToolUse / Bash で `git commit` を検出し、staged blob (`git show :path`) を
# ESLint / Ruff に流して lint する。エラーがあれば commit を deny する。
#
# `git commit -a` は本フックの発火タイミングでは working tree がまだ stage
# されていないため lint 対象から漏れる。明示的に `git add` してから commit
# する運用を推奨 (README 参照)。

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

# 行継続 `\<改行>` を空白に、real newline を `;` に正規化する
# (block-default-branch-commit.sh と同じ前処理)。
COMMAND="${COMMAND//$'\\\n'/ }"
COMMAND="${COMMAND//$'\n'/;}"

# `git commit` invocation を正規表現で検出 (block-default-branch-commit.sh と
# 同じ構造)。OPT_ARG までを許容して `git -c user.email=... commit` のような
# global option 経由の呼び出しも拾う。
OPT='-[^[:space:];&|]+'
OPT_ARG='([[:space:]]+[^-[:space:];&|][^[:space:];&|]*)?'
ENV_VAR_PREFIX='([A-Za-z_][A-Za-z0-9_]*=[^[:space:];&|]*[[:space:]]+)*'
COMMIT_INVOCATION_REGEX="(^|[;&|])[[:space:]]*${ENV_VAR_PREFIX}git[[:space:]]+(${OPT}${OPT_ARG}[[:space:]]+)*commit([[:space:]]|\$)"

if ! [[ "$COMMAND" =~ $COMMIT_INVOCATION_REGEX ]]; then
  exit 0
fi

# `git diff --cached --name-only` は repo root 相対のパスを返すため、cwd を
# repo root に切り替えてから以降の処理を行う (sub-directory での commit 時の
# path 解釈ズレを回避)。
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
[ -n "$REPO_ROOT" ] || exit 0
cd "$REPO_ROOT" || exit 0

STAGED_FILES=()
while IFS= read -r -d '' f; do
  STAGED_FILES+=("$f")
done < <(git diff --cached --name-only --diff-filter=ACMR -z 2>/dev/null)

[ ${#STAGED_FILES[@]} -gt 0 ] || exit 0

# Pass 1: staged ファイルを (linter, config-root) でグルーピングする。Pass 2 で
# linter 解決 (resolve_eslint / resolve_ruff は `pnpm exec --version` 等の子
# プロセスを起こす) を root あたり 1 回に抑えるため。`find_config_root` の
# 結果も dirname 単位でキャッシュする (同じ dir のファイルは同じ root)。
declare -A CFG_ROOT_CACHE
declare -A FILES_BY_KEY

resolve_root_cached() {
  local file="$1" linter="$2"
  local key
  key="${linter}:$(dirname "$file")"
  if [ -z "${CFG_ROOT_CACHE[$key]+x}" ]; then
    CFG_ROOT_CACHE[$key]=$(find_config_root "$file" "$linter")
  fi
  printf '%s' "${CFG_ROOT_CACHE[$key]}"
}

for REL_PATH in "${STAGED_FILES[@]}"; do
  if is_js_like "$REL_PATH"; then
    LINTER=eslint
  elif is_python "$REL_PATH"; then
    LINTER=ruff
  else
    continue
  fi
  ROOT=$(resolve_root_cached "$REL_PATH" "$LINTER")
  [ -n "$ROOT" ] || continue
  FILES_BY_KEY["${LINTER}|${ROOT}"]+="$REL_PATH"$'\n'
done

# Pass 2: 各 (linter, root) で linter binary を 1 回だけ解決し、配下のファイル
# を順に lint する。
HAS_ERROR=0
COMBINED_OUTPUT=""

for KEY in "${!FILES_BY_KEY[@]}"; do
  LINTER="${KEY%%|*}"
  ROOT="${KEY#*|}"

  case "$LINTER" in
    eslint)
      resolve_eslint "$ROOT" || {
        log_warn "eslint config が $ROOT にあるが eslint バイナリが見つからない。skip"
        continue
      }
      LABEL=ESLint
      ;;
    ruff)
      resolve_ruff || {
        log_warn "ruff config が $ROOT にあるが ruff バイナリが見つからない。skip"
        continue
      }
      LABEL=Ruff
      ;;
  esac

  while IFS= read -r REL_PATH; do
    [ -n "$REL_PATH" ] || continue
    # `git show :path` は末尾改行を含むが、`$()` は trailing newline を strip
    # する。`&& printf X` のセンチネルで実 trailing newline を保持し、ESLint
    # `eol-last` / Ruff W292 の false positive を防ぐ。
    STAGED_PADDED=$(git show ":$REL_PATH" 2>/dev/null && printf X)
    [ -n "$STAGED_PADDED" ] || continue
    STAGED_CONTENT="${STAGED_PADDED%X}"
    ABS_PATH=$(normalize_path "$REL_PATH") || continue

    case "$LINTER" in
      eslint)
        LINT_OUT=$( (cd "$ROOT" && printf '%s' "$STAGED_CONTENT" | "${ESLINT_CMD[@]}" --stdin --stdin-filename "$ABS_PATH") 2>&1 )
        RC=$?
        ;;
      ruff)
        LINT_OUT=$( (cd "$ROOT" && printf '%s' "$STAGED_CONTENT" | "${RUFF_CMD[@]}" check --stdin-filename "$ABS_PATH" -) 2>&1 )
        RC=$?
        ;;
    esac

    if [ "$RC" -ne 0 ]; then
      HAS_ERROR=1
      COMBINED_OUTPUT+=$'--- '"$REL_PATH"$' ('"$LABEL"$') ---\n'"$LINT_OUT"$'\n\n'
    fi
  done <<< "${FILES_BY_KEY[$KEY]}"
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
