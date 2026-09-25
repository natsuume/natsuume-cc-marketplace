#!/bin/sh
# lint-payload-size.sh
#
# agent-discipline plugin の注入スクリプト (hooks/scripts/ 配下で additionalContext を出力する
# もの) を模擬 hook input で実際に実行し、出力 JSON の `.hookSpecificOutput.additionalContext`
# (以下「要素」) の文字数を実測する lint。Claude Code は additionalContext 1 要素が inline 閾値
# (約 9〜10K 文字) を超えると本文をファイルへ退避し先頭 2KB のプレビューしか context に載せない
# ため、各要素を上限 8,000 字以下に保つことをスクリプト実行ベースで検査する。組み立てロジック
# (前置き・連結・分岐) の変更に自動追従させるため、prompt ファイル単体の静的サイズではなく
# スクリプトの実出力を測る。
#
# 同期ドリフト検査 (lint-prompt-sync.sh) とは責務を分け、本スクリプトはサイズ検査のみを行う。
#
# ============================================================================
# 入出力契約
# ============================================================================
#
# - 引数: なし。引数が渡された場合は誤用として ERROR (exit 1) とする。
# - 実行位置: リポジトリルートから実行する (plugins/agent-discipline/... への相対パス参照を
#   用いるため)。参照先 (hooks/scripts/ の対象スクリプト・hooks.json・prompts ディレクトリ) が
#   見つからない場合は ERROR (exit 1) とし、検査を silent skip しない。
# - exit code:
#     0 … 全ケースが pass (WARN のみを含む場合も 0)
#     1 … FAIL が 1 件以上、または前提エラー (ERROR) があった
# - 出力メッセージ (lint-prompt-sync.sh と同じ様式。OK は stdout、WARN / FAIL / ERROR は stderr):
#     OK: <script>[ <arg>] / <case-id>: additionalContext <N> 字
#     OK: <script>[ <arg>] / <case-id>: 出力なし (対応表の期待どおり)
#     WARN: <script>[ <arg>] / <case-id>: additionalContext <N> 字 (警告閾値 7800 超、上限 8000 以下)
#     FAIL: <script>[ <arg>] / <case-id>: additionalContext <N> 字 (上限 8000 超)
#     FAIL: <script>[ <arg>] / <case-id>: <fail-closed 規則に該当した理由>
#     ERROR: <前提エラーの説明>
#   すべての OK / WARN / FAIL に対象スクリプト・ケース ID (分岐)・実測値 (または不成立理由) を
#   含める。最終行は
#   `lint-payload-size.sh: OK (<ケース数> cases, <サイズ実測数> measured, <WARN 数> warnings)`
#   または `lint-payload-size.sh: FAIL`。
# - 文字数の定義: Unicode code point 数 (`wc -m` を UTF-8 ロケールで実行した値と同じ)。
#   ロケール設定に依存させないため jq の `length` (文字列に対しては code point 数を返す) で
#   数える。
# - 動作環境: CI (ubuntu-latest) での動作を必須とし、POSIX 準拠の記法のみを用いて Linux /
#   macOS の sh・bash のどちらでも実行できるようにする (配列・[[ ]]・mapfile 等を使わない)。
#   jq と git (check-uncommitted-on-session-start.sh の fixture 作成に使う) に依存し、欠落時は
#   ERROR (exit 1)。対象スクリプトは shebang に依らず `bash <script> [<arg>]` で実行する。
#
# ============================================================================
# 判定 (2 段階閾値)
# ============================================================================
#
# 対応表で「出力あり」と定義されたケースの要素文字数 N について:
#   - N > PAYLOAD_LIMIT_CHARS (8000)                       → FAIL (exit 1 に寄与)
#   - PAYLOAD_WARN_CHARS (7800) < N <= PAYLOAD_LIMIT_CHARS → WARN (exit code に影響しない)
#   - N <= PAYLOAD_WARN_CHARS                              → OK
#
# ============================================================================
# fail-closed 規則 (いずれも FAIL。サイズ 0 として pass させない)
# ============================================================================
#
# ケース単位:
#   1. 対象スクリプトの exit code が 0 以外 (注入スクリプトは常に exit 0 の契約のため)
#   2. 期待 = 出力あり で、stdout が空 (空白のみを含む)
#   3. 期待 = 出力あり で、stdout が 1 個の JSON として parse できない
#   4. 期待 = 出力あり で、`.hookSpecificOutput.additionalContext` が存在しない・文字列でない
#   5. 期待 = 出力あり で、`.hookSpecificOutput.additionalContext` が空文字列
#   6. 期待 = 出力なし で、stdout が空でない (対応表とスクリプト挙動の乖離検知)
#   7. 分岐の前提 (配送済みマーカー・fixture リポジトリ) の作成に失敗した
#
# 対応表と実装の乖離 (前段の整合検査。1 件でも不成立なら各ケースの実行結果に関わらず FAIL):
#   8. hooks.json に type: command で登録された全エントリ (script basename と args の組) の
#      集合が、CASE_TABLE に現れる (script, arg) の集合と EXCLUDED_SCRIPTS の和集合に一致する
#      こと。hooks.json 側にだけ在る (対応表への追加漏れ) / 対応表側にだけ在る (登録解除・
#      改名への追従漏れ) のどちらも FAIL とし、差分を列挙する
#   9. EXCLUDED_SCRIPTS の各スクリプト本文に文字列 `additionalContext` が含まれないこと
#      (除外理由「additionalContext を出力しない」が崩れた場合の検知)
#  10. CASE_TABLE の各ケースの hook_event_name が、hooks.json で当該 (script, arg) が登録
#      されている event のいずれかであること (模擬 input の event 不備の検知)
#  11. CASE_TABLE に現れる各 (script, arg) について、EXPECT が output または
#      output-if-temporary のケースが 1 件以上あること (サイズを測る分岐を持たない対象を
#      作らない。temporary md が 0 件の状態は暫定ルール撤去後の正常状態であり、その場合の
#      output-if-temporary ケースは出力なしの確認として pass させる)
#  12. CASE_TABLE の各行が `|` 区切りでちょうど 6 フィールドを持ち (INPUT_JSON に `|` を
#      含めない)、CASE_ID が英数字と `._-` のみで一意、SCRIPT が hooks/scripts/ 直下に実在し、
#      EXPECT と前提トークンが既知の値で、INPUT_JSON が空でない hook_event_name を持つ JSON
#      object であること。不成立の行は実行しない
#
# ============================================================================
# 実行環境の隔離
# ============================================================================
#
# - 起動時に `mktemp -d` で隔離ディレクトリ LINT_TMPDIR を作り、EXIT の trap で終了時に
#   `rm -rf` する (INT / TERM / HUP は exit 1 に変換して EXIT の trap を通す)。mktemp -d 自体が
#   失敗した場合は ERROR (exit 1)。
# - ケースごとに LINT_TMPDIR/case-<case-id>/ を新規作成し、そのディレクトリを TMPDIR として
#   env 指定して対象スクリプトを実行する。これにより各スクリプトのマーカー
#   (`${TMPDIR}/agent-discipline-state/` と `${TMPDIR}/agent-discipline-markers/`) はケースごとに
#   空の状態から始まり、ケース間でマーカーが漏れず、実システムの
#   `${TMPDIR:-/tmp}/agent-discipline-state` には読み書きしない。
# - 対象スクリプトの実行時は CLAUDE_PLUGIN_ROOT を空文字列で渡す (対象スクリプトは prompts
#   ディレクトリをスクリプト位置基準で解決する。呼び出し元の環境に依存させないため)。
# - 実行時の cwd はリポジトリルートとし、対象スクリプトは絶対パスで起動する (prompts
#   ディレクトリの実パス行が実運用と同じく絶対パスになるようにするため)。
# - ロケールは呼び出し元の設定をそのまま対象スクリプトへ引き継ぐ。inject-always.sh の
#   ランタイム 8K ガードは `wc -m` で計測するため、実運用と同じ判定にするには UTF-8 ロケールで
#   実行する (CI の ubuntu-latest は既定で C.UTF-8)。lint 自身の sort / comm だけは
#   LC_ALL=C を個別に指定してバイト順に固定する。
#
# ============================================================================
# スコープ外
# ============================================================================
#
# - 実行環境依存の可変部 (inject-always.sh が埋め込む prompts ディレクトリの絶対パス、
#   check-uncommitted-on-session-start.sh が埋め込む cwd パスと git status 行) の長さは、
#   lint 実行環境のパスと fixture で測った値のみを検査する。
#   極端に長いインストールパスへの防御は inject-always.sh のランタイム 8K ガードの責務とする。
# - inject-always.sh はランタイム 8K ガード (実パス行 → delivery-note の順に落とす縮退) を
#   適用した後の実出力を測る。
# - 同一 session での複数 event の連続実行 (SessionStart → UserPromptSubmit の順序依存) は
#   模擬しない。前提はケースごとに配送済みマーカーを直接配置して成立させる。

