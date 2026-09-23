#!/bin/bash
# isolated-roots.sh
# commit 系 hook (block-default-branch-commit.sh) が、利用者の明示した隔離ルート配下の
# repo だけを対象とする commit を検査対象から免除するための判定関数群。
#
# ## 許可ルート
#
# env `CLAUDE_ISOLATED_GIT_ROOTS` に PATH と同じコロン区切りで絶対パスを列挙する。
#   - 空文字 entry・相対パス entry・存在しないディレクトリの entry は無視する
#   - 有効な entry は `cd -P <entry> && pwd -P` で canonical 実パスに変換して比較に使う
#   - env が未設定・空、または有効な entry が 1 つも無い場合は免除しない
#
# ## 免除条件
#
# 次の全てを満たす場合のみ免除する:
#   1. コマンド内に commit invocation が 1 つ以上あり、最後の commit invocation より前に
#      `cd <path>` と `git add` / `git commit` 以外の segment が無く、それらの git
#      invocation 全ての対象 dir を静的に解決できる (解決規則は
#      resolve_commit_target_dirs を参照。判定後・commit 前に対象 dir や repo レイアウトを
#      差し替える前段コマンドを排除するため)。認識した commit invocation の件数が、
#      hook 自身が検出した件数と一致する
#   2. 各対象 dir の canonical 実パスが、いずれかの許可ルート配下にある
#   3. 各対象 dir で実行した `git rev-parse --git-common-dir` の canonical 実パスが、
#      いずれかの許可ルート配下にある (許可ルート配下に置いた linked worktree / symlink
#      経由でルート外の repo を更新する経路を塞ぐ)。common-dir 直下の `refs` /
#      `objects` / `HEAD` / `packed-refs` / `logs` と、worktree 固有 git dir の `HEAD` /
#      `index` の実体も許可ルート配下にある (isolated_dir_is_within_roots を参照)
#   4. hook プロセスの環境に `GIT_DIR` / `GIT_WORK_TREE` / `GIT_INDEX_FILE` /
#      `GIT_COMMON_DIR` が設定されていない (コマンド内の再代入で commit 先が変わりうるため)
# 「配下」はパス境界での前方一致で判定し、ルート自身との一致も配下とみなす
# (`/a/b` は `/a/b` と `/a/b/c` を含み、`/a/bc` を含まない)。
#
# ## fail-closed
#
# 解析・パス解決・git 呼び出しのいずれかが失敗した場合、または判定に必要な情報が
# 静的に確定しない場合は「免除しない」(return 1) を返す。免除しない場合、caller は
# 従来の判定 (target-mismatch deny / default branch 上 commit の deny) をそのまま行う。
#
# ## 互換性・依存
#
# macOS 標準 bash 3.2 と Linux bash の両方で動く構文だけを使う (`declare -A` /
# `mapfile` / nameref / `${x,,}` / `realpath` / `readlink -f` を使わない)。canonical 化は
# `cd -P && pwd -P` で行い、macOS の `/tmp` → `/private/tmp` のような symlink を実体に
# 解決する。
# cmd-parser.sh (split_command / tokenize_segment / skip_env_assignments) と
# default-branch.sh (normalize_shell_word_syntax) を先に source しておくこと。

# commit 先 repo の解決を変える環境変数。コマンド内での代入も hook 環境での設定も
# 免除しない理由になる。
_ISOLATED_REPO_ENV_NAMES="GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR"

# 引数: <path> (絶対パス)
# stdout: <path> が既存ディレクトリなら、その canonical 実パス (`cd -P <path> && pwd -P`)
# 戻り値: 0 = 解決できた / 1 = 解決できない (相対パス・存在しない・ディレクトリでない・
#         権限不足・改行を含む)
# 相対パスを受け付けないのは、`cd` が CDPATH を参照して別の dir へ移動しうるため。
isolated_canonical_dir() {
  local path="$1"
  local resolved
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
    *$'\n'*) return 1 ;;
  esac
  printf '%s' "$resolved"
}

