#!/usr/bin/env bash
# block-commit-lint.sh
#
# PreToolUse / Bash で `git commit` を検出し、commit 対象になるファイルを
# ESLint / Ruff に流して lint する。エラーがあれば commit を deny する。
#
# 本フックは Bash ツールの **実行前** に発火するため、同一コマンドの `git add`
# / `commit -a` がまだ走っていない時点で index を見ても lint をすり抜ける。
# これを避けるため、コマンド文字列を見て `git add` / `git stage` / `commit -a`
# / `--all` / `commit <pathspec>` を検出した場合 (HAS_STAGING=1) は staged
# だけでなく working tree の変更 (modified + untracked) も lint 対象に含め、
# ソースは working tree を読む。
#
# 必要: bash 4+ (連想配列 / nameref を利用)。macOS 標準 /bin/bash は 3.2 で
# 動作しないため、版が低い場合は gracefully skip する (lint せず通す)。

if (( BASH_VERSINFO[0] < 4 )); then
  printf '[auto-lint-check] block-commit-lint requires bash 4+. found %s. skip\n' "$BASH_VERSION" >&2
  exit 0
fi

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

# 本フック発火時点では、同一コマンド内の `git add` / `git stage` がまだ走って
# おらず、`git commit -a` / `--all` の自動 stage や `git commit <pathspec>`
# (pathspec form: 引数で指定したパスを working tree からそのまま commit) も
# 未実行。staged だけ見ると lint をすり抜けるため、これらの兆候を検出した
# 場合 (HAS_STAGING=1) は working tree の変更 (modified + untracked) も lint
# 対象に含め、ソースを working tree から読む。
#
# 検出は Python の shlex (punctuation_chars=";&|") でシェルトークン化した
# 結果に対して行う (`lib/parse-commit-command.py` 参照)。`-m "msg with git add"`
# のような commit message 内の文字列を staging 操作として誤検出しないため、
# bash の `case` / `=~` で生コマンドを scan することは避けている。
#
# 過検出 (commit に含めない予定の編集まで lint) は許容する設計トレードオフ。
# 失敗時は安全側で HAS_STAGING=1。
HAS_STAGING=0
if command -v python3 >/dev/null 2>&1; then
  python3 "$SCRIPT_DIR/lib/parse-commit-command.py" "$COMMAND"
  PY_RC=$?
  case "$PY_RC" in
    0|2) HAS_STAGING=1 ;;  # 2 は parse failure: 安全側で有効化
    3)
      # `-C` / `--git-dir` / `--work-tree` で別 repo に commit する形式。
      # 本フックは cwd repo を見るため、silent に別 repo を lint する経路を
      # 避けて何もせず通す (警告は stderr に出す)。
      log_warn "block-commit-lint: git global option (-C / --git-dir / --work-tree) で repo を切り替える commit はサポート対象外。skip"
      exit 0
      ;;
    4)
      # 初期の COMMIT_INVOCATION_REGEX が quote 非対応のため、`echo "; git
      # commit"` のような quoted 文字列に誤マッチした場合に shlex parser
      # が「commit subcommand 不在」を返す。実コマンドは git commit ではない
      # ので何もせず通す。
      exit 0
      ;;
  esac
fi

# Lint 対象ファイルを単一の assoc array で管理する。値は source の集合を
# `staged` / `working` / `staged working` の形で持つ。dual-membership 時には
# 両ソースを別個に lint してどちらか失敗で deny する:
#   - 元から staged で dirty → staged lint で検出 (再 stage されない場合に
#     こちらが committed される)
#   - working tree で変更されて新たに staged される予定 → working tree lint
#     で検出
declare -A FILE_SOURCES

add_source() {
  local f="$1" src="$2"
  # space delimiter で token 単位の完全一致を取る (substring 誤マッチ防止)。
  case " ${FILE_SOURCES[$f]:-} " in
    *" $src "*) ;;
    "  ")       FILE_SOURCES[$f]="$src" ;;
    *)          FILE_SOURCES[$f]+=" $src" ;;
  esac
}

while IFS= read -r -d '' f; do
  add_source "$f" staged
done < <(git diff --cached --name-only --diff-filter=ACMR -z 2>/dev/null)

if [ "$HAS_STAGING" -eq 1 ]; then
  while IFS= read -r -d '' f; do
    add_source "$f" working
  done < <(git diff --name-only --diff-filter=ACMR -z 2>/dev/null)
  while IFS= read -r -d '' f; do
    add_source "$f" working
  done < <(git ls-files --others --exclude-standard -z 2>/dev/null)
fi

