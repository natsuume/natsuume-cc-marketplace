#!/bin/bash
# statusline メインスクリプト: JSON解析 → 行の組み立て → 出力
#
# 注: `set -e` / `set -u` / `pipefail` は意図的に有効化していない。
# 一部のコンポーネント (git/gh/レートリミット) が欠落していてもプロンプトを
# 完全に空にしないよう、各レンダラが個別に空文字を返してフォールバックする
# 設計に依存している。strict mode を入れると 1 か所のエラーで全行が消える。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# JSON入力を一括パース
input=$(cat)

# jq が無い環境では JSON を解析できないため空出力で終了する (jq は README で必須依存と明記)。
command -v jq >/dev/null 2>&1 || exit 0

# `@sh` は安全にクオートするため injection は無いが、 入力は信頼境界外なので echo ではなく
# printf で渡す。 cwd は workspace.current_dir → top-level cwd → 空 の順でフォールバックし、
# キー欠落時に文字列 "null" がパスとして表示されるのを防ぐ。
eval "$(printf '%s' "$input" | jq -r '
  @sh "cwd=\(.workspace.current_dir // .cwd // "")",
  @sh "rate_5h=\(.rate_limits.five_hour.used_percentage // "")",
  @sh "rate_5h_reset=\(.rate_limits.five_hour.resets_at // "")",
  @sh "rate_7d=\(.rate_limits.seven_day.used_percentage // "")",
  @sh "rate_7d_reset=\(.rate_limits.seven_day.resets_at // "")"
')"

# 共通関数・各行のコンポーネントを読み込み
source "$SCRIPT_DIR/lib.sh"
source "$SCRIPT_DIR/line1.sh"
source "$SCRIPT_DIR/line2.sh"
source "$SCRIPT_DIR/line3.sh"

# ターミナル幅を一度だけ取得し、各行の組み立てで共有する
TERM_WIDTH=$(terminal_width)

# --- 1行目: パス、リポジトリ、ブランチ、変更量、未コミット ---
# 段階的フォールバックで組み立てる:
#   L1: prefix あり (`repository:` `branch:`) + フルパス
#   L2: prefix あり + パス短縮（リポジトリルート相対）
#   L3: prefix なし + パス短縮
# 各段階で全幅がターミナル幅に収まるか確認し、収まる最も豊かな表示を採用する。

is_git=0
toplevel=""
porcelain=""
repo_url=""
branch=""
sep=" | "
sep_w=${#sep}

# git 由来の情報は描画あたり一度だけ取得して各レンダラへ渡す (#79: render 毎の git 再呼び出しを排除)。
if git -C "$cwd" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  is_git=1
  toplevel=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)
  porcelain=$(git -C "$cwd" status --porcelain 2>/dev/null)
  repo_url=$(git -C "$cwd" remote get-url origin 2>/dev/null)
  branch=$(git -C "$cwd" branch --show-current 2>/dev/null)
fi

# 与えられた prefix 設定で他セグメント（path以外）を組み立て、配列 OTHER に格納する
# 引数: $1=repo_prefix, $2=branch_prefix
build_other_segments() {
  local repo_prefix="$1" branch_prefix="$2"
  OTHER=()
  local out
  if [ "$is_git" -eq 1 ]; then
    out=$(render_repository "$repo_url" "$repo_prefix")
    [ -n "$out" ] && OTHER+=("$out")
    out=$(render_branch "$branch" "$branch_prefix")
    [ -n "$out" ] && OTHER+=("$out")
    out=$(render_changes "$porcelain")
    [ -n "$out" ] && OTHER+=("$out")
    out=$(render_uncommitted "$porcelain")
    [ -n "$out" ] && OTHER+=("$out")
  fi
}

# 配列 OTHER の合計可視幅（セパレータ込み）
other_total_width() {
  local total=0
  local s
  for s in "${OTHER[@]}"; do
    total=$((total + sep_w + $(visible_length "$s")))
  done
  printf '%s' "$total"
}

# パスセグメントを決定する:
# - cwd がリポジトリルート、かつ repository 表示が出る場合に限りパスを省略
#   (origin が無い／GitHub 以外のリポジトリでは repository 表示が空になるため、
#    位置を示す情報が消えないようパスを残す)
# - git 管理下でフルパス込みがターミナル幅を超える場合は短縮版に
# - それ以外はフルパス
decide_path_segment() {
  PATH_SEGMENT=""
  local path_full full_total
  path_full=$(render_path "$cwd")
  if [ "$is_git" -eq 1 ] && [ -n "$toplevel" ] && [ "$cwd" = "$toplevel" ] \
    && [ -n "$(render_repository "$repo_url" "")" ]; then
    PATH_SEGMENT=""
    return
  fi
  if [ "$is_git" -eq 1 ]; then
    full_total=$(( $(visible_length "$path_full") + $(other_total_width) ))
    if [ "$full_total" -gt "$TERM_WIDTH" ]; then
      PATH_SEGMENT=$(render_path_repo_relative "$cwd" "$toplevel")
      return
    fi
  fi
  PATH_SEGMENT="$path_full"
}

# 全セグメントの可視幅を計算
all_total_width() {
  local total=0 first=1 s
  [ -n "$PATH_SEGMENT" ] && { total=$(visible_length "$PATH_SEGMENT"); first=0; }
  for s in "${OTHER[@]}"; do
    [ "$first" -eq 0 ] && total=$((total + sep_w))
    total=$((total + $(visible_length "$s")))
    first=0
  done
  printf '%s' "$total"
}

# L1: prefix あり + パス（自動短縮）
build_other_segments "repository: " "branch: "
decide_path_segment
total=$(all_total_width)

# L3: 入りきらない & git 管理下なら prefix を落として再構築
if [ "$total" -gt "$TERM_WIDTH" ] && [ "$is_git" -eq 1 ]; then
  build_other_segments "" ""
  decide_path_segment
fi

segments=()
[ -n "$PATH_SEGMENT" ] && segments+=("$PATH_SEGMENT")
segments+=("${OTHER[@]}")

# 1行目はターミナル幅に収めて出力（折り返しが発生すると2行目以降の表示が崩れるため）
fit_segments "$sep" "$TERM_WIDTH" "${segments[@]}"

# --- 2行目: レートリミット ---
line2_out=$(render_ratelimit "$rate_5h" "$rate_5h_reset" "$rate_7d" "$rate_7d_reset")
if [ -n "$line2_out" ]; then
  printf '\n%b' "$line2_out"
fi

# --- 3行目: 将来拡張用 ---
line3_out=$(render_line3)
if [ -n "$line3_out" ]; then
  printf '\n%b' "$line3_out"
fi
