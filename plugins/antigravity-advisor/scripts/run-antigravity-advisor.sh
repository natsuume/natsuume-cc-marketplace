#!/bin/bash
# run-antigravity-advisor.sh
# antigravity-advisor plugin の相談 wrapper。antigravity-advisor-runner subagent が
# foreground の Bash 1 回で起動し、相談 request のファイルを Antigravity CLI (agy) に
# 読ませて、助言の本文だけを stdout に返す。
#
# ## 使い方
#
#   bash run-antigravity-advisor.sh <request file>
#
# wrapper を起動したときの cwd を相談対象のリポジトリとみなす。
#
# ## 引数
#
# - ちょうど 1 つ。相談 request を書いたファイルのパス
# - 引数のパスそのものが symlink でなく、存在する通常ファイルで、空 (0 byte) でないこと。
#   親ディレクトリの経路に含まれる symlink (macOS の /var -> /private/var 等) は問わない
# - 違反は exit 2
#
# ## 環境変数
#
# - ANTIGRAVITY_ADVISOR_MODEL: 空または未設定なら agy に --model を渡さず、agy の既定の
#   model を使う。非空なら `^[A-Za-z0-9._-]+$` に一致する場合だけ `--model <値>` として
#   渡し、一致しなければ agy を起動せず exit 2。一致の判定はロケールの影響を受けない
#   よう LC_ALL=C の下で行う (agy と jq には LC_ALL=C を渡さない)
# - TMPDIR: wrapper 専用の一時ディレクトリを `${TMPDIR:-/tmp}` の直下に `mktemp -d` で
#   2 つ作る。1 つは agy の cwd にする空のディレクトリで、request のコピーだけを置く。
#   もう 1 つは agy の stdout / stderr と working tree の記録を置く、agy に渡さないもの。
#   作ったディレクトリが git の work tree の中にある (`git -C <dir> rev-parse
#   --is-inside-work-tree` が成功する、または祖先に `.git` がある) 場合は削除して `/tmp`
#   の直下に作り直し、それでも中なら exit 1。どちらも EXIT trap で削除し、成功・失敗の
#   どちらでも残さない
#
# ## 必要なコマンド
#
# - agy と jq が PATH 上にあること。どちらかが無ければ agy を起動せず exit 3。どちらが
#   無いのかは stderr のメッセージで区別する
# - git は、一時ディレクトリの場所の検査と、cwd が git の work tree 内のときの比較に使う
#
# ## 処理
#
# 1. 2 つの一時ディレクトリを作り、request を agy の cwd にするディレクトリへコピーする
# 2. cwd が git の work tree 内なら、次の 4 つを記録する。work tree の外なら記録も比較も
#    しない。wrapper が実行する git コマンドにはすべて `GIT_OPTIONAL_LOCKS=0` と
#    `-c core.fsmonitor=false -c core.splitIndex=false` を付け、index の更新や fsmonitor の
#    コマンドを起こさない
#    - `<git-dir>/config`・`config.worktree`・`hooks/` 配下・`info/` 配下の各ファイルの
#      path と、内容のハッシュ (`git hash-object --no-filters`) と実行権の一覧
#      (worktree では git-dir と common dir の両方)
#    - `git symbolic-ref -q HEAD` (detached なら空) と `git rev-parse --verify -q HEAD`
#      (commit の無い repo なら空) と `git for-each-ref --format='%(refname) %(objectname)'`
#      の出力
#    - `git status --porcelain=v1 -z --untracked-files=all` の出力
#    - working tree 全体 (ignore されていない未追跡のファイルを含む) の tree OID。
#      一時的な index (`GIT_INDEX_FILE`) と一時的な object ディレクトリ
#      (`GIT_OBJECT_DIRECTORY`。元の object ディレクトリは `GIT_ALTERNATE_OBJECT_DIRECTORIES`
#      で読み取り用に参照する) を記録用の一時ディレクトリに置き、`git read-tree HEAD`
#      (commit の無い repo では `--empty`) → `git update-index -q --refresh` →
#      `git add -A` → `git write-tree` で求める。リポジトリの object ディレクトリと本物の
#      index には書き込まない
# 3. agy の cwd にするディレクトリへサブシェルの中で `cd` して (wrapper 自身の cwd は
#    変えない)、次を実行する。`--add-dir` は使わない。`-p` は直後の引数を値に取り、続けて
#    別のフラグを置くとエラーになるため、`-p <prompt>` をフラグ列の最後に置く。
#
#      agy --mode plan --output-format json [--model <model>] \
#        --settings '{"allowNonWorkspaceAccess":true,"permissions":{"deny":["write_file(*)","command(*)","mcp(*)","read_url(*)","execute_url(*)","download_url(*)"]}}' \
#        --print-timeout 25m -p <prompt>
#
#    agy の stdout と stderr は記録用の一時ディレクトリのファイルに書く。
#    agy は cwd のリポジトリの `.agents/` (hook・MCP・rules・skills) を読み込むため、
#    相談対象のリポジトリではなく git の work tree の外の空のディレクトリを cwd にする。
#    --settings はこのセッションに限って、ファイルの書き込み・shell の実行・MCP・URL の
#    取得・実行・download を権限設定で拒否する。値は固定である。
#    prompt は英語の固定文で、request のコピーの絶対パスと、相談対象のリポジトリのルートの
#    絶対パス (cwd が git の work tree 内ならその toplevel、外ならその cwd) を含み、request
#    の本文は含まない。prompt は次を指示する:
#    - read-only の advisor として振る舞う
#    - shell コマンドを一切実行せず、ファイルの作成・編集・削除もしない。ローカルの
#      ファイルを読むツールだけを使う
#    - request のコピーを読み、その問いに答える
#    - リポジトリのファイルは絶対パスで読む (一覧・検索のツールは使えない可能性がある)。
#      必要ならそれらを読んで裏取りする
#    - request と同じ言語で答える
#    - 計画の承認待ちをせず、実装計画の artifact を作らない
# 4. 2 で記録した場合は、agy の終了コードに関わらず、終了後に同じ順で取り直して比較する。
#    `.git` の設定・hook・info が変わっていれば、後続の記録 (git add の実行を含む) を
#    行わずに判定する。取り直しに失敗した場合も変化したものとして扱う
# 5. agy の stdout を jq で検査し、JSON 1 つであることを確かめて `.response` を取り出す
#
# agy の成功時の stdout は
# `{"conversation_id":str,"status":"SUCCESS","response":str,"duration_seconds":num,"num_turns":int,"usage":{...}}`
# の JSON 1 つである。承認を求めずに拒否された tool の呼び出し (soft-deny) は
# `denied_actions` に記録され、応答は空のまま終わる。deny の規則で拒否された tool の
# 呼び出しは `denied_actions` に記録されない (2026-11-27 agy 1.5.18 / WSL2 で確認)。
#
# ## 終了コード
#
# | exit | runner の Failure class | 条件 |
# |---|---|---|
# | 0 | (成功) | 下のいずれにも該当しない |
# | 2 | invalid-request | 引数が不正、または ANTIGRAVITY_ADVISOR_MODEL が不正 |
# | 3 | agy-unavailable | agy または jq が PATH に無い |
# | 4 | agy-failed | agy が非 0 で終了した |
# | 5 | workspace-modified | cwd が git の work tree 内で、agy の実行前後に `.git` の設定・hook・info、HEAD・ref、status、working tree の内容のいずれかが変わった |
# | 6 | incomplete-response | agy の stdout が JSON 1 つでない、`.status` が "SUCCESS" でない、`.denied_actions` が空でない、または `.response` が空 |
#
# agy の実行後に複数の条件が同時に成り立つ場合は、5 (workspace-modified) > 4 (agy-failed) > 6 (incomplete-response)
# の順に採用する。上の表にない失敗 (一時ディレクトリの作成、work tree の外に一時ディレクトリを
# 作れない、request のコピー、実行前の記録の失敗等) は exit 1 とし、runner は Failure class を
# other とする。
#
# ## stdout / stderr
#
# - stdout: exit 0 のときだけ `.response` の本文を出す。それ以外の内容は出さない。
#   0 以外で終了するときは stdout に何も出さない
# - stderr: 成功の経路では何も出さない。失敗のときだけ、理由を日本語で出す。agy の
#   stderr はそのまま流さず、agy が失敗したときに終了コードと先頭の 1 行 (200 文字まで、
#   制御文字を除く) だけを要約として出す
#
# ## 制約
#
# Linux (WSL2 含む) と macOS (bash 3.2 / BSD ツール) の両方で動くこと。bash 4 以降の
# 拡張 (連想配列等) と GNU 専用のオプションを使わない。mktemp の `-t` は GNU と BSD で
# 意味が違うため使わず、`<root>/<prefix>.XXXXXX` の形のテンプレートを明示する。
# set -u の下で空の配列を展開する箇所は `${arr[@]+"${arr[@]}"}` の形にする (bash 3.2 は
# 空配列の "${arr[@]}" を未定義変数として扱う)。git は `rev-parse --path-format=absolute`
# を使うため 2.31 以降を前提とする。

