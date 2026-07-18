#!/bin/bash
# statusline 共通関数・定数

_STATUSLINE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_STATUSLINE_WIDTH_HELPER="$_STATUSLINE_LIB_DIR/display-width.py"

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

# パーセンテージを表示用に整形する。
# 第2引数 as_int=1 のとき四捨五入して整数表示（横幅節約用の段階0）。
# それ以外は小数第2位以下を切り捨て、整数値なら小数点を付けず、端数があれば小数1桁。
# 例(既定):  57.99999 -> "57.9", 12 -> "12", 12.5 -> "12.5", 57.0 -> "57"
# 例(整数): 57.99 -> "58", 12.4 -> "12", 12.5 -> "13"（四捨五入。pct は非負前提）
format_pct() {
  local val="$1" as_int="${2:-0}"
  [ -z "$val" ] && return
  awk -v v="$val" -v as_int="$as_int" 'BEGIN {
    if (as_int == 1) {
      printf("%d", int(v + 0.5))
      exit
    }
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

# トークン数を人間可読な短縮表記にする
# 1000未満はそのまま、1000以上は k / 1000000以上は M を付け、小数1桁（末尾 .0 は省略）
# 例: 512 -> "512", 75100 -> "75.1k", 200000 -> "200k", 1000000 -> "1M", 1500000 -> "1.5M"
humanize_tokens() {
  local n="$1"
  # 値は jq から整数で渡る。空・非整数（異常値）は素通しして壊さない。
  case "$n" in
    '' | *[!0-9]*)
      printf '%s' "$n"
      return
      ;;
  esac
  awk -v n="$n" 'BEGIN {
    if (n < 1000)         { printf("%d", n); exit }
    if (n < 1000000)      { unit = "k"; v = n / 1000 }
    else                  { unit = "M"; v = n / 1000000 }
    s = sprintf("%.1f", v)
    sub(/\.0$/, "", s)
    printf("%s%s", s, unit)
  }'
}

# git remote URLから owner/repo を抽出（GitHub以外は空）
extract_github_repo() {
  local url="$1"
  echo "$url" | sed -n 's#.*github\.com[:/]\(.*\)$#\1#p' | sed 's/\.git$//'
}

# GNU timeout が標準搭載されない macOS でも外部 command の待機時間を制限する。
# non-interactive Bash の monitor mode で command を専用 process group にし、deadline
# 到達時は descendant ごと終了する。statusline 用の read-only query にだけ使用する。
_statusline_run_with_timeout() {
  local timeout_seconds="$1"
  shift
  local monitor_was_enabled command_pid watchdog_pid command_status

  case "$timeout_seconds" in
    ''|*[!0-9]*|0) return 1 ;;
  esac
  [ "$#" -gt 0 ] || return 1

  case $- in
    *m*) monitor_was_enabled=1 ;;
    *)
      monitor_was_enabled=0
      set -m
      ;;
  esac
  (exec "$@") &
  command_pid=$!

  (
    sleep "$timeout_seconds"
    kill -TERM -- "-$command_pid" 2>/dev/null || exit 0
    # query の出力先は一時ファイルで、graceful cleanup を必要としない。TERM を
    # 無視する descendant が statusline の pipe を保持し続けないよう直ちに回収する。
    kill -KILL -- "-$command_pid" 2>/dev/null || true
  ) &
  watchdog_pid=$!
  if [ "$monitor_was_enabled" -eq 0 ]; then
    set +m
  fi

  if wait "$command_pid" 2>/dev/null; then
    command_status=0
  else
    command_status=$?
  fi
  # watchdog の shell だけでなく待機中の sleep も同じ process group から回収する。
  kill -TERM -- "-$watchdog_pid" 2>/dev/null || true
  kill -KILL -- "-$watchdog_pid" 2>/dev/null || true
  wait "$watchdog_pid" 2>/dev/null || true
  return "$command_status"
}

