#!/bin/bash
# inject-always.sh
# SessionStart で「permission_mode に依らず常時適用」 すべき agent-discipline ルールの
# part 1/3 を、セッションのモデルに応じて additionalContext として注入する (#175、issue #236
# で 3 part 分割に再設計)。
#
# ## issue #236 (注入ペイロード分割) の背景
#
# 旧設計は常時ルール全文 (always-fable.md / always-sonnet.md) + 分業規律を 1 つの
# additionalContext に連結しており、Claude Code の inline 配送閾値 (約 9〜10K 文字/要素) を
# 超えて persisted-output (2KB プレビューのみ) に劣化していた。本スクリプトは SessionStart で
# part 1 (delivery-note + 常時ルールの一部) のみを注入し、残りの part (part 2/3・part 3/3) と
# 分業規律は UserPromptSubmit の別スクリプト (inject-rules-part.sh / inject-discipline.sh) が
# 最初のユーザプロンプト処理時に個別の要素として配送する。設計契約全文は Phase A commit の
# docs/issue-236-phase-a.md (Phase B 完了後に削除済み) を参照。
#
# ## モデル判定 fallback chain (先に確定した段階で判定を打ち切る、本スクリプトのみが実行する)
#
# 1. stdin (hook input JSON) の `.model` フィールド
# 2. transcript 解析: hook input の `.transcript_path` に対し
#      jq -r 'select(.type=="assistant" and .isSidechain != true) | .message.model // empty' \
#        | tail -n 1
#    で最後の main-chain assistant 行のモデル ID を取得する
# 3. state file `${TMPDIR:-/tmp}/agent-discipline-state/model-<session_id>`
#    (同一セッションの過去 SessionStart で確定した値のキャッシュ)
# 4. 上記いずれも空 → 判定不能
#
# ## 配送マトリクス (issue #236 再設計後)
#
#   | モデル判定     | SessionStart (本スクリプト、1 要素)                    |
#   |----------------|----------------------------------------------------------|
#   | fable を含む   | delivery-note + always-fable.md                          |
#   | sonnet を含む  | delivery-note + always-sonnet-1.md                       |
#   | 非空でその他   | delivery-note + always-sonnet-1.md                       |
#   | 判定不能       | delivery-note + preamble-self-gate.md + always-sonnet-1.md |
#
# 全分岐共通で、要素の最先頭 (delivery-note より前) に自己修復指示 (SELF_HEAL、issue #235、
# 下記「自己修復指示」節) が付く。
#
# 残り (UserPromptSubmit で個別配送、本スクリプトの責務外):
#   - always-sonnet-2.md / always-sonnet-3.md (非 fable 確定時、または判定不能時は
#     part-self-gate.md 付き) … inject-rules-part.sh
#   - 分業規律 (discipline-*.md、モデル別) … inject-discipline.sh
#   - 判定不能 → Fable 確定時の常時ルール one-shot 補正 (prefix + always-fable.md) …
#     resolve-model-on-prompt.sh
#
# ## state file の atomic 書込と成否確認 (issue #236 で追加)
#
# state file (`model-<session_id>`) は分割後の part 2/3・分業規律スクリプトが読む IPC に
# なるため、同一ディレクトリ内に temp file を書いてから `mv` する atomic 書込にし、書込の成否
# (temp 書込・mv の両方) を確認する。モデルが確定したのに書込が失敗した場合は pending マーカー
# (`pending-model-<session_id>`) の作成にフォールバックし、判定不能セマンティクス (自己ゲート
# 付き配送 + resolve-model-on-prompt.sh の one-shot 補正) に縮退する。pending の作成にも
# 失敗した場合は state 変更なしで注入のみ継続する (既知の制約、設計契約 §8-4 の床)。
# state 書込が成功した場合のみ、過去の判定不能 SessionStart が残した pending マーカーを削除する。
#
# session_id が取得できない (空の) 場合は、state 書込自体を試みない (書けないため) 上に
# 自己ゲートへの縮退もしない。session_id が無いと state file / pending マーカーのどちらも
# 名前が付けられず、UserPromptSubmit 側のスクリプトも同じ理由で早期 exit するため、
# 自己ゲート文言が約束する「one-shot 補正で確定版が届く」が成立しない (縮退すると誤誘導になる)。
# この場合はモデルが確定しているとおりの part1 本文をそのまま配送する。
#
# 配送済みマーカー (`delivered-rules-2-<session_id>` / `delivered-rules-3-<session_id>` /
# `delivered-discipline-<session_id>`) は SessionStart のたびに無条件で削除する (`startup` 以外に
# `resume` / `clear` / `compact` でも発火するため、全要素を毎回再配送するセマンティクスを維持する)。
#
# ## 自己修復指示 (issue #235 で追加)
#
# Claude Code は hook の additionalContext 1 要素が inline 閾値 (約 9〜10K 文字、UTF-16
# code unit 基準) を超えると本文をファイルへ退避し、`<persisted-output>` スタブ (退避パス +
# 先頭 2KB プレビュー) のみを context に載せる。#236 の分割で全要素は 8,000 字以下に収まって
# いるが、将来の閾値変動・ペイロード増加で劣化が再発しても「退避ファイルを読み直せば機能する」
# 状態を保つため、additionalContext の最先頭 (4 分岐すべて、delivery-note より前 = プレビュー
# 2KB に必ず入る位置) に自己修復指示 1 段落を必ず置く:
#
# - 文言はプレビューを圧迫しないよう 200 字以内とし、「persisted-output として退避されている
#   場合は」という条件付き文言にする (inline 配送時に読んでも違和感がないこと)。指示には
#   「退避ファイル (スタブに記載されたパス) を Read で全文読了してから作業を開始する」ことを
#   明記する
# - 文言は本スクリプト内の SELF_HEAL 定数が保持する (issue #235 の I/O 契約により、md
#   ファイル側には置かずスクリプト側で付与する)
# - resolve-model-on-prompt.sh の one-shot 補正ペイロード最先頭にも byte-identical な同文を
#   置く (スクリプト間の二重管理。変更時は必ず両スクリプトを同時に更新すること)
#
# ## delivery-note と 8K ガード (issue #236 で追加、設計契約 §2。#235 で不落単位を拡張)
#
# 自己修復指示 (SELF_HEAL) に続けて `delivery-note.md` (常時ルール・分業規律が複数メッセージに
# 分割配送される旨の短い前置き) と、実行時に解決した prompts ディレクトリの絶対パス 1 行を
# 付加する。prompts ディレクトリの実パスは実行環境依存で長さが非有界のため、additionalContext
# を最終的に組み立てた後の全文に対して文字数を計測し、8,000 を超える場合は
#   (i) 実パス行を落として再計測 → (ii) それでも超える場合は delivery-note 全体を落として再計測
# の順で段階的に縮退する。SELF_HEAL とルール本文 (self-gate 前置き preamble-self-gate.md を
# 含む always-fable.md / always-sonnet-1.md) は不落単位 (ESSENTIAL = SELF_HEAL + CORE) であり、
# いかなる場合も落とさない。ESSENTIAL 単体が 8,000 字を超える場合、ガードは超過を許容する
# (best effort) — persisted-output 化されたときにこそ自己修復指示が必要になるため、その場合も
# 指示を先頭に残すことを優先する。
#
# ## 出力 JSON 形状
#
#   {
#     "hookSpecificOutput": {
#       "hookEventName": "<入力の hook_event_name をそのまま echo>",
#       "additionalContext": "<分岐に応じた注入本文>"
#     }
#   }
#
# ## fail-open 条件
#
# - jq 不在
# - stdin が不正 JSON / hook_event_name が空
# - 注入対象の part 1 本文 (always-fable.md / always-sonnet-1.md / preamble-self-gate.md) が
#   読めない (空文字列を含む)
# - delivery-note.md が読めない場合は delivery-note 無しで part 1 本文のみ注入する
#   (ペイロード単位の fail-open)
# - state file / pending マーカー / 配送済みマーカーの書き込み失敗 (state はあくまで補助情報
#   であり、書き込みに失敗しても注入自体は継続する。ただし state 書込失敗は判定不能
#   セマンティクスへの縮退を伴う、上記参照)
#
# auto mode 限定の方針 (after 系 = commit→push→PR→merge 自走) は inject-auto.sh が別途配送する。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# hook_event_name / model / session_id / transcript_path を 1 回の jq 呼び出しで取得する。
{ read -r HOOK_EVENT; read -r STDIN_MODEL; read -r SESSION_ID; read -r TRANSCRIPT_PATH; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.hook_event_name // ""),
    (.model // ""),
    (.session_id // ""),
    (.transcript_path // "")
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
# (resume / clear / compact のたびに全要素を再配送するセマンティクスを維持するため)。
# state dir が無い場合も rm -f は無害に no-op する。
if [ -n "$SAFE_SESSION_ID" ]; then
  rm -f \
    "$STATE_DIR/delivered-rules-2-$SAFE_SESSION_ID" \
    "$STATE_DIR/delivered-rules-3-$SAFE_SESSION_ID" \
    "$STATE_DIR/delivered-discipline-$SAFE_SESSION_ID" \
    2>/dev/null
fi

# fallback chain: 1) stdin.model 2) transcript 解析 3) state file 4) 判定不能
MODEL="$STDIN_MODEL"

if [ -z "$MODEL" ] && [ -n "$TRANSCRIPT_PATH" ]; then
  MODEL=$(jq -r 'select(.type=="assistant" and .isSidechain != true) | .message.model // empty' "$TRANSCRIPT_PATH" 2>/dev/null | tail -n 1)
fi

if [ -z "$MODEL" ] && [ -n "$SAFE_SESSION_ID" ]; then
  MODEL=$(cat "$STATE_DIR/model-$SAFE_SESSION_ID" 2>/dev/null)
fi

PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)

# state file を同一ディレクトリ内 temp file → mv で atomic に書き込む。成功したら 0、
# 失敗したら 1 を返す (temp 書込・mkdir・mv のいずれかの失敗を書込失敗として扱う)。
write_state_atomic() {
  local target content tmp
  target="$1"
  content="$2"
  tmp="${target}.tmp.$$"
  if ! mkdir -p "$STATE_DIR" 2>/dev/null; then
    return 1
  fi
  # 2>/dev/null は「>」より前に置く: shell の出力リダイレクト設定自体が失敗した場合
  # (permission denied 等)、そのエラーはコマンド自身の stderr 抑制より前に評価されるため、
  # 後置の 2>/dev/null では抑制できない (bash の既知の挙動)。無音 fail-open を保証するため
  # 先に stderr を /dev/null へ向けてから出力先を開く。
  if ! printf '%s' "$content" 2>/dev/null > "$tmp"; then
    rm -f "$tmp" 2>/dev/null
    return 1
  fi
  if ! mv "$tmp" "$target" 2>/dev/null; then
    rm -f "$tmp" 2>/dev/null
    return 1
  fi
  return 0
}

USE_SELF_GATE=0

if [ -z "$MODEL" ]; then
  USE_SELF_GATE=1
elif [ -n "$SAFE_SESSION_ID" ]; then
  if write_state_atomic "$STATE_DIR/model-$SAFE_SESSION_ID" "$MODEL"; then
    # state 書込が成功した場合のみ pending マーカーを削除する
    # (書込失敗時に旧 pending を消すと判定不能セマンティクスへの縮退ができなくなるため)。
    rm -f "$STATE_DIR/pending-model-$SAFE_SESSION_ID" 2>/dev/null
  else
    # モデルは確定したが state 書込に失敗した。判定不能セマンティクスへ縮退する。
    USE_SELF_GATE=1
  fi
fi
# else: モデルは確定しているが session_id が空。state 協調ができないため書込を試みず、
# 縮退もしない (USE_SELF_GATE は 0 のまま)。後続の分岐で確定モデルどおりの part1 を配送する。

if [ "$USE_SELF_GATE" -eq 1 ]; then
  # 判定不能 (または state 書込失敗によるフォールバック): 自己ゲート前置き +
  # always-sonnet-1.md を注入し、state file の代わりに pending マーカーを作成する
  # (次回 UserPromptSubmit で resolve-model-on-prompt.sh / inject-rules-part.sh /
  # inject-discipline.sh が判定不能セマンティクスで扱う)。pending 作成の失敗は無視する
  # (state も pending も書けない持続障害下では、注入のみを継続する既知の制約、設計契約 §8-4)。
  PREAMBLE=$(cat "$PROMPTS_DIR/preamble-self-gate.md" 2>/dev/null)
  BODY=$(cat "$PROMPTS_DIR/always-sonnet-1.md" 2>/dev/null)
  if [ -z "$PREAMBLE" ] || [ -z "$BODY" ]; then
    exit 0
  fi

  if [ -n "$SAFE_SESSION_ID" ] && mkdir -p "$STATE_DIR" 2>/dev/null; then
    : 2>/dev/null > "$STATE_DIR/pending-model-$SAFE_SESSION_ID"
  fi

  CORE="$PREAMBLE

$BODY"
elif printf '%s' "$MODEL" | grep -qi 'fable'; then
  CORE=$(cat "$PROMPTS_DIR/always-fable.md" 2>/dev/null)
  if [ -z "$CORE" ]; then
    exit 0
  fi
else
  # sonnet を含む場合も、非空でそのいずれでもない (opus / haiku 等) 場合も、
  # 同じく always-sonnet-1.md (part 1/3) を注入する。
  CORE=$(cat "$PROMPTS_DIR/always-sonnet-1.md" 2>/dev/null)
  if [ -z "$CORE" ]; then
    exit 0
  fi
fi

NOTE=$(cat "$PROMPTS_DIR/delivery-note.md" 2>/dev/null)
PATH_LINE="(参照パス) $PROMPTS_DIR"

# additionalContext を組み立てた後の全文文字数を計測し、8,000 字を超える場合は
# (i) 実パス行を落として再計測 → (ii) それでも超えるなら delivery-note 全体を落として再計測、
# の順で段階的に縮退する。CORE (self-gate 前置き + ルール本文) はいかなる場合も落とさない。
if [ -n "$NOTE" ]; then
  FULL="$NOTE
$PATH_LINE

$CORE"
else
  FULL="$CORE"
fi

LEN=$(printf '%s' "$FULL" | wc -m)

if [ "$LEN" -gt 8000 ] && [ -n "$NOTE" ]; then
  FULL="$NOTE

$CORE"
  LEN=$(printf '%s' "$FULL" | wc -m)
fi

if [ "$LEN" -gt 8000 ]; then
  FULL="$CORE"
fi

CONTEXT="$FULL"

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'
