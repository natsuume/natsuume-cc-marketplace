#!/bin/bash
# block-default-branch-pr.sh
# `gh pr create` で master/main を head (= source 側) にする PR を作成しようとした際に
# ブロックする PreToolUse フック。
#
# `gh pr create` の挙動:
#   - `--head <branch>` が無いと、カレントブランチが head になる
#   - `--head master` 等で明示するとそのブランチが head になる
#   - `--base <branch>` は target 側で、デフォルトはリポジトリのデフォルトブランチ
#
# 本フックは head 側 (= 「PR で master の中身を別ブランチに入れる」変な経路) を deny する。
# base 側の判定は不要 (PR の base がデフォルトブランチなのは正常運用)。

INPUT=$(cat)

# 大半の Bash 呼び出しは `gh pr create` と無関係。粗フィルタで早期離脱。
case "$INPUT" in
  *"gh"*"pr"*"create"*) ;;
  *) exit 0 ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -n "$COMMAND" ] || exit 0

# 行継続 `\<改行>` を空白に、real newline を `;` に正規化する (詳細は push hook 側の
# コメント参照)。順序が重要: 行継続を先に処理しないと `gh pr \<NL>create` が `gh pr \;create`
# に化けて invocation regex を素通りする。
COMMAND="${COMMAND//$'\\\n'/ }"
COMMAND="${COMMAND//$'\n'/;}"

# `gh ... pr create` のときだけ拾う。`gh -R owner/repo pr create` のような global option を
# 伴う形式、および `cd repo && gh pr create ...` / `xxx ; gh pr create ...` のような連結
# プレフィックスも許容する。左境界はシェルのコマンド開始位置 (`^` / `;` / `&` / `|`) のみ
# で、`[[:space:]]` を境界に含めると `echo gh pr create` のような echo 引数内 text
# reference が誤マッチする (commit/push hook と揃え)。
# env-var assignment (`GH_TOKEN=xxx gh pr create ...` / `GIT_DIR=... gh pr create ...`) も
# invocation として検出する (検出後 target-mismatch スコープで GIT_DIR= を deny に倒すため)。
ENV_VAR_PREFIX='([A-Za-z_][A-Za-z0-9_]*=[^[:space:];&|]*[[:space:]]+)*'
PR_INVOCATION_REGEX="(^|[;&|])[[:space:]]*${ENV_VAR_PREFIX}gh([[:space:]]+[^[:space:];&|]+)*[[:space:]]+pr[[:space:]]+create([[:space:]]|\$)"
if ! [[ "$COMMAND" =~ $PR_INVOCATION_REGEX ]]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/default-branch.sh
source "$SCRIPT_DIR/lib/default-branch.sh"

# 同一コマンドに複数の `gh pr create` が含まれる (`gh pr create --head feat && gh pr create
# --head master` など) ケースに対応するため、while ループで各 PR 作成を独立に target-
# mismatch 検査する。push/commit hook と同形。各 iteration で「当該 invocation の前段 +
# invocation 自体」を範囲に取り、post-PR の cd は対象外、invocation 内の `-R` は target を
# 変えないので問題なし。
REM="$COMMAND"
while [[ "$REM" =~ $PR_INVOCATION_REGEX ]]; do
  PR_INVOCATION="${BASH_REMATCH[0]}"
  PR_TARGET_SCOPE="${REM%%"$PR_INVOCATION"*}$PR_INVOCATION"
  if has_target_mismatch_prefix "$PR_TARGET_SCOPE"; then
    emit_deny "$TARGET_MISMATCH_DENY_REASON"
    exit 0
  fi
  REM="${REM#*"$PR_INVOCATION"}"
done

DENY_REASON='デフォルトブランチ (master/main) を head とする PR の作成は禁止されています。working branch (例: feat/my-change) に切り替えるか、`--head <working-branch>` を指定してください。デフォルトブランチへの変更は他ブランチからの PR merge 経由のみで取り込む運用です。'

