#!/bin/bash
# gauges.sh — ゲージ行 (context 使用量 / レートリミット) の共通レンダラ
#
# 2 行目と 3 行目は「先頭固定セグメント + context + レートリミットゲージ列」という
# 同型のレイアウトを持つため、組み立てと横幅への段階的縮小をここに一元化する。
# line2.sh / line3.sh は本ファイルの関数を呼ぶ薄い assembler になる。
#
# ■ 提供する関数
#
# build_context_segment
#   引数: $1=使用率%, $2=使用トークン数, $3=最大コンテキスト長,
#         $4=使用率%を表示するか(1/0), $5=使用率を四捨五入して整数表示するか(1/0)
#   出力: "ctx: (45%) 75.1k/1M" 形式のセグメント (詳細は関数直前のコメント参照)
#
# build_ratelimit_segment
#   引数: $1=ラベル, $2=使用率%, $3=リセット時刻(epoch秒 or ISO8601),
#         $4=バー幅(0=バー無し), $5=整数表示するか(1/0)
#   出力: "5h: 62% (58m) [████░░]" 形式のセグメント
#   備考: ラベルは呼び出し元が決める。2 行目は "5h"、3 行目は "7d" と
#         "7d(<display_name>)" (例: "7d(Fable)") を渡す。
#
# render_gauge_line
#   先頭固定セグメント + context + レートリミットゲージ列の組み立てと、横幅に
#   応じた段階的縮小を担う本体。
#   入力 (bash の配列は位置引数で渡せないため、呼び出し元が globals を設定する):
#     GAUGE_LABELS[] / GAUGE_PCTS[] / GAUGE_RESETS[]
#       — レートリミットゲージの (ラベル, 使用率%, リセット時刻) の平行配列。
#         使用率が空の要素は呼び出し元が事前に除外しておくこと。呼び出し前に
#         必ず (空でも) 配列を再初期化すること (前回呼び出しの値が残留しないよう)。
#   引数:
#     $1=先頭固定セグメント (例: モデル名 "Fable 5"。空なら無し)
#     $2=ctx_pct, $3=ctx_used, $4=ctx_max (context 無しの行は 3 つとも空を渡す)
#   出力: 1 行分の組み立て済み文字列 (先頭固定セグメント・ctx・ゲージがすべて
#         無ければ空。呼び出し元はその場合行自体を出力しない)
#
# ■ 段階的縮小ラダー (先頭固定セグメントのみ縮小対象外)
#   段階0: 使用率の小数を四捨五入して整数化
#   段階1: ctx の "(P%)" を削除 (used/max が残る。ctx 無しの行ではスキップ)
#   段階2: ゲージのバー長を短縮 (GAUGE_MAX_BAR_WIDTH → GAUGE_MIN_BAR_WIDTH)
#   段階3: バーを削除
#   先頭固定セグメントは縮小対象にせず、幅計算にはその可視幅 + separator 幅を含める
#   (最終手段の fit_segments の … 切り詰めのみ縮小されうる)。
#
# ■ 依存
#   lib.sh (rate_color / progress_bar / time_remaining / format_pct /
#   int_pct_from_display / humanize_tokens / visible_length / fit_segments) を
#   source 済みの環境で呼ばれる。main.sh が lib.sh → gauges.sh の順で source する。

# レートリミットのバー幅の上限/下限。横幅に応じて [MIN, MAX] で可変。
# MIN 未満になる場合はバーを描かず、さらに横幅を節約する（段階的縮小の最終段）。
GAUGE_MAX_BAR_WIDTH=10
GAUGE_MIN_BAR_WIDTH=3
# バー周辺の装飾 " [" + "]" の文字数。build_ratelimit_segment の出力形式と対応。
GAUGE_BAR_DECORATION_WIDTH=3

