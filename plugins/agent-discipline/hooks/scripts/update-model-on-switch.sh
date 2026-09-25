#!/bin/bash
# update-model-on-switch.sh
# PostModelSwitch hook。`/model` によるセッション途中のモデル切替を session model state に
# 反映し、state を読む分業規律の配送 (inject-discipline.sh) を切替後のモデルに追随させる。
#
# I/O 契約:
#   stdin  : PostModelSwitch hook input JSON
#            {hook_event_name, session_id, from_model, to_model} のうち from_model は読まない
#            (通知の要否と案内する版は切替後のモデルと pending の有無だけで決まるため)
#   stdout : 通知する場合のみ
#            {"hookSpecificOutput": {"hookEventName": "PostModelSwitch", "additionalContext": "..."}}
#   env    : TMPDIR (state の置き場)、CLAUDE_PLUGIN_ROOT (prompts ディレクトリの解決)
#   exit   : 常に 0
#
# 動作:
#   - hook_event_name が PostModelSwitch 以外、session_id が空 (sanitize 後に空になる場合を
#     含む)、to_model が空または欠落 のときは、state も pending も触らず無音で exit 0 する
#   - `${TMPDIR:-/tmp}/agent-discipline-state/model-<session_id>` に to_model を書く。書込は
#     同一ディレクトリ内の temp file → mv による atomic 上書きで行い、成否を確認する
#     (session_id の sanitize は inject-always.sh と同じ `tr -cd 'A-Za-z0-9._-'`)
#   - state の書込 (temp 書込と mv の両方) に成功した場合にのみ、pending マーカー
#     `${TMPDIR:-/tmp}/agent-discipline-state/pending-model-<session_id>` を削除する。書込に
#     失敗した場合は pending を残し、通知も出さずに無音で exit 0 する (pending 優先・state 次点
#     という優先規則を守るため。state 無し + pending 無しの状態を作らない)
#   - 通知は pending マーカーが存在したときにだけ出す。pending が存在した session は
#     判定不能のまま自己ゲート付きの暫定版を配送されているため、切替後のモデルに対応する
#     確定版の所在を案内して自己修復させる
#   - 通知本文には prompts ディレクトリの絶対パスと、切替後のモデルに対応する常時適用ルール /
#     分業規律のファイル名を書く。版の対応は inject-always.sh / inject-discipline.sh と同じで、
#     常時適用ルールはモデルに依らず always-sonnet-{1,2,3}.md、分業規律は opus 系が
#     discipline-opus.md、それ以外 (fable を含む) が discipline-sonnet.md
#
# 制約:
#   - Linux (WSL2) / macOS の両方で動作すること
#   - jq 不在時は何もせず exit 0 (他の hook と同じ fail-open 方針)

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# 各フィールドは 1 行 1 値で読むため、値に含まれる改行 (CR / LF) は空白に置き換えて欄ずれを防ぐ。
{ read -r HOOK_EVENT; read -r SESSION_ID; read -r TO_MODEL; } < <(
  printf '%s' "$INPUT" | jq -r '
    [ (.hook_event_name // ""),
      (.session_id // ""),
      (.to_model // "") ]
    | map(tostring | gsub("[\r\n]"; " "))
    | .[]
  ' 2>/dev/null
)

if [ "$HOOK_EVENT" != "PostModelSwitch" ]; then
  exit 0
fi

trim() {
  printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

TO_MODEL=$(trim "$TO_MODEL")

# 切替後のモデルが分からなければ state を更新できない (旧 state と pending をそのまま残す)。
if [ -z "$TO_MODEL" ]; then
  exit 0
fi

SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9._-')
if [ -z "$SAFE_SESSION_ID" ]; then
  exit 0
fi

STATE_DIR="${TMPDIR:-/tmp}/agent-discipline-state"
STATE_FILE="$STATE_DIR/model-$SAFE_SESSION_ID"
PENDING_FILE="$STATE_DIR/pending-model-$SAFE_SESSION_ID"

PENDING_EXISTED=0
if [ -e "$PENDING_FILE" ]; then
  PENDING_EXISTED=1
fi

# 通知が必要なのは、pending マーカーを消す (= 判定不能の暫定配送を切替後のモデルで確定させる)
# 場合に限る。それ以外は配送済みの版をそのまま使う。
NEED_NOTICE="$PENDING_EXISTED"

# 通知本文は state を書く前に組み立てる。pending の削除は通知と対で行う必要があり、
# 通知を作れない (prompts ディレクトリを解決できない / JSON を組めない) 場合は state も
# pending も触らずに終了する (pending が残れば resolve-model-on-prompt.sh がモデルを確定し、
# inject-discipline.sh の one-shot 補正が分業規律の確定版を配送する)。
NOTICE=""
if [ "$NEED_NOTICE" -eq 1 ]; then
  if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    PROMPTS_DIR="$CLAUDE_PLUGIN_ROOT/hooks/prompts"
  else
    PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)
  fi
  if [ -z "$PROMPTS_DIR" ]; then
    exit 0
  fi

  ALWAYS_FILES="always-sonnet-1.md / always-sonnet-2.md / always-sonnet-3.md"
  if printf '%s' "$TO_MODEL" | grep -qi 'opus'; then
    DISCIPLINE_FILE="discipline-opus.md"
  else
    DISCIPLINE_FILE="discipline-sonnet.md"
  fi

  # 通知にはモデル ID をそのままではなく sanitize した形で載せる (hook 入力の文字列を
  # 無検証で本文へ反映しない)。
  SAFE_TO_MODEL=$(printf '%s' "$TO_MODEL" | tr -cd 'A-Za-z0-9._-')

  CONTEXT="(モデル切替) このセッションのモデルが ${SAFE_TO_MODEL} に切り替わりました。常時適用ルールと分業規律の確定版は ${ALWAYS_FILES} と ${DISCIPLINE_FILE} です。prompts ディレクトリ ${PROMPTS_DIR} からこれらを Read して自己修復し、以後は確定版を優先して、切替前のモデル向けに配送済みの版は破棄してください。"

  NOTICE=$(jq -n --arg ctx "$CONTEXT" '{
    hookSpecificOutput: {
      hookEventName: "PostModelSwitch",
      additionalContext: $ctx
    }
  }' 2>/dev/null) || exit 0
  if [ -z "$NOTICE" ]; then
    exit 0
  fi
fi

if ! mkdir -p "$STATE_DIR" 2>/dev/null; then
  exit 0
fi

# 2>/dev/null は「>」より前に置く: 出力リダイレクト自体の失敗 (permission denied 等) は
# 後置の 2>/dev/null では抑制できないため、無音 fail-open を保証するには先に stderr を
# /dev/null へ向けてから出力先を開く必要がある。
TMP_FILE="$STATE_FILE.tmp.$$"
if ! printf '%s' "$TO_MODEL" 2>/dev/null > "$TMP_FILE"; then
  rm -f "$TMP_FILE" 2>/dev/null
  exit 0
fi
if ! mv "$TMP_FILE" "$STATE_FILE" 2>/dev/null; then
  rm -f "$TMP_FILE" 2>/dev/null
  exit 0
fi

# state 書込が確認できた後にのみ pending マーカーを削除する (書込失敗時は本行に到達しない)。
# 通知は既に組み立ててあるため、pending の削除と通知の出力は対で行われる。
if [ "$PENDING_EXISTED" -eq 1 ]; then
  rm -f "$PENDING_FILE" 2>/dev/null
fi

if [ "$NEED_NOTICE" -eq 1 ]; then
  printf '%s\n' "$NOTICE"
fi
