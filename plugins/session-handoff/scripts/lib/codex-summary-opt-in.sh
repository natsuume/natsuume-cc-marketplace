#!/bin/bash
# session-handoff Codex nested summary opt-in の共有契約。
# save hook と setup helper は marker 名・exact content・安全性判定を必ずこのファイルから共有する。

SESSION_HANDOFF_CODEX_SUMMARY_OPT_IN_MARKER_NAME=".codex-summary-opt-in"
SESSION_HANDOFF_CODEX_SUMMARY_OPT_IN_CONTENT="session-handoff:nested-codex-summary-opt-in:v1"
# shellcheck disable=SC2034 # setup helper が source 後に参照する共有 protocol identifier
SESSION_HANDOFF_CODEX_SUMMARY_OPT_IN_PROTOCOL="v1"

session_handoff_codex_summary_file_mode() {
  marker_path=$1
  stat -c %a "$marker_path" 2>/dev/null || stat -f %Lp "$marker_path" 2>/dev/null
}

# stdout: enabled / disabled / unsafe-* / different-*
session_handoff_codex_summary_opt_in_state() {
  handoff_dir=$1
  marker_path="$handoff_dir/$SESSION_HANDOFF_CODEX_SUMMARY_OPT_IN_MARKER_NAME"

  if [ -L "$handoff_dir" ]; then
    printf '%s' "unsafe-directory-symlink"
    return 0
  fi
  if [ ! -e "$handoff_dir" ]; then
    printf '%s' "disabled"
    return 0
  fi
  if [ ! -d "$handoff_dir" ]; then
    printf '%s' "unsafe-directory-nonregular"
    return 0
  fi
  if [ ! -O "$handoff_dir" ]; then
    printf '%s' "unsafe-directory-owner"
    return 0
  fi

  if [ -L "$marker_path" ]; then
    printf '%s' "unsafe-symlink"
    return 0
  fi
  if [ ! -e "$marker_path" ]; then
    printf '%s' "disabled"
    return 0
  fi
  if [ ! -f "$marker_path" ]; then
    printf '%s' "unsafe-nonregular"
    return 0
  fi
  if [ ! -O "$marker_path" ]; then
    printf '%s' "unsafe-owner"
    return 0
  fi

  marker_mode=$(session_handoff_codex_summary_file_mode "$marker_path")
  if [ "$marker_mode" != "600" ]; then
    printf '%s' "different-mode"
    return 0
  fi
  if ! cmp -s "$marker_path" <(printf '%s' "$SESSION_HANDOFF_CODEX_SUMMARY_OPT_IN_CONTENT"); then
    printf '%s' "different-content"
    return 0
  fi

  printf '%s' "enabled"
}