# context 使用量セグメントを組み立てる（バー無し・reset 無しの数値表示）
# 引数: $1=使用率%, $2=使用トークン数, $3=最大コンテキスト長,
#       $4=使用率%を表示するか(1/0), $5=使用率を四捨五入して整数表示するか(1/0)
# 出力フォーマット:
#   show_pct=1                 : "ctx: (45%) 75.1k/1M"
#   show_pct=0 かつ used/max 有 : "ctx: 75.1k/1M"（横幅節約のため % を省略）
#   used/max 無                : "ctx: (45%)"（% が唯一の情報なので show_pct に関わらず表示）
build_context_segment() {
  local pct="$1" used="$2" max="$3" show_pct="$4" round="${5:-0}"
  local display_pct int_pct color tokens="" segment

  # 表示用は format_pct（round=1 で四捨五入の整数表示）、色判定用は floor の整数
  display_pct=$(format_pct "$pct" "$round")
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

# 単一レートリミットゲージを組み立てる（5h / 7d / 7d(モデル名) 共通、バー付き）
# 引数:
#   $1=ラベル, $2=使用率%, $3=リセット時刻(epoch秒 or ISO8601),
#   $4=バー幅（0 ならバーを描画しない: 残り幅計算用 / バー削除時の本番用）,
#   $5=使用率を四捨五入して整数表示するか(1/0)
# 出力フォーマット: "5h: 62% (58m) [████░░]"（bar_width=0 なら "5h: 62% (58m)"）
build_ratelimit_segment() {
  # bar_width は呼び出し元 (render_gauge_line) が常に明示で渡す
  # （0=非バー / 算出済みバー幅）。省略経路は無いためデフォルトは持たない。
  local label="$1" pct="$2" resets_at="$3" bar_width="$4" round="${5:-0}"
  local color bar remaining segment display_pct int_pct

  # 表示用は format_pct（round=1 で四捨五入の整数表示）、判定用は floor の整数
  # （bash の算術評価は小数を扱えないため、progress_bar / rate_color には整数を渡す）
  display_pct=$(format_pct "$pct" "$round")
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

# ゲージ行 (先頭固定セグメント + context + レートリミット列) を描画する。
# 横幅に収まる最も豊かな表示を選び、収まらなければ段階的に縮小する。
# 縮小の優先順位（先に削るもの順）:
#   段階0: 使用率の小数表示を四捨五入して整数化（"(45.2%)"→"(45%)", "62.5%"→"63%"）
#   段階1: ctx の使用率表示 "(P%)" を削除（used/max が残るので情報は保たれる）
#   段階2: レートリミットのバー長を短縮（GAUGE_MAX→GAUGE_MIN）
#   段階3: バーを削除（"label: P% (reset)" のみ）
# それでも収まらない極端な狭幅は最後に fit_segments が … で切り詰める。
# 先頭固定セグメントはどの段階でも縮小対象にしない。
render_gauge_line() {
  local leading="$1" ctx_pct="$2" ctx_used="$3" ctx_max="$4"
  local term_width="${TERM_WIDTH:-80}"
  local separator=" | "
  local sep_w=${#separator}

  # レートリミット（バー付き）は呼び出し元が GAUGE_LABELS[] / GAUGE_PCTS[] / GAUGE_RESETS[]
  # に設定済みの前提（使用率が空の要素は呼び出し元が事前に除外している）。
  local labels=("${GAUGE_LABELS[@]}") pcts=("${GAUGE_PCTS[@]}") resets=("${GAUGE_RESETS[@]}")
  local rate_count=${#labels[@]}

  local has_ctx=0
  [ -n "$ctx_pct" ] && has_ctx=1

  local leading_w=0
  [ -n "$leading" ] && leading_w=$(visible_length "$leading")

  # 表示レベルを richest→poorest の順に試し、バー最大幅で横幅に収まる最初を採用する。
  # 各レベル = (round, show_pct) の組:
  #   "0 1": 小数% + ctx% 表示（最も豊か）
  #   "1 1": 整数%（段階0）+ ctx% 表示
  #   "1 0": 整数% + ctx% 非表示（段階1）
  # ※ 段階0（整数化）を段階1（ctx% 削除）より先に試すため、この順序で並べる。
  # ※ 確定後にバー幅で段階2/3（短縮・削除）を吸収する。
  # ※ どのレベルも収まらない場合は最後の "1 0" を採用し、バー縮小/fit_segments で詰める。
  # ループ脱出後に使うのは round と組み立て済みの ctx_seg/rate_core_total/sep_total。
  # show_pct は ctx_seg に焼き込まれるのでループ内 (lvl_show) でのみ使う。
  local round="" ctx_seg="" rate_core_total=0 sep_total=0
  local level lvl_round lvl_show cseg rct i nonbar total_segments full_bars total_w
  for level in "0 1" "1 1" "1 0"; do
    lvl_round=${level%% *}
    lvl_show=${level##* }
    # show_pct=0（ctx% 非表示）は ctx があるときのみ意味を持つ。
    [ "$lvl_show" -eq 0 ] && [ "$has_ctx" -eq 0 ] && continue

    cseg=""
    [ "$has_ctx" -eq 1 ] && cseg=$(build_context_segment "$ctx_pct" "$ctx_used" "$ctx_max" "$lvl_show" "$lvl_round")

    # この round でのレートリミット非バー部（"label: P% (reset)"）の可視幅合計。
    rct=0
    for ((i = 0; i < rate_count; i++)); do
      nonbar=$(build_ratelimit_segment "${labels[$i]}" "${pcts[$i]}" "${resets[$i]}" 0 "$lvl_round")
      rct=$((rct + $(visible_length "$nonbar")))
    done

    # 描画対象（先頭固定 + context + レートリミット）の総数。1 つも無ければ何も出さない。
    total_segments=$rate_count
    [ -n "$cseg" ] && total_segments=$((total_segments + 1))
    [ -n "$leading" ] && total_segments=$((total_segments + 1))
    [ "$total_segments" -eq 0 ] && return

    full_bars=$(( rate_count * (GAUGE_MAX_BAR_WIDTH + GAUGE_BAR_DECORATION_WIDTH) ))
    total_w=$(( leading_w + $(visible_length "$cseg") + rct + (total_segments - 1) * sep_w + full_bars ))

    round="$lvl_round"
    ctx_seg="$cseg"; rate_core_total="$rct"
    sep_total=$(( (total_segments - 1) * sep_w ))
    # バー最大幅で収まればこのレベルを確定。最後の "1 0" は収まらなくても採用。
    [ "$total_w" -le "$term_width" ] && break
  done

  # 段階2/3: 残り幅をレートリミットのバーへ均等分配する。
  #   GAUGE_MAX_BAR_WIDTH で頭打ち（広いと最大長）、分配結果が縮むと段階2。
  #   GAUGE_MIN_BAR_WIDTH 未満ならバーを描かない（段階3）。
  # 先頭固定セグメント・context にバーは無い。レートリミット各バーに装飾幅を確保する。
  local ctx_w=0
  [ -n "$ctx_seg" ] && ctx_w=$(visible_length "$ctx_seg")
  local bar_width=0
  if [ "$rate_count" -gt 0 ]; then
    local available=$(( term_width - leading_w - ctx_w - rate_core_total - sep_total - rate_count * GAUGE_BAR_DECORATION_WIDTH ))
    bar_width=$(( available / rate_count ))
    [ "$bar_width" -gt "$GAUGE_MAX_BAR_WIDTH" ] && bar_width="$GAUGE_MAX_BAR_WIDTH"
    [ "$bar_width" -lt "$GAUGE_MIN_BAR_WIDTH" ] && bar_width=0
  fi

  # 最終セグメントを組み立てる（先頭固定 → context → レートリミットの順）。round を反映する。
  local parts=()
  [ -n "$leading" ] && parts+=("$leading")
  [ -n "$ctx_seg" ] && parts+=("$ctx_seg")
  for ((i = 0; i < rate_count; i++)); do
    parts+=("$(build_ratelimit_segment "${labels[$i]}" "${pcts[$i]}" "${resets[$i]}" "$bar_width" "$round")")
  done

  fit_segments "$separator" "$term_width" "${parts[@]}"
}
