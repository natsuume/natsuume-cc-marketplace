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
#
# ## 検出方式 (segment/token ベース、v0.4.0)
#
# v0.3.x までは COMMAND 文字列の改行を無条件に `;` へ正規化してから quote 非対応の
# regex (`PR_INVOCATION_REGEX`) + awk で invocation / head 引数を走査していた。この方式は
# quote 内・heredoc 内の「コマンド例文」(JSON 文字列中の `gh pr create --head master` 等)
# を実コマンドと誤認して deny する false-positive を持っていた (#136)。
# v0.4.0 では pre-push-review/block-pre-push.sh と同じ cmd-parser.sh (split_command +
# tokenize_segment) ベースの検出に刷新した。segment 分割 → グループ unwrap → 置換 shape
# 保守的 deny という前段は push/commit hook と共通で、invocation 検出のみ「`gh` から始まる
# token 列の中で隣接する `pr` `create` token を探す」形に変える (`gh` と `pr create` の
# 間に任意 token を許容する既存の意味論を維持するため)。

# 予期せぬ非ゼロ終了 (jq クラッシュ / library 読み込み失敗 / signal 等) を stderr に
# 可視化する。最初の library 自体が欠損していても診断できるよう、caller 内の最小
# bootstrap trap を先に張り、exit-trap.sh の読み込み成功後に共有 handler へ置き換える。
_GIT_GUARDRAILS_HOOK_TAG="block-default-branch-pr"
_GIT_GUARDRAILS_HOOK_IMPACT="master/main を head にする PR 作成を deny できず default branch 保護が外れた可能性があります。"
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

# 大半の Bash 呼び出しは `gh pr create` と無関係。粗フィルタで早期離脱。
case "$INPUT" in
  *"gh"*"pr"*"create"*) ;;
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

DENY_REASON='デフォルトブランチ (master/main) を head とする PR の作成は禁止されています。working branch (例: feat/my-change) に切り替えるか、`--head <working-branch>` を指定してください。デフォルトブランチへの変更は他ブランチからの PR merge 経由のみで取り込む運用です。'

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
# 置き換える (詳細は block-default-branch-push.sh のコメント参照。pr hook でも同じ
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
# `gh pr create` を保守的に deny する (詳細は push hook 側のコメント参照)。
# 左境界に `^` を含めないのは、segment 先頭の正当な invocation 自身を誤マッチさせない
# ため。バッククォートを含む文字クラスはシングルクォートで組み立てる。
#
# `gh` と `pr create` の間に任意 token (`-R owner/repo` 等) を許容する既存の意味論を
# 維持するため、`gh([[:space:]]+[^[:space:];&|]+)*[[:space:]]+pr[[:space:]]+create` の
# 形をそのまま置換 shape 検出にも使う。
#
# `ENV_VAR_PREFIX` (`FOO=bar gh pr create ...` のような env-var prefix を許容する) を
# push/commit hook と揃えて含める。これが無いと `echo $(FOO=1 gh pr create --head
# master)` のような env-var prefix 付き invocation が置換 shape check を素通りし、
# 兄弟 hook (push/commit) との非対称な bypass になる (code-reviewer 4 巡目で実測)。
ENV_VAR_PREFIX='([A-Za-z_][A-Za-z0-9_]*=[^[:space:];&|]*[[:space:]]+)*'
SUBST_BOUNDARY='[;&|(`]'
SUBST_PR_INVOCATION_REGEX="${SUBST_BOUNDARY}[[:space:]]*${ENV_VAR_PREFIX}gh([[:space:]]+[^[:space:];&|]+)*[[:space:]]+pr[[:space:]]+create([[:space:]]|\$)"

# 第 2 パス: dquote 内で実行される command substitution を opener-anchored に検出する
# (詳細・設計理由は block-default-branch-push.sh の同箇所コメント参照)。「任意 token」
# 部分は substitution の閉じ `)` を誤って跨がないよう `)` も除外文字に含める。
SUBST2_OPENER='(\$\(|<\(|>\()'
SUBST2_PR_INVOCATION_REGEX="${SUBST2_OPENER}[[:space:]]*${ENV_VAR_PREFIX}gh([[:space:]]+[^[:space:];&|)]+)*[[:space:]]+pr[[:space:]]+create([[:space:]]|\)|\$)"