set -u

# ============================================================================
# 閾値
# ============================================================================

# 要素文字数がこの値を超え PAYLOAD_LIMIT_CHARS 以下なら WARN。
PAYLOAD_WARN_CHARS=7800
# 要素文字数がこの値を超えたら FAIL。
PAYLOAD_LIMIT_CHARS=8000

# ============================================================================
# パス
# ============================================================================

HOOK_SCRIPTS_DIR="plugins/agent-discipline/hooks/scripts"
HOOKS_JSON="plugins/agent-discipline/hooks/hooks.json"
TEMPORARY_PROMPTS_DIR="plugins/agent-discipline/hooks/prompts/temporary"

# ============================================================================
# 模擬 input の共通値
# ============================================================================

# 全ケース共通の session_id (各スクリプトの sanitize で変化しない文字のみで構成する)。
LINT_SESSION_ID="lint-payload-size"

# ============================================================================
# 検査対象外スクリプト
# ============================================================================
#
# hooks.json に type: command で登録されているが additionalContext を出力しないスクリプト。
# 1 行 1 スクリプト (basename)。整合検査 8・9 で使う。
#   - block-fable-subagent.sh: PreToolUse (Agent|Task) で permissionDecision (deny) のみを返す
EXCLUDED_SCRIPTS="block-fable-subagent.sh"

