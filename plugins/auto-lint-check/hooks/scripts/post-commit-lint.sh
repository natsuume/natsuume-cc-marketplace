#!/usr/bin/env bash
# post-commit-lint.sh
#
# PostToolUse / Bash で `git commit` を検出し、HEAD コミットの内容を
# ESLint / Ruff に流して lint する。`block-commit-lint.sh` (PreToolUse) が
# 何らかの理由で bypass された経路 (例: ユーザーが当該プラグインを一時無効化、
# subagent 起動など hook 制御範囲外の commit、Edit/Write 後の自動 format で
# 検出できない linter rule、etc.) を後追いでカバーするためのセーフティネット。
#
# PostToolUse の時点で commit は既に作成済み (rollback 不可) のため
# permissionDecision: "deny" は使えない。代わりに `{"decision": "block",
# "reason": "..."}` を返すことで、Claude のターン context に lint エラーを
# 注入し `commit --amend` 等の修正アクションを促す。tool 自体の実行を止め
# ないため "non-blocking" な feedback として機能する。
#
# policy: fail-open (non-blocking)
# 必須ツール (jq / python3 / git) が欠ける場合は silent skip する (non-blocking
# なので fail-closed する必要はない。エラーは stderr に log_warn で出す)。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

# 異常終了をユーザの stderr に可視化する (sibling と同型; #68)。non-blocking なので
# crash しても commit 自体は通るが、safety-net lint が走らなかったことに気づけるようにする。
# tmpfile 掃除もこのハンドラに集約する (後段で AUTO_LINT_CHECK_CLEANUP_FILE に登録)。
install_auto_lint_exit_trap "post-commit-lint" "block-commit-lint を bypass した経路への safety-net lint が走らない可能性があります。"

if ! command -v jq >/dev/null 2>&1; then
  log_warn "post-commit-lint: jq が見つからないため skip。"
  exit 0
fi
if ! command -v python3 >/dev/null 2>&1; then
  log_warn "post-commit-lint: python3 が見つからないため skip。"
  exit 0
fi

INPUT=$(cat)

# 高速パス: `git` と `commit` の両方を含まない入力は早期 exit。
case "$INPUT" in
  *git*commit*) ;;
  *) exit 0 ;;
esac

TOOL_NAME=$(extract_tool_name "$INPUT")
[ "$TOOL_NAME" = "Bash" ] || exit 0

# tool_response.exit_code は意図的に見ない。`git commit -m msg && git push`
# で push が失敗した場合でも Bash 全体の exit_code は非 0 となるため、
# exit_code をゲートに使うと「成功 commit の lint をすり抜ける」経路ができる。
# 代わりに「現在の HEAD を常に lint する」セマンティクスで動作し、commit
# 失敗時 (pre-commit reject 等) は前回 HEAD が再 lint されるだけ (副次効果)。

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -n "$COMMAND" ] || exit 0

# bash の `$(...)` trailing-LF trim で消えた `\<LF>` を復元してから parser に渡す。
# 詳細は block-commit-lint.sh の同位置コメントを参照。
case "$COMMAND" in *\\) COMMAND="${COMMAND}"$'\n' ;; esac

# parse-commit-command.py で実 commit invocation の存在を判定する。
# 行継続展開 / 改行→`;` 変換 / 安全な heredoc 除去は parser 側で行うため、
# raw command をそのまま渡す。詳細は block-commit-lint.sh の同じ呼び出し
# 箇所のコメントを参照。
# post 側では以下の exit code を扱う:
#   0  HAS_STAGING あり (commit invocation あり) → HEAD を lint
#   2  parse failure → fail-open で HEAD を lint (空振りしても害なし)
#   5  staging trigger なし (plain commit) → HEAD を lint
#   3  repo override (-C / --git-dir / GIT_DIR= / cd 等) → cwd repo の HEAD は
#      コミット対象 repo の HEAD と異なる可能性があるため、誤検出を避けて skip
#   4  実 commit が走らない (dry-run / help / 非 command position の git 等) → skip
python3 "$SCRIPT_DIR/lib/parse-commit-command.py" "$COMMAND"
PARSER_RC=$?
case "$PARSER_RC" in
  0|2|5) ;;       # 実 commit が cwd repo に対して走った → lint 続行
  3|4) exit 0 ;;  # 別 repo / 実 commit なし → skip
  *)
    log_warn "post-commit-lint: parser returned unexpected exit code $PARSER_RC. fail-open で続行。"
    ;;
esac

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
[ -n "$REPO_ROOT" ] || exit 0
cd "$REPO_ROOT" || exit 0

# HEAD が無い (初回 commit 失敗・空 repo) のケースは skip。
git rev-parse --verify HEAD >/dev/null 2>&1 || exit 0

TMP_INPUT=$(mktemp "${TMPDIR:-/tmp}/auto-lint-check.XXXXXX" 2>/dev/null)
if [ -z "$TMP_INPUT" ]; then
  log_warn "post-commit-lint: mktemp failed. skip."
  exit 0
fi
# tmpfile 掃除は install_auto_lint_exit_trap が張った EXIT trap に集約する。
AUTO_LINT_CHECK_CLEANUP_FILE="$TMP_INPUT"

