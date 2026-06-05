#!/bin/bash
# statusline 2行目: context 使用量 + レートリミット (5h/7d)

# バーの最大幅。ターミナルが広くてもこれ以上は伸ばさず、グラフを短く保つ。
GAUGE_MAX_BAR_WIDTH=10

# 単一ゲージ行を組み立てる（context / 5h / 7d 共通）
# 引数:
#   $1=ラベル(ctx/5h/7d), $2=使用率%, $3=リセット時刻(epoch秒 or ISO8601。無ければ空),
#   $4=バー幅（0 ならバーを描画しない: 残り幅計算用）
# 出力フォーマット: "5h: 62% (58m) [████░░]" / "ctx: 45% [████░░]"（reset 無し）
build_gauge_segment() {
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
  # reset 残時間（バーの左側に置く）。context のように resets_at が空なら省略される。
  [ -n "$remaining" ] && segment+=$(printf ' (%s)' "$remaining")
  # バー（bar_width=0 のときは省略 → 非バー幅の事前計測に使う）
  if [ "$bar_width" -gt 0 ]; then
    bar=$(progress_bar "$int_pct" "$bar_width")
    segment+=$(printf ' %s%s%b' "$color" "$bar" "$RESET")
  fi

  printf '%s' "$segment"
}

# 2行目を描画する。context 使用量と 5h/7d レートリミットを
# "label: P% (reset) [bar]" 形式のゲージとして横並びにする。
# バー幅はターミナル幅に応じて可変。極端に短くなる場合は最小幅でクランプ。
render_line2() {
  local ctx_pct="$1" rate_5h="$2" reset_5h="$3" rate_7d="$4" reset_7d="$5"
  local term_width="${TERM_WIDTH:-80}"
  local separator=" | "
  local sep_w=${#separator}

  # 描画対象を (label, pct, reset) の3つ組として収集する。
  # pct が空のものは Claude Code がまだ値を渡していない
  # （context はセッション初期/compact 直後、ratelimit は非サブスク等）ため除外する。
  local labels=() pcts=() resets=()
  if [ -n "$ctx_pct" ]; then
    labels+=("ctx"); pcts+=("$ctx_pct"); resets+=("")
  fi
  if [ -n "$rate_5h" ]; then
    labels+=("5h"); pcts+=("$rate_5h"); resets+=("$reset_5h")
  fi
  if [ -n "$rate_7d" ]; then
    labels+=("7d"); pcts+=("$rate_7d"); resets+=("$reset_7d")
  fi

  local count=${#labels[@]}
  [ "$count" -eq 0 ] && return

  # 1段目: バーなしで各ゲージを組み立て、非バー部分の可視幅の総和を測る
  local total_nonbar=0 i nonbar
  for ((i = 0; i < count; i++)); do
    nonbar=$(build_gauge_segment "${labels[$i]}" "${pcts[$i]}" "${resets[$i]}" 0)
    total_nonbar=$((total_nonbar + $(visible_length "$nonbar")))
  done

  # 2段目: 残り幅をバーに均等分配（最小4文字、最大 GAUGE_MAX_BAR_WIDTH でクランプ）
  # バー周辺の装飾 = " [" + "]" の3文字 / バー
  local separators_total=$(( (count - 1) * sep_w ))
  local bar_decorations=$(( count * 3 ))
  local available=$(( term_width - total_nonbar - separators_total - bar_decorations ))
  local bar_width=$(( available / count ))

  [ "$bar_width" -lt 4 ] && bar_width=4
  [ "$bar_width" -gt "$GAUGE_MAX_BAR_WIDTH" ] && bar_width="$GAUGE_MAX_BAR_WIDTH"

  # 3段目: 決定したバー幅でゲージを再構築
  local parts=()
  for ((i = 0; i < count; i++)); do
    parts+=("$(build_gauge_segment "${labels[$i]}" "${pcts[$i]}" "${resets[$i]}" "$bar_width")")
  done

  fit_segments "$separator" "$term_width" "${parts[@]}"
}
