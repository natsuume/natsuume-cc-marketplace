#!/bin/bash
# inject-always.sh
# SessionStart で「permission_mode に依らず常時適用」 すべき agent-discipline ルールを、
# セッションのモデルに応じて always-fable.md / always-sonnet.md のいずれかを
# additionalContext として注入する (#175)。
#
# ## モデル判定 fallback chain (先に確定した段階で判定を打ち切る)
#
# 1. stdin (hook input JSON) の `.model` フィールド
# 2. transcript 解析: hook input の `.transcript_path` に対し
#      jq -r 'select(.type=="assistant" and .isSidechain != true) | .message.model // empty' \
#        | tail -n 1
#    で最後の main-chain assistant 行のモデル ID を取得する (セッション中の /model 切替後も
#    最新の観測値が得られる、state file より常に新鮮な情報源)
# 3. state file `${TMPDIR:-/tmp}/agent-discipline-state/model-<session_id>`
#    (同一セッションの過去 SessionStart で確定した値のキャッシュ。transcript が空 (/clear 直後
#    等)・読めない場合の最後の砦であり、/model 切替を跨ぐと stale になりうるため transcript より
#    後に置く。session_id は fable-discipline の inject-fable-role.sh と同じ sanitize 方式
#    `tr -cd 'A-Za-z0-9._-'` を適用したもの)
# 4. 上記いずれも空 → 判定不能
#
# ## 適用規則 (4 分岐)
#
# - モデル ID (小文字化) が `fable` を含む → hooks/prompts/always-fable.md を注入
# - `sonnet` を含む → hooks/prompts/always-sonnet.md を注入
# - 非空でそのいずれでもない (opus / haiku 等) → hooks/prompts/always-sonnet.md を注入
# - 判定不能 → hooks/prompts/preamble-self-gate.md + hooks/prompts/always-sonnet.md を注入し、
#   state file の代わりに pending マーカー
#   `${TMPDIR:-/tmp}/agent-discipline-state/pending-model-<session_id>` を作成する
#
# 判定不能以外の 3 分岐では、確定したモデル ID を毎回 state file
# `${TMPDIR:-/tmp}/agent-discipline-state/model-<session_id>` に書き込む
# (fable-discipline の inject-fable-role.sh と同じ sanitize 方式)。
#
# ## 分業規律の併載 (#193 設計契約、#194 で非 Fable 配送に拡張)
#
# fable-discipline plugin の統合 (#192 決定事項 2) により、本スクリプトは 1 回のモデル判定で
# 「常時適用ルール」と「分業規律」の 2 ペイロードを注入する。#194 (決定事項 5: 配送対象は
# 非 Fable モデル全て) 適用後の配送マトリクス (#194 で実装済み):
#
#   | モデル判定     | 常時適用ルール                           | 分業規律                                                |
#   |----------------|------------------------------------------|---------------------------------------------------------|
#   | fable を含む   | always-fable.md                          | discipline-preamble-fable.md + discipline-fable.md      |
#   | sonnet を含む  | always-sonnet.md                         | discipline-sonnet.md (#194 新設)                        |
#   | 非空でその他   | always-sonnet.md                         | discipline-sonnet.md (同上)                             |
#   | 判定不能       | preamble-self-gate.md + always-sonnet.md | discipline-preamble-self-gate.md + discipline-sonnet.md |
#
# - 2 ペイロードは 1 つの additionalContext に「常時ルール → 分業規律」の順で連結し、分業規律
#   ブロックの先頭に model 別の見出しを置く: fable 分岐は「# agent-discipline: 分業規律
#   (Fable セッション)」、sonnet / その他 / 判定不能分岐は「# agent-discipline: 分業規律 (Sonnet)」
#   (常時ルールの「(Sonnet)」表記と同じ規則。非 Fable 用の本文が Sonnet 向け書式のため)
# - 見出しを参照する self-gate (preamble-self-gate.md / discipline-preamble-self-gate.md) の
#   境界記述は、model 別見出しの導入に伴い「# agent-discipline: 分業規律」で始まる見出し、という
#   プレフィクス一致で書く (見出し文字列の完全一致参照にすると model 分岐ごとに文言が割れるため)
# - 分業規律ブロックは additionalContext の末尾に置くこと (制約)。判定不能時の自己ゲート
#   (discipline-preamble-self-gate.md) は無視の射程を「見出し〜メッセージ末尾」で定義しており、
#   分業規律より後ろに別ペイロードを追加するとそれも無視射程に入る。後続ペイロードを追加する
#   場合は self-gate の境界定義ごと更新すること (codex rescue 指摘の将来懸念への予防)
# - 分業規律側のみ読めない場合は常時ルールのみ注入する (fail-open の粒度はペイロード単位。
#   常時ルールが読めない場合は従来どおり無音終了)
# - state file は agent-discipline-state/model-<session_id> に一本化し、移設される
#   block-fable-subagent.sh も同 state を参照する (fable-discipline-state は廃止)
# - resolve-model-on-prompt.sh の pending 補正 (判定不能セッションの one-shot 再配送) も
#   上記マトリクスと同じ組で両ペイロードを配送する
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
# - 注入対象の prompts/*.md が読めない (空文字列を含む)
# - state file / pending マーカーの書き込み失敗 (state はあくまで補助情報であり、書き込みに
#   失敗しても注入自体は継続する)
#
# auto mode 限定の方針 (after 系 = commit→push→PR→merge 自走) は inject-auto.sh が別途配送する。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# hook_event_name / model / session_id / transcript_path を 1 回の jq 呼び出しで取得する。
# INPUT が不正な JSON / 空の場合 jq は parse error を stderr に吐くため 2>/dev/null で抑制し、
# 各値の空判定でフォールバックさせる (hook の stderr は利用者に見えるため、解析失敗を
# ノイズとして表に出さない)。
{ read -r HOOK_EVENT; read -r STDIN_MODEL; read -r SESSION_ID; read -r TRANSCRIPT_PATH; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.hook_event_name // ""),
    (.model // ""),
    (.session_id // ""),
    (.transcript_path // "")
  ' 2>/dev/null
)

# hook_event_name が取れなければイベント名を正しくエコーできないので無音終了する
# (誤った既定値で hookSpecificOutput.hookEventName を返すと別 event の文脈に誘導する恐れがある)。
if [ -z "$HOOK_EVENT" ]; then
  exit 0
fi

# session_id はマーカー/state file 名に使うため英数字とピリオド・アンダースコア・ハイフンのみに
# sanitize する (fable-discipline の inject-fable-role.sh と同じ方式)。
SAFE_SESSION_ID=""
if [ -n "$SESSION_ID" ]; then
  SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9._-')
fi

STATE_DIR="${TMPDIR:-/tmp}/agent-discipline-state"

# fallback chain: 1) stdin.model 2) transcript 解析 3) state file 4) 判定不能
# (transcript は最後の main-chain assistant 行 = 最新の観測値であり、/model 切替を跨いで
# stale になりうる state file キャッシュより必ず先に参照する。codex review P2 対応)
MODEL="$STDIN_MODEL"

if [ -z "$MODEL" ] && [ -n "$TRANSCRIPT_PATH" ]; then
  MODEL=$(jq -r 'select(.type=="assistant" and .isSidechain != true) | .message.model // empty' "$TRANSCRIPT_PATH" 2>/dev/null | tail -n 1)
fi

if [ -z "$MODEL" ] && [ -n "$SAFE_SESSION_ID" ]; then
  MODEL=$(cat "$STATE_DIR/model-$SAFE_SESSION_ID" 2>/dev/null)
fi

# 注入本文を prompts/ から読み込む。読めない場合は fail-open で無音終了する
# (jq 不在時と同じ方針。壊れた・欠けた注入で誤誘導するより注入しない方が安全)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)

if [ -z "$MODEL" ]; then
  # 判定不能: 自己ゲート前置き + always-sonnet.md を注入し、state file の代わりに
  # pending マーカーを作成する (次回 UserPromptSubmit で resolve-model-on-prompt.sh が補正する)。
  PREAMBLE=$(cat "$PROMPTS_DIR/preamble-self-gate.md" 2>/dev/null)
  BODY=$(cat "$PROMPTS_DIR/always-sonnet.md" 2>/dev/null)
  if [ -z "$PREAMBLE" ] || [ -z "$BODY" ]; then
    exit 0
  fi

  if [ -n "$SAFE_SESSION_ID" ] && mkdir -p "$STATE_DIR" 2>/dev/null; then
    : > "$STATE_DIR/pending-model-$SAFE_SESSION_ID" 2>/dev/null
  fi

  CONTEXT="$PREAMBLE

$BODY"

  # 分業規律 (判定不能時): discipline-preamble-self-gate.md + discipline-sonnet.md (#194)。
  # fail-open はペイロード単位: どちらか読めない/空なら分業規律ブロックを付けず常時ルールのみ注入する。
  DISCIPLINE_PREAMBLE=$(cat "$PROMPTS_DIR/discipline-preamble-self-gate.md" 2>/dev/null)
  DISCIPLINE_BODY=$(cat "$PROMPTS_DIR/discipline-sonnet.md" 2>/dev/null)
  if [ -n "$DISCIPLINE_PREAMBLE" ] && [ -n "$DISCIPLINE_BODY" ]; then
    CONTEXT="$CONTEXT


# agent-discipline: 分業規律 (Sonnet)

$DISCIPLINE_PREAMBLE

$DISCIPLINE_BODY"
  fi

  jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
    hookSpecificOutput: {
      hookEventName: $evt,
      additionalContext: $ctx
    }
  }'
  exit 0
fi

# 判定できた場合は毎回 state file に書き込む (fable-discipline の inject-fable-role.sh と
# 同じ sanitize 方式)。書き込み失敗は無視する (state はあくまで補助情報)。
# 過去の判定不能 SessionStart が残した pending マーカーもここで掃除する (残すと
# resolve-model-on-prompt.sh が確定済みモデルの同一プロンプトをもう一度注入してしまう)。
if [ -n "$SAFE_SESSION_ID" ] && mkdir -p "$STATE_DIR" 2>/dev/null; then
  printf '%s' "$MODEL" > "$STATE_DIR/model-$SAFE_SESSION_ID" 2>/dev/null
  rm -f "$STATE_DIR/pending-model-$SAFE_SESSION_ID" 2>/dev/null
fi

if printf '%s' "$MODEL" | grep -qi 'fable'; then
  CONTEXT=$(cat "$PROMPTS_DIR/always-fable.md" 2>/dev/null)

  if [ -n "$CONTEXT" ]; then
    # 分業規律 (fable 確定時): discipline-preamble-fable.md + discipline-fable.md。
    # fail-open はペイロード単位: どちらか読めない/空なら分業規律ブロックを付けず常時ルールのみ注入する。
    DISCIPLINE_PREAMBLE=$(cat "$PROMPTS_DIR/discipline-preamble-fable.md" 2>/dev/null)
    DISCIPLINE_BODY=$(cat "$PROMPTS_DIR/discipline-fable.md" 2>/dev/null)
    if [ -n "$DISCIPLINE_PREAMBLE" ] && [ -n "$DISCIPLINE_BODY" ]; then
      CONTEXT="$CONTEXT


# agent-discipline: 分業規律 (Fable セッション)

$DISCIPLINE_PREAMBLE

$DISCIPLINE_BODY"
    fi
  fi
else
  # sonnet を含む場合も、非空でそのいずれでもない (opus / haiku 等) 場合も、
  # 同じく always-sonnet.md を注入する (ユーザ決定事項 6)。分業規律 (#194 で実装) も併載する。
  CONTEXT=$(cat "$PROMPTS_DIR/always-sonnet.md" 2>/dev/null)

  if [ -n "$CONTEXT" ]; then
    # 分業規律 (sonnet / その他確定時): discipline-sonnet.md のみ (preamble は付けない、判定不能時と
    # 異なり自己ゲートが不要なため)。fail-open はペイロード単位: 読めない/空なら分業規律ブロックを
    # 付けず常時ルールのみ注入する。
    DISCIPLINE_BODY=$(cat "$PROMPTS_DIR/discipline-sonnet.md" 2>/dev/null)
    if [ -n "$DISCIPLINE_BODY" ]; then
      CONTEXT="$CONTEXT


# agent-discipline: 分業規律 (Sonnet)

$DISCIPLINE_BODY"
    fi
  fi
fi

if [ -z "$CONTEXT" ]; then
  exit 0
fi

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'
