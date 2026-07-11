#!/bin/bash
# fetch-rate-limit.sh — サブスク usage limit の取得本体 (①→② フォールバック)
#
# /rate-limit:status skill が foreground で 1 回実行する。引数・stdin なし。
#
# 処理 (issue #225 の契約):
#   経路① statusline キャッシュ (lib/cache-paths.sh のパス) を読み、valid かつ 60 秒以内なら採用
#   経路② ①が使えないときのみ GET https://api.anthropic.com/api/oauth/usage
#   両方失敗 → exit 1 (stderr に経路ごとの失敗理由を必ず列挙)
#
# キャッシュ valid の定義 (すべて満たす。欠ければ stale として経路②へ):
#   - JSON parse 可能で written_at を持つ
#   - written_at が過去、かつ経過秒が 0〜60 (未来時刻は不正扱い)
#   - five_hour / seven_day の少なくとも一方の used_percentage が 0〜100 の数値
#
# 経路② の HTTP 契約:
#   - Authorization: Bearer <token> / anthropic-beta: oauth-2025-04-20 /
#     User-Agent: claude-code/<claude --version から抽出した X.Y.Z>
#   - version 抽出不能・claude CLI 不在 → 経路②を試行せず失敗扱い (429 バケット回避)
#   - curl --max-time 10、リトライなし (実行 1 回につき呼び出し最大 1 回)、-L 禁止、
#     接続先 https://api.anthropic.com 固定、token は --config - (stdin) 渡し
#   - レスポンスの期待フィールド欠落・値域外 → 不正値を黙って返さず経路②失敗として扱う
#
# 出力 (stdout、公開契約):
#   { "source": "statusline-cache" | "oauth-endpoint",
#     "fetched_at": "<ISO 8601 UTC>",
#     "cache_age_seconds": <①のみ>,
#     "five_hour": { "used_percentage": <0-100>, "resets_at": "<ISO 8601 UTC>" } | null,
#     "seven_day": 同上 | null,
#     "extras": <②のみ任意。seven_day_opus / seven_day_sonnet / extra_usage の非 null のみ> }
#
# 正規化マッピング (実機検証済み。2026-07-11、Linux/WSL2、Claude Code v2.1.207):
#   | 出力フィールド            | 経路① (statusline キャッシュ)          | 経路② (oauth/usage)              |
#   |---------------------------|------------------------------------------|-----------------------------------|
#   | source                    | "statusline-cache"                        | "oauth-endpoint"                   |
#   | fetched_at                | キャッシュの written_at (ISO のまま)      | 取得時刻 (date -u +%Y-%m-%dT%H:%M:%SZ) |
#   | cache_age_seconds         | now_epoch - written_at_epoch (0-60 検証済) | (出力しない)                       |
#   | five_hour.used_percentage | rate_limits.five_hour.used_percentage     | five_hour.utilization (0-100 実測) |
#   | five_hour.resets_at       | epoch なら ISO 8601 UTC に変換、ISO ならそのまま | ISO のままパススルー (実測: +00:00 オフセット付き文字列) |
#   | seven_day.*               | 同上                                       | 同上                                |
#   | extras                    | (出力しない)                              | seven_day_opus / seven_day_sonnet / extra_usage のうち非 null のみ。全 null ならキー省略 |
#   片 window が invalid (used_percentage が 0-100 の数値でない、resets_at 欠落等) → その window
#   は null。両方 invalid なら経路失敗 (キャッシュなら経路②へ、経路②なら exit 1)。
#
# 終了コード: 0 = いずれかの経路で取得成功 / 1 = 全経路失敗
# 依存: jq (必須)、curl・claude CLI (経路②のみ)。jq 不在は明示エラーで exit 1。

set +x

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

if ! command -v jq >/dev/null 2>&1; then
  echo "[rate-limit] jq が見つかりません。jq をインストールしてから再実行してください。" >&2
  exit 1
fi

# shellcheck source=lib/cache-paths.sh
source "$LIB_DIR/cache-paths.sh"
# shellcheck source=lib/portable-time.sh
source "$LIB_DIR/portable-time.sh"

