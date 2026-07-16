#!/bin/bash
# Inspect and mutate the repo/worktree-scoped consent marker used by the Codex
# semantic validator.  Mutations require an action-specific token from inspect.

set -u

SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
LIBRARY="$SCRIPT_DIR/../hooks/scripts/lib/codex-semantic-opt-in.sh"

if [ ! -r "$LIBRARY" ]; then
  printf '%s\n' 'agent-discipline: opt-in state library is unavailable.' >&2
  exit 1
fi
# shellcheck source=../hooks/scripts/lib/codex-semantic-opt-in.sh disable=SC1091
. "$LIBRARY"

usage() {
  printf '%s\n' 'Usage:' >&2
  printf '%s\n' '  setup-codex-semantic-validator.sh inspect [--repo PATH]' >&2
  printf '%s\n' '  setup-codex-semantic-validator.sh enable --approval-token TOKEN [--repo PATH]' >&2
  printf '%s\n' '  setup-codex-semantic-validator.sh disable --approval-token TOKEN [--repo PATH]' >&2
  exit 2
}

ACTION=${1:-}
[ -n "$ACTION" ] || usage
shift

REPOSITORY='.'
APPROVAL_TOKEN=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo)
      [ "$#" -ge 2 ] || usage
      REPOSITORY=$2
      shift 2
      ;;
    --approval-token)
      [ "$#" -ge 2 ] || usage
      APPROVAL_TOKEN=$2
      shift 2
      ;;
    *) usage ;;
  esac
done

case "$ACTION" in
  inspect|enable|disable) ;;
  *) usage ;;
esac

if ! command -v git >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' 'agent-discipline: setup requires git and jq.' >&2
  exit 1
fi
if ! agent_discipline_codex_opt_in_resolve "$REPOSITORY"; then
  printf '%s\n' 'agent-discipline: --repo must resolve to a non-bare Git worktree.' >&2
  exit 1
fi
agent_discipline_codex_opt_in_classify

# shellcheck disable=SC2016 # backticks are user-facing literal command notation
DISCLOSURE='有効化後、この worktree の gh issue/pr create/edit payload（inline body を含む）、shell adapter が明示参照から読み取った --body-file 内容、current branch は、親 session とは別の nested `codex exec` provider へ送られます。nested process は repository 外の一時 cwd で AGENTS.md・project/user config・hooks・rules・web search・shell/search tool を無効化し、既定で gpt-5.6-sol（AGENT_DISCIPLINE_CODEX_MODEL の明示設定時だけ gpt-5.6-luna）を使います。provider identity と親 session との verdict equality は保証しません。この同意は disable するまで当該 Git worktree に永続します。'

inspect() {
  _agent_discipline_enable_token=''
  _agent_discipline_disable_token=''
  if [ "$AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS" = 'disabled' ]; then
    _agent_discipline_enable_token=$(agent_discipline_codex_approval_token enable 2>/dev/null || true)
  elif [ "$AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS" = 'enabled' ]; then
    _agent_discipline_disable_token=$(agent_discipline_codex_approval_token disable 2>/dev/null || true)
  fi

  jq -n \
    --arg repository "$AGENT_DISCIPLINE_CODEX_REPOSITORY" \
    --arg gitDir "$AGENT_DISCIPLINE_CODEX_GIT_DIR" \
    --arg marker "$AGENT_DISCIPLINE_CODEX_MARKER" \
    --arg status "$AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS" \
    --arg disclosure "$DISCLOSURE" \
    --arg enableToken "$_agent_discipline_enable_token" \
    --arg disableToken "$_agent_discipline_disable_token" '
      {
        repository: $repository,
        gitDir: $gitDir,
        marker: $marker,
        status: $status,
        disclosure: $disclosure,
        enableApprovalToken: (if $enableToken == "" then null else $enableToken end),
        disableApprovalToken: (if $disableToken == "" then null else $disableToken end)
      }
    '
}

refuse_unsafe_state() {
  case "$AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS" in
    disabled|enabled) return 1 ;;
    *)
      printf 'agent-discipline: refusing to mutate unsafe opt-in state: %s (%s)\n' \
        "$AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS" "$AGENT_DISCIPLINE_CODEX_MARKER" >&2
      return 0
      ;;
  esac
}

if [ "$ACTION" = 'inspect' ]; then
  [ -z "$APPROVAL_TOKEN" ] || usage
  inspect
  exit 0
fi

[ -n "$APPROVAL_TOKEN" ] || {
  printf '%s\n' 'agent-discipline: mutation requires --approval-token from a fresh inspect.' >&2
  exit 2
}
if refuse_unsafe_state; then
  exit 1
fi

