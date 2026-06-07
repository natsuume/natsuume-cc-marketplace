#!/bin/bash
# markers.sh
# pre-push-review プラグインのレビューマーカーファイル名を単一ソース化する。
#
# block-pre-push.sh が読み (3 マーカーのハッシュ検証)、 auto-mark.sh / run-codex-review.sh
# が書き込む (各 review 完了時のハッシュ書き込み)。 両者でファイル名が 1 文字でも乖離すると
# マーカーは永遠に一致せず push が通らなくなる致命バグになるため、 ここに集約する。
#
# ## v2.0.0: 3 マーカー構成
#
# - CODE_REVIEWED_MARKER ← /code-review (Anthropic read-only バグ検出)
# - CODEX_MARKER         ← codex review (OpenAI バグ検出 / wrapper script 経由)
# - SECURITY_MARKER      ← security-reviewer subagent (self-contained security review)
#
# /simplify (cleanup-only Anthropic skill) のマーカーは v1.x で扱っていたが、 v2.0.0 で
# 削除した。 v2.0.0 は 3 レビューを `/pre-push-review:review` slash command 経由で並列起動
# する確定的フローに切替えたため、 cleanup ステップを廃止して bug 検出 + codex + security の
# 3 軸 defense-in-depth に純化している。

CODE_REVIEWED_MARKER_NAME=".claude-pre-push-code-reviewed"
CODEX_MARKER_NAME=".claude-pre-push-codex-reviewed"
SECURITY_MARKER_NAME=".claude-pre-push-security-reviewed"

# 引数: <git-dir>
# 出力: code-reviewed マーカー (/code-review = read-only バグ検出) の絶対パス
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
