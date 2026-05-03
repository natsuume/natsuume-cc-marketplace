#!/bin/bash
# markers.sh
# pre-push-review プラグインのレビューマーカーファイル名を単一ソース化する。
#
# block-pre-push.sh が読み (両マーカーのハッシュ検証)、auto-mark.sh が書き込む
# (各 review 完了時のハッシュ書き込み)。両者でファイル名が 1 文字でも乖離すると
# マーカーが永遠に一致せず push が通らなくなる。

# `<git-dir>` 配下に置くマーカーファイルの basename。
SIMPLIFIED_MARKER_NAME=".claude-pre-push-simplified"
CODEX_MARKER_NAME=".claude-pre-push-codex-reviewed"

# 引数: <git-dir>
# 出力: simplified マーカーの絶対パス
simplified_marker_path() {
  printf '%s/%s' "$1" "$SIMPLIFIED_MARKER_NAME"
}

# 引数: <git-dir>
# 出力: codex-reviewed マーカーの絶対パス
codex_marker_path() {
  printf '%s/%s' "$1" "$CODEX_MARKER_NAME"
}