# ============================================================================
# 対象スクリプト × 分岐 × 期待の対応表
# ============================================================================
#
# 1 行 1 ケース、`|` 区切りの 6 フィールド:
#
#   CASE_ID|SCRIPT|ARG|PRECONDITIONS|EXPECT|INPUT_JSON
#
#   CASE_ID        ケース ID (<対象の略称>.<分岐>)。ケース用 TMPDIR 名とメッセージに使う
#   SCRIPT         hooks/scripts/ 直下のスクリプト basename
#   ARG            スクリプトに渡す引数 1 個。引数なしは `-`
#   PRECONDITIONS  分岐を成立させる前提。空白区切りのトークン列、前提なしは `-`
#                  (下記「前提トークン」参照)。ケース用 TMPDIR 配下に作成してから実行する
#   EXPECT         output              … 要素を 1 個出力する (サイズ判定の対象)
#                  none                … 何も出力しない
#                  output-if-temporary … temporary md (TEMPORARY_PROMPTS_DIR/*.md のうち
#                                        `[ -n "$(cat <file>)" ]` が真のもの) が 1 件以上なら
#                                        output、0 件なら none (inject-temporary.sh の実在
#                                        ファイル連結を現物で検査するため)
#   INPUT_JSON     stdin に与える模擬 hook input JSON (1 行)。値全体が次のプレースホルダで
#                  ある JSON 文字列は、実行前に jq で実パスへ置換する:
#                    @CWD@        → <ケース用 TMPDIR>/repo
#
# 前提トークン (<SID> = LINT_SESSION_ID、<STATE> = <ケース用 TMPDIR>/agent-discipline-state):
#
#   rules-marker=<n>           <STATE>/delivered-rules-<n>-<SID> を空ファイルで作る (<n> は 2 / 3。
#                              inject-rules-part.sh <n> の配送済みマーカー)
#   discipline-marker=<value>  <STATE>/delivered-discipline-<SID> に <value> を書く
#                              (inject-discipline.sh の配送済みマーカー)
#   git-dirty-repo             <ケース用 TMPDIR>/repo に git init した work tree を作り、
#                              untracked ファイル 1 個 (untracked.txt) を置く
#
# 分岐の網羅方針: 要素を出力しうる全分岐を output で持つ。出力しない分岐は、対応表と
# スクリプト挙動の乖離検知を兼ねて代表的なものを none で持つ。
#
# 分岐ごとの注入内容 (対応表の読み方の補足):
#   inject-always.sh           SessionStart。自己修復指示 + delivery-note + 実パス行 +
#                              always-1.md (モデルに依らず同一)
#   inject-rules-part.sh 2|3   UserPromptSubmit。配送済みマーカー無し → always-<n>.md、
#                              マーカーあり → 出力なし
#   inject-discipline.sh       UserPromptSubmit。配送済みマーカー無し → 見出し + discipline.md、
#                              マーカーあり → 出力なし
#   inject-temporary.sh        SessionStart / UserPromptSubmit (同 session 未配送) で
#                              temporary/*.md の連結
#   inject-subagent-rules.sh   SubagentStart。subagent-rules.md (分岐なし)
#   inject-auto.sh             UserPromptSubmit。permission_mode == auto → auto-mode.md、
#                              それ以外 → 出力なし
#   check-uncommitted-on-session-start.sh
#                              UserPromptSubmit。permission_mode == auto かつ cwd が未コミット
#                              変更のある git work tree → uncommitted-check.md に cwd と
#                              git status 行を埋めた本文、それ以外 → 出力なし

