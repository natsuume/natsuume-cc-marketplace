#!/bin/sh
# lint-payload-size.sh
#
# experimental-agent-discipline plugin の注入スクリプト (hooks/scripts/ 配下で additionalContext を出力する
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
# - 実行位置: リポジトリルートから実行する (plugins/experimental-agent-discipline/... への相対パス参照を
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
#   含める。最終行は `lint-payload-size.sh: OK (<ケース数> cases, <WARN 数> warnings)` または
#   `lint-payload-size.sh: FAIL`。
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
#   7. 分岐の前提 (state ファイル・transcript・fixture リポジトリ) の作成に失敗した
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
#  11. CASE_TABLE に現れる各 (script, arg) について、期待 = 出力あり のケースが 1 件以上
#      あること (サイズを 1 度も測らない対象を作らない。output-if-temporary は temporary md
#      が 1 件以上ある場合のみ出力ありとして数える)
#  12. CASE_TABLE の各行が 6 フィールドを持ち、EXPECT と前提トークンが既知の値であること
#
# ============================================================================
# 実行環境の隔離
# ============================================================================
#
# - 起動時に `mktemp -d` で隔離ディレクトリ LINT_TMPDIR を作り、`trap ... EXIT INT TERM HUP`
#   で終了時に `rm -rf` する。mktemp -d 自体が失敗した場合は ERROR (exit 1)。
# - ケースごとに LINT_TMPDIR/case-<case-id>/ を新規作成し、そのディレクトリを TMPDIR として
#   env 指定して対象スクリプトを実行する。これにより各スクリプトの state
#   (`${TMPDIR}/agent-discipline-state/` と `${TMPDIR}/agent-discipline-markers/`) はケースごとに
#   空の状態から始まり、ケース間で state が漏れず、実システムの
#   `${TMPDIR:-/tmp}/agent-discipline-state` には読み書きしない。
# - 対象スクリプトの実行時は CLAUDE_PLUGIN_ROOT を空文字列で渡す (update-model-on-switch.sh が
#   prompts ディレクトリをスクリプト位置基準で解決するようにし、呼び出し元の環境に依存させない)。
# - 実行時の cwd はリポジトリルートとする。
#
# ============================================================================
# スコープ外
# ============================================================================
#
# - 実行環境依存の可変部 (inject-always.sh / update-model-on-switch.sh が埋め込む prompts
#   ディレクトリの絶対パス、check-uncommitted-on-session-start.sh が埋め込む cwd パスと
#   git status 行) の長さは、lint 実行環境のパスと fixture で測った値のみを検査する。
#   極端に長いインストールパスへの防御は inject-always.sh のランタイム 8K ガードの責務とする。
# - inject-always.sh はランタイム 8K ガード (実パス行 → delivery-note の順に落とす縮退) を
#   適用した後の実出力を測る。
# - 同一 session での複数 event の連続実行 (SessionStart → UserPromptSubmit の順序依存) は
#   模擬しない。前提はケースごとに state ファイルを直接配置して成立させる。

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

HOOK_SCRIPTS_DIR="plugins/experimental-agent-discipline/hooks/scripts"
HOOKS_JSON="plugins/experimental-agent-discipline/hooks/hooks.json"
TEMPORARY_PROMPTS_DIR="plugins/experimental-agent-discipline/hooks/prompts/temporary"

# ============================================================================
# 模擬 input の共通値
# ============================================================================

# 全ケース共通の session_id (各スクリプトの sanitize で変化しない文字のみで構成する)。
LINT_SESSION_ID="lint-payload-size"

