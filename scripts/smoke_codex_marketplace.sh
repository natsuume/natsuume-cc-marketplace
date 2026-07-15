#!/bin/bash
# Install this repository as a Codex marketplace and install every plugin.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

command -v codex >/dev/null 2>&1 || {
  printf '%s\n' "error: codex CLI is required" >&2
  exit 1
}
command -v python3 >/dev/null 2>&1 || {
  printf '%s\n' "error: python3 is required" >&2
  exit 1
}

SMOKE_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/codex-marketplace-smoke.XXXXXX")
cleanup() {
  rm -rf "$SMOKE_ROOT"
}
trap cleanup EXIT

export CODEX_HOME="$SMOKE_ROOT/codex-home"
mkdir -p "$CODEX_HOME"

run_strict_config_with_deadline() {
  local strict_home=$1 stdout_path=$2 stderr_path=$3
  local strict_pid started_at now
  CODEX_HOME="$strict_home" codex --strict-config app-server < /dev/null \
    > "$stdout_path" 2> "$stderr_path" &
  strict_pid=$!
  started_at=$(date +%s)
  while kill -0 "$strict_pid" 2>/dev/null; do
    now=$(date +%s)
    if [ $((now - started_at)) -ge 30 ]; then
      kill "$strict_pid" 2>/dev/null || true
      sleep 1
      kill -9 "$strict_pid" 2>/dev/null || true
      wait "$strict_pid" 2>/dev/null || true
      return 124
    fi
    sleep 1
  done
  if wait "$strict_pid"; then
    return 0
  else
    return $?
  fi
}

MARKETPLACE_NAME=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["name"])' \
  "$ROOT/.agents/plugins/marketplace.json")
PLUGIN_NAMES=$(python3 -c \
  'import json,sys; print(" ".join(p["name"] for p in json.load(open(sys.argv[1], encoding="utf-8"))["plugins"]))' \
  "$ROOT/.agents/plugins/marketplace.json")

codex plugin marketplace add "$ROOT" --json > "$SMOKE_ROOT/marketplace.json"

COUNT=0
for plugin in $PLUGIN_NAMES; do
  printf 'installing %s@%s\n' "$plugin" "$MARKETPLACE_NAME"
  codex plugin add "$plugin@$MARKETPLACE_NAME" --json \
    > "$SMOKE_ROOT/plugin-$plugin.json"
  COUNT=$((COUNT + 1))
done

codex plugin list --marketplace "$MARKETPLACE_NAME" --json \
  > "$SMOKE_ROOT/installed.json"
python3 - "$SMOKE_ROOT/installed.json" "$COUNT" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
plugins = payload.get("installed") if isinstance(payload, dict) else None
if not isinstance(plugins, list):
    raise SystemExit("error: codex plugin list did not return installed plugins")
installed = [
    plugin
    for plugin in plugins
    if plugin.get("installed") is True and plugin.get("enabled") is True
]
expected = int(sys.argv[2])
if len(installed) != expected:
    raise SystemExit(
        f"error: expected {expected} installed plugins, found {len(installed)}"
    )
PY

# `--version` は config 読み込み前に終了するため検証には使えない。setup Skills が案内する
# app-server + EOF 経路が、実際に strict parser を通ることを pinned/latest CLI で固定する。
STRICT_HOME="$SMOKE_ROOT/strict-config-home"
mkdir -p "$STRICT_HOME"
printf '%s\n' 'marketplace_smoke_unknown_key = true' > "$STRICT_HOME/config.toml"
if run_strict_config_with_deadline \
  "$STRICT_HOME" \
  "$SMOKE_ROOT/strict-config.stdout" \
  "$SMOKE_ROOT/strict-config.stderr"; then
  printf '%s\n' "error: strict config smoke accepted an unknown key" >&2
  exit 1
else
  strict_status=$?
  if [ "$strict_status" -eq 124 ]; then
    printf '%s\n' "error: strict config smoke timed out on an unknown key" >&2
    exit 1
  fi
fi

printf '%s\n' \
  '[tui]' \
  'status_line = ["project-name", "current-dir", "git-branch", "context-used", "five-hour-limit", "weekly-limit"]' \
  > "$STRICT_HOME/config.toml"
if ! run_strict_config_with_deadline \
  "$STRICT_HOME" \
  "$SMOKE_ROOT/valid-config.stdout" \
  "$SMOKE_ROOT/valid-config.stderr"; then
  printf '%s\n' "error: strict config smoke rejected or timed out on a valid config" >&2
  exit 1
fi

printf 'OK: Codex installed %s plugins from %s.\n' "$COUNT" "$MARKETPLACE_NAME"
