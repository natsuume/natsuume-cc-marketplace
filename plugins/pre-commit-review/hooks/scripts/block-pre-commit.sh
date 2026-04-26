#!/bin/bash
# block-pre-commit.sh
# git commit を未レビューの状態でブロックする PreToolUse フック。

INPUT=$(cat)

# 大半の Bash 呼び出しは git commit と無関係。jq を起動する前に粗フィルタで抜ける。
case "$INPUT" in
  *commit*) ;;
  *) exit 0 ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
if [ -z "$COMMAND" ]; then
  exit 0
fi

# `git \<改行>commit` のような Bash の行継続は実行時にバックスラッシュ+改行が消えて
# `git commit` になる。検出ロジックがこれを見落とさないよう、入力段階で空白に正規化する。
COMMAND=$(printf '%s' "$COMMAND" | awk 'BEGIN { RS = "" } { gsub(/\\\n/, " "); print }')

# PreToolUse の deny payload を出力する共通ヘルパ。各 deny 経路で同じ JSON 構造を
# 生成しているため一箇所に集約しておくと、フィールド名のドリフト (1 箇所だけ
# `permissionDecisionReason` が `reason` になる、等) を防げる。
deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
}

# `git commit` サブコマンドを検出する。
# `git` と `commit` の間に許すのは「`-` で始まる global option と任意の引数値」のみ。
# これにより `git log --grep commit` 等の commit を引数として持つ別サブコマンドを誤検知しない。
#   OPT      : `-x` / `--long` / `--long=val` のような option トークン
#   OPT_ARG  : `-` で始まらないオプション引数 (例: `-C dir` の `dir`)
OPT='-[^[:space:];&|]+'
OPT_ARG='([[:space:]]+[^-[:space:];&|][^[:space:];&|]*)?'
# 検出用 (どこかに `git ... commit` を含むか): 軽量フィルタ
COMMIT_DETECT_REGEX="(^|[^[:alnum:]_-])git[[:space:]]+(${OPT}${OPT_ARG}[[:space:]]+)*commit"
# 連結プレフィックス共通形 (CHAIN_PREFIX_REGEX) と、それを土台に定義する各種検出 regex:
#   セグメント境界 `(^|[;&|])` + 空白 + 任意の env-var assignment + 任意の wrapper
#   (`builtin`/`command`/`eval`、`command -p` 等の flag 列も許容)。
# COMMIT_INVOCATION_REGEX / CD_PREFIX_REGEX / EXPORT_TARGET_REGEX は target keyword を
# CHAIN_PREFIX_REGEX に append して構成する。境界・assignment・wrapper の書き換えが
# 1 か所で完結し、ドリフトを防げる。
#
# COMMIT_INVOCATION_REGEX は help-only スキップ判定と postfix scan の起点取得で
# `BASH_REMATCH[0]` を取り出すために使う。`(^|[;&|])` の前方境界は
# `echo git commit && git commit -m x` のような echo 引数内の偽 git commit に最初に
# match して postfix を `&& git commit -m x` と誤算するのを防ぐ。`GIT_AUTHOR_NAME=bot
# git commit -m one && git commit -m two` や `command git commit && command git commit`
# のような env-var / wrapper 経由の最初の commit も同じ仕組みで正しく match させる。
# BASH_REMATCH[0] に含まれる boundary 文字は postfix 切り出し `${var#*"$rematch"}` の
# strip 対象として共に消えるので副作用なし。
CHAIN_PREFIX_REGEX='(^|[;&|])[[:space:]]*([A-Za-z_][A-Za-z0-9_]*=[^[:space:];&|]*[[:space:]<>]+)*(builtin[[:space:]<>]+|command([[:space:]<>]+-[^[:space:];&|]*)*[[:space:]<>]+|eval[[:space:]<>]+)?'
COMMIT_INVOCATION_REGEX="${CHAIN_PREFIX_REGEX}git[[:space:]]+(${OPT}${OPT_ARG}[[:space:]]+)*commit([[:space:]]|\$)"

if ! printf '%s' "$COMMAND" | grep -qE "${COMMIT_DETECT_REGEX}([[:space:]]|$)"; then
  exit 0
fi