set -u

readonly LOG_PREFIX="[run-antigravity-advisor]"
readonly PERMISSION_SETTINGS='{"allowNonWorkspaceAccess":true,"permissions":{"deny":["write_file(*)","command(*)","mcp(*)","read_url(*)","execute_url(*)","download_url(*)"]}}'
readonly PRINT_TIMEOUT="25m"
readonly FALLBACK_TEMPORARY_ROOT="/tmp"

agy_directory=""
state_directory=""
created_directory=""
repository_root=""
git_directory=""
git_common_directory=""
object_directory=""

log() {
  printf '%s %s\n' "$LOG_PREFIX" "$1" >&2
}

fail() {
  log "$2"
  exit "$1"
}

remove_temporary_directories() {
  if [ -n "$agy_directory" ]; then
    rm -rf "$agy_directory"
  fi
  if [ -n "$state_directory" ]; then
    rm -rf "$state_directory"
  fi
}

# wrapper が実行する git コマンドの共通形。index の更新・fsmonitor のコマンド・split index
# の共有ファイルの書き込みを起こさない。
run_git() {
  GIT_OPTIONAL_LOCKS=0 git -c core.fsmonitor=false -c core.splitIndex=false "$@"
}

# 一時的な index と object ディレクトリで、相談対象のリポジトリに対して git を実行する。
# $1: index のファイル、$2: object ディレクトリ、以降: git の引数
run_git_with_temporary_index() {
  local index_file=$1 objects=$2
  shift 2
  GIT_INDEX_FILE=$index_file GIT_OBJECT_DIRECTORY=$objects \
    GIT_ALTERNATE_OBJECT_DIRECTORIES=$object_directory GIT_OPTIONAL_LOCKS=0 \
    git -c core.fsmonitor=false -c core.splitIndex=false -C "$repository_root" "$@"
}