CASE_TABLE=$(cat <<EOF
always.session-start|inject-always.sh|-|-|output|{"hook_event_name":"SessionStart","session_id":"${LINT_SESSION_ID}"}
part2.first|inject-rules-part.sh|2|-|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
part2.delivered|inject-rules-part.sh|2|rules-marker=2|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
part3.first|inject-rules-part.sh|3|-|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
part3.delivered|inject-rules-part.sh|3|rules-marker=3|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
discipline.first|inject-discipline.sh|-|-|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
discipline.delivered|inject-discipline.sh|-|discipline-marker=delivered|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
temporary.session-start|inject-temporary.sh|-|-|output-if-temporary|{"hook_event_name":"SessionStart","session_id":"${LINT_SESSION_ID}"}
temporary.first-prompt|inject-temporary.sh|-|-|output-if-temporary|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
subagent.any|inject-subagent-rules.sh|-|-|output|{"hook_event_name":"SubagentStart","session_id":"${LINT_SESSION_ID}","agent_id":"lint-agent","agent_type":"general-purpose"}
auto.auto|inject-auto.sh|-|-|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}","permission_mode":"auto"}
auto.default|inject-auto.sh|-|-|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}","permission_mode":"default"}
uncommitted.auto-dirty|check-uncommitted-on-session-start.sh|-|git-dirty-repo|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}","permission_mode":"auto","cwd":"@CWD@"}
uncommitted.default-dirty|check-uncommitted-on-session-start.sh|-|git-dirty-repo|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}","permission_mode":"default","cwd":"@CWD@"}
EOF
)

# ============================================================================
# pre-flight: 引数 / 実行位置 / 依存コマンド / 隔離ディレクトリ
# ============================================================================

if [ "$#" -ne 0 ]; then
  echo "ERROR: lint-payload-size.sh は引数を取りません (渡された引数: $*)。リポジトリルートから引数なしで実行してください。" >&2
  exit 1
fi

if [ ! -d "$HOOK_SCRIPTS_DIR" ] || [ ! -f "$HOOKS_JSON" ]; then
  echo "ERROR: $HOOK_SCRIPTS_DIR または $HOOKS_JSON が見つかりません。リポジトリルートから実行してください。" >&2
  exit 1
fi

for required_command in jq git bash; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "ERROR: $required_command が見つかりません。インストールしてから再実行してください。" >&2
    exit 1
  fi
done

REPO_ROOT=$(pwd -P) || {
  echo "ERROR: カレントディレクトリの絶対パスを取得できません。" >&2
  exit 1
}

LINT_TMPDIR=$(mktemp -d 2>/dev/null) || {
  echo "ERROR: 一時ディレクトリの作成 (mktemp -d) に失敗しました。" >&2
  exit 1
}
trap 'rm -rf "$LINT_TMPDIR"' EXIT
trap 'exit 1' INT TERM HUP

TABLE_FILE="$LINT_TMPDIR/case-table.txt"
VALID_CASES_FILE="$LINT_TMPDIR/valid-cases.txt"
CASE_IDS_FILE="$LINT_TMPDIR/case-ids.txt"
REGISTERED_FILE="$LINT_TMPDIR/registered-entries.txt"
EXCLUDED_FILE="$LINT_TMPDIR/excluded-scripts.txt"

overall_fail=0
warn_count=0
measured_count=0