[ ${#FILE_SOURCES[@]} -gt 0 ] || exit 0

# Pass 1: ファイルを (linter, config-root) でグルーピングする。Pass 2 で
# linter 解決 (resolve_eslint / resolve_ruff は `pnpm exec --version` 等の
# 子プロセスを起こす) を root あたり 1 回に抑えるため。`find_config_root`
# の結果も dirname 単位でキャッシュする (同じ dir のファイルは同じ root)。
declare -A CFG_ROOT_CACHE
declare -A FILES_BY_KEY

# FILES_BY_KEY entry separator: 制御文字 (US, \x1f) を使うことで path と
# source field の区切りを衝突なく行える (path に TAB が含まれる場合への保険)。
FS_SEP=$'\x1f'
RS_SEP=$'\n'

resolve_root_cached() {
  local file="$1" linter="$2"
  local key
  key="${linter}:$(dirname "$file")"
  if [ -z "${CFG_ROOT_CACHE[$key]+x}" ]; then
    CFG_ROOT_CACHE[$key]=$(find_config_root "$file" "$linter")
  fi
  printf '%s' "${CFG_ROOT_CACHE[$key]}"
}

for REL_PATH in "${!FILE_SOURCES[@]}"; do
  if is_js_like "$REL_PATH"; then
    LINTER=eslint
  elif is_python "$REL_PATH"; then
    LINTER=ruff
  else
    continue
  fi
  ROOT=$(resolve_root_cached "$REL_PATH" "$LINTER")
  [ -n "$ROOT" ] || continue
  for SRC in ${FILE_SOURCES[$REL_PATH]}; do
    FILES_BY_KEY["${LINTER}|${ROOT}"]+="$REL_PATH$FS_SEP$SRC$RS_SEP"
  done
done

# (linter, root) ごとに linter binary を 1 回だけ解決し、配下のファイルを
# 順に lint する。LINTER_LABEL は LINTER → 表示名のマッピング。
declare -A LINTER_LABEL=(
  [eslint]=ESLint
  [ruff]=Ruff
)
HAS_ERROR=0
COMBINED_OUTPUT=""

for KEY in "${!FILES_BY_KEY[@]}"; do
  LINTER="${KEY%%|*}"
  ROOT="${KEY#*|}"
  LABEL="${LINTER_LABEL[$LINTER]}"

  case "$LINTER" in
    eslint) resolve_eslint "$ROOT" || { log_warn "eslint config が $ROOT にあるが eslint バイナリが見つからない。skip"; continue; } ;;
    ruff)   resolve_ruff           || { log_warn "ruff config が $ROOT にあるが ruff バイナリが見つからない。skip";   continue; } ;;
  esac

  while IFS="$FS_SEP" read -r REL_PATH SOURCE; do
    [ -n "$REL_PATH" ] || continue
    # Lint 対象ソース: staged → `git show :path`, working → working tree。
    # `$()` の trailing newline strip を避けるため `&& printf X` センチネル
    # で末尾改行を保持する (ESLint `eol-last` / Ruff W292 の false positive 防止)。
    case "$SOURCE" in
      staged)
        CONTENT_PADDED=$(git show ":$REL_PATH" 2>/dev/null && printf X)
        SRC_LABEL="staged"
        ;;
      working)
        [ -f "$REL_PATH" ] || continue
        CONTENT_PADDED=$(cat "$REL_PATH" 2>/dev/null && printf X)
        SRC_LABEL="working tree"
        ;;
      *) continue ;;
    esac
    [ -n "$CONTENT_PADDED" ] || continue
    LINT_CONTENT="${CONTENT_PADDED%X}"
    ABS_PATH=$(normalize_path "$REL_PATH") || continue

    case "$LINTER" in
      eslint)
        LINT_OUT=$( (cd "$ROOT" && printf '%s' "$LINT_CONTENT" | "${ESLINT_CMD[@]}" --stdin --stdin-filename "$ABS_PATH") 2>&1 )
        RC=$?
        ;;
      ruff)
        LINT_OUT=$( (cd "$ROOT" && printf '%s' "$LINT_CONTENT" | "${RUFF_CMD[@]}" check --stdin-filename "$ABS_PATH" -) 2>&1 )
        RC=$?
        ;;
    esac

    if [ "$RC" -ne 0 ]; then
      HAS_ERROR=1
      COMBINED_OUTPUT+=$'--- '"$REL_PATH"$' ('"$LABEL, $SRC_LABEL"$') ---\n'"$LINT_OUT"$'\n\n'
    fi
  done <<< "${FILES_BY_KEY[$KEY]}"
done

if [ "$HAS_ERROR" -eq 1 ]; then
  REASON=$(printf '%s\n' \
    "git commit を中断しました。commit 対象ファイルに lint エラーがあります。" \
    "本体のコードを修正してから再度 commit してください。" \
    "" \
    "$COMBINED_OUTPUT")
  emit_deny "$REASON"
fi

exit 0