# used_percentage / resets_at の組を正規化する。
# 数値域 (0-100) を外れる・resets_at が空の場合は文字列 "null" を返す
# (jq --argjson にそのまま渡せる形にするため JSON の null をそのまま文字列化している)。
normalize_window_values() {
  local pct="$1" resets_at="$2"

  if ! printf '%s' "$pct" | grep -Eq '^[0-9]+(\.[0-9]+)?$'; then
    printf 'null'
    return 0
  fi
  if ! awk -v v="$pct" 'BEGIN { exit !(v >= 0 && v <= 100) }'; then
    printf 'null'
    return 0
  fi
  if [ -z "$resets_at" ]; then
    printf 'null'
    return 0
  fi

  local resets_iso
  if printf '%s' "$resets_at" | grep -Eq '^[0-9]+$'; then
    # epoch 秒 → ISO 8601 UTC に変換する
    resets_iso=$(epoch_to_iso "$resets_at") || { printf 'null'; return 0; }
  else
    # ISO 文字列はそのままパススルー
    resets_iso="$resets_at"
  fi
  [ -n "$resets_iso" ] || { printf 'null'; return 0; }

  jq -n --argjson pct "$pct" --arg resets_at "$resets_iso" \
    '{used_percentage: $pct, resets_at: $resets_at}'
}

# ---- 経路①: statusline キャッシュ ----

extract_cache_window() {
  local field="$1" pct resets_at
  pct=$(jq -r --arg f "$field" '.rate_limits[$f].used_percentage // empty' "$RATE_LIMIT_CACHE_FILE" 2>/dev/null)
  resets_at=$(jq -r --arg f "$field" '.rate_limits[$f].resets_at // empty' "$RATE_LIMIT_CACHE_FILE" 2>/dev/null)
  normalize_window_values "$pct" "$resets_at"
}

try_cache() {
  if [ ! -f "$RATE_LIMIT_CACHE_FILE" ]; then
    echo "[rate-limit] 経路①: キャッシュファイルが存在しません ($RATE_LIMIT_CACHE_FILE)" >&2
    return 1
  fi

  if ! jq empty "$RATE_LIMIT_CACHE_FILE" >/dev/null 2>&1; then
    echo "[rate-limit] 経路①: キャッシュが JSON として parse できません" >&2
    return 1
  fi

  local written_at written_epoch now age
  written_at=$(jq -r '.written_at // empty' "$RATE_LIMIT_CACHE_FILE" 2>/dev/null)
  if [ -z "$written_at" ]; then
    echo "[rate-limit] 経路①: written_at フィールドがありません" >&2
    return 1
  fi

  written_epoch=$(iso_to_epoch "$written_at") || {
    echo "[rate-limit] 経路①: written_at の parse に失敗しました ($written_at)" >&2
    return 1
  }
  now=$(now_epoch)
  age=$((now - written_epoch))
  if [ "$age" -lt 0 ] || [ "$age" -gt 60 ]; then
    echo "[rate-limit] 経路①: キャッシュが stale です (age=${age}s、未来時刻または60秒超過)" >&2
    return 1
  fi

  local five_hour_json seven_day_json
  five_hour_json=$(extract_cache_window "five_hour")
  seven_day_json=$(extract_cache_window "seven_day")

  if [ "$five_hour_json" = "null" ] && [ "$seven_day_json" = "null" ]; then
    echo "[rate-limit] 経路①: five_hour / seven_day のいずれも valid な used_percentage を持ちません" >&2
    return 1
  fi

  jq -n \
    --arg source "statusline-cache" \
    --arg fetched_at "$written_at" \
    --argjson cache_age_seconds "$age" \
    --argjson five_hour "$five_hour_json" \
    --argjson seven_day "$seven_day_json" \
    '{source: $source, fetched_at: $fetched_at, cache_age_seconds: $cache_age_seconds, five_hour: $five_hour, seven_day: $seven_day}'
}

# ---- 経路②: GET https://api.anthropic.com/api/oauth/usage ----

extract_oauth_window() {
  local field="$1" resp_file="$2" pct resets_at
  pct=$(jq -r --arg f "$field" '.[$f].utilization // empty' "$resp_file" 2>/dev/null)
  resets_at=$(jq -r --arg f "$field" '.[$f].resets_at // empty' "$resp_file" 2>/dev/null)
  normalize_window_values "$pct" "$resets_at"
}

# extras は allowlist (seven_day_opus / seven_day_sonnet / extra_usage) の非 null のみ。
build_extras() {
  local resp_file="$1"
  jq -c '
    {seven_day_opus, seven_day_sonnet, extra_usage}
    | with_entries(select(.value != null))
  ' "$resp_file" 2>/dev/null
}