# $1 = FAIL メッセージ本文。stderr に出力し、全体結果を FAIL にする。
report_fail() {
  echo "FAIL: $1" >&2
  overall_fail=1
}

# temporary md のうち、inject-temporary.sh が注入対象にするもの (本文が空でない .md) の件数。
temporary_count=0
for temporary_file in "$TEMPORARY_PROMPTS_DIR"/*.md; do
  [ -f "$temporary_file" ] || continue
  temporary_body=$(cat "$temporary_file" 2>/dev/null)
  [ -n "$temporary_body" ] || continue
  temporary_count=$((temporary_count + 1))
done

if ! printf '%s\n' "$CASE_TABLE" > "$TABLE_FILE"; then
  echo "ERROR: 対応表を一時ファイルに書き出せませんでした。" >&2
  exit 1
fi

# ============================================================================
# check 1: 対応表の形式 (fail-closed 規則 12)
# ============================================================================

echo "== check 1: case table format =="

# $1 = 前提トークン 1 個。既知のトークンなら 0 を返す。
is_known_precondition() {
  case "$1" in
    git-dirty-repo | rules-marker=2 | rules-marker=3) return 0 ;;
    discipline-marker=?*) return 0 ;;
    *) return 1 ;;
  esac
}

: > "$VALID_CASES_FILE"
: > "$CASE_IDS_FILE"
table_line_no=0
table_format_fail=0
while IFS= read -r table_row || [ -n "$table_row" ]; do
  table_line_no=$((table_line_no + 1))
  row_problems=""

  field_count=$(printf '%s\n' "$table_row" | awk -F'|' '{ print NF }')
  if [ "$field_count" != "6" ]; then
    report_fail "対応表 ${table_line_no} 行目: フィールド数が 6 ではありません (${field_count}): $table_row"
    table_format_fail=1
    continue
  fi

  IFS='|' read -r c_id c_script c_arg c_pre c_expect c_json <<ROW
$table_row
ROW

  case "$c_id" in
    '' | *[!A-Za-z0-9._-]*) row_problems="$row_problems CASE_ID が不正 ($c_id);" ;;
    *)
      if grep -Fqx "$c_id" "$CASE_IDS_FILE"; then
        row_problems="$row_problems CASE_ID が重複 ($c_id);"
      fi
      ;;
  esac
  case "$c_script" in
    '' | */*) row_problems="$row_problems SCRIPT が不正 ($c_script);" ;;
    *)
      if [ ! -f "$HOOK_SCRIPTS_DIR/$c_script" ]; then
        row_problems="$row_problems SCRIPT が $HOOK_SCRIPTS_DIR/ に実在しない ($c_script);"
      fi
      ;;
  esac
  if [ -z "$c_arg" ]; then
    row_problems="$row_problems ARG が空 (引数なしは -);"
  fi
  if [ "$c_pre" != "-" ]; then
    if [ -z "$c_pre" ]; then
      row_problems="$row_problems PRECONDITIONS が空 (前提なしは -);"
    fi
    for pre_token in $c_pre; do
      if ! is_known_precondition "$pre_token"; then
        row_problems="$row_problems 未知の前提トークン ($pre_token);"
      fi
    done
  fi
  case "$c_expect" in
    output | none | output-if-temporary) ;;
    *) row_problems="$row_problems 未知の EXPECT ($c_expect);" ;;
  esac
  if ! printf '%s' "$c_json" | jq -e 'type == "object" and ((.hook_event_name | type) == "string") and ((.hook_event_name | length) > 0)' >/dev/null 2>&1; then
    row_problems="$row_problems INPUT_JSON が hook_event_name を持つ JSON object ではない;"
  fi

  printf '%s\n' "$c_id" >> "$CASE_IDS_FILE"
  if [ -n "$row_problems" ]; then
    report_fail "対応表 ${table_line_no} 行目:${row_problems}"
    table_format_fail=1
    continue
  fi
  printf '%s\n' "$table_row" >> "$VALID_CASES_FILE"
done < "$TABLE_FILE"

if [ ! -s "$VALID_CASES_FILE" ]; then
  echo "ERROR: 対応表に実行可能なケースが 1 件もありません。" >&2
  exit 1
fi
if [ "$table_format_fail" -eq 0 ]; then
  echo "OK: 対応表 ${table_line_no} ケースの形式が正しいです"
fi

# ============================================================================
# check 2: 対応表と hooks.json の整合 (fail-closed 規則 8〜11)
# ============================================================================