# モデル分岐の代表値。対象スクリプトはモデル ID を大文字小文字無視の部分一致
# ('fable' / 'opus') で分類し、それ以外 (sonnet を含む・その他) は同じ非 fable 扱いになる。
MODEL_FABLE="claude-fable-5-1"
MODEL_SONNET="claude-sonnet-5"
MODEL_OPUS="claude-opus-5-5"
MODEL_OTHER="claude-haiku-4-5"

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
#                    @TRANSCRIPT@ → <ケース用 TMPDIR>/transcript.jsonl
#                    @CWD@        → <ケース用 TMPDIR>/repo
#
# 前提トークン (<SID> = LINT_SESSION_ID、<STATE> = <ケース用 TMPDIR>/agent-discipline-state):
#
#   state=<model>              <STATE>/model-<SID> に <model> を書く (確定済みモデル)
#   pending                    <STATE>/pending-model-<SID> を空ファイルで作る (判定不能セッション)
#   discipline-marker=<value>  <STATE>/delivered-discipline-<SID> に <value> を書く
#                              (inject-discipline.sh のマーカー。sonnet-gate は判定不能時の
#                              自己ゲート付き分業規律を配送済みで one-shot 補正待ちの状態)
#   transcript=<model>         <ケース用 TMPDIR>/transcript.jsonl に main-chain assistant 行
#                              {"type":"assistant","message":{"model":"<model>"}} を 1 行書く
#   transcript=none            <ケース用 TMPDIR>/transcript.jsonl を空ファイルで作る
#                              (assistant 行がまだ無い状態)
#   git-dirty-repo             <ケース用 TMPDIR>/repo に git init した work tree を作り、
#                              untracked ファイル 1 個 (untracked.txt) を置く
#
# 分岐の網羅方針: 各スクリプトがコード上で区別するモデル分類 (fable / sonnet / opus /
# その他 / 判定不能) と one-shot 補正経路のうち、要素を出力しうる全分岐を output で持つ。
# 出力しない分岐は、対応表とスクリプト挙動の乖離検知を兼ねて代表的なものを none で持つ。
#
# 分岐ごとの注入内容 (対応表の読み方の補足):
#   inject-always.sh           SessionStart。fable → always-fable.md、sonnet / opus / その他 →
#                              always-sonnet-1.md、判定不能 → preamble-self-gate.md +
#                              always-sonnet-1.md。いずれも自己修復指示 + delivery-note +
#                              実パス行が先頭に付く
#   inject-rules-part.sh 2|3   UserPromptSubmit。state が fable → 出力なし、非 fable →
#                              always-sonnet-<n>.md、pending あり・state も pending も無い →
#                              part-self-gate.md + always-sonnet-<n>.md
#   inject-discipline.sh       UserPromptSubmit。マーカー無しで fable → 分業規律 Fable 版、
#                              opus → Opus 版、sonnet / その他 → Sonnet 版、pending あり・
#                              state も pending も無い → 自己ゲート付き Sonnet 版。マーカー
#                              sonnet-gate で fable / opus に確定 → one-shot 補正前置き + 各版、
#                              sonnet / その他に確定・pending 残存 → 出力なし
#   resolve-model-on-prompt.sh UserPromptSubmit。pending あり + transcript が fable → 常時ルール
#                              one-shot 補正 (自己修復指示 + 補正前置き + always-fable.md)、
#                              それ以外 → 出力なし
#   inject-temporary.sh        SessionStart / UserPromptSubmit (同 session 未配送) で
#                              temporary/*.md の連結
#   inject-subagent-rules.sh   SubagentStart。subagent-rules.md (分岐なし)
#   inject-auto.sh             UserPromptSubmit。permission_mode == auto → auto-mode.md、
#                              それ以外 → 出力なし
#   check-uncommitted-on-session-start.sh
#                              UserPromptSubmit。permission_mode == auto かつ cwd が未コミット
#                              変更のある git work tree → uncommitted-check.md に cwd と
#                              git status 行を埋めた本文、それ以外 → 出力なし
#   update-model-on-switch.sh  PostModelSwitch。Fable 境界をまたぐ切替、または pending が
#                              存在した → 確定版ルールの所在通知、それ以外 → 出力なし