if [ "$ACTION" = 'enable' ]; then
  if [ "$AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS" != 'disabled' ]; then
    printf '%s\n' 'agent-discipline: semantic validator is already enabled; inspect before choosing an action.' >&2
    exit 1
  fi
  EXPECTED_TOKEN=$(agent_discipline_codex_approval_token enable 2>/dev/null) || {
    printf '%s\n' 'agent-discipline: sha256sum or shasum is required to validate approval tokens.' >&2
    exit 1
  }
  if [ "$APPROVAL_TOKEN" != "$EXPECTED_TOKEN" ]; then
    printf '%s\n' 'agent-discipline: approval token is invalid or stale; inspect again.' >&2
    exit 2
  fi

  CREATED_STATE_DIR=0
  if [ ! -e "$AGENT_DISCIPLINE_CODEX_STATE_DIR" ] && [ ! -L "$AGENT_DISCIPLINE_CODEX_STATE_DIR" ]; then
    umask 077
    if ! mkdir "$AGENT_DISCIPLINE_CODEX_STATE_DIR" 2>/dev/null; then
      printf '%s\n' 'agent-discipline: failed to create the private opt-in state directory; inspect again.' >&2
      exit 1
    fi
    CREATED_STATE_DIR=1
    chmod 700 "$AGENT_DISCIPLINE_CODEX_STATE_DIR" 2>/dev/null || {
      rmdir "$AGENT_DISCIPLINE_CODEX_STATE_DIR" 2>/dev/null || true
      printf '%s\n' 'agent-discipline: failed to secure the opt-in state directory.' >&2
      exit 1
    }
  fi

  # Re-check after state-directory creation and immediately before creating the
  # marker.  The 0700 directory prevents other users from substituting a target.
  agent_discipline_codex_opt_in_classify
  if [ "$AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS" != 'disabled' ]; then
    [ "$CREATED_STATE_DIR" -eq 0 ] || rmdir "$AGENT_DISCIPLINE_CODEX_STATE_DIR" 2>/dev/null || true
    printf '%s\n' 'agent-discipline: opt-in state changed after approval; inspect again.' >&2
    exit 2
  fi

  umask 077
  TEMP_MARKER=$(mktemp "$AGENT_DISCIPLINE_CODEX_STATE_DIR/.enabled.tmp.XXXXXX" 2>/dev/null) || {
    [ "$CREATED_STATE_DIR" -eq 0 ] || rmdir "$AGENT_DISCIPLINE_CODEX_STATE_DIR" 2>/dev/null || true
    printf '%s\n' 'agent-discipline: failed to create a temporary marker.' >&2
    exit 1
  }
  cleanup_temp_marker() {
    # shellcheck disable=SC2317 # EXIT/signal trap invokes this indirectly
    rm -f "$TEMP_MARKER"
  }
  trap cleanup_temp_marker EXIT HUP INT TERM
  if ! printf '%s\n' "$AGENT_DISCIPLINE_CODEX_OPT_IN_MARKER_CONTENT" > "$TEMP_MARKER" || \
     ! chmod 600 "$TEMP_MARKER" || \
     ! ln "$TEMP_MARKER" "$AGENT_DISCIPLINE_CODEX_MARKER" 2>/dev/null; then
    printf '%s\n' 'agent-discipline: marker creation lost a race or failed; inspect again.' >&2
    exit 1
  fi
  rm -f "$TEMP_MARKER"
  trap - EXIT HUP INT TERM

  agent_discipline_codex_opt_in_classify
  if [ "$AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS" != 'enabled' ]; then
    printf '%s\n' 'agent-discipline: created marker did not pass owner/mode/content validation.' >&2
    exit 1
  fi
  inspect
  exit 0
fi

if [ "$AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS" != 'enabled' ]; then
  printf '%s\n' 'agent-discipline: semantic validator is already disabled; inspect before choosing an action.' >&2
  exit 1
fi
EXPECTED_TOKEN=$(agent_discipline_codex_approval_token disable 2>/dev/null) || {
  printf '%s\n' 'agent-discipline: sha256sum or shasum is required to validate approval tokens.' >&2
  exit 1
}
if [ "$APPROVAL_TOKEN" != "$EXPECTED_TOKEN" ]; then
  printf '%s\n' 'agent-discipline: approval token is invalid or stale; inspect again.' >&2
  exit 2
fi

# Revalidate immediately before deletion.  Unsafe substitutions are never removed.
agent_discipline_codex_opt_in_classify
if [ "$AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS" != 'enabled' ]; then
  printf '%s\n' 'agent-discipline: opt-in state changed after approval; refusing removal.' >&2
  exit 2
fi
if ! rm -f "$AGENT_DISCIPLINE_CODEX_MARKER"; then
  printf '%s\n' 'agent-discipline: failed to remove the opt-in marker.' >&2
  exit 1
fi
rmdir "$AGENT_DISCIPLINE_CODEX_STATE_DIR" 2>/dev/null || true
agent_discipline_codex_opt_in_classify
if [ "$AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS" != 'disabled' ]; then
  printf '%s\n' 'agent-discipline: disabled marker did not settle into a safe state; inspect manually.' >&2
  exit 1
fi
inspect
