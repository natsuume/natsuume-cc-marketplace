#!/bin/bash
# record-english-message.sh
# 表示された assistant のメッセージが英語で書かれていたら、書き直し指示待ちの印
# (pending) を残す MessageDisplay hook。
#
# MessageDisplay は表示専用のイベントで、Claude に何も返せない。そのため、この hook は
# 英語のメッセージを検知したことを状態ディレクトリーに記録するだけにし、書き直しの
# 指示は次の PostToolBatch hook (request-japanese-rewrite.sh) が出す。tool 呼び出しの
# 合間に表示されるメッセージも turn 末尾の応答も、表示されるすべての assistant の
# テキストがこの hook に届く。
#
# 目標言語の解決と英語の判定は lib/language-judgement.sh、状態の場所と形式は
# lib/pending-state.sh の契約に従う。以下はこの hook の I/O 契約である。
#
# ## I/O 契約
#
# - 入力: MessageDisplay hook の stdin JSON。使うキーは次のとおり
#   - `session_id`: 状態ディレクトリーを決める
#   - `prompt_id` (任意): pending に書く。文字列でなければ空文字列として扱う
#   - `cwd`: env `CLAUDE_PROJECT_DIR` が空のとき、プロジェクトの settings を探す
#     基準ディレクトリ
#   - `message_id`: バッファーを決める。同じメッセージのすべての batch で同じ値になる
#   - `index`: メッセージ内の batch の番号 (0 始まりの整数)
#   - `final`: メッセージの最後の batch なら true
#   - `delta`: 前の batch 以降に確定したテキスト
#   - `agent_id`: subagent の中で発火したときだけ付く
# - 環境変数: `CLAUDE_PROJECT_DIR`、`HOME`、`TMPDIR`
# - 出力: stdout には何も出力しない (出力すると表示が置き換わるため)
# - exit code: どの場合も 0
#
# ## 処理
#
# 同じメッセージの hook は並行して走り、完了の順は index の順と限らない。そのため
# batch ごとに断片を書き、断片が揃った時点で 1 つの hook だけが判定する (状態の構造と
# 排他の方法は lib/pending-state.sh)。
#
# 1. `index` が 0 (新しいメッセージの開始) なら、同じセッションの `buffers/` 直下で
#    最終更新から 10 分を超えたメッセージディレクトリー (最後の batch が届かなかった
#    もの) を削除する
# 2. メッセージディレクトリーに自分の断片 `<index>` (内容は `delta`) を書く。`final` が
#    true なら続けて `final` に `index` を書く
# 3. `final` があり、その値 N について 0..N の断片がすべて揃っていれば、`mkdir judged`
#    で判定役を取る。揃っていない・判定役を取れなければ、ここで終える (後から完了した
#    hook が判定する)
# 4. 判定役になった hook は、0..N を index の順に連結して本文とし、目標言語を解決する。
#    目標言語が日本語で、かつ本文が英語と判定されたら pending を作る (内容は
#    `prompt_id`)。その後、メッセージディレクトリーを丸ごと削除する
#
# 判定の済んだメッセージディレクトリーは削除されているため、同じ message_id の batch が
# 後から届くと、新しいメッセージの断片として扱う (0..N が揃わない限り判定しない)。
#
# 非対話の実行 (`claude -p`、Agent SDK) では、メッセージ全体が `index` 0・`final` true
# の 1 回の呼び出しで届く。この場合も 1 から 4 を同じ順に行う。
#
# ## 何もしない条件 (いずれも無出力で exit 0。状態を読み書きしない)
#
# 1. jq が無い (`command -v jq` が失敗する) → 判定できないため何もしない (fail-open)
# 2. stdin を JSON object として解析できない → 何もしない (fail-open)
# 3. `agent_id` が空でない文字列 → subagent のメッセージは対象外
# 4. `session_id` または `message_id` が無い・文字列でない・`^[A-Za-z0-9_-]{1,128}$` に
#    一致しない → パスに使えないため何もしない
# 5. `delta` が文字列でない、`index` が 0 以上 100000 未満の整数でない、`final` が
#    bool でない → 何もしない
# 6. 目標言語が日本語でない・未設定、または本文が英語でない → メッセージディレクトリー
#    の削除だけを行い、pending は作らない
# 7. 状態ディレクトリーが所有者の検査 (lib/pending-state.sh) に通らない → 状態を
#    読み書きしない (fail-open)
# 8. 途中の jq 呼び出しやファイル操作が失敗した等、処理を完了できない → そこで終える
#    (fail-open)
#
# ## 実行環境
#
# bash と jq と POSIX のコマンドのみに依存し、Linux (WSL2) と macOS (bash 3.2 / BSD
# ツール) の両方で動くように書く。lib はこの script の場所を基準に source する。

