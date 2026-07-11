#!/bin/bash
# detect-context-threshold.sh
# PostToolUse (matcher "*") で毎ツール実行後に発火し、context 使用率が閾値を超えた
# ことを検知したら、handoff ドキュメント作成を指示する additionalContext を
# 1 セッション 1 回だけ注入する (issue #228)。
#
# 参照する cache は natsuume-statusline plugin (#227) の producer が書き出す
# ${TMPDIR:-/tmp}/natsuume-context-cache-<uid>/<sanitized_session_id>.json であり、
# 本 plugin 自身は cache への書き込みを行わない。producer が未構成 (statusline 未設定)
# のセッションでは cache が存在せず、本 hook は何もしない (/session-handoff:setup を
# 案内する導線は skill 側の責務)。
#
# 判定順序 (途中の失敗はすべて無音 exit 0。fail-open でセッションを壊さない):
#   1. jq 不在
#   2. agent_id が非空 (subagent 内での実行)
#   3. session_id 欠落 / サニタイズ後空
#   4. uid 取得不能
#   5. marker (1 セッション 1 回の通知済みガード) 存在
#   6. git 管理下でない (cwd から絶対 git-dir が解決できない)
#   7. context cache 不在 / 破損 / used_percentage 非数値
#   8. SESSION_HANDOFF_THRESHOLD 不正値は 60 に fallback
#   9. used_percentage が閾値未満
#   10. handoff ディレクトリの準備 (作成・symlink 拒否・所有確認・書き込み可能性の実地 probe)
#   11. 保存パスの組み立てとテンプレート (__HANDOFF_PATH__ 差し込み) の展開
#   12. marker の atomic claim (非再帰 mkdir) に成功して初めて additionalContext を出力する
#
# marker はディレクトリであり、「通知を発行済み」を意味する (handoff が実際に保存された
# ことまでは保証しない)。Claude 側の Write 失敗までは再通知しない。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

