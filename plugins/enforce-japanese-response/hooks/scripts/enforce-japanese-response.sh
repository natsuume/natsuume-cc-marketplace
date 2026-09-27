#!/bin/bash
# enforce-japanese-response.sh
# turn 末尾の応答が英語で書かれていたら、日本語で書き直させる Stop hook。
#
# 入力の検査・目標言語の解決・英語の応答の判定を順に行い、英語の応答と判定したときだけ
# block を出力する。以下はこの実装が従う I/O 契約と判定規則である。
#
# ## I/O 契約
#
# - 入力: Stop hook の stdin JSON。使うキーは次の 4 つ
#   - `last_assistant_message`: 直前の応答本文 (文字列)
#   - `stop_hook_active`: この Stop hook の block によって継続した turn なら true
#   - `cwd`: env `CLAUDE_PROJECT_DIR` が空のとき、プロジェクトの settings を探す
#     基準ディレクトリ
#   - `session_id`: 削除する pending (後述) の状態ディレクトリーを決める
# - 環境変数: `CLAUDE_PROJECT_DIR` (Claude Code が渡すプロジェクトのルート)、`HOME`、
#   `TMPDIR` (状態ディレクトリーの場所)
# - 出力: 英語の応答と判定した場合のみ、stdout に 1 つの JSON を出す
#     {"decision": "block", "reason": "<日本語の reason>"}
#   それ以外は何も出力しない
# - exit code: どの場合も 0
#
# ## reason
#
# reason は日本語で、次の 3 点を含む:
#   1. 直前の応答が英語で書かれていること
#   2. 内容を変えずに日本語で書き直すこと
#   3. ユーザが英語での出力 (翻訳・英文の文面等) を明示的に求めていた場合は書き直さず、
#      その旨を日本語 1 文で添えて終えること
# 文面は次のとおり:
#   直前の応答が英語で書かれています。内容を変えずに日本語で書き直してください。
#   ユーザが英語での出力 (翻訳・英文の文面等) を明示的に求めていた場合は書き直さず、
#   その旨を日本語 1 文で添えて終えてください。
#
# ## pending の削除
#
# MessageDisplay hook (record-english-message.sh) は、英語のメッセージを表示したことを
# 表す印 `pending` を状態ディレクトリーに残す (場所と形式は lib/pending-state.sh)。
# turn 末尾の応答はこの Stop hook 自身が判定するため、stdin を JSON object として
# 解析できたら、`stop_hook_active` や目標言語に依らず、判定より前にその session の
# `pending` を削除して次の turn に持ち越さない。続けて、`buffers` 直下のメッセージ
# ディレクトリーをすべて削除し (turn 末尾のメッセージはこの hook が判定する)、空になった
# `buffers` とセッションディレクトリーを削除する。`session_id` が無い・文字列でない・
# `^[A-Za-z0-9_-]{1,128}$` に一致しない場合と、状態ディレクトリーが所有者の検査
# (lib/pending-state.sh) に通らない場合は削除しない。
#
# ## 処理順と block しない条件 (いずれも無出力で exit 0)
#
# 1. jq が無い (`command -v jq` が失敗する) → 判定できないため何もしない (fail-open)
# 2. stdin を JSON object として解析できない → 何もしない (fail-open)
#    (解析できたら、ここで pending を削除する)
# 3. `stop_hook_active` が true → 何もしない (書き直し後の応答を再び block しない)
# 4. `last_assistant_message` が無い・文字列でない・空文字列 → 何もしない
# 5. 目標言語が日本語でない、または解決できない → 何もしない
# 6. 除去後の本文が英語の条件を満たさない → 何もしない
# 7. 途中の jq 呼び出しが失敗した等、判定を完了できない → 何もしない (fail-open)
#
# ## 目標言語の解決と英語の判定
#
# 目標言語の解決順・日本語とみなす値・除去処理・判定基準は lib/language-judgement.sh
# の契約に従う。この hook では次のように使う:
# - settings を探す `<project>` は、env `CLAUDE_PROJECT_DIR` が空でなければその値、
#   空または未設定なら hook 入力の `cwd`。セッションの作業ディレクトリがプロジェクトの
#   サブディレクトリに移っても、プロジェクトのルートの settings を読むため
#   `CLAUDE_PROJECT_DIR` を優先する
# - 除去処理と判定基準は `last_assistant_message` に適用する
#
# ## 対象外
#
# - subagent の応答 (SubagentStop には登録しない)
#
# tool 呼び出しの合間に表示されるメッセージは、この hook ではなく MessageDisplay hook
# (record-english-message.sh) と PostToolBatch hook (request-japanese-rewrite.sh) が
# 扱う。
#
# ## 実行環境
#
# bash と jq と POSIX のコマンドのみに依存し、Linux (WSL2) と macOS (bash 3.2 / BSD
# ツール) の両方で動くように書く。文字種の数え上げと除去処理は jq の中で行い、
# sed / grep / awk の GNU 拡張に依存しない。lib はこの script の場所を基準に source する。