# 値が model 名として使える文字 (英数字と . _ -) だけでできているか。ロケールによって
# 文字範囲の解釈が変わらないよう、LC_ALL=C の subshell の中で判定する。
is_valid_model_name() {
  (
    LC_ALL=C
    case $1 in
      '' | *[!A-Za-z0-9._-]*) exit 1 ;;
    esac
    exit 0
  )
}

git_is_available() {
  command -v git > /dev/null 2>&1
}

# wrapper の cwd が git の work tree 内か。git が無い場合と work tree の外の場合は偽。
cwd_is_inside_git_work_tree() {
  git_is_available || return 1
  [ "$(run_git rev-parse --is-inside-work-tree 2> /dev/null)" = "true" ]
}

# 絶対パスのディレクトリ $1 が git の管理下にあるか。git の判定に加え、git の設定に
# よらず祖先に `.git` があるかも見る。
is_under_git_management() {
  local directory=$1
  if git_is_available && run_git -C "$directory" rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    return 0
  fi
  while :; do
    if [ -e "$directory/.git" ]; then
      return 0
    fi
    if [ "$directory" = "/" ]; then
      return 1
    fi
    directory=$(dirname "$directory")
  done
}

# git の work tree の外に一時ディレクトリを作り、その絶対パスを created_directory に入れる。
# `${TMPDIR:-/tmp}` で作ったものが git の管理下なら削除して /tmp で作り直す。
# 戻り値: 0 成功、1 作成の失敗、2 どちらの場所でも git の管理下になった
create_directory_outside_work_tree() {
  local prefix=$1 root raw absolute
  created_directory=""
  for root in "${TMPDIR:-/tmp}" "$FALLBACK_TEMPORARY_ROOT"; do
    raw=$(mktemp -d "$root/$prefix.XXXXXX") || return 1
    if ! absolute=$(cd "$raw" && pwd -P); then
      rm -rf "$raw"
      return 1
    fi
    if ! is_under_git_management "$absolute"; then
      created_directory=$absolute
      return 0
    fi
    rm -rf "$raw"
  done
  return 2
}

