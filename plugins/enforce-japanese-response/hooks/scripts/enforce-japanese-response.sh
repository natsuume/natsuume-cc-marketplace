#!/bin/bash
# enforce-japanese-response.sh
# turn 末尾の応答が英語で書かれていたら、日本語で書き直させる Stop hook。
#
# 入力の検査・目標言語の解決・英語の応答の判定を順に行い、英語の応答と判定したときだけ
# block を出力する。以下はこの実装が従う I/O 契約と判定規則である。
#
# ## I/O 契約
#
# - 入力: Stop hook の stdin JSON。使うキーは次の 3 つ
#   - `last_assistant_message`: 直前の応答本文 (文字列)
#   - `stop_hook_active`: この Stop hook の block によって継続した turn なら true
#   - `cwd`: プロジェクトの settings を探す基準ディレクトリ
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
# ## 処理順と block しない条件 (いずれも無出力で exit 0)
#
# 1. jq が無い (`command -v jq` が失敗する) → 判定できないため何もしない (fail-open)
# 2. stdin を JSON object として解析できない → 何もしない (fail-open)
# 3. `stop_hook_active` が true → 何もしない (書き直し後の応答を再び block しない)
# 4. `last_assistant_message` が無い・文字列でない・空文字列 → 何もしない
# 5. 目標言語 (後述) が日本語でない、または解決できない → 何もしない
# 6. 除去後の本文 (後述) が英語の応答の条件を満たさない → 何もしない
# 7. 途中の jq 呼び出しが失敗した等、判定を完了できない → 何もしない (fail-open)
#
# ## 目標言語の解決順
#
# 次の順に settings ファイルを探し、`language` キーを持つ最初のファイルの値を使う:
#   1. `<cwd>/.claude/settings.local.json`
#   2. `<cwd>/.claude/settings.json`
#   3. `$HOME/.claude/settings.json`
# - `<cwd>` は hook 入力の `cwd`。`cwd` が無い・空文字列なら 1 と 2 を飛ばす
# - ファイルが存在しない・JSON object として解析できない・`language` キーを持たない
#   (値が null の場合を含む) ときは、そのファイルを無いものとして次を探す
# - 値が次のいずれかなら日本語とみなす。英字の大文字小文字は区別しない:
#     `日本語` / `ja` / `ja-` で始まる文字列 / `japanese`
#   前後の空白は取り除かない。値が文字列でない場合と上記以外の文字列は日本語以外とする
# - どのファイルにも `language` が無ければ未設定として判定を行わない
#
# ## 除去処理
#
# `last_assistant_message` から次の順に取り除いた残りを「除去後の本文」とする:
#   1. fenced code block: 行内の位置に依らず ``` から次の ``` までの範囲 (両端を含む)。
#      閉じる ``` が無い場合は、開始の ``` から本文末尾までを取り除く
#   2. インライン code: ` から次の ` までの範囲 (両端を含む。改行をまたいでもよい)
#   3. URL: `http://` または `https://` と、それに続く 1 文字以上の URL 本体。URL 本体は
#      印字可能な ASCII (0x21〜0x7E) のうち `(` `)` `<` `>` `[` `]` `"` を除いた文字の
#      並びで、空白文字・印字可能な ASCII 以外の文字 (日本語など)・上記 7 文字の
#      いずれかの直前で終わる。URL の直後に空白なしで続く日本語や、Markdown のリンクの
#      閉じ括弧は URL に含めない
#
# ## 判定基準
#
# 除去後の本文について、jq の正規表現 (Oniguruma) で次を数える:
#   - L: ASCII 英字 `[A-Za-z]` の数
#   - J: ひらがな・カタカナ・漢字 `[\p{Hiragana}\p{Katakana}\p{Han}]` の数
# `L >= 40` かつ `J / (J + L) < 0.05` のとき英語の応答と判定する。
# 比率は浮動小数点の丸めを避けるため、同値な整数比較 `20 * J < J + L` で評価する。
#
# ## 対象外
#
# - tool 呼び出しの合間に出力される英語の文 (Stop 時点の最終応答だけを見る)
# - subagent の応答 (SubagentStop には登録しない)
#
# ## 実行環境
#
# bash と jq のみに依存し、Linux (WSL2) と macOS (bash 3.2 / BSD ツール) の両方で動く
# ように書く。文字種の数え上げと除去処理は jq の中で行い、sed / grep / awk の GNU 拡張に
# 依存しない。

BLOCK_REASON='直前の応答が英語で書かれています。内容を変えずに日本語で書き直してください。ユーザが英語での出力 (翻訳・英文の文面等) を明示的に求めていた場合は書き直さず、その旨を日本語 1 文で添えて終えてください。'

