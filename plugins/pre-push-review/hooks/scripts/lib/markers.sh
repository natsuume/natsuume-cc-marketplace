#!/bin/bash
# markers.sh
# pre-push-review プラグインのレビューマーカーファイル名を単一ソース化する。
#
# block-pre-push.sh が読み (3 マーカーのハッシュ検証)、 auto-mark.sh が final marker を
# 書き込む。run-codex-review.sh は Codex review 完了時の pending attestation を書き、
# auto-mark.sh が parent-safe report の正常完了後に final marker へ昇格する。各 path が
# 1 文字でも乖離すると
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
CODEX_PENDING_MARKER_NAME=".claude-pre-push-codex-reviewed.pending"
SECURITY_MARKER_NAME=".claude-pre-push-security-reviewed"
# issue #285: SubagentStart が書く launch attestation (agent_id ごとに 1 ファイル) の prefix。
# auto-mark.sh の SubagentStart/SubagentStop 契約が「開始時 review hash」を束縛するために使う。
LAUNCH_ATTESTATION_PREFIX=".claude-pre-push-launch-"
# issue #285 (codex review P1 指摘): SendMessage による resume で SubagentStart が
# 同一 agent_id で再発火する環境では、 attestation の「作り直し」がフル review を経ない
# まま開始 hash を更新してしまう (= resume 後の再 stop が誤って marker を書ける経路)。
# tombstone は「この agent_id は一度 SubagentStop (stop_hook_active=false) まで到達した」
# ことを恒久的に記録し、 同一 agent_id での 2 回目以降の SubagentStart を構造的に拒否する。
# attestation とは別の prefix にするのは、 agent_id が `.done` 等の文字列を含みうるため
# `<attestation-path>.done` のような suffix 方式だと agent_id の内容次第で name 衝突しうる
# ため (例: agent_id="foo.done" だと衝突する)。 prefix 方式なら agent_id の中身に依存しない。
LAUNCH_TOMBSTONE_PREFIX=".claude-pre-push-done-"

# 引数: <git-dir> [runtime]
# 出力: runtime に対応する marker storage directory
#
# runtime は hook payload を検証した caller が明示的に `codex` を渡す。
# Claude Code も CLAUDE_PLUGIN_ROOT / CLAUDE_PLUGIN_DATA を持つため、plugin env の存在で
# runtime を推測しない。Codex の既定 sandbox では .git が read-only のため、
# PLUGIN_DATA を優先し、未設定時は CLAUDE_PLUGIN_DATA を互換 fallback として使う。
# repo / worktree ごとの衝突を避ける key は physical git-dir の絶対 path bytes
# だけを SHA-256 に入力して求める。選択した plugin data path が使えない Codex
# runtime では .git へ fallback せず non-zero を返す。
marker_storage_dir() {
  local git_dir="$1"
  local runtime="${2:-claude}"
  local plugin_data canonical_git_dir digest repo_key

  if [ "$runtime" != "codex" ]; then
    if [ -z "$git_dir" ]; then
      printf '%s\n' '[pre-push-review] git-dir が空のため marker path を解決できません。' >&2
      return 1
    fi
    printf '%s' "$git_dir"
    return 0
  fi

  if [ "${PLUGIN_DATA+x}" = "x" ]; then
    plugin_data="$PLUGIN_DATA"
  else
    plugin_data="${CLAUDE_PLUGIN_DATA:-}"
  fi

  case "$plugin_data" in
    /*) ;;
    *)
      printf '%s\n' '[pre-push-review] Codex plugin data path (PLUGIN_DATA / CLAUDE_PLUGIN_DATA) が空、未設定、または絶対 path でないため marker path を解決できません。' >&2
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

  printf '%s/pre-push-review/markers/%s' "${plugin_data%/}" "$repo_key"
}

marker_path() {
  local storage_dir
  storage_dir=$(marker_storage_dir "$1" "${3:-claude}") || return 1
  printf '%s/%s' "$storage_dir" "$2"
}

# 引数: <git-dir>
# 出力: code-reviewed マーカー (/code-review = read-only バグ検出) の path
code_reviewed_marker_path() {
  marker_path "$1" "$CODE_REVIEWED_MARKER_NAME" "${2:-claude}"
}

# 引数: <git-dir>
# 出力: codex-reviewed マーカーの path
codex_marker_path() {
  marker_path "$1" "$CODEX_MARKER_NAME" "${2:-claude}"
}

# 引数: <git-dir>
# 出力: codex review wrapper が書く pending attestation の path。
# Claude Code の auto-mark.sh が parent-safe report 成功後にのみ final marker へ昇格する。
codex_pending_marker_path() {
  marker_path "$1" "$CODEX_PENDING_MARKER_NAME" "${2:-claude}"
}

# 引数: <git-dir>
# 出力: security-reviewed マーカーの path
security_marker_path() {
  marker_path "$1" "$SECURITY_MARKER_NAME" "${2:-claude}"
}

# 引数: <git-dir> <agent_id> [runtime]
# 出力: SubagentStart が書く launch attestation (git-dir/.claude-pre-push-launch-<agent_id>) の path
#
# agent_id の validation (path 混入防止: ^[A-Za-z0-9._-]{1,128}$ 等) は呼び出し側
# (auto-mark.sh) の責務。 本関数は既存の marker_path (= 単一ソース化された storage
# resolution) を再利用して agent_id を prefix に連結するだけで、 それ自身は agent_id の
# 形状を検証しない。
launch_attestation_path() {
  local git_dir="$1"
  local agent_id="$2"
  local runtime="${3:-claude}"
  marker_path "$git_dir" "${LAUNCH_ATTESTATION_PREFIX}${agent_id}" "$runtime"
}

# 引数: <git-dir> <agent_id> [runtime]
# 出力: SubagentStop が作る launch tombstone (git-dir/.claude-pre-push-done-<agent_id>) の path
#
# agent_id の validation は launch_attestation_path と同様に呼び出し側 (auto-mark.sh) の
# 責務。 tombstone は一度作られたら (30 日の opportunistic prune までは) 恒久的に残り、
# 同一 agent_id での SubagentStart 再発火 (resume 等) を拒否するために使う。
launch_tombstone_path() {
  local git_dir="$1"
  local agent_id="$2"
  local runtime="${3:-claude}"
  marker_path "$git_dir" "${LAUNCH_TOMBSTONE_PREFIX}${agent_id}" "$runtime"
}
