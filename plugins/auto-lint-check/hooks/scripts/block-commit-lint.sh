#!/usr/bin/env bash
# block-commit-lint.sh
#
# PreToolUse / Bash で `git commit` を検出し、commit 対象になるファイルを
# ESLint / Ruff に流して lint する。エラーがあれば commit を deny する。
#
# 本フックは Bash ツールの **実行前** に発火するため、同一コマンドの `git add`
# / `commit -a` がまだ走っていない時点で index を見ても lint をすり抜ける。
# これを避けるため、コマンド文字列を見て `git add` / `git stage` / `commit -a`
# / `--all` / `commit <pathspec>` を検出した場合 (HAS_STAGING=1) は staged
# だけでなく working tree の変更 (modified + untracked) も lint 対象に含め、
# ソースは working tree を読む。
#
# lint plan の構築 (ファイル → ソース集合のマッピング、(linter, config-root)
# でのグルーピング) は `lib/build-lint-plan.py` に委譲しており、本スクリプトは
# git からの入力収集と linter 実行の orchestration だけを担う。
#
# 必須ツール (jq, python3) が欠ける、もしくは想定外の状況で lint を実行
# できない場合は silent skip せず deny に倒す (fail closed)。silently skip
# すると lint が走らないまま commit が通る経路ができてしまうため。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

# jq-free な deny エミッタ。jq missing でも fail closed できるよう、
# emit_deny (jq に依存) より先に静的 heredoc で deny を出力する。
emit_deny_no_jq() {
  local reason="$1"
  # JSON-escape: " と \ をエスケープ、改行は \n。reason は本ファイル内の固定
  # 文字列のみ渡す前提なので、簡易エスケープで十分。
  local escaped="${reason//\\/\\\\}"
  escaped="${escaped//\"/\\\"}"
  cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "$escaped"
  }
}
EOF
  exit 0
}

INPUT=$(cat)

# 高速パス: jq / python3 起動前の粗フィルタ。`git` と `commit` の両方を含む
# 入力 (= 実 commit invocation の必要条件) でなければ抜ける。
# 注: substring match なので `commit-graph` や `--commit-msg` のような関連
# 語も拾うが、後段の shlex parser が正確に判定するため害はない。
case "$INPUT" in
  *git*commit*) ;;
  *) exit 0 ;;
esac

# jq / python3 のいずれが欠けても本フックは正しく lint できない。
# silent skip だと `git commit` を含む Bash で lint が走らないまま commit が
# 通る経路になるため fail closed (deny) する。
# 注: 先に jq check する。emit_deny は jq に依存するため、jq missing が
# 先に handle されていないと silent skip 経路ができてしまう。
if ! command -v jq >/dev/null 2>&1; then
  echo "[auto-lint-check] block-commit-lint requires jq. found nothing." >&2
  emit_deny_no_jq "auto-lint-check の block-commit-lint hook には jq が必要ですが見つかりません。jq をインストールするか、auto-lint-check プラグインを無効化してください。silently skip すると lint が走らないまま commit が通る経路になるため fail closed (deny) しています。"
fi
if ! command -v python3 >/dev/null 2>&1; then
  log_warn "block-commit-lint requires python3. found nothing."
  emit_deny "auto-lint-check の block-commit-lint hook には python3 が必要ですが見つかりません。python3 をインストールするか、auto-lint-check プラグインを無効化してください。silently skip すると lint が走らないまま commit が通る経路になるため fail closed (deny) しています。"
fi

TOOL_NAME=$(extract_tool_name "$INPUT")
[ "$TOOL_NAME" = "Bash" ] || exit 0

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -n "$COMMAND" ] || exit 0