# 同一コマンドに複数の `gh pr create` が含まれる (`gh pr create --head feat && gh pr create
# --head master` など) ケースに対応するため、while ループで各 PR 作成を独立に検査する。
# 各 iteration で:
#   - 「当該 invocation の前段 + invocation 自体」を target-mismatch 検査範囲に取る
#   - 当該 invocation の引数範囲 (次のシェル区切り文字まで) から head 指定を抽出して評価
# 引数抽出を全 COMMAND ではなく当該 invocation の引数範囲に絞ることで、後続 PR の head
# が最初の PR の引数として誤って拾われるのを防ぐ。
REM="$COMMAND"
while [[ "$REM" =~ $PR_INVOCATION_REGEX ]]; do
  PR_INVOCATION="${BASH_REMATCH[0]}"
  PR_POSTFIX="${REM#*"$PR_INVOCATION"}"
  # この PR の引数範囲は、次のシェル区切り文字 (`;`/`&`/`|`) までに限定する。
  PR_ARGS="${PR_POSTFIX%%[;&|]*}"
  PR_TARGET_SCOPE="${REM%%"$PR_INVOCATION"*}$PR_INVOCATION"

  if has_target_mismatch_prefix "$PR_TARGET_SCOPE"; then
    emit_deny "$TARGET_MISMATCH_DENY_REASON"
    exit 0
  fi

  # gh CLI の head 指定形式を網羅的に抽出する (gh は cobra 系なので long/short の各派生
  # を同等に受け付ける):
  #   --head <branch>    (別トークン long)
  #   --head=<branch>    (`=` 付き long)
  #   -H <branch>        (別トークン short)
  #   -H=<branch>        (`=` 付き short, gh では正式には未対応だが将来互換)
  #   -H<branch>         (cluster short, cobra のデフォルト挙動で許容)
  HEAD_BRANCH=$(printf '%s' "$PR_ARGS" | awk '
{
  n = split($0, t, /[[:space:]]+/)
  for (i = 1; i <= n; i++) {
    tok = t[i]
    if ((tok == "--head" || tok == "-H") && (i + 1) <= n) {
      print t[i+1]
      exit
    }
    if (substr(tok, 1, 7) == "--head=") {
      print substr(tok, 8)
      exit
    }
    if (substr(tok, 1, 3) == "-H=") {
      print substr(tok, 4)
      exit
    }
    if (substr(tok, 1, 2) == "-H" && length(tok) > 2) {
      print substr(tok, 3)
      exit
    }
  }
}')

  if [ -n "$HEAD_BRANCH" ]; then
    # ユーザーが引用符付きで指定した場合 (例: `--head "master"` / `--head='main'`) に
    # 生の token に quote が残るため、shell quote を 1 段剥がす。
    # `--head=owner:"master"` のように owner 剥がし後にも quote が残り得る経路があるため、
    # 剥がし → 分割 → 剥がし の順で 2 段 strip する。
    HEAD_BRANCH=$(strip_shell_quotes "$HEAD_BRANCH")
    # `--head owner:branch` 形式 (cross-fork PR を作る形式) では owner プレフィックスを
    # 剥がして branch 部分だけで判定する。
    case "$HEAD_BRANCH" in
      *:*) HEAD_BRANCH="${HEAD_BRANCH##*:}" ;;
    esac
    HEAD_BRANCH=$(strip_shell_quotes "$HEAD_BRANCH")
    if is_default_branch "$HEAD_BRANCH"; then
      emit_deny "$DENY_REASON"
      exit 0
    fi
  else
    # この PR は --head 未指定 → カレントブランチが head になる。デフォルトブランチなら
    # deny。
    BRANCH=$(current_branch)
    if [ -n "$BRANCH" ] && is_default_branch "$BRANCH"; then
      emit_deny "$DENY_REASON"
      exit 0
    fi
  fi

  REM="$PR_POSTFIX"
done

exit 0
