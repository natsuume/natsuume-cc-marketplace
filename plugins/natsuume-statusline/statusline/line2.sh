#!/bin/bash
# statusline 2行目: レートリミット

# 単一レートリミット行を組み立てる
# 引数:
#   $1=ラベル(5h/7d), $2=使用率%, $3=リセット時刻(epoch秒 or ISO8601)
#   $4=バー幅（0 ならバーを描画しない: 残り幅計算用）
# 出力フォーマット: "5h: 62% (58m) [████░░░░░░]"
build_ratelimit_segment() {
  local label="$1" pct="$2" resets_at="$3" bar_width="${4:-20}"
  local color bar remaining segment display_pct int_pct

  # 表示用は format_pct（整数なら小数点なし）、判定用は floor の整数
  # （bash の算術評価は小数を扱えないため、progress_bar / rate_color には整数を渡す）
  display_pct=$(format_pct "$pct")
  [ -z "$display_pct" ] && return
  int_pct=$(int_pct_from_display "$display_pct")

  color=$(rate_color "$int_pct")
  remaining=$(time_remaining "$resets_at")

  # "label: P%" まで
  segment=$(printf '%s: %s%s%%%b' "$label" "$color" "$display_pct" "$RESET")
  # reset 残時間（バーの左側に置く）
  [ -n "$remaining" ] && segment+=$(printf ' (%s)' "$remaining")
  # バー（bar_width=0 のときは省略 → 非バー幅の事前計測に使う）
  if [ "$bar_width" -gt 0 ]; then
    bar=$(progress_bar "$int_pct" "$bar_width")
    segment+=$(printf ' %s%s%b' "$color" "$bar" "$RESET")
  fi

  printf '%s' "$segment"
}

# レートリミット表示（5h/7d のパーセンテージ、reset 残時間、プログレスバー）
# バー幅はターミナル幅に応じて可変。バーが極端に短くなる場合は最小幅でクランプ。
render_ratelimit() {
  local rate_5h="$1" reset_5h="$2" rate_7d="$3" reset_7d="$4"
  local term_width="${TERM_WIDTH:-80}"
  local separator=" | "
  local sep_w=${#separator}

  # 1段目: バーなしで両セグメントを組み立て、非バー部分の可視幅を測る
  local nonbar_5h="" nonbar_7d=""
  local total_nonbar=0 bar_count=0

  if [ -n "$rate_5h" ]; then
    nonbar_5h=$(build_ratelimit_segment "5h" "$rate_5h" "$reset_5h" 0)
    total_nonbar=$((total_nonbar + $(visible_length "$nonbar_5h")))
    bar_count=$((bar_count + 1))
  fi
  if [ -n "$rate_7d" ]; then
    nonbar_7d=$(build_ratelimit_segment "7d" "$rate_7d" "$reset_7d" 0)
    total_nonbar=$((total_nonbar + $(visible_length "$nonbar_7d")))
    bar_count=$((bar_count + 1))
  fi

  [ "$bar_count" -eq 0 ] && return

  # 2段目: 残り幅をバーに均等分配（最小4文字、最大20文字でクランプ）
  # バー周辺の装飾 = " [" + "]" の3文字 / バー
  local separators_total=$(( (bar_count - 1) * sep_w ))
  local bar_decorations=$(( bar_count * 3 ))
  local available=$(( term_width - total_nonbar - separators_total - bar_decorations ))
  local bar_width=$(( available / bar_count ))

  [ "$bar_width" -lt 4 ] && bar_width=4
  [ "$bar_width" -gt 20 ] && bar_width=20

  # 3段目: 決定したバー幅でセグメントを再構築
  local parts=()
  [ -n "$rate_5h" ] && parts+=("$(build_ratelimit_segment "5h" "$rate_5h" "$reset_5h" "$bar_width")")
  [ -n "$rate_7d" ] && parts+=("$(build_ratelimit_segment "7d" "$rate_7d" "$reset_7d" "$bar_width")")

  fit_segments "$separator" "$term_width" "${parts[@]}"
}