# `lib/parse-commit-command.py` が shlex でシェルトークン化したコマンドを解析し、
# 行継続展開 / 改行→`;` 変換 / 安全な heredoc (`$(cat <<'DELIM' ... DELIM)`)
# の除去まで含めて parser 側で処理する。bash 側で先に改行を ``;`` に置換すると
# heredoc 構造 (delimiter は行頭にある必要がある) が壊れるため、 raw command を
# そのまま渡す。
#
# exit code で以下を返す (Python が SyntaxError / ImportError で返す 1 と衝突
# しないよう、正常 return code は 2 以上を使う):
#   0  HAS_STAGING (working tree も lint 対象に含めるべき)
#   5  commit はあるが staging trigger なし (staged blob のみ lint)
#   2  parse failure (安全側で HAS_STAGING=1 に倒す)
#   3  repo override (`-C` / `--git-dir` / `--work-tree` / `GIT_DIR=` / cd 等で
#      cwd と異なる repo に commit するため、silent に cwd を lint しないよう
#      deny)
#   4  実 commit が走らない (commit subcommand 不在 / `echo "git commit"` の
#      ような非 command position の git / `--dry-run` / `--help` 等)。skip
#   その他 (1 を含む): 想定外のエラー。default 分岐で fail-safe (HAS_STAGING=1)
#
# parser 呼び出しは `git rev-parse` の check より先に行う: hook 実行時の cwd
# が repo 外で `cd repo && git commit` のようなコマンドが来た場合、`rev-parse`
# 失敗で早期 exit 0 すると bypass になる。parser で repo override / sticky cd
# を fail closed しておけば、cwd が repo か否かに関わらず deny される。
#
# 過検出 (commit に含めない予定の編集まで lint) は許容する設計トレードオフ。
HAS_STAGING=0
python3 "$SCRIPT_DIR/lib/parse-commit-command.py" "$COMMAND"
PARSER_RC=$?
case "$PARSER_RC" in
  0|2) HAS_STAGING=1 ;;
  5) ;;  # commit はあるが staging trigger なし (staged blob のみ lint)
  3)
    # `-C` / `--git-dir` / `--work-tree` / `GIT_DIR=` / `cd dir &&` 等で repo
    # override する commit。本フックは cwd repo を見るため、別 repo を指す
    # 場合は silent に cwd を lint する経路、`git -C . commit` のように同一
    # repo を指す場合も exit 0 で skip すれば lint をすり抜ける経路になる。
    # 静的に同一性を判別できないため fail closed (deny) する。利用者は
    # 対象 repo に `cd` してから別の Bash 呼び出しで commit すれば通る。
    log_warn "block-commit-lint: repo override (-C / --git-dir / --work-tree / GIT_DIR= / cd 等) を伴う commit はサポート対象外。"
    emit_deny "auto-lint-check の block-commit-lint hook は repo override (\`git -C\` / \`--git-dir\` / \`--work-tree\` / \`GIT_DIR=\` / \`cd dir &&\` 等) を伴う commit をサポートしません。silent skip すると別 repo の lint を取り違える / 同一 repo でも lint を素通りさせる経路になるため fail closed (deny) しています。対象 repo に \`cd\` してから別の Bash 呼び出しで \`git commit\` を実行してください。"
    ;;
  4) exit 0 ;;  # 実 commit が走らない (dry-run / help / 非 command position の git 等)
  *)
    # 想定外の exit code (例: Python 3.6 以前の SyntaxError, import 失敗 等で
    # parser が exit 1 する経路)。silent skip だと lint が走らないまま commit
    # を通す経路になるため、安全側で HAS_STAGING=1 に倒して working tree も
    # 含めて lint する (over-detect は false-positive deny で気付ける)。
    log_warn "block-commit-lint: parser returned unexpected exit code $PARSER_RC. fail-safe to HAS_STAGING=1."
    HAS_STAGING=1
    ;;
esac

# parser で「実 commit が cwd の repo に対して走る」と判定された場合に、cwd が
# 実際に git repo かを確認する。repo 外で plain `git commit` は失敗するだけだが、
# 後段の `git diff --cached` などが noise を出すので silent exit 0 で抜ける。
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
[ -n "$REPO_ROOT" ] || exit 0
cd "$REPO_ROOT" || exit 0

# lint plan の入力 (NUL 区切り `<source>\t<rel_path>` レコード) を一時ファイル
# 経由で build-lint-plan.py に渡す。bash の shell variable は NUL を保持
# できないうえ、3 つの git source (staged / working diff / untracked) を 1 つの
# pipeline でも concat できないため、tmpfile に追記して 1 入力にまとめる。
TMP_INPUT=$(mktemp "${TMPDIR:-/tmp}/auto-lint-check.XXXXXX" 2>/dev/null)
if [ -z "$TMP_INPUT" ]; then
  log_warn "block-commit-lint: mktemp failed. cannot build lint plan."
  emit_deny "auto-lint-check の block-commit-lint hook が lint plan 用の一時ファイル作成に失敗しました。silently skip すると lint が走らないまま commit が通る経路になるため fail closed (deny) しています。"
fi
trap 'rm -f "$TMP_INPUT"' EXIT

git diff --cached --name-only --diff-filter=ACMR -z 2>/dev/null \
  | prepend_source_label staged >> "$TMP_INPUT"

if [ "$HAS_STAGING" -eq 1 ]; then
  git diff --name-only --diff-filter=ACMR -z 2>/dev/null \
    | prepend_source_label working >> "$TMP_INPUT"
  git ls-files --others --exclude-standard -z 2>/dev/null \
    | prepend_source_label working >> "$TMP_INPUT"
fi

