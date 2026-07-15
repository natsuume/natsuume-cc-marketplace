#!/bin/bash
# resolve-model-on-prompt.sh
# UserPromptSubmit で発火する one-shot 補正 (#175、issue #236 で分業規律ブロックを分離)。
# SessionStart 時点でモデル判定不能だった session (inject-always.sh が判定不能分岐で作成した
# pending マーカーが残っている session) に対し、会話が進んで transcript に main-chain
# assistant 行が現れた最初のタイミングでモデルを確定し、常時ルールの確定版を 1 度だけ再送する。
#
# ## issue #236 での変更点
#
# - 再注入ペイロードから分業規律ブロックを外した (prefix + always-fable.md のみ)。分業規律の
#   Fable 補正は inject-discipline.sh の `sonnet-gate` → `final` マーカー遷移が別要素として担う
#   (設計契約 §4.3/§4.4)。常時ルールと分業規律を 1 つの additionalContext に連結していた旧設計
#   (persisted-output 劣化の原因) を解消するための分離
# - state file (`model-<session_id>`) の書込を同一ディレクトリ内 temp file → mv の atomic 書込
#   にし、書込の成否を確認する。書込に失敗した場合は pending マーカーを削除せず、注入も行わず
#   無音 exit する (次回 UserPromptSubmit で再試行。stale state を残したまま pending を消すと
#   後続スクリプト (inject-rules-part.sh / inject-discipline.sh) が誤った変種を確定配送する
#   ため、pending 優先・state 次点という優先規則 (設計契約 §5) を壊さないための必須条件)
# - prefix の文言を「常時適用ルールの確定版」への言及に更新し、分業規律の補正が別要素で届く
#   ことに触れる
#
# ## 自己修復指示 (issue #235 で追加)
#
# 再注入ペイロードの最先頭 (one-shot 補正 prefix より前) に、persisted-output 退避時の
# 自己修復指示 1 段落を必ず置く (Claude Code は additionalContext 1 要素が inline 閾値
# 約 9〜10K 文字を超えると本文をファイルへ退避し、スタブ + 先頭 2KB プレビューのみを context
# に載せるため、プレビューに必ず入る先頭へ置く)。文言は本スクリプト内の SELF_HEAL 定数が
# 保持し、inject-always.sh の SELF_HEAL 定数と byte-identical に保つ (スクリプト間の二重管理。
# 変更時は必ず両スクリプトを同時に更新すること)。本スクリプトのペイロードは単一構成
# (≈5.8K 字) のため 8K ガードは持たないが、指示は無条件で先頭に付す。
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
# - pending マーカーあり + assistant 行あり → モデルを確定し、state file への atomic 書込を試みる:
#   - 書込成功:
#     - pending マーカーを削除する
#     - モデル ID (小文字化) が `fable` を含む → 確定版 (prefix + hooks/prompts/always-fable.md)
#       を 1 度だけ additionalContext で注入する。always-fable.md が読めない/空の場合は
#       再注入自体を行わず exit 0 (state 書込・pending 削除は既に完了している)
#     - それ以外 (sonnet / opus / haiku 等) → 再注入しない (出力なしで exit 0。自己ゲート時に
#       inject-always.sh が注入済みの always-sonnet-1.md および inject-rules-part.sh が配送する
#       part 2/3 が非 Fable セッション向けの確定内容そのものであるため)
#   - 書込失敗: pending マーカーを削除せず、注入も行わず無音 exit する
#
# ## 出力 JSON 形状 (再注入する場合のみ)
#
#   {
#     "hookSpecificOutput": {
#       "hookEventName": "<入力の hook_event_name をそのまま echo>",
#       "additionalContext": "<自己修復指示 (SELF_HEAL) + prefix + always-fable.md 本文>"
#     }
#   }
#
# 再注入しない分岐 (pending マーカーなし / assistant 行なし / state 書込失敗 / 確定版が
# always-sonnet-1.md) では何も出力せず exit 0 する。
#
# ## fail-open 条件
#
# - jq 不在
# - stdin が不正 JSON / hook_event_name / session_id が空
# - transcript_path が読めない
# - state file の atomic 書込 (temp 書込・mv のいずれか) 失敗 → pending 残置 + 無音 exit
# - always-fable.md が読めない (空文字列を含む) → 再注入自体を行わず exit 0

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

# state 書込が確認できた後にのみ pending マーカーを削除する (TOCTOU の隙間を作らない、
# #155 の教訓。書込失敗時は本行に到達せず pending を残置する、上記コメント参照)。
rm -f "$PENDING_FILE" 2>/dev/null

# 確定版が always-sonnet-1.md の場合 (sonnet を含む、または非空でそのいずれでもない場合)、
# 自己ゲート時に inject-always.sh / inject-rules-part.sh が注入済みと同内容のため再注入しない。
if ! printf '%s' "$MODEL" | grep -qi 'fable'; then
  exit 0
fi

PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)
BODY=$(cat "$PROMPTS_DIR/always-fable.md" 2>/dev/null)
if [ -z "$BODY" ]; then
  exit 0
fi

# 自己修復指示 (issue #235、ヘッダ「自己修復指示」節)。ペイロード最先頭に必ず置く。
# 文言は inject-always.sh の SELF_HEAL と byte-identical に保つこと (二重管理)。
SELF_HEAL='(自己修復) このメッセージが persisted-output として退避されている場合は、スタブに記載されたパスの退避ファイルを Read で全文読了してから作業を開始すること。'

PREFIX="(one-shot 補正) セッション開始時点ではモデルを判定できず、自己ゲート付きで SONNET 向けの常時適用ルール (part 1/3 以降) を暫定注入していた。会話の進行によりこのセッションのモデルが Fable であると確定したため、以後は本メッセージ以下の常時適用ルール確定版を優先し、セッション冒頭の自己ゲート付き注入は破棄すること。分業規律の Fable 版補正は別要素 (inject-discipline.sh) で届く。"

CONTEXT="$SELF_HEAL

$PREFIX

$BODY"

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'
