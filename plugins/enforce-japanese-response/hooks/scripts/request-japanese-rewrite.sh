#!/bin/bash
# request-japanese-rewrite.sh
# 英語のメッセージを表示した印 (pending) があれば、日本語での書き直しを Claude に
# 指示する PostToolBatch hook。
#
# PostToolBatch は並列の tool 呼び出しの batch 全体が終わった後、次のモデル呼び出しの
# 前に 1 回だけ発火し、`additionalContext` で Claude に文脈を渡せる。MessageDisplay
# hook (record-english-message.sh) が残した pending をここで消費し、tool 呼び出しの
# 合間に英語で書かれたメッセージを日本語で書き直させる。
#
# 状態の場所と形式は lib/pending-state.sh の契約に従う。以下はこの hook の I/O 契約
# である。
#
# ## I/O 契約
#
# - 入力: PostToolBatch hook の stdin JSON。使うキーは次のとおり
#   - `session_id`: 状態ディレクトリーを決める
#   - `prompt_id` (任意): pending の内容と比べる。文字列でなければ空文字列として扱う
#   - `agent_id`: subagent の中で発火したときだけ付く
#   `cwd` と `tool_calls` は使わない
# - 環境変数: `TMPDIR`
# - 出力: 書き直しを指示する場合のみ、stdout に 1 つの JSON を出す
#     {"systemMessage": "<SYSTEM_MESSAGE>",
#      "hookSpecificOutput": {"hookEventName": "PostToolBatch",
#                             "additionalContext": "<REWRITE_CONTEXT>"}}
#   それ以外は何も出力しない
# - exit code: どの場合も 0
#
# ## 文面
#
# SYSTEM_MESSAGE はユーザ向けの警告で、次のとおり:
#   enforce-japanese-response: 英語のメッセージを検知したため、日本語での書き直しを
#   指示しました。
# REWRITE_CONTEXT は Claude への指示で、次のとおり:
#   直前に表示したメッセージは英語で書かれていました (settings の language は日本語
#   です)。そのメッセージを内容を変えずに日本語で書き直して再掲してから作業を続け、
#   以降のメッセージも日本語で書いてください。ユーザが英語での出力 (翻訳・英文の文面等)
#   を明示的に求めていた場合は、書き直さずにそのまま作業を続けてください。すでにその
#   メッセージを日本語で書き直して再掲している場合は、再掲せずに作業を続けてください。
# (いずれも実際の文字列は改行を含まない 1 行)
#
# 最後の文は、同じメッセージへの指示が重複した場合への備えである。Stop hook が pending
# を消した後に、turn 末尾のメッセージの MessageDisplay hook が pending を書くと、次の
# PostToolBatch で書き直しの指示が重複しうる (hook の起動順は保証されていない)。
#
# ## 処理
#
# 1. pending があれば削除する
# 2. pending の内容 (記録時の `prompt_id`) と入力の `prompt_id` がどちらも空でなく、
#    かつ異なる場合は何も出力しない (前の turn の印を持ち越さない)
# 3. それ以外は上記の JSON を出力する
#
# ## 何もしない条件 (いずれも無出力で exit 0)
#
# 1. jq が無い (`command -v jq` が失敗する) → 何もしない (fail-open。pending も残す)
# 2. stdin を JSON object として解析できない → 何もしない (fail-open。pending も残す)
# 3. `agent_id` が空でない文字列 → subagent の tool batch では指示しない。親 session の
#    pending を消費しないよう、pending も残す
# 4. `session_id` が無い・文字列でない・`^[A-Za-z0-9_-]{1,128}$` に一致しない
#    → 状態を読み書きしない
# 5. 状態ディレクトリーが所有者の検査 (lib/pending-state.sh) に通らない → 状態を
#    読み書きしない (fail-open)
# 6. pending が無い → 何もしない
#
# ## 実行環境
#
# bash と jq と POSIX のコマンドのみに依存し、Linux (WSL2) と macOS (bash 3.2 / BSD
# ツール) の両方で動くように書く。lib はこの script の場所を基準に source する。

case ${BASH_SOURCE[0]} in
  */*) SCRIPT_DIR=${BASH_SOURCE[0]%/*} ;;
  *) SCRIPT_DIR=. ;;
esac
# shellcheck source=plugins/enforce-japanese-response/hooks/scripts/lib/pending-state.sh
. "$SCRIPT_DIR/lib/pending-state.sh" || exit 0

SYSTEM_MESSAGE='enforce-japanese-response: 英語のメッセージを検知したため、日本語での書き直しを指示しました。'
REWRITE_CONTEXT='直前に表示したメッセージは英語で書かれていました (settings の language は日本語です)。そのメッセージを内容を変えずに日本語で書き直して再掲してから作業を続け、以降のメッセージも日本語で書いてください。ユーザが英語での出力 (翻訳・英文の文面等) を明示的に求めていた場合は、書き直さずにそのまま作業を続けてください。すでにそのメッセージを日本語で書き直して再掲している場合は、再掲せずに作業を続けてください。'

# stdin の JSON を検査し、pending を探す session_id を出力する。agent_id が空でない
# 文字列、または session_id が不正なら何も出力しない。object として解析できなければ
# 失敗 (非 0) を返す。
extract_session_id() {
  jq -r "$STATE_ID_JQ_DEF"'
    if type != "object" then error("hook input is not an object") else . end
    | if (.agent_id | type == "string" and . != "") then empty
      elif (.session_id | is_valid_state_id) then .session_id
      else empty
      end
  '
}

main() {
  command -v jq >/dev/null 2>&1 || return 0

  local hook_input session_id
  hook_input=$(cat) || return 0
  session_id=$(printf '%s' "$hook_input" | extract_session_id 2>/dev/null) || return 0
  [ -n "$session_id" ] || return 0

  local recorded_prompt_id current_prompt_id
  recorded_prompt_id=$(read_pending "$session_id") || return 0
  clear_pending "$session_id"
  current_prompt_id=$(printf '%s' "$hook_input" |
    jq -r '.prompt_id | if type == "string" then . else "" end' 2>/dev/null) || return 0
  # 前の turn で記録した印は持ち越さない
  if [ -n "$recorded_prompt_id" ] && [ -n "$current_prompt_id" ] &&
    [ "$recorded_prompt_id" != "$current_prompt_id" ]; then
    return 0
  fi

  jq -n -c --arg system_message "$SYSTEM_MESSAGE" --arg context "$REWRITE_CONTEXT" '
    {
      systemMessage: $system_message,
      hookSpecificOutput: {
        hookEventName: "PostToolBatch",
        additionalContext: $context
      }
    }
  '
}

main 2>/dev/null
exit 0
