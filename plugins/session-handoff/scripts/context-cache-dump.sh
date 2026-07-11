#!/bin/bash
# natsuume-statusline v0.6.0 の statusline/context-cache-dump.sh の同梱コピー
# (plugin 間でファイル参照できないため)。契約変更時は両 plugin を同時改版する。
#
# statusline context cache dump: session-handoff plugin (#228) 向け producer
#
# 役割: statusline stdin JSON の context_window データを per-session の一時 cache
#       ファイルへ書き出す。表示ロジックとは無関係な副作用であり、失敗しても
#       statusline の表示には一切影響させない (fail-open)。
#
# 出力先: ${TMPDIR:-/tmp}/natsuume-context-cache-<uid>/<sanitized_session_id>.json
#   uid = id -u (per-user 分離。他ユーザの共有 /tmp 経由の symlink/tampering を避ける)
#   sanitized_session_id = printf '%s' "$session_id" | tr -cd 'A-Za-z0-9._-'
#
# 出力スキーマ (jq -n で生成):
#   updated_at            : main.sh が stdin 受領直後に採時した epoch 秒 (number)
#   session_id            : サニタイズ前の session_id (string)
#   used_percentage       : 使用率 (number)
#   total_input_tokens    : 使用トークン数 (number、検証に通らない場合はキー省略)
#   context_window_size   : 最大コンテキスト長 (number、検証に通らない場合はキー省略)
#
# 直列化: per-session mkdir lock ($cache_dir/.lock-<sanitized>、mkdir は POSIX で atomic)。
# lock 保持区間は compare+mktemp+mv の数十 ms のみなので、競合時は 0.1 秒間隔で最大 2 回
# 再試行してから諦める (通常経路に sleep は入らない。競合時のみ最大 ~200ms プロセス終了が
# 遅れる)。10 秒以上古い lock は前回プロセスの異常終了とみなし破棄してから再取得を試みる。
#
# monotonic guard: 保証するのは「cache の updated_at (秒値) が減少しない」ことのみ。
# 同一秒内は last-writer-wins で、ペイロードの実時間順序までは保証しない (最大 1 描画間隔
# ぶん古いサンプルが残りうるが、より後の秒に採時された次の書き込みで自己回復する)。
# consumer は advisory 用途 (session-handoff の閾値検知) を前提とする。
#
# 安全対策: cache ディレクトリが symlink、または実行ユーザの所有でない場合は書き込まない。
#
# fail-open 方針: session_id 欠落/サニタイズ後空、used_percentage 欠落/非数値、
# received_at 非数値、uid 取得不可、jq 不在、mkdir/chmod/lock/mktemp/mv 失敗
# — いずれも無音でスキップし、stdout/stderr には何も出力せず常に return 0 とする。
# 呼び出し元 (main.sh) はこの関数呼び出し前に全表示出力を終えていること (表示への不干渉)。

# 引数: $1=session_id (raw), $2=used_percentage, $3=total_input_tokens,
#       $4=context_window_size, $5=received_at (main.sh が stdin 受領直後に採時した epoch 秒)
dump_context_cache() {
  local session_id="$1" used_percentage="$2" total_input_tokens="$3" context_window_size="$4" received_at="$5"

  # main.sh 冒頭で jq 存在チェック済みだが、この関数単体で呼ばれる将来変更に備え二重に防御する。
  command -v jq >/dev/null 2>&1 || return 0

  [ -z "$session_id" ] && return 0
  local sanitized
  sanitized=$(printf '%s' "$session_id" | tr -cd 'A-Za-z0-9._-')
  [ -z "$sanitized" ] && return 0

  # used_percentage が数値でなければ書き込まない (context_window null/欠落を含む)。既存 cache は残す。
  [[ "$used_percentage" =~ ^[0-9]+(\.[0-9]+)?$ ]] || return 0

  # received_at (updated_at に使う値) が数値でなければ採時失敗とみなし書き込まない。
  [[ "$received_at" =~ ^[0-9]+$ ]] || return 0

  # per-user にキャッシュディレクトリを分離する。uid が取得できない環境では書き込まない。
  local uid
  uid=$(id -u 2>/dev/null)
  [[ "$uid" =~ ^[0-9]+$ ]] || return 0

  local updated_at="$received_at"

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

  local cache_dir="${TMPDIR:-/tmp}/natsuume-context-cache-$uid"
  local cache_file="$cache_dir/$sanitized.json"
  local lock_dir="$cache_dir/.lock-$sanitized"

  # atomic write: サブシェル内で umask 077 → symlink/所有者チェック → per-session lock
  # 取得 → monotonic guard → mktemp → 書き込み → mv -f
  # (呼び出し元プロセスの umask を変更しないためサブシェルに閉じる)。
  # mktemp を cache_dir 配下に作るのは、異なる FS 間コピーを避けて mv を atomic rename にするため。
  (
    umask 077
    mkdir -p "$cache_dir" 2>/dev/null || exit 1

    # 他ユーザによる symlink 差し込み対策: cache_dir 自体が symlink なら書き込まない。
    [ -L "$cache_dir" ] && exit 1

    # 非所有ディレクトリ対策: 実行ユーザの所有でなければ書き込まない。
    [ -O "$cache_dir" ] || exit 1

    chmod 700 "$cache_dir" 2>/dev/null || exit 1

    # stale lock 破棄: mtime が現在時刻 (received_at で代用) より 10 秒以上古ければ
    # 前回プロセスの異常終了とみなし破棄する (失敗しても続行、best-effort)。
    if [ -d "$lock_dir" ]; then
      lock_mtime=$(stat -c %Y "$lock_dir" 2>/dev/null || stat -f %m "$lock_dir" 2>/dev/null)
      if [[ "$lock_mtime" =~ ^[0-9]+$ ]] && [ $((received_at - lock_mtime)) -ge 10 ]; then
        rmdir "$lock_dir" 2>/dev/null
      fi
    fi

    # lock 取得 (mkdir は POSIX で atomic)。lock 保持者が自分より新しい描画とは限らない
    # (描画は lock 取得前に完了している) ため、競合時は即諦めず 0.1 秒間隔で最大 2 回
    # 再試行する。保持区間は数十 ms なので通常は初回の再試行で取得できる。
    acquired=0
    for attempt in 1 2 3; do
      if mkdir "$lock_dir" 2>/dev/null; then
        acquired=1
        break
      fi
      [ "$attempt" -lt 3 ] && sleep 0.1
    done
    [ "$acquired" -eq 1 ] || exit 1
    trap 'rmdir "$lock_dir" 2>/dev/null' EXIT

    # monotonic guard: 既存 cache の updated_at が新しい updated_at より大きければ
    # 古いデータで新しい cache を上書きしない (同値は last-writer-wins で書き込み続行)。
    if [ -f "$cache_file" ]; then
      existing_updated_at=$(jq -r '.updated_at // 0' "$cache_file" 2>/dev/null)
      [[ "$existing_updated_at" =~ ^[0-9]+$ ]] || existing_updated_at=0
      [ "$existing_updated_at" -gt "$updated_at" ] && exit 0
    fi

    tmp=$(mktemp "$cache_dir/.ctx.XXXXXX" 2>/dev/null) || exit 1
    printf '%s\n' "$cache_json" > "$tmp" 2>/dev/null || { rm -f "$tmp"; exit 1; }
    mv -f "$tmp" "$cache_file" 2>/dev/null || { rm -f "$tmp"; exit 1; }
  ) 2>/dev/null

  return 0
}