case ${BASH_SOURCE[0]} in
  */*) SCRIPT_DIR=${BASH_SOURCE[0]%/*} ;;
  *) SCRIPT_DIR=. ;;
esac
# shellcheck source=plugins/enforce-japanese-response/hooks/scripts/lib/language-judgement.sh
. "$SCRIPT_DIR/lib/language-judgement.sh" || exit 0
# shellcheck source=plugins/enforce-japanese-response/hooks/scripts/lib/pending-state.sh
. "$SCRIPT_DIR/lib/pending-state.sh" || exit 0

# stdin の JSON を検査し、処理に使う値を空白区切りの 1 行で出力する。
#   <session_id> <message_id> <index (10 進の整数)> <final>
# 何もしない条件 (agent_id・id・delta・index・final) に当たれば何も出力しない。
# object として解析できなければ失敗 (非 0) を返す。
extract_display_fields() {
  jq -r "$STATE_ID_JQ_DEF"'
    if type != "object" then error("hook input is not an object") else . end
    | if (.agent_id | type == "string" and . != "") then empty
      elif (.session_id | is_valid_state_id | not)
        or (.message_id | is_valid_state_id | not) then empty
      elif (.delta | type) != "string" then empty
      elif (.index | type) != "number" or .index < 0 or .index >= 100000
        or .index != (.index | floor) then empty
      elif (.final | type) != "boolean" then empty
      else [.session_id, .message_id, (.index | floor | tostring), (.final | tostring)]
        | join(" ")
      end
  '
}

# 目標言語が日本語で、本文 <message> が英語なら pending を作る。
record_if_english() {
  local hook_input=$1 session_id=$2 message=$3
  local cwd project_dir prompt_id
  cwd=$(printf '%s' "$hook_input" |
    jq -r '.cwd | if type == "string" then . else "" end' 2>/dev/null) || return 0
  project_dir=$(resolve_project_dir "$cwd")
  is_target_language_japanese "$project_dir" || return 0
  printf '%s' "$message" | is_english_text || return 0
  prompt_id=$(printf '%s' "$hook_input" |
    jq -r '.prompt_id | if type == "string" then . else "" end' 2>/dev/null) || return 0
  mark_pending "$session_id" "$prompt_id"
}

main() {
  command -v jq >/dev/null 2>&1 || return 0

  local hook_input fields
  hook_input=$(cat) || return 0
  fields=$(printf '%s' "$hook_input" | extract_display_fields 2>/dev/null) || return 0
  [ -n "$fields" ] || return 0

  local session_id message_id index final
  read -r session_id message_id index final <<<"$fields"

  # コマンド置換は末尾の改行を落とすため、番兵の `.` を付けて取り出してから外す
  local delta
  delta=$(printf '%s' "$hook_input" | jq -j '.delta + "."' 2>/dev/null) || return 0
  delta=${delta%.}

  if [ "$index" = "0" ]; then
    remove_stale_messages "$session_id"
  fi
  store_message_fragment "$session_id" "$message_id" "$index" "$delta" "$final" ||
    return 0
  claim_complete_message "$session_id" "$message_id" || return 0

  local message
  message=$(read_message_text "$session_id" "$message_id")
  record_if_english "$hook_input" "$session_id" "$message"
  # 判定と pending の作成を終えてから削除する (PostToolBatch hook はディレクトリーが
  # 残っている間 pending を読むのを待つ)
  remove_message "$session_id" "$message_id"
}

# 出力すると表示が置き換わるため、stdout には何も出さない
main >/dev/null 2>&1
exit 0
