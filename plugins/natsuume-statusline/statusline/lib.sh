#!/bin/bash
# statusline 共通関数・定数

# ANSIカラー定数
RESET='\033[00m'
BOLD_GREEN='\033[01;32m'
BOLD_YELLOW='\033[01;33m'
BOLD_RED='\033[01;31m'
BOLD_BLUE='\033[01;34m'
BOLD_MAGENTA='\033[01;35m'
BOLD_CYAN='\033[01;36m'

# 使用率に応じた色コードを返す
rate_color() {
  local pct=$1
  if [ "$pct" -ge 80 ] 2>/dev/null; then
    printf '%b' "$BOLD_RED"
  elif [ "$pct" -ge 60 ] 2>/dev/null; then
    printf '%b' "$BOLD_YELLOW"
  else
    printf '%b' "$BOLD_GREEN"
  fi
}

# プログレスバーを描画（幅は第2引数、デフォルト 20 文字）
progress_bar() {
  local pct=${1:-0}
  local width=${2:-20}
  [ "$width" -lt 1 ] && width=1
  local filled=$((pct * width / 100))
  local empty=$((width - filled))
  local bar="" i
  for ((i = 0; i < filled; i++)); do bar+="█"; done
  for ((i = 0; i < empty; i++)); do bar+="░"; done
  printf '[%s]' "$bar"
}

# タイムスタンプから残り時間を算出
# Claude Code は Unix epoch 秒（整数）を渡してくる
# ISO 8601 文字列も将来的な変更に備えて受け付ける
time_remaining() {
  local resets_at="$1"
  [ -z "$resets_at" ] && return

  local reset_epoch
  if [[ "$resets_at" =~ ^[0-9]+$ ]]; then
    reset_epoch="$resets_at"
  else
    # GNU date は `-d`、BSD/macOS date には `-d` がない。
    # 共通の portable な経路として python3 の datetime.fromisoformat に委譲する
    # (`Z` 接尾辞は Python 3.11+ で直接読めるが、3.7-3.10 のため `+00:00` に置換)。
    # `python3` が無い環境では silent return (statusline は毎回呼ばれるため
    # warning 出力はノイズになる。README で optional 依存として明記している)。
    reset_epoch=$(date -d "$resets_at" +%s 2>/dev/null) \
      || reset_epoch=$(python3 -c 'import sys, datetime; s=sys.argv[1].replace("Z","+00:00"); print(int(datetime.datetime.fromisoformat(s).timestamp()))' "$resets_at" 2>/dev/null) \
      || return
  fi

  local now diff_sec
  now=$(date +%s)
  diff_sec=$((reset_epoch - now))
  [ "$diff_sec" -le 0 ] && return

  local days=$((diff_sec / 86400))
  local hours=$(( (diff_sec % 86400) / 3600 ))
  local mins=$(( (diff_sec % 3600) / 60 ))

  if [ "$days" -gt 0 ]; then
    printf '%dd%02dh' "$days" "$hours"
  elif [ "$hours" -gt 0 ]; then
    printf '%dh%02dm' "$hours" "$mins"
  else
    printf '%dm' "$mins"
  fi
}

# パーセンテージを小数第2位以下で切り捨て表示
# 整数値になる場合は小数点を付けず、切り捨てが必要だった場合のみ小数1桁で表示
# 例: 57.99999 -> "57.9", 12 -> "12", 12.5 -> "12.5", 57.0 -> "57"
format_pct() {
  local val="$1"
  [ -z "$val" ] && return
  awk -v v="$val" 'BEGIN {
    floored = int(v * 10) / 10
    if (floored == int(floored)) {
      printf("%d", floored)
    } else {
      printf("%.1f", floored)
    }
  }'
}

# format_pct の結果から判定用の整数（floor）を取り出す
# 例: "57.9" -> 57, "12.0" -> 12
int_pct_from_display() {
  local val="$1"
  printf '%s' "${val%.*}"
}

# git remote URLから owner/repo を抽出（GitHub以外は空）
extract_github_repo() {
  local url="$1"
  echo "$url" | sed -n 's#.*github\.com[:/]\(.*\)$#\1#p' | sed 's/\.git$//'
}

# ユーザー自身の GitHub ログイン名と所属 org の一覧をキャッシュ経由で取得
# キャッシュ: $HOME/.claude/cache/owned-github-namespaces.txt（24時間有効）
# gh コマンドが無い／未認証の場合は空キャッシュを作って空を返す
owned_github_namespaces() {
  local cache_dir="$HOME/.claude/cache"
  local cache_file="$cache_dir/owned-github-namespaces.txt"
  local max_age=$((24 * 3600))

  if [ -f "$cache_file" ]; then
    local mtime now age
    # GNU coreutils は `stat -c %Y`、BSD/macOS は `stat -f %m`。両方を順に試す。
    mtime=$(stat -c %Y "$cache_file" 2>/dev/null || stat -f %m "$cache_file" 2>/dev/null)
    if [ -n "$mtime" ]; then
      now=$(date +%s)
      age=$((now - mtime))
      if [ "$age" -lt "$max_age" ]; then
        cat "$cache_file"
        return
      fi
    fi
  fi

  mkdir -p "$cache_dir"
  if ! command -v gh >/dev/null 2>&1; then
    : > "$cache_file"
    return
  fi

  # 初回または期限切れ時にのみ gh を叩く（数百ms〜数秒のコスト）。
  # 並行 statusline 実行が同一 cache_file を同時 truncate し、 失敗側の空ファイルを
  # 別プロセスが読む競合を避けるため、 tmp に書いてから mv でアトミックに差し替える。
  local tmp
  tmp=$(mktemp "$cache_dir/.ns.XXXXXX" 2>/dev/null) || tmp="$cache_file.$$.tmp"
  {
    gh api user --jq '.login' 2>/dev/null
    gh api user/orgs --jq '.[].login' 2>/dev/null
  } > "$tmp"
  mv -f "$tmp" "$cache_file" 2>/dev/null
  cat "$cache_file" 2>/dev/null
}

