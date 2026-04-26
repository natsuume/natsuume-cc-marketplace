#!/bin/bash
# statusline 1行目: パス、リポジトリ、ブランチ、変更量、未コミット

# パス表示（~/... 形式に短縮）
render_path() {
  local cwd="$1"
  local short="${cwd/#$HOME/\~}"
  printf '%b%s%b' "$BOLD_BLUE" "$short" "$RESET"
}

# パス表示（リポジトリルート以下に短縮）
# git 管理下でない場合は通常の render_path にフォールバック
render_path_repo_relative() {
  local cwd="$1"
  local toplevel rel repo_name
  toplevel=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || {
    render_path "$cwd"
    return
  }
  repo_name="${toplevel##*/}"
  rel="${cwd#$toplevel}"
  rel="${rel#/}"
  if [ -n "$rel" ]; then
    printf '%b%s/%s%b' "$BOLD_BLUE" "$repo_name" "$rel" "$RESET"
  else
    printf '%b%s%b' "$BOLD_BLUE" "$repo_name" "$RESET"
  fi
}

# GitHubリポジトリ名
# 第2引数で prefix を上書き可能（フォールバック表示時に "" を渡してプレフィックス省略）
# owner が自分のアカウント／所属 org の場合は repo 名のみ表示
render_repository() {
  local cwd="$1"
  local prefix="${2-repository: }"
  local url repo owner name
  url=$(git -C "$cwd" remote get-url origin 2>/dev/null) || return
  repo=$(extract_github_repo "$url")
  [ -z "$repo" ] && return

  owner="${repo%%/*}"
  name="${repo#*/}"
  if [ -n "$owner" ] && [ -n "$name" ] && [ "$owner" != "$name" ] && is_owned_namespace "$owner"; then
    printf '%s%b%s%b' "$prefix" "$BOLD_CYAN" "$name" "$RESET"
  else
    printf '%s%b%s%b' "$prefix" "$BOLD_CYAN" "$repo" "$RESET"
  fi
}

# ブランチ名
# 第2引数で prefix を上書き可能（フォールバック表示時に "" を渡してプレフィックス省略）
render_branch() {
  local cwd="$1"
  local prefix="${2-branch: }"
  local branch
  branch=$(git -C "$cwd" branch --show-current 2>/dev/null)
  [ -z "$branch" ] && return
  printf '%s%b%s%b' "$prefix" "$BOLD_MAGENTA" "$branch" "$RESET"
}

# staged/modified 変更量
render_changes() {
  local porcelain="$1"
  [ -z "$porcelain" ] && return

  local staged modified parts=""
  staged=$(echo "$porcelain" | grep -c '^[MADRC]')
  modified=$(echo "$porcelain" | grep -c '^.[MD]')

  [ "$staged" -gt 0 ] && parts="${parts}$(printf '%bstaged:%d%b' "$BOLD_GREEN" "$staged" "$RESET")"
  if [ "$modified" -gt 0 ]; then
    [ -n "$parts" ] && parts="${parts} "
    parts="${parts}$(printf '%bmodified:%d%b' "$BOLD_YELLOW" "$modified" "$RESET")"
  fi

  [ -z "$parts" ] && return
  printf '%b' "$parts"
}

# 未コミット変更数（staged + unstaged + untracked の合計）
# 変更なしの場合も clean を表示し、状態を必ず可視化する
render_uncommitted() {
  local porcelain="$1"
  local count=0

  if [ -n "$porcelain" ]; then
    # grep -c . で空行を除いた実際の件数を数える（echo は末尾改行を加えるため）
    count=$(printf '%s\n' "$porcelain" | grep -c . 2>/dev/null)
  fi

  if [ "$count" -eq 0 ]; then
    printf '%bclean%b' "$BOLD_GREEN" "$RESET"
  else
    printf '%b%d uncommitted%b' "$BOLD_YELLOW" "$count" "$RESET"
  fi
}
