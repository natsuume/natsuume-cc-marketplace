#!/bin/bash
# isolated-commit-template.sh
# 利用者が env `CLAUDE_ISOLATED_GIT_ROOTS` で明示した隔離ルート配下の repo への commit を、
# commit 系 hook の検査から免除してよいかを判定する自己完結の判定器。
#
# 正本は git-guardrails (hooks/scripts/lib/isolated-commit-template.sh)。auto-lint-check
# (hooks/scripts/lib/isolated-commit-template.sh) は byte-identical なコピーを保つ。
# 他の lib に依存しない (単独で source して使える)。
#
# ## 公開関数
#
#   isolated_commit_template_exempts <command>
#     免除する場合だけ 0 を返す (それ以外は 1)。<command> は hook が受け取った正規化前の
#     Bash コマンド文字列。次の全てを満たす場合に免除する:
#       1. env `CLAUDE_ISOLATED_GIT_ROOTS` に有効な許可ルートが 1 つ以上ある
#       2. hook プロセスの環境に _ICT_FORBIDDEN_ENV_NAMES のいずれも設定されていない
#       3. <command> 全体が免除テンプレート T1 / T2 のいずれかに完全一致する
#          (isolated_commit_template_target)
#       4. テンプレートの対象 dir (`-C` の値) が対象 repo 検査を満たす
#          (_ict_repo_is_within_roots)
#
#   isolated_commit_template_target <command>
#     <command> 全体が免除テンプレートに完全一致すれば、対象 dir (`-C` の値) を stdout に
#     出力して 0 を返す。一致しなければ何も出力せず 1 を返す。字句だけで判定し、
#     ファイルシステムや env は見ない。
#
# ## 免除テンプレート
#
#   T1: git -C <ABS> commit <COMMIT_ARGS>
#   T2: git -C <ABS> add <ADD_ARGS> && git -C <ABS> commit <COMMIT_ARGS>
#       (2 つの <ABS> は文字列として完全一致)
#
# ## 字句規則
#
#   - 入力は 1 行 (LF / CR を含まない)。語の区切りは ASCII 空白 (スペース / タブ) の
#     1 個以上。先頭・末尾の空白は許容する
#   - 区切り演算子は T2 の `&&` 1 つだけ (前後に空白が必要)。それ以外のシェルメタ文字
#     (`;` `|` `&` `(` `)` `{` `}` `<` `>` `#` `$` バッククォート `\` 等) は、quote 外に
#     あれば不一致
#   - 構造語 (`git` / `-C` / `commit` / `add` / `&&`) と各フラグは quote なしの語で、
#     文字列として完全一致すること (`/usr/bin/git`・環境変数代入の前置・`git` の直後の
#     `-C <ABS>` 以外の global option は不可)
#   - <ABS>: quote なしの語で `^/[A-Za-z0-9._/-]+$` に一致し、`/./`・`/../`・末尾 `/..`・
#     末尾 `/.` を含まないもの
#   - LIT (commit メッセージの語): 次のいずれか 1 形式だけから成る語 (形式の結合は不可)
#       single quote: `'...'` (内部に `'` を含まない)
#       double quote: `"..."` (内部に `"` `$` バッククォート `\` `!` を含まない)
#       bare: `^[A-Za-z0-9._/:@%+=,-]+$`
#
# ## COMMIT_ARGS (1 個以上。順序自由。`-m` か `-F` が少なくとも 1 つ必要)
#
#   - `-m` LIT (別の語)
#   - `-F` <ABS> (別の語)
#   - フラグ単体: `--allow-empty` / `--allow-empty-message` / `-q` / `--quiet` / `-a` /
#     `--all` / `--no-verify` / `-n`
#
# ## ADD_ARGS (1 個以上)
#
#   - `-A` / `--all` / `.`
#   - 相対パスの bare 語: `^[A-Za-z0-9._/-]+$` で、`-` / `/` で始まらず `..` 要素を
#     含まないもの
#
# ## 対象 repo 検査 (_ict_repo_is_within_roots)
#
#   次がいずれも許可ルート配下 (パス境界での前方一致。ルート自身を含む) にあること:
#     - <ABS> の canonical 実パス (`cd -P && pwd -P`)
#     - <ABS> で実行した `git rev-parse --git-common-dir` / `--git-dir` の canonical 実パス
#     - common-dir 直下の `refs` / `objects` / `HEAD` / `packed-refs` / `logs` と、
#       worktree 固有 git dir 直下の `HEAD` / `index` のうち存在するものの実体
#       (ディレクトリは canonical 実パス。ファイルは自体が symlink なら不可、それ以外は
#       親 dir の canonical 実パス)
#     - HEAD が指す branch ref の格納先ディレクトリ (未作成なら存在する最も近い祖先)。
#       ref ファイル自体が symlink なら不可。detached HEAD なら検査しない
#   objects / logs 配下の深い階層の symlink は検査しない (branch は動かないため)。
#
# ## 許可ルート
#
#   env `CLAUDE_ISOLATED_GIT_ROOTS` は PATH と同じコロン区切りの絶対パス列。空文字・相対
#   パス・存在しないディレクトリの entry は無視し、有効な entry は canonical 実パスで比較
#   する。未設定・空、または有効な entry が無ければ免除しない。
#
# ## fail-closed
#
#   字句解析・パス解決・git 呼び出しのいずれかが失敗した場合は免除しない。
#
# ## 互換性
#
#   macOS 標準 bash 3.2 と Linux bash の両方で動く構文だけを使う (`declare -A` /
#   `mapfile` / nameref / `${x,,}` / `realpath` / `readlink -f` を使わない)。

