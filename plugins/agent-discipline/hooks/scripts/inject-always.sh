#!/bin/bash
# inject-always.sh
# SessionStart で「permission_mode に依らず常時適用」 すべき agent-discipline ルールの
# part 1/3 (hooks/prompts/always-1.md) を additionalContext として注入する。
# セッションのモデルは判定せず、全モデルへ同じ内容を配送する。
#
# ## 分割配送
#
# 常時ルール全文と分業規律を 1 つの additionalContext に連結すると、Claude Code の inline
# 配送閾値 (約 9〜10K 文字/要素) を超えて persisted-output (2KB プレビューのみ) に劣化する。
# 本スクリプトは SessionStart で part 1 (delivery-note + always-1.md) のみを注入し、
# 残りは UserPromptSubmit の別スクリプトが最初のユーザプロンプト処理時に個別の要素として
# 配送する (本スクリプトの責務外):
#   - always-2.md / always-3.md … inject-rules-part.sh
#   - 分業規律 (discipline.md) … inject-discipline.sh
#
# ## 配送済みマーカーの削除
#
# UserPromptSubmit 側の配送済みマーカー (`${TMPDIR:-/tmp}/agent-discipline-state/` 配下の
# `delivered-rules-2-<session_id>` / `delivered-rules-3-<session_id>` /
# `delivered-discipline-<session_id>`) を、session_id が非空なら SessionStart のたびに
# 無条件で削除する (`startup` 以外に `resume` / `clear` / `compact` でも発火するため、
# 全要素を毎回再配送する)。
#
# ## 自己修復指示
#
# Claude Code は hook の additionalContext 1 要素が inline 閾値 (約 9〜10K 文字、UTF-16
# code unit 基準) を超えると本文をファイルへ退避し、`<persisted-output>` スタブ (退避パス +
# 先頭 2KB プレビュー) のみを context に載せる。分割配送で全要素は 8,000 字以下に収まって
# いるが、閾値変動・ペイロード増加で劣化が起きても「退避ファイルを読み直せば機能する」
# 状態を保つため、additionalContext の最先頭 (delivery-note より前 = プレビュー 2KB に
# 必ず入る位置) に自己修復指示 1 段落を必ず置く:
#
# - 文言はプレビューを圧迫しないよう 200 字以内とし、「persisted-output として退避されている
#   場合は」という条件付き文言にする (inline 配送時に読んでも違和感がないこと)。指示には
#   「退避ファイル (スタブに記載されたパス) を Read で全文読了してから作業を開始する」ことを
#   明記する
# - 文言は本スクリプト内の SELF_HEAL 定数が保持する (md ファイル側には置かずスクリプト側で
#   付与する)
#
# ## delivery-note と 8K ガード
#
# additionalContext は次の順に組み立てる:
#
#   SELF_HEAL + 空行 + delivery-note.md + 改行 + `(参照パス) <prompts dir>` + 空行 + always-1.md
#
# delivery-note.md と always-1.md は、先頭の保守者向け HTML コメントと直後の空行を除いた本文を
# 使う (lib/prompt-body.sh の read_agent_discipline_prompt)。
#
# delivery-note.md は常時ルール・分業規律が複数メッセージに分割配送される旨の短い前置き、
# `(参照パス)` 行は実行時に解決した prompts ディレクトリの絶対パスである。実パスは実行環境
# 依存で長さが非有界のため、組み立てた全文の文字数を計測し、8,000 を超える場合は
#   (i) 実パス行を落として再計測 → (ii) それでも超える場合は delivery-note 全体を落として再計測
# の順で段階的に縮退する。SELF_HEAL と always-1.md は不落単位 (ESSENTIAL) であり、いかなる
# 場合も落とさない。ESSENTIAL 単体が 8,000 字を超える場合、ガードは超過を許容する
# (best effort) — persisted-output 化されたときにこそ自己修復指示が必要になるため、その場合も
# 指示を先頭に残すことを優先する。
#
# ## 出力 JSON 形状
#
#   {
#     "hookSpecificOutput": {
#       "hookEventName": "<入力の hook_event_name をそのまま echo>",
#       "additionalContext": "<上記の組み立て結果>"
#     }
#   }
#
# ## fail-open 条件
#
# - jq 不在
# - stdin が不正 JSON / hook_event_name が空
# - always-1.md が読めない (空文字列を含む)
# - delivery-note.md が読めない場合は delivery-note 無しで ESSENTIAL のみ注入する
#   (ペイロード単位の fail-open)
# - 配送済みマーカーの削除失敗は無視して注入を継続する
#
# auto mode 限定の方針 (after 系 = commit→push→PR→merge 自走) は inject-auto.sh が別途配送する。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# hook_event_name / session_id を 1 回の jq 呼び出しで取得する。
{ read -r HOOK_EVENT; read -r SESSION_ID; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.hook_event_name // ""),
    (.session_id // "")
  ' 2>/dev/null
)

if [ -z "$HOOK_EVENT" ]; then
  exit 0
fi

SAFE_SESSION_ID=""
if [ -n "$SESSION_ID" ]; then
  SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9._-')
fi

STATE_DIR="${TMPDIR:-/tmp}/agent-discipline-state"

# SessionStart のたびに、UserPromptSubmit 側の配送済みマーカーを無条件で削除する
# (resume / clear / compact のたびに全要素を再配送するため)。
# state dir が無い場合も rm -f は無害に no-op する。
if [ -n "$SAFE_SESSION_ID" ]; then
  rm -f \
    "$STATE_DIR/delivered-rules-2-$SAFE_SESSION_ID" \
    "$STATE_DIR/delivered-rules-3-$SAFE_SESSION_ID" \
    "$STATE_DIR/delivered-discipline-$SAFE_SESSION_ID" \
    2>/dev/null
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
if [ -z "$SCRIPT_DIR" ] || [ ! -r "$SCRIPT_DIR/lib/prompt-body.sh" ]; then
  exit 0
fi
# shellcheck source=plugins/agent-discipline/hooks/scripts/lib/prompt-body.sh
source "$SCRIPT_DIR/lib/prompt-body.sh" || exit 0

PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)