# $1 の役割の一時ディレクトリを作り、失敗したら exit 1 で終える。
create_private_directory_or_fail() {
  local status=0
  create_directory_outside_work_tree "$1" || status=$?
  case $status in
    0) ;;
    2) fail 1 "git の work tree の外に一時ディレクトリを作れません (TMPDIR と /tmp のどちらも git の管理下です)" ;;
    *) fail 1 "一時ディレクトリを作成できません (${TMPDIR:-/tmp})" ;;
  esac
}

# 相談対象のリポジトリの toplevel・git-dir・common dir・object ディレクトリを求める。
prepare_git_context() {
  repository_root=$(run_git rev-parse --show-toplevel) || return 1
  git_directory=$(run_git rev-parse --absolute-git-dir) || return 1
  git_common_directory=$(run_git rev-parse --path-format=absolute --git-common-dir) || return 1
  object_directory=$(run_git rev-parse --path-format=absolute --git-path objects) || return 1
  [ -n "$repository_root" ] && [ -n "$git_directory" ] && [ -n "$git_common_directory" ] && [ -n "$object_directory" ]
}

# `.git` の設定・hook・info の各ファイルの path・種類・内容のハッシュを $1.control に書く。
# ハッシュは work tree の外 (記録用の一時ディレクトリ) で求め、リポジトリの設定を読まない。
record_control_files() {
  local directory entry file hash mode
  for directory in "$git_directory" "$git_common_directory"; do
    for entry in config config.worktree hooks info; do
      if [ -e "$directory/$entry" ] || [ -L "$directory/$entry" ]; then
        find "$directory/$entry" ! -type d -print 2> /dev/null
      fi
    done
  done | LC_ALL=C sort -u > "$1.control-paths"
  : > "$1.control"
  while IFS= read -r file; do
    if [ -L "$file" ]; then
      printf 'link %s %s\n' "$file" "$(readlink "$file")" >> "$1.control"
    elif [ -f "$file" ]; then
      hash=$(run_git -C "$state_directory" hash-object --no-filters -- "$file") || return 1
      mode=plain
      if [ -x "$file" ]; then
        mode=executable
      fi
      printf 'file %s %s %s\n' "$file" "$mode" "$hash" >> "$1.control"
    else
      printf 'other %s\n' "$file" >> "$1.control"
    fi
  done < "$1.control-paths"
}

# HEAD の参照先・HEAD の commit・すべての ref を $1.refs に書く。
record_refs() {
  printf 'symbolic-ref %s\nhead %s\n' \
    "$(run_git -C "$repository_root" symbolic-ref -q HEAD 2> /dev/null)" \
    "$(run_git -C "$repository_root" rev-parse --verify -q HEAD 2> /dev/null)" > "$1.refs"
  run_git -C "$repository_root" for-each-ref --format='%(refname) %(objectname)' >> "$1.refs" 2> /dev/null
}