# hook プロセスの環境に設定されていれば免除しない環境変数 (commit 先 repo・object
# 格納先・config の解決を変えるもの)。
_ICT_FORBIDDEN_ENV_NAMES="GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT"

# 字句規則の正規表現。bash 3.2 / 4 以降で `=~` の右辺 quote の扱いが異なるため、変数に
# 入れて quote なしで展開する。
_ICT_BARE_WORD_RE='^[A-Za-z0-9._/:@%+=,-]+$'
_ICT_ABS_PATH_RE='^/[A-Za-z0-9._/-]+$'
_ICT_ADD_PATH_RE='^[A-Za-z0-9._/-]+$'

# 字句解析の結果。_ict_tokenize が設定する (kind は B = bare / S = single quote /
# D = double quote / O = 演算子 `&&`、value は quote を外した内容)。
_ICT_KINDS=()
_ICT_VALUES=()

# 引数: <command>
# 戻り値: 0 = 字句規則どおりに語へ分割できた (結果を _ICT_KINDS / _ICT_VALUES に設定) /
#         1 = 字句規則違反
_ict_tokenize() {
  local cmd="$1"
  local len=${#cmd}
  local i=0 start c value
  local nl=$'\n' cr=$'\r' tab=$'\t'
  _ICT_KINDS=()
  _ICT_VALUES=()

  case "$cmd" in
    *"$nl"*|*"$cr"*) return 1 ;;
  esac

  while [ "$i" -lt "$len" ]; do
    c="${cmd:$i:1}"
    if [ "$c" = " " ] || [ "$c" = "$tab" ]; then
      i=$((i+1))
      continue
    fi

    case "$c" in
      "'")
        start=$((i+1))
        i=$start
        while [ "$i" -lt "$len" ] && [ "${cmd:$i:1}" != "'" ]; do
          i=$((i+1))
        done
        [ "$i" -lt "$len" ] || return 1
        value="${cmd:$start:$((i-start))}"
        i=$((i+1))
        _ict_word_ends_at "$cmd" "$i" || return 1
        _ICT_KINDS+=("S")
        _ICT_VALUES+=("$value")
        ;;
      '"')
        start=$((i+1))
        i=$start
        while [ "$i" -lt "$len" ] && [ "${cmd:$i:1}" != '"' ]; do
          i=$((i+1))
        done
        [ "$i" -lt "$len" ] || return 1
        value="${cmd:$start:$((i-start))}"
        case "$value" in
          *'$'*|*'`'*|*\\*|*'!'*) return 1 ;;
        esac
        i=$((i+1))
        _ict_word_ends_at "$cmd" "$i" || return 1
        _ICT_KINDS+=("D")
        _ICT_VALUES+=("$value")
        ;;
      *)
        start=$i
        while [ "$i" -lt "$len" ]; do
          c="${cmd:$i:1}"
          [ "$c" = " " ] || [ "$c" = "$tab" ] && break
          i=$((i+1))
        done
        value="${cmd:$start:$((i-start))}"
        if [ "$value" = "&&" ]; then
          _ICT_KINDS+=("O")
        else
          [[ "$value" =~ $_ICT_BARE_WORD_RE ]] || return 1
          _ICT_KINDS+=("B")
        fi
        _ICT_VALUES+=("$value")
        ;;
    esac
  done
  return 0
}

