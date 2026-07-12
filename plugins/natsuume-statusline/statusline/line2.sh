#!/bin/bash
# statusline 2行目: モデル名 + context 使用量 + レートリミット (5h)
#
# 実体 (build_context_segment / build_ratelimit_segment / render_gauge_line) は
# gauges.sh に一元化されている。本ファイルはモデル名を先頭固定セグメントとして、
# 5h ゲージを render_gauge_line に渡す薄い assembler。

# 2行目を描画する。
# 引数: $1=モデル名 (空なら先頭セグメント無し), $2=ctx_pct, $3=ctx_used, $4=ctx_max,
#       $5=5h 使用率%, $6=5h リセット時刻
# モデル名は色付けせず素のテキストで先頭に置く。横幅への段階的縮小・バー等の
# 詳細は gauges.sh の render_gauge_line を参照。
render_line2() {
  local model_name="$1" ctx_pct="$2" ctx_used="$3" ctx_max="$4"
  local rate_5h="$5" reset_5h="$6"

  # 非表示文字 (制御文字・DEL) を除去する (line3.sh の scoped display_name と同じ理由:
  # ANSI エスケープ等の混入による可視幅計算の破壊・表示汚染の防止)。
  model_name=$(printf '%s' "$model_name" | tr -d '\000-\037\177')

  # レートリミット（バー付き）を render_gauge_line に渡す。使用率が空のもの
  # (Claude Code がまだ値を渡していない、非サブスク等) は含めない。
  GAUGE_LABELS=()
  GAUGE_PCTS=()
  GAUGE_RESETS=()
  if [ -n "$rate_5h" ]; then
    GAUGE_LABELS+=("5h")
    GAUGE_PCTS+=("$rate_5h")
    GAUGE_RESETS+=("$reset_5h")
  fi

  render_gauge_line "$model_name" "$ctx_pct" "$ctx_used" "$ctx_max"
}
