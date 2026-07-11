#!/bin/bash
# statusline context cache dump: session-handoff plugin (#228) 向け producer
#
# 役割: statusline stdin JSON の context_window データを per-session の一時 cache
#       ファイルへ書き出す。表示ロジックとは無関係な副作用であり、失敗しても
#       statusline の表示には一切影響させない (fail-open)。
#
# 出力先: ${TMPDIR:-/tmp}/natsuume-context-cache/<sanitized_session_id>.json
#   sanitized_session_id = printf '%s' "$session_id" | tr -cd 'A-Za-z0-9._-'
#
# 出力スキーマ (jq -n で生成):
#   updated_at            : date +%s の値 (number)
#   session_id            : サニタイズ前の session_id (string)
#   used_percentage       : 使用率 (number)
#   total_input_tokens    : 使用トークン数 (number、検証に通らない場合はキー省略)
#   context_window_size   : 最大コンテキスト長 (number、検証に通らない場合はキー省略)
#
# fail-open 方針: session_id 欠落/サニタイズ後空、used_percentage 欠落/非数値、
# jq 不在、mkdir/mktemp/mv 失敗 — いずれも無音でスキップし、stdout/stderr には
# 何も出力せず常に return 0 とする。呼び出し元 (main.sh) はこの関数呼び出し前に
# 全表示出力を終えていること (表示への不干渉)。

# 引数: $1=session_id (raw), $2=used_percentage, $3=total_input_tokens, $4=context_window_size
dump_context_cache() {
  local session_id="$1" used_percentage="$2" total_input_tokens="$3" context_window_size="$4"

  # main.sh 冒頭で jq 存在チェック済みだが、この関数単体で呼ばれる将来変更に備え二重に防御する。
  command -v jq >/dev/null 2>&1 || return 0

  [ -z "$session_id" ] && return 0
  local sanitized
  sanitized=$(printf '%s' "$session_id" | tr -cd 'A-Za-z0-9._-')
  [ -z "$sanitized" ] && return 0

  # used_percentage が数値でなければ書き込まない (context_window null/欠落を含む)。既存 cache は残す。
  [[ "$used_percentage" =~ ^[0-9]+(\.[0-9]+)?$ ]] || return 0

  local updated_at
  updated_at=$(date +%s) || return 0

  # total_input_tokens / context_window_size は検証に通らない場合キーごと省略する
  # (jq_args / jq_filter を対で組み立てる)。
  local jq_args=(-n --arg session_id "$session_id" --argjson used_percentage "$used_percentage" --argjson updated_at "$updated_at")
  local jq_filter='{updated_at: $updated_at, session_id: $session_id, used_percentage: $used_percentage}'

  if [[ "$total_input_tokens" =~ ^[0-9]+$ ]]; then
    jq_args+=(--argjson total_input_tokens "$total_input_tokens")
    jq_filter+=' + {total_input_tokens: $total_input_tokens}'
  fi

  if [[ "$context_window_size" =~ ^[0-9]+$ ]]; then
    jq_args+=(--argjson context_window_size "$context_window_size")
    jq_filter+=' + {context_window_size: $context_window_size}'
  fi

  local cache_json
  cache_json=$(jq "${jq_args[@]}" "$jq_filter" 2>/dev/null) || return 0
  [ -z "$cache_json" ] && return 0

  local cache_dir="${TMPDIR:-/tmp}/natsuume-context-cache"
  local cache_file="$cache_dir/$sanitized.json"

  # atomic write: サブシェル内で umask 077 → cache ディレクトリ内に mktemp → 書き込み → mv -f
  # (呼び出し元プロセスの umask を変更しないためサブシェルに閉じる)。
  # mktemp を cache_dir 配下に作るのは、異なる FS 間コピーを避けて mv を atomic rename にするため。
  (
    umask 077
    mkdir -p "$cache_dir" 2>/dev/null || exit 1
    tmp=$(mktemp "$cache_dir/.ctx.XXXXXX" 2>/dev/null) || exit 1
    printf '%s\n' "$cache_json" > "$tmp" 2>/dev/null || { rm -f "$tmp"; exit 1; }
    mv -f "$tmp" "$cache_file" 2>/dev/null || { rm -f "$tmp"; exit 1; }
  ) 2>/dev/null

  return 0
}
