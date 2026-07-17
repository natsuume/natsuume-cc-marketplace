#!/bin/bash
# official companion v1.0.6 の単発 status を bounded poll に構成する。
# GNU timeout / date +%s%N に依存せず、macOS bash 3.2 と Linux の両方で動作する。

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANION=${1:-}
JOB_ID=${2:-}
TIMEOUT_MS=${3:-}

if [ -z "$COMPANION" ] || [ -z "$JOB_ID" ]; then
  printf '%s\n' '[poll-codex-job] companion and job ID are required.' >&2
  exit 1
fi
case "$TIMEOUT_MS" in
  ''|*[!0-9]*)
    printf '%s\n' '[poll-codex-job] timeout-ms must be a non-negative integer.' >&2
    exit 1 ;;
esac

TIMEOUT_SECONDS=$(((TIMEOUT_MS + 999) / 1000))
STARTED_AT=$(date +%s) || exit 1
STATUS_FILE=$(mktemp "${TMPDIR:-/tmp}/codex-job-status.XXXXXX") || exit 1
trap 'rm -f "$STATUS_FILE"' EXIT
trap 'exit 130' HUP INT TERM

while :; do
  node "$COMPANION" status "$JOB_ID" --json > "$STATUS_FILE" || exit $?
  if node "$SCRIPT_DIR/inspect-codex-job-status.mjs" "$STATUS_FILE"; then
    cat "$STATUS_FILE"
    exit 0
  else
    INSPECT_STATUS=$?
  fi
  if [ "$INSPECT_STATUS" -ne 1 ]; then
    printf '%s\n' '[poll-codex-job] status returned invalid or unknown job JSON.' >&2
    exit 1
  fi

  NOW=$(date +%s) || exit 1
  if [ $((NOW - STARTED_AT)) -ge "$TIMEOUT_SECONDS" ]; then
    printf '%s\n' "[poll-codex-job] job $JOB_ID did not become terminal within ${TIMEOUT_MS}ms." >&2
    exit 2
  fi
  sleep 1
done
