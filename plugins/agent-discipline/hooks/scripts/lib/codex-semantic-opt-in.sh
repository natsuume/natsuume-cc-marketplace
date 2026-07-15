#!/bin/bash
# repo/worktree scoped consent state for the Codex semantic validator.
#
# The state lives below the worktree's absolute git dir so it is neither tracked nor
# shared accidentally with another worktree.  A private directory lets the helper
# create the regular marker without following a repository-supplied symlink.

AGENT_DISCIPLINE_CODEX_OPT_IN_STATE_BASENAME='.agent-discipline-codex-semantic-validator'
AGENT_DISCIPLINE_CODEX_OPT_IN_MARKER_BASENAME='enabled'
AGENT_DISCIPLINE_CODEX_OPT_IN_MARKER_CONTENT='agent-discipline-codex-semantic-validator-enabled-v1'

agent_discipline_codex_stat_uid() {
  stat -c '%u' "$1" 2>/dev/null || stat -f '%u' "$1" 2>/dev/null
}

agent_discipline_codex_stat_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null
}

agent_discipline_codex_stat_size() {
  stat -c '%s' "$1" 2>/dev/null || stat -f '%z' "$1" 2>/dev/null
}

agent_discipline_codex_opt_in_resolve() {
  _agent_discipline_repository=$1

  AGENT_DISCIPLINE_CODEX_REPOSITORY=$(git -C "$_agent_discipline_repository" rev-parse --show-toplevel 2>/dev/null) || return 1
  AGENT_DISCIPLINE_CODEX_GIT_DIR=$(git -C "$_agent_discipline_repository" rev-parse --absolute-git-dir 2>/dev/null) || return 1

  case "$AGENT_DISCIPLINE_CODEX_REPOSITORY" in
    /*) ;;
    *) return 1 ;;
  esac
  case "$AGENT_DISCIPLINE_CODEX_GIT_DIR" in
    /*) ;;
    *) return 1 ;;
  esac

  AGENT_DISCIPLINE_CODEX_STATE_DIR="$AGENT_DISCIPLINE_CODEX_GIT_DIR/$AGENT_DISCIPLINE_CODEX_OPT_IN_STATE_BASENAME"
  AGENT_DISCIPLINE_CODEX_MARKER="$AGENT_DISCIPLINE_CODEX_STATE_DIR/$AGENT_DISCIPLINE_CODEX_OPT_IN_MARKER_BASENAME"
  return 0
}

# Sets AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS.  Only "enabled" authorizes a nested
# Codex invocation.  Every unsafe or malformed state is intentionally distinct so
# the helper can refuse to overwrite/remove it and the hook can fail closed.
agent_discipline_codex_opt_in_classify() {
  AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unresolved'
  AGENT_DISCIPLINE_CODEX_OPT_IN_UID=''
  AGENT_DISCIPLINE_CODEX_OPT_IN_MODE=''
  AGENT_DISCIPLINE_CODEX_OPT_IN_SIZE=''

  if [ -L "$AGENT_DISCIPLINE_CODEX_STATE_DIR" ]; then
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-state-symlink'
    return 0
  fi
  if [ ! -e "$AGENT_DISCIPLINE_CODEX_STATE_DIR" ]; then
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='disabled'
    return 0
  fi
  if [ ! -d "$AGENT_DISCIPLINE_CODEX_STATE_DIR" ]; then
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-state-non-directory'
    return 0
  fi

  _agent_discipline_uid=$(id -u 2>/dev/null) || {
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-owner-unverifiable'
    return 0
  }
  _agent_discipline_state_uid=$(agent_discipline_codex_stat_uid "$AGENT_DISCIPLINE_CODEX_STATE_DIR") || {
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-owner-unverifiable'
    return 0
  }
  _agent_discipline_state_mode=$(agent_discipline_codex_stat_mode "$AGENT_DISCIPLINE_CODEX_STATE_DIR") || {
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-mode-unverifiable'
    return 0
  }
  if [ "$_agent_discipline_state_uid" != "$_agent_discipline_uid" ]; then
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-state-owner'
    return 0
  fi
  if [ "$_agent_discipline_state_mode" != '700' ]; then
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-state-mode'
    return 0
  fi

  if [ -L "$AGENT_DISCIPLINE_CODEX_MARKER" ]; then
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-marker-symlink'
    return 0
  fi
  if [ ! -e "$AGENT_DISCIPLINE_CODEX_MARKER" ]; then
    # An empty, helper-owned 0700 state directory is a recoverable interrupted
    # enable/disable state.  Unexpected entries are not silently removed.
    _agent_discipline_state_entries=$(LC_ALL=C ls -A "$AGENT_DISCIPLINE_CODEX_STATE_DIR" 2>/dev/null) || {
      AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-state-unreadable'
      return 0
    }
    if [ -n "$_agent_discipline_state_entries" ]; then
      AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-state-contents'
    else
      AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='disabled'
    fi
    return 0
  fi
  if [ ! -f "$AGENT_DISCIPLINE_CODEX_MARKER" ]; then
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-marker-nonregular'
    return 0
  fi

  AGENT_DISCIPLINE_CODEX_OPT_IN_UID=$(agent_discipline_codex_stat_uid "$AGENT_DISCIPLINE_CODEX_MARKER") || {
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-owner-unverifiable'
    return 0
  }
  AGENT_DISCIPLINE_CODEX_OPT_IN_MODE=$(agent_discipline_codex_stat_mode "$AGENT_DISCIPLINE_CODEX_MARKER") || {
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-mode-unverifiable'
    return 0
  }
  AGENT_DISCIPLINE_CODEX_OPT_IN_SIZE=$(agent_discipline_codex_stat_size "$AGENT_DISCIPLINE_CODEX_MARKER") || {
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-size-unverifiable'
    return 0
  }

  if [ "$AGENT_DISCIPLINE_CODEX_OPT_IN_UID" != "$_agent_discipline_uid" ]; then
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-marker-owner'
    return 0
  fi
  if [ "$AGENT_DISCIPLINE_CODEX_OPT_IN_MODE" != '600' ]; then
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-marker-mode'
    return 0
  fi

  _agent_discipline_expected_size=$(printf '%s\n' "$AGENT_DISCIPLINE_CODEX_OPT_IN_MARKER_CONTENT" | wc -c | tr -d '[:space:]')
  if [ "$AGENT_DISCIPLINE_CODEX_OPT_IN_SIZE" != "$_agent_discipline_expected_size" ]; then
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='invalid-marker-content'
    return 0
  fi
  _agent_discipline_marker_content=$(cat "$AGENT_DISCIPLINE_CODEX_MARKER" 2>/dev/null) || {
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='invalid-marker-content'
    return 0
  }
  if [ "$_agent_discipline_marker_content" != "$AGENT_DISCIPLINE_CODEX_OPT_IN_MARKER_CONTENT" ]; then
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='invalid-marker-content'
    return 0
  fi

  # GNU find の -mindepth/-maxdepth は macOS 標準 find と互換でないため使わない。
  # private 0700 directory の直下は marker 1 entry だけという exact contract で比較する。
  _agent_discipline_state_entries=$(LC_ALL=C ls -A "$AGENT_DISCIPLINE_CODEX_STATE_DIR" 2>/dev/null) || {
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-state-unreadable'
    return 0
  }
  if [ "$_agent_discipline_state_entries" != "$AGENT_DISCIPLINE_CODEX_OPT_IN_MARKER_BASENAME" ]; then
    AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='unsafe-state-contents'
    return 0
  fi

  AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS='enabled'
  return 0
}

agent_discipline_codex_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  else
    return 1
  fi
}

# The token is a state/action binding used to prove that the caller inspected the
# current state.  It is deliberately not treated as user approval by the Skill.
agent_discipline_codex_approval_token() {
  _agent_discipline_action=$1
  printf '%s\000%s\000%s\000%s\000%s\000%s\000%s\000' \
    'agent-discipline-codex-semantic-validator-approval-v1' \
    "$_agent_discipline_action" \
    "$AGENT_DISCIPLINE_CODEX_REPOSITORY" \
    "$AGENT_DISCIPLINE_CODEX_GIT_DIR" \
    "$AGENT_DISCIPLINE_CODEX_MARKER" \
    "$AGENT_DISCIPLINE_CODEX_OPT_IN_STATUS" \
    "$AGENT_DISCIPLINE_CODEX_OPT_IN_SIZE" \
    | agent_discipline_codex_sha256
}