# stdin の JSON から判定に使う値を取り出し、1 行の JSON で出力する。
#   {"active": <stop_hook_active が true か>, "message": <本文 or null>, "cwd": <文字列>}
# message は空でない文字列のときだけ値を持ち、それ以外は null にする。
# object として解析できなければ失敗 (非 0) を返す。
extract_hook_fields() {
  jq -c '
    if type != "object" then error("hook input is not an object") else . end
    | {
        active: (.stop_hook_active == true),
        message: (
          .last_assistant_message
          | if type == "string" and . != "" then . else null end
        ),
        cwd: (.cwd | if type == "string" then . else "" end)
      }
  '
}

# settings ファイル 1 つから language の値を JSON で出力する。
# ファイルが無い・JSON object 1 つとして解析できない・language が無い (null を含む)
# ときは何も出力しない。
read_language_setting() {
  local settings_file=$1
  [ -f "$settings_file" ] || return 0
  jq -c -s '
    if length == 1 and (.[0] | type) == "object" and .[0].language != null
    then .[0].language
    else empty
    end
  ' "$settings_file" 2>/dev/null
}

# local → project → user の順に settings を探し、最初に見つかった language の値を
# JSON で出力する。どこにも無ければ何も出力しない。
resolve_target_language() {
  local project_dir=$1
  local settings_file value
  for settings_file in \
    "${project_dir:+$project_dir/.claude/settings.local.json}" \
    "${project_dir:+$project_dir/.claude/settings.json}" \
    "${HOME:+$HOME/.claude/settings.json}"; do
    [ -n "$settings_file" ] || continue
    value=$(read_language_setting "$settings_file")
    if [ -n "$value" ]; then
      printf '%s\n' "$value"
      return 0
    fi
  done
  return 0
}

# language の値 (JSON) が日本語を表すなら true、それ以外なら false を出力する。
is_japanese_language() {
  local language_json=$1
  jq -n --argjson language "$language_json" '
    ($language | type) == "string"
    and (
      ($language | ascii_downcase) as $value
      | $value == "日本語"
        or $value == "ja"
        or ($value | startswith("ja-"))
        or $value == "japanese"
    )
  '
}

# extract_hook_fields の出力を stdin で受け取り、message が英語の応答なら block の
# JSON を出力する。英語の応答でなければ何も出力しない。
# URL 本体の文字クラス `[!#-'*-;=?-Z\\^-~]` は、0x21〜0x7E から `"` `(` `)` `<` `>`
# `[` `]` を除いた範囲を表す。jq のプログラムを単一引用符で囲んでいるため、`'` は
# jq の文字列リテラル内で ' と書き、`\` は \\\\ と書く。
judge_message() {
  jq -c --arg reason "$BLOCK_REASON" '
    .message
    | gsub("```[\\s\\S]*?(```|\\z)"; "")
    | gsub("`[^`]*`"; "")
    | gsub("https?://[!#-\u0027*-;=?-Z\\\\^-~]+"; "")
    | ([scan("[A-Za-z]")] | length) as $letters
    | ([scan("[\\p{Hiragana}\\p{Katakana}\\p{Han}]")] | length) as $japanese
    | if $letters >= 40 and 20 * $japanese < $japanese + $letters
      then {decision: "block", reason: $reason}
      else empty
      end
  '
}

main() {
  command -v jq >/dev/null 2>&1 || return 0

  local hook_input fields
  hook_input=$(cat) || return 0
  fields=$(printf '%s' "$hook_input" | extract_hook_fields 2>/dev/null) || return 0
  [ -n "$fields" ] || return 0

  local active has_message project_dir
  active=$(printf '%s' "$fields" | jq -r '.active' 2>/dev/null) || return 0
  [ "$active" = "false" ] || return 0
  has_message=$(printf '%s' "$fields" | jq -r '.message != null' 2>/dev/null) || return 0
  [ "$has_message" = "true" ] || return 0
  project_dir=$(printf '%s' "$fields" | jq -r '.cwd' 2>/dev/null) || return 0

  local language_json is_japanese
  language_json=$(resolve_target_language "$project_dir")
  [ -n "$language_json" ] || return 0
  is_japanese=$(is_japanese_language "$language_json" 2>/dev/null) || return 0
  [ "$is_japanese" = "true" ] || return 0

  local decision
  decision=$(printf '%s' "$fields" | judge_message 2>/dev/null) || return 0
  [ -n "$decision" ] || return 0
  printf '%s\n' "$decision"
}

main
exit 0