CORE=$(read_agent_discipline_prompt "$PROMPTS_DIR/always-1.md")
if [ -z "$CORE" ]; then
  exit 0
fi

# 自己修復指示 (ヘッダ「自己修復指示」節)。additionalContext の最先頭に必ず置く。
SELF_HEAL='(自己修復) このメッセージが persisted-output として退避されている場合は、スタブに記載されたパスの退避ファイルを Read で全文読了してから作業を開始すること。'

NOTE=$(read_agent_discipline_prompt "$PROMPTS_DIR/delivery-note.md")
PATH_LINE="(参照パス) $PROMPTS_DIR"

# additionalContext を組み立てた後の全文文字数を計測し、8,000 字を超える場合は
# (i) 実パス行を落として再計測 → (ii) それでも超えるなら delivery-note 全体を落として再計測、
# の順で段階的に縮退する。ESSENTIAL (= SELF_HEAL + CORE) はいかなる場合も落とさない。
# ESSENTIAL 単体が 8,000 字を超える場合は超過を許容する (best effort、ヘッダ参照)。
ESSENTIAL="$SELF_HEAL

$CORE"

if [ -n "$NOTE" ]; then
  FULL="$SELF_HEAL

$NOTE
$PATH_LINE

$CORE"
else
  FULL="$ESSENTIAL"
fi

LEN=$(printf '%s' "$FULL" | wc -m)

if [ "$LEN" -gt 8000 ] && [ -n "$NOTE" ]; then
  FULL="$SELF_HEAL

$NOTE

$CORE"
  LEN=$(printf '%s' "$FULL" | wc -m)
fi

if [ "$LEN" -gt 8000 ]; then
  FULL="$ESSENTIAL"
fi

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$FULL" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'
