#!/bin/bash
# weekly-scoped-limits.sh — per-model 週次レートリミット (例: Fable) のデータ供給
#
# Claude Code が statusline stdin に渡す rate_limits には five_hour / seven_day しか
# 無く (v2.1.207 の実 stdin dump で確認)、Fable 等のモデル別週次枠は含まれない。
# 一方 OAuth usage API (https://api.anthropic.com/api/oauth/usage) の limits[] には
# {kind:"weekly_scoped", percent, resets_at, scope.model.display_name} が返ってくる
# (2026-07-12 実測)。本ファイルはこの API を TTL 付き file cache + background fetch で
# 取得し、3 行目のレンダラへ表示データを供給する。
#
# ■ データ優先順位 (main.sh 側の配線契約)
#   1. stdin の rate_limits.model_scoped[] — Claude Code バイナリに schema が存在する
#      公式経路 (issue #231 時点では実 stdin 未出現)。emit され始めたらこちらを優先し、
#      本ファイルの cache 経路は読まず background fetch も起動しない。
#   2. 本ファイルの cache (OAuth usage API 由来)。
#
# ■ cache ファイル
#   パス: ${XDG_CACHE_HOME:-$HOME/.cache}/natsuume-statusline/weekly-scoped.json
#   権限: ディレクトリ・ファイルとも所有者のみ (umask 077)。
#   書き込み: 同一ディレクトリの mktemp + mv による atomic write のみ。
#   schema (書き手 = 本ファイルの fetch worker):
#     {
#       "fetched_at": <最後に成功した fetch の epoch 秒。成功前は 0>,
#       "consecutive_failures": <連続失敗回数。成功で 0 にリセット>,
#       "next_attempt_at": <この epoch 秒より前は再 fetch しない>,
#       "weekly_scoped": [
#         {"display_name": "<モデル名 (例: Fable)>",
#          "percent": <使用率 0-100 の数値>,
#          "resets_at": "<ISO 8601 文字列 (API の値をそのまま保存)>"}
#       ]
#     }
#   失敗時も weekly_scoped は前回成功値を保持したまま counters のみ更新する
#   (レートリミットは変化が緩やかで、古い値でも非表示より情報価値がある)。
#
# ■ 提供する関数 (main.sh から source して使う)
#
# read_weekly_scoped_entries
#   cache を読み、表示可能な entry を 1 行 1 entry の TSV で stdout に返す:
#     <display_name>\t<percent>\t<resets_at>
#   fail-open: cache 不在 / jq parse 不能 / weekly_scoped 空・欠落 / percent が
#   数値でない entry はスキップし、出力可能なものが無ければ空出力 (exit 0)。
#
# kick_weekly_scoped_refresh
#   全表示出力の後に main.sh が呼ぶ (表示への不干渉は context-cache-dump.sh と
#   同じ配置契約)。以下すべてを満たすときのみ background worker を起動する:
#     - curl と jq が存在する
#     - now >= next_attempt_at (cache 不在時は常に満たす)
#     - now - fetched_at > TTL (300 秒)
#     - mkdir lock を取得できた
#   worker は `bash <本ファイル> --fetch-worker` を `</dev/null >/dev/null 2>&1 &`
#   で切り離して起動し、statusline のレンダリングを一切ブロックしない。
#   kick 側で取得した lock は worker が引き継いで解放する (worker の trap が担う)。
#   `&` 起動の失敗は同期検知できないため、万一 worker が起動せず lock が残った
#   場合は stale 判定 (120 秒) による奪取で回収する。
#
# ■ fetch worker (直接実行モード: `bash weekly-scoped-limits.sh --fetch-worker`。
#   source されたときはこの節の関数定義のみが行われ、実行はされない)
#   1. lock 下で TTL / next_attempt_at を再確認する (kick 判定との TOCTOU 対策)
#   2. OAuth token を読む:
#      - ~/.claude/.credentials.json の .claudeAiOauth.accessToken
#      - 無ければ macOS (Darwin) に限り Keychain
#        (security find-generic-password -s "Claude Code-credentials" -w)
#      - token は argv / ログ / stderr / 一時ファイルに書かない。冒頭 set +x。
#        形式検証 (改行・引用符・空白を含まない 1 行) を通らなければ失敗扱い。
#   3. curl --disable --config - (stdin 経由で Authorization header を渡す。
#      ps 露出防止) --max-time 10 で GET https://api.anthropic.com/api/oauth/usage
#      header: anthropic-beta: oauth-2025-04-20 / User-Agent: claude-code/<version>
#      (<version> は `claude --version` から抽出。取得不能なら固定文字列にせず失敗扱い)
#   4. 応答の limits[] から kind == "weekly_scoped" かつ scope.model.display_name が
#      非空文字列かつ percent が数値の entry のみ抽出して cache に atomic write。
#      抽出結果 0 件は「成功・空配列」として保存する (常に空なら 3 行目は 7d のみ)。
#   5. 失敗時 (token 不能 / curl 失敗 / 非 200 / JSON 不能):
#      consecutive_failures += 1、
#      next_attempt_at = now + min(60 * 2^(consecutive_failures - 1), 1800)
#      成功時: consecutive_failures = 0、next_attempt_at = now + TTL。
#   6. 終了時に必ず lock を解放する (trap)。
#
# ■ lock
#   パス: <cache_dir>/.fetch.lock (mkdir による排他)。
#   stale 判定: lock dir の mtime が 120 秒より古ければ奪取してよい
#   (curl の 10 秒 timeout に対して十分長い)。
#
# ■ 依存と縮退
#   jq: 必須 (プラグイン全体の必須依存)。curl: optional — 無ければ fetch せず
#   cache 供給のみ (fail-open)。python3: 不要 (ISO 8601 の解釈は表示側の
#   lib.sh time_remaining が担う)。
#   いかなる失敗でも stdout / stderr に出力しない (statusline を汚さない)。

