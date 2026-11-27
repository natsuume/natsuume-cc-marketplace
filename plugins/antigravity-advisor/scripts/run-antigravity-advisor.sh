#!/bin/bash
# run-antigravity-advisor.sh
# antigravity-advisor plugin の相談 wrapper。antigravity-advisor-runner subagent が
# foreground の Bash 1 回で起動し、相談 request のファイルを Antigravity CLI (agy) に
# plan mode で読ませて、助言の本文だけを stdout に返す。
#
# ## 使い方
#
#   bash run-antigravity-advisor.sh <request file>
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
#   2 つ作る。1 つは request のコピーだけを置いて agy の --add-dir に渡すもの、もう 1 つは
#   agy の stdout と working tree の記録を置く、agy に渡さないもの。どちらも EXIT trap で
#   削除し、成功・失敗のどちらでも残さない
#
# ## 必要なコマンド
#
# - agy と jq が PATH 上にあること。どちらかが無ければ agy を起動せず exit 3。どちらが
#   無いのかは stderr のメッセージで区別する
# - git は cwd が git の work tree 内のときだけ使う
#
# ## 処理
#
# 1. 一時ディレクトリを作り、request をコピーする
# 2. cwd が git の work tree 内なら、`git rev-parse --verify -q HEAD` と
#    `git status --porcelain=v1 -z --untracked-files=all` の出力を記録する。どちらも
#    GIT_OPTIONAL_LOCKS=0 を付けて実行し、index の更新などの書き込みをさせない。
#    commit の無い repo では HEAD の出力が空になるが、失敗にせず空のまま比較に使う。
#    work tree の外なら記録も比較もしない
# 3. cwd を変えずに次を実行する。`-p` は直後の引数を値に取り、続けて別のフラグを
#    置くとエラーになるため、`-p <prompt>` をフラグ列の最後に置く。
#
#      agy --mode plan --output-format json [--model <model>] \
#        --settings '{"permissions":{"deny":["write_file(*)","command(*)","mcp(*)","read_url(*)"]}}' \
#        --add-dir <request の一時ディレクトリ> --print-timeout 25m -p <prompt>
#
#    --settings はこのセッションに限って、ファイルの書き込み・shell の実行・MCP・URL の
#    取得を権限設定で拒否し、ローカルのファイルの読み取りだけを許す。値は固定である。
#    prompt は英語の固定文で、コピーした request の絶対パスを含み、request の本文は
#    含まない。prompt は次を指示する:
#    - read-only の advisor として振る舞う
#    - shell コマンドを一切実行せず、ファイルの作成・編集もしない。読み取りのツールだけを使う
#    - コピーした request ファイルを読み、その問いに答える
#    - 必要ならカレントディレクトリ (リポジトリ) のファイルを読んで裏取りする
#    - request と同じ言語で答える
#    - 計画の承認待ちをせず、実装計画の artifact を作らない
# 4. 2 で記録した場合は、agy の終了コードに関わらず、終了後に同じ 2 つの出力を取り直して
#    比較する。取り直しに失敗した場合も変化したものとして扱う
# 5. agy の stdout を jq で検査し、JSON 1 つであることを確かめて `.response` を取り出す
#
# agy の成功時の stdout は
# `{"conversation_id":str,"status":"SUCCESS","response":str,"duration_seconds":num,"num_turns":int,"usage":{...}}`
# の JSON 1 つである。plan mode で shell の実行を試みると拒否され、`status` は
# "SUCCESS" のまま `"denied_actions":[{"action":"command","display_name":"Bash"}]` が
# 付き、応答は途中で終わる (2026-11-27 agy 1.5.18 / WSL2 で確認)。
#
# ## 終了コード
#
# | exit | runner の Failure class | 条件 |
# |---|---|---|
# | 0 | (成功) | 下のいずれにも該当しない |
# | 2 | invalid-request | 引数が不正、または ANTIGRAVITY_ADVISOR_MODEL が不正 |
# | 3 | agy-unavailable | agy または jq が PATH に無い |
# | 4 | agy-failed | agy が非 0 で終了した |
# | 5 | workspace-modified | cwd が git の work tree 内で、agy の実行前後に HEAD または working tree の状態が変わった |
# | 6 | incomplete-response | agy の stdout が JSON 1 つでない、`.status` が "SUCCESS" でない、`.denied_actions` が空でない、または `.response` が空 |
#
# agy の実行後に複数の条件が同時に成り立つ場合は、5 (workspace-modified) > 4 (agy-failed) > 6 (incomplete-response)
# の順に採用する。上の表にない失敗 (一時ディレクトリの作成、request のコピー、実行前の
# working tree の記録の失敗等) は exit 1 とし、runner は Failure class を other とする。
#
# ## stdout / stderr
#
# - stdout: exit 0 のときだけ `.response` の本文を出す。それ以外の内容は出さない。
#   0 以外で終了するときは stdout に何も出さない
# - stderr: 進捗と失敗の理由を日本語で出す。agy 自身の stderr はそのまま通す
#
# ## 制約
#
# Linux (WSL2 含む) と macOS (bash 3.2 / BSD ツール) の両方で動くこと。bash 4 以降の
# 拡張 (連想配列等) と GNU 専用のオプションを使わない。mktemp の `-t` は GNU と BSD で
# 意味が違うため使わず、`${TMPDIR:-/tmp}/<prefix>.XXXXXX` の形のテンプレートを明示する。
# set -u の下で空の配列を展開する箇所は `${arr[@]+"${arr[@]}"}` の形にする (bash 3.2 は
# 空配列の "${arr[@]}" を未定義変数として扱う)。

