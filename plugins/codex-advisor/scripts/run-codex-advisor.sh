#!/bin/bash
# run-codex-advisor.sh
# codex-advisor plugin の **Codex advisor 相談 (consultation) 実行 wrapper** (issue #219)。
# `/codex-advisor:consult` skill が Bash tool 経由で本 wrapper を foreground 起動し、
# 相談プロンプトを stdin から渡す。 Codex は read-only sandbox でリポジトリを自分で
# 読んで裏取りしたうえで、 plan / course-correction の助言テキストを返す。 実行 (ファイル
# 変更・コマンド実行) は一切行わない。
#
# ## I/O 契約
#
# - 標準入力 (stdin): Claude Code では相談プロンプト全文をファイルからの stdin
#   リダイレクト (`< "/path/to/prompt.md"`) で渡す。Codex では
#   `--codex-session-stdin` を付けて専用 PTY session を開始し、tool の stdin channel から
#   本文と 2-byte EOT terminator (`0x04 0x04`) を送る。wrapper は PTY を raw/noncanonical mode
#   にして EOT pair を明示 frame delimiter として読むため、MAX_CANON による長い行の欠落や
#   CR 変換を避ける。
#   どちらもプロンプトを argv に載せない
#   (プロンプトを argv に乗せない設計。 shell quoting 事故や history 露出を避けるため
#   stdin 経由に統一する)
# - 標準出力 (stdout): Codex companion または direct `codex exec` の stdout (= 助言テキスト) をそのまま流す。 wrapper
#   はこれを一切加工しない (呼び出し側が助言を verbatim で受け取れることが contract)
# - 標準エラー出力 (stderr): wrapper 自身の進捗 / エラーメッセージ (companion path・
#   実行コマンド・完了通知・失敗理由)。 stdout (助言) と stderr (wrapper 状態) を分離する
#   ことで、 呼び出し側が両者を混同しない設計は run-codex-review.sh と同一
# - 終了コード: companion が exit 0 で完了したら 0。 それ以外 (Node.js 不在 / stdin 未指定 /
#   空プロンプト / companion 未検出 / companion 失敗) は `fail()` 経由で人間可読メッセージを
#   stderr に出して 1
#
# ## 制約
#
# Linux (WSL2) / macOS (bash 3.2 / BSD ツール) の両方で動作すること。 bash 4+ 拡張
# (`${var//pattern/}` の複雑な parameter expansion、 連想配列等) や GNU 専用オプション
# (`sort -V` 等) は使わない。
#
# ## 設計判断
#
# - **reasoning effort は `xhigh` 固定**: advisor 用途は「戦略的な岐路での深い助言」が
#   目的であり、 浅い effort では pre-push-review の codex review (bug 検出) と差別化
#   できない。 issue #219 でユーザが xhigh 固定を確定させたため、 上書きフラグは設けない
#   (呼び出し側からの effort 引き下げは「相談の質を落とす」判断そのものであり、 wrapper が
#   軽々に許可する余地ではない)
# - **`--write` は付けない (read-only sandbox 固定)**: advisor は Claude の判断品質を
#   上げるための助言役であり、 実行主体ではない (advisor-rules.md の rule:advisor-boundary)。
#   companion に書き込み権限を与えると executor/advisor の役割分離が崩れるため、 sandbox は
#   常に read-only に固定する
# - **model は未指定**: issue #219 でユーザが「Codex 側の既定に委ねる」ことを確定済み。
#   model を固定すると Codex 側のデフォルト更新に追従できず、 陳腐化した model 指定が
#   silent に残るリスクがある
# - **git 状態を検査しない**: run-codex-review.sh (pre-push-review) は dirty tree / branch /
#   diff hash を検査するが、 それは「committed 差分をレビューする」ため。 本 wrapper の
#   相談は git 状態に依存しない (質問に git diff が絡むかどうかは呼び出し側がプロンプトに
#   含める判断であり、 wrapper 側で強制しない)。direct Codex path は
#   `--skip-git-repo-check` を固定し、git repository の外でも動作する
# - **marker ファイルを書かない**: pre-push-review の marker はレビュー gate の再実行防止
#   機構だが、 advisor 相談は gate ではなく都度の助言取得であり、 marker という永続状態を
#   持つ必要がない