# 自ファイルの絶対パス。source 時点 (main.sh の SCRIPT_DIR は絶対パス) で解決される。
# kick 側が background worker を起動する際の対象パスとして使う。
WEEKLY_SCOPED_SELF_PATH="${BASH_SOURCE[0]}"

WEEKLY_SCOPED_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/natsuume-statusline"
WEEKLY_SCOPED_CACHE_FILE="$WEEKLY_SCOPED_CACHE_DIR/weekly-scoped.json"
WEEKLY_SCOPED_LOCK_DIR="$WEEKLY_SCOPED_CACHE_DIR/.fetch.lock"
WEEKLY_SCOPED_TTL=300
WEEKLY_SCOPED_LOCK_STALE_SEC=120
WEEKLY_SCOPED_MAX_BACKOFF=1800
WEEKLY_SCOPED_API_URL="https://api.anthropic.com/api/oauth/usage"

# cache を読み、表示可能な entry を TSV で返す (display_name, percent, resets_at)。
# fail-open: cache 不在 / jq parse 不能 / percent が数値でない entry はすべて
# 無音でスキップし、無ければ空出力のまま exit 0 とする。
read_weekly_scoped_entries() {
  [ -f "$WEEKLY_SCOPED_CACHE_FILE" ] || return 0
  command -v jq >/dev/null 2>&1 || return 0

  jq -r '
    try (
      (.weekly_scoped // [])
      | .[]?
      | select(type == "object")
      | select((.percent | type) == "number")
      | [(.display_name // ""), (.percent | tostring), (.resets_at // "")]
      | @tsv
    ) catch empty
  ' "$WEEKLY_SCOPED_CACHE_FILE" 2>/dev/null

  return 0
}

# curl/jq の存在、TTL、lock 取得のすべてを満たすときのみ background worker を起動する。
# 表示への不干渉のため、main.sh は全出力の後にのみこの関数を呼ぶ契約。
kick_weekly_scoped_refresh() {
  command -v curl >/dev/null 2>&1 || return 0
  command -v jq >/dev/null 2>&1 || return 0

  local now fetched_at=0 next_attempt_at=0
  now=$(date +%s 2>/dev/null) || return 0

  if [ -f "$WEEKLY_SCOPED_CACHE_FILE" ]; then
    fetched_at=$(jq -r '.fetched_at // 0' "$WEEKLY_SCOPED_CACHE_FILE" 2>/dev/null)
    [[ "$fetched_at" =~ ^[0-9]+$ ]] || fetched_at=0
    next_attempt_at=$(jq -r '.next_attempt_at // 0' "$WEEKLY_SCOPED_CACHE_FILE" 2>/dev/null)
    [[ "$next_attempt_at" =~ ^[0-9]+$ ]] || next_attempt_at=0
  fi

  [ "$now" -ge "$next_attempt_at" ] || return 0
  [ $((now - fetched_at)) -gt "$WEEKLY_SCOPED_TTL" ] || return 0

  (
    umask 077
    mkdir -p "$WEEKLY_SCOPED_CACHE_DIR" 2>/dev/null || exit 1

    # stale lock 奪取: mtime が WEEKLY_SCOPED_LOCK_STALE_SEC 秒より古ければ
    # 前回 worker の異常終了とみなし破棄する (curl の 10 秒 timeout に対し十分長い)。
    if [ -d "$WEEKLY_SCOPED_LOCK_DIR" ]; then
      lock_mtime=$(stat -c %Y "$WEEKLY_SCOPED_LOCK_DIR" 2>/dev/null || stat -f %m "$WEEKLY_SCOPED_LOCK_DIR" 2>/dev/null)
      if [[ "$lock_mtime" =~ ^[0-9]+$ ]] && [ $((now - lock_mtime)) -gt "$WEEKLY_SCOPED_LOCK_STALE_SEC" ]; then
        rm -rf "$WEEKLY_SCOPED_LOCK_DIR" 2>/dev/null
      fi
      unset lock_mtime
    fi

    mkdir "$WEEKLY_SCOPED_LOCK_DIR" 2>/dev/null || exit 1

    # worker を切り離して起動する。lock はここから worker の trap へ引き継がれる。
    # `&` 起動の成否は同期的に検知できない (`$?` は常に 0) ため、万一 worker が
    # 起動せず lock が残っても、stale 判定 (120 秒) が次回 kick で回収する。
    bash "$WEEKLY_SCOPED_SELF_PATH" --fetch-worker </dev/null >/dev/null 2>&1 &
  ) 2>/dev/null

  return 0
}

# OAuth token を読む (statusline 用に stderr メッセージを一切出さない縮退版。
# 元実装: plugins/rate-limit/scripts/lib/read-oauth-token.sh)。
# 取得できれば stdout に 1 行で返し exit 0、できなければ非ゼロ exit のみ (無音)。
weekly_scoped_read_token() {
  local creds_file token newline_count keychain_payload

  creds_file="$HOME/.claude/.credentials.json"
  token=""

  if [ -f "$creds_file" ] && command -v jq >/dev/null 2>&1; then
    token=$(jq -r '.claudeAiOauth.accessToken // empty' "$creds_file" 2>/dev/null)
  fi

  if [ -z "$token" ] && [ "$(uname -s 2>/dev/null)" = "Darwin" ] && command -v security >/dev/null 2>&1; then
    keychain_payload=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null)
    if [ -n "$keychain_payload" ]; then
      if printf '%s' "$keychain_payload" | jq empty >/dev/null 2>&1; then
        token=$(printf '%s' "$keychain_payload" \
          | jq -r '(.claudeAiOauth.accessToken | select(type == "string")) // empty' 2>/dev/null)
      else
        token="$keychain_payload"
      fi
    fi
  fi

  [ -z "$token" ] && return 1

  # token 形式検証 (curl --config への injection 防止): 改行を含まない 1 行、
  # かつ引用符・空白を含まないこと。
  newline_count=$(printf '%s' "$token" | wc -l | tr -d ' ')
  if [ "$newline_count" != "0" ] \
    || printf '%s' "$token" | grep -q "[[:space:]]" \
    || printf '%s' "$token" | grep -q "'" \
    || printf '%s' "$token" | grep -q '"'; then
    return 1
  fi

  printf '%s\n' "$token"
}

# `claude --version` から X.Y.Z 形式のバージョン文字列を抽出する。抽出不能なら失敗扱い
# (User-Agent を固定文字列にすり替えない)。
weekly_scoped_claude_version() {
  command -v claude >/dev/null 2>&1 || return 1
  local raw version
  raw=$(claude --version 2>/dev/null) || return 1
  version=$(printf '%s' "$raw" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)
  [ -z "$version" ] && return 1
  printf '%s' "$version"
}

# OAuth usage API を GET する。token は argv に載せず --config (stdin) 経由で渡す。
# 成功時 (curl 自体が完走し http code を取得できた場合): 0 を返し、
# WEEKLY_SCOPED_LAST_HTTP_CODE / WEEKLY_SCOPED_LAST_BODY に結果を格納する
# (bash は複数値を直接 return できないための呼び出し規約)。
weekly_scoped_fetch_usage() {
  local token="$1" version="$2"
  local config raw

  config=$(printf 'header = "Authorization: Bearer %s"\nheader = "anthropic-beta: oauth-2025-04-20"\nheader = "User-Agent: claude-code/%s"\n' "$token" "$version")

  raw=$(printf '%s' "$config" | curl --disable --config - --silent --show-error \
    --max-time 10 --write-out $'\n%{http_code}' "$WEEKLY_SCOPED_API_URL" 2>/dev/null) || return 1

  WEEKLY_SCOPED_LAST_HTTP_CODE="${raw##*$'\n'}"
  WEEKLY_SCOPED_LAST_BODY="${raw%$'\n'*}"

  [[ "$WEEKLY_SCOPED_LAST_HTTP_CODE" =~ ^[0-9]+$ ]] || return 1
  return 0
}

# cache を jq で生成し、同一ディレクトリの mktemp + mv で atomic に書き込む。
# 引数: $1=fetched_at, $2=consecutive_failures, $3=next_attempt_at,
#       $4=weekly_scoped (jq 済みの JSON 配列文字列)
weekly_scoped_atomic_write() {
  local fetched_at="$1" consecutive_failures="$2" next_attempt_at="$3" weekly_scoped_json="$4"

  (
    umask 077
    mkdir -p "$WEEKLY_SCOPED_CACHE_DIR" 2>/dev/null || exit 1
    tmp=$(mktemp "$WEEKLY_SCOPED_CACHE_DIR/.weekly-scoped.XXXXXX" 2>/dev/null) || exit 1
    jq -n \
      --argjson fetched_at "$fetched_at" \
      --argjson consecutive_failures "$consecutive_failures" \
      --argjson next_attempt_at "$next_attempt_at" \
      --argjson weekly_scoped "$weekly_scoped_json" \
      '{fetched_at: $fetched_at, consecutive_failures: $consecutive_failures, next_attempt_at: $next_attempt_at, weekly_scoped: $weekly_scoped}' \
      > "$tmp" 2>/dev/null || { rm -f "$tmp"; exit 1; }
    mv -f "$tmp" "$WEEKLY_SCOPED_CACHE_FILE" 2>/dev/null || { rm -f "$tmp"; exit 1; }
  ) 2>/dev/null
}

# 成功時の cache 更新: consecutive_failures=0, next_attempt_at=now+TTL。
weekly_scoped_record_success() {
  local now="$1" weekly_scoped_json="$2"
  local next_attempt_at=$((now + WEEKLY_SCOPED_TTL))
  weekly_scoped_atomic_write "$now" 0 "$next_attempt_at" "$weekly_scoped_json"
}

# 失敗時の cache 更新: fetched_at と weekly_scoped は前回値を保持し、
# consecutive_failures をインクリメント、next_attempt_at を指数 backoff で延長する。
# 引数: $1=前回の fetched_at, $2=前回の consecutive_failures
weekly_scoped_record_failure() {
  local prev_fetched_at="$1" prev_failures="$2"
  local now new_failures backoff next_attempt_at prev_weekly_scoped existing

  now=$(date +%s 2>/dev/null) || now="$prev_fetched_at"
  new_failures=$((prev_failures + 1))

  # 2 ** (new_failures - 1) は new_failures が大きいと桁あふれしうるため、
  # 上限 (1800 秒) に確実に到達する指数で頭打ちしてから計算する。
  if [ "$new_failures" -gt 20 ]; then
    backoff="$WEEKLY_SCOPED_MAX_BACKOFF"
  else
    backoff=$(( 60 * (2 ** (new_failures - 1)) ))
    [ "$backoff" -gt "$WEEKLY_SCOPED_MAX_BACKOFF" ] && backoff="$WEEKLY_SCOPED_MAX_BACKOFF"
  fi
  next_attempt_at=$((now + backoff))

  prev_weekly_scoped='[]'
  if [ -f "$WEEKLY_SCOPED_CACHE_FILE" ]; then
    existing=$(jq -c '.weekly_scoped // []' "$WEEKLY_SCOPED_CACHE_FILE" 2>/dev/null)
    [ -n "$existing" ] && prev_weekly_scoped="$existing"
  fi

  weekly_scoped_atomic_write "$prev_fetched_at" "$new_failures" "$next_attempt_at" "$prev_weekly_scoped"
}

# fetch worker 本体。lock 保持は kick 側から引き継ぐ前提 (trap で必ず解放する)。
weekly_scoped_fetch_worker() {
  set +x
  trap 'rmdir "$WEEKLY_SCOPED_LOCK_DIR" 2>/dev/null' EXIT

  local now fetched_at=0 next_attempt_at=0 consecutive_failures=0
  now=$(date +%s 2>/dev/null) || return 0

  if [ -f "$WEEKLY_SCOPED_CACHE_FILE" ]; then
    fetched_at=$(jq -r '.fetched_at // 0' "$WEEKLY_SCOPED_CACHE_FILE" 2>/dev/null)
    [[ "$fetched_at" =~ ^[0-9]+$ ]] || fetched_at=0
    next_attempt_at=$(jq -r '.next_attempt_at // 0' "$WEEKLY_SCOPED_CACHE_FILE" 2>/dev/null)
    [[ "$next_attempt_at" =~ ^[0-9]+$ ]] || next_attempt_at=0
    consecutive_failures=$(jq -r '.consecutive_failures // 0' "$WEEKLY_SCOPED_CACHE_FILE" 2>/dev/null)
    [[ "$consecutive_failures" =~ ^[0-9]+$ ]] || consecutive_failures=0
  fi

  # TOCTOU 再確認: kick 判定後に他プロセスが既に成功・再試行済みなら何もしない。
  if [ "$now" -lt "$next_attempt_at" ] || [ $((now - fetched_at)) -le "$WEEKLY_SCOPED_TTL" ]; then
    return 0
  fi

  local token version
  token=$(weekly_scoped_read_token) || {
    weekly_scoped_record_failure "$fetched_at" "$consecutive_failures"
    return 0
  }
  version=$(weekly_scoped_claude_version) || {
    weekly_scoped_record_failure "$fetched_at" "$consecutive_failures"
    return 0
  }

  WEEKLY_SCOPED_LAST_HTTP_CODE=""
  WEEKLY_SCOPED_LAST_BODY=""
  if ! weekly_scoped_fetch_usage "$token" "$version"; then
    weekly_scoped_record_failure "$fetched_at" "$consecutive_failures"
    return 0
  fi
  token=""

  if [ "$WEEKLY_SCOPED_LAST_HTTP_CODE" != "200" ]; then
    weekly_scoped_record_failure "$fetched_at" "$consecutive_failures"
    return 0
  fi

  local weekly_scoped_json
  weekly_scoped_json=$(printf '%s' "$WEEKLY_SCOPED_LAST_BODY" | jq -c '
    [ (.limits // [])[]?
      | try (
          select(.kind == "weekly_scoped")
          | select((.scope.model.display_name | type) == "string")
          | select((.scope.model.display_name | length) > 0)
          | select((.percent | type) == "number")
          | {display_name: .scope.model.display_name, percent: .percent, resets_at: (.resets_at // "")}
        ) catch empty
    ]
  ' 2>/dev/null)

  if [ -z "$weekly_scoped_json" ]; then
    weekly_scoped_record_failure "$fetched_at" "$consecutive_failures"
    return 0
  fi

  weekly_scoped_record_success "$now" "$weekly_scoped_json"
}

# 直接実行時 (`bash weekly-scoped-limits.sh --fetch-worker`) のみ worker を発動する。
# source されたとき ($0 がこのファイルと一致しない) は関数定義のみで何も実行しない。
if [ "${BASH_SOURCE[0]}" = "$0" ] && [ "${1:-}" = "--fetch-worker" ]; then
  weekly_scoped_fetch_worker
fi
