#!/bin/bash
# block-default-branch-commit.sh
# デフォルトブランチ (master/main) 上での `git commit` をブロックする PreToolUse フック。
#
# 「デフォルトブランチへの変更は GitHub 上の PR merge 経由のみ」という運用を構造強制する
# ための一段目。ローカル master で commit が走らなければ、push 段階で改めて止める必要も
# 減り、誤って master を進める経路を入口で塞げる。
#
# 検出対象:
#   - カレントブランチが master/main のときに実行される `git commit` 系コマンド
#   - 連結プレフィックス `xxx && git commit ...` / `cd /other && git commit ...` /
#     `git -C dir commit ...` / `GIT_DIR=... git commit ...` のような target-mismatch
#     経路は `has_target_mismatch_prefix` で本フック自身が deny に倒す (lib 経由の
#     自前防御で、pre-push-review プラグインへの依存はない)。
#
# detached HEAD (cherry-pick 中・rebase 中など) では通す: ブランチ名が空文字列で
# is_default_branch は false 判定になるため、自然に exit 0 経路に流れる。

INPUT=$(cat)

# 大半の Bash 呼び出しは無関係。jq 起動前に粗フィルタで抜ける。
case "$INPUT" in
  *commit*) ;;
  *) exit 0 ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -n "$COMMAND" ] || exit 0

# bash `$(...)` の trailing-LF trim で消えた `\<LF>` を復元 (詳細は pre-push-review の
# cmd-parser.sh の「末尾 `\<LF>` 復元の caller 側 inline パターン」 セクション)。
case "$COMMAND" in *\\) COMMAND="${COMMAND}"$'\n' ;; esac

# 行継続 `\<改行>` を空白に、real newline を `;` に正規化する (詳細は push hook 側の
# コメント参照)。順序が重要: 行継続を先に処理しないと `git \<NL>commit` が `git \;commit`
# に化けて invocation regex を素通りする。 macOS bash 3.2 互換性のため `${var//$'\\\n'/...}`
# は使わず cmd-parser.sh の純 bash + sed fallback 実装に委譲する。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/cmd-parser.sh
source "$SCRIPT_DIR/lib/cmd-parser.sh"
# fast-path: line continuation を含まない 99% の入力では `$(...)` subshell fork を回避。
case "$COMMAND" in
  *\\$'\n'*) COMMAND=$(normalize_line_continuations_to_space "$COMMAND") ;;
esac
COMMAND="${COMMAND//$'\n'/;}"

# `git commit` サブコマンドだけを検出する。`git` と `commit` の間に許容する中間トークンは
# 「git の global option (`-X` / `--long` / `--long=val`) と任意の option 引数」のみ。
# 任意の subcommand を許容する旧 regex は `git log --grep commit` のような read-only
# コマンドまで `git commit` と誤検出していた (`log` を中間トークンとして拾ってしまうため)。
# pre-push-review/block-pre-push.sh の PUSH_DETECT_REGEX と同じ OPT/OPT_ARG 構造に揃える。
#   OPT      : `-x` / `--long` / `--long=val` のような option トークン
#   OPT_ARG  : `-` で始まらないオプション引数 (例: `-C dir` の `dir`)
OPT='-[^[:space:];&|]+'
OPT_ARG='([[:space:]]+[^-[:space:];&|][^[:space:];&|]*)?'
# 左境界はシェルのコマンド開始位置 (`^` / `;` / `&` / `|`) のみ。`[[:space:]]` を境界に
# 含めると `echo git commit` のような echo 引数内の text reference が誤マッチする。
# env-var assignment (`GIT_DIR=/foo/.git git commit ...`) も invocation として検出する
# (検出後 target-mismatch スコープで GIT_DIR= を deny に倒すため)。
ENV_VAR_PREFIX='([A-Za-z_][A-Za-z0-9_]*=[^[:space:];&|]*[[:space:]]+)*'
COMMIT_INVOCATION_REGEX="(^|[;&|])[[:space:]]*${ENV_VAR_PREFIX}git[[:space:]]+(${OPT}${OPT_ARG}[[:space:]]+)*commit([[:space:]]|\$)"

if ! [[ "$COMMAND" =~ $COMMIT_INVOCATION_REGEX ]]; then
  exit 0
fi

# shellcheck source=lib/default-branch.sh
source "$SCRIPT_DIR/lib/default-branch.sh"

# 同一コマンドに複数の commit 呼び出しが含まれる (`git commit && cd /other && git commit`
# など) ケースに対応するため、while ループで各 commit を独立に検査する。各 iteration で
# 「当該 commit invocation の前段 + invocation 自体」を target-mismatch 検査範囲に取り、
# post-commit の cd は対象外、invocation 内の `-C dir` は対象内に倒す。push hook と同じ
# パターン。
REM="$COMMAND"
while [[ "$REM" =~ $COMMIT_INVOCATION_REGEX ]]; do
  COMMIT_INVOCATION="${BASH_REMATCH[0]}"
  COMMIT_TARGET_SCOPE="${REM%%"$COMMIT_INVOCATION"*}$COMMIT_INVOCATION"
  if has_target_mismatch_prefix "$COMMIT_TARGET_SCOPE"; then
    emit_deny "$TARGET_MISMATCH_DENY_REASON"
    exit 0
  fi
  REM="${REM#*"$COMMIT_INVOCATION"}"
done

BRANCH=$(current_branch)
if [ -n "$BRANCH" ] && is_default_branch "$BRANCH"; then
  emit_deny "デフォルトブランチ ($BRANCH) 上での git commit は禁止されています。working branch を切ってから commit してください (例: git switch -c feat/my-change)。デフォルトブランチへの変更は GitHub 上の PR merge 経由のみで取り込む運用です。"
  exit 0
fi

exit 0
