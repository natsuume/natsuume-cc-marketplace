#!/bin/bash
# setup-codex-summary.sh - nested Codex transcript summary の repository 単位 opt-in を管理する。
#
# inspect は read-only で現在状態と action-bound plan token を返す。enable/disable は直前の
# inspect token を必須とし、marker state が変化していれば何も変更しない。marker は git-dir 内の
# owner-only regular file、exact versioned content、mode 0600 の場合だけ enabled とみなす。

set -e
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091 # runtime-resolved plugin root
source "$SCRIPT_DIR/lib/codex-summary-opt-in.sh"

usage() {
  printf '%s\n' \
    "usage: setup-codex-summary.sh inspect [--repo <path>]" \
    "       setup-codex-summary.sh enable --plan-token <sha256> [--repo <path>]" \
    "       setup-codex-summary.sh disable --plan-token <sha256> [--repo <path>]" >&2
}

sha256_stream() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  else
    printf '%s\n' "[session-handoff] sha256sum または shasum が必要です。" >&2
    return 1
  fi
}

ACTION="${1:-}"
case "$ACTION" in
  inspect|enable|disable)
    shift
    ;;
  *)
    usage
    exit 2
    ;;
esac

REPO="."
PLAN_TOKEN=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo)
      [ "$#" -ge 2 ] || {
        usage
        exit 2
      }
      REPO=$2
      shift 2
      ;;
    --plan-token)
      [ "$#" -ge 2 ] || {
        usage
        exit 2
      }
      PLAN_TOKEN=$2
      shift 2
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [ "$ACTION" = "inspect" ] && [ -n "$PLAN_TOKEN" ]; then
  usage
  exit 2
fi
if [ "$ACTION" != "inspect" ] && [ -z "$PLAN_TOKEN" ]; then
  printf '%s\n' "[session-handoff] $ACTION には承認済み inspect の --plan-token が必要です。" >&2
  exit 2
fi

GIT_DIR=$(git -C "$REPO" rev-parse --absolute-git-dir 2>/dev/null) || {
  printf '[session-handoff] git repository を解決できません: %s\n' "$REPO" >&2
  exit 1
}
if [ -L "$GIT_DIR" ] || [ ! -d "$GIT_DIR" ] || [ ! -O "$GIT_DIR" ]; then
  printf '[session-handoff] git-dir が安全な owner directory ではありません: %s\n' "$GIT_DIR" >&2
  exit 1
fi

HANDOFF_DIR="$GIT_DIR/session-handoff"
MARKER_PATH="$HANDOFF_DIR/$SESSION_HANDOFF_CODEX_SUMMARY_OPT_IN_MARKER_NAME"

marker_fingerprint() {
  state=$1
  case "$state" in
    enabled|different-mode|different-content)
      {
        printf 'mode\0%s\0content\0' "$(session_handoff_codex_summary_file_mode "$MARKER_PATH")"
        command cat "$MARKER_PATH"
      } | sha256_stream
      ;;
    *)
      printf '%s' "$state"
      ;;
  esac
}

compute_plan_token() {
  requested_action=$1
  current_state=$(session_handoff_codex_summary_opt_in_state "$HANDOFF_DIR")
  current_fingerprint=$(marker_fingerprint "$current_state") || return 1
  {
    printf 'session-handoff-codex-summary-consent\0%s\0' "$SESSION_HANDOFF_CODEX_SUMMARY_OPT_IN_PROTOCOL"
    printf 'action\0%s\0target\0%s\0state\0%s\0fingerprint\0%s\0' \
      "$requested_action" "$MARKER_PATH" "$current_state" "$current_fingerprint"
  } | sha256_stream
}

CURRENT_STATE=$(session_handoff_codex_summary_opt_in_state "$HANDOFF_DIR")

if [ "$ACTION" = "inspect" ]; then
  printf 'target\t%s\n' "$MARKER_PATH"
  printf 'state\t%s\n' "$CURRENT_STATE"
  printf 'protocol\t%s\n' "$SESSION_HANDOFF_CODEX_SUMMARY_OPT_IN_PROTOCOL"
  printf 'exact-content\t%s\n' "$SESSION_HANDOFF_CODEX_SUMMARY_OPT_IN_CONTENT"
  printf 'privacy-boundary\t%s\n' \
    "nested codex uses --ignore-user-config and can send transcript data to the default provider/account instead of the parent session provider"
  printf 'enable-plan-token\t%s\n' "$(compute_plan_token enable)"
  printf 'disable-plan-token\t%s\n' "$(compute_plan_token disable)"
  exit 0