echo ""
echo "== check 2: case table <-> hooks.json consistency =="

# hooks.json の type: command エントリを `<event>|<script basename>|<args>` (args なしは `-`、
# 複数 args は空白区切り) の 1 行 1 エントリで抽出する。
if ! jq -r '
  .hooks | to_entries[] | .key as $event | .value[] | .hooks[]?
  | select(.type == "command")
  | [ $event,
      (.command | sub("^.*/"; "")),
      (if ((.args // []) | length) == 0 then "-" else (.args | join(" ")) end) ]
  | join("|")
' "$HOOKS_JSON" > "$REGISTERED_FILE" 2>/dev/null; then
  echo "ERROR: $HOOKS_JSON の type: command エントリを抽出できませんでした (.hooks の構造が想定と異なる可能性があります)。" >&2
  exit 1
fi
if [ ! -s "$REGISTERED_FILE" ]; then
  echo "ERROR: $HOOKS_JSON に type: command エントリが 1 件もありません。" >&2
  exit 1
fi

consistency_fail=0

# 規則 9 と、除外スクリプトの hooks.json 登録確認 (規則 8 の和集合の片側)。
: > "$EXCLUDED_FILE"
for excluded_script in $EXCLUDED_SCRIPTS; do
  printf '%s\n' "$excluded_script" >> "$EXCLUDED_FILE"
  if ! cut -d'|' -f2 "$REGISTERED_FILE" | grep -Fqx "$excluded_script"; then
    report_fail "除外スクリプト $excluded_script が $HOOKS_JSON に登録されていません (EXCLUDED_SCRIPTS の更新漏れ)"
    consistency_fail=1
  fi
  if [ ! -f "$HOOK_SCRIPTS_DIR/$excluded_script" ]; then
    report_fail "除外スクリプト $excluded_script が $HOOK_SCRIPTS_DIR/ に実在しません"
    consistency_fail=1
  elif grep -q 'additionalContext' "$HOOK_SCRIPTS_DIR/$excluded_script"; then
    report_fail "除外スクリプト $excluded_script が additionalContext に言及しています (除外理由「additionalContext を出力しない」が崩れた可能性。対応表へ移してください)"
    consistency_fail=1
  fi
done

# 規則 8: hooks.json の (script, arg) 集合 - 除外 と 対応表の (script, arg) 集合の一致。
awk -F'|' 'FILENAME == ARGV[1] { excluded[$0] = 1; next } !($2 in excluded) { print $2 "|" $3 }' \
  "$EXCLUDED_FILE" "$REGISTERED_FILE" | LC_ALL=C sort -u > "$LINT_TMPDIR/registered-targets.txt"
awk -F'|' '{ print $2 "|" $3 }' "$VALID_CASES_FILE" | LC_ALL=C sort -u > "$LINT_TMPDIR/table-targets.txt"

awk -F'|' 'FILENAME == ARGV[1] { excluded[$0] = 1; next } ($1 in excluded) { print $1 }' \
  "$EXCLUDED_FILE" "$LINT_TMPDIR/table-targets.txt" | LC_ALL=C sort -u > "$LINT_TMPDIR/table-excluded.txt"
if [ -s "$LINT_TMPDIR/table-excluded.txt" ]; then
  report_fail "EXCLUDED_SCRIPTS のスクリプトが対応表にも含まれています:"
  sed 's/^/  - /' "$LINT_TMPDIR/table-excluded.txt" >&2
  consistency_fail=1
fi

LC_ALL=C comm -23 "$LINT_TMPDIR/registered-targets.txt" "$LINT_TMPDIR/table-targets.txt" > "$LINT_TMPDIR/only-registered.txt"
LC_ALL=C comm -13 "$LINT_TMPDIR/registered-targets.txt" "$LINT_TMPDIR/table-targets.txt" > "$LINT_TMPDIR/only-table.txt"
if [ -s "$LINT_TMPDIR/only-registered.txt" ]; then
  report_fail "$HOOKS_JSON に登録されているが対応表にも EXCLUDED_SCRIPTS にも無い (script|arg) があります (対応表への追加漏れ):"
  sed 's/^/  - /' "$LINT_TMPDIR/only-registered.txt" >&2
  consistency_fail=1
fi
if [ -s "$LINT_TMPDIR/only-table.txt" ]; then
  report_fail "対応表にあるが $HOOKS_JSON に登録されていない (script|arg) があります (登録解除・改名への追従漏れ):"
  sed 's/^/  - /' "$LINT_TMPDIR/only-table.txt" >&2
  consistency_fail=1
fi

# 規則 10: 各ケースの hook_event_name が、当該 (script, arg) の登録 event であること。
while IFS='|' read -r c_id c_script c_arg c_pre c_expect c_json; do
  case_event=$(printf '%s' "$c_json" | jq -r '.hook_event_name' 2>/dev/null)
  if ! grep -Fqx "$case_event|$c_script|$c_arg" "$REGISTERED_FILE"; then
    report_fail "$c_script / $c_id: hook_event_name ($case_event) で $c_script (arg: $c_arg) は $HOOKS_JSON に登録されていません (模擬 input の event 不備)"
    consistency_fail=1
  fi
done < "$VALID_CASES_FILE"

# 規則 11: 各 (script, arg) に、サイズを測るケース (output / output-if-temporary) があること。
while IFS= read -r table_target; do
  if ! awk -F'|' -v target="$table_target" \
    '($2 "|" $3) == target && ($5 == "output" || $5 == "output-if-temporary") { found = 1 } END { exit found ? 0 : 1 }' \
    "$VALID_CASES_FILE"; then
    report_fail "対応表の $table_target に EXPECT が output (または output-if-temporary) のケースがありません (サイズを測らない対象)"
    consistency_fail=1
  fi
done < "$LINT_TMPDIR/table-targets.txt"

if [ "$consistency_fail" -eq 0 ]; then
  target_count=$(wc -l < "$LINT_TMPDIR/table-targets.txt" | tr -d ' ')
  echo "OK: 対応表の対象 ${target_count} 組 (script|arg) と $HOOKS_JSON の type: command エントリ (EXCLUDED_SCRIPTS を除く) が一致しました"
fi

# ============================================================================
# check 3: 各ケースの実行と要素サイズの実測 (fail-closed 規則 1〜7、2 段階閾値)
# ============================================================================

echo ""
echo "== check 3: additionalContext size per case (warn > ${PAYLOAD_WARN_CHARS}, fail > ${PAYLOAD_LIMIT_CHARS}) =="
echo "(temporary md: ${temporary_count} 件)"

# $1 = ケース用 TMPDIR、$2 = 前提トークン列 (`-` = なし)。前提をすべて作成できたら 0 を返す。
setup_preconditions() {
  setup_dir=$1
  setup_state_dir="$setup_dir/agent-discipline-state"
  if ! mkdir -p "$setup_state_dir" 2>/dev/null; then
    return 1
  fi
  if [ "$2" = "-" ]; then
    return 0
  fi
  for setup_token in $2; do
    case "$setup_token" in
      rules-marker=*)
        : 2>/dev/null > "$setup_state_dir/delivered-rules-${setup_token#rules-marker=}-$LINT_SESSION_ID" || return 1
        ;;
      discipline-marker=*)
        printf '%s' "${setup_token#discipline-marker=}" 2>/dev/null > "$setup_state_dir/delivered-discipline-$LINT_SESSION_ID" || return 1
        ;;
      git-dirty-repo)
        git init -q "$setup_dir/repo" >/dev/null 2>&1 < /dev/null || return 1
        : 2>/dev/null > "$setup_dir/repo/untracked.txt" || return 1
        ;;
      *)
        return 1
        ;;
    esac
  done
  return 0
}