record_status() {
  run_git -C "$repository_root" status --porcelain=v1 -z --untracked-files=all > "$1.status" 2> /dev/null
}

# working tree 全体の tree OID を $1.tree に書く。index と object は $1.index / $1.objects
# (記録用の一時ディレクトリ) に置き、リポジトリの object ディレクトリと index は変えない。
record_tree() {
  local index_file="$1.index" objects="$1.objects"
  mkdir "$objects" || return 1
  if [ -n "$(run_git -C "$repository_root" rev-parse --verify -q HEAD 2> /dev/null)" ]; then
    run_git_with_temporary_index "$index_file" "$objects" read-tree HEAD > /dev/null 2>&1 || return 1
  else
    run_git_with_temporary_index "$index_file" "$objects" read-tree --empty > /dev/null 2>&1 || return 1
  fi
  # 内容が変わっていないファイルの stat 情報を埋め、add で object を書かせないようにする。
  # 変わったファイルがあると非 0 で終わるため、終了コードは見ない。
  run_git_with_temporary_index "$index_file" "$objects" update-index -q --refresh > /dev/null 2>&1
  run_git_with_temporary_index "$index_file" "$objects" add -A > /dev/null 2>&1 || return 1
  run_git_with_temporary_index "$index_file" "$objects" write-tree > "$1.tree" 2> /dev/null
}

record_workspace_state() {
  record_control_files "$1" && record_refs "$1" && record_status "$1" && record_tree "$1"
}

# 実行後の状態を記録しながら実行前と比べ、変化があれば exit 5 で終える。
# `.git` の設定・hook・info が変わっていれば、git add を実行する前に終える。
fail_if_workspace_changed() {
  local before="$state_directory/before" after="$state_directory/after"
  record_control_files "$after" ||
    fail 5 "実行後の .git の設定・hook・info を記録できないため、変更されたものとして扱います"
  cmp -s "$before.control" "$after.control" ||
    fail 5 "agy の実行中に .git の設定・hook・info が変わりました"
  record_refs "$after" ||
    fail 5 "実行後の ref を記録できないため、変更されたものとして扱います"
  cmp -s "$before.refs" "$after.refs" ||
    fail 5 "agy の実行中に HEAD または ref が変わりました。git log と git for-each-ref で変更を確認してください"
  record_status "$after" ||
    fail 5 "実行後の working tree の状態を記録できないため、変更されたものとして扱います"
  cmp -s "$before.status" "$after.status" ||
    fail 5 "agy の実行中に working tree の状態が変わりました。git status で変更を確認してください"
  record_tree "$after" ||
    fail 5 "実行後の working tree の内容を記録できないため、変更されたものとして扱います"
  cmp -s "$before.tree" "$after.tree" ||
    fail 5 "agy の実行中に working tree のファイルの内容が変わりました。git diff で変更を確認してください"
}

# agy の stderr の先頭の 1 行を、制御文字を除いて 200 文字までに切り詰めて出す。
agy_stderr_head() {
  sed -n '/[^[:space:]]/{p;q;}' "$state_directory/agy-stderr" 2> /dev/null |
    tr -d '\000-\010\013-\037' | cut -c 1-200
}

