#!/bin/bash
# target-resolver.sh
# `git push` コマンドの実行 target (= git push が操作する repo の cwd) を
# コマンド文字列から決定的に解決する。
#
# 設計意図: 従来の「危険プレフィクスを deny」設計ではなく、 target を直接 resolve して
# その target に対して markers / hash 比較を行う positive list 設計を取る。
#
# 解決対象:
#   - `cd <dir>` chain prefix    : virtual cwd を更新
#   - `git -C <dir>` git option   : push 呼び出しの cwd を override
#   - `--git-dir=<path>` git option : push 呼び出しの GIT_DIR を override
#   - `GIT_DIR=<path>` env-var prefix: push 呼び出しの GIT_DIR を override
#
# サポート外 (resolve に失敗 = return 1、呼び出し側は保守的 deny する):
#   - pushd / popd (stack を保持しない)
#   - subshell `(cd ...; git push)` (cd が外に伝播しないが、本 resolver は subshell を
#     検出すると return 1)
#   - brace group `{ cd ...; git push; }` (cd は外に伝播するが、 cooperative では稀)
#   - bash -c "..." 経由の push (resolve 不能)
#   - export GIT_DIR=... のような prefix 命令
#
# 提供する関数:
#   - resolve_push_target <cmd> : 成功時 stdout に target cwd を出力。 失敗時 return 1。

# 引数: <cmd>
# stdout: target cwd (絶対パス)
# return: 0 = 解決成功、1 = 解決不能 (呼び出し側で deny 推奨)
resolve_push_target() {
  local cmd="$1"
  local cwd="$PWD"

  # cmd-parser.sh から split_command / tokenize_segment / unquote_token を読み込む
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # shellcheck source=cmd-parser.sh
  source "$script_dir/cmd-parser.sh"

  # コマンドを segment に分割し、 push を含む segment を見つける
  local segments=()
  local push_index=-1
  local i=0
  while IFS= read -r line; do
    if [[ "$line" == SEP:* ]]; then
      continue
    fi
    segments+=("$line")
    if [ "$push_index" -lt 0 ]; then
      # 先頭が `git ... push` (option を許容、 path-qualified `/usr/bin/git` も許容) かを
      # 軽量判定。 boundary は空白 / 行頭 / `/` (path 前段からの遷移)。
      if [[ "$line" =~ (^|[[:space:]/])git([[:space:]]+[^[:space:]]+)*[[:space:]]+push([[:space:]]|$) ]]; then
        push_index=$i
      fi
    fi
    i=$((i+1))
  done < <(split_command "$cmd")

  [ "$push_index" -lt 0 ] && return 1

  # push の前にある segment 群を順次処理して cwd を更新する。
  # サブシェル / ブレースグループ / pushd / popd 等を見つけたら return 1 (保守的 deny)。
  local k
  for ((k=0; k < push_index; k++)); do
    if ! _process_pre_push_segment "${segments[$k]}"; then
      return 1
    fi
  done

  # push segment 自体を解析して、 target cwd を最終決定する。
  if ! _process_push_segment "${segments[$push_index]}"; then
    return 1
  fi

  # cwd を絶対パスに正規化 (実在する場合のみ chdir で正規化、 そうでなければ素の連結値を返す)
  if [ -d "$cwd" ]; then
    local normalized
    normalized=$(cd "$cwd" 2>/dev/null && pwd) && cwd="$normalized"
  fi

  printf '%s' "$cwd"
}

