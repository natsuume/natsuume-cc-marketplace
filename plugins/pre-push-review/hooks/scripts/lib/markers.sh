#!/bin/bash
# markers.sh
# pre-push-review プラグインのレビューマーカーファイル名を単一ソース化する。
#
# block-pre-push.sh が読み (両マーカーのハッシュ検証)、auto-mark.sh が書き込む
# (各 review 完了時のハッシュ書き込み)。両者でファイル名が 1 文字でも乖離すると
# マーカーが永遠に一致せず push が通らなくなる。

# `<git-dir>` 配下に置くマーカーファイルの basename。
# v0.7.0: Claude Code v2.1.146 で bundled skill `/simplify` が `/code-review` に
# リネームされたのに合わせて marker ファイル名も `.claude-pre-push-code-reviewed`
# に統一した。検出ロジック (auto-mark.sh) は `simplify` / `code-review` の両方を
# 受け付けるため、 v2.1.145 以下のユーザーが古い skill 名で呼んでも同じマーカーに
# 書き込まれ、 push が gate される。
CODE_REVIEWED_MARKER_NAME=".claude-pre-push-code-reviewed"
CODEX_MARKER_NAME=".claude-pre-push-codex-reviewed"
SECURITY_MARKER_NAME=".claude-pre-push-security-reviewed"

# 引数: <git-dir>
# 出力: code-reviewed マーカーの絶対パス
code_reviewed_marker_path() {
  printf '%s/%s' "$1" "$CODE_REVIEWED_MARKER_NAME"
}

# 引数: <git-dir>
# 出力: codex-reviewed マーカーの絶対パス
codex_marker_path() {
  printf '%s/%s' "$1" "$CODEX_MARKER_NAME"
}

# 引数: <git-dir>
# 出力: security-reviewed マーカーの絶対パス
security_marker_path() {
  printf '%s/%s' "$1" "$SECURITY_MARKER_NAME"
}