fi

case "$CURRENT_STATE" in
  disabled|enabled|different-mode|different-content) ;;
  *)
    printf '[session-handoff] unsafe marker state は変更しません: %s (%s)\n' \
      "$MARKER_PATH" "$CURRENT_STATE" >&2
    exit 1
    ;;
esac

CURRENT_TOKEN=$(compute_plan_token "$ACTION") || exit 1
if [ "$CURRENT_TOKEN" != "$PLAN_TOKEN" ]; then
  printf '%s\n' \
    "[session-handoff] inspect 後に opt-in marker state が変化しました。再 inspect と再承認が必要です。" >&2
  exit 1
fi

if [ "$ACTION" = "enable" ]; then
  umask 077
  mkdir -p "$HANDOFF_DIR"
  if [ -L "$HANDOFF_DIR" ] || [ ! -d "$HANDOFF_DIR" ] || [ ! -O "$HANDOFF_DIR" ]; then
    printf '[session-handoff] handoff directory が安全ではありません: %s\n' "$HANDOFF_DIR" >&2
    exit 1
  fi
  chmod 700 "$HANDOFF_DIR"
elif [ ! -e "$HANDOFF_DIR" ]; then
  printf '%s\n' "[session-handoff] nested Codex summary opt-in は既に disabled です。" >&2
  exit 0
fi

LOCK_DIR="$HANDOFF_DIR/.codex-summary-opt-in.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  printf '%s\n' "[session-handoff] 別の opt-in setup が実行中です。" >&2
  exit 1
fi
TEMP_FILE=""
# trap から間接呼び出しされるため、ShellCheck の到達不能推定をこの関数だけ抑制する。
# shellcheck disable=SC2317
cleanup() {
  if [ -n "$TEMP_FILE" ]; then
    rm -f "$TEMP_FILE" 2>/dev/null || true
  fi
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

# lock 取得後にも approved state/action を再検証する。
CURRENT_STATE=$(session_handoff_codex_summary_opt_in_state "$HANDOFF_DIR")
case "$CURRENT_STATE" in
  disabled|enabled|different-mode|different-content) ;;
  *)
    printf '[session-handoff] unsafe marker state は変更しません: %s (%s)\n' \
      "$MARKER_PATH" "$CURRENT_STATE" >&2
    exit 1
    ;;
esac
CURRENT_TOKEN=$(compute_plan_token "$ACTION") || exit 1
if [ "$CURRENT_TOKEN" != "$PLAN_TOKEN" ]; then
  printf '%s\n' \
    "[session-handoff] write 開始前に opt-in marker state が変化しました。再 inspect と再承認が必要です。" >&2
  exit 1
fi

if [ "$ACTION" = "enable" ]; then
  TEMP_FILE=$(mktemp "$HANDOFF_DIR/.codex-summary-opt-in.tmp.XXXXXX") || exit 1
  printf '%s' "$SESSION_HANDOFF_CODEX_SUMMARY_OPT_IN_CONTENT" > "$TEMP_FILE"
  chmod 600 "$TEMP_FILE"
  mv "$TEMP_FILE" "$MARKER_PATH"
  TEMP_FILE=""
  if [ "$(session_handoff_codex_summary_opt_in_state "$HANDOFF_DIR")" != "enabled" ]; then
    printf '%s\n' "[session-handoff] opt-in marker の install verification に失敗しました。" >&2
    exit 1
  fi
  printf '[session-handoff] enabled: %s\n' "$MARKER_PATH" >&2
  exit 0
fi

# disable: owner regular marker だけを削除する。unsafe state は上の case で拒否済み。
if [ "$CURRENT_STATE" != "disabled" ]; then
  rm -f "$MARKER_PATH"
fi
if [ "$(session_handoff_codex_summary_opt_in_state "$HANDOFF_DIR")" != "disabled" ]; then
  printf '%s\n' "[session-handoff] opt-in marker の disable verification に失敗しました。" >&2
  exit 1
fi
printf '[session-handoff] disabled: %s\n' "$MARKER_PATH" >&2
exit 0