CASE_TABLE=$(cat <<EOF
always.fable|inject-always.sh|-|-|output|{"hook_event_name":"SessionStart","session_id":"${LINT_SESSION_ID}","model":"${MODEL_FABLE}"}
always.sonnet|inject-always.sh|-|-|output|{"hook_event_name":"SessionStart","session_id":"${LINT_SESSION_ID}","model":"${MODEL_SONNET}"}
always.opus|inject-always.sh|-|-|output|{"hook_event_name":"SessionStart","session_id":"${LINT_SESSION_ID}","model":"${MODEL_OPUS}"}
always.other|inject-always.sh|-|-|output|{"hook_event_name":"SessionStart","session_id":"${LINT_SESSION_ID}","model":"${MODEL_OTHER}"}
always.unknown|inject-always.sh|-|-|output|{"hook_event_name":"SessionStart","session_id":"${LINT_SESSION_ID}"}
part2.fable|inject-rules-part.sh|2|state=${MODEL_FABLE}|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
part2.sonnet|inject-rules-part.sh|2|state=${MODEL_SONNET}|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
part2.opus|inject-rules-part.sh|2|state=${MODEL_OPUS}|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
part2.other|inject-rules-part.sh|2|state=${MODEL_OTHER}|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
part2.unknown|inject-rules-part.sh|2|pending|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
part2.no-state|inject-rules-part.sh|2|-|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
part3.fable|inject-rules-part.sh|3|state=${MODEL_FABLE}|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
part3.sonnet|inject-rules-part.sh|3|state=${MODEL_SONNET}|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
part3.opus|inject-rules-part.sh|3|state=${MODEL_OPUS}|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
part3.other|inject-rules-part.sh|3|state=${MODEL_OTHER}|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
part3.unknown|inject-rules-part.sh|3|pending|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
part3.no-state|inject-rules-part.sh|3|-|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
discipline.fable|inject-discipline.sh|-|state=${MODEL_FABLE}|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
discipline.sonnet|inject-discipline.sh|-|state=${MODEL_SONNET}|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
discipline.opus|inject-discipline.sh|-|state=${MODEL_OPUS}|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
discipline.other|inject-discipline.sh|-|state=${MODEL_OTHER}|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
discipline.unknown|inject-discipline.sh|-|pending|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
discipline.no-state|inject-discipline.sh|-|-|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
discipline.correct-fable|inject-discipline.sh|-|discipline-marker=sonnet-gate state=${MODEL_FABLE}|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
discipline.correct-opus|inject-discipline.sh|-|discipline-marker=sonnet-gate state=${MODEL_OPUS}|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
discipline.correct-sonnet|inject-discipline.sh|-|discipline-marker=sonnet-gate state=${MODEL_SONNET}|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
discipline.correct-other|inject-discipline.sh|-|discipline-marker=sonnet-gate state=${MODEL_OTHER}|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
discipline.correct-pending|inject-discipline.sh|-|discipline-marker=sonnet-gate pending|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
resolve.fable|resolve-model-on-prompt.sh|-|pending transcript=${MODEL_FABLE}|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}","transcript_path":"@TRANSCRIPT@"}
resolve.sonnet|resolve-model-on-prompt.sh|-|pending transcript=${MODEL_SONNET}|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}","transcript_path":"@TRANSCRIPT@"}
resolve.opus|resolve-model-on-prompt.sh|-|pending transcript=${MODEL_OPUS}|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}","transcript_path":"@TRANSCRIPT@"}
resolve.other|resolve-model-on-prompt.sh|-|pending transcript=${MODEL_OTHER}|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}","transcript_path":"@TRANSCRIPT@"}
resolve.no-assistant|resolve-model-on-prompt.sh|-|pending transcript=none|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}","transcript_path":"@TRANSCRIPT@"}
resolve.no-pending|resolve-model-on-prompt.sh|-|transcript=${MODEL_FABLE}|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}","transcript_path":"@TRANSCRIPT@"}
temporary.session-start|inject-temporary.sh|-|-|output-if-temporary|{"hook_event_name":"SessionStart","session_id":"${LINT_SESSION_ID}"}
temporary.first-prompt|inject-temporary.sh|-|-|output-if-temporary|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}"}
subagent.any|inject-subagent-rules.sh|-|-|output|{"hook_event_name":"SubagentStart","session_id":"${LINT_SESSION_ID}","agent_id":"lint-agent","agent_type":"general-purpose"}
auto.auto|inject-auto.sh|-|-|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}","permission_mode":"auto"}
auto.default|inject-auto.sh|-|-|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}","permission_mode":"default"}
uncommitted.auto-dirty|check-uncommitted-on-session-start.sh|-|git-dirty-repo|output|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}","permission_mode":"auto","cwd":"@CWD@"}
uncommitted.default-dirty|check-uncommitted-on-session-start.sh|-|git-dirty-repo|none|{"hook_event_name":"UserPromptSubmit","session_id":"${LINT_SESSION_ID}","permission_mode":"default","cwd":"@CWD@"}
switch.to-fable|update-model-on-switch.sh|-|-|output|{"hook_event_name":"PostModelSwitch","session_id":"${LINT_SESSION_ID}","from_model":"${MODEL_SONNET}","to_model":"${MODEL_FABLE}"}
switch.to-sonnet|update-model-on-switch.sh|-|-|output|{"hook_event_name":"PostModelSwitch","session_id":"${LINT_SESSION_ID}","from_model":"${MODEL_FABLE}","to_model":"${MODEL_SONNET}"}
switch.to-opus|update-model-on-switch.sh|-|-|output|{"hook_event_name":"PostModelSwitch","session_id":"${LINT_SESSION_ID}","from_model":"${MODEL_FABLE}","to_model":"${MODEL_OPUS}"}
switch.pending|update-model-on-switch.sh|-|pending|output|{"hook_event_name":"PostModelSwitch","session_id":"${LINT_SESSION_ID}","from_model":"${MODEL_SONNET}","to_model":"${MODEL_OTHER}"}
switch.no-notice|update-model-on-switch.sh|-|-|none|{"hook_event_name":"PostModelSwitch","session_id":"${LINT_SESSION_ID}","from_model":"${MODEL_SONNET}","to_model":"${MODEL_OPUS}"}
EOF
)

# ============================================================================
# 検査ロジック (未実装)
# ============================================================================

echo "lint-payload-size.sh: 未実装 (契約・閾値・対応表の定義のみ。検査ロジック本体は未実装)" >&2
exit 1
