#!/bin/bash
# cache-write-wrapper.sh — statusLine 入力 JSON のキャッシュ書き出し + 内側 statusline への委譲
#
# setup.sh が生成する launcher (~/.claude/rate-limit-statusline-launcher.sh) から呼ばれる。
# 直接 statusLine.command に登録しない (plugin update で消える version 固有パスのため。
# issue #51 / Claude Code bug #52079)。
#
# I/O 契約 (issue #225):
#   stdin  : Claude Code が statusLine に渡す JSON (全体を一度メモリに読み込む)
#   引数   : $1 = 内側 statusline コマンド文字列 1 引数 (省略可)。bash -c "$1" で実行し、
#            pipeline・引用符・環境変数参照を含む shell 文字列でも元の意味を保つ
#   stdout : 内側コマンドの出力を素通し (内側未指定なら空出力)
#   exit   : 内側コマンドの exit code をそのまま返す (内側未指定なら 0)
#
# キャッシュ書き込み:
#   - stdin JSON に .rate_limits があるときのみ、lib/cache-paths.sh のパスへ
#     { written_at, rate_limits } を atomic write (umask 077、同一ディレクトリ temp + mv)
#   - .rate_limits が無い tick (セッション最初の API 応答前、API key 認証環境等) は
#     キャッシュを上書きしない (既存キャッシュ保持)
#   - 書き込み失敗 (ディレクトリ作成不可等) は無音でスキップし、内側への委譲は続行する
#     (statusline 表示を壊さないことを最優先)

set +x

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$SCRIPT_DIR/../scripts/lib"

# stdin 全体を一度メモリに読み込む (statusline 入力 JSON。内側コマンドと共有するため)
input=$(cat)

# キャッシュ書き込みは何が起きても無音でスキップして良い (statusline を壊さないことが最優先)。
# jq 不在・lib 読み込み失敗・書き込み失敗、いずれも return 0 で早期終了し、
# 内側コマンドへの委譲 (delegate_to_inner) は必ず実行させる。
write_cache() {
  command -v jq >/dev/null 2>&1 || return 0
  [ -f "$LIB_DIR/cache-paths.sh" ] && [ -f "$LIB_DIR/portable-time.sh" ] || return 0

  # shellcheck source=../scripts/lib/cache-paths.sh
  source "$LIB_DIR/cache-paths.sh" 2>/dev/null || return 0
  # shellcheck source=../scripts/lib/portable-time.sh
  source "$LIB_DIR/portable-time.sh" 2>/dev/null || return 0

  # .rate_limits が無い (欠落 or null) tick はキャッシュを上書きしない。
  local rate_limits_json
  rate_limits_json=$(printf '%s' "$input" | jq -c '.rate_limits // empty' 2>/dev/null)
  [ -n "$rate_limits_json" ] && [ "$rate_limits_json" != "null" ] || return 0

  local written_at cache_json
  written_at=$(now_iso) || return 0
  cache_json=$(jq -n --arg written_at "$written_at" --argjson rate_limits "$rate_limits_json" \
    '{written_at: $written_at, rate_limits: $rate_limits}' 2>/dev/null) || return 0

  umask 077
  mkdir -p "$RATE_LIMIT_CACHE_DIR" 2>/dev/null || return 0

  local tmp
  tmp=$(mktemp "$RATE_LIMIT_CACHE_DIR/.rate_limits.XXXXXX" 2>/dev/null) || return 0
  if ! printf '%s\n' "$cache_json" > "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    return 0
  fi
  # 同一ディレクトリ内の mv は atomic rename になる (異なる FS 間コピーを避けるため
  # mktemp をキャッシュディレクトリ配下に作っている)。
  mv -f "$tmp" "$RATE_LIMIT_CACHE_FILE" 2>/dev/null || rm -f "$tmp"
  return 0
}

delegate_to_inner() {
  if [ -n "${1:-}" ]; then
    printf '%s' "$input" | bash -c "$1"
    return $?
  fi
  return 0
}

write_cache
delegate_to_inner "${1:-}"
exit $?
