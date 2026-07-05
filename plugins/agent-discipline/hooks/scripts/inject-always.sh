#!/bin/bash
# inject-always.sh
# SessionStart で「permission_mode に依らず常時適用」 すべき agent-discipline ルールを、
# セッションのモデルに応じて always-fable.md / always-sonnet.md のいずれかを
# additionalContext として注入する (#175)。
#
# ## モデル判定 fallback chain (先に確定した段階で判定を打ち切る)
#
# 1. stdin (hook input JSON) の `.model` フィールド
# 2. state file `${TMPDIR:-/tmp}/agent-discipline-state/model-<session_id>`
#    (同一セッションの過去 SessionStart で確定した値。session_id は fable-discipline の
#    inject-fable-role.sh と同じ sanitize 方式 `tr -cd 'A-Za-z0-9._-'` を適用したもの)
# 3. transcript 解析: hook input の `.transcript_path` に対し
#      jq -r 'select(.type=="assistant" and .isSidechain != true) | .message.model // empty' \
#        | tail -n 1
#    で最後の main-chain assistant 行のモデル ID を取得する
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
# 実装本体 (本コメントブロック以下のコード) は #175 Phase A 時点では旧実装
# (常時 always-rules.md のみを注入する単一分岐) のまま据え置いている。本コメントブロックは
# Phase B で実装するモデル別配送の設計を文書化したものであり、現時点の実際の挙動を表さない。
#
# auto mode 限定の方針 (after 系 = commit→push→PR→merge 自走) は inject-auto.sh が別途配送する。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# hook_event_name は入力からそのまま読み取る (既定値を埋めると別 event の
# 文脈に誘導する恐れがあるため)。INPUT が不正な JSON / 空の場合 jq は parse error を
# stderr に吐くため 2>/dev/null で抑制し、HOOK_EVENT 空判定でフォールバックさせる
# (hook の stderr は利用者に見えるため、解析失敗をノイズとして表に出さない)。
HOOK_EVENT=$(printf '%s' "$INPUT" | jq -r '.hook_event_name // ""' 2>/dev/null)
if [ -z "$HOOK_EVENT" ]; then
  exit 0
fi

# 注入本文を prompts/ から読み込む。読めない場合は fail-open で無音終了する
# (jq 不在時と同じ方針。壊れた・欠けた注入で誤誘導するより注入しない方が安全)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)
CONTEXT=$(cat "$PROMPTS_DIR/always-rules.md" 2>/dev/null)
if [ -z "$CONTEXT" ]; then
  exit 0
fi

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'
