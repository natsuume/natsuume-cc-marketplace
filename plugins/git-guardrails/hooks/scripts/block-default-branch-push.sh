#!/bin/bash
# block-default-branch-push.sh
# デフォルトブランチ (master/main) への直接 push をブロックする PreToolUse フック。
#
# 検出対象 (どれも deny):
#   - target-mismatch prefix を含む push (cd / -C / GIT_DIR= 等で対象 repo を切り替える形)
#   - カレントブランチが master/main のときの全 push 系コマンド
#     (`git push` 単独、`git push origin`、`git push -u origin` 等の引数省略形を含む。
#      upstream 設定で master が暗黙の更新先になるケースを取りこぼさないため。)
#   - 任意のブランチから明示引数で master/main を指定する push
#     (`git push origin master`、`git push origin feat:master`、
#      `git push origin master:feat`、`git push origin origin/master` など。)
#
# 通す:
#   - master/main 以外のブランチからの引数明示 push (例: `git push origin feature`)
#   - tag や別 ref 名のみへの push
#
# ## 検出方式 (segment/token ベース、v0.4.0)
#
# v0.3.x までは COMMAND 文字列の改行を無条件に `;` へ正規化してから quote 非対応の
# regex (`PUSH_INVOCATION_REGEX`) で invocation を走査していた。この方式は quote 内・
# heredoc 内の「コマンド例文」(複数行コミットメッセージ中の `git push origin master` 等)
# を実コマンドと誤認して deny する false-positive を持っていた (#135)。
# v0.4.0 では pre-push-review/block-pre-push.sh と同じ cmd-parser.sh (split_command +
# tokenize_segment) ベースの検出に刷新し、quote / heredoc 内のテキストを実コマンドと
# 混同しない構造にした。大まかな流れ:
#   1. split_command で COMMAND を segment (top-level `;`/`&&`/`||`/`&`/`|`/改行区切り)
#      に分割する
#   2. subshell `(...)` / brace group `{...}` で始まる segment は中身を unwrap して
#      再分割し、worklist に順序を保って差し込む (`(cd /other; git push ...)` を
#      捕捉するため)
#   3. `$(...)` / `<(...)` / `>(...)` / バッククォート内に push invocation の形状が
#      見つかったら保守的に deny する (置換内部は本 parser では安全に解析できないため)
#   4. 各 segment を tokenize し、env-var assignment を skip した先頭 token が
#      `git`/`*/git` で global option を walk して `push` サブコマンドに到達する
#      ものだけを push invocation として検出する
#   5. invocation を検出した segment ごとに target-mismatch (前段の cd / -C / branch
#      切替) と refspec (--all/--mirror や `:` 区切りの destination) を検査する

# 予期せぬ非ゼロ終了 (jq クラッシュ / signal / シェル展開失敗等) を stderr に可視化する
# 診断 trap を最初に install する。jq 呼び出しより前に張ることで jq クラッシュも捕捉できる。
# trap は exit code を変えないため deny/allow 挙動は不変 (#61; 詳細は lib/exit-trap.sh)。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/exit-trap.sh
source "$SCRIPT_DIR/lib/exit-trap.sh"
install_exit_trap "block-default-branch-push" "デフォルトブランチへの直接 push を deny できず default branch 保護が外れた可能性があります。"

INPUT=$(cat)

# 大半の Bash 呼び出しは git push と無関係。jq 起動前に builtin の glob で粗フィルタ。
case "$INPUT" in
  *push*) ;;
  *) exit 0 ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -n "$COMMAND" ] || exit 0

# bash `$(...)` の trailing-LF trim で消えた `\<LF>` を復元 (詳細は pre-push-review の
# cmd-parser.sh の「末尾 `\<LF>` 復元の caller 側 inline パターン」 セクション)。
# 末尾が `\<LF>` で終わる JSON 値が取得時点で `\` 単独になる経路で security gate
# (default branch push deny) を bypass されるのを防ぐ。
case "$COMMAND" in *\\) COMMAND="${COMMAND}"$'\n' ;; esac