# ユーザー自身の GitHub ログイン名と所属 org の一覧をキャッシュ経由で取得
# キャッシュ: $HOME/.claude/cache/owned-github-namespaces.txt（24時間有効）
# gh コマンドが無い／未認証の場合は空キャッシュを作って空を返す
owned_github_namespaces() {
  local cache_dir="$HOME/.claude/cache"
  local cache_file="$cache_dir/owned-github-namespaces.txt"
  local lock_dir="$cache_dir/.owned-github-namespaces.lock"
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

  # cache miss の並行描画では 1 process だけが refresh し、他は stale cache (または
  # 初回なら空) を返す。通常 refresh は 2 秒上限のため、30 秒超の lock は前回の
  # statusline 異常終了で残った stale lock とみなして reclaim する。
  local lock_acquired=0 lock_mtime lock_now lock_age
  if mkdir "$lock_dir" 2>/dev/null; then
    lock_acquired=1
  else
    lock_mtime=$(stat -c %Y "$lock_dir" 2>/dev/null || stat -f %m "$lock_dir" 2>/dev/null)
    if [ -n "$lock_mtime" ]; then
      lock_now=$(date +%s)
      lock_age=$((lock_now - lock_mtime))
      if [ "$lock_age" -ge 30 ] && rmdir "$lock_dir" 2>/dev/null \
        && mkdir "$lock_dir" 2>/dev/null; then
        lock_acquired=1
      fi
    fi
  fi
  if [ "$lock_acquired" -ne 1 ]; then
    cat "$cache_file" 2>/dev/null
    return 0
  fi

  # user / org endpoint は独立しているため並行取得し、各 process group を 2 秒で
  # 打ち切る。合計待ち時間を endpoint 数に比例させず、完了後だけ atomic replace する。
  local tmp user_tmp orgs_tmp user_pid orgs_pid
  tmp=$(mktemp "$cache_dir/.ns.XXXXXX" 2>/dev/null) || tmp="$cache_file.$$.tmp"
  user_tmp="${tmp}.user"
  orgs_tmp="${tmp}.orgs"
  _statusline_run_with_timeout 2 gh api user --jq '.login' > "$user_tmp" 2>/dev/null &
  user_pid=$!
  _statusline_run_with_timeout 2 gh api user/orgs --jq '.[].login' > "$orgs_tmp" 2>/dev/null &
  orgs_pid=$!
  wait "$user_pid" 2>/dev/null || true
  wait "$orgs_pid" 2>/dev/null || true
  {
    cat "$user_tmp" 2>/dev/null
    cat "$orgs_tmp" 2>/dev/null
  } > "$tmp"
  rm -f "$user_tmp" "$orgs_tmp"
  mv -f "$tmp" "$cache_file" 2>/dev/null
  rmdir "$lock_dir" 2>/dev/null || true
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

# Bash の文字列演算が byte 単位になる C/POSIX locale でも UTF-8 path を壊さないよう、
# 幅計算にだけ使える UTF-8 locale を一度検出する。Linux と macOS で一般的な候補を
# Bash 自身が `日` を1文字と数えるかで確認し、process 全体の locale は変更しない。
# BSD locale は `locale charmap` keyword を提供しないため、外部 command の出力には依存しない。
_statusline_locale_counts_utf8() {
  local LC_ALL='' LC_CTYPE="$1" probe='日'
  [ "${#probe}" -eq 1 ]
}

_STATUSLINE_UTF8_LOCALE=""
for _statusline_locale_candidate in \
  "${LC_CTYPE:-}" "${LANG:-}" "C.UTF-8" "en_US.UTF-8" "UTF-8"; do
  [ -n "$_statusline_locale_candidate" ] || continue
  if _statusline_locale_counts_utf8 "$_statusline_locale_candidate" 2>/dev/null; then
    _STATUSLINE_UTF8_LOCALE="$_statusline_locale_candidate"
    break
  fi
done
unset _statusline_locale_candidate
unset -f _statusline_locale_counts_utf8
# macOS 標準の Bash 3.2 は process 起動時の locale では multibyte 文字数を返しても、
# function 内で LC_CTYPE を切り替えた後の substring が byte 単位になる。version 3 系は
# locale probe の結果にかかわらず Python helper へ切り替える。
if [ "${BASH_VERSINFO[0]}" -lt 4 ] || [ "${_STATUSLINE_FORCE_PYTHON_WIDTH:-0}" = "1" ]; then
  _STATUSLINE_UTF8_LOCALE=""
fi

# 1 code point が占める terminal cell 幅を `_STATUSLINE_CELL_WIDTH` に設定する。
# Unicode East Asian Width の Wide / Fullwidth、主要 emoji は 2、結合文字・variation
# selector・ZWJ は 0、それ以外は 1。外部 wcwidth/Python process を文字ごとに起動せず、
# macOS 標準 Bash 3.2 と Linux の UTF-8 locale で同じ判定を使う。
_statusline_set_cell_width() {
  local char="$1" codepoint
  _STATUSLINE_CELL_WIDTH=1
  [ -n "$char" ] || { _STATUSLINE_CELL_WIDTH=0; return; }
  printf -v codepoint '%d' "'$char" 2>/dev/null || return

  # C0/C1 control、結合文字、format selector は cell を進めない。
  if (( codepoint < 32 \
    || (codepoint >= 127 && codepoint < 160) \
    || (codepoint >= 0x0300 && codepoint <= 0x036f) \
    || (codepoint >= 0x1ab0 && codepoint <= 0x1aff) \
    || (codepoint >= 0x1dc0 && codepoint <= 0x1dff) \
    || codepoint == 0x200d \
    || (codepoint >= 0x20d0 && codepoint <= 0x20ff) \
    || (codepoint >= 0xfe00 && codepoint <= 0xfe0f) \
    || (codepoint >= 0xfe20 && codepoint <= 0xfe2f) \
    || (codepoint >= 0x1f3fb && codepoint <= 0x1f3ff) \
    || (codepoint >= 0xe0020 && codepoint <= 0xe007f) \
    || (codepoint >= 0xe0100 && codepoint <= 0xe01ef) )); then
    _STATUSLINE_CELL_WIDTH=0
    return
  fi

  # Unicode の主要 Wide / Fullwidth range。East Asian Ambiguous は多くの modern
  # terminal の既定に合わせて 1 のまま扱う。
  if (( (codepoint >= 0x1100 && codepoint <= 0x115f) \
    || codepoint == 0x2329 || codepoint == 0x232a \
    || (codepoint >= 0x2e80 && codepoint <= 0xa4cf && codepoint != 0x303f) \
    || (codepoint >= 0xac00 && codepoint <= 0xd7a3) \
    || (codepoint >= 0xf900 && codepoint <= 0xfaff) \
    || (codepoint >= 0xfe10 && codepoint <= 0xfe19) \
    || (codepoint >= 0xfe30 && codepoint <= 0xfe6f) \
    || (codepoint >= 0xff01 && codepoint <= 0xff60) \
    || (codepoint >= 0xffe0 && codepoint <= 0xffe6) \
    || (codepoint >= 0x1f300 && codepoint <= 0x1faff) \
    || (codepoint >= 0x20000 && codepoint <= 0x3fffd) )); then
    _STATUSLINE_CELL_WIDTH=2
  fi
}

# ANSI SGR escape を除いた terminal cell 幅を返す。
visible_length() {
  local LC_ALL="" LC_CTYPE="${_STATUSLINE_UTF8_LOCALE:-${LC_CTYPE:-}}"
  local s="$1" len=${#1} i=0 in_esc=0 c total=0
  if [ -z "$_STATUSLINE_UTF8_LOCALE" ] && command -v python3 >/dev/null 2>&1; then
    printf '%s' "$s" | python3 "$_STATUSLINE_WIDTH_HELPER" width
    return
  fi
  while [ "$i" -lt "$len" ]; do
    c="${s:$i:1}"
    if [ "$in_esc" -eq 1 ]; then
      [ "$c" = "m" ] && in_esc=0
    elif [ "$c" = $'\033' ]; then
      in_esc=1
    else
      _statusline_set_cell_width "$c"
      total=$((total + _STATUSLINE_CELL_WIDTH))
    fi
    i=$((i + 1))
  done
  printf '%s' "$total"
}

# 文字列をANSIエスケープを保持したまま可視幅 max で切り詰める
# 末尾に色漏れ防止のリセットコードを必ず付与する
truncate_visible() {
  local LC_ALL="" LC_CTYPE="${_STATUSLINE_UTF8_LOCALE:-${LC_CTYPE:-}}"
  local s="$1" max="$2"
  local len=${#s}
  local i=0 visible=0 in_esc=0 c result="" next_visible
  if [ -z "$_STATUSLINE_UTF8_LOCALE" ] && command -v python3 >/dev/null 2>&1; then
    printf '%s' "$s" | python3 "$_STATUSLINE_WIDTH_HELPER" truncate "$max"
    return
  fi

  while [ "$i" -lt "$len" ]; do
    c="${s:$i:1}"
    if [ "$in_esc" -eq 1 ]; then
      result+="$c"
      [ "$c" = "m" ] && in_esc=0
    elif [ "$c" = $'\033' ]; then
      result+="$c"
      in_esc=1
    else
      _statusline_set_cell_width "$c"
      next_visible=$((visible + _STATUSLINE_CELL_WIDTH))
      if [ "$_STATUSLINE_CELL_WIDTH" -gt 0 ] && [ "$next_visible" -gt "$max" ]; then
        break
      fi
      result+="$c"
      visible=$next_visible
    fi
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