set -e

_RUN_CODEX_ADVISOR_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/codex-companion-resolver.sh
source "$_RUN_CODEX_ADVISOR_SCRIPT_DIR/lib/codex-companion-resolver.sh"

# stderr に人間可読のエラーを出して非ゼロ exit する helper。 set -e と組み合わせて使う。
fail() {
  printf '%s\n' "[run-codex-advisor] $1" >&2
  exit 1
}

# usage を stderr に表示する helper (stdin 未指定 / 空プロンプトの両方から呼ぶため共通化)。
usage() {
  printf '%s\n' "[run-codex-advisor] 使い方: Claude Code は prompt file を stdin redirect、Codex は --codex-session-stdin を付けた PTY session の stdin で渡してください。" >&2
  printf '%s\n' "[run-codex-advisor] 例: bash run-codex-advisor.sh < \"/path/to/prompt.md\"" >&2
  printf '%s\n' "[run-codex-advisor] Codex: bash run-codex-advisor.sh --codex-session-stdin" >&2
}

# Codex の unified exec は初回 command と後続 stdin を分離できる。その明示 mode だけを
# 例外として受け付け、相談本文を argv や shell command に載せずに渡す。
CODEX_SESSION_STDIN=0
if [ "$#" -eq 1 ] && [ "$1" = "--codex-session-stdin" ]; then
  CODEX_SESSION_STDIN=1
elif [ "$#" -ne 0 ]; then
  usage
  fail "未知の引数です。相談プロンプト自体を引数にせず stdin で渡してください。"
fi

# 通常 mode の対話起動と、Codex mode の非 PTY 起動はいずれも誤用として拒否する。
if [ "$CODEX_SESSION_STDIN" -eq 0 ] && [ -t 0 ]; then
  usage
  fail "相談プロンプトが stdin から渡されていません (TTY 検出)。"
fi
if [ "$CODEX_SESSION_STDIN" -eq 1 ] && [ ! -t 0 ]; then
  usage
  fail "--codex-session-stdin には PTY の unified exec session が必要です。"
fi

# PTY では raw mode にして echo と canonical input processing を一時的に無効化する。canonical mode の
# MAX_CANON は長い単一行を欠落させ、ICRNL 等は入力 byte を変換し得るため、2-byte EOT
# terminator (`0x04 0x04`) を wrapper 自身が frame delimiter として読む。Bash の `read`
# builtin は TTY 入力中に termios を再設定して CR を LF に変換するため使わず、POSIX awk を
# LC_ALL=C で起動する。sentinel を末尾に付けて command substitution の trailing-newline
# 除去を防ぎ、受信直後、nested Codex 起動前に必ず terminal を元へ戻す。
ORIGINAL_STTY=""
restore_terminal() {
  if [ -n "$ORIGINAL_STTY" ]; then
    stty "$ORIGINAL_STTY" >/dev/null 2>&1 || true
    ORIGINAL_STTY=""
  fi
}
if [ "$CODEX_SESSION_STDIN" -eq 1 ]; then
  command -v awk >/dev/null 2>&1 \
    || fail "Codex PTY frame の受信に必要な POSIX awk が見つかりません。"
  ORIGINAL_STTY=$(stty -g) || fail "PTY 設定を取得できませんでした。"
  trap 'restore_terminal' EXIT
  trap 'restore_terminal; exit 130' HUP INT TERM
  stty raw -echo min 1 time 0 \
    || fail "PTY の raw/noncanonical input mode を設定できませんでした。"
  printf '%s\n' '[run-codex-advisor] ready for Codex session stdin; send prompt, then EOT EOT (0x04 0x04).' >&2
  FRAME_SENTINEL='__CODEX_ADVISOR_FRAME_END_5f9d3c2a__'
  if ! FRAMED_PROMPT=$(
    LC_ALL=C awk -v sentinel="$FRAME_SENTINEL" '
      BEGIN {
        RS = sprintf("%c", 4)
        ORS = ""
      }
      NR == 1 {
        prompt = $0
        if ((getline trailer) != 1 || trailer != "") {
          exit 42
        }
        printf "%s%s", prompt, sentinel
        exit 0
      }
    '
  ); then
    restore_terminal
    trap - EXIT HUP INT TERM
    fail "Codex session stdin が完全な EOT EOT frame terminator より前に閉じたか、prompt に予約済み EOT byte が含まれています。"
  fi
  case "$FRAMED_PROMPT" in
    *"$FRAME_SENTINEL") PROMPT=${FRAMED_PROMPT%"$FRAME_SENTINEL"} ;;
    *)
      restore_terminal
      trap - EXIT HUP INT TERM
      fail "Codex session stdin frame の検証に失敗しました。"
      ;;
  esac
  restore_terminal
  trap - EXIT HUP INT TERM