_si=0
while [ "$_si" -lt "${#SEGMENTS[@]}" ]; do
  _seg="${SEGMENTS[$_si]}"
  case "$_seg" in
    *'$('*|*'<('*|*'>('*|*'`'*)
      _stripped="$(strip_quoted_text "$_seg")"
      if [[ "$_stripped" =~ $SUBST_PR_INVOCATION_REGEX ]]; then
        REASON=$(cat <<'EOF'
デフォルトブランチ保護フックをブロックしました。コマンド置換 `$(...)` / プロセス置換 `<(...)` / `>(...)` / バッククォート内の `gh pr create` は本フックでは解析できないため保守的に deny します。

これらは内部の cwd や head branch を本 parser から隠蔽する経路で、例えば `echo $(git switch master; gh pr create)` のようなコマンドはデフォルトブランチ保護を素通りする bypass になり得ます。

置換の外で直接 `gh pr create` を実行してください。
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
      if [[ "$_stripped2" =~ $SUBST2_PR_INVOCATION_REGEX ]]; then
        REASON=$(cat <<'EOF'
デフォルトブランチ保護フックをブロックしました。ダブルクォート内であっても command substitution `$(...)` / プロセス置換 `<(...)` / `>(...)` は bash によって実行されるため、`echo "$(gh pr create --head master)"` のような形の `gh pr create` を保守的に deny します。

引用符の内側であっても PR 作成は実際に実行されるため、除外することはできません。置換の外で直接 `gh pr create` を実行してください。
EOF
)
        emit_deny "$REASON"
        exit 0
      fi
      ;;
  esac
  _si=$((_si+1))
done

# 各 segment を token level で検査する。`gh` (または path-qualified `*/gh`) から始まる
# token 列の中で、隣接する 2 token が (unquote 後) `pr` `create` である位置を探す
# (`gh` と `pr create` の間に任意 token を許容する既存の regex 意味論を維持するため)。
_si=0
while [ "$_si" -lt "${#SEGMENTS[@]}" ]; do
  _seg="${SEGMENTS[$_si]}"
  declare -a _gtoks
  tokenize_segment "$_seg" _gtoks
  _gidx=0
  _gn=${#_gtoks[@]}
  skip_env_assignments _gtoks _gidx
  _gcreate_idx=-1
  if [ "$_gidx" -lt "$_gn" ]; then
    _gfirst="$(unquote_token "${_gtoks[$_gidx]}")"
    case "$_gfirst" in
      gh|*/gh)
        _gj=$_gidx
        while [ "$_gj" -lt "$((_gn-1))" ]; do
          _gt1="$(unquote_token "${_gtoks[$_gj]}")"
          _gt2="$(unquote_token "${_gtoks[$((_gj+1))]}")"
          if [ "$_gt1" = "pr" ] && [ "$_gt2" = "create" ]; then
            _gcreate_idx=$((_gj+1))
            break
          fi
          _gj=$((_gj+1))
        done
        ;;
    esac
  fi

  if [ "$_gcreate_idx" -ge 0 ]; then
    # target-mismatch scope: 先行 segment (`;` join) + 当該 invocation の「先頭 token
    # (env assignment 含む) から `create` サブコマンド token までの raw token 列を
    # 空白 join した invocation prefix」。create より後の引数 (`--head` / `--title` 等)
    # は scope から除外する (詳細は block-default-branch-push.sh の同箇所コメント参照。
    # `gh pr create --head feature --title cd` の `cd` が --title の値の場合に誤 deny
    # しないため)。`gh -R owner/repo pr create` のような global option は invocation
    # prefix 側に残るため、target-mismatch 検出は引き続き働く。
    _invprefix=""
    _pk=0
    while [ "$_pk" -le "$_gcreate_idx" ]; do
      if [ -z "$_invprefix" ]; then
        _invprefix="${_gtoks[$_pk]}"
      else
        _invprefix="$_invprefix ${_gtoks[$_pk]}"
      fi
      _pk=$((_pk+1))
    done

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
      unset _gtoks
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
    # `create` より後の token だけを走査する (前段の `pr` `create` 自体や `-R` 等の gh
    # 側 global option を head 指定と誤認しないため)。
    #
    # 他の value-bearing option (`--title` 等) の値を先に skip してから flag 形状を
    # 判定する。判定は unquote 後の値に対して行う (`gh pr create "--head=master"` の
    # ように flag 自体を quote するのは普通の shell 記法であり、これを見逃すと本物の
    # head 指定が素通りする regression になる (security-reviewer 4 巡目で実測))。
    # 一方で unquote 後の値だけを見ると `gh pr create --title "--head=master"` の
    # ように **他の option の値** が偶然 head flag 形状と一致するケースを誤検出する
    # (codex review 3 巡目)。この 2 つを両立するため、`gh pr create` の
    # value-bearing option (`--title`/`--body`/`--body-file`/`--base`/`--assignee`/
    # `--label`/`--milestone`/`--reviewer`/`--project`/`--template`/`--recover`、
    # `gh pr create --help` の FLAGS 節で確認した値を取る全 option) を固定リストで
    # 認識し、それらの token に遭遇したら (flag 名自体が quote されていても
    # unquote して識別する) 値 token を **形状を問わず無条件に** 1 つ skip する。
    # それ以外の token だけを head flag 候補として判定する。
    GH_VALUE_OPT_RE='^(-a|--assignee|-B|--base|-b|--body|-F|--body-file|-l|--label|-m|--milestone|-p|--project|--recover|-r|--reviewer|-T|--template|-t|--title)$'
    GH_VALUE_OPT_EQ_RE='^(--assignee|--base|--body|--body-file|--label|--milestone|--project|--recover|--reviewer|--template|--title)='
    declare -a _gtoks2
    tokenize_segment "$_seg" _gtoks2
    HEAD_BRANCH=""
    _hk=$((_gcreate_idx+1))
    _hn=${#_gtoks2[@]}
    while [ "$_hk" -lt "$_hn" ]; do
      _htok="$(unquote_token "${_gtoks2[$_hk]}")"
      if [[ "$_htok" =~ $GH_VALUE_OPT_RE ]]; then
        # value-bearing option (別トークン形式): 値 token は形状を問わず skip する。
        _hk=$((_hk+2))
        continue
      fi
      if [[ "$_htok" =~ $GH_VALUE_OPT_EQ_RE ]]; then
        # value-bearing option (`=` 付き 1 token 形式): この token 自体が値を含むので
        # 1 つだけ進める。
        _hk=$((_hk+1))
        continue
      fi
      case "$_htok" in
        --head)
          _hk=$((_hk+1))
          if [ "$_hk" -lt "$_hn" ]; then
            HEAD_BRANCH="$(unquote_token "${_gtoks2[$_hk]}")"
          fi
          break
          ;;
        -H)
          _hk=$((_hk+1))
          if [ "$_hk" -lt "$_hn" ]; then
            HEAD_BRANCH="$(unquote_token "${_gtoks2[$_hk]}")"
          fi
          break
          ;;
        --head=*)
          HEAD_BRANCH="${_htok#--head=}"
          break
          ;;
        -H=*)
          HEAD_BRANCH="${_htok#-H=}"
          break
          ;;
        -H?*)
          HEAD_BRANCH="${_htok#-H}"
          break
          ;;
      esac
      _hk=$((_hk+1))
    done
    unset _gtoks2

    if [ -n "$HEAD_BRANCH" ]; then
      # ユーザーが引用符付きで指定した場合 (例: `--head "master"` / `--head='main'`) に
      # 生の token に quote が残るため、shell quote を 1 段剥がす。
      # `--head=owner:"master"` のように owner 剥がし後にも quote が残り得る経路がある
      # ため、剥がし → 分割 → 剥がし の順で 2 段 strip する。
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
  fi

  unset _gtoks
  _si=$((_si+1))
done

exit 0
