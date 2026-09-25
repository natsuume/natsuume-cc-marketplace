#!/bin/bash
# resolve-model-on-prompt.sh
# UserPromptSubmit で発火し、SessionStart 時点でモデル判定不能だった session (inject-always.sh
# が判定不能分岐で作成した pending マーカーが残っている session) のモデルを、会話が進んで
# transcript に main-chain assistant 行が現れた最初のタイミングで確定する。確定したモデルを
# session model state に書き、pending マーカーを削除する。本スクリプト自身は何も出力しない。
#
# 確定後の配送は後続スクリプトが担う:
# - 常時適用ルール: 自己ゲート付きで配送済みの always-sonnet-{1,2,3}.md がモデルに依らず
#   確定内容そのものであるため、再送しない
# - 分業規律: inject-discipline.sh が state を読み、`sonnet-gate` → `final` マーカー遷移で
#   one-shot 補正 (Opus 版の対象なら Opus 版を配送) を行う
#
# ## 発火条件
#
# pending マーカー `${TMPDIR:-/tmp}/agent-discipline-state/pending-model-<session_id>`
# (session_id は inject-always.sh と同じ sanitize 方式 `tr -cd 'A-Za-z0-9._-'`) が存在する
# session に限る。マーカーが無ければ即 exit 0。
#
# ## 分岐
#
# - pending マーカーなし → 即 exit 0
# - pending マーカーあり + transcript にまだ main-chain assistant 行が無い → 何もしない
#   (pending マーカーは残したまま exit 0。次回 UserPromptSubmit で再試行)
# - pending マーカーあり + assistant 行あり → モデルを確定し、state file
#   (`model-<session_id>`) への atomic 書込 (同一ディレクトリ内 temp file → mv) を試みる:
#   - 書込成功: pending マーカーを削除して exit 0
#   - 書込失敗: pending マーカーを削除せず無音 exit する (次回 UserPromptSubmit で再試行。
#     stale state を残したまま pending を消すと後続スクリプト (inject-rules-part.sh /
#     inject-discipline.sh) が誤った版を確定配送するため、pending 優先・state 次点という
#     優先規則 (設計契約 §5) を壊さないための必須条件)
#
# ## fail-open 条件
#
# - jq 不在
# - stdin が不正 JSON / hook_event_name / session_id が空
# - transcript_path が読めない
# - state file の atomic 書込 (temp 書込・mv のいずれか) 失敗 → pending 残置 + 無音 exit

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

{ read -r HOOK_EVENT; read -r SESSION_ID; read -r TRANSCRIPT_PATH; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.hook_event_name // ""),
    (.session_id // ""),
    (.transcript_path // "")
  ' 2>/dev/null
)

if [ -z "$HOOK_EVENT" ] || [ -z "$SESSION_ID" ]; then
  exit 0
fi

SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9._-')
if [ -z "$SAFE_SESSION_ID" ]; then
  exit 0
fi

STATE_DIR="${TMPDIR:-/tmp}/agent-discipline-state"
PENDING_FILE="$STATE_DIR/pending-model-$SAFE_SESSION_ID"

# pending マーカーが無ければ即 exit 0 (通常時のオーバーヘッドをこのファイル存在チェック
# 1 回に抑え、transcript 解析はこの後に限定する)。
if [ ! -f "$PENDING_FILE" ]; then
  exit 0
fi

if [ -z "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

MODEL=$(jq -r 'select(.type=="assistant" and .isSidechain != true) | .message.model // empty' "$TRANSCRIPT_PATH" 2>/dev/null | tail -n 1)

# transcript にまだ main-chain assistant 行が無ければ何もしない (pending マーカーは
# 残したまま終了し、次回の UserPromptSubmit で再試行する)。
if [ -z "$MODEL" ]; then
  exit 0
fi

# state file を同一ディレクトリ内 temp file → mv で atomic に書き込み、成否を確認する。
STATE_FILE="$STATE_DIR/model-$SAFE_SESSION_ID"
TMP_FILE="$STATE_FILE.tmp.$$"

if ! mkdir -p "$STATE_DIR" 2>/dev/null; then
  exit 0
fi
# 2>/dev/null は「>」より前に置く: 出力リダイレクト自体の失敗 (permission denied 等) は
# 後置の 2>/dev/null では抑制できない (bash の既知の挙動) ため、無音 fail-open を保証するには
# 先に stderr を /dev/null へ向けてから出力先を開く必要がある。
if ! printf '%s' "$MODEL" 2>/dev/null > "$TMP_FILE"; then
  rm -f "$TMP_FILE" 2>/dev/null
  exit 0
fi
if ! mv "$TMP_FILE" "$STATE_FILE" 2>/dev/null; then
  rm -f "$TMP_FILE" 2>/dev/null
  exit 0
fi

# state 書込が確認できた後にのみ pending マーカーを削除する (TOCTOU の隙間を作らない。
# 書込失敗時は本行に到達せず pending を残置する、上記コメント参照)。
rm -f "$PENDING_FILE" 2>/dev/null

exit 0

