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
# あり連想配列を使えないため、本フックは実行不能になる。silently skip すると
# 「commit 直前 lint が走っているつもりで実は素通り」という silent failure
# になり最も危険なので、明示的に deny に倒す (fail closed)。利用者は
# Homebrew 等で bash 4+ を入れて PATH に置くか、本プラグインを無効化する
# 判断を取ることになる。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

if (( BASH_VERSINFO[0] < 4 )); then
  log_warn "block-commit-lint requires bash 4+. found $BASH_VERSION."
  emit_deny "auto-lint-check の block-commit-lint hook には bash 4+ が必要ですが、現在 bash $BASH_VERSION で実行されています。Homebrew 等で bash 4+ を導入して PATH の先頭に置くか、auto-lint-check プラグインを無効化してください。silently skip すると lint がすり抜けるため fail closed (deny) しています。"
fi

INPUT=$(cat)

# 高速パス: jq / python3 起動前の粗フィルタ。`git` と `commit` の両方を含む
# 入力 (= 実 commit invocation の必要条件) でなければ抜ける。
# 注: substring match なので `commit-graph` や `--commit-msg` のような関連
# 語も拾うが、後段の shlex parser が正確に判定するため害はない。
case "$INPUT" in
  *git*commit*) ;;
  *) exit 0 ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi
if ! command -v python3 >/dev/null 2>&1; then
  log_warn "block-commit-lint: python3 が見つかりません。skip"
  exit 0
fi

TOOL_NAME=$(extract_tool_name "$INPUT")
[ "$TOOL_NAME" = "Bash" ] || exit 0

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -n "$COMMAND" ] || exit 0

# 行継続 `\<改行>` を空白に、real newline を `;` に正規化する。shlex は
# 行継続を保持しないため、明示的に space 化する。
COMMAND="${COMMAND//$'\\\n'/ }"
COMMAND="${COMMAND//$'\n'/;}"

# `git diff --cached --name-only` は repo root 相対のパスを返すため、cwd を
# repo root に切り替えてから以降の処理を行う (sub-directory での commit 時の
# path 解釈ズレを回避)。
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
[ -n "$REPO_ROOT" ] || exit 0
cd "$REPO_ROOT" || exit 0

# `lib/parse-commit-command.py` が shlex でシェルトークン化したコマンドを解析し、
# exit code で以下を返す:
#   0  HAS_STAGING (working tree も lint 対象に含めるべき)
#   1  commit はあるが staging trigger なし (staged blob のみ lint)
#   2  parse failure (安全側で HAS_STAGING=1 に倒す)
#   3  repo override (`-C` / `--git-dir` / `--work-tree` / `GIT_DIR=` 等で cwd
#      と異なる repo に commit するため、silent に cwd を lint しないよう skip)
#   4  実 commit が走らない (commit subcommand 不在 / `echo "git commit"` の
#      ような非 command position の git / `--dry-run` / `--help` 等)。skip
#
# 過検出 (commit に含めない予定の編集まで lint) は許容する設計トレードオフ。
HAS_STAGING=0
python3 "$SCRIPT_DIR/lib/parse-commit-command.py" "$COMMAND"
PY_RC=$?
case "$PY_RC" in
  0|2) HAS_STAGING=1 ;;
  3)
    log_warn "block-commit-lint: repo を切り替える commit (-C / --git-dir / --work-tree / GIT_DIR= 等) はサポート対象外。skip"
    exit 0
    ;;
  4) exit 0 ;;
esac

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

# 設計判断: dual-membership (staged + working) のファイルは両方を lint する。
# `git add path && commit` のように当該 path が確実に再 stage されるケースでは
# 古い staged blob は committed されないため、staged 側の lint は over-detect
# (false-positive deny) になりうる。逆に `git add other && commit` のように
# 当該 path が再 stage されない場合は staged blob が committed されるため、
# working 側だけ lint すると真の lint エラーを見逃す (under-detect)。両者の
# 厳密な判別には `git add` の引数 pathspec resolution が必要。本実装では
# under-detect を避ける方を優先し、両ソースを lint する。dual-membership で
# false-positive deny が出た場合は commit 直前の re-stage を促す挙動として扱う。

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
