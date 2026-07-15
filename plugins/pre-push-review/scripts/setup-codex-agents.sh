#!/bin/bash
# setup-codex-agents.sh - Codex project custom agent を安全かつ決定的に導入する。
#
# inspect は destination の状態と plan token を表示するだけで変更しない。write は直前に
# ユーザーが承認した inspect の token を必須とし、destination が変化していた場合は何も
# 書かずに失敗する。GNU 固有 option を使わず、macOS 標準 Bash 3.2 でも動作させる。

set -e
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE_DIR="$PLUGIN_DIR/skills/setup-pre-push-agents/assets/agents"

AGENT_FILES=(
  "pre-push-correctness-reviewer.toml"
  "pre-push-independent-reviewer.toml"
  "pre-push-security-reviewer.toml"
)

usage() {
  printf '%s\n' \
    "usage: setup-codex-agents.sh inspect [--repo <path>]" \
    "       setup-codex-agents.sh write --plan-token <sha256> [--repo <path>]" >&2
}

sha256_stream() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  else
    printf '%s\n' "[pre-push-review] sha256sum または shasum が必要です。" >&2
    return 1
  fi
}

MODE="${1:-}"
case "$MODE" in
  inspect|write)
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
      if [ "$#" -lt 2 ]; then
        usage
        exit 2
      fi
      REPO="$2"
      shift 2
      ;;
    --plan-token)
      if [ "$#" -lt 2 ]; then
        usage
        exit 2
      fi
      PLAN_TOKEN="$2"
      shift 2
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [ "$MODE" = "write" ] && [ -z "$PLAN_TOKEN" ]; then
  printf '%s\n' "[pre-push-review] write には承認済み inspect の --plan-token が必要です。" >&2
  exit 2
fi
if [ "$MODE" = "inspect" ] && [ -n "$PLAN_TOKEN" ]; then
  usage
  exit 2
fi

for agent_file in "${AGENT_FILES[@]}"; do
  if [ ! -f "$TEMPLATE_DIR/$agent_file" ] || [ -L "$TEMPLATE_DIR/$agent_file" ]; then
    printf '[pre-push-review] agent template が不正です: %s\n' "$TEMPLATE_DIR/$agent_file" >&2
    exit 1
  fi
done

REPO_ROOT=$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null) || {
  printf '[pre-push-review] git repository を解決できません: %s\n' "$REPO" >&2
  exit 1
}
CODEX_DIR="$REPO_ROOT/.codex"
TARGET_DIR="$CODEX_DIR/agents"

assert_safe_directory_shape() {
  if [ -L "$CODEX_DIR" ]; then
    printf '[pre-push-review] symlink の .codex は変更しません: %s\n' "$CODEX_DIR" >&2
    return 1
  fi
  if [ -e "$CODEX_DIR" ] && [ ! -d "$CODEX_DIR" ]; then
    printf '[pre-push-review] .codex が directory ではありません: %s\n' "$CODEX_DIR" >&2
    return 1
  fi
  if [ -L "$TARGET_DIR" ]; then
    printf '[pre-push-review] symlink の agents directory は変更しません: %s\n' "$TARGET_DIR" >&2
    return 1
  fi
  if [ -e "$TARGET_DIR" ] && [ ! -d "$TARGET_DIR" ]; then
    printf '[pre-push-review] agents が directory ではありません: %s\n' "$TARGET_DIR" >&2
    return 1
  fi
}

destination_state() {
  local destination="$1"
  local template="$2"
  if [ -L "$destination" ]; then
    printf '%s' "unsafe-symlink"
  elif [ ! -e "$destination" ]; then
    printf '%s' "missing"
  elif [ ! -f "$destination" ]; then
    printf '%s' "unsafe-nonregular"
  elif cmp -s "$template" "$destination"; then
    printf '%s' "current"
  else
    printf '%s' "different"
  fi
}

