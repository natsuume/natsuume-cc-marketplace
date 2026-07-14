#!/bin/bash
# statusline 2行目: モデル名 (effort) + context 使用量 + レートリミット (5h)
#
# 実体 (build_context_segment / build_ratelimit_segment / render_gauge_line) は
# gauges.sh に一元化されている。本ファイルはモデル名 + effort を先頭固定セグメント
# として、5h ゲージを render_gauge_line に渡す薄い assembler。

# 2行目を描画する。
# 引数: $1=モデル名 (空なら先頭セグメント無し), $2=effort level (空なら非表示),
#       $3=ctx_pct, $4=ctx_used, $5=ctx_max, $6=5h 使用率%, $7=5h リセット時刻
# モデル名は色付けせず素のテキストで先頭に置き、effort level があれば
# "Fable 5 (high)" 形式で併記する。effort は stdin の .effort.level 由来で、
# モデルが reasoning effort 非対応・古い Claude Code ではキーごと欠落する
# (その場合はモデル名のみ)。横幅への段階的縮小・バー等の詳細は gauges.sh の
# render_gauge_line を参照。
render_line2() {
  local model_name="$1" effort_level="$2" ctx_pct="$3" ctx_used="$4" ctx_max="$5"
  local rate_5h="$6" reset_5h="$7"

  # 非表示文字 (制御文字・DEL) を除去する (line3.sh の scoped display_name と同じ理由:
  # ANSI エスケープ等の混入による可視幅計算の破壊・表示汚染の防止)。
  model_name=$(printf '%s' "$model_name" | tr -d '\000-\037\177')
  effort_level=$(printf '%s' "$effort_level" | tr -d '\000-\037\177')

  # effort はモデル名の修飾なので、モデル名が空のとき単独では表示しない
  # ("(high)" だけが先頭に浮くのを防ぐ)。
  local leading="$model_name"
  if [ -n "$leading" ] && [ -n "$effort_level" ]; then
    leading="$leading ($effort_level)"
  fi

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

  render_gauge_line "$leading" "$ctx_pct" "$ctx_used" "$ctx_max"
}