# 置換系の検出には bash の quote 評価規則を反映した dequote を 2 段階で行う:
#   - Stage 1: single quote (`'...'`) のみ剥がす (`COMMAND_NO_SINGLE`)
#       → bash は `'...'` 内では何も評価しない一方、`"..."` 内では `$(...)` /
#         バッククォートを評価する。`$(...)` / バッククォートは single-quote だけ
#         剥がした文字列で検出すれば、`echo '$(...)'` のようなテキスト参照を
#         誤検知せず、`git commit -m "$(rm -rf /)"` のような double-quote 内の
#         実評価ケースは正しく捕捉できる。
#   - Stage 2: double quote (`"..."`) も剥がす (`COMMAND_DEQUOTED`)
#       → 後段のセグメント分割・トークン化に使う。プロセス置換 `<(...)` / `>(...)` は
#         bash の quote 内では評価されないため、完全に dequote した文字列で検出する。
COMMAND_NO_SINGLE=$(printf '%s' "$COMMAND" | sed -E "s/'[^']*'//g")
COMMAND_DEQUOTED=$(printf '%s' "$COMMAND_NO_SINGLE" | sed -E 's/"[^"]*"//g')

# `git commit` がクォート内にしか現れない場合は、テキスト参照かラッパー経由のいずれか。
# クォートを除去した残りに commit がないとき、コマンド先頭がシェルインタプリタなら
# ラッパー (`bash -c "git commit"` 等) として後段で deny し、それ以外はテキスト参照
# (`echo 'git commit' && echo $(date)` 等) として skip する。本判定を後段の置換検査
# よりも **前** に置く: テキスト参照に置換が含まれていても deny ではなく skip するため。
SHELL_WRAPPER_REGEX='^[[:space:]]*(bash|sh|zsh|dash|ksh|eval)([[:space:]]|$)'
IS_SHELL_WRAPPER=0
if printf '%s' "$COMMAND" | grep -qE "$SHELL_WRAPPER_REGEX"; then
  IS_SHELL_WRAPPER=1
fi
if ! printf '%s' "$COMMAND_DEQUOTED" | grep -qE "${COMMIT_DETECT_REGEX}([[:space:]]|$)"; then
  if [ "$IS_SHELL_WRAPPER" -eq 0 ]; then
    exit 0
  fi
fi

# コマンド置換 (`$(...)`, バッククォート) とプロセス置換 (`<(...)`, `>(...)`) は本
# フックの文字列ベースなパーサで内側を解析できず、レビュー検証後に index を
# 書き換える経路となり得る。`$(...)` / バッククォートは double-quote 内でも評価される
# ので `COMMAND_NO_SINGLE` で検出し、`<(...)` / `>(...)` は quote 内では評価されない
# ので `COMMAND_DEQUOTED` で検出する。`<(...)` の検出には paren-strip 前の
# `COMMAND_DEQUOTED` を使う必要があるため、paren-strip より前に配置する。
if [[ "$COMMAND_NO_SINGLE" == *'$('* ]] \
   || [[ "$COMMAND_NO_SINGLE" == *'`'* ]] \
   || [[ "$COMMAND_DEQUOTED" == *'<('* ]] \
   || [[ "$COMMAND_DEQUOTED" == *'>('* ]]; then
  REASON=$(cat <<'EOF'
コミットをブロックしました。`$(...)` / バッククォート / `<(...)` / `>(...)` (コマンド置換・プロセス置換) を含む `git commit` はサポート外です。

これらは内側のコマンドを別 subshell で評価するため、レビュー検証後に index を書き換えたり commit 内部で別コマンドを走らせたりする経路となり得ます。コマンド置換が必要な処理は別の Bash 呼び出しに分離して、結果を `git commit -m "..."` の文字列として直接埋め込んでください。
EOF
)
  deny "$REASON"
  exit 0