# 設計判断: dual-membership (staged + working) のファイルは両方を lint する。
# `git add path && commit` のように当該 path が確実に再 stage されるケースでは
# 古い staged blob は committed されないため、staged 側の lint は over-detect
# (false-positive deny) になりうる。逆に `git add other && commit` のように
# 当該 path が再 stage されない場合は staged blob が committed されるため、
# working 側だけ lint すると真の lint エラーを見逃す (under-detect)。両者の
# 厳密な判別には `git add` の引数 pathspec resolution が必要。本実装では
# under-detect を避ける方を優先し、両ソースを lint する。dual-membership で
# false-positive deny が出た場合は commit 直前の re-stage を促す挙動として扱う。

PLAN_JSON=$(python3 "$SCRIPT_DIR/lib/build-lint-plan.py" < "$TMP_INPUT")
PLAN_RC=$?
if [ "$PLAN_RC" -ne 0 ]; then
  log_warn "block-commit-lint: build-lint-plan.py が exit $PLAN_RC で失敗。"
  emit_deny "auto-lint-check の block-commit-lint hook が lint plan の構築に失敗しました (build-lint-plan.py exit $PLAN_RC)。silently skip すると lint が走らないまま commit が通る経路になるため fail closed (deny) しています。"
fi

# group ごとの meta info (index, linter, label, root) を 1 回の jq で TSV 化して
# 取り出す。group ごとに jq を再起動するとレイテンシが嵩むため、items だけは
# group index で参照しながら個別取得する。
META_TSV=$(printf '%s' "$PLAN_JSON" | jq -r '.groups | to_entries[] | [.key, .value.linter, .value.label, .value.root] | @tsv' 2>/dev/null)
JQ_RC=$?
if [ "$JQ_RC" -ne 0 ]; then
  log_warn "block-commit-lint: build-lint-plan.py の出力 (PLAN_JSON) が parse できない。"
  emit_deny "auto-lint-check の block-commit-lint hook が lint plan JSON の parse に失敗しました。silently skip すると lint が走らないまま commit が通る経路になるため fail closed (deny) しています。"
fi
[ -n "$META_TSV" ] || exit 0

HAS_ERROR=0
COMBINED_OUTPUT=""

while IFS=$'\t' read -r IDX LINTER LABEL ROOT; do
  case "$LINTER" in
    eslint) resolve_eslint "$ROOT" || { log_warn "eslint config が $ROOT にあるが eslint バイナリが見つからない。skip"; continue; } ;;
    ruff)   resolve_ruff           || { log_warn "ruff config が $ROOT にあるが ruff バイナリが見つからない。skip";   continue; } ;;
  esac

  while IFS= read -r -d '' REL_PATH && IFS= read -r -d '' SOURCE; do
    [ -n "$REL_PATH" ] || continue
    # Lint 対象ソース: staged → `git show :path`, working → working tree。
    # `$()` の trailing newline strip を避けるため `&& printf X` センチネル
    # で末尾改行を保持する (ESLint `eol-last` / Ruff W292 の false positive 防止)。
    case "$SOURCE" in
      staged)
        CONTENT_PADDED=$(git show ":$REL_PATH" 2>/dev/null && printf X)
        SRC_LABEL="staged"
        ;;
      working)
        [ -f "$REL_PATH" ] || continue
        CONTENT_PADDED=$(cat "$REL_PATH" 2>/dev/null && printf X)
        SRC_LABEL="working tree"
        ;;
      *) continue ;;
    esac
    [ -n "$CONTENT_PADDED" ] || continue
    LINT_CONTENT="${CONTENT_PADDED%X}"
    ABS_PATH=$(normalize_path "$REL_PATH") || continue

    case "$LINTER" in
      eslint)
        LINT_OUT=$( (cd "$ROOT" && printf '%s' "$LINT_CONTENT" | "${ESLINT_CMD[@]}" --stdin --stdin-filename "$ABS_PATH") 2>&1 )
        RC=$?
        ;;
      ruff)
        LINT_OUT=$( (cd "$ROOT" && printf '%s' "$LINT_CONTENT" | "${RUFF_CMD[@]}" check --stdin-filename "$ABS_PATH" -) 2>&1 )
        RC=$?
        ;;
    esac

    if [ "$RC" -ne 0 ]; then
      HAS_ERROR=1
      COMBINED_OUTPUT+=$'--- '"$REL_PATH"$' ('"$LABEL, $SRC_LABEL"$') ---\n'"$LINT_OUT"$'\n\n'
    fi
  done < <(printf '%s' "$PLAN_JSON" | jq -j --argjson i "$IDX" '.groups[$i].items[] | "\(.file)\u0000\(.source)\u0000"')
done <<< "$META_TSV"

if [ "$HAS_ERROR" -eq 1 ]; then
  REASON=$(printf '%s\n' \
    "git commit を中断しました。commit 対象ファイルに lint エラーがあります。" \
    "本体のコードを修正してから再度 commit してください。" \
    "" \
    "$COMBINED_OUTPUT")
  emit_deny "$REASON"
fi

exit 0
