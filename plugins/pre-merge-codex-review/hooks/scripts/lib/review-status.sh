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
#        regression identified」)。 ただし同じ行に否定・不確実・保留を表す語
#        (`cannot` / `could not` / `not` / `n't` / `unable` / `unclear` / `uncertain` /
#        `unsure` / `whether` / `if` / `unverified` / `unconfirmed`、 法助動詞 `may` /
#        `might` / `could` / `should` / `would`、 推量 `possibly` / `likely` / `probably` /
#        `presumably` / `seems` / `appears`、 検証保留 `pending` / `needs` / `needed` /
#        `requires` / `required` / `confirm` / `verify` / `verification` / `expect` /
#        `expected` / `assume` / `assumed` / `assuming`) が単語として含まれる場合は、
#        結論が肯定形でない (例: 「we cannot establish that there are no regressions」
#        「there may be no regressions」「further testing is needed to confirm no
#        regressions」) ため一致とみなさない
# 5. 空 report・末尾 10 行が空白のみ・上記に一致しない場合は findings
#
# ## 移植性の制約
#
# POSIX の `tail` / `grep -E` / `sed -E` / `tr` の範囲で書く (`grep -P` は使わない)。
# macOS (BSD grep/sed) と Linux (GNU grep/sed) の両方で動作すること。 `\b` `\<` `\>` の
# ような GNU 拡張の単語境界は使わない。

# 引数: <report-file>
# 出力 (stdout): `pass` または `findings` (改行なし)
# exit code: 常に 0
detect_review_status() {
  local report_file="$1"

  local tail_lines
  if ! tail_lines=$(tail -n 10 -- "$report_file" 2>/dev/null); then
    printf '%s' 'findings'
    return 0
  fi

  # 以下の grep / sed / tr は LC_ALL=C (byte 指向) で実行する。 report は非 ASCII を含みうる
  # ため、 UTF-8 locale のまま BSD sed / tr に通すと不正な multibyte 列で途中終了し、 結論行が
  # 照合されないまま findings に倒れる。 照合するパターンはすべて ASCII なので C locale で
  # 十分に判定できる。
  # finding 記述 (## Finding / - Severity: / 前後が英数字でない P0〜P3) が末尾 10 行に
  # 1 行でもあれば、他の条件に関わらず findings 確定。
  if printf '%s\n' "$tail_lines" \
    | LC_ALL=C grep -qE '^[[:space:]]*#+[[:space:]]+Finding|^[[:space:]]*[-*][[:space:]]+Severity:|(^|[^A-Za-z0-9])P[0-3]([^A-Za-z0-9]|$)'
  then
    printf '%s' 'findings'
    return 0
  fi

  # 各行を正規化する: 行頭の markdown 装飾除去 → 強調記号 (*/_) 除去 → 行末の句読点・
  # 空白除去 → 小文字化。
  local normalized
  normalized=$(printf '%s\n' "$tail_lines" \
    | LC_ALL=C sed -E 's/^[[:space:]]*(([-*>]|[0-9]+[.)])[[:space:]]*)*//' \
    | LC_ALL=C sed -E 's/[*_]//g' \
    | LC_ALL=C sed -E 's/[[:space:].!]+$//' \
    | LC_ALL=C tr '[:upper:]' '[:lower:]')

  # 正規化後の行のいずれかが 「no findings」 等の表現に行全体一致するか、行末が同表現に
  # 一致すれば (直前が行頭または非英字) pass。 ただし否定・不確実を表す語を含む行
  # (「cannot establish that there are no regressions」「there may be no regressions」等) は
  # 肯定の結論ではないため除外する
  # (1 段目の grep で候補行を抽出し、 2 段目の grep -v で否定語を含む行を落とす。 残る行が
  # 1 つでもあれば pass)。
  if printf '%s\n' "$normalized" \
    | LC_ALL=C grep -E '(^|[^a-z])no (material |actionable |significant )?(issue|issues|regression|regressions|findings)( found| identified)?$' \
    | LC_ALL=C grep -qvE "(^|[^a-z])(cannot|could not|not|unable|unclear|uncertain|unsure|whether|if|unverified|unconfirmed|may|might|could|should|would|possibly|likely|probably|presumably|seems?|appears?|pending|needs?|needed|requires?|required|confirm|verify|verification|expect|expected|assum(e|ed|ing))([^a-z]|$)|n't([^a-z]|$)"
  then
    printf '%s' 'pass'
    return 0
  fi

  printf '%s' 'findings'
  return 0
}