fi
# `()` (サブシェル) と `{}` (group) はコマンド境界として `;`/`&`/`|` と等価なため、
# スペースに正規化しておくと下流のセグメント分割・トークン化が均一に動く。これに
# より `(cd /other && git commit)` や `{ GIT_DIR=/x git commit; }` のようなグループ
# 越しの target-mismatch バイパスを cd-prefix / env-var チェックがそのまま捕捉できる。
# `[(){}]` を class として一括置換すると `}` がパラメータ展開の終端と衝突して
# 動かないため、4 回に分けて置換する (各々フォーク無しの parameter expansion)。
COMMAND_DEQUOTED="${COMMAND_DEQUOTED//\(/ }"
COMMAND_DEQUOTED="${COMMAND_DEQUOTED//\)/ }"
COMMAND_DEQUOTED="${COMMAND_DEQUOTED//\{/ }"
COMMAND_DEQUOTED="${COMMAND_DEQUOTED//\}/ }"
# 改行も `;` と等価なコマンド区切りなので、`cd /other<newline>git commit` のように
# 改行で連結された prefix が cd-prefix / 区切り文字検査をすり抜ける経路を塞ぐ。
# `;` に正規化して下流の `[;&|]` パターンに統一して載せる。
COMMAND_DEQUOTED="${COMMAND_DEQUOTED//$'\n'/;}"
# bash の redirection (`>file`, `2>&1`, `&>file`, `<<EOF` 等) は command 名と
# その引数の間を分断し得る (例: `cd>/dev/null /other`, `builtin>/dev/null cd /other`,
# `GIT_DIR=/foo>/dev/null git commit`)。本体コマンドの実行可否判定だけが目的なので、
# redirection 全体をスペースに置換して下流の検査から除外する。pattern は
# `[0-9]? (>>|<<<|<<|<>|>&|<&|>|<) [[:space:]]* [^[:space:];&|]*` で
# fd 番号 / target / heredoc word をまとめて吸収する。
COMMAND_DEQUOTED=$(printf '%s' "$COMMAND_DEQUOTED" \
  | sed -E 's/[0-9]?(>>|<<<|<<|<>|>&|<&|>|<)[[:space:]]*[^[:space:];&|]*/ /g')

# 最初の `git ... commit` 呼び出しの後ろ (= 「実 commit の引数部分以降」) を
# COMMIT_POSTFIX として一度だけ抽出する。help-only スキップ判定と postfix scan の
# 両方が同じ値を必要とするため共通化する (BASH_REMATCH は後段の `[[ =~ ]]` 評価で
# 上書きされるので、結果はスカラ変数に確保する)。
# 早期 COMMIT_DETECT_REGEX フィルタは通っているのに COMMIT_INVOCATION_REGEX が
# 一致しないケース (例: `time git commit ...`, `env git commit ...` のように本フックが
# 認識していない wrapper を介する形) は、postfix scan 起点が取れず未レビュー commit を
# 素通しさせるリスクがある (BASH_REMATCH 空 → POSTFIX 空 → postfix チェックで素通り)。
# wrapper-deny 経路 (`bash -c "..."` 等) は別途 IS_SHELL_WRAPPER で deny されるため、
# wrapper でも commit でもない「parse 不能」状態は保守的に deny する。
COMMIT_POSTFIX=""
if [[ "$COMMAND_DEQUOTED" =~ $COMMIT_INVOCATION_REGEX ]]; then
  COMMIT_POSTFIX="${COMMAND_DEQUOTED#*"${BASH_REMATCH[0]}"}"
elif [ "$IS_SHELL_WRAPPER" -eq 0 ]; then
  REASON=$(cat <<'EOF'
コミットをブロックしました。本フックが解析できない形式の `git commit` 呼び出しが含まれています (例: `time git commit ...`, `env git commit ...` のように未対応の wrapper 経由など)。

`git commit` を直接実行するか、対応している `xxx && git commit ...` 形式 (前段が `cd` / `pushd` / `popd` / target-mismatch を起こす env-var を含まないもの) で連結してください。
EOF
)
  deny "$REASON"
  exit 0
fi

# `git commit --help` (もしくは `-h`) ならスキップ。素朴に「コマンド末尾が --help」を
# 判定すると `git commit -m bad && git commit --help` のように **最初の commit が
# real なケース** までスキップしてしまい、未レビュー commit を素通しする致命的な
# バイパスになる。COMMIT_POSTFIX (= 最初の commit の引数部分以降) が `-h|--help` のみで
# 構成される場合だけスキップする。`-m bad && git commit --help` のような chain では
# COMMIT_POSTFIX が `-m bad && git commit --help` となり、ここでは skip しないため
# real commit として後段の検証へ進む。
if [[ "$COMMIT_POSTFIX" =~ ^[[:space:]]*(-h|--help)[[:space:]]*$ ]]; then
  exit 0
fi

