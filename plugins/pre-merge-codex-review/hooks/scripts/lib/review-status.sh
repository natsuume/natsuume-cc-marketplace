#!/bin/bash
# review-status.sh
# codex review report のテキストから 「指摘なし」 か否かを判定する共通ユーティリティ。
#
# ## 何をするか
#
# `detect_review_status <report-file>` は codex review report (テキストファイル) を
# 読み、 report の末尾に 「指摘なし」 の結論があるかどうかを判定して pass / findings を
# stdout に返す。 caller (run-pre-merge-codex-review.sh) はこの結果を投稿する header の
# `status=pass|findings` に使う。 status は merge gate の判定には影響しない
# (pass / findings のどちらでも 「レビュー済み」 として成立する) ため、 誤判定は記録の
# 精度の問題に留まる。 とはいえ判定できない場合は常に findings に倒す (「findings 0 件」
# を誤って主張しないため)。
#
# ## I/O 契約
#
# - 関数名: detect_review_status
# - 引数: <report-file> (codex review report のテキストファイルの path) を 1 つ
# - 出力: stdout に `pass` または `findings` を改行なしで出力し (`printf '%s'`)、
#   return 0 する。 ファイルが存在しない・読めない場合も `findings` を出力して return 0
#   する (判定できない場合は findings に倒す)
# - `set -e` の有無に依存せず動く (関数内で非ゼロを返しうるコマンドは if / || で受ける)
#
# ## 判定仕様
#
# 1. 判定対象は report の末尾 10 行
# 2. 末尾 10 行の中に次のいずれかの finding 記述が 1 行でも含まれる場合は、他の条件に
#    関わらず findings:
#    - `## Finding` (行頭の `#` 1 個以上 + 空白 + `Finding`)
#    - `- Severity:` (行頭の `-` または `*` + 空白 + `Severity:`)
#    - `P0`〜`P3` の優先度表記 (前後が英数字でない `P[0-3]`。 例: `**P1**`、 `(P2)`、
#      `P3:`。 `P2P` のように英数字が続く場合は該当しない)
# 3. finding 記述が無い場合、 各行を次の順で正規化する:
#    - 行頭の markdown 装飾を除去: 空白、 `-`/`*`/`>` の箇条書き・引用記号、 `1.`/`1)` の
#      番号付きリスト記号 (複数重なっていてもすべて除去)
#    - 強調記号 `*` と `_` をすべて除去
#    - 行末の `.` `!` と空白を除去
#    - 大文字小文字を区別しない (小文字化)
# 4. 正規化後の行について、 次のいずれかを満たす行が 1 つでもあれば pass:
#    (a) 行全体が次のいずれかに一致:
#        `no findings` / `no material findings` / `no actionable findings` /
#        `no issues found` / `no issues identified` / `no regression found` /
#        `no regressions found` / `no regression identified` /
#        `no regressions identified` /
#        `no (material|actionable|significant) (issue|issues|regression|regressions|
#        findings)` の後に任意で ` found` / ` identified` を続けた形
#    (b) 行の末尾が
#        `no (material |actionable |significant )?(issue|issues|regression|regressions|
#        findings)( found| identified)?`
#        に一致する (直前は行頭または英字以外。 例: 「..., with no actionable
#        regression identified」)
# 5. 空 report・末尾 10 行が空白のみ・上記に一致しない場合は findings
#
# ## 移植性の制約
#
# POSIX の `tail` / `grep -E` / `sed -E` / `tr` の範囲で書く (`grep -P` は使わない)。
# macOS (BSD grep/sed) と Linux (GNU grep/sed) の両方で動作すること。 `\b` `\<` `\>` の
# ような GNU 拡張の単語境界は使わない。
#
# ## 現在の実装状態
#
# 上記の判定仕様は未実装。 本関数は常に `findings` を返すスタブ。

# 引数: <report-file>
# 出力 (stdout): `pass` または `findings` (改行なし)
# exit code: 常に 0
detect_review_status() {
  local report_file="$1"
  : "$report_file"
  printf '%s' 'findings'
  return 0
}