# 行継続 `\<改行>` (= バックスラッシュ + real newline) は実行時に両方とも消えて隣接
# トークンに連結される。先にこれを空白に置換しないと、`git \<NL>push` のような入力が
# 下流の split_command で `git` と `push` が別 token 扱いのまま残ってしまう。
# `${COMMAND//$'\\\n'/ }` を直接書かないのは macOS bash 3.2.57 互換性のため (詳細は
# cmd-parser.sh の `_normalize_line_continuations_impl` を参照)。
# (SCRIPT_DIR は冒頭の exit-trap install 時に定義済み。)
# shellcheck source=lib/cmd-parser.sh
source "$SCRIPT_DIR/lib/cmd-parser.sh"
# fast-path: line continuation を含まない 99% の入力では `$(...)` subshell fork を回避
# する (詳細は cmd-parser.sh の hot-path 設計コメント参照、実測で bash 3.2 で +637us
# の fork コストが消える)。
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
source "$SCRIPT_DIR/lib/default-branch.sh"

DENY_REASON='デフォルトブランチ (master/main) への直接 push は禁止されています。working branch から PR を作成し、GitHub 上で merge してください。'

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
# 置き換える。`(cd /other; git push origin feature)` が `cd /other` + `git push origin
# feature` の 2 segment になることで、後段の scope 判定で `cd` が捕捉される。ネストは
# この反復で自然に処理される (置換後の先頭 segment が再び `(`/`{` で始まればもう一度
# unwrap される)。
#
# 対応する閉じ文字の特定には `find_group_close` (quote 文脈 + depth 追跡) を使う。
# 「segment 内の**最後**の `)`/`}` で切る」という文字列ヒューリスティックは
# `(git push origin master) > $(mktemp)` のような入力で `$(mktemp)` 側の `)` を誤って
# 選んでしまい、外側 group の中身が壊れて refspec 比較を素通りする bypass になっていた
# (code-reviewer review で実測確認、#F9)。
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
        # brace group の文法上、閉じ `}` の直前には `;` が必要 (`{ cmd; }`)。中身
        # からはこの構文上の `;` を剥がす (無くても再分割は正しく動くが、見た目を
        # 従来と揃えるため)。
        _winner="${_winner%"${_winner##*[![:space:]]}"}"
        case "$_winner" in
          *';') _winner="${_winner%;}" ;;
        esac
      else
        # 閉じ文字が見つからない (不正な入力・記述途中等)。切り出す根拠が無いため
        # 先頭 1 文字のみ剥がして再分割する (無限ループ防止の長さチェックにより、
        # これ以上の unwrap は起きない)。
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
        # 独立した worklist 要素として中身の segment 群の直後に追加する。捨てないの
        # は `(echo hi) > $(git push origin master)` のように redirection target 内の
        # 置換に保護対象 invocation が隠れる形を、後段の置換 shape check に到達させる
        # ため (#F9)。
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

# 置換 shape の保守的 deny: `$(...)` / `<(...)` / `>(...)` / バッククォートは中身を本
# parser が解析できない (paren depth には算入されるが token level で「segment 内の
# invocation」として拾えない/拾ってしまう経路がある)。raw segment にこれらの
# substring が含まれ、かつ quoted text を除去したテキストが「置換内 push invocation」
# 形状にマッチする場合は保守的に deny する。
#
# 左境界に `^` を含めないのが重要: segment 先頭の正当な invocation 自身
# (`git push origin master` そのもの) を誤マッチさせないため。バッククォートを含む
# 文字クラスをダブルクォート文字列内に直接書くと command substitution と誤認される
# ため、シングルクォートで組み立てる。
OPT='-[^[:space:];&|]+'
OPT_ARG='([[:space:]]+[^-[:space:];&|][^[:space:];&|]*)?'
ENV_VAR_PREFIX='([A-Za-z_][A-Za-z0-9_]*=[^[:space:];&|]*[[:space:]]+)*'
SUBST_BOUNDARY='[;&|(`]'
SUBST_PUSH_INVOCATION_REGEX="${SUBST_BOUNDARY}[[:space:]]*${ENV_VAR_PREFIX}git[[:space:]]+(${OPT}${OPT_ARG}[[:space:]]+)*push([[:space:]]|\$)"