compute_plan_token() {
  {
    printf 'pre-push-review-codex-agents-v1\0'
    printf 'target\0%s\0' "$TARGET_DIR"
    local agent_file
    for agent_file in "${AGENT_FILES[@]}"; do
      local template="$TEMPLATE_DIR/$agent_file"
      local destination="$TARGET_DIR/$agent_file"
      local state
      state=$(destination_state "$destination" "$template")
      printf 'name\0%s\0state\0%s\0template\0' "$agent_file" "$state"
      command cat "$template"
      printf '\0destination\0'
      if [ "$state" = "current" ] || [ "$state" = "different" ]; then
        command cat "$destination"
      else
        printf '%s' "$state"
      fi
      printf '\0'
    done
  } | sha256_stream
}

inspect() {
  local token
  token=$(compute_plan_token) || return 1
  printf 'target\t%s\n' "$TARGET_DIR"
  local agent_file
  for agent_file in "${AGENT_FILES[@]}"; do
    printf '%s\t%s\n' \
      "$(destination_state "$TARGET_DIR/$agent_file" "$TEMPLATE_DIR/$agent_file")" \
      "$agent_file"
  done
  printf 'plan-token\t%s\n' "$token"
}

assert_safe_directory_shape || exit 1

if [ "$MODE" = "inspect" ]; then
  inspect
  exit 0
fi

CURRENT_TOKEN=$(compute_plan_token) || exit 1
if [ "$CURRENT_TOKEN" != "$PLAN_TOKEN" ]; then
  printf '%s\n' \
    "[pre-push-review] inspect 後に agent destination が変化しました。再 inspect と再承認が必要です。" >&2
  exit 1
fi

umask 077
mkdir -p "$TARGET_DIR"
assert_safe_directory_shape || exit 1

LOCK_DIR="$TARGET_DIR/.pre-push-review-install.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  printf '%s\n' "[pre-push-review] 別の agent setup が実行中です。" >&2
  exit 1
fi
TEMP_FILE=""
cleanup() {
  if [ -n "$TEMP_FILE" ]; then
    rm -f "$TEMP_FILE"
  fi
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

# lock 取得までに destination が変化していないことをもう一度確認する。
CURRENT_TOKEN=$(compute_plan_token) || exit 1
if [ "$CURRENT_TOKEN" != "$PLAN_TOKEN" ]; then
  printf '%s\n' \
    "[pre-push-review] write 開始前に agent destination が変化しました。再 inspect と再承認が必要です。" >&2
  exit 1
fi

for agent_file in "${AGENT_FILES[@]}"; do
  TEMPLATE="$TEMPLATE_DIR/$agent_file"
  DESTINATION="$TARGET_DIR/$agent_file"
  STATE=$(destination_state "$DESTINATION" "$TEMPLATE")
  case "$STATE" in
    current)
      continue
      ;;
    missing|different)
      ;;
    *)
      printf '[pre-push-review] unsafe destination は変更しません: %s (%s)\n' \
        "$DESTINATION" "$STATE" >&2
      exit 1
      ;;
  esac
  TEMP_FILE=$(mktemp "${DESTINATION}.tmp.XXXXXX") || exit 1
  command cp "$TEMPLATE" "$TEMP_FILE"
  chmod 0644 "$TEMP_FILE"
  mv "$TEMP_FILE" "$DESTINATION"
  TEMP_FILE=""
  printf '[pre-push-review] installed: %s\n' "$DESTINATION" >&2
done

for agent_file in "${AGENT_FILES[@]}"; do
  if ! cmp -s "$TEMPLATE_DIR/$agent_file" "$TARGET_DIR/$agent_file"; then
    printf '[pre-push-review] install verification failed: %s\n' "$agent_file" >&2
    exit 1
  fi
done

printf '%s\n' "[pre-push-review] Codex project agents を導入しました。新しい Codex thread で有効になります。" >&2
exit 0