# シェルラッパー (`bash -c "git commit ..."` 等) はクォート内のコマンドを本フックの
# 文字列ベースなパーサで解析できず、`-C` 等の検証もすり抜ける。`git commit` を
# 直接実行する経路 (単独 / `xxx && git commit ...`) のみサポートする。
if [ "$IS_SHELL_WRAPPER" -eq 1 ]; then
  REASON=$(cat <<'EOF'
コミットをブロックしました。`bash -c "..."` のようなシェルラッパー経由の `git commit` はサポート外です。

`git commit` を直接実行してください (前段コマンドが必要な場合は `xxx && git commit ...` 形式で連結できます)。
EOF
)
  deny "$REASON"
  exit 0
fi

# 単独の `&` (background) と `|` (pipeline) は連結というより並列実行になるため、
# `git add newfile & git commit ...` や `cmd | git commit ...` の形式はマーカー検証
# 完了後に index が並行変更されたり stdin 経由で commit に状態が流れ込んだりする
# 経路になる。chain prefix では `&&` / `||` / `;` のみ許容し、単独の `&` / `|` は
# COMMAND_DEQUOTED に存在するだけで deny する。`(^|[^X])X([^X]|$)` で「X が連続
# しないただ 1 個の X」を識別する (`&&` / `||` は連続なのでマッチしない)。
if [[ "$COMMAND_DEQUOTED" =~ (^|[^&])\&([^&]|$) ]] \
   || [[ "$COMMAND_DEQUOTED" =~ (^|[^\|])\|([^\|]|$) ]]; then
  REASON=$(cat <<'EOF'
コミットをブロックしました。`&` (background) や `|` (pipeline) で `git commit` と他コマンドを連結する形式はサポート外です。

これらの区切りはコマンドを並列実行するため、レビューマーカー検証後に index が変更されたり commit へ状態が流れ込んだりする経路になります。

連結が必要なら `&&` (success-and) や `;` (sequential) を使用してください。並列実行や pager 接続が必要な場合は別の Bash 呼び出しに分けてください。
EOF
)
  deny "$REASON"
  exit 0
fi

# 連結プレフィックスに `cd` / `pushd` / `popd` が含まれる場合、hook 検証時の cwd と
# commit 実行時の cwd が乖離し、別リポジトリ (or 同一 repo の別 worktree) のマーカーで
# commit を許可してしまう経路になる (`-C` deny と同じ target-mismatch)。
# 同一リポジトリ内のサブディレクトリへの cd でも cwd 不一致は残るうえ、in-repo か
# 別 repo かを静的に判別する手段がないため一律 deny する。
# `${var%[;&|]*}` は最後のシェル区切り文字以降を取り除くので、prefix が full と
# 異なるとき = 連結が存在するときだけ検査する。`git commit ; cd subdir` のように
# cd が postfix にあるケースは prefix に cd が出ないため誤検知せず、後段の postfix
# チェックで適切な deny メッセージが出る。
#
# `builtin cd` / `command cd` / `eval cd` のように cd 系コマンドの前に
# bash の builtin/command/eval ラッパーを挟む形式もカバーする (各々 alias/function
# を回避して cd 本体を実行できるため、素の `cd` と等価な cwd 変更経路になる)。
# `eval` は連結先頭にある場合 `IS_SHELL_WRAPPER` の deny で先に弾かれるが、
# 連結途中 (`xxx && eval cd ...`) では先頭ラッパー検出をすり抜けるため、こちらでも
# 拾う必要がある。
# bash の redirection (`cd>/dev/null /other`) は command 名直後に空白なく `>`/`<` が
# 来ても本体は実行される。command 名の直後・wrapper 名の直後の boundary class に
# `<>` を含めて、`cd>file /other && git commit` 等の bypass を捕捉する。
# `command` には `-p` (default PATH 利用), `-v`/`-V` (情報表示) 等の flag があり、
# `command -p cd /other` のように flag を挟んでも cd 本体は実行される。command の
# wrapper 部分には flag 列 `(-[^...]*)*` を許容する。`-v` は実行しないが保守的に deny
# する (cooperative 利用での実害なし)。
# bash は `FOO=bar cd /other` のように command の前に env-var assignment を置ける。
# その assignment は cd 実行時の environment に適用されるが cd 本体は実行されるので、
# `(name=value[[:space:]<>]+)*` を wrapper の前に optional で許容して捕捉する。
CD_PREFIX_REGEX="${CHAIN_PREFIX_REGEX}(cd|pushd|popd)([[:space:]<>]|\$)"
# `export GIT_DIR=...` / `declare -x GIT_DIR=...` / `typeset -x ...` / `readonly -x ...`
# のように、対象切替系の env-var を前段で export すると後段の `git commit` にも
# 引き継がれて target-mismatch を起こす (bare `GIT_DIR=/foo git commit` は LAST_SEGMENT
# 経由の TARGET_OVERRIDE_REGEX で捕捉済みだが、別セグメントの export はそこに現れない)。
# `-x` 有無は識別せず `declare GIT_DIR` 等も保守的に deny する (cooperative 利用では
# 副作用なし)。
# `[^;&|]*` を素のままにすると `GIT_DIR` が `MY_GIT_DIR` / `GIT_DIRECTOR` /
# `FOOGIT_DIR=foo` 等の部分一致でも match して false positive になる。
# canonical name の左側は「先頭 or 直前にスペース」を要求し (= タンデム空白で区切られた
# 独立トークンであることを保証)、右側は `[[:space:]]|=|$` で名前末尾を boundary 化する。
EXPORT_TARGET_REGEX="${CHAIN_PREFIX_REGEX}(export|declare|typeset|readonly)[[:space:]<>]+([^;&|]*[[:space:]<>])?(GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE)([[:space:]<>]|=|\$)"
COMMAND_PREFIX="${COMMAND_DEQUOTED%[;&|]*}"
if [ "$COMMAND_PREFIX" != "$COMMAND_DEQUOTED" ] \
   && [[ "$COMMAND_PREFIX" =~ $EXPORT_TARGET_REGEX ]]; then
  REASON=$(cat <<'EOF'
コミットをブロックしました。`export GIT_DIR=...` / `declare -x GIT_DIR=...` 等で対象リポジトリ・インデックスを切り替える環境変数を前段でセットする形式の commit はサポート外です (検証先と commit 先が食い違うのを防ぐため)。

これらの環境変数を export せず、対象リポジトリへ移動してから `git commit` を実行してください (Claude の作業ディレクトリと commit 先が同じになるようにしてください)。
EOF
)
  deny "$REASON"
  exit 0
