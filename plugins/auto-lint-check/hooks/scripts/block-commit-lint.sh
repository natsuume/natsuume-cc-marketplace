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
# 未実行。staged だけ見ると lint をすり抜けるため、コマンド文字列からこれら
# の兆候を検出した場合 (HAS_STAGING=1) は working tree の変更 (modified +
# untracked) も lint 対象に含め、ソースを working tree から読む。
#
# pathspec 検出には Python の `shlex` でクォート対応トークン化を行う
# (`-m "long message"` を正しく扱うため)。失敗時は安全側で HAS_STAGING=1。
#
# 過検出 (commit に含めない予定の編集まで lint) は許容する設計トレードオフ。
HAS_STAGING=0
case "$COMMAND" in
  *"git add"*|*"git stage"*) HAS_STAGING=1 ;;
esac
if [[ "$COMMAND" =~ commit[^\;\&\|]*[[:space:]]-a[^[:space:]]*([[:space:]]|;|\&|\||$) ]] \
  || [[ "$COMMAND" =~ commit[^\;\&\|]*[[:space:]]--all([[:space:]]|;|\&|\||$) ]]; then
  HAS_STAGING=1
fi
if [ "$HAS_STAGING" -eq 0 ] && command -v python3 >/dev/null 2>&1; then
  # shlex.shlex に punctuation_chars=";&|" を渡してシェルセパレータを独立
  # トークン化する (`shlex.split` は `ok;` のように連結したまま返すため不適)。
  # `commit` 以降の位置引数 (pathspec) を検出する。
  # exit 0 → pathspec あり、1 → 無し、それ以外 → 解析失敗。
  if python3 - "$COMMAND" <<'PY'; then
import shlex, sys
lex = shlex.shlex(sys.argv[1], posix=True, punctuation_chars=";&|")
lex.whitespace_split = True
try:
    toks = list(lex)
except ValueError:
    sys.exit(2)
VALUE_FLAGS = {
    "-m", "--message", "-F", "--file", "-t", "--template",
    "-c", "--reedit-message", "-C", "--reuse-message",
    "-S", "--gpg-sign", "--author", "--date", "--cleanup",
    "--fixup", "--squash", "--trailer",
}
# `-o` (`--only`) / `-i` (`--include`) は flag に続く pathspec を working tree
# から取り込む。出現した時点で pathspec mode 確定。
PATHSPEC_MODE_FLAGS = {"-o", "--only", "-i", "--include"}
# punctuation_chars でクラスタになった `&&` / `||` / `;` / `&` / `|` の全て
SEPARATORS = {";", "&&", "||", "|", "&"}
i = 0
n = len(toks)
while i < n:
    if toks[i] != "commit":
        i += 1
        continue
    j = i + 1
    expect_val = False
    while j < n:
        t = toks[j]
        if t in SEPARATORS:
            break
        if expect_val:
            expect_val = False
        elif t == "--":
            sys.exit(0)
        elif t in PATHSPEC_MODE_FLAGS:
            sys.exit(0)
        elif t in VALUE_FLAGS:
            expect_val = True
        elif t.startswith("-"):
            pass
        else:
            sys.exit(0)
        j += 1
    i = j
sys.exit(1)
PY
    HAS_STAGING=1
  fi
fi

# Lint 対象ファイルを 2 つの set で管理する: IS_STAGED (現 index にある =
# staged blob を lint) と IS_WT (working tree で変更/未追跡 = working tree
# を lint)。両方に属するファイルは両ソースを lint し、どちらかが失敗したら
# deny する。これにより
#   - 元から staged で dirty → staged lint で検出 (再 stage されない場合に
#     こちらが committed される)
#   - working tree で変更されて新たに staged される予定 → working tree lint
#     で検出
# の両方を取りこぼさない。
declare -A IS_STAGED
declare -A IS_WT
FILES_TO_LINT=()
declare -A SEEN_FILES

add_file() {
  local f="$1" set_name="$2"
  case "$set_name" in
    staged) IS_STAGED[$f]=1 ;;
    wt)     IS_WT[$f]=1 ;;
  esac
  if [ -z "${SEEN_FILES[$f]+x}" ]; then
    SEEN_FILES[$f]=1
    FILES_TO_LINT+=("$f")
  fi
}

while IFS= read -r -d '' f; do
  add_file "$f" staged
done < <(git diff --cached --name-only --diff-filter=ACMR -z 2>/dev/null)

if [ "$HAS_STAGING" -eq 1 ]; then
  while IFS= read -r -d '' f; do
    add_file "$f" wt
  done < <(git diff --name-only --diff-filter=ACMR -z 2>/dev/null)
  while IFS= read -r -d '' f; do
    add_file "$f" wt
  done < <(git ls-files --others --exclude-standard -z 2>/dev/null)
fi

[ ${#FILES_TO_LINT[@]} -gt 0 ] || exit 0

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

for REL_PATH in "${FILES_TO_LINT[@]}"; do
  if is_js_like "$REL_PATH"; then
    LINTER=eslint
  elif is_python "$REL_PATH"; then
    LINTER=ruff
  else
    continue
  fi
  ROOT=$(resolve_root_cached "$REL_PATH" "$LINTER")
  [ -n "$ROOT" ] || continue
  # 各 source ごとに別エントリ。dual-membership は両方 lint される。エントリ
  # は <path>\t<source>\n 形式 (path に TAB が含まれることはまず無いと想定)。
  if [ -n "${IS_STAGED[$REL_PATH]+x}" ]; then
    FILES_BY_KEY["${LINTER}|${ROOT}"]+="$REL_PATH"$'\t'"staged"$'\n'
  fi
  if [ -n "${IS_WT[$REL_PATH]+x}" ]; then
    FILES_BY_KEY["${LINTER}|${ROOT}"]+="$REL_PATH"$'\t'"working"$'\n'
  fi
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

  while IFS=$'\t' read -r REL_PATH SOURCE; do
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
