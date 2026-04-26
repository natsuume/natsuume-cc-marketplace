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

# クォート部分を空にしてシェル区切り文字や `commit` の有無を判定するための共通処理。
# ダブル/シングルクォートで囲まれた範囲だけを除去する (シンプルな対応で大半をカバー)。
dequote() {
  printf '%s' "$1" | sed -E 's/"[^"]*"//g' | sed -E "s/'[^']*'//g"
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
# 検証用 (コマンドが `git ... commit` で始まるか): バイパス防止のため厳格なアンカー付き
COMMIT_ANCHORED_REGEX="^[[:space:]]*git[[:space:]]+(${OPT}${OPT_ARG}[[:space:]]+)*commit"

if ! printf '%s' "$COMMAND" | grep -qE "${COMMIT_DETECT_REGEX}([[:space:]]|$)"; then
  exit 0
fi

# `git commit` がクォート内にしか現れない場合は、テキスト参照かラッパー経由のいずれか。
# クォートを除去した残りに commit がないとき、コマンド先頭がシェルインタプリタなら
# ラッパー (`bash -c "git commit"` 等) として扱い、それ以外はテキスト参照 (`grep "git commit" ...`) として skip する。
COMMAND_DEQUOTED=$(dequote "$COMMAND")
SHELL_WRAPPER_REGEX='^[[:space:]]*(bash|sh|zsh|dash|ksh|eval)([[:space:]]|$)'
if ! printf '%s' "$COMMAND_DEQUOTED" | grep -qE "${COMMIT_DETECT_REGEX}([[:space:]]|$)"; then
  if ! printf '%s' "$COMMAND" | grep -qE "$SHELL_WRAPPER_REGEX"; then
    exit 0
  fi
fi

# コマンド全体が単独の `git commit --help` (もしくは `-h`) ならスキップ。
# 連結 (`git commit --help && git commit -m bad`) を経由するバイパスを防ぐため、
# 両端アンカー付きで「ヘルプ呼び出し以外を含まない」ことを要求する。
HELP_ONLY_REGEX="${COMMIT_ANCHORED_REGEX}[[:space:]]+(-h|--help)[[:space:]]*\$"
if printf '%s' "$COMMAND" | grep -qE "$HELP_ONLY_REGEX"; then
  exit 0
fi

# 先頭が `git ... commit` でないコマンドは、ラッパーやチェーンを通じてレビューを
# すり抜けるリスクがあるため一律 deny する (例: `cd dir && git commit`,
# `git add . && git commit`, `bash -c '... && git commit'`)。
if ! printf '%s' "$COMMAND" | grep -qE "${COMMIT_ANCHORED_REGEX}([[:space:]]|$)"; then
  REASON=$(cat <<'EOF'
コミットをブロックしました。`git commit` はコマンド先頭で単独実行する必要があります。

以下のような形式はサポート外です:
  - `cd dir && git commit ...` (作業ディレクトリ変更を伴う)
  - `git add . && git commit ...` (commit 直前に index を変更する)
  - `git status && git commit ...` (前段のコマンドと連結する)
  - `bash -c "... git commit ..."` (シェルラッパー経由)

前段のコマンドと commit を別の Bash 呼び出しに分けて、最後に単独の `git commit ...` を実行してください。
EOF
)
  jq -n --arg reason "$REASON" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
fi

# 先頭一致を確認した上で、`git -C` 等の対象リポジトリ変更系オプションを deny する
# (Claude の CWD と commit 先が食い違ってマーカー検証先がズレるのを防ぐため)。
if printf '%s' "$COMMAND" \
  | grep -qE '^[[:space:]]*git[[:space:]]+([^[:space:];&|]+[[:space:]]+)*(-C|--git-dir|--work-tree)([[:space:]=])'; then
  REASON=$(cat <<'EOF'
コミットをブロックしました。`-C`, `--git-dir`, `--work-tree` で対象リポジトリを変更する形式の commit はサポート外です。

対象リポジトリへ `cd` してから `git commit` を実行してください (Claude の作業ディレクトリと commit 先が同じになるようにしてください)。
EOF
)
  jq -n --arg reason "$REASON" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
fi

# コマンド置換 (`$(...)`, バッククォート) は commit 本体より前に評価されるため、
# `git commit -m "$(git add evil; echo msg)"` のような形でマーカー検証後に index を
# 書き換えるバイパスが可能になる。コマンド全体で検出して deny する。
# (GNU grep ERE では `\`` がバッファ先頭にマッチするため、固定文字列マッチを使う)
if printf '%s' "$COMMAND" | grep -qF -e '$(' -e '`'; then
  REASON=$(cat <<'EOF'
コミットをブロックしました。`git commit` のコマンド内に `$(...)` やバッククォートによるコマンド置換が含まれています。

コマンド置換は commit 本体の評価より前に実行されるため、レビュー検証後に index を書き換える経路となり得ます。コマンド置換が必要な処理は別の Bash 呼び出しに分離して、結果を `git commit -m "..."` の文字列として直接埋め込んでください。
EOF
)
  jq -n --arg reason "$REASON" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
fi

# `git commit -m reviewed && ... && git commit unreviewed` のように、commit の後に
# シェル区切り文字を伴う追加コマンドが続くと、マーカー消費後に未レビューな commit が
# 走り得る。1 マーカー = 1 commit を保証するため、`commit` 以降に区切り文字を含む
# コマンドは deny する。引用符で囲まれた commit メッセージ内のメタ文字 (`fix A & B` 等)
# を誤検知しないよう、ダブル/シングルクォート部分を先に取り除いてから検査する。
COMMAND_POSTFIX="${COMMAND#*commit}"
COMMAND_POSTFIX_DEQUOTED=$(dequote "$COMMAND_POSTFIX")
if printf '%s' "$COMMAND_POSTFIX_DEQUOTED" | grep -qE '[;&|]'; then
  REASON=$(cat <<'EOF'
コミットをブロックしました。`git commit` の後に `;`, `&`, `&&`, `||`, `|` などのシェル区切り文字が続く複合コマンドはサポート外です (1 マーカー = 1 commit を保証するため)。

`git commit` は単独の Bash コマンドとして実行し、後続コマンドは別の Bash 呼び出しに分けてください。
EOF
)
  jq -n --arg reason "$REASON" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
fi

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0
MARKER="$GIT_DIR/.claude-pre-commit-reviewed"

# マーカーが無ければハッシュ計算をスキップして即 deny。
if [ -f "$MARKER" ]; then
  # `git commit -a` / `git commit <pathspec>` は unstaged tracked な変更も commit
  # 対象にできるため、staged + unstaged を連結したハッシュで同一性を検証する。
  CURRENT_HASH=$( {
    git diff --cached 2>/dev/null
    git diff 2>/dev/null
  } | sha256sum | awk '{print $1}')
  STORED_HASH=$(cat "$MARKER" 2>/dev/null)
  if [ "$STORED_HASH" = "$CURRENT_HASH" ]; then
    rm -f "$MARKER"
    exit 0
  fi
fi

MARK_SCRIPT="${CLAUDE_PLUGIN_ROOT:-}/hooks/scripts/mark-reviewed.sh"

REASON=$(cat <<EOF
コミットをブロックしました。コミット前に下記のレビューを実行してください。

実行手順:
  1. /codex:review
  2. /code-review:code-review
  3. /simplify

各レビューで指摘された箇所はすべて修正してください。修正が発生したら \`git add\` で再度ステージングします。

すべての修正が完了し、ステージング内容が確定したら、コミットの直前に以下を実行してマーカーを作成してください:

  bash "$MARK_SCRIPT"

マーカー作成後の \`git commit\` は許可されます。マーカーは差分のハッシュに紐づくため、ステージング内容が変わると再度レビューが必要になります。
EOF
)

jq -n --arg reason "$REASON" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason: $reason
  }
}'
