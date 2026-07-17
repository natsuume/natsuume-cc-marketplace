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
#
# ## 検出方式 (segment/token ベース、v0.4.0)
#
# v0.3.x までは COMMAND 文字列の改行を無条件に `;` へ正規化してから quote 非対応の
# regex (`COMMIT_INVOCATION_REGEX`) で invocation を走査していた。この方式は quote 内・
# heredoc 内の「コマンド例文」(複数行コミットメッセージ中の `git commit -am wip` 等) を
# 実コマンドと誤認して deny する false-positive を持っていた (#137)。
# v0.4.0 では pre-push-review/block-pre-push.sh と同じ cmd-parser.sh (split_command +
# tokenize_segment) ベースの検出に刷新した。詳細な流れは block-default-branch-push.sh の
# ヘッダコメント参照 (segment 分割 → グループ unwrap → 置換 shape 保守的 deny → token
# level invocation 検出 → target-mismatch 判定、という同一パイプラインを commit 用に
# 適用している)。

# 予期せぬ非ゼロ終了 (jq クラッシュ / library 読み込み失敗 / signal 等) を stderr に
# 可視化する。最初の library 自体が欠損していても診断できるよう、caller 内の最小
# bootstrap trap を先に張り、exit-trap.sh の読み込み成功後に共有 handler へ置き換える。
_GIT_GUARDRAILS_HOOK_TAG="block-default-branch-commit"
_GIT_GUARDRAILS_HOOK_IMPACT="デフォルトブランチ上での commit を deny できず default branch 保護が外れた可能性があります。"
_git_guardrails_bootstrap_exit_handler() {
  local exit_code=$?
  if [ "$exit_code" -ne 0 ]; then
    printf '[git-guardrails/%s] 予期せぬエラーで hook が exit %s で終了しました。\n' \
      "$_GIT_GUARDRAILS_HOOK_TAG" "$exit_code" >&2
    printf '[git-guardrails/%s] %s marketplace https://github.com/natsuume/natsuume-cc-marketplace に hook 実装の bug として報告してください。\n' \
      "$_GIT_GUARDRAILS_HOOK_TAG" "$_GIT_GUARDRAILS_HOOK_IMPACT" >&2
  fi
}
trap _git_guardrails_bootstrap_exit_handler EXIT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit $?
# shellcheck source=lib/exit-trap.sh
source "$SCRIPT_DIR/lib/exit-trap.sh" || exit $?
declare -F install_exit_trap >/dev/null 2>&1 || exit 127
declare -F require_git_guardrails_functions >/dev/null 2>&1 || exit 127
install_exit_trap "$_GIT_GUARDRAILS_HOOK_TAG" "$_GIT_GUARDRAILS_HOOK_IMPACT" || exit $?

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
_jq_status=$?
[ "$_jq_status" -eq 0 ] || exit "$_jq_status"
[ -n "$COMMAND" ] || exit 0

# bash `$(...)` の trailing-LF trim で消えた `\<LF>` を復元 (詳細は pre-push-review の
# cmd-parser.sh の「末尾 `\<LF>` 復元の caller 側 inline パターン」 セクション)。
case "$COMMAND" in *\\) COMMAND="${COMMAND}"$'\n' ;; esac

# 行継続 `\<改行>` を空白に正規化する (詳細は push hook 側のコメント参照)。
# macOS bash 3.2 互換性のため `${var//$'\\\n'/...}` は使わず cmd-parser.sh の純 bash +
# sed fallback 実装に委譲する。
# (SCRIPT_DIR は冒頭の exit-trap install 時に定義済み。)
# shellcheck source=lib/cmd-parser.sh
source "$SCRIPT_DIR/lib/cmd-parser.sh" || exit $?
require_git_guardrails_functions "$_GIT_GUARDRAILS_HOOK_TAG" \
  normalize_line_continuations normalize_line_continuations_to_space \
  split_command unquote_token skip_env_assignments tokenize_segment || exit $?
# fast-path: line continuation を含まない 99% の入力では `$(...)` subshell fork を回避。
case "$COMMAND" in
  *\\$'\n'*) COMMAND=$(normalize_line_continuations_to_space "$COMMAND") ;;
esac