else
  # Claude Code の既存 file redirect 経路は従来どおり EOF まで読む。command substitution が
  # 末尾 newline を除く挙動も既存 contract のまま維持する。
  PROMPT=$(cat)
fi

# 空 / 空白のみの判定は bash 3.2 互換で行う (`${var//[[:space:]]/}` は bash 4 拡張のため
# 使わない)。 `tr -d '[:space:]'` で空白類を全て除去した結果が空文字かどうかで判定する。
if [ -z "$(printf '%s' "$PROMPT" | tr -d '[:space:]')" ]; then
  usage
  fail "相談プロンプトが空です。"
fi

# Claude Code では公式 openai-codex companion を優先する。Codex plugin からの実行時や
# companion 未導入環境では、installed `codex` CLI の独立 read-only process に fallback
# する。direct process では hooks を無効化し、本 plugin の SessionStart hook が再帰的に
# advisor process へ注入される経路を塞ぐ。
# Codex host は current CLI の認証/providerを維持するため、Claude plugin cache に companion
# が偶然存在しても direct process を使う。Claude Code host だけ companion を優先する。
COMPANION=""
if [ "$CODEX_SESSION_STDIN" -eq 0 ]; then
  COMPANION=$(resolve_codex_companion) || COMPANION=""
fi

# Codex host の unified exec には Bash tool の timeout parameter が無いため、wrapper 自身が
# nested process group を監視する。process は stdout/stderr を同じ foreground PTY に継承するので
# 結果は常に観察され、期限超過時は group 全体を TERM -> grace -> KILL、最後に leader を
# wait して回収する。Codex が起動した descendant だけが pipe FD を保持して session を止める
# 経路も、同じ process group への signal で閉じる。
ADVISOR_TIMEOUT_SECONDS="${CODEX_ADVISOR_TIMEOUT_SECONDS:-600}"
ADVISOR_KILL_GRACE_SECONDS="${CODEX_ADVISOR_KILL_GRACE_SECONDS:-5}"
case "$ADVISOR_TIMEOUT_SECONDS" in
  ''|*[!0-9]*) fail "CODEX_ADVISOR_TIMEOUT_SECONDS は正の整数で指定してください。" ;;
esac
case "$ADVISOR_KILL_GRACE_SECONDS" in
  ''|*[!0-9]*) fail "CODEX_ADVISOR_KILL_GRACE_SECONDS は 0 以上の整数で指定してください。" ;;
esac
if [ "$ADVISOR_TIMEOUT_SECONDS" -le 0 ]; then
  fail "CODEX_ADVISOR_TIMEOUT_SECONDS は正の整数で指定してください。"
fi

ADVISOR_PID=""
ADVISOR_PGID=""
ADVISOR_MONITOR_WAS_ENABLED=0

# Bash の monitor mode は、non-interactive shell でも background job を wrapper とは別の
# process group に置く。background subshell を group leader にして、その中で prompt pipe と
# advisor を foreground 実行することで、$! は wait 対象 PID と signal 対象 PGID の双方になる。
begin_advisor_process_group() {
  case $- in
    *m*) ADVISOR_MONITOR_WAS_ENABLED=1 ;;
    *)
      ADVISOR_MONITOR_WAS_ENABLED=0
      set -m
      ;;
  esac
}

