#!/bin/bash
# loop-counter.sh
# pre-commit-review プラグインで使う「`/codex:review --wait` 連続実行回数」カウンタの
# ファイル名・パス組み立て・読み書きロジックを共通化する。
#
# block-pre-commit.sh が閾値判定で読み (read / reset)、auto-mark.sh が +1 して書き込む (read / write)。
# 両者でファイル名やサニタイズ条件がずれると loop discipline (= 閾値判定) が壊れるため、
# `diff-hash.sh` を共有 helper にしたのと同じ動機で、ここに単一ソース化している。

# `<git-dir>` 配下に置くカウンタファイルの basename (内部参照用)。
# caller は直接参照せず、下記の loop_counter_path / read_loop_count / write_loop_count /
# reset_loop_count 関数を経由する。
LOOP_COUNTER_NAME=".claude-pre-commit-codex-loop-count"

# 引数: <git-dir>
# 出力: カウンタファイルの絶対パス
loop_counter_path() {
  printf '%s/%s' "$1" "$LOOP_COUNTER_NAME"
}

# 引数: <git-dir>
# 出力: 整数 (ファイル無し / 不正な内容なら 0)
read_loop_count() {
  local file
  file=$(loop_counter_path "$1")
  [ -f "$file" ] || { printf '0'; return; }
  local raw
  raw=$(<"$file")
  case "$raw" in
    ''|*[!0-9]*) printf '0' ;;
    *)           printf '%s' "$raw" ;;
  esac
}

# 引数: <git-dir> <整数>
write_loop_count() {
  printf '%s' "$2" > "$1/$LOOP_COUNTER_NAME"
}

# 引数: <git-dir>
reset_loop_count() {
  rm -f "$1/$LOOP_COUNTER_NAME"
}
