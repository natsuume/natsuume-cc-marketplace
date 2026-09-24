#!/bin/bash
# fable-weekly-usage.sh
# cross-model-advisor が相談ごとに fable-advisor-runner を起動するかを決める Fable 週次枠の
# 使用率判定を提供する。bin/cross-model-advisor-fable-usage が source する。
#
# ## 判定仕様
#
# agent-discipline の block-fable-subagent.sh と同じ
# 判定を自前で実装する (plugin 間で実行時にコードを参照しない)。
#   - 入力: ${XDG_CACHE_HOME:-$HOME/.cache}/natsuume-statusline/weekly-scoped.json (producer は
#     natsuume-statusline。本 lib は読むだけで書き込まず、OAuth usage API も呼ばない)
#   - 閾値: env FABLE_WEEKLY_MAX_PERCENT (前後空白を trim した 0〜100 の 10 進整数)。未設定・空・
#     範囲外・非整数は既定値 80
#   - weekly_scoped[] のうち display_name が大文字小文字を無視して fable を含み percent が数値の
#     entry の最大 percent を使い、percent <= 閾値 なら利用可 (比較は小数を扱える jq で行う)
#   - cache が symlink / 通常ファイルでない / 存在しない / 読めない / JSON document が 1 つで
#     ない / fetched_at が欠落・非数値 / now - fetched_at > 1800 (stale) / weekly_scoped が
#     欠落・非配列 / Fable entry が無い / 現在時刻を取得できない / jq が無い場合は使用率不明。
#     fetched_at が未来時刻でも stale とはみなさない (時計ずれの許容)
#
# ## 関数と結果の受け渡し
#
# fable_weekly_usage を呼ぶと、次のグローバル変数に結果を設定する (exit はしない)。
#   FABLE_USAGE_STATUS    available (利用可) / over (超過) / unknown (不明)
#   FABLE_USAGE_PERCENT   判定に使った使用率 (available / over のときのみ)
#   FABLE_USAGE_THRESHOLD 判定に使った閾値
#   FABLE_USAGE_DETAIL    unknown のときの理由 (1 行)

# FABLE_USAGE_* は source した呼び出し側が読むグローバル変数。
# shellcheck disable=SC2034
fable_weekly_usage() {
  FABLE_USAGE_STATUS="unknown"
  FABLE_USAGE_PERCENT=""
  FABLE_USAGE_DETAIL=""

  FABLE_USAGE_THRESHOLD=$(printf '%s' "${FABLE_WEEKLY_MAX_PERCENT:-}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  if ! printf '%s' "$FABLE_USAGE_THRESHOLD" | grep -qE '^(0|[1-9][0-9]?|100)$'; then
    FABLE_USAGE_THRESHOLD=80
  fi

  local cache_file now content output rc lines status percent end
  cache_file="${XDG_CACHE_HOME:-${HOME:-}/.cache}/natsuume-statusline/weekly-scoped.json"

  if ! command -v jq >/dev/null 2>&1; then
    FABLE_USAGE_DETAIL="jq が無く使用率 cache を読み取れません。"
    return 0
  fi
  if [ -L "$cache_file" ]; then
    FABLE_USAGE_DETAIL="使用率 cache ($cache_file) が symlink です。"
    return 0
  fi
  if [ ! -f "$cache_file" ]; then
    FABLE_USAGE_DETAIL="使用率 cache ($cache_file) が通常ファイルとして存在しません。"
    return 0
  fi
  if [ ! -r "$cache_file" ]; then
    FABLE_USAGE_DETAIL="使用率 cache ($cache_file) を読み取れません。"
    return 0
  fi

  now=$(date +%s 2>/dev/null)
  if ! printf '%s' "$now" | grep -qE '^[0-9]+$'; then
    FABLE_USAGE_DETAIL="現在時刻を取得できず、使用率 cache の鮮度を判定できません。"
    return 0
  fi

  content=$(cat "$cache_file" 2>/dev/null)

  # 検査と閾値比較を jq に閉じる (percent は小数を取りうる)。--slurp で JSON document がちょうど
  # 1 つの場合だけ判定する。出力は 3 行 (status / percent / 固定終端 END) で、jq の exit status
  # と行数・終端行も検証し、契約どおりでなければ不明として扱う (fail-closed)。
  output=$(printf '%s' "$content" | jq -rs \
    --argjson now "$now" --argjson threshold "$FABLE_USAGE_THRESHOLD" '
    def emit(status; percent): "\(status)\n\(percent)\nEND";
    if length != 1 then emit("invalid"; "")
    else .[0] |
    if type != "object" then emit("invalid"; "")
    elif (.fetched_at | type) != "number" then emit("fetched_at"; "")
    elif ($now - .fetched_at) > 1800 then emit("stale"; "")
    elif (.weekly_scoped | type) != "array" then emit("entries"; "")
    else
      [ .weekly_scoped[]
        | select(type == "object")
        | select((.display_name | type) == "string")
        | select(.display_name | ascii_downcase | contains("fable"))
        | select((.percent | type) == "number")
        | .percent
      ] as $percents
      | if ($percents | length) == 0 then emit("entries"; "")
        else ($percents | max) as $top
          | if $top > $threshold then emit("over"; $top) else emit("available"; $top) end
        end
    end
    end
  ' 2>/dev/null)
  rc=$?

  lines=$(printf '%s\n' "$output" | grep -c '')
  { read -r status; read -r percent; read -r end; } <<FABLE_USAGE_EOF
$output
FABLE_USAGE_EOF
  if [ "$rc" -ne 0 ] || [ "$lines" -ne 3 ] || [ "$end" != "END" ]; then
    FABLE_USAGE_DETAIL="使用率 cache を JSON として読み取れません。"
    return 0
  fi

  case "$status" in
    available|over)
      FABLE_USAGE_STATUS="$status"
      FABLE_USAGE_PERCENT="$percent"
      ;;
    fetched_at)
      FABLE_USAGE_DETAIL="使用率 cache の fetched_at が欠落しているか数値ではありません。"
      ;;
    stale)
      FABLE_USAGE_DETAIL="使用率 cache が古すぎます (fetched_at が 1800 秒より前)。"
      ;;
    entries)
      FABLE_USAGE_DETAIL="使用率 cache に Fable の週次枠 entry (display_name が fable を含み percent が数値) が見つかりません。"
      ;;
    *)
      FABLE_USAGE_DETAIL="使用率 cache を JSON として読み取れません。"
      ;;
  esac
  return 0
}