# レスポンスを取得し、正規化した JSON を stdout に返す。失敗理由は stderr に区別して出す。
request_oauth_usage() {
  local token="$1" version="$2" resp_file="$3"
  local http_code curl_exit

  # token はコマンドライン引数に置かず、curl --config - (stdin) の header 行で渡す
  # (ps 一覧への露出防止)。レスポンス保存用の一時ファイルは token を含まないので
  # umask 077 の mktemp で作成すれば十分 (呼び出し元で作成済み)。
  http_code=$(
    {
      printf 'header = "Authorization: Bearer %s"\n' "$token"
      printf 'header = "anthropic-beta: oauth-2025-04-20"\n'
      printf 'header = "User-Agent: claude-code/%s"\n' "$version"
      printf 'url = "https://api.anthropic.com/api/oauth/usage"\n'
    } | curl --config - --max-time 10 --silent --output "$resp_file" --write-out '%{http_code}' 2>/dev/null
  )
  curl_exit=$?

  if [ "$curl_exit" -ne 0 ]; then
    if [ "$curl_exit" -eq 28 ]; then
      echo "[rate-limit] 経路②: リクエストがタイムアウトしました (--max-time 10)" >&2
    else
      echo "[rate-limit] 経路②: 接続できませんでした (curl exit code: $curl_exit)" >&2
    fi
    return 1
  fi

  case "$http_code" in
    200) ;;
    401 | 403)
      echo "[rate-limit] 経路②: 認証エラーです (HTTP $http_code)。claude の再ログインが必要な可能性があります。" >&2
      return 1
      ;;
    429)
      echo "[rate-limit] 経路②: レート制限されました (HTTP 429)。リトライしません。" >&2
      return 1
      ;;
    *)
      echo "[rate-limit] 経路②: 予期しない HTTP ステータスです (HTTP $http_code)" >&2
      return 1
      ;;
  esac

  if ! jq empty "$resp_file" >/dev/null 2>&1; then
    echo "[rate-limit] 経路②: レスポンスが JSON として parse できません (schema 不一致)" >&2
    return 1
  fi

  local five_hour_json seven_day_json
  five_hour_json=$(extract_oauth_window "five_hour" "$resp_file")
  seven_day_json=$(extract_oauth_window "seven_day" "$resp_file")

  if [ "$five_hour_json" = "null" ] && [ "$seven_day_json" = "null" ]; then
    echo "[rate-limit] 経路②: five_hour / seven_day のいずれも期待する schema (utilization 0-100 の数値 + resets_at) に一致しません" >&2
    return 1
  fi

  local extras_json fetched_at
  extras_json=$(build_extras "$resp_file")
  fetched_at=$(now_iso)

  if [ -z "$extras_json" ] || [ "$extras_json" = "{}" ]; then
    jq -n \
      --arg source "oauth-endpoint" \
      --arg fetched_at "$fetched_at" \
      --argjson five_hour "$five_hour_json" \
      --argjson seven_day "$seven_day_json" \
      '{source: $source, fetched_at: $fetched_at, five_hour: $five_hour, seven_day: $seven_day}'
  else
    jq -n \
      --arg source "oauth-endpoint" \
      --arg fetched_at "$fetched_at" \
      --argjson five_hour "$five_hour_json" \
      --argjson seven_day "$seven_day_json" \
      --argjson extras "$extras_json" \
      '{source: $source, fetched_at: $fetched_at, five_hour: $five_hour, seven_day: $seven_day, extras: $extras}'
  fi
}

try_oauth() {
  local claude_version_output version token resp_file rc

  if ! command -v claude >/dev/null 2>&1; then
    echo "[rate-limit] 経路②: claude CLI が見つかりません。User-Agent を構成できないため試行しません。" >&2
    return 1
  fi

  claude_version_output=$(claude --version 2>/dev/null)
  version=$(printf '%s' "$claude_version_output" | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)
  if [ -z "$version" ]; then
    echo "[rate-limit] 経路②: claude --version から X.Y.Z を抽出できません (出力: $claude_version_output)" >&2
    return 1
  fi

  # shellcheck source=lib/read-oauth-token.sh
  source "$LIB_DIR/read-oauth-token.sh"
  token=$(read_oauth_token) || return 1

  umask 077
  resp_file=$(mktemp "${TMPDIR:-/tmp}/rate-limit-resp.XXXXXX" 2>/dev/null)
  if [ -z "$resp_file" ]; then
    echo "[rate-limit] 経路②: 一時ファイルの作成に失敗しました" >&2
    return 1
  fi

  request_oauth_usage "$token" "$version" "$resp_file"
  rc=$?
  rm -f "$resp_file"
  return "$rc"
}

# ---- メイン: ①→② フォールバック ----

output=""
if output=$(try_cache); then
  printf '%s\n' "$output"
  exit 0
fi

echo "[rate-limit] 経路①失敗。経路②を試行します。" >&2

if output=$(try_oauth); then
  printf '%s\n' "$output"
  exit 0
fi

echo "[rate-limit] 経路①・経路②ともに失敗しました。usage limit を取得できません。" >&2
exit 1