# `&` を含む shell redirection (`2>&1` / `&>file` / `<<EOF` 等) を空白に置換する。
# cmd-parser (split_command) は `&` を一律 separator として扱うため、redirection 内の
# `&` を parallel separator と誤認して false-positive な segment 分割を起こす経路を
# 塞ぐ目的 (pre-push-review/block-pre-push.sh の同種処理と同じ理由・同じ記法)。
COMMAND=$(printf '%s' "$COMMAND" \
  | sed -E 's/[0-9]?(&>>|&>|>>|>\&|<\&|<<<|<<|<>)[[:space:]]*[A-Za-z0-9_./=+@:-]*/ /g')

# shellcheck source=lib/default-branch.sh
source "$SCRIPT_DIR/lib/default-branch.sh" || exit $?
require_git_guardrails_functions "$_GIT_GUARDRAILS_HOOK_TAG" \
  is_default_branch current_branch strip_shell_quotes normalize_refspec_part \
  strip_quoted_text strip_squoted_text find_group_close emit_deny \
  has_target_mismatch_prefix || exit $?

# コマンドを segment (top-level `;`/`&&`/`||`/`&`/`|`/改行区切り) に分割する。
# SEPARATORS は本 hook では使わないため配列化せず読み捨てる。
SEGMENTS=()
while IFS= read -r line; do
  case "$line" in
    SEP:*) continue ;;
  esac
  SEGMENTS+=("$line")
done < <(split_command "$COMMAND")

# グループ unwrap: SEGMENTS を worklist として扱い、trim 後に `(` / `{` で始まる segment
# (subshell / brace group) は、外側の `(`/`{` ... `)`/`}` を剥がした内側テキストを
# split_command で再分割し、得られた segment 群を worklist の現在位置に順序を保って
# 置き換える (詳細は block-default-branch-push.sh のコメント参照。commit hook でも同じ
# アルゴリズムを使う)。
# 対応する閉じ文字の特定には `find_group_close` (quote 文脈 + depth 追跡、詳細は
# block-default-branch-push.sh のコメント参照) を使う。「最後の `)`/`}` で切る」という
# 文字列ヒューリスティックは redirection target 内の置換 (`> $(mktemp)` 等) の `)` を
# 誤って選んでしまう bug があったため (#F9)、depth 追跡方式に統合した。
_wi=0
while [ "$_wi" -lt "${#SEGMENTS[@]}" ]; do
  _wseg="${SEGMENTS[$_wi]}"
  _wtrim="${_wseg#"${_wseg%%[![:space:]]*}"}"
  _wtrim="${_wtrim%"${_wtrim##*[![:space:]]}"}"
  case "$_wtrim" in
    '('*|'{'*)
      _wclose_idx="$(find_group_close "$_wtrim")"
      if [ -n "$_wclose_idx" ]; then
        _winner="${_wtrim:1:$((_wclose_idx-1))}"
        _wsuffix="${_wtrim:$((_wclose_idx+1))}"
        _winner="${_winner%"${_winner##*[![:space:]]}"}"
        case "$_winner" in
          *';') _winner="${_winner%;}" ;;
        esac
      else
        _winner="${_wtrim:1}"
        _wsuffix=""
      fi

      # 無限ループ防止: 除去操作で文字列長が減らない場合はそのまま 1 segment として
      # 処理を続ける (先頭 1 文字を必ず剥がすため実際には常に減るが、防御的に確認する)。
      if [ "${#_winner}" -lt "${#_wtrim}" ]; then
        _gwnew=()
        while IFS= read -r _gwline; do
          case "$_gwline" in
            SEP:*) continue ;;
          esac
          _gwnew+=("$_gwline")
        done < <(split_command "$_winner")

        # 閉じ文字より後の suffix (redirection 等) が空白以外を含む場合、それも
        # 独立した worklist 要素として中身の segment 群の直後に追加する (詳細は
        # block-default-branch-push.sh の同箇所コメント参照。#F9)。
        _wsuffix_trim="${_wsuffix#"${_wsuffix%%[![:space:]]*}"}"
        _wsuffix_trim="${_wsuffix_trim%"${_wsuffix_trim##*[![:space:]]}"}"
        if [ -n "$_wsuffix_trim" ]; then
          while IFS= read -r _gwline; do
            case "$_gwline" in
              SEP:*) continue ;;
            esac
            _gwnew+=("$_gwline")
          done < <(split_command "$_wsuffix")
        fi

        _gwnewall=()
        _gwk=0
        while [ "$_gwk" -lt "$_wi" ]; do
          _gwnewall+=("${SEGMENTS[$_gwk]}")
          _gwk=$((_gwk+1))
        done
        for _gwe in "${_gwnew[@]}"; do
          _gwnewall+=("$_gwe")
        done
        _gwk=$((_wi+1))
        while [ "$_gwk" -lt "${#SEGMENTS[@]}" ]; do
          _gwnewall+=("${SEGMENTS[$_gwk]}")
          _gwk=$((_gwk+1))
        done
        SEGMENTS=("${_gwnewall[@]}")
        continue
      fi
      ;;
  esac
  _wi=$((_wi+1))