fi
if [ "$COMMAND_PREFIX" != "$COMMAND_DEQUOTED" ] \
   && [[ "$COMMAND_PREFIX" =~ $CD_PREFIX_REGEX ]]; then
  REASON=$(cat <<'EOF'
コミットをブロックしました。`cd` / `pushd` / `popd` を前段に含む連結 commit はサポート外です。

hook の検証 cwd と commit 実行時の cwd が乖離するため、別リポジトリ (もしくは別 worktree) のマーカーで commit が許可されてしまう経路になります。これは `-C` / `--git-dir` / `--work-tree` を deny する理由と同じ target-mismatch です。

対象ディレクトリへ移動してから別の Bash 呼び出しで `git commit ...` を実行してください (Claude の作業ディレクトリと commit 先が同じになるようにしてください)。
EOF
)
  deny "$REASON"
  exit 0
fi

# `git -C` 等の対象リポジトリ変更系オプションを deny する。検査範囲はサブコマンド
# `commit` トークンの **直前** までに限定する。空白区切りトークン化したうえで、
# 直前のトークンが「別トークン引数を取るグローバルオプション」だった場合は
# 引数値とみなして subcommand 判定をスキップする。これにより
# `git --namespace commit -C ../other commit ...` のように option 値が偶然
# "commit" のケースでも subcommand 境界を取り違えない。`=` 形式
# (`--namespace=commit`) は単一トークンなので別トークン引数の対象外。
#
# `xxx && git commit ...` のような連結形式では、前段コマンド (`xxx`) が偶然 `-C`
# を含む (例: `tar -C /tmp ...`) と false positive で deny になってしまう。
# 後段の postfix チェックで `commit` がコマンド末尾に位置することを強制するため、
# `;`/`&`/`|` 区切りの最終セグメント (= `${COMMAND_DEQUOTED##*[;&|]}`) を見れば
# commit を含むセグメントが取れる。検査範囲をそこに限定することで前段コマンドの
# `-C` 誤検知を防ぐ。bash の parameter expansion を使って awk フォークを省略する。
#
# `read -ra` は `\<space>` を 2 トークンに分割してしまうため、トークン化前に
# バックスラッシュエスケープ空白を非空白プレースホルダ \x01 に置換する。
# これにより `git -c foo=a\ commit ...` の `a commit` 部分が単一トークンとして
# 維持され、subcommand 境界判定の取り違えを防げる。
LAST_SEGMENT="${COMMAND_DEQUOTED##*[;&|]}"
LAST_SEGMENT_NORMALIZED=$(printf '%s' "$LAST_SEGMENT" \
  | sed -E "s/\\\\[[:space:]]/$(printf '\x01')/g")