{ read -r HOOK_EVENT; read -r RAW_SESSION_ID; read -r AGENT_ID; read -r CWD; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.hook_event_name // ""),
    (.session_id // ""),
    (.agent_id // ""),
    (.cwd // "")
  ' 2>/dev/null
)

# 2. subagent 内の PostToolUse では検知しない (agent_id が付与されるのは subagent 実行時)
if [ -n "$AGENT_ID" ]; then
  exit 0
fi

# 3. session_id を marker/保存パスに使うため英数・./_/- のみに sanitize する
SANITIZED_SESSION_ID=$(printf '%s' "$RAW_SESSION_ID" | tr -cd 'A-Za-z0-9._-')
if [ -z "$SANITIZED_SESSION_ID" ]; then
  exit 0
fi

# 4. uid が取得できない環境では per-user 分離パスを組み立てられないため何もしない
uid=$(id -u 2>/dev/null)
if ! [[ "$uid" =~ ^[0-9]+$ ]]; then
  exit 0
fi

STATE_ROOT="${TMPDIR:-/tmp}/session-handoff-$uid"
MARKERS_DIR="$STATE_ROOT/markers"
MARKER_PATH="$MARKERS_DIR/$SANITIZED_SESSION_ID.notified"

# 5. すでに通知済みなら何もしない (このチェックは早期リターンの最適化であり、
# 実際の排他制御は手順 12 の非再帰 mkdir atomic claim が担う)。
if [ -e "$MARKER_PATH" ]; then
  exit 0
fi

# 6. 非 git プロジェクトでは検知・注入とも対象外。--absolute-git-dir は常に絶対パスを
# 返すため (--git-dir はリポジトリルートで相対パス ".git" を返し得る)、保存パスの
# 案内や inject 側の走査と矛盾しない絶対パスをここで確定させる。
GIT_DIR=$(git -C "$CWD" rev-parse --absolute-git-dir 2>/dev/null)
if [ -z "$GIT_DIR" ]; then
  exit 0
fi

# 7. context cache (producer #227 が書き出す) を読む。不在・破損・
# used_percentage が数値でない場合は producer 未構成/未更新とみなし何もしない。
CACHE_FILE="${TMPDIR:-/tmp}/natsuume-context-cache-$uid/$SANITIZED_SESSION_ID.json"
if [ ! -e "$CACHE_FILE" ]; then
  exit 0
fi
USED_PERCENTAGE=$(jq -r '.used_percentage // empty' "$CACHE_FILE" 2>/dev/null)
if ! [[ "$USED_PERCENTAGE" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
  exit 0
fi

# 8. 閾値は 1-99 の整数のみ受け付ける。不正値・未設定は 60 に fallback する。
THRESHOLD="${SESSION_HANDOFF_THRESHOLD:-}"
if ! [[ "$THRESHOLD" =~ ^[0-9]+$ ]] || [ "$THRESHOLD" -lt 1 ] || [ "$THRESHOLD" -gt 99 ]; then
  THRESHOLD=60
fi

# 9. 閾値未満なら何もしない。used_percentage は小数を取り得るため awk で比較する。
if ! awk -v used="$USED_PERCENTAGE" -v threshold="$THRESHOLD" 'BEGIN { exit !(used >= threshold) }'; then
  exit 0
fi

# 10. handoff ディレクトリの準備。#227 producer の cache dir と同一の防御シーケンス
# (umask 077 → mkdir -p → symlink 拒否 → ディレクトリ確認 → 所有確認 → chmod 700) に加え、
# [ -w ] は NFS 等で偽陽性になり得るため、一時ファイルの作成→削除 probe で
# 書き込み可能性を実地に確認する。いずれかの失敗は marker を作らず無音終了する
# (次のツール実行で再検知される)。
HANDOFF_DIR="$GIT_DIR/session-handoff"
if ! (
  umask 077
  mkdir -p "$HANDOFF_DIR" 2>/dev/null || exit 1
  [ -L "$HANDOFF_DIR" ] && exit 1
  [ -d "$HANDOFF_DIR" ] || exit 1
  [ -O "$HANDOFF_DIR" ] || exit 1
  chmod 700 "$HANDOFF_DIR" 2>/dev/null || exit 1
  probe=$(mktemp "$HANDOFF_DIR/.probe.XXXXXX" 2>/dev/null) || exit 1
  rm -f "$probe" 2>/dev/null || exit 1
) 2>/dev/null; then
  exit 0
fi

# 11. 保存パスを組み立て、テンプレートの __HANDOFF_PATH__ を差し込む。
# bash 5.2 の patsub_replacement (置換文字列中の & 展開) を避けるため ${var//} は使わず、
# プレースホルダが「ちょうど 1 個」であることを先に検証したうえで %%/# による
# 分割・連結でリテラルに差し込む。プレースホルダが不在・複数の場合 (テンプレート破損)
# は marker を作らず無音終了する。
PENDING_PATH="$HANDOFF_DIR/pending-$SANITIZED_SESSION_ID-$(date +%s).md"

PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)
TEMPLATE=$(cat "$PROMPTS_DIR/handoff-instruction.md" 2>/dev/null)
if [ -z "$TEMPLATE" ]; then
  exit 0
fi

PLACEHOLDER_COUNT=$(printf '%s' "$TEMPLATE" | grep -o '__HANDOFF_PATH__' | wc -l | tr -d '[:space:]')
if [ "$PLACEHOLDER_COUNT" != "1" ]; then
  exit 0
fi

PREFIX=${TEMPLATE%%__HANDOFF_PATH__*}
SUFFIX=${TEMPLATE#*__HANDOFF_PATH__}
BODY="$PREFIX$PENDING_PATH$SUFFIX"

CONTEXT_JSON=$(jq -n --arg evt "$HOOK_EVENT" --arg ctx "$BODY" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}' 2>/dev/null)
if [ -z "$CONTEXT_JSON" ]; then
  exit 0
fi

# 12. ここまで準備がすべて成功して初めて marker を claim する。状態ルート/markers/ 親は
# 防御シーケンス込みの mkdir -p で作成・検証し、leaf の marker だけを非再帰 mkdir で
# atomic に claim する (mkdir は POSIX で atomic。並列 PostToolUse が同時に手順 5 で
# marker 不在を観測しても、ここで成功できるのは 1 プロセスだけ)。
if ! (
  umask 077
  mkdir -p "$MARKERS_DIR" 2>/dev/null || exit 1
  [ -L "$STATE_ROOT" ] && exit 1
  [ -d "$STATE_ROOT" ] || exit 1
  [ -O "$STATE_ROOT" ] || exit 1
  chmod 700 "$STATE_ROOT" 2>/dev/null || exit 1
  [ -L "$MARKERS_DIR" ] && exit 1
  [ -d "$MARKERS_DIR" ] || exit 1
  [ -O "$MARKERS_DIR" ] || exit 1
  chmod 700 "$MARKERS_DIR" 2>/dev/null || exit 1
) 2>/dev/null; then
  exit 0
fi

mkdir "$MARKER_PATH" 2>/dev/null || exit 0

printf '%s\n' "$CONTEXT_JSON"
