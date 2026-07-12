#!/bin/bash
# statusline 3行目: 週次 (7d) レートリミット + モデル別週次枠 (例: 7d(Fable))
#
# 実体 (render_gauge_line 等) は gauges.sh に一元化されている。本ファイルは
# 7d ゲージとモデル別週次枠 (scoped) の各ゲージを組み立てて render_gauge_line に
# 渡す薄い assembler。scoped データの取得元 (stdin 優先・cache フォールバック) は
# main.sh 側の配線契約であり、本ファイルは受け取った TSV をそのまま描画するのみ。

# 3行目を描画する。
# 引数: $1=7d 使用率%, $2=7d リセット時刻,
#       $3=scoped_tsv (1行1entry の "<display_name>\t<percent>\t<resets_at>"。空可)
# ctx・先頭固定セグメントは無し。ゲージが 0 件なら空文字列を返す
# (main.sh がそれを見て行自体を省略する)。
render_line3() {
  local rate_7d="$1" reset_7d="$2" scoped_tsv="$3"

  GAUGE_LABELS=()
  GAUGE_PCTS=()
  GAUGE_RESETS=()

  if [ -n "$rate_7d" ]; then
    GAUGE_LABELS+=("7d")
    GAUGE_PCTS+=("$rate_7d")
    GAUGE_RESETS+=("$reset_7d")
  fi

  if [ -n "$scoped_tsv" ]; then
    local display_name percent resets_at clean_name
    while IFS=$'\t' read -r display_name percent resets_at; do
      # 非表示文字 (制御文字・DEL) を除去する。ANSI エスケープ等が display_name に
      # 混入すると可視幅計算が壊れ表示が汚染されるため。
      clean_name=$(printf '%s' "$display_name" | tr -d '\000-\037\177')
      [ -z "$clean_name" ] && continue
      GAUGE_LABELS+=("7d($clean_name)")
      GAUGE_PCTS+=("$percent")
      GAUGE_RESETS+=("$resets_at")
    done <<< "$scoped_tsv"
  fi

  render_gauge_line "" "" "" ""
}
