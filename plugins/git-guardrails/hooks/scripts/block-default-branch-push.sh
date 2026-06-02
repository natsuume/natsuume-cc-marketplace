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

# 入力の改行を扱う (順序重要):
#   1. 行継続 `\<改行>` (= バックスラッシュ + real newline) は実行時に両方とも消えて
#      隣接トークンに連結される。先にこれを空白に置換しないと、`git \<NL>push` のような
#      入力が次の `;` 置換で `git \;push` に化けて PUSH_INVOCATION_REGEX が不一致になる。
#      `${COMMAND//$'\\\n'/ }` を直接書かないのは macOS bash 3.2.57 互換性のため (詳細
#      は cmd-parser.sh の `_normalize_line_continuations_impl` を参照)。
#   2. 行継続を消した残りの real newline を `;` (コマンド区切り等価) に正規化する。
#      multi-line command (`echo ok\ngit push origin master`) の 2 行目以降の保護対象
#      invocation を検出するため。`grep -qE` が改行をパターン中に含められない / bash の
#      `=~` 境界クラスに改行を直接書くと grep 経路でエラーになる事情を回避する目的でも
#      ある。改行は bash でもコマンド区切り (`;` 等価) なので置換しても解釈は変わらない。
#      `${COMMAND//$'\n'/;}` (LF のみ) は bash 3.2 で正しく動作する (backslash と
#      組み合わせた `$'\\\n'` pattern とは別)。
# (SCRIPT_DIR は冒頭の exit-trap install 時に定義済み。)
# shellcheck source=lib/cmd-parser.sh
source "$SCRIPT_DIR/lib/cmd-parser.sh"
# fast-path: line continuation を含まない 99% の入力では `$(...)` subshell fork を回避
# する (詳細は cmd-parser.sh の hot-path 設計コメント参照、 実測で bash 3.2 で +637us
# の fork コストが消える)。
case "$COMMAND" in
  *\\$'\n'*) COMMAND=$(normalize_line_continuations_to_space "$COMMAND") ;;
esac
COMMAND="${COMMAND//$'\n'/;}"

# `git push` で始まる (もしくは連結 prefix 経由で git push を含む) コマンドだけが対象。
# git の global option (`-C`, `-c key=val`, `--git-dir=path` 等) を任意個許容したうえで
# `push` サブコマンドが続く形を拾う。
#   OPT      : `-x` / `--long` / `--long=val` のような option トークン
#   OPT_ARG  : `-` で始まらないオプション引数 (例: `-C dir` の `dir`)
OPT='-[^[:space:];&|]+'
OPT_ARG='([[:space:]]+[^-[:space:];&|][^[:space:];&|]*)?'
# 左境界はシェルのコマンド開始位置 (`^` / `;` / `&` / `|`) のみ。`[[:space:]]` 全体を
# 境界に含めると `echo git push origin master` のような echo 引数内の text reference が
# `echo` と `git` の間のスペースを境界として誤マッチする。`time git push` のような未対応
# wrapper 経由は false negative で通すが、cooperative 利用では発生しにくいため許容。
#
# env-var assignment (`GIT_DIR=/foo/.git git push ...` / `FOO=bar BAZ=qux git push ...`) は
# git の前に prefix として現れる。`NAME=VALUE` トークンを 0 個以上許容して invocation
# を捕捉する (= invocation 自体は検出して、target-mismatch スコープに env-var を含めて
# has_target_mismatch_prefix で deny に倒す経路に乗せる)。
ENV_VAR_PREFIX='([A-Za-z_][A-Za-z0-9_]*=[^[:space:];&|]*[[:space:]]+)*'
PUSH_INVOCATION_REGEX="(^|[;&|])[[:space:]]*${ENV_VAR_PREFIX}git[[:space:]]+(${OPT}${OPT_ARG}[[:space:]]+)*push([[:space:]]|\$)"

if ! printf '%s' "$COMMAND" | grep -qE "$PUSH_INVOCATION_REGEX"; then
  exit 0