# 第 2 パス: dquote (`"..."`) 内で実行される command substitution `$(...)` / `<(...)` /
# `>(...)` を検出する。bash は dquote 内でも `$(...)` を実行するため
# (`echo "$(git push origin master)"` は実際に push を実行する)、これを検出対象外の
# ままにはできない。一方、上の第 1 パス (`strip_quoted_text` ベース、境界に `;`/`&`/`|`/
# `(`/backtick を許す) をそのまま dquote 内容にも適用すると、コミットメッセージ規約
# `git commit -m "$(cat <<'EOF' ... EOF)"` の本文中に含まれるコマンド例文 (`&&` / `;`
# 直後の例文) まで誤検出して deny してしまう (このプラグインが解消した false-positive
# クラスの再導入になる)。
#
# そこで、左境界を「substitution opener そのもの」(`$(` / `<(` / `>(`) に限定した
# **opener-anchored** 検出を第 2 パスとして追加する。境界を bare `(` や `;`/`&`/`|` に
# 広げないのは、opener の直後に invocation が来る形 (`$(git push ...)`) だけを狙い、
# opener から離れた位置にある「本文中の例文」を誤って拾わないようにするため。
# バッククォートを opener に含めないのは、このリポジトリのコミットメッセージが
# markdown code span (`` `git push origin master` `` のような例文をバッククォートで
# 囲む書き方) を日常的に含むため、backtick を opener にすると house style の
# コミットメッセージが軒並み deny される事故になるから。dquote 内バッククォート置換
# (archaic command substitution) は既知制約として残す (README 参照)。
#
# 検査対象テキストは `strip_squoted_text` (single quote 領域のみ空白化、dquote 領域は
# 保持) を通す。single-quote literal (`echo '$(git push origin master)'` のような
# 実行されない形) を誤検出しないための処理で、dquote 内容は残るのでこちらは検出できる。
SUBST2_OPENER='(\$\(|<\(|>\()'
SUBST2_PUSH_INVOCATION_REGEX="${SUBST2_OPENER}[[:space:]]*${ENV_VAR_PREFIX}git[[:space:]]+(${OPT}${OPT_ARG}[[:space:]]+)*push([[:space:]]|\)|\$)"

_si=0
while [ "$_si" -lt "${#SEGMENTS[@]}" ]; do
  _seg="${SEGMENTS[$_si]}"
  case "$_seg" in
    *'$('*|*'<('*|*'>('*|*'`'*)
      _stripped="$(strip_quoted_text "$_seg")"
      if [[ "$_stripped" =~ $SUBST_PUSH_INVOCATION_REGEX ]]; then
        REASON=$(cat <<'EOF'
プッシュをブロックしました。コマンド置換 `$(...)` / プロセス置換 `<(...)` / `>(...)` / バッククォート内の `git push` は本フックでは解析できないため保守的に deny します。

これらは内部の cwd や `git push` を本 parser から隠蔽する経路で、例えば `echo $(cd /other; git push origin feature)` のようなコマンドはデフォルトブランチ保護を素通りする bypass になり得ます。

置換の外で直接 `git push` を実行してください。
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
      if [[ "$_stripped2" =~ $SUBST2_PUSH_INVOCATION_REGEX ]]; then
        REASON=$(cat <<'EOF'
プッシュをブロックしました。ダブルクォート内であっても command substitution `$(...)` / プロセス置換 `<(...)` / `>(...)` は bash によって実行されるため、`echo "$(git push origin master)"` のような形の `git push` を保守的に deny します。

引用符の内側であっても push は実際に実行されるため、除外することはできません。置換の外で直接 `git push` を実行してください。
EOF
)
        emit_deny "$REASON"
        exit 0
      fi
      ;;
  esac
  _si=$((_si+1))
done

# token level で push invocation を検出する。env-var assignment を skip した後、最初の
# 実 token が `git` (または path-qualified `*/git`) で、続く global option を walk して
# `push` サブコマンドに到達するものだけを拾う。
PUSH_INVOCATION_INDICES=()
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
            push)
              PUSH_INVOCATION_INDICES+=("$_si")
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

