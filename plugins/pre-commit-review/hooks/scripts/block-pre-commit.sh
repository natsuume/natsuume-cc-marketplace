#!/bin/bash
# block-pre-commit.sh
# git commit を未レビューの状態でブロックする PreToolUse フック。
# 緩和方針 / 残す deny の根拠は README の v0.4.0 緩和節を参照。

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
COMMAND="${COMMAND//$'\\\n'/ }"

# 改行は **context-aware** に変換する:
#   - quote 内 (`"..."` または `'...'`) の改行 → スペース。`git commit -m "$(cat <<'EOF'
#       fix
#       EOF
#       )"` のような heredoc 埋め込み commit message を後段の sed `s/"[^"]*"//g` が
#       行単位で動く都合上 double quote として 1 行に纏める必要があるため。
#   - quote 外の改行 → `;` (コマンド区切り)。素の改行で連結した `git commit -m a\n
#       git commit -m b` を一律スペース化すると、postfix `[;&|]` deny を素通りして
#       2 つ目の未レビュー commit が走る (= 1 マーカー = 1 commit 保証の貫通バイパス) ため、
#       quote 外の改行は区切り文字として保持し下流の deny ロジックに載せる必要がある。
# 簡易 quote tracker で in_squote / in_dquote を追う。bash のフル POSIX 規則ではなく、
# cooperative 利用に出てくる範囲 (heredoc の double-quoted、英文 single-quoted) を網羅する
# 軽量 parser。`\` エスケープは quote 外と double quote 内で次の 1 文字を保護する。
# 既知の limitation: `<<EOF ... EOF` の heredoc body は bash 上では quote 評価されないが、
# 本 tracker は body 中の `'` / `"` を素朴にトグルしてしまう。cooperative 利用では
# 実害が出にくく、仮にトグル誤りで quote 状態がずれても下流の sed quote-strip と postfix
# scan で deny 寄りに倒れるため、loop discipline の貫通バイパスにはならない。
COMMAND=$(printf '%s' "$COMMAND" | awk '
BEGIN { in_squote = 0; in_dquote = 0 }
{
  if (NR > 1) {
    if (in_dquote || in_squote) printf " "; else printf ";"
  }
  printf "%s", $0
  L = length($0); bs = 0
  for (i = 1; i <= L; i++) {
    c = substr($0, i, 1)
    if (in_squote) {
      if (c == "\047") in_squote = 0
      continue
    }
    if (bs) { bs = 0; continue }
    if (c == "\\") { bs = 1; continue }
    if (c == "\"") { in_dquote = !in_dquote; continue }
    if (c == "\047" && !in_dquote) in_squote = 1
  }
}
END { if (NR > 0) print "" }
')

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
# COMMIT_INVOCATION_REGEX は target keyword を CHAIN_PREFIX_REGEX に append して構成する。
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

# 後段のセグメント分割・トークン化のために single quote と double quote の両方を剥がす。
# 1 つの sed invocation で両 quote を strip して fork を 1 つに抑える。
# **順序重要**: double quote を先に剥がす。逆順だと `git commit -m "don't" && git commit -m "won't"`
# のような英語アポストロフィを含む commit message で、最初の `'` 〜 最後の `'` までを 1 つの
# single-quoted region と誤認し、間に挟まれた `&& git commit -m "won` ごと食ってしまう。
# 結果として COMMIT_POSTFIX が空になり、postfix `[;&|]` deny を素通りして 2 つ目の commit が
# 通る (= 1 マーカー = 1 commit 保証を貫通する) 致命的バイパスになる。
# double quote を先に剥がせば、内側の `'` は double quote 削除と同時に消えるため誤誘発しない。
# `git commit -m "$(cat <<'EOF' ... EOF)"` のような heredoc 埋め込み commit message は
# 本プラグインの想定する正常 path であり、置換構文 (`$(...)`, バッククォート, `<(...)`, `>(...)`)
# を deny する旧ロジックは撤廃した (cooperative 利用前提)。
COMMAND_DEQUOTED=$(printf '%s' "$COMMAND" | sed -E -e 's/"[^"]*"//g' -e "s/'[^']*'//g")

# `git commit` がクォート内にしか現れない場合は、テキスト参照かラッパー経由のいずれか。
# クォートを除去した残りに commit がないとき、コマンド先頭がシェルインタプリタなら
# ラッパー (`bash -c "git commit"` 等) として後段で deny し、それ以外はテキスト参照
# (`echo 'git commit' && echo $(date)` 等) として skip する。
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

# `()` (サブシェル) と `{}` (group) はコマンド境界として `;`/`&`/`|` と等価なため、
# スペースに正規化しておくと下流のセグメント分割・トークン化が均一に動く。`[(){}]` を
# class として一括置換すると `}` がパラメータ展開の終端と衝突して動かないため、
# 4 回に分けて置換する (各々フォーク無しの parameter expansion)。
COMMAND_DEQUOTED="${COMMAND_DEQUOTED//\(/ }"
COMMAND_DEQUOTED="${COMMAND_DEQUOTED//\)/ }"
COMMAND_DEQUOTED="${COMMAND_DEQUOTED//\{/ }"
COMMAND_DEQUOTED="${COMMAND_DEQUOTED//\}/ }"
# (改行は入力直後にスペースへ正規化済み — cd deny を撤廃した本バージョンでは
# 改行を「コマンド境界」として扱う必要がなく、quote 整合を優先している)
# bash の redirection (`>file`, `2>&1`, `&>file`, `<<EOF` 等) は command 名と
# その引数の間を分断し得る。本体コマンドの実行可否判定だけが目的なので、
# redirection 全体をスペースに置換して下流の検査から除外する。
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

`git commit` を直接実行するか、対応している `xxx && git commit ...` 形式 (cd や heredoc 埋め込みは許容) で連結してください。
EOF
)
  deny "$REASON"
  exit 0