set -u

readonly LOG_PREFIX="[run-antigravity-advisor]"
readonly PERMISSION_SETTINGS='{"permissions":{"deny":["write_file(*)","command(*)","mcp(*)","read_url(*)"]}}'
readonly PRINT_TIMEOUT="25m"

request_dir=""
state_dir=""

log() {
  printf '%s %s\n' "$LOG_PREFIX" "$1" >&2
}

fail() {
  log "$2"
  exit "$1"
}

remove_temporary_directories() {
  if [ -n "$request_dir" ]; then
    rm -rf "$request_dir"
  fi
  if [ -n "$state_dir" ]; then
    rm -rf "$state_dir"
  fi
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

# cwd が git の work tree 内か。git が無い場合と work tree の外の場合は偽。
inside_git_work_tree() {
  command -v git > /dev/null 2>&1 || return 1
  [ "$(GIT_OPTIONAL_LOCKS=0 git rev-parse --is-inside-work-tree 2> /dev/null)" = "true" ]
}

# HEAD と working tree の状態を $1.head / $1.status に書く。git status が失敗したら偽。
# commit の無い repo では rev-parse が何も出さずに非 0 で終わるため、その終了コードは見ない。
record_workspace_state() {
  GIT_OPTIONAL_LOCKS=0 git rev-parse --verify -q HEAD > "$1.head" 2> /dev/null
  GIT_OPTIONAL_LOCKS=0 git status --porcelain=v1 -z --untracked-files=all > "$1.status" 2> /dev/null
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

# --- 一時ディレクトリと request のコピー ---
temporary_root=${TMPDIR:-/tmp}
request_dir=$(mktemp -d "$temporary_root/antigravity-advisor-request.XXXXXX") ||
  fail 1 "一時ディレクトリを作成できません ($temporary_root)"
state_dir=$(mktemp -d "$temporary_root/antigravity-advisor-state.XXXXXX") ||
  fail 1 "一時ディレクトリを作成できません ($temporary_root)"
request_dir_absolute=$(cd "$request_dir" && pwd -P) ||
  fail 1 "一時ディレクトリの絶対パスを取得できません ($request_dir)"
request_copy="$request_dir_absolute/request.md"
if ! cp -- "$request_file" "$request_copy"; then
  fail 1 "相談 request を一時ディレクトリにコピーできません"
fi

# --- 実行前の working tree の記録 ---
compare_workspace=0
if inside_git_work_tree; then
  compare_workspace=1
  if ! record_workspace_state "$state_dir/before"; then
    fail 1 "実行前の working tree の状態を記録できません"
  fi
fi

prompt="You are a read-only advisor. Do not run any shell commands, and do not create, edit, or delete any files: use only tools that read local files. Read the consultation request at ${request_copy} and answer the question it asks. If needed, read files in the current working directory (the repository) to verify your claims. Answer in the same language as the request. Do not wait for plan approval, and do not create implementation plan artifacts; reply with your advice directly."

# --- agy の実行 ---
log "agy に相談しています (plan mode、最大 ${PRINT_TIMEOUT})"
agy_status=0
agy --mode plan --output-format json ${model_args[@]+"${model_args[@]}"} \
  --settings "$PERMISSION_SETTINGS" \
  --add-dir "$request_dir_absolute" --print-timeout "$PRINT_TIMEOUT" \
  -p "$prompt" > "$state_dir/agy-stdout" || agy_status=$?

# --- 判定 (5 > 4 > 6) ---
if [ "$compare_workspace" -eq 1 ]; then
  if ! record_workspace_state "$state_dir/after"; then
    fail 5 "実行後の working tree の状態を取得できないため、変更されたものとして扱います"
  fi
  if ! cmp -s "$state_dir/before.head" "$state_dir/after.head"; then
    fail 5 "agy の実行中に HEAD が変わりました。git log で変更を確認してください"
  fi
  if ! cmp -s "$state_dir/before.status" "$state_dir/after.status"; then
    fail 5 "agy の実行中に working tree が変わりました。git status で変更を確認してください"
  fi
fi

if [ "$agy_status" -ne 0 ]; then
  fail 4 "agy が終了コード ${agy_status} で失敗しました"
fi

if ! is_complete_response "$state_dir/agy-stdout"; then
  fail 6 "agy の応答が不完全です (JSON でない、status が SUCCESS でない、拒否された操作がある、または応答が空)"
fi

# 取り出しに失敗したときに stdout へ途中まで出さないよう、一度ファイルに書いてから出す。
if ! jq -r -s '.[0].response' "$state_dir/agy-stdout" > "$state_dir/response"; then
  fail 1 "agy の応答から本文を取り出せません"
fi
cat "$state_dir/response"
log "agy から助言を受け取りました"