# push invocation を 1 つも含まないなら本 hook 対象外。
if [ "${#PUSH_INVOCATION_INDICES[@]}" -eq 0 ]; then
  exit 0
fi

# カレントブランチがデフォルトブランチなら、引数の有無に関わらず push を deny する。
# upstream 設定により `git push` 単独でも remote の master を更新するケースがあるため、
# 引数解析よりも先にここで止める。
BRANCH=$(current_branch)
if [ -n "$BRANCH" ] && is_default_branch "$BRANCH"; then
  emit_deny "$DENY_REASON"
  exit 0
fi

# それ以外のブランチ: 検出した各 push invocation について target-mismatch → refspec の
# 順に検査する。
#
# トレードオフ: `git push origin origin/master` のように remote-tracking ref を引数に
# 直接書く形 (= ローカル `origin/master` ref を remote `master` に push する稀な経路) は
# false negative で通る。cooperative 利用では発生しにくく、誤検出を増やすほうが実害大
# のため許容する。
#
# Git の refspec は `master` の他に `+master` (force-push) や `refs/heads/master`
# (full ref name)、`HEAD:refs/heads/master` (HEAD を default branch に push) のような
# 形でも default branch を更新できる。token / left / right のそれぞれで
# `normalize_refspec_part` を経由して `+` / `refs/heads/` プレフィックスを剥がしてから
# 完全一致比較する。
for _si in "${PUSH_INVOCATION_INDICES[@]}"; do
  # push サブコマンドの位置を再特定する (target-mismatch scope の構築と refspec 検査の
  # 両方で使う)。
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
      push) break ;;
      *) break ;;
    esac
  done

  # target-mismatch scope: 先行 segment (`;` join) + 当該 invocation の「先頭 token
  # (env assignment 含む) から `push` サブコマンド token までの raw token 列を空白 join
  # した invocation prefix」。push より後の引数 (refspec / `--force-with-lease` 等) は
  # scope から除外する。target-mismatch は「invocation より前の cwd/branch 切替」を
  # 検出するためのものであり、invocation 自身の引数 (`git push origin cd` の `cd` が
  # branch 名の場合など) を含めると、引数の文字列がたまたま `cd` 等のキーワードと一致
  # したときに誤 deny する。`GIT_DIR=... git -C dir push` のような env prefix / global
  # option は invocation prefix 側に残るため、target-mismatch 検出は引き続き働く。
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

  # push サブコマンドより後の全 token を refspec として検査する (`_gidx` は上の walk で
  # 既に `push` token の位置を指しているので 1 つ進めて開始する)。
  _gidx=$((_gidx+1))
  while [ "$_gidx" -lt "$_gn" ]; do
    tok=$(strip_shell_quotes "${_gtoks[$_gidx]}")
    case "$tok" in
      # `--all` / `--mirror` は refspec を明示せず全 local branch を remote に push する
      # モード。master/main が local に存在すれば自動的にそれも push されるため、refspec
      # 完全一致比較を素通りする bypass になる。default branch 保護のため一律 deny する。
      --all|--mirror)
        emit_deny "$DENY_REASON"
        exit 0
        ;;
      *:*)
        left=$(normalize_refspec_part "$(strip_shell_quotes "${tok%%:*}")")
        right=$(normalize_refspec_part "$(strip_shell_quotes "${tok#*:}")")
        if is_default_branch "$left" || is_default_branch "$right"; then
          emit_deny "$DENY_REASON"
          exit 0
        fi
        ;;
      *)
        norm=$(normalize_refspec_part "$tok")
        if is_default_branch "$norm"; then
          emit_deny "$DENY_REASON"
          exit 0
        fi
        ;;
    esac
    _gidx=$((_gidx+1))
  done
  unset _gtoks
done

exit 0