fi

# `git commit --help` (もしくは `-h`) ならスキップ。素朴に「コマンド末尾が --help」を
# 判定すると `git commit -m bad && git commit --help` のように **最初の commit が
# real なケース** までスキップしてしまい、未レビュー commit を素通しする致命的な
# バイパスになる。COMMIT_POSTFIX (= 最初の commit の引数部分以降) が `-h|--help` のみで
# 構成される場合だけスキップする。
if [[ "$COMMIT_POSTFIX" =~ ^[[:space:]]*(-h|--help)[[:space:]]*$ ]]; then
  exit 0
fi

# シェルラッパー (`bash -c "git commit ..."` 等) はクォート内のコマンドを本フックの
# 文字列ベースなパーサで解析できず、postfix scan も成立しない。`git commit` を
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
# COMMAND_DEQUOTED に存在するだけで deny する。
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/loop-counter.sh
source "$SCRIPT_DIR/lib/loop-counter.sh"

# `/codex:review --wait` の完了が連続して何回走ったか。auto-mark.sh が成功時に +1 する。
# commit 通過時 (両マーカー一致) にカウンタを削除して 0 起算にリセット。
# 閾値を超えても commit を block しない (loop discipline はマーカーのハッシュ比較で
# 既に保証されている)。閾値超過時は deny メッセージに `/codex:adversarial-review` の
# 案内文を追加する形で「実装表層の修正だけで収束しないなら設計レベルの見直しを」と
# 促す。adversarial review 自体はマーカーを書かないため、ループ進行をブロックしない。
LOOP_THRESHOLD=3
LOOP_COUNT=$(read_loop_count "$GIT_DIR")

SIMPLIFIED_HASH=$([ -f "$SIMPLIFIED_MARKER" ] && cat "$SIMPLIFIED_MARKER" 2>/dev/null)
CODEX_HASH=$([ -f "$CODEX_MARKER" ] && cat "$CODEX_MARKER" 2>/dev/null)

# 両マーカーとも欠落の最頻 deny パスでは git diff を呼ばずに即 deny できるよう、
# ハッシュ計算は少なくとも片方のマーカーが存在するときだけ走らせる。
CURRENT_HASH=""
if [ -n "$SIMPLIFIED_HASH" ] || [ -n "$CODEX_HASH" ]; then
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
  reset_loop_count "$GIT_DIR"
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

# loop counter が閾値以上なら「設計レベルの再考」を促す追加文を組み立てる。
# 通常の deny メッセージ末尾に追記される形で出力する。
ADVERSARIAL_NOTE=""
if [ "$LOOP_COUNT" -ge "$LOOP_THRESHOLD" ]; then
  ADVERSARIAL_NOTE=$(cat <<EOF

⚠ レビューループが ${LOOP_COUNT} 回に達しています (閾値 ${LOOP_THRESHOLD} 回)。
\`/codex:review\` の指摘修正だけで収束しない場合、根本的な実装方針・アーキテクチャ設計に
ミスマッチがある可能性があります。次のいずれかの対応を検討してください:

  - **\`/codex:adversarial-review --wait --scope working-tree\`** を Skill tool で呼び出し、
    現在のステージング/作業内容に対する **批判的レビュー** (採用しているアプローチ自体が
    妥当か、設計選択のトレードオフ、暗黙の前提が壊れていないか) を取得する。
    実装表層を見直す \`/codex:review\` とは別観点なので、ループの停滞を打開する手がかりに
    なる場合があります。
  - 大きな方針転換が必要そうなら、ユーザーに状況をエスカレートして判断を仰ぐ。

\`/codex:adversarial-review\` は本ループのマーカー対象外です (= 実行してもマーカーは更新
されません)。実行後は通常通り \`/simplify\` → \`/codex:review --wait\` を走らせて
コミットへ進んでください。

\`/codex:adversarial-review\` を Skill tool から呼ぶには姉妹プラグイン
\`codex-review-customize\` の \`/codex-review-customize:setup\` でパッチを適用しておく必要が
あります。未適用の場合は会話入力としての
\`/codex:adversarial-review --wait --scope working-tree\` を使用してください。
EOF
)
fi

REASON=$(cat <<EOF
コミットをブロックしました。コミット前に下記のレビューを実行してください。

レビュー状態 (双方が「✓ 最新の差分でレビュー済み」になるとコミットが許可されます):
  /simplify      : $SIMPLIFIED_STATUS
  /codex:review  : $CODEX_STATUS
  ループ回数     : ${LOOP_COUNT} 回 (閾値 ${LOOP_THRESHOLD} 回でアーキテクチャレビュー誘導)

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

ループ回数の上限は設けません (実装表層レビューの強制ブロックはマーカーのハッシュ比較
のみで行います)。Claude が自身の判断で「修正不要」または「人間判断を仰ぐべき」と
判断したタイミングで進行/エスカレートしてください。

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

(注: PR 作成後の adversarial レビューは post-pr-review プラグイン経由で
 \`/codex:adversarial-review\` が起動されます。)$ADVERSARIAL_NOTE
EOF
)

deny "$REASON"