# owner が「自分のものとみなせる namespace」か判定
# 一致すれば true (return 0)、不一致／空なら false (return 1)
is_owned_namespace() {
  local owner="$1"
  [ -z "$owner" ] && return 1
  local line
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    [ "$line" = "$owner" ] && return 0
  done < <(owned_github_namespaces)
  return 1
}

# ターミナル幅を取得（取得不能なら 80 を返す）
# COLUMNS（Claude Code v2.1.153+ が渡す実描画幅）→ tput → /dev/tty 経由の stty の順。
# COLUMNS を最優先にするのは、 tput が terminfo/ioctl 由来の固定値を返して CC の live な
# 幅より優先されるのを避けるため。 COLUMNS 未設定の旧 CC では tput/stty にフォールバックする。
terminal_width() {
  local w
  if [ -n "$COLUMNS" ] && [ "$COLUMNS" -gt 0 ] 2>/dev/null; then
    printf '%s' "$COLUMNS"
    return
  fi
  w=$(tput cols 2>/dev/null)
  if [ -n "$w" ] && [ "$w" -gt 0 ] 2>/dev/null; then
    printf '%s' "$w"
    return
  fi
  # `</dev/tty` の open 失敗 (制御端末なし) メッセージも抑制するため、 グループ全体を
  # 2>/dev/null で包む。 単純コマンドへの `stty ... </dev/tty 2>/dev/null` では入力リダイレクト
  # の open エラーが 2>/dev/null 適用前に漏れる。
  w=$( { stty size </dev/tty | awk '{print $2}'; } 2>/dev/null )
  if [ -n "$w" ] && [ "$w" -gt 0 ] 2>/dev/null; then
    printf '%s' "$w"
    return
  fi
  printf '80'
}

# ANSIエスケープシーケンスを除いた可視文字数を返す
visible_length() {
  local s="$1" stripped
  stripped=$(printf '%s' "$s" | sed $'s/\x1b\\[[0-9;]*m//g')
  printf '%s' "${#stripped}"
}

# 文字列をANSIエスケープを保持したまま可視幅 max で切り詰める
# 末尾に色漏れ防止のリセットコードを必ず付与する
truncate_visible() {
  local s="$1" max="$2"
  local len=${#s}
  local i=0 visible=0 in_esc=0 c result=""

  while [ "$i" -lt "$len" ] && [ "$visible" -lt "$max" ]; do
    c="${s:$i:1}"
    if [ "$in_esc" -eq 1 ]; then
      result+="$c"
      [ "$c" = "m" ] && in_esc=0
    elif [ "$c" = $'\033' ]; then
      result+="$c"
      in_esc=1
    else
      result+="$c"
      visible=$((visible + 1))
    fi
    i=$((i + 1))
  done

  # 開きっぱなしのエスケープがあれば閉じきる
  while [ "$i" -lt "$len" ] && [ "$in_esc" -eq 1 ]; do
    c="${s:$i:1}"
    result+="$c"
    [ "$c" = "m" ] && in_esc=0
    i=$((i + 1))
  done

  result+=$'\033[00m'
  printf '%s' "$result"
}

# セグメント配列を separator で結合し、可視幅 max に収まるよう調整
# 入りきらない場合は途中で打ち切り、末尾に … を付ける
# 引数: $1=separator, $2=max_width, $3..=segments
fit_segments() {
  local separator="$1" max_width="$2"
  shift 2
  local sep_width=${#separator}
  local total=0 first=1 result="" seg seg_w needed available

  for seg in "$@"; do
    seg_w=$(visible_length "$seg")
    needed=$seg_w
    [ "$first" -eq 0 ] && needed=$((needed + sep_width))

    if [ $((total + needed)) -le "$max_width" ]; then
      [ "$first" -eq 0 ] && result+="$separator"
      result+="$seg"
      total=$((total + needed))
      first=0
      continue
    fi

    # 残りスペースに省略形でも入るなら詰めて終了
    available=$((max_width - total))
    [ "$first" -eq 0 ] && available=$((available - sep_width))
    if [ "$available" -ge 2 ]; then
      [ "$first" -eq 0 ] && result+="$separator"
      result+=$(truncate_visible "$seg" $((available - 1)))
      result+='…'
    fi
    break
  done

  printf '%s' "$result"
}
