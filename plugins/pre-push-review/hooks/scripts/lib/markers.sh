#!/bin/bash
# markers.sh
# pre-push-review プラグインのレビューマーカーファイル名を単一ソース化する。
#
# block-pre-push.sh が読み (両マーカーのハッシュ検証)、auto-mark.sh が書き込む
# (各 review 完了時のハッシュ書き込み)。両者でファイル名が 1 文字でも乖離すると
# マーカーが永遠に一致せず push が通らなくなる。

# `<git-dir>` 配下に置くマーカーファイルの basename。
#
# ## v1.0.0: /simplify と /code-review を別マーカーに分離 (案 B)
#
# v0.7.0 は「Claude Code v2.1.146 で /simplify が /code-review にリネームされた = 同一 skill
# の改名」と仮定し、両 skill 名を 1 つの `.claude-pre-push-code-reviewed` マーカーに conflate
# していた。しかし一次情報 (公式 CHANGELOG) が示す通り、これは改名ではなく **役割の分岐** だった
# (詳細は lib/first-party-review.sh のヘッダ参照):
#   - /simplify   = cleanup-only (reuse/simplification/efficiency/altitude を **適用** = コードを編集)
#   - /code-review = correctness バグ検出 (findings を **報告**、コードは編集しない = read-only)
# editsCode が真逆の 2 つを 1 マーカーに畳むと「片方を走らせれば gate 通過」になり、cleanup と
# バグ検出のどちらかが構造的に skip され得る穴があった。
#
# v1.0.0 は両者を別マーカーに分離する:
#   - SIMPLIFIED_MARKER_NAME    ← /simplify   (cleanup・編集する。v0.7.0 以前の旧名を復活させ、
#                                  今度は「編集する cleanup」専用の意味で再利用)
#   - CODE_REVIEWED_MARKER_NAME ← /code-review (read-only バグ検出。意味を本来の姿に純化)
# auto-mark.sh の case 分岐がこの 2 skill 名をそれぞれのマーカーに書き分ける。push gate
# (block-pre-push.sh) は CC >= 2.1.154 を確認できたとき両方を、それ以外は fail-open で
# どちらか 1 本を要求する (lib/first-party-review.sh)。
SIMPLIFIED_MARKER_NAME=".claude-pre-push-simplified"
CODE_REVIEWED_MARKER_NAME=".claude-pre-push-code-reviewed"
CODEX_MARKER_NAME=".claude-pre-push-codex-reviewed"
SECURITY_MARKER_NAME=".claude-pre-push-security-reviewed"

# 引数: <git-dir>
# 出力: simplified マーカー (/simplify = cleanup・編集する) の絶対パス
simplified_marker_path() {
  printf '%s/%s' "$1" "$SIMPLIFIED_MARKER_NAME"
}

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
