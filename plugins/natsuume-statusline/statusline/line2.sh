#!/bin/bash
# statusline 2行目: context 使用量 + レートリミット (5h/7d)

# レートリミットのバー幅の上限/下限。横幅に応じて [MIN, MAX] で可変。
# MIN 未満になる場合はバーを描かず、さらに横幅を節約する（段階的縮小の最終段）。
GAUGE_MAX_BAR_WIDTH=10
GAUGE_MIN_BAR_WIDTH=3
# バー周辺の装飾 " [" + "]" の文字数。build_ratelimit_segment の出力形式と対応。
GAUGE_BAR_DECORATION_WIDTH=3

# context 使用量セグメントを組み立てる（バー無し・reset 無しの数値表示）
# 引数: $1=使用率%, $2=使用トークン数, $3=最大コンテキスト長, $4=使用率%を表示するか(1/0)
# 出力フォーマット:
#   show_pct=1                 : "ctx: (45%) 75.1k/1M"
#   show_pct=0 かつ used/max 有 : "ctx: 75.1k/1M"（横幅節約のため % を省略）
#   used/max 無                : "ctx: (45%)"（% が唯一の情報なので show_pct に関わらず表示）
build_context_segment() {
  local pct="$1" used="$2" max="$3" show_pct="$4"
  local display_pct int_pct color tokens="" segment

  # 表示用は format_pct（整数なら小数点なし）、色判定用は floor の整数
  display_pct=$(format_pct "$pct")
  [ -z "$display_pct" ] && return
  int_pct=$(int_pct_from_display "$display_pct")
  color=$(rate_color "$int_pct")

  # used が非負整数 かつ max が正整数のときのみトークン数を併記（先頭スペース込み）。
  # 非整数/空/負値は算術比較が失敗 → 2>/dev/null で握り潰し、生値を表示せず安全側に倒す。
  if [ "$used" -ge 0 ] 2>/dev/null && [ "$max" -gt 0 ] 2>/dev/null; then
    tokens=$(printf ' %s/%s' "$(humanize_tokens "$used")" "$(humanize_tokens "$max")")
  fi

  # % を表示する条件: show_pct=1、または used/max が無く % しか情報が無いとき。
  if [ "$show_pct" -eq 1 ] || [ -z "$tokens" ]; then
    segment=$(printf 'ctx: %s(%s%%)%b%s' "$color" "$display_pct" "$RESET" "$tokens")
  else
    segment=$(printf 'ctx:%s' "$tokens")
  fi

  printf '%s' "$segment"
}

# 単一レートリミットゲージを組み立てる（5h / 7d 共通、バー付き）
# 引数:
#   $1=ラベル(5h/7d), $2=使用率%, $3=リセット時刻(epoch秒 or ISO8601),
#   $4=バー幅（0 ならバーを描画しない: 残り幅計算用 / バー削除時の本番用）
# 出力フォーマット: "5h: 62% (58m) [████░░]"（bar_width=0 なら "5h: 62% (58m)"）
build_ratelimit_segment() {
  # bar_width は呼び出し元 (render_line2) が常に明示で渡す
  # （0=非バー / 算出済みバー幅）。省略経路は無いためデフォルトは持たない。
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
  # バー（bar_width=0 のときは省略 → 非バー幅の事前計測 / 段階的縮小でのバー削除に使う）
  if [ "$bar_width" -gt 0 ]; then
    bar=$(progress_bar "$int_pct" "$bar_width")
    segment+=$(printf ' %s%s%b' "$color" "$bar" "$RESET")
  fi

  printf '%s' "$segment"
}

# 2行目を描画する。横幅に収まる最も豊かな表示を選び、収まらなければ段階的に縮小する。
# 縮小の優先順位（先に削るもの順）:
#   段階1: ctx の使用率表示 "(P%)" を削除（used/max が残るので情報は保たれる）
#   段階2: レートリミットのバー長を短縮（GAUGE_MAX→GAUGE_MIN）
#   段階3: バーを削除（"label: P% (reset)" のみ）
# それでも収まらない極端な狭幅は最後に fit_segments が … で切り詰める。
render_line2() {
  local ctx_pct="$1" ctx_used="$2" ctx_max="$3"
  local rate_5h="$4" reset_5h="$5" rate_7d="$6" reset_7d="$7"
  local term_width="${TERM_WIDTH:-80}"
  local separator=" | "
  local sep_w=${#separator}

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

  # レートリミットの非バー部分（"label: P% (reset)"）の可視幅合計を測る。
  local rate_core_total=0 i nonbar
  for ((i = 0; i < rate_count; i++)); do
    nonbar=$(build_ratelimit_segment "${labels[$i]}" "${pcts[$i]}" "${resets[$i]}" 0)
    rate_core_total=$((rate_core_total + $(visible_length "$nonbar")))
  done

  # context セグメントを「% あり」（最大表示）でまず組み立てる。
  # ctx_pct が空 / format_pct が空を返す場合は context 非表示。
  local ctx_full=""
  [ -n "$ctx_pct" ] && ctx_full=$(build_context_segment "$ctx_pct" "$ctx_used" "$ctx_max" 1)

  # 描画対象（context + レートリミット）の総数。0 なら何も出さない。
  local total_segments=$rate_count
  [ -n "$ctx_full" ] && total_segments=$((total_segments + 1))
  [ "$total_segments" -eq 0 ] && return
  local sep_total=$(( (total_segments - 1) * sep_w ))

  # 段階1: 「ctx% あり + バー最大幅」が横幅に収まるか判定。収まらなければ ctx% を落とす。
  local ctx_seg="$ctx_full" ctx_w=0
  if [ -n "$ctx_full" ]; then
    local full_bars=$(( rate_count * (GAUGE_MAX_BAR_WIDTH + GAUGE_BAR_DECORATION_WIDTH) ))
    if [ $(( $(visible_length "$ctx_full") + rate_core_total + sep_total + full_bars )) -gt "$term_width" ]; then
      ctx_seg=$(build_context_segment "$ctx_pct" "$ctx_used" "$ctx_max" 0)
    fi
    ctx_w=$(visible_length "$ctx_seg")
  fi

  # 段階2/3: 残り幅をレートリミットのバーへ均等分配する。
  #   GAUGE_MAX_BAR_WIDTH で頭打ち（広いと最大長）、分配結果が縮むと段階2。
  #   GAUGE_MIN_BAR_WIDTH 未満ならバーを描かない（段階3）。
  # context にバーは無い。レートリミット各バーに装飾幅を確保する。
  local bar_width=0
  if [ "$rate_count" -gt 0 ]; then
    local available=$(( term_width - ctx_w - rate_core_total - sep_total - rate_count * GAUGE_BAR_DECORATION_WIDTH ))
    bar_width=$(( available / rate_count ))
    [ "$bar_width" -gt "$GAUGE_MAX_BAR_WIDTH" ] && bar_width="$GAUGE_MAX_BAR_WIDTH"
    [ "$bar_width" -lt "$GAUGE_MIN_BAR_WIDTH" ] && bar_width=0
  fi

  # 最終セグメントを組み立てる（context を先頭、続いてレートリミット）。
  local parts=()
  [ -n "$ctx_seg" ] && parts+=("$ctx_seg")
  for ((i = 0; i < rate_count; i++)); do
    parts+=("$(build_ratelimit_segment "${labels[$i]}" "${pcts[$i]}" "${resets[$i]}" "$bar_width")")
  done

  fit_segments "$separator" "$term_width" "${parts[@]}"
}