done

# 置換 shape の保守的 deny: `$(...)` / `<(...)` / `>(...)` / バッククォート内に隠れた
# `git commit` を保守的に deny する (詳細は push hook 側のコメント参照)。
# 左境界に `^` を含めないのは、segment 先頭の正当な invocation 自身を誤マッチさせない
# ため。バッククォートを含む文字クラスはシングルクォートで組み立てる。
OPT='-[^[:space:];&|]+'
OPT_ARG='([[:space:]]+[^-[:space:];&|][^[:space:];&|]*)?'
ENV_VAR_PREFIX='([A-Za-z_][A-Za-z0-9_]*=[^[:space:];&|]*[[:space:]]+)*'
SUBST_BOUNDARY='[;&|(`]'
SUBST_COMMIT_INVOCATION_REGEX="${SUBST_BOUNDARY}[[:space:]]*${ENV_VAR_PREFIX}git[[:space:]]+(${OPT}${OPT_ARG}[[:space:]]+)*commit([[:space:]]|\$)"

# 第 2 パス: dquote 内で実行される command substitution を opener-anchored に検出する
# (詳細・設計理由は block-default-branch-push.sh の同箇所コメント参照)。
SUBST2_OPENER='(\$\(|<\(|>\()'
SUBST2_COMMIT_INVOCATION_REGEX="${SUBST2_OPENER}[[:space:]]*${ENV_VAR_PREFIX}git[[:space:]]+(${OPT}${OPT_ARG}[[:space:]]+)*commit([[:space:]]|\)|\$)"

_si=0
while [ "$_si" -lt "${#SEGMENTS[@]}" ]; do
  _seg="${SEGMENTS[$_si]}"
  case "$_seg" in
    *'$('*|*'<('*|*'>('*|*'`'*)
      _stripped="$(strip_quoted_text "$_seg")"
      if [[ "$_stripped" =~ $SUBST_COMMIT_INVOCATION_REGEX ]]; then
        REASON=$(cat <<'EOF'
デフォルトブランチ保護フックをブロックしました。コマンド置換 `$(...)` / プロセス置換 `<(...)` / `>(...)` / バッククォート内の `git commit` は本フックでは解析できないため保守的に deny します。

これらは内部の cwd や branch 切替を本 parser から隠蔽する経路で、例えば `echo $(git switch master; git commit -m x)` のようなコマンドはデフォルトブランチ保護を素通りする bypass になり得ます。

置換の外で直接 `git commit` を実行してください。
EOF
)
        emit_deny "$REASON"
        exit 0
      fi
      ;;
  esac
  case "$_seg" in
    *'$('*|*'<('*|*'>('*)
      _stripped2="$(strip_squoted_text "$_seg")"
      if [[ "$_stripped2" =~ $SUBST2_COMMIT_INVOCATION_REGEX ]]; then
        REASON=$(cat <<'EOF'
デフォルトブランチ保護フックをブロックしました。ダブルクォート内であっても command substitution `$(...)` / プロセス置換 `<(...)` / `>(...)` は bash によって実行されるため、`echo "$(git commit -m x)"` のような形の `git commit` を保守的に deny します。

引用符の内側であっても commit は実際に実行されるため、除外することはできません。置換の外で直接 `git commit` を実行してください。
EOF
)
        emit_deny "$REASON"
        exit 0
      fi
      ;;
  esac
  _si=$((_si+1))
done

