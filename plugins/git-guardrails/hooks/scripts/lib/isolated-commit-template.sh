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
#     - common-dir 直下の `refs` / `objects` / `HEAD` / `packed-refs` / `logs` /
#       `reftable` と、worktree 固有 git dir 直下の `HEAD` / `index` / `reftable` のうち
#       存在するものの実体 (ディレクトリは canonical 実パス。ファイルは自体が symlink なら
#       不可、それ以外は親 dir の canonical 実パス)
#     - branch ref の格納先。ref 格納形式 (`git rev-parse --show-ref-format`。このオプション
#       に対応しない古い git では files とみなす) で分ける:
#         files: HEAD が指す ref (`git symbolic-ref -q HEAD`) の ref ファイルの親
#           ディレクトリ (未作成なら存在する最も近い祖先)。ref ファイル自体が symlink なら
#           不可。detached HEAD なら検査しない
#         reftable: common-dir 直下の `reftable` ディレクトリ (存在しなければ不可)
#         それ以外の形式: 不可
#   objects / logs 配下の深い階層の symlink は検査しない (branch は動かないため)。
#   パス・git の出力に LF / CR が含まれる場合は不可。コマンド置換で受け取る値には番兵文字を
#   付け、コマンド置換が末尾の改行を全て削ることで値が別のパス・ref 名に化けるのを防ぐ。
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
# 戻り値: 0 = <index> から `git -C <ABS>` の 3 語が並ぶ (<ABS> を _ICT_PREFIX_ABS に設定) /
#         1 = それ以外
_ict_git_c_prefix_at() {
  local index="$1"
  _ICT_PREFIX_ABS=""
  _ict_bare_is "$index" "git" || return 1
  _ict_bare_is $((index+1)) "-C" || return 1
  [ $((index+2)) -lt "${#_ICT_KINDS[@]}" ] || return 1
  [ "${_ICT_KINDS[$((index+2))]}" = "B" ] || return 1
  _ict_is_abs_path "${_ICT_VALUES[$((index+2))]}" || return 1
  _ICT_PREFIX_ABS="${_ICT_VALUES[$((index+2))]}"
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
# 戻り値: 0 = <command> 全体が T1 / T2 に完全一致 (対象 dir を _ICT_TARGET に設定) /
#         1 = それ以外
_ict_match_template() {
  local cmd="$1"
  local count abs op_index i
  _ICT_TARGET=""
  _ict_tokenize "$cmd" || return 1
  count=${#_ICT_KINDS[@]}

  _ict_git_c_prefix_at 0 || return 1
  abs="$_ICT_PREFIX_ABS"

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
    _ict_git_c_prefix_at $((op_index+1)) || return 1
    [ "$_ICT_PREFIX_ABS" = "$abs" ] || return 1
    _ict_bare_is $((op_index+4)) "commit" || return 1
    _ict_commit_args_ok $((op_index+5)) "$count" || return 1
  fi
  _ICT_TARGET="$abs"
}

# 引数: <command>
# stdout: 一致したテンプレートの対象 dir (`-C` の値)
# 戻り値: 0 = <command> 全体が T1 / T2 に完全一致 / 1 = それ以外
isolated_commit_template_target() {
  _ict_match_template "$1" || return 1
  printf '%s\n' "$_ICT_TARGET"
}

# 引数: <value>
# 戻り値: 0 = <value> が LF / CR を含む / 1 = 含まない
_ict_has_newline() {
  local nl=$'\n' cr=$'\r'
  case "$1" in
    *"$nl"*|*"$cr"*) return 0 ;;
  esac
  return 1
}

# 引数: <path> (絶対パス)
# 戻り値: 0 = 既存ディレクトリで canonical 実パス (`cd -P && pwd -P`) を _ICT_CANONICAL に
#         設定した / 1 = 解決できない (相対パス・存在しない・ディレクトリでない・権限不足・
#         入力または結果が LF / CR を含む)
#
# `pwd -P` の出力は番兵文字を付けてコマンド置換で受け取り、番兵と `pwd` 自身が付ける末尾
# 改行 1 個だけを除去する (コマンド置換が末尾の改行を全て削ることで、名前が改行で終わる
# ディレクトリの実パスが別のパスに化けるのを防ぐため)。
_ict_canonical_dir() {
  local path="$1"
  local resolved
  local nl=$'\n'
  _ICT_CANONICAL=""
  case "$path" in
    /*) ;;
    *) return 1 ;;
  esac
  _ict_has_newline "$path" && return 1
  [ -d "$path" ] || return 1
  resolved="$(cd -P -- "$path" 2>/dev/null && pwd -P && printf x)" || return 1
  case "$resolved" in
    *x) resolved="${resolved%x}" ;;
    *) return 1 ;;
  esac
  case "$resolved" in
    *"$nl") resolved="${resolved%"$nl"}" ;;
    *) return 1 ;;
  esac
  case "$resolved" in
    /*) ;;
    *) return 1 ;;
  esac
  _ict_has_newline "$resolved" && return 1
  _ICT_CANONICAL="$resolved"
}

# 引数: <dir> <git の引数...>
# 戻り値: git の終了コード (出力を _ICT_GIT_OUT に設定) / 125 = <dir> に移動できない /
#         126 = 出力が番兵付きで受け取れない、または git 自身が付ける末尾改行 1 個を除いた
#         出力に LF / CR が残る
#
# git の出力は番兵文字を付けてコマンド置換で受け取り、番兵と末尾改行 1 個だけを除去する
# (パスや ref 名が改行で終わる場合に、コマンド置換の末尾改行削除で別の値に化けるのを
# 防ぐため)。git の標準エラー出力は捨てる。
_ict_git_line() {
  local dir="$1"
  local out status
  local nl=$'\n'
  shift
  _ICT_GIT_OUT=""
  out="$(
    cd -P -- "$dir" 2>/dev/null || exit 125
    git "$@" 2>/dev/null
    git_status=$?
    printf x
    exit "$git_status"
  )"
  status=$?
  [ "$status" -eq 125 ] && return 125
  case "$out" in
    *x) out="${out%x}" ;;
    *) return 126 ;;
  esac
  case "$out" in
    *"$nl") out="${out%"$nl"}" ;;
  esac
  _ict_has_newline "$out" && return 126
  _ICT_GIT_OUT="$out"
  return "$status"
}

# 戻り値: 0 = 有効な許可ルートが 1 件以上ある (canonical 実パスを 1 行 1 件で
#         _ICT_ROOT_LIST に設定) / 1 = 無い
_ict_roots() {
  local rest="${CLAUDE_ISOLATED_GIT_ROOTS:-}"
  local entry
  local nl=$'\n'
  _ICT_ROOT_LIST=""
  [ -n "$rest" ] || return 1
  rest="$rest:"
  while [ -n "$rest" ]; do
    entry="${rest%%:*}"
    rest="${rest#*:}"
    _ict_canonical_dir "$entry" || continue
    _ICT_ROOT_LIST="$_ICT_ROOT_LIST$_ICT_CANONICAL$nl"
  done
  [ -n "$_ICT_ROOT_LIST" ]
}

# 引数: <path> (canonical 実パス)
# 戻り値: 0 = <path> が _ICT_ROOT_LIST のいずれかの root 自身または配下 (パス境界で前方
#         一致) / 1 = それ以外
_ict_within_roots() {
  local path="$1"
  local root
  case "$path" in
    /*) ;;
    *) return 1 ;;
  esac
  _ict_has_newline "$path" && return 1
  while IFS= read -r root; do
    [ -n "$root" ] || continue
    [ "$root" = "/" ] && return 0
    case "$path" in
      "$root"|"$root"/*) return 0 ;;
    esac
  done <<< "$_ICT_ROOT_LIST"
  return 1
}

# 引数: <path> (canonical 化した git dir 直下の entry)
# 戻り値: 0 = <path> が存在しない、または実体が許可ルート配下 / 1 = それ以外
#
# ディレクトリ (symlink 経由を含む) は canonical 実パスで判定する。ファイルはそれ自体が
# symlink (リンク切れを含む) なら 1、そうでなければ親 dir の canonical 実パスで判定する。
_ict_git_entry_within_roots() {
  local path="$1"
  if [ -d "$path" ]; then
    _ict_canonical_dir "$path" || return 1
    _ict_within_roots "$_ICT_CANONICAL"
    return
  fi
  [ -L "$path" ] && return 1
  [ -e "$path" ] || return 0
  _ict_canonical_dir "${path%/*}" || return 1
  _ict_within_roots "$_ICT_CANONICAL"
}

# 引数: <git-dir-path> <dir> (<git-dir-path> は rev-parse の出力。相対なら <dir> 基準)
# 戻り値: 0 = canonical 実パスを _ICT_CANONICAL に設定した / 1 = 解決できない
_ict_canonical_git_dir() {
  local path="$1"
  local dir="$2"
  _ICT_CANONICAL=""
  [ -n "$path" ] || return 1
  case "$path" in
    /*) ;;
    *) path="$dir/$path" ;;
  esac
  _ict_canonical_dir "$path"
}

# 引数: <dir>
# 戻り値: 0 = ref 格納形式を _ICT_REF_FORMAT に設定した / 1 = 取得できない
#
# `git rev-parse --show-ref-format` の出力を使う。このオプションに対応しない古い git
# (オプションを解釈せずそのまま出力する、または失敗する) では `files` とみなす (古い git
# は reftable 形式の repo を扱えず、その場合は rev-parse --git-common-dir の時点で失敗する
# ため)。
_ict_ref_format() {
  local dir="$1"
  local status
  _ICT_REF_FORMAT=""
  _ict_git_line "$dir" rev-parse --show-ref-format
  status=$?
  case "$status" in
    0)
      case "$_ICT_GIT_OUT" in
        --show-ref-format) _ICT_REF_FORMAT="files" ;;
        *) _ICT_REF_FORMAT="$_ICT_GIT_OUT" ;;
      esac
      ;;
    125|126) return 1 ;;
    *) _ICT_REF_FORMAT="files" ;;
  esac
  [ -n "$_ICT_REF_FORMAT" ]
}

# 引数: <dir> <canonical_common_dir>
# 戻り値: 0 = HEAD が指す branch ref の格納先が許可ルート配下 (detached HEAD を含む) /
#         1 = それ以外
#
# ref 格納形式が files の場合は、HEAD が指す ref 名 (`git symbolic-ref -q HEAD`) の ref
# ファイルが symlink でなく、その親ディレクトリ (未作成なら存在する最も近い祖先) の
# canonical 実パスが許可ルート配下であることを要求する。reftable の場合は loose ref を
# 使わないため、common-dir 直下の `reftable` ディレクトリの canonical 実パスが許可ルート
# 配下であることを要求する。それ以外の形式は 1 を返す。
_ict_head_ref_within_roots() {
  local dir="$1"
  local canonical_common_dir="$2"
  local ref_name ref_status ref_path parent
  _ict_ref_format "$dir" || return 1
  case "$_ICT_REF_FORMAT" in
    files) ;;
    reftable)
      [ -d "$canonical_common_dir/reftable" ] || return 1
      _ict_canonical_dir "$canonical_common_dir/reftable" || return 1
      _ict_within_roots "$_ICT_CANONICAL"
      return
      ;;
    *) return 1 ;;
  esac

  _ict_git_line "$dir" symbolic-ref -q HEAD
  ref_status=$?
  ref_name="$_ICT_GIT_OUT"
  [ "$ref_status" -eq 1 ] && [ -z "$ref_name" ] && return 0
  [ "$ref_status" -eq 0 ] || return 1
  case "$ref_name" in
    refs/*) ;;
    *) return 1 ;;
  esac
  case "/$ref_name/" in
    */../*) return 1 ;;
  esac
  ref_path="$canonical_common_dir/$ref_name"
  [ -L "$ref_path" ] && return 1
  parent="${ref_path%/*}"
  while [ ! -d "$parent" ]; do
    { [ -L "$parent" ] || [ -e "$parent" ]; } && return 1
    parent="${parent%/*}"
    [ -n "$parent" ] || return 1
  done
  _ict_canonical_dir "$parent" || return 1
  _ict_within_roots "$_ICT_CANONICAL"
}

# 引数: <abs>
# 戻り値: 0 = 対象 repo 検査 (ヘッダの「対象 repo 検査」) を全て満たす / 1 = それ以外
# 許可ルートは _ICT_ROOT_LIST を使う (_ict_roots で設定済みであること)。
_ict_repo_is_within_roots() {
  local abs="$1"
  local dir canonical_common_dir canonical_git_dir entry
  _ict_canonical_dir "$abs" || return 1
  dir="$_ICT_CANONICAL"
  _ict_within_roots "$dir" || return 1
  _ict_git_line "$dir" rev-parse --git-common-dir || return 1
  _ict_canonical_git_dir "$_ICT_GIT_OUT" "$dir" || return 1
  canonical_common_dir="$_ICT_CANONICAL"
  _ict_git_line "$dir" rev-parse --git-dir || return 1
  _ict_canonical_git_dir "$_ICT_GIT_OUT" "$dir" || return 1
  canonical_git_dir="$_ICT_CANONICAL"
  _ict_within_roots "$canonical_common_dir" || return 1
  for entry in refs objects HEAD packed-refs logs reftable; do
    _ict_git_entry_within_roots "$canonical_common_dir/$entry" || return 1
  done
  if [ "$canonical_git_dir" != "$canonical_common_dir" ]; then
    for entry in HEAD index reftable; do
      _ict_git_entry_within_roots "$canonical_git_dir/$entry" || return 1
    done
  fi
  _ict_head_ref_within_roots "$dir" "$canonical_common_dir"
}

# 引数: <command>
# 戻り値: 0 = 免除する / 1 = 免除しない (条件はヘッダの「公開関数」を参照)
isolated_commit_template_exempts() {
  local cmd="$1"
  local name
  [ -n "${CLAUDE_ISOLATED_GIT_ROOTS:-}" ] || return 1
  for name in $_ICT_FORBIDDEN_ENV_NAMES; do
    eval "[ -z \"\${$name+set}\" ]" || return 1
  done
  _ict_roots || return 1
  _ict_match_template "$cmd" || return 1
  [ -n "$_ICT_TARGET" ] || return 1
  _ict_repo_is_within_roots "$_ICT_TARGET"
}
