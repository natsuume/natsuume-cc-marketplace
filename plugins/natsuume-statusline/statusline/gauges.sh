#!/bin/bash
# gauges.sh — ゲージ行 (context 使用量 / レートリミット) の共通レンダラ
#
# 2 行目と 3 行目は「先頭固定セグメント + context + レートリミットゲージ列」という
# 同型のレイアウトを持つため、組み立てと横幅への段階的縮小をここに一元化する。
# line2.sh / line3.sh は本ファイルの関数を呼ぶ薄い assembler になる。
#
# ============================================================================
# Phase A 設計契約 (Phase B で line2.sh から実体を移設・一般化する)
# ============================================================================
#
# ■ 提供する関数
#
# build_context_segment
#   line2.sh の同名関数を無変更で移設する。
#   引数: $1=使用率%, $2=使用トークン数, $3=最大コンテキスト長,
#         $4=使用率%を表示するか(1/0), $5=使用率を四捨五入して整数表示するか(1/0)
#   出力: "ctx: (45%) 75.1k/1M" 形式のセグメント (詳細は関数 doc コメント参照)
#
# build_ratelimit_segment
#   line2.sh の同名関数を無変更で移設する。
#   引数: $1=ラベル, $2=使用率%, $3=リセット時刻(epoch秒 or ISO8601),
#         $4=バー幅(0=バー無し), $5=整数表示するか(1/0)
#   出力: "5h: 62% (58m) [████░░]" 形式のセグメント
#   備考: ラベルは呼び出し元が決める。2 行目は "5h"、3 行目は "7d" と
#         "7d(<display_name>)" (例: "7d(Fable)") を渡す。
#
# render_gauge_line
#   line2.sh の render_line2 の組み立て・段階的縮小ロジックを一般化した本体。
#   入力 (bash の配列は位置引数で渡せないため、呼び出し元が globals を設定する):
#     GAUGE_LABELS[] / GAUGE_PCTS[] / GAUGE_RESETS[]
#       — レートリミットゲージの (ラベル, 使用率%, リセット時刻) の平行配列。
#         使用率が空の要素は呼び出し元が事前に除外しておく。
#   引数:
#     $1=先頭固定セグメント (例: モデル名 "Fable 5"。空なら無し)
#     $2=ctx_pct, $3=ctx_used, $4=ctx_max (context 無しの行は 3 つとも空を渡す)
#   出力: 1 行分の組み立て済み文字列 (空なら呼び出し元は行自体を出力しない)
#
# ■ 段階的縮小ラダー (現行 render_line2 と同一。先頭固定セグメントのみ追加)
#   段階0: 使用率の小数を四捨五入して整数化
#   段階1: ctx の "(P%)" を削除 (used/max が残る。ctx 無しの行ではスキップ)
#   段階2: ゲージのバー長を短縮 (GAUGE_MAX_BAR_WIDTH → GAUGE_MIN_BAR_WIDTH)
#   段階3: バーを削除
#   先頭固定セグメントは縮小対象にしない (最終手段の fit_segments の … 切り詰めのみ)。
#   幅計算には先頭固定セグメント + separator を含める。
#
# ■ 依存
#   lib.sh (rate_color / progress_bar / time_remaining / format_pct /
#   int_pct_from_display / humanize_tokens / visible_length / fit_segments) を
#   source 済みの環境で呼ばれる。main.sh が lib.sh → gauges.sh の順で source する。

# 実装は Phase B で line2.sh から移設する。