finish_advisor_process_group_start() {
  ADVISOR_PID=$1
  ADVISOR_PGID=$1
  if [ "$ADVISOR_MONITOR_WAS_ENABLED" -eq 0 ]; then
    set +m
  fi
}

advisor_group_alive() {
  [ -n "$ADVISOR_PGID" ] \
    && kill -0 -- "-$ADVISOR_PGID" 2>/dev/null
}

signal_advisor_group_term() {
  if advisor_group_alive; then
    kill -TERM -- "-$ADVISOR_PGID" 2>/dev/null || true
  fi
}

signal_advisor_group_kill() {
  if advisor_group_alive; then
    kill -KILL -- "-$ADVISOR_PGID" 2>/dev/null || true
  fi
}

cleanup_advisor_process() {
  signal_advisor_group_term
  signal_advisor_group_kill
  if [ -n "$ADVISOR_PID" ]; then
    wait "$ADVISOR_PID" 2>/dev/null || true
  fi
  ADVISOR_PID=""
  ADVISOR_PGID=""
}

wait_for_advisor() {
  local started_at now deadline grace_deadline status timed_out
  started_at=$(date +%s) || return 1
  deadline=$((started_at + ADVISOR_TIMEOUT_SECONDS))
  timed_out=0

  while kill -0 "$ADVISOR_PID" 2>/dev/null; do
    now=$(date +%s) || {
      cleanup_advisor_process
      return 1
    }
    if [ "$now" -ge "$deadline" ]; then
      timed_out=1
      signal_advisor_group_term
      grace_deadline=$((now + ADVISOR_KILL_GRACE_SECONDS))
      while advisor_group_alive; do
        now=$(date +%s) || break
        if [ "$now" -ge "$grace_deadline" ]; then
          break
        fi
        sleep 1
      done
      signal_advisor_group_kill
      break
    fi
    sleep 1
  done

  if wait "$ADVISOR_PID"; then
    status=0
  else
    status=$?
  fi
  # leader が正常終了して descendant だけを残した場合も、foreground session の pipe を
  # 保持させない。leader の wait 後も group が残っていれば全員を終了させる。
  signal_advisor_group_term
  signal_advisor_group_kill
  ADVISOR_PID=""
  ADVISOR_PGID=""
  if [ "$timed_out" -eq 1 ]; then
    printf '[run-codex-advisor] advisor timed out after %s seconds.\n' \
      "$ADVISOR_TIMEOUT_SECONDS" >&2
    return 124
  fi
  return "$status"
}

trap 'cleanup_advisor_process' EXIT
trap 'cleanup_advisor_process; exit 130' HUP INT TERM

if [ -n "$COMPANION" ] && command -v node >/dev/null 2>&1; then
  printf '[run-codex-advisor] codex companion: %s\n' "$COMPANION" >&2
  printf '[run-codex-advisor] running: node %s task --effort xhigh\n' "$COMPANION" >&2
  begin_advisor_process_group
  (
    printf '%s' "$PROMPT" | node "$COMPANION" task --effort xhigh
  ) &
  finish_advisor_process_group_start "$!"
  if ! wait_for_advisor; then
    fail "codex companion の実行に失敗しました。codex CLI の install / login 状態を確認してください。"
  fi
elif command -v codex >/dev/null 2>&1; then
  printf '%s\n' '[run-codex-advisor] companion unavailable; running direct codex exec (read-only, ephemeral, hooks disabled, xhigh)' >&2
  begin_advisor_process_group
  (
    printf '%s' "$PROMPT" \
      | codex exec --sandbox read-only --ephemeral --disable hooks \
        --skip-git-repo-check --color never -c 'model_reasoning_effort="xhigh"' -
  ) &
  finish_advisor_process_group_start "$!"
  if ! wait_for_advisor; then
    fail "direct codex exec に失敗しました。codex login と設定を確認してください。"
  fi
elif [ -n "$COMPANION" ]; then
  fail "codex companion は見つかりましたが Node.js が無く、direct codex CLI も見つかりません。"
else
  fail "codex companion と codex CLI のどちらも見つかりません。"
fi

trap - EXIT HUP INT TERM

printf '[run-codex-advisor] codex advisor への相談が完了しました。\n' >&2

exit 0