# 引数: <command> <index>
# 戻り値: 0 = <index> が文字列末尾、または空白 (語の区切り) / 1 = それ以外 (quote の直後に
#         別の形式が続く結合)
_ict_word_ends_at() {
  local cmd="$1"
  local i="$2"
  local c tab=$'\t'
  [ "$i" -ge "${#cmd}" ] && return 0
  c="${cmd:$i:1}"
  [ "$c" = " " ] || [ "$c" = "$tab" ]
}

# 引数: <word>
# 戻り値: 0 = <ABS> の字句規則を満たす / 1 = それ以外
_ict_is_abs_path() {
  local word="$1"
  [[ "$word" =~ $_ICT_ABS_PATH_RE ]] || return 1
  case "$word" in
    */./*|*/../*|*/..|*/.) return 1 ;;
  esac
  return 0
}

# 引数: <word>
# 戻り値: 0 = ADD_ARGS の相対パス bare 語の規則を満たす / 1 = それ以外
_ict_is_add_path() {
  local word="$1"
  [[ "$word" =~ $_ICT_ADD_PATH_RE ]] || return 1
  case "$word" in
    -*|/*) return 1 ;;
  esac
  case "/$word/" in
    */../*) return 1 ;;
  esac
  return 0
}

# 引数: <index> <expected-value>
# 戻り値: 0 = _ICT_* の <index> 番目が quote なしの語で値が <expected-value> と一致 /
#         1 = それ以外
_ict_bare_is() {
  local index="$1"
  local expected="$2"
  [ "$index" -lt "${#_ICT_KINDS[@]}" ] || return 1
  [ "${_ICT_KINDS[$index]}" = "B" ] && [ "${_ICT_VALUES[$index]}" = "$expected" ]
}

# 引数: <index>
# stdout: `git -C <ABS>` の <ABS>
# 戻り値: 0 = <index> から `git -C <ABS>` の 3 語が並ぶ / 1 = それ以外
_ict_git_c_prefix_at() {
  local index="$1"
  _ict_bare_is "$index" "git" || return 1
  _ict_bare_is $((index+1)) "-C" || return 1
  [ $((index+2)) -lt "${#_ICT_KINDS[@]}" ] || return 1
  [ "${_ICT_KINDS[$((index+2))]}" = "B" ] || return 1
  _ict_is_abs_path "${_ICT_VALUES[$((index+2))]}" || return 1
  printf '%s' "${_ICT_VALUES[$((index+2))]}"
}

# 引数: <start> <end> (_ICT_* の半開区間 [start, end))
# 戻り値: 0 = 区間が COMMIT_ARGS の規則を満たす / 1 = それ以外
_ict_commit_args_ok() {
  local i="$1"
  local end="$2"
  local has_message=1
  [ "$i" -lt "$end" ] || return 1
  while [ "$i" -lt "$end" ]; do
    [ "${_ICT_KINDS[$i]}" = "B" ] || return 1
    case "${_ICT_VALUES[$i]}" in
      -m)
        [ $((i+1)) -lt "$end" ] || return 1
        [ "${_ICT_KINDS[$((i+1))]}" != "O" ] || return 1
        has_message=0
        i=$((i+2))
        ;;
      -F)
        [ $((i+1)) -lt "$end" ] || return 1
        [ "${_ICT_KINDS[$((i+1))]}" = "B" ] || return 1
        _ict_is_abs_path "${_ICT_VALUES[$((i+1))]}" || return 1
        has_message=0
        i=$((i+2))
        ;;
      --allow-empty|--allow-empty-message|-q|--quiet|-a|--all|--no-verify|-n)
        i=$((i+1))
        ;;
      *)
        return 1
        ;;
    esac
  done
  return "$has_message"
}

# 引数: <start> <end> (_ICT_* の半開区間 [start, end))
# 戻り値: 0 = 区間が ADD_ARGS の規則を満たす / 1 = それ以外
_ict_add_args_ok() {
  local i="$1"
  local end="$2"
  [ "$i" -lt "$end" ] || return 1
  while [ "$i" -lt "$end" ]; do
    [ "${_ICT_KINDS[$i]}" = "B" ] || return 1
    case "${_ICT_VALUES[$i]}" in
      -A|--all|.) ;;
      *) _ict_is_add_path "${_ICT_VALUES[$i]}" || return 1 ;;
    esac
    i=$((i+1))
  done
  return 0
}

# 引数: <command>
# stdout: 一致したテンプレートの対象 dir (`-C` の値)
# 戻り値: 0 = <command> 全体が T1 / T2 に完全一致 / 1 = それ以外
isolated_commit_template_target() {
  local cmd="$1"
  local count abs second_abs op_index i
  _ict_tokenize "$cmd" || return 1
  count=${#_ICT_KINDS[@]}

  abs="$(_ict_git_c_prefix_at 0)" || return 1

  op_index=-1
  i=0
  while [ "$i" -lt "$count" ]; do
    if [ "${_ICT_KINDS[$i]}" = "O" ]; then
      [ "$op_index" -eq -1 ] || return 1
      op_index=$i
    fi
    i=$((i+1))
  done

  if [ "$op_index" -eq -1 ]; then
    # T1: git -C <ABS> commit <COMMIT_ARGS>
    _ict_bare_is 3 "commit" || return 1
    _ict_commit_args_ok 4 "$count" || return 1
  else
    # T2: git -C <ABS> add <ADD_ARGS> && git -C <ABS> commit <COMMIT_ARGS>
    _ict_bare_is 3 "add" || return 1
    _ict_add_args_ok 4 "$op_index" || return 1
    second_abs="$(_ict_git_c_prefix_at $((op_index+1)))" || return 1
    [ "$second_abs" = "$abs" ] || return 1
    _ict_bare_is $((op_index+4)) "commit" || return 1
    _ict_commit_args_ok $((op_index+5)) "$count" || return 1
  fi
  printf '%s\n' "$abs"
}

# 引数: <path> (絶対パス)
# stdout: 既存ディレクトリなら canonical 実パス (`cd -P && pwd -P`)
# 戻り値: 0 = 解決できた / 1 = 解決できない (相対パス・存在しない・ディレクトリでない・
#         権限不足・改行を含む)
_ict_canonical_dir() {
  local path="$1"
  local resolved
  local nl=$'\n'
  case "$path" in
    /*) ;;
    *) return 1 ;;
  esac
  [ -d "$path" ] || return 1
  resolved="$(cd -P -- "$path" 2>/dev/null && pwd -P)" || return 1
  case "$resolved" in
    /*) ;;
    *) return 1 ;;
  esac
  case "$resolved" in
    *"$nl"*) return 1 ;;
  esac
  printf '%s' "$resolved"
}

# stdout: 有効な許可ルートの canonical 実パスを 1 行 1 件
# 戻り値: 0 = 1 件以上ある / 1 = 無い
_ict_roots() {
  local rest="${CLAUDE_ISOLATED_GIT_ROOTS:-}"
  local entry canonical
  local found=1
  [ -n "$rest" ] || return 1
  rest="$rest:"
  while [ -n "$rest" ]; do
    entry="${rest%%:*}"
    rest="${rest#*:}"
    canonical="$(_ict_canonical_dir "$entry")" || continue
    printf '%s\n' "$canonical"
    found=0
  done
  return "$found"
}

# 引数: <path> <roots> (<path> は canonical 実パス、<roots> は _ict_roots の出力)
# 戻り値: 0 = <path> がいずれかの root 自身または配下 (パス境界で前方一致) / 1 = それ以外
_ict_within_roots() {
  local path="$1"
  local roots="$2"
  local root
  case "$path" in
    /*) ;;
    *) return 1 ;;
  esac
  while IFS= read -r root; do
    [ -n "$root" ] || continue
    [ "$root" = "/" ] && return 0
    case "$path" in
      "$root"|"$root"/*) return 0 ;;
    esac
  done <<< "$roots"
  return 1
}

# 引数: <path> <roots> (<path> は canonical 化した git dir 直下の entry)
# 戻り値: 0 = <path> が存在しない、または実体が許可ルート配下 / 1 = それ以外
_ict_git_entry_within_roots() {
  local path="$1"
  local roots="$2"
  local canonical
  if [ -d "$path" ]; then
    canonical="$(_ict_canonical_dir "$path")" || return 1
    _ict_within_roots "$canonical" "$roots"
    return
  fi
  [ -L "$path" ] && return 1
  [ -e "$path" ] || return 0
  canonical="$(_ict_canonical_dir "${path%/*}")" || return 1
  _ict_within_roots "$canonical" "$roots"
}

# 引数: <git-dir-path> <dir> (<git-dir-path> は rev-parse の出力。相対なら <dir> 基準)
# stdout: canonical 実パス
_ict_canonical_git_dir() {
  local path="$1"
  local dir="$2"
  [ -n "$path" ] || return 1
  case "$path" in
    /*) ;;
    *) path="$dir/$path" ;;
  esac
  _ict_canonical_dir "$path"
}

# 引数: <dir> <canonical_common_dir> <roots>
# 戻り値: 0 = HEAD が指す branch ref の格納先が許可ルート配下 (detached HEAD を含む) /
#         1 = それ以外
_ict_head_ref_within_roots() {
  local dir="$1"
  local canonical_common_dir="$2"
  local roots="$3"
  local ref_name ref_status ref_path parent canonical_parent
  local nl=$'\n'
  ref_name="$(cd -P -- "$dir" 2>/dev/null && git symbolic-ref -q HEAD 2>/dev/null)"
  ref_status=$?
  [ "$ref_status" -eq 1 ] && [ -z "$ref_name" ] && return 0
  [ "$ref_status" -eq 0 ] || return 1
  case "$ref_name" in
    refs/*) ;;
    *) return 1 ;;
  esac
  case "/$ref_name/" in
    */../*|*"$nl"*) return 1 ;;
  esac
  ref_path="$canonical_common_dir/$ref_name"
  [ -L "$ref_path" ] && return 1
  parent="${ref_path%/*}"
  while [ ! -d "$parent" ]; do
    { [ -L "$parent" ] || [ -e "$parent" ]; } && return 1
    parent="${parent%/*}"
    [ -n "$parent" ] || return 1
  done
  canonical_parent="$(_ict_canonical_dir "$parent")" || return 1
  _ict_within_roots "$canonical_parent" "$roots"
}

# 引数: <abs> <roots>
# 戻り値: 0 = 対象 repo 検査 (ヘッダの「対象 repo 検査」) を全て満たす / 1 = それ以外
_ict_repo_is_within_roots() {
  local abs="$1"
  local roots="$2"
  local dir rev_parse_output common_dir git_dir canonical_common_dir canonical_git_dir
  local entry
  local nl=$'\n'
  dir="$(_ict_canonical_dir "$abs")" || return 1
  _ict_within_roots "$dir" "$roots" || return 1
  rev_parse_output="$(cd -P -- "$dir" 2>/dev/null \
    && git rev-parse --git-common-dir --git-dir 2>/dev/null)" || return 1
  common_dir="${rev_parse_output%%"$nl"*}"
  git_dir="${rev_parse_output#*"$nl"}"
  [ "$common_dir" != "$rev_parse_output" ] || return 1
  case "$git_dir" in
    *"$nl"*) return 1 ;;
  esac
  canonical_common_dir="$(_ict_canonical_git_dir "$common_dir" "$dir")" || return 1
  canonical_git_dir="$(_ict_canonical_git_dir "$git_dir" "$dir")" || return 1
  _ict_within_roots "$canonical_common_dir" "$roots" || return 1
  for entry in refs objects HEAD packed-refs logs; do
    _ict_git_entry_within_roots "$canonical_common_dir/$entry" "$roots" || return 1
  done
  if [ "$canonical_git_dir" != "$canonical_common_dir" ]; then
    for entry in HEAD index; do
      _ict_git_entry_within_roots "$canonical_git_dir/$entry" "$roots" || return 1
    done
  fi
  _ict_head_ref_within_roots "$dir" "$canonical_common_dir" "$roots"
}

# 引数: <command>
# 戻り値: 0 = 免除する / 1 = 免除しない (条件はヘッダの「公開関数」を参照)
isolated_commit_template_exempts() {
  local cmd="$1"
  local roots abs name
  [ -n "${CLAUDE_ISOLATED_GIT_ROOTS:-}" ] || return 1
  for name in $_ICT_FORBIDDEN_ENV_NAMES; do
    eval "[ -z \"\${$name+set}\" ]" || return 1
  done
  roots="$(_ict_roots)" || return 1
  abs="$(isolated_commit_template_target "$cmd")" || return 1
  [ -n "$abs" ] || return 1
  _ict_repo_is_within_roots "$abs" "$roots"
}