# agy の stdout が JSON 1 つの完全な成功応答か。
is_complete_response() {
  jq -e -s '
    length == 1
    and (.[0] | type == "object")
    and .[0].status == "SUCCESS"
    and ((.[0].denied_actions // []) | type == "array" and length == 0)
    and (.[0].response | type == "string" and length > 0)
  ' "$1" > /dev/null 2>&1
}

trap remove_temporary_directories EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# --- 引数 ---
if [ "$#" -ne 1 ]; then
  fail 2 "引数には相談 request のファイルを 1 つだけ指定してください (指定された数: $#)"
fi
request_file=$1
if [ -L "$request_file" ]; then
  fail 2 "相談 request のファイルに symlink は指定できません: $request_file"
fi
if [ ! -f "$request_file" ]; then
  fail 2 "相談 request のファイルが存在しないか、通常のファイルではありません: $request_file"
fi
if [ ! -s "$request_file" ]; then
  fail 2 "相談 request のファイルが空です: $request_file"
fi

# --- 必要なコマンド ---
if ! command -v agy > /dev/null 2>&1; then
  fail 3 "agy (Antigravity CLI) が PATH にありません。agy を install してログインしてください"
fi
if ! command -v jq > /dev/null 2>&1; then
  fail 3 "jq が PATH にありません。agy の応答を検査するために jq を install してください"
fi

# --- model ---
model=${ANTIGRAVITY_ADVISOR_MODEL:-}
model_args=()
if [ -n "$model" ]; then
  if ! is_valid_model_name "$model"; then
    fail 2 "ANTIGRAVITY_ADVISOR_MODEL には英数字と . _ - だけでできた model 名を指定してください"
  fi
  model_args=(--model "$model")
fi

# --- 相談対象のリポジトリ ---
compare_workspace=0
if cwd_is_inside_git_work_tree; then
  compare_workspace=1
  prepare_git_context || fail 1 "相談対象の git リポジトリの情報を取得できません (git 2.31 以降が必要です)"
else
  repository_root=$(pwd -P) || fail 1 "cwd の絶対パスを取得できません"
fi

# --- 一時ディレクトリと request のコピー ---
create_private_directory_or_fail antigravity-advisor-cwd
agy_directory=$created_directory
create_private_directory_or_fail antigravity-advisor-state
state_directory=$created_directory
request_copy="$agy_directory/request.md"
if ! cp -- "$request_file" "$request_copy"; then
  fail 1 "相談 request を一時ディレクトリにコピーできません"
fi

# --- 実行前の記録 ---
if [ "$compare_workspace" -eq 1 ]; then
  record_workspace_state "$state_directory/before" ||
    fail 1 "実行前の working tree の状態を記録できません"
fi

prompt="You are a read-only advisor. Do not run any shell commands, and do not create, edit, or delete any files: use only tools that read local files. Read the consultation request at ${request_copy} and answer the question it asks. The repository under discussion is at ${repository_root}. Read repository files by their absolute paths under that directory, because directory listing and search tools may be unavailable; read them as needed to verify your claims. Answer in the same language as the request. Do not wait for plan approval, and do not create implementation plan artifacts; reply with your advice directly."

# --- agy の実行 (cwd は空の一時ディレクトリ。wrapper の cwd は変えない) ---
agy_status=0
(
  cd "$agy_directory" || exit 125
  exec agy --mode plan --output-format json ${model_args[@]+"${model_args[@]}"} \
    --settings "$PERMISSION_SETTINGS" \
    --print-timeout "$PRINT_TIMEOUT" -p "$prompt"
) > "$state_directory/agy-stdout" 2> "$state_directory/agy-stderr" || agy_status=$?

# --- 判定 (5 > 4 > 6) ---
if [ "$compare_workspace" -eq 1 ]; then
  fail_if_workspace_changed
fi

if [ "$agy_status" -ne 0 ]; then
  agy_error=$(agy_stderr_head)
  if [ -n "$agy_error" ]; then
    fail 4 "agy が終了コード ${agy_status} で失敗しました。agy のエラー出力の先頭: ${agy_error}"
  fi
  fail 4 "agy が終了コード ${agy_status} で失敗しました"
fi

if ! is_complete_response "$state_directory/agy-stdout"; then
  fail 6 "agy の応答が不完全です (JSON でない、status が SUCCESS でない、拒否された操作がある、または応答が空)"
fi

# 取り出しに失敗したときに stdout へ途中まで出さないよう、一度ファイルに書いてから出す。
if ! jq -r -s '.[0].response' "$state_directory/agy-stdout" > "$state_directory/response" 2> /dev/null; then
  fail 1 "agy の応答から本文を取り出せません"
fi
cat "$state_directory/response"