# 引数: なし (env `CLAUDE_ISOLATED_GIT_ROOTS` を読む)
# stdout: 有効な許可ルートの canonical 実パスを 1 行 1 件で出力する
# 戻り値: 0 = 有効な許可ルートが 1 件以上ある / 1 = 無い (未設定・空・全 entry が無効)
isolated_git_roots() {
  local rest="${CLAUDE_ISOLATED_GIT_ROOTS:-}"
  local entry canonical
  local found=1
  [ -n "$rest" ] || return 1
  rest="$rest:"
  while [ -n "$rest" ]; do
    entry="${rest%%:*}"
    rest="${rest#*:}"
    canonical="$(isolated_canonical_dir "$entry")" || continue
    printf '%s\n' "$canonical"
    found=0
  done
  return "$found"
}

# 引数: <path> <root> (どちらも canonical 実パスであること)
# 戻り値: 0 = <path> が <root> 自身または <root> 配下 (パス境界で前方一致) / 1 = それ以外
# <root> が `/` の場合は全ての絶対パスを配下とみなす。
isolated_path_is_within() {
  local path="$1"
  local root="$2"
  case "$path" in
    /*) ;;
    *) return 1 ;;
  esac
  case "$root" in
    /*) ;;
    *) return 1 ;;
  esac
  [ "$root" = "/" ] && return 0
  case "$path" in
    "$root"|"$root"/*) return 0 ;;
  esac
  return 1
}

# 引数: <path> <roots> (<roots> は isolated_git_roots の出力 = 1 行 1 件)
# 戻り値: 0 = <path> がいずれかの root 配下 / 1 = それ以外
_isolated_path_is_within_any_root() {
  local path="$1"
  local roots="$2"
  local root
  while IFS= read -r root; do
    [ -n "$root" ] || continue
    isolated_path_is_within "$path" "$root" && return 0
  done <<< "$roots"
  return 1
}

# 引数: <path> <roots> (<path> は canonical 化した git dir 直下の entry)
# 戻り値: 0 = <path> が存在しない、または実体がいずれかの許可ルート配下にある / 1 = それ以外
#
# ディレクトリ (symlink 経由を含む) は `cd -P && pwd -P` の canonical 実パスで判定する。
# ファイルはそれ自体が symlink (リンク切れを含む) なら 1 を返し、そうでなければ親 dir の
# canonical 実パスで判定する。
_isolated_git_entry_is_within_roots() {
  local path="$1"
  local roots="$2"
  local canonical
  if [ -d "$path" ]; then
    canonical="$(isolated_canonical_dir "$path")" || return 1
    _isolated_path_is_within_any_root "$canonical" "$roots"
    return
  fi
  [ -L "$path" ] && return 1
  [ -e "$path" ] || return 0
  canonical="$(isolated_canonical_dir "${path%/*}")" || return 1
  _isolated_path_is_within_any_root "$canonical" "$roots"
}

# 引数: <git-dir-path> <dir> (<git-dir-path> は rev-parse の出力。相対なら <dir> 基準)
# stdout: canonical 実パス
# 戻り値: 0 = 解決できた / 1 = 解決できない
_isolated_canonical_git_dir() {
  local path="$1"
  local dir="$2"
  [ -n "$path" ] || return 1
  case "$path" in
    /*) ;;
    *) path="$dir/$path" ;;
  esac
  isolated_canonical_dir "$path"
}

# 引数: <dir> (canonical 実パス)
# 戻り値: 0 = 次の全てを満たす / 1 = それ以外
#   - <dir> がいずれかの許可ルート配下にある
#   - <dir> で実行した `git rev-parse --git-common-dir` の canonical 実パスが、いずれかの
#     許可ルート配下にある
#   - common-dir 直下の `refs` / `objects` / `HEAD` / `packed-refs` / `logs` のうち存在する
#     ものの実体が、いずれかの許可ルート配下にある (_isolated_git_entry_is_within_roots)
#   - `git rev-parse --git-dir` (worktree 固有 dir) が common-dir と異なる場合は、その
#     直下の `HEAD` / `index` についても同じ条件を満たす
# git dir 内部の symlink でルート外 repo の ref / object を更新する経路を塞ぐ。
#
# rev-parse の出力が相対パスなら <dir> 基準で解決する。rev-parse は hook プロセスの環境を
# 継承して実行する (実際の commit と同じ解決結果を得るため)。<dir> が git repo でない・
# rev-parse が失敗した場合は 1 を返す。
isolated_dir_is_within_roots() {
  local dir="$1"
  local roots rev_parse_output common_dir git_dir canonical_common_dir canonical_git_dir
  local entry
  local nl=$'\n'
  roots="$(isolated_git_roots)" || return 1
  _isolated_path_is_within_any_root "$dir" "$roots" || return 1
  rev_parse_output="$(cd -P -- "$dir" 2>/dev/null \
    && git rev-parse --git-common-dir --git-dir 2>/dev/null)" || return 1
  common_dir="${rev_parse_output%%"$nl"*}"
  git_dir="${rev_parse_output#*"$nl"}"
  case "$git_dir" in
    *"$nl"*) return 1 ;;
  esac
  [ "$common_dir" != "$rev_parse_output" ] || return 1
  canonical_common_dir="$(_isolated_canonical_git_dir "$common_dir" "$dir")" || return 1
  canonical_git_dir="$(_isolated_canonical_git_dir "$git_dir" "$dir")" || return 1
  _isolated_path_is_within_any_root "$canonical_common_dir" "$roots" || return 1
  for entry in refs objects HEAD packed-refs logs; do
    _isolated_git_entry_is_within_roots "$canonical_common_dir/$entry" "$roots" || return 1
  done
  if [ "$canonical_git_dir" != "$canonical_common_dir" ]; then
    for entry in HEAD index; do
      _isolated_git_entry_is_within_roots "$canonical_git_dir/$entry" "$roots" || return 1
    done
  fi
  return 0
}

# 引数: <command>
# 戻り値: 0 = 静的解決を妨げる構文を含む / 1 = 含まない
#
# quote 文脈を追跡する文字 walk で、次のいずれかを検出する:
#   - quote 外の `(` / `)` / `{` / `}` (subshell・brace group・プロセス置換・算術・関数定義)
#   - quote 外・double quote 内のバッククォートと `$(` (コマンド置換)
#   - quote 外の `<` / `>` (redirection 演算子全般。`>>` / `<>` / `>&` / `<&` / `&>` /
#     `>|` / heredoc `<<` / here-string `<<<` と数字 fd 前置を含む。fd 番号とパス末尾の
#     数字を区別できず、`>/dev/null git commit` のように redirection が先行する commit を
#     hook 側と異なる形で解析しうるため、コマンド内のどの位置にあっても解決不能とする)
#   - quote 外の `$'` / `$"` (ANSI-C / locale quoting。quote 境界を静的に追えないため)
#   - quote 外の `#` (コメント。後続を segment と誤認させないため)
#   - quote 外の CR / VT / FF (bash は単語区切りとして扱わないため token 分割がずれる)
#   - 閉じていない quote
_isolated_has_unresolvable_syntax() {
  local cmd="$1"
  local i=0 len=${#cmd}
  local in_squote=0 in_dquote=0
  local c nc
  local cr=$'\r' vt=$'\v' ff=$'\f'

  while [ "$i" -lt "$len" ]; do
    c="${cmd:$i:1}"
    nc="${cmd:$((i+1)):1}"

    if [ "$in_squote" -eq 1 ]; then
      [ "$c" = "'" ] && in_squote=0
      i=$((i+1)); continue
    fi

    if [ "$in_dquote" -eq 1 ]; then
      case "$c" in
        \\)
          i=$((i+2)); continue ;;
        '`')
          return 0 ;;
        '$')
          [ "$nc" = "(" ] && return 0 ;;
        '"')
          in_dquote=0 ;;
      esac
      i=$((i+1)); continue
    fi

    case "$c" in
      \\) i=$((i+2)); continue ;;
      "'") in_squote=1 ;;
      '"') in_dquote=1 ;;
      '`'|'('|')'|'{'|'}'|'#') return 0 ;;
      '$')
        case "$nc" in
          "'"|'"') return 0 ;;
        esac
        ;;
      '<'|'>') return 0 ;;
      "$cr"|"$vt"|"$ff") return 0 ;;
    esac
    i=$((i+1))
  done

  [ "$in_squote" -eq 0 ] && [ "$in_dquote" -eq 0 ] || return 0
  return 1
}

# 引数: <raw-token>
# stdout: token が静的なパス文字列なら quote を外した値
# 戻り値: 0 = 静的なパス / 1 = それ以外
#
# 受け付ける形は、token 全体が quote されていない文字列・token 全体が 1 組の single
# quote・token 全体が 1 組の double quote (内部に `$` / バッククォート / `\` を含まない)
# のいずれか。値が空・`-` 始まり・`~` 始まり・glob 文字 (`*` / `?` / `[`)・改行・`..`
# 要素を含む場合は受け付けない (`..` は `cd` の論理パス解決と実パス解決が symlink 越しに
# 食い違うため)。
_isolated_static_path_value() {
  local raw="$1"
  local value
  case "$raw" in
    \'*\')
      value="${raw#\'}"; value="${value%\'}"
      case "$value" in
        *\'*) return 1 ;;
      esac
      ;;
    \"*\")
      value="${raw#\"}"; value="${value%\"}"
      case "$value" in
        *\"*|*'$'*|*'`'*|*\\*) return 1 ;;
      esac
      ;;
    *)
      case "$raw" in
        *\'*|*\"*|*'$'*|*'`'*|*\\*) return 1 ;;
      esac
      value="$raw"
      ;;
  esac
  [ -n "$value" ] || return 1
  case "$value" in
    -*|'~'*|*'*'*|*'?'*|*'['*|*$'\n'*) return 1 ;;
  esac
  case "/$value/" in
    */../*) return 1 ;;
  esac
  printf '%s' "$value"
}

# 引数: <word> (segment の先頭 word。quote / escape を正規化済み)
# 戻り値: 0 = <word> が、後続の word を command として実行しうる、または shell の cwd /
#         環境の解決を静的に追えなくする shell keyword / builtin / 透過 wrapper /
#         1 = それ以外
# これらが先頭にある segment は、commit invocation を隠しうるためコマンド内のどの位置に
# あっても解決不能とする。
_isolated_is_unresolvable_command_word() {
  case "$1" in
    '!'|'time'|'if'|'then'|'else'|'elif'|'fi'|'while'|'until'|'do'|'done') return 0 ;;
    'for'|'in'|'case'|'esac'|'select'|'function'|'coproc'|'[['|']]') return 0 ;;
    pushd|popd|export|declare|typeset|readonly|unset|local|source|.|eval|exec) return 0 ;;
    builtin|command|trap|alias|unalias|shopt|enable|hash|set) return 0 ;;
    env|sudo|doas|nice|ionice|timeout|chrt|stdbuf) return 0 ;;
  esac
  return 1
}

# 引数: <assignment-token> (`NAME=VALUE` 形式、quote 付きでもよい)
# 戻り値: 0 = commit 先 repo / cd の解決を変える変数への代入 / 1 = それ以外
_isolated_is_unresolvable_assignment() {
  local name
  name="$(normalize_shell_word_syntax "$1")"
  name="${name%%=*}"
  local candidate
  for candidate in $_ISOLATED_REPO_ENV_NAMES PWD CDPATH; do
    [ "$name" = "$candidate" ] && return 0
  done
  return 1
}

# 引数: <base-dir> <path-value>
# stdout: <path-value> を <base-dir> 基準で解決した canonical 実パス
# 戻り値: 0 = 解決できた / 1 = 解決できない
_isolated_resolve_path_from() {
  local base="$1"
  local value="$2"
  case "$value" in
    /*) isolated_canonical_dir "$value" ;;
    *) isolated_canonical_dir "$base/$value" ;;
  esac
}

# 引数: <command> <base_dir> <expected_commit_count>
#   <command>  : hook が受け取った Bash コマンド文字列 (行継続の正規化後、redirection
#                正規化前。パス解決には元のコマンドの token をそのまま使う)
#   <base_dir> : hook プロセスの cwd (commit invocation の対象 dir の初期値)
#   <expected_commit_count> : hook 自身が検出した commit invocation の件数
# stdout: 最後の commit invocation までの各 git invocation (`git add` / `git commit`)
#         について、静的に解決した対象 dir の canonical 実パスを出現順に 1 行 1 件で
#         出力する (呼び出し側はその全てに免除条件を要求する)
# 戻り値: 0 = commit invocation が 1 つ以上あり、規則どおりに全て解決できた / 1 = それ以外
#
# 静的解決の規則 (これ以外の形は解決不能として 1 を返す):
#   - _isolated_has_unresolvable_syntax が検出する構文 (subshell / brace group /
#     コマンド置換 / プロセス置換 / redirection / heredoc / コメント 等) を、コマンド内の
#     どの位置にも含まないこと
#   - 本関数が認識した commit invocation の件数が <expected_commit_count> と一致すること
#     (hook と本関数の解析が食い違い、免除判定が一部の commit を見落とす経路を塞ぐ)
#   - 先頭 segment から最後の commit invocation までの区切りが `&&` だけであること
#     (`;` / 改行は cd が実行時に失敗しても commit が元の cwd で実行されるため、
#     `||` / `|` / `&` は cd の効果が commit に及ぶかを静的に確定できないため解決不能)
#   - 最後の commit invocation より前の segment は、次のいずれかであること。それ以外の
#     segment (判定時点のファイルシステム状態を commit 前に変えうる任意のコマンド。
#     対象 dir の削除・移動・symlink 化や `git config` / `git init` 等による repo
#     レイアウトの変更を含む) があれば解決不能:
#       1. `cd <path>` (引数ちょうど 1 個)
#       2. subcommand が `add` または `commit` の git invocation (その対象 dir も
#          出力に含め、commit と同じ免除条件を要求する)
#     最後の commit invocation より後ろの segment は、次の規則を除き判定に影響しない
#   - commit invocation を隠しうる先頭 word (shell keyword / `eval` / `exec` /
#     `command` / `builtin` / `env` / `sudo` 等。_isolated_is_unresolvable_command_word
#     参照) の segment は、コマンド内のどの位置にあっても解決不能
#   - env assignment のみの segment は上記のいずれにも当たらない。git / cd 直前の env
#     assignment のうち `GIT_DIR` / `GIT_WORK_TREE` / `GIT_INDEX_FILE` /
#     `GIT_COMMON_DIR` / `PWD` / `CDPATH` への代入は解決不能
#   - git invocation の global option で対象を切り替えるものは `-C <path>` だけを
#     受け付ける (複数指定時は git と同じく順に相対解決する)。`--git-dir` /
#     `--work-tree` (および `=` 形式)、`-C<path>` の連結形は解決不能
#   - `cd` / `-C` の <path> は _isolated_static_path_value が受け付ける静的な文字列で
#     あること (変数展開・先頭の `~`・glob 文字・バックスラッシュ・`-` 始まり・`..` 要素は
#     解決不能)
#   - `cd` の相対パスは CDPATH の影響を受けない `./` 始まりに限る。`-C` の相対パスは
#     直前までに解決した dir を基準に解決する
#   - 各段階で既存ディレクトリとして canonical 化できること
resolve_commit_target_dirs() {
  local cmd="$1"
  local base="$2"
  local expected_commit_count="$3"
  local current_dir
  local -a _iso_segments=()
  local -a _iso_separators=()
  local -a _iso_targets=()
  local line
  local last_commit_index=-1
  local commit_count=0

  case "$expected_commit_count" in
    ''|*[!0-9]*) return 1 ;;
  esac
  _isolated_has_unresolvable_syntax "$cmd" && return 1
  current_dir="$(isolated_canonical_dir "$base")" || return 1

  # redirection は _isolated_has_unresolvable_syntax で解決不能にしているため、元の
  # コマンドをそのまま分割する (パス解決にも元の token を使う)。
  while IFS= read -r line; do
    case "$line" in
      SEP:*)
        _iso_separators[${#_iso_segments[@]}-1]="${line#SEP:}"
        ;;
      *)
        _iso_segments+=("$line")
        ;;
    esac
  done < <(split_command "$cmd")

  local _iso_seg_count=${#_iso_segments[@]}
  local -a _iso_toks=()
  local _iso_idx _iso_tok_count _iso_word _iso_oi _iso_opt

  # 1 周目: 最後の commit invocation の segment を特定する。global option の walk は
  # block-default-branch-commit.sh の commit invocation 検出と同じ規則で行う。
  # commit invocation を隠しうる先頭 word の segment は、位置に関わらず解決不能とする。
  local _iso_si=0
  while [ "$_iso_si" -lt "$_iso_seg_count" ]; do
    _iso_toks=()
    _iso_idx=0
    tokenize_segment "${_iso_segments[$_iso_si]}" _iso_toks
    _iso_tok_count=${#_iso_toks[@]}
    skip_env_assignments _iso_toks _iso_idx
    if [ "$_iso_idx" -lt "$_iso_tok_count" ]; then
      _iso_word="$(normalize_shell_word_syntax "${_iso_toks[$_iso_idx]}")"
      _isolated_is_unresolvable_command_word "$_iso_word" && return 1
      case "$_iso_word" in
        git|*/git)
          _iso_oi=$((_iso_idx+1))
          while [ "$_iso_oi" -lt "$_iso_tok_count" ]; do
            _iso_opt="$(normalize_shell_word_syntax "${_iso_toks[$_iso_oi]}")"
            case "$_iso_opt" in
              -C|--git-dir|--work-tree|-c|--config|--config-env) _iso_oi=$((_iso_oi+2)) ;;
              -*) _iso_oi=$((_iso_oi+1)) ;;
              commit)
                last_commit_index="$_iso_si"
                commit_count=$((commit_count+1))
                break
                ;;
              *) break ;;
            esac
          done
          ;;
      esac
    fi
    _iso_si=$((_iso_si+1))
  done
  [ "$last_commit_index" -ge 0 ] || return 1
  [ "$commit_count" -eq "$expected_commit_count" ] || return 1

  # 2 周目: 最後の commit invocation までの segment を解決する。
  _iso_si=0
  while [ "$_iso_si" -le "$last_commit_index" ]; do
    _iso_toks=()
    _iso_idx=0
    tokenize_segment "${_iso_segments[$_iso_si]}" _iso_toks
    _iso_tok_count=${#_iso_toks[@]}
    if [ "$_iso_tok_count" -eq 0 ]; then
      _iso_si=$((_iso_si+1))
      continue
    fi

    skip_env_assignments _iso_toks _iso_idx
    local _iso_ai=0
    while [ "$_iso_ai" -lt "$_iso_idx" ]; do
      _isolated_is_unresolvable_assignment "${_iso_toks[$_iso_ai]}" && return 1
      _iso_ai=$((_iso_ai+1))
    done
    # env assignment のみの segment は cd / git add / git commit のいずれでもない。
    [ "$_iso_idx" -lt "$_iso_tok_count" ] || return 1

    _iso_word="$(normalize_shell_word_syntax "${_iso_toks[$_iso_idx]}")"
    case "$_iso_word" in
      cd)
        [ "$_iso_tok_count" -eq $((_iso_idx+2)) ] || return 1
        local _iso_cd_value
        _iso_cd_value="$(_isolated_static_path_value "${_iso_toks[$((_iso_idx+1))]}")" \
          || return 1
        case "$_iso_cd_value" in
          /*|./*) ;;
          *) return 1 ;;
        esac
        current_dir="$(_isolated_resolve_path_from "$current_dir" "$_iso_cd_value")" \
          || return 1
        ;;
      git|*/git)
        # 最初の non-option token を subcommand とみなし、`add` / `commit` だけを受け付ける。
        local _iso_target="$current_dir"
        local _iso_subcommand=""
        _iso_oi=$((_iso_idx+1))
        while [ "$_iso_oi" -lt "$_iso_tok_count" ]; do
          _iso_opt="$(normalize_shell_word_syntax "${_iso_toks[$_iso_oi]}")"
          case "$_iso_opt" in
            -C)
              [ $((_iso_oi+1)) -lt "$_iso_tok_count" ] || return 1
              local _iso_c_value
              _iso_c_value="$(_isolated_static_path_value "${_iso_toks[$((_iso_oi+1))]}")" \
                || return 1
              _iso_target="$(_isolated_resolve_path_from "$_iso_target" "$_iso_c_value")" \
                || return 1
              _iso_oi=$((_iso_oi+2))
              ;;
            -C?*|--git-dir|--git-dir=*|--work-tree|--work-tree=*)
              return 1
              ;;
            -c|--config|--config-env)
              _iso_oi=$((_iso_oi+2))
              ;;
            -*)
              _iso_oi=$((_iso_oi+1))
              ;;
            *)
              _iso_subcommand="$_iso_opt"
              break
              ;;
          esac
        done
        case "$_iso_subcommand" in
          add|commit) _iso_targets+=("$_iso_target") ;;
          *) return 1 ;;
        esac
        ;;
      *)
        return 1
        ;;
    esac
    _iso_si=$((_iso_si+1))
  done

  [ "${#_iso_targets[@]}" -gt 0 ] || return 1

  local _iso_sep_i=0
  while [ "$_iso_sep_i" -lt "$last_commit_index" ]; do
    case "${_iso_separators[$_iso_sep_i]:-}" in
      '&&') ;;
      *) return 1 ;;
    esac
    _iso_sep_i=$((_iso_sep_i+1))
  done

  printf '%s\n' "${_iso_targets[@]}"
}

# 引数: <command> <base_dir> <expected_commit_count> (意味は resolve_commit_target_dirs と同じ)
# 戻り値: 0 = 免除する (許可ルートが有効で、resolve_commit_target_dirs が出力した全対象
#         dir (git add / git commit) が免除条件を満たす) / 1 = 免除しない
# env `CLAUDE_ISOLATED_GIT_ROOTS` が未設定・空なら、コマンドを解析せずに 1 を返す。
command_commits_only_to_isolated_roots() {
  local cmd="$1"
  local base="$2"
  local expected_commit_count="$3"
  local targets target name
  [ -n "${CLAUDE_ISOLATED_GIT_ROOTS:-}" ] || return 1
  for name in $_ISOLATED_REPO_ENV_NAMES; do
    eval "[ -z \"\${$name+set}\" ]" || return 1
  done
  isolated_git_roots >/dev/null || return 1
  targets="$(resolve_commit_target_dirs "$cmd" "$base" "$expected_commit_count")" || return 1
  [ -n "$targets" ] || return 1
  while IFS= read -r target; do
    [ -n "$target" ] || continue
    isolated_dir_is_within_roots "$target" || return 1
  done <<< "$targets"
  return 0
}
