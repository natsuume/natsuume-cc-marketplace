#!/bin/bash
# mark-review.sh - marker writer の低レベル診断 helper (通常フローでは使用しない)。
#
# auto-mark.sh が subagent lifecycle hook の launch attestation と report を検証して
# 2 reviewer の marker を書く。通常の利用者は本 helper を直接呼ばない。hash/marker の計算確認
# や保守時の診断専用として残す。

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/diff-hash.sh
source "$SCRIPT_DIR/lib/diff-hash.sh"
# shellcheck source=lib/markers.sh
source "$SCRIPT_DIR/lib/markers.sh"

usage() {
  printf '%s\n' "usage: mark-review.sh code|security|all" >&2
}

if [ "$#" -ne 1 ]; then
  usage
  exit 2
fi

case "$1" in
  code|security|all) ;;
  *)
    usage
    exit 2
    ;;
esac

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || {
  printf '%s\n' "[pre-push-review] git repository ではありません。" >&2
  exit 1
}
BASE=$(detect_base_branch) || {
  printf '%s\n' "[pre-push-review] default branch を検出できません。" >&2
  exit 1
}
BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null) || {
  printf '%s\n' "[pre-push-review] detached HEAD では marker を作成できません。" >&2
  exit 1
}
case "$BRANCH" in
  master|main)
    printf '%s\n' "[pre-push-review] default branch は review marker の対象外です。" >&2
    exit 1
    ;;
esac

HASH=$(compute_review_hash "$BASE") || {
  printf '%s\n' "[pre-push-review] review hash を計算できません。" >&2
  exit 1
}

TEMP_MARKER=""
cleanup() {
  if [ -n "$TEMP_MARKER" ]; then
    rm -f "$TEMP_MARKER"
  fi
}
trap cleanup EXIT

write_marker() {
  local marker_fn="$1"
  local marker_path
  marker_path=$("$marker_fn" "$GIT_DIR")
  TEMP_MARKER="${marker_path}.tmp.$$"
  umask 077
  printf '%s' "$HASH" > "$TEMP_MARKER"
  mv "$TEMP_MARKER" "$marker_path"
  TEMP_MARKER=""
  printf '[pre-push-review] marker updated: %s\n' "$marker_path" >&2
}

case "$1" in
  code)
    write_marker code_reviewed_marker_path
    ;;
  security)
    write_marker security_marker_path
    ;;
  all)
    write_marker code_reviewed_marker_path
    write_marker security_marker_path
    ;;
esac

exit 0