# 現在の HEAD コミットの変更ファイルを head source として TMP_INPUT に書き出す。
# --diff-filter=ACMR: Added / Copied / Modified / Renamed のみ (Deleted /
#   Unmerged / type-changed は lint 対象外)。
# --root: 初回 commit (parentless) でも HEAD の全ファイルを列挙する。
# -m: merge commit (2 parent 以上) を各 parent との diff として出力する。
#     これがないと merge commit は空を返し、conflict 解決ファイルや片親から
#     持ち込まれたファイルが lint をすり抜ける。octopus merge (3+ parents)
#     ではパスが N 倍出力されるが、build-lint-plan.py が `{rel_path: {source}}`
#     で dedupe するため lint 実行回数は重複しない。
git diff-tree --no-commit-id --name-only -r -m --root HEAD --diff-filter=ACMR -z 2>/dev/null \
  | prepend_source_label head >> "$TMP_INPUT"

# 変更ファイルが 0 件 (merge commit で diff-tree が空 / lint 対象拡張子なし)
# の場合は TMP_INPUT も空になる。build-lint-plan.py は空入力で空 plan を返す
# だけなので、ここでは特別扱いせず素通しする。
PLAN_JSON=$(python3 "$SCRIPT_DIR/lib/build-lint-plan.py" < "$TMP_INPUT")
PLAN_RC=$?
if [ "$PLAN_RC" -ne 0 ]; then
  log_warn "post-commit-lint: build-lint-plan.py が exit $PLAN_RC で失敗。skip。"
  exit 0
fi

META_TSV=$(printf '%s' "$PLAN_JSON" | jq -r '.groups | to_entries[] | [.key, .value.linter, .value.label, .value.root] | @tsv' 2>/dev/null)
JQ_RC=$?
if [ "$JQ_RC" -ne 0 ]; then
  log_warn "post-commit-lint: build-lint-plan.py の出力 (PLAN_JSON) が parse できない。skip。"
  exit 0
fi
[ -n "$META_TSV" ] || exit 0

HAS_ERROR=0
COMBINED_OUTPUT=""

while IFS=$'\t' read -r IDX LINTER LABEL ROOT; do
  case "$LINTER" in
    eslint) resolve_eslint "$ROOT" || { log_warn "post-commit-lint: eslint config が $ROOT にあるが eslint バイナリが見つからない。skip"; continue; } ;;
    ruff)   resolve_ruff           || { log_warn "post-commit-lint: ruff config が $ROOT にあるが ruff バイナリが見つからない。skip";   continue; } ;;
  esac

  while IFS= read -r -d '' REL_PATH && IFS= read -r -d '' SOURCE; do
    [ -n "$REL_PATH" ] || continue
    case "$SOURCE" in
      head)
        # `git show HEAD:path` で HEAD コミットの blob を取得。末尾改行の
        # strip 回避は block-commit-lint.sh と同じ printf X センチネル方式。
        CONTENT_PADDED=$(git show "HEAD:$REL_PATH" 2>/dev/null && printf X)
        SRC_LABEL="HEAD commit"
        ;;
      *) continue ;;
    esac
    [ -n "$CONTENT_PADDED" ] || continue
    LINT_CONTENT="${CONTENT_PADDED%X}"
    ABS_PATH=$(normalize_path "$REL_PATH") || continue

    # 各イテレーションで RC を初期化し、未知 LINTER で stale RC を流用しないようにする (#66)。
    RC=0
    case "$LINTER" in
      eslint)
        LINT_OUT=$( (cd "$ROOT" && printf '%s' "$LINT_CONTENT" | "${ESLINT_CMD[@]}" --stdin --stdin-filename "$ABS_PATH") 2>&1 )
        RC=$?
        ;;
      ruff)
        LINT_OUT=$( (cd "$ROOT" && printf '%s' "$LINT_CONTENT" | "${RUFF_CMD[@]}" check --stdin-filename "$ABS_PATH" -) 2>&1 )
        RC=$?
        ;;
      *) continue ;;
    esac

    if [ "$RC" -ne 0 ]; then
      HAS_ERROR=1
      COMBINED_OUTPUT+=$'--- '"$REL_PATH"$' ('"$LABEL, $SRC_LABEL"$') ---\n'"$LINT_OUT"$'\n\n'
    fi
  done < <(printf '%s' "$PLAN_JSON" | jq -j --argjson i "$IDX" '.groups[$i].items[] | "\(.file)\u0000\(.source)\u0000"')
done <<< "$META_TSV"

if [ "$HAS_ERROR" -eq 1 ]; then
  HEAD_SHA=$(git rev-parse --short HEAD 2>/dev/null)
  REASON=$(printf '%s\n' \
    "現在の HEAD コミット (${HEAD_SHA:-unknown}) の対象ファイルに lint エラーがあります。" \
    "本フックは \`git commit\` を含む Bash 実行の **直後** に現 HEAD を再 lint します (block-commit-lint を bypass した経路への safety net)。HEAD が直前の commit 実行で動いていない場合 (pre-commit reject 等) でも、現 HEAD に lint エラーが残っていれば通知されます。" \
    "直前の commit が原因なら \`git commit --amend\` で差し替え、過去 commit が原因なら fix 用の commit を追加してください。" \
    "" \
    "$COMBINED_OUTPUT")
  jq -n --arg reason "$REASON" '{
    decision: "block",
    reason: $reason
  }'
fi

exit 0
