#!/bin/bash
# markers.sh
# pre-merge-codex-review プラグインが git-dir 直下に置くファイルの名前を単一ソース化する。
#
# codex review wrapper が pending attestation と投稿用のレビュー本文を書き、 subagent
# lifecycle hook (auto-mark.sh) が launch attestation / tombstone を扱って pending を
# final attestation へ昇格する。 merge gate は final attestation と本文ファイルを読み、
# PR にレビューコメントを投稿してから merge を通す。 これらの path が 1 文字でも乖離すると
# attestation は永遠に一致せず merge が通らなくなるため、 ここに集約する。
#
# ## 本 plugin が扱うファイル
#
# - PRE_MERGE_FINAL_MARKER   ← report / HEAD 検証を通過した final attestation
# - PRE_MERGE_PENDING_MARKER ← codex review wrapper が書く pending attestation
# - PRE_MERGE_COMMENT_BODY   ← codex review wrapper が書く投稿用のレビュー本文
# - LAUNCH_ATTESTATION       ← SubagentStart が記録するレビュー開始時 HEAD (agent_id ごと)
# - LAUNCH_TOMBSTONE         ← attestation 消費時に排他作成される one-shot 記録 (agent_id ごと)
#
# ファイル名の prefix はすべて `.claude-pre-merge-` であり、 pre-push 系が使う
# `.claude-pre-push-*` とは衝突しない (両 plugin が同じ git-dir を共有しても互いの
# attestation を読み書きしない)。
#
# ## attestation ファイルの内容契約
#
# pending / final はいずれも次の 2 行のテキストで、 内容は同一である (昇格は rename のみで
# 内容を書き換えない):
#
#   pr=<PR 番号 (全数字)>
#   head=<full head SHA (40 hex 小文字)>
#
# 投稿用の本文ファイルは 1 行目が機械可読 header で、 2 行目以降が codex review report:
#
#   <!-- codex-review: head=<attestation と同じ head SHA> status=pass|findings -->
#   # Codex Review
#   ...
#
# launch attestation と tombstone の内容は、 いずれも記録時点のローカル HEAD の full SHA
# 1 行である。

# 検証を通過した attestation。 gate はこれと本文ファイルの組を見てレビューを投稿する。
PRE_MERGE_FINAL_MARKER_NAME=".claude-pre-merge-codex-reviewed"
# codex review wrapper が書く pending attestation。 auto-mark.sh が report / HEAD 検証を
# 通過した場合にのみ final へ昇格する。
PRE_MERGE_PENDING_MARKER_NAME=".claude-pre-merge-codex-reviewed.pending"
# codex review wrapper が書く投稿用のレビュー本文。 gate が投稿の本文として使うため、
# final への昇格後も残す (投稿完了時に gate が attestation と併せて掃除する)。
PRE_MERGE_COMMENT_BODY_NAME=".claude-pre-merge-codex-comment.md"
# SubagentStart が書く launch attestation (agent_id ごとに 1 ファイル) の prefix。
# auto-mark.sh の SubagentStart / SubagentStop 契約が「レビュー開始時のローカル HEAD」を
# 束縛するために使う。
LAUNCH_ATTESTATION_PREFIX=".claude-pre-merge-launch-"
# tombstone は「この agent_id は一度 SubagentStop (stop_hook_active=false) まで到達した」
# ことを恒久的に記録し、 同一 agent_id での 2 回目以降の SubagentStart を構造的に拒否する。
# attestation とは別の prefix にするのは、 agent_id が `.done` 等の文字列を含みうるため
# `<attestation-path>.done` のような suffix 方式だと agent_id の内容次第で name 衝突しうる
# ため (例: agent_id="foo.done" だと衝突する)。 prefix 方式なら agent_id の中身に依存しない。
LAUNCH_TOMBSTONE_PREFIX=".claude-pre-merge-done-"

# 引数: <git-dir>
# 出力: attestation storage directory (= git-dir 直下)
marker_storage_dir() {
  local git_dir="$1"

  if [ -z "$git_dir" ]; then
    printf '%s\n' '[pre-merge-codex-review] git-dir が空のため attestation path を解決できません。' >&2
    return 1
  fi
  printf '%s' "$git_dir"
}

marker_path() {
  local storage_dir
  storage_dir=$(marker_storage_dir "$1") || return 1
  printf '%s/%s' "$storage_dir" "$2"
}

# 引数: <git-dir>
# 出力: final attestation の path
pre_merge_final_marker_path() {
  marker_path "$1" "$PRE_MERGE_FINAL_MARKER_NAME"
}

# 引数: <git-dir>
# 出力: codex review wrapper が書く pending attestation の path
pre_merge_pending_marker_path() {
  marker_path "$1" "$PRE_MERGE_PENDING_MARKER_NAME"
}

# 引数: <git-dir>
# 出力: codex review wrapper が書く投稿用レビュー本文の path
pre_merge_comment_body_path() {
  marker_path "$1" "$PRE_MERGE_COMMENT_BODY_NAME"
}

# 引数: <git-dir> <agent_id>
# 出力: SubagentStart が書く launch attestation
#       (git-dir/.claude-pre-merge-launch-<agent_id>) の path
#
# agent_id の validation (path 混入防止: ^[A-Za-z0-9._-]{1,128}$) は呼び出し側
# (auto-mark.sh) の責務。 本関数は単一ソース化された storage resolution を再利用して
# agent_id を prefix に連結するだけで、 それ自身は agent_id の形状を検証しない。
launch_attestation_path() {
  local git_dir="$1"
  local agent_id="$2"
  marker_path "$git_dir" "${LAUNCH_ATTESTATION_PREFIX}${agent_id}"
}

# 引数: <git-dir> <agent_id>
# 出力: SubagentStop が作る launch tombstone
#       (git-dir/.claude-pre-merge-done-<agent_id>) の path
#
# agent_id の validation は launch_attestation_path と同様に呼び出し側 (auto-mark.sh) の
# 責務。 tombstone は一度作られたら恒久的に残り (prune しない。 理由は auto-mark.sh の
# ヘッダ契約を参照)、 同一 agent_id での SubagentStart 再発火 (resume 等) を拒否するために
# 使う。
launch_tombstone_path() {
  local git_dir="$1"
  local agent_id="$2"
  marker_path "$git_dir" "${LAUNCH_TOMBSTONE_PREFIX}${agent_id}"
}