read -ra _tokens <<< "$LAST_SEGMENT_NORMALIZED"
sandbox_prefix_tokens=()
prev_is_argopt=0
for _tok in "${_tokens[@]}"; do
  if [ "$prev_is_argopt" -eq 1 ]; then
    sandbox_prefix_tokens+=("$_tok")
    prev_is_argopt=0
    continue
  fi
  [ "$_tok" = "commit" ] && break
  case "$_tok" in
    -C|-c|--git-dir|--work-tree|--namespace|--super-prefix|--exec-path)
      prev_is_argopt=1 ;;
  esac
  sandbox_prefix_tokens+=("$_tok")
done
SANDBOX_PREFIX="${sandbox_prefix_tokens[*]}"
# git は `-C` / `--git-dir` / `--work-tree` オプションだけでなく、`GIT_DIR=/path`
# `GIT_WORK_TREE=/path` `GIT_INDEX_FILE=/path` 環境変数による対象切替もサポートする。
# どちらも commit 先のリポジトリ/インデックスを hook 検証時の cwd と乖離させる
# target-mismatch 経路になるため、両形式とも deny する。env-var は `name=value` 形式
# でコマンド先頭または space 区切りで現れる必要があるため、後段の `=` は不要。
TARGET_OVERRIDE_REGEX='(^|[[:space:]=<>])(-C|--git-dir|--work-tree)([[:space:]=<>]|$)|(^|[[:space:]<>])(GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE)='
if printf '%s' "$SANDBOX_PREFIX" | grep -qE "$TARGET_OVERRIDE_REGEX"; then
  REASON=$(cat <<'EOF'
コミットをブロックしました。`-C` / `--git-dir` / `--work-tree` オプションまたは `GIT_DIR` / `GIT_WORK_TREE` / `GIT_INDEX_FILE` 環境変数で対象リポジトリ・インデックスを変更する形式の commit はサポート外です (検証先と commit 先が食い違うのを防ぐため)。

これらの設定を使わず、対象リポジトリへ移動してから `git commit` を実行してください (Claude の作業ディレクトリと commit 先が同じになるようにしてください)。
EOF
)
  deny "$REASON"
  exit 0
fi

# `git commit -m reviewed && ... && git commit unreviewed` のように、commit の後に
# シェル区切り文字を伴う追加コマンドが続くと、マーカー消費後に未レビューな commit が
# 走り得る。1 マーカー = 1 commit を保証するため、COMMIT_POSTFIX (= 実 commit 呼び出し
# 以降のテキスト、上で抽出済み) に区切り文字を含むコマンドは deny する。
if printf '%s' "$COMMIT_POSTFIX" | grep -qE '[;&|]'; then
  REASON=$(cat <<'EOF'
コミットをブロックしました。`git commit` の後に `;`, `&`, `&&`, `||`, `|` などのシェル区切り文字が続く複合コマンドはサポート外です (1 マーカー = 1 commit を保証するため)。

`git commit` は単独の Bash コマンドとして実行し、後続コマンドは別の Bash 呼び出しに分けてください。
EOF
)
  deny "$REASON"
  exit 0
fi

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0
SIMPLIFIED_MARKER="$GIT_DIR/.claude-pre-commit-simplified"
CODEX_MARKER="$GIT_DIR/.claude-pre-commit-codex-reviewed"

SIMPLIFIED_HASH=$([ -f "$SIMPLIFIED_MARKER" ] && cat "$SIMPLIFIED_MARKER" 2>/dev/null)
CODEX_HASH=$([ -f "$CODEX_MARKER" ] && cat "$CODEX_MARKER" 2>/dev/null)