# token level で commit invocation を検出する。env-var assignment を skip した後、最初の
# 実 token が `git` (または path-qualified `*/git`) で、続く global option を walk して
# `commit` サブコマンドに到達するものだけを拾う。
COMMIT_INVOCATION_INDICES=()
_si=0
while [ "$_si" -lt "${#SEGMENTS[@]}" ]; do
  _seg="${SEGMENTS[$_si]}"
  declare -a _gtoks
  tokenize_segment "$_seg" _gtoks
  _gidx=0
  _gn=${#_gtoks[@]}
  skip_env_assignments _gtoks _gidx
  if [ "$_gidx" -lt "$_gn" ]; then
    _gfirst="$(unquote_token "${_gtoks[$_gidx]}")"
    case "$_gfirst" in
      git|*/git)
        _gidx=$((_gidx+1))
        while [ "$_gidx" -lt "$_gn" ]; do
          _gopt="$(unquote_token "${_gtoks[$_gidx]}")"
          case "$_gopt" in
            -C|--git-dir|--work-tree|-c|--config|--config-env)
              _gidx=$((_gidx+2)); continue ;;
            --git-dir=*|--work-tree=*)
              _gidx=$((_gidx+1)); continue ;;
            -*)
              _gidx=$((_gidx+1)); continue ;;
            commit)
              COMMIT_INVOCATION_INDICES+=("$_si")
              break
              ;;
            *)
              break
              ;;
          esac
        done
        ;;
    esac
  fi
  unset _gtoks
  _si=$((_si+1))
done

# commit invocation を 1 つも含まないなら本 hook 対象外。
if [ "${#COMMIT_INVOCATION_INDICES[@]}" -eq 0 ]; then
  exit 0
fi

# 同一コマンドに複数の commit 呼び出しが含まれる (`git commit && cd /other && git commit`
# など) ケースに対応するため、検出した各 commit invocation を独立に target-mismatch
# 検査する。「当該 invocation より前の全 segment + invocation 自体」を検査範囲に取り、
# post-commit の cd は対象外、invocation 内の `-C dir` は対象内に倒す。
for _si in "${COMMIT_INVOCATION_INDICES[@]}"; do
  # commit サブコマンドの位置を再特定する (invocation prefix の構築に使う)。
  _seg="${SEGMENTS[$_si]}"
  declare -a _gtoks
  tokenize_segment "$_seg" _gtoks
  _gidx=0
  _gn=${#_gtoks[@]}
  skip_env_assignments _gtoks _gidx
  _gidx=$((_gidx+1))
  while [ "$_gidx" -lt "$_gn" ]; do
    _gopt="$(unquote_token "${_gtoks[$_gidx]}")"
    case "$_gopt" in
      -C|--git-dir|--work-tree|-c|--config|--config-env) _gidx=$((_gidx+2)); continue ;;
      --git-dir=*|--work-tree=*) _gidx=$((_gidx+1)); continue ;;
      -*) _gidx=$((_gidx+1)); continue ;;
      commit) break ;;
      *) break ;;
    esac
  done

  # target-mismatch scope: 先行 segment (`;` join) + 当該 invocation の「先頭 token
  # (env assignment 含む) から `commit` サブコマンド token までの raw token 列を空白
  # join した invocation prefix」。commit より後の引数 (`-m` 本文等) は scope から
  # 除外する (詳細は block-default-branch-push.sh の同箇所コメント参照。`git commit -m
  # cd` の `cd` がコミットメッセージ本文の場合に誤 deny しないため)。
  _invprefix=""
  _pk=0
  while [ "$_pk" -le "$_gidx" ] && [ "$_pk" -lt "$_gn" ]; do
    if [ -z "$_invprefix" ]; then
      _invprefix="${_gtoks[$_pk]}"
    else
      _invprefix="$_invprefix ${_gtoks[$_pk]}"
    fi
    _pk=$((_pk+1))
  done
  unset _gtoks

  _scope=""
  _sj=0
  while [ "$_sj" -lt "$_si" ]; do
    if [ -z "$_scope" ]; then
      _scope="${SEGMENTS[$_sj]}"
    else
      _scope="$_scope;${SEGMENTS[$_sj]}"
    fi
    _sj=$((_sj+1))
  done
  if [ -z "$_scope" ]; then
    _scope="$_invprefix"
  else
    _scope="$_scope;$_invprefix"
  fi
  if has_target_mismatch_prefix "$_scope"; then
    emit_deny "$TARGET_MISMATCH_DENY_REASON"
    exit 0
  fi
done

BRANCH=$(current_branch)
if [ -n "$BRANCH" ] && is_default_branch "$BRANCH"; then
  emit_deny "デフォルトブランチ ($BRANCH) 上での git commit は禁止されています。working branch を切ってから commit してください (例: git switch -c feat/my-change)。デフォルトブランチへの変更は GitHub 上の PR merge 経由のみで取り込む運用です。"
  exit 0
fi

exit 0
