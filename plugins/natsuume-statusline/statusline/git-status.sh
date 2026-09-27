#!/bin/bash
# statusline 1 行目で使う git 情報の取得と、git status (porcelain v2) 出力の解析
#
# git の呼び出しは collect_git_info に集約し、描画 1 回あたり
# rev-parse --show-toplevel / status --porcelain=v2 --branch --no-ahead-behind / remote get-url の 3 回に抑える。
# index.lock を取らないよう、呼び出し側 (main.sh) が GIT_OPTIONAL_LOCKS=0 を export しておく。

# git status --porcelain=v2 --branch の出力からブランチ名を取り出す。
# 引数: $1=porcelain v2 出力
# 出力: `# branch.head` の値。detached HEAD (`(detached)`) とヘッダ行が無い場合は空文字
parse_status_branch() {
  local line head
  while IFS= read -r line; do
    case "$line" in
      "# branch.head "*)
        head="${line#"# branch.head "}"
        [ "$head" = "(detached)" ] && head=""
        printf '%s' "$head"
        return
        ;;
    esac
  done <<<"$1"
}

# git status --porcelain=v2 --branch の出力を porcelain v1 互換の XY ステータス行に変換する。
# 引数: $1=porcelain v2 出力
# 出力: エントリ 1 件につき 1 行の 2 文字 (X=index 側, Y=worktree 側。変更なしは空白)。
#   通常 (`1`)・rename/copy (`2`)・unmerged (`u`) は XY 欄の `.` を空白に置き換え、
#   untracked (`?`) は `??` とする。ヘッダ行 (`#`) と ignored (`!`) は出力しない
parse_status_entries() {
  local line xy
  while IFS= read -r line; do
    case "$line" in
      "1 "* | "2 "* | "u "*)
        xy="${line:2:2}"
        printf '%s\n' "${xy//./ }"
        ;;
      "? "*)
        printf '??\n'
        ;;
    esac
  done <<<"$1"
}

# cwd の git 情報を取得し、呼び出し側のグローバル変数に設定する。
# 引数: $1=cwd
# 設定する変数:
#   is_git    — git の作業ツリー内なら 1、それ以外は 0
#   toplevel  — 作業ツリーのルートの絶対パス
#   porcelain — parse_status_entries の出力 (porcelain v1 互換の XY ステータス行)
#   repo_url  — origin の URL (未設定なら空)
#   branch    — parse_status_branch の出力
collect_git_info() {
  local cwd="$1" status_v2
  is_git=0
  toplevel=""
  porcelain=""
  repo_url=""
  branch=""
  # 作業ツリーの外 (git 管理外・.git ディレクトリ内・bare repository) では失敗する
  toplevel=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || return 0
  [ -n "$toplevel" ] || return 0
  is_git=1
  # --branch はブランチ名のために付ける。使わない upstream との ahead/behind の計算は
  # 履歴をたどるため --no-ahead-behind で止める
  status_v2=$(git -C "$cwd" status --porcelain=v2 --branch --no-ahead-behind 2>/dev/null)
  porcelain=$(parse_status_entries "$status_v2")
  branch=$(parse_status_branch "$status_v2")
  repo_url=$(git -C "$cwd" remote get-url origin 2>/dev/null)
}