case ${BASH_SOURCE[0]} in
  */*) SCRIPT_DIR=${BASH_SOURCE[0]%/*} ;;
  *) SCRIPT_DIR=. ;;
esac
# shellcheck source=plugins/enforce-japanese-response/hooks/scripts/lib/language-judgement.sh
. "$SCRIPT_DIR/lib/language-judgement.sh" || exit 0
# shellcheck source=plugins/enforce-japanese-response/hooks/scripts/lib/pending-state.sh
. "$SCRIPT_DIR/lib/pending-state.sh" || exit 0

BLOCK_REASON='直前の応答が英語で書かれています。内容を変えずに日本語で書き直してください。ユーザが英語での出力 (翻訳・英文の文面等) を明示的に求めていた場合は書き直さず、その旨を日本語 1 文で添えて終えてください。'

# stdin の JSON から判定に使う値を取り出し、1 行の JSON で出力する。
#   {"active": <stop_hook_active が true か>, "message": <本文 or null>, "cwd": <文字列>,
#    "session_id": <有効な session_id or 空文字列>}
# message は空でない文字列のときだけ値を持ち、それ以外は null にする。
# object として解析できなければ失敗 (非 0) を返す。
extract_hook_fields() {
  jq -c "$STATE_ID_JQ_DEF"'
    if type != "object" then error("hook input is not an object") else . end
    | {
        active: (.stop_hook_active == true),
        message: (
          .last_assistant_message
          | if type == "string" and . != "" then . else null end
        ),
        cwd: (.cwd | if type == "string" then . else "" end),
        session_id: (.session_id | if is_valid_state_id then . else "" end)
      }
  '
}

main() {
  command -v jq >/dev/null 2>&1 || return 0

  local hook_input fields
  hook_input=$(cat) || return 0
  fields=$(printf '%s' "$hook_input" | extract_hook_fields 2>/dev/null) || return 0
  [ -n "$fields" ] || return 0

  # turn 末尾の応答はこの hook が扱うため、MessageDisplay hook の印を持ち越さない
  local session_id
  session_id=$(printf '%s' "$fields" | jq -r '.session_id' 2>/dev/null) || session_id=""
  if [ -n "$session_id" ]; then
    clear_pending "$session_id"
    remove_all_messages "$session_id"
    remove_empty_session_state "$session_id"
  fi

  local active has_message cwd project_dir
  active=$(printf '%s' "$fields" | jq -r '.active' 2>/dev/null) || return 0
  [ "$active" = "false" ] || return 0
  has_message=$(printf '%s' "$fields" | jq -r '.message != null' 2>/dev/null) || return 0
  [ "$has_message" = "true" ] || return 0
  cwd=$(printf '%s' "$fields" | jq -r '.cwd' 2>/dev/null) || return 0
  project_dir=$(resolve_project_dir "$cwd")

  is_target_language_japanese "$project_dir" || return 0
  printf '%s' "$fields" | jq -j '.message' 2>/dev/null | is_english_text || return 0
  jq -n -c --arg reason "$BLOCK_REASON" '{decision: "block", reason: $reason}'
}

main
exit 0