fi

# shellcheck source=lib/default-branch.sh
source "$SCRIPT_DIR/lib/default-branch.sh"

DENY_REASON='デフォルトブランチ (master/main) への直接 push は禁止されています。working branch から PR を作成し、GitHub 上で merge してください。'

# target-mismatch 検査は while ループの中で各 push 呼び出しの「前段 + invocation」を
# 対象に行う (post-push の `&& cd ..` を target-mismatch 扱いしない、かつ invocation 内の
# `git -C dir push` は拾う)。

# カレントブランチがデフォルトブランチなら、引数の有無に関わらず push を deny する。
# upstream 設定により `git push` 単独でも remote の master を更新するケースがあるため、
# 引数解析よりも先にここで止める。
BRANCH=$(current_branch)
if [ -n "$BRANCH" ] && is_default_branch "$BRANCH"; then
  emit_deny "$DENY_REASON"
  exit 0
fi

# それ以外のブランチ: push の引数に master/main が refspec として含まれていれば deny。
# 全 command 文字列に対する grep だと、`git push origin feature/main` のような working
# branch 名や `git push origin feat && git switch main` のような連結後段の master/main
# トークンを誤検出する。push サブコマンドの引数範囲だけ取り出してから、各 token を
# 完全一致 (refspec の場合は `:` 分割の左右) で is_default_branch 比較する。
#
# トレードオフ: `git push origin origin/master` のように remote-tracking ref を引数に
# 直接書く形 (= ローカル `origin/master` ref を remote `master` に push する稀な経路) は
# false negative で通る。cooperative 利用では発生しにくく、誤検出を増やすほうが実害大
# のため許容する。
# `git push origin feat && git push origin master` のように複数の push を含む chained
# コマンドでは、各 push 呼び出しを独立に検査する。`[[ =~ ]]` ループで COMMAND の残り
# (REM) から PUSH_INVOCATION_REGEX を先頭から消費していき、すべての push 呼び出しが
# 検査されるまで継続する。`${BASH_REMATCH[0]}` は最低でも "git push" を含むため REM は
# 必ず短くなり、無限ループにならない。
#
# Git の refspec は `master` の他に `+master` (force-push) や `refs/heads/master`
# (full ref name)、`HEAD:refs/heads/master` (HEAD を default branch に push) のような
# 形でも default branch を更新できる。token / left / right のそれぞれで
# `normalize_refspec_part` を経由して `+` / `refs/heads/` プレフィックスを剥がしてから
# 完全一致比較する。
REM="$COMMAND"
while [[ "$REM" =~ $PUSH_INVOCATION_REGEX ]]; do
  PUSH_INVOCATION="${BASH_REMATCH[0]}"
  PUSH_POSTFIX="${REM#*"$PUSH_INVOCATION"}"
  # 当該 push の「前段 + invocation」で target-mismatch を検査する (post-push の cd 等は
  # 対象外)。同一コマンドに複数の push が含まれる場合は、各 iteration で対応する prefix
  # を切り出して独立に判定する。
  PUSH_TARGET_SCOPE="${REM%%"$PUSH_INVOCATION"*}$PUSH_INVOCATION"
  if has_target_mismatch_prefix "$PUSH_TARGET_SCOPE"; then
    emit_deny "$TARGET_MISMATCH_DENY_REASON"
    exit 0
  fi
  # 1 つの push の引数範囲は、次のシェル区切り文字 (`;`/`&`/`|`) までに限定する。
  # `&&`/`||` は連続文字だが、片方だけ拾えば次のコマンドを引数から除外できる。
  PUSH_ARGS="${PUSH_POSTFIX%%[;&|]*}"
  read -ra PUSH_TOKENS <<< "$PUSH_ARGS"
  for tok in "${PUSH_TOKENS[@]}"; do
    tok=$(strip_shell_quotes "$tok")
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
  done
  REM="$PUSH_POSTFIX"
done

exit 0