# 両マーカーとも欠落の最頻 deny パスでは git diff を呼ばずに即 deny できるよう、
# ハッシュ計算は少なくとも片方のマーカーが存在するときだけ走らせる。
CURRENT_HASH=""
if [ -n "$SIMPLIFIED_HASH" ] || [ -n "$CODEX_HASH" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # shellcheck source=lib/diff-hash.sh
  source "$SCRIPT_DIR/lib/diff-hash.sh"
  CURRENT_HASH=$(compute_review_hash)
fi

# 双方のマーカーが現在の差分と一致 = 「現状の staged+unstaged に対して
# /simplify と /codex:review が直近で実走済み」を意味する。
# 修正が入って差分が変わると片方または両方が失効し、ループ再実行が要求される。
if [ -n "$SIMPLIFIED_HASH" ] && [ -n "$CODEX_HASH" ] \
   && [ "$SIMPLIFIED_HASH" = "$CURRENT_HASH" ] \
   && [ "$CODEX_HASH" = "$CURRENT_HASH" ]; then
  rm -f "$SIMPLIFIED_MARKER" "$CODEX_MARKER"
  exit 0
fi

format_status() {
  local stored="$1"
  if [ -z "$stored" ]; then
    printf '未実行'
  elif [ "$stored" = "$CURRENT_HASH" ]; then
    printf '✓ 最新の差分でレビュー済み'
  else
    printf '⚠ 失効 (差分が変わったため再実行が必要)'
  fi
}
SIMPLIFIED_STATUS=$(format_status "$SIMPLIFIED_HASH")
CODEX_STATUS=$(format_status "$CODEX_HASH")

REASON=$(cat <<EOF
コミットをブロックしました。コミット前に下記のレビューを実行してください。

レビュー状態 (双方が「✓ 最新の差分でレビュー済み」になるとコミットが許可されます):
  /simplify      : $SIMPLIFIED_STATUS
  /codex:review  : $CODEX_STATUS

実行手順 (修正が落ち着くまでループ):
  1. /simplify を Skill tool で呼び出す (コード変更を伴うため先に実行)
  2. /codex:review --wait を Skill tool で呼び出す (read-only のコードレビュー)
  3. レビュー結果に指摘があれば修正し、\`git add\` で再ステージングする
  4. ステージング差分が変わるとマーカーは自動的に失効する。
     その場合は手順 1〜2 を最初から再実行する
     (/codex:review の修正で /simplify 対象が新たに発生する可能性も、
      /simplify の修正で /codex:review の新規指摘が出る可能性もあるため、
      両方を再走させる)
  5. 双方のマーカーが「✓ 最新の差分でレビュー済み」になったら \`git commit\` を再試行する

マーカーは PostToolUse hook (auto-mark.sh) が \`/simplify\` と
\`/codex:review --wait\` の実行完了を検知して自動的に記録します。手動で
スクリプトを呼び出す必要はありません。コミット成功時にはマーカーを
自動削除するため、次回のコミットでは再度レビューが必要になります。

ループ回数の上限は設けません。Claude が自身の判断で「修正不要」または
「人間判断を仰ぐべき」と判断したタイミングで進行/エスカレートしてください。

\`/codex:review\` の実行方式 (Claude が自律判断し、ユーザーには確認しないこと):
  - **\`--wait\` (フォアグラウンド) のみサポート**。本コンテキストでは
    \`--background\` は使用しないこと。auto-mark hook は Bash tool 完了時に
    発火するため、background 起動だとレビュー完了前に PostToolUse が走り、
    マーカーが更新されない (= ループが永遠に閉じない)
  - 単体の \`/codex:review\` は通常 AskUserQuestion で実行方式を尋ねるが、
    pre-commit-review の文脈では明示的に \`--wait\` を渡して質問をスキップする

⚠ 重要: \`/codex:review\` であって \`/codex:rescue\` ではありません。両者は別コマンドで、
  - \`/codex:review\`: read-only のコードレビュー (本プラグインが要求する用途)
  - \`/codex:rescue\`: 修正・調査を delegate する subagent (本プラグインの用途には不適)

Claude は名前の似た \`/codex:rescue\` を誤って選ぶ傾向が報告されています。
コマンド名を必ず確認してください。\`/codex:review\` は frontmatter で
\`disable-model-invocation: true\` が指定されている場合 Skill tool から
呼び出せません。その場合は姉妹プラグイン
\`codex-review-customize\` の \`/codex-review-customize:setup\` を実行して
パッチを適用してください。

(注: \`/code-review:code-review\` は PR を対象とするため、PR 作成後に
post-pr-review プラグイン経由で実行されます。)
EOF
)

deny "$REASON"
