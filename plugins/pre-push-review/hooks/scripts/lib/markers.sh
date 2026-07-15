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
# 出力: runtime に対応する marker storage directory
#
# Codex plugin hook は PLUGIN_ROOT を非空で受け取り、既定 sandbox では .git が
# read-only のため writable な PLUGIN_DATA を使う。repo / worktree ごとの衝突を避ける
# key は physical git-dir の絶対 path bytes だけを SHA-256 に入力して求める。
# PLUGIN_DATA が使えない Codex runtime では .git へ fallback せず non-zero を返す。
marker_storage_dir() {
  local git_dir="$1"
  local canonical_git_dir digest repo_key

  if [ -z "${PLUGIN_ROOT:-}" ]; then
    if [ -z "$git_dir" ]; then
      printf '%s\n' '[pre-push-review] git-dir が空のため marker path を解決できません。' >&2
      return 1
    fi
    printf '%s' "$git_dir"
    return 0
  fi

  case "${PLUGIN_DATA:-}" in
    /*) ;;
    *)
      printf '%s\n' '[pre-push-review] Codex PLUGIN_DATA が空、未設定、または絶対 path でないため marker path を解決できません。' >&2
      return 1
      ;;
  esac

  canonical_git_dir=$(cd "$git_dir" 2>/dev/null && pwd -P) || {
    printf '%s\n' '[pre-push-review] physical git-dir を解決できないため Codex marker path を生成できません。' >&2
    return 1
  }

  if command -v sha256sum >/dev/null 2>&1; then
    digest=$(printf '%s' "$canonical_git_dir" | sha256sum) || return 1
  elif command -v shasum >/dev/null 2>&1; then
    digest=$(printf '%s' "$canonical_git_dir" | shasum -a 256) || return 1
  else
    printf '%s\n' '[pre-push-review] sha256sum / shasum が無いため Codex marker path を生成できません。' >&2
    return 1
  fi

  repo_key=${digest%% *}
  if [ "${#repo_key}" -ne 64 ]; then
    printf '%s\n' '[pre-push-review] git-dir SHA-256 の長さが不正なため Codex marker path を生成できません。' >&2
    return 1
  fi
  case "$repo_key" in
    *[!0-9a-f]*)
      printf '%s\n' '[pre-push-review] git-dir SHA-256 が lowercase hex でないため Codex marker path を生成できません。' >&2
      return 1
      ;;
  esac

  printf '%s/pre-push-review/markers/%s' "${PLUGIN_DATA%/}" "$repo_key"
}

marker_path() {
  local storage_dir
  storage_dir=$(marker_storage_dir "$1") || return 1
  printf '%s/%s' "$storage_dir" "$2"
}

# 引数: <git-dir>
# 出力: code-reviewed マーカー (/code-review = read-only バグ検出) の path
code_reviewed_marker_path() {
  marker_path "$1" "$CODE_REVIEWED_MARKER_NAME"
}

# 引数: <git-dir>
# 出力: codex-reviewed マーカーの path
codex_marker_path() {
  marker_path "$1" "$CODEX_MARKER_NAME"
}

# 引数: <git-dir>
# 出力: security-reviewed マーカーの path
security_marker_path() {
  marker_path "$1" "$SECURITY_MARKER_NAME"
}