while IFS='|' read -r c_id c_script c_arg c_pre c_expect c_json; do
  case_label="$c_script"
  if [ "$c_arg" != "-" ]; then
    case_label="$c_script $c_arg"
  fi
  case_label="$case_label / $c_id"

  case_dir="$LINT_TMPDIR/case-$c_id"
  case_stdout="$LINT_TMPDIR/stdout-$c_id"
  case_stderr="$LINT_TMPDIR/stderr-$c_id"

  # 規則 7: 前提の作成
  if ! setup_preconditions "$case_dir" "$c_pre"; then
    report_fail "$case_label: 分岐の前提 ($c_pre) を作成できませんでした"
    continue
  fi

  case_json=$(printf '%s' "$c_json" | jq -c \
    --arg cwd "$case_dir/repo" \
    'walk(if . == "@CWD@" then $cwd else . end)' 2>/dev/null)
  if [ -z "$case_json" ]; then
    report_fail "$case_label: 模擬 hook input のプレースホルダを置換できませんでした"
    continue
  fi

  if [ "$c_arg" = "-" ]; then
    printf '%s' "$case_json" \
      | env TMPDIR="$case_dir" CLAUDE_PLUGIN_ROOT= bash "$REPO_ROOT/$HOOK_SCRIPTS_DIR/$c_script" \
        > "$case_stdout" 2> "$case_stderr"
  else
    printf '%s' "$case_json" \
      | env TMPDIR="$case_dir" CLAUDE_PLUGIN_ROOT= bash "$REPO_ROOT/$HOOK_SCRIPTS_DIR/$c_script" "$c_arg" \
        > "$case_stdout" 2> "$case_stderr"
  fi
  case_rc=$?

  # 規則 1: exit code
  if [ "$case_rc" -ne 0 ]; then
    report_fail "$case_label: 対象スクリプトが exit $case_rc で終了しました (注入スクリプトは常に exit 0 の契約)"
    continue
  fi

  has_output=0
  if [ -n "$(tr -d ' \t\r\n' < "$case_stdout")" ]; then
    has_output=1
  fi

  effective_expect="$c_expect"
  if [ "$c_expect" = "output-if-temporary" ]; then
    if [ "$temporary_count" -gt 0 ]; then
      effective_expect="output"
    else
      effective_expect="none"
    fi
  fi

  # 規則 6: 出力なしの期待
  if [ "$effective_expect" = "none" ]; then
    if [ "$has_output" -eq 1 ]; then
      report_fail "$case_label: 出力あり (対応表の期待: 出力なし)"
    else
      echo "OK: $case_label: 出力なし (対応表の期待どおり)"
    fi
    continue
  fi

  # 規則 2: 出力ありの期待で出力なし
  if [ "$has_output" -eq 0 ]; then
    report_fail "$case_label: 出力なし (対応表の期待: 出力あり)"
    continue
  fi

  # 規則 3: 1 個の JSON として parse できること
  if ! jq -e -s 'length == 1' < "$case_stdout" >/dev/null 2>&1; then
    report_fail "$case_label: 出力が 1 個の JSON として parse できません"
    continue
  fi

  # 規則 4: additionalContext が文字列であること
  context_type=$(jq -r '.hookSpecificOutput.additionalContext | type' < "$case_stdout" 2>/dev/null)
  if [ "$context_type" != "string" ]; then
    report_fail "$case_label: .hookSpecificOutput.additionalContext が存在しないか文字列ではありません (type: ${context_type:-取得不能})"
    continue
  fi

  context_length=$(jq -r '.hookSpecificOutput.additionalContext | length' < "$case_stdout" 2>/dev/null)
  case "$context_length" in
    '' | *[!0-9]*)
      report_fail "$case_label: additionalContext の文字数を取得できませんでした (${context_length})"
      continue
      ;;
  esac

  # 規則 5: 空文字列
  if [ "$context_length" -eq 0 ]; then
    report_fail "$case_label: additionalContext が空文字列です"
    continue
  fi

  measured_count=$((measured_count + 1))
  if [ "$context_length" -gt "$PAYLOAD_LIMIT_CHARS" ]; then
    report_fail "$case_label: additionalContext ${context_length} 字 (上限 ${PAYLOAD_LIMIT_CHARS} 超)"
  elif [ "$context_length" -gt "$PAYLOAD_WARN_CHARS" ]; then
    echo "WARN: $case_label: additionalContext ${context_length} 字 (警告閾値 ${PAYLOAD_WARN_CHARS} 超、上限 ${PAYLOAD_LIMIT_CHARS} 以下)" >&2
    warn_count=$((warn_count + 1))
  else
    echo "OK: $case_label: additionalContext ${context_length} 字"
  fi
done < "$VALID_CASES_FILE"

# ============================================================================
# 結果
# ============================================================================

case_count=$(wc -l < "$VALID_CASES_FILE" | tr -d ' ')

echo ""
if [ "$overall_fail" -ne 0 ]; then
  echo "lint-payload-size.sh: FAIL" >&2
  exit 1
fi

echo "lint-payload-size.sh: OK (${case_count} cases, ${measured_count} measured, ${warn_count} warnings)"
exit 0
