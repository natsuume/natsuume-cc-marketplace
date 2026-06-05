#!/bin/bash
# statusline 2行目: context 使用量 + レートリミット (5h/7d)

# レートリミットのバー最大幅。ターミナルが広くてもこれ以上は伸ばさず、グラフを短く保つ。
GAUGE_MAX_BAR_WIDTH=10

# context 使用量セグメントを組み立てる（バー無し・reset 無しの数値表示）
# 引数: $1=使用率%, $2=使用トークン数, $3=最大コンテキスト長
# 出力フォーマット: "ctx: (45%) 75.1k/1M"（used/max が揃わなければ "ctx: (45%)"）
build_context_segment() {
  local pct="$1" used="$2" max="$3"
  local display_pct int_pct color segment

  # 表示用は format_pct（整数なら小数点なし）、色判定用は floor の整数
  display_pct=$(format_pct "$pct")
  [ -z "$display_pct" ] && return
  int_pct=$(int_pct_from_display "$display_pct")
  color=$(rate_color "$int_pct")

  # "ctx: (P%)" まで。パーセンテージを使用率で色付け（高使用率ほど赤）。
  segment=$(printf 'ctx: %s(%s%%)%b' "$color" "$display_pct" "$RESET")
  # used/max が揃っていれば "75.1k/1M" を付す（max が数値かつ正のときのみ）
  if [ -n "$used" ] && [ -n "$max" ] && [ "$max" -gt 0 ] 2>/dev/null; then
    segment+=$(printf ' %s/%s' "$(humanize_tokens "$used")" "$(humanize_tokens "$max")")
  fi

  printf '%s' "$segment"
}

# 単一レートリミットゲージを組み立てる（5h / 7d 共通、バー付き）
# 引数:
#   $1=ラベル(5h/7d), $2=使用率%, $3=リセット時刻(epoch秒 or ISO8601),
#   $4=バー幅（0 ならバーを描画しない: 残り幅計算用）
# 出力フォーマット: "5h: 62% (58m) [████░░]"
build_ratelimit_segment() {
  # bar_width は呼び出し元 (render_line2) が常に明示で渡す
  # （0=非バー計測 / 算出済みバー幅）。省略経路は無いためデフォルトは持たない。
  local label="$1" pct="$2" resets_at="$3" bar_width="$4"
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

# 2行目を描画する。
# - context 使用量: "ctx: (P%) used/max"（バー無しの数値表示）を最左に置く
# - レートリミット (5h/7d): "label: P% (reset) [bar]" をバー付きで続ける
# バー幅は context セグメント分を差し引いた残り幅をレートリミット間で均等分配し、
# 極端に短くなる場合は最小幅でクランプする。
render_line2() {
  local ctx_pct="$1" ctx_used="$2" ctx_max="$3"
  local rate_5h="$4" reset_5h="$5" rate_7d="$6" reset_7d="$7"
  local term_width="${TERM_WIDTH:-80}"
  local separator=" | "
  local sep_w=${#separator}

  # context セグメント（バー無し）を先に組み立てる。
  # ctx_pct が空のものは Claude Code がまだ値を渡していない
  # （セッション初期/compact 直後）ため非表示。
  local ctx_seg=""
  [ -n "$ctx_pct" ] && ctx_seg=$(build_context_segment "$ctx_pct" "$ctx_used" "$ctx_max")

  # レートリミット（バー付き）を (label, pct, reset) の3つ組として収集する。
  # pct が空のものは Claude Code がまだ値を渡していない（非サブスク等）ため除外する。
  local labels=() pcts=() resets=()
  if [ -n "$rate_5h" ]; then
    labels+=("5h"); pcts+=("$rate_5h"); resets+=("$reset_5h")
  fi
  if [ -n "$rate_7d" ]; then
    labels+=("7d"); pcts+=("$rate_7d"); resets+=("$reset_7d")
  fi
  local rate_count=${#labels[@]}

  # 描画対象（context + レートリミット）の総数。0 なら何も出さない。
  local total_segments=$rate_count
  [ -n "$ctx_seg" ] && total_segments=$((total_segments + 1))
  [ "$total_segments" -eq 0 ] && return

  # 1段目: 非バー部分の可視幅の総和を測る（context は全幅が非バー）
  local total_nonbar=0 i nonbar
  [ -n "$ctx_seg" ] && total_nonbar=$(visible_length "$ctx_seg")
  for ((i = 0; i < rate_count; i++)); do
    nonbar=$(build_ratelimit_segment "${labels[$i]}" "${pcts[$i]}" "${resets[$i]}" 0)
    total_nonbar=$((total_nonbar + $(visible_length "$nonbar")))
  done

  # 2段目: 残り幅をレートリミットのバーへ均等分配（最小4文字、最大 GAUGE_MAX_BAR_WIDTH）
  # バー周辺の装飾 = " [" + "]" の3文字 / バー。context にバーは無い。
  local bar_width=0
  if [ "$rate_count" -gt 0 ]; then
    local separators_total=$(( (total_segments - 1) * sep_w ))
    local bar_decorations=$(( rate_count * 3 ))
    local available=$(( term_width - total_nonbar - separators_total - bar_decorations ))
    bar_width=$(( available / rate_count ))
    [ "$bar_width" -lt 4 ] && bar_width=4
    [ "$bar_width" -gt "$GAUGE_MAX_BAR_WIDTH" ] && bar_width="$GAUGE_MAX_BAR_WIDTH"
  fi

  # 3段目: 最終セグメントを組み立てる（context を先頭、続いてレートリミット）
  local parts=()
  [ -n "$ctx_seg" ] && parts+=("$ctx_seg")
  for ((i = 0; i < rate_count; i++)); do
    parts+=("$(build_ratelimit_segment "${labels[$i]}" "${pcts[$i]}" "${resets[$i]}" "$bar_width")")
  done

  fit_segments "$separator" "$term_width" "${parts[@]}"
}