# 内部: pre-push segment (= push よりも前の chain segment) を処理して cwd を更新する。
# 上書き対象は外部スコープの cwd 変数 (caller が宣言)。
# return: 0 = 処理成功 (cwd 更新済 or 無関係 segment)、 1 = 解析不能 segment (保守的 deny)
_process_pre_push_segment() {
  local seg="$1"
  # 前後の空白を除去
  seg="${seg#"${seg%%[![:space:]]*}"}"
  seg="${seg%"${seg##*[![:space:]]}"}"

  [ -z "$seg" ] && return 0

  # サブシェル `(...)` / ブレースグループ `{...}` は cwd 伝播のセマンティクスが
  # 異なる (subshell は伝播しない、 brace は伝播する) が、内部に cd を持つ場合は
  # 解析が複雑になるため保守的に deny する。
  case "$seg" in
    \(*|\{*)
      return 1 ;;
  esac

  local -a tokens
  tokenize_segment "$seg" tokens
  local n=${#tokens[@]}
  [ "$n" -eq 0 ] && return 0

  local idx=0

  # env-var prefix の処理: `cd` の前に env-var が付くケースは稀だが、念のため skip。
  # ただし `export FOO=...` / `declare ...` のような prefix 命令は cwd を変えない別命令
  # なので、別 segment として扱う想定。
  while [ "$idx" -lt "$n" ]; do
    local t
    t="$(unquote_token "${tokens[$idx]}")"
    if [[ "$t" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      idx=$((idx+1))
    else
      break
    fi
  done

  [ "$idx" -ge "$n" ] && return 0

  local cmd_name
  cmd_name="$(unquote_token "${tokens[$idx]}")"

  case "$cmd_name" in
    cd)
      idx=$((idx+1))
      local target=""
      while [ "$idx" -lt "$n" ]; do
        local t
        t="$(unquote_token "${tokens[$idx]}")"
        # cd の option (-L, -P, -e, -@) を skip
        if [[ "$t" == -* ]]; then
          idx=$((idx+1)); continue
        fi
        target="$t"
        break
      done
      if [ -z "$target" ]; then
        # `cd` 単独 = $HOME へ
        cwd="$HOME"
      elif [[ "$target" == /* ]]; then
        cwd="$target"
      else
        cwd="$cwd/$target"
      fi
      return 0
      ;;
    pushd|popd)
      # stack 保持していないため deny
      return 1
      ;;
    builtin|command|eval|exec)
      # wrapper 経由の cd は深追いせず deny (cooperative 利用では稀)
      return 1
      ;;
    export|declare|typeset|readonly)
      # GIT_DIR / GIT_WORK_TREE を env-var に持続的に export する経路は cwd 解決に影響する
      # ため deny (cooperative 利用では稀)。
      return 1
      ;;
    *)
      # cwd を変えない別命令 (echo, git status 等)。 segment は無視して継続。
      return 0
      ;;
  esac
}

# 内部: push segment を処理して cwd を最終決定する。
# 上書き対象は外部スコープの cwd 変数。
# return: 0 = 処理成功、 1 = 解析不能 (保守的 deny)
_process_push_segment() {
  local seg="$1"
  seg="${seg#"${seg%%[![:space:]]*}"}"
  seg="${seg%"${seg##*[![:space:]]}"}"

  case "$seg" in
    \(*|\{*) return 1 ;;
  esac

  local -a tokens
  tokenize_segment "$seg" tokens
  local n=${#tokens[@]}
  local idx=0

  # env-var prefix: GIT_DIR= があれば抽出
  local seg_git_dir=""
  while [ "$idx" -lt "$n" ]; do
    local t
    t="$(unquote_token "${tokens[$idx]}")"
    if [[ "$t" =~ ^GIT_DIR=(.*)$ ]]; then
      seg_git_dir="${BASH_REMATCH[1]}"
      idx=$((idx+1))
    elif [[ "$t" =~ ^GIT_WORK_TREE=(.*)$ ]]; then
      # GIT_WORK_TREE は cwd を変えない (work tree を別箇所に置くだけ) が、
      # cooperative では稀かつセマンティクスが複雑なので deny。
      return 1
    elif [[ "$t" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      # その他の env-var は無関係なので skip
      idx=$((idx+1))
    else
      break
    fi
  done

  # wrapper コマンド (export, builtin, command, eval, exec) を見つけたら deny
  if [ "$idx" -lt "$n" ]; then
    local first
    first="$(unquote_token "${tokens[$idx]}")"
    case "$first" in
      builtin|command|eval|exec|export|declare|typeset|readonly)
        return 1 ;;
    esac
  fi

  # `git` または path-qualified (`/usr/bin/git`, `./git` 等) を期待
  [ "$idx" -ge "$n" ] && return 1
  local cmd_name
  cmd_name="$(unquote_token "${tokens[$idx]}")"
  case "$cmd_name" in
    git|*/git) ;;
    *) return 1 ;;
  esac
  idx=$((idx+1))

  # git の global option を walk: -C / --git-dir / --work-tree を検出
  while [ "$idx" -lt "$n" ]; do
    local opt
    opt="$(unquote_token "${tokens[$idx]}")"
    case "$opt" in
      -C)
        idx=$((idx+1))
        if [ "$idx" -lt "$n" ]; then
          local target
          target="$(unquote_token "${tokens[$idx]}")"
          if [[ "$target" == /* ]]; then
            cwd="$target"
          else
            cwd="$cwd/$target"
          fi
        fi
        ;;
      --git-dir=*) seg_git_dir="${opt#--git-dir=}" ;;
      --git-dir)
        idx=$((idx+1))
        [ "$idx" -lt "$n" ] && seg_git_dir="$(unquote_token "${tokens[$idx]}")"
        ;;
      --work-tree=*|--work-tree)
        # work tree override は cwd / GIT_DIR とは独立した複雑な semantics になる。 deny。
        return 1
        ;;
      -c|--config|--config-env)
        # -c key=value 形式: 引数を 1 個 skip
        idx=$((idx+1))
        ;;
      -*)
        # その他の global option (-p, --paginate, --no-pager, --bare 等) は cwd に影響しない
        ;;
      *)
        # subcommand に到達。 option walk 終了。
        break
        ;;
    esac
    idx=$((idx+1))
  done

  # GIT_DIR= や --git-dir= が指定されていた場合、 その親ディレクトリを cwd として採用
  # (work tree は通常 .git の親)
  if [ -n "$seg_git_dir" ]; then
    if [[ "$seg_git_dir" == /* ]]; then
      cwd="$(dirname "$seg_git_dir")"
    else
      cwd="$(dirname "$cwd/$seg_git_dir")"
    fi
  fi

  return 0
}
