#!/bin/bash
# default-branch.sh
# git-guardrails の各 hook で共有する「カレントブランチがデフォルトブランチか」判定。
#
# 3 つの hook (push / commit / pr) で同一のブランチ名集合と判定ロジックを使うため、
# 1 か所に集約してドリフトを防ぐ。

# デフォルトブランチとして扱うブランチ名集合。GitHub のリポジトリでは master か main の
# どちらかが慣習的に使われる。両方を保護対象として扱う (個別リポジトリの実 default
# branch を `git symbolic-ref refs/remotes/origin/HEAD` で動的解決する手もあるが、
# `origin/HEAD` が未設定の clone も存在し信頼度が低いため、ハードコードで cooperative
# 利用に十分な範囲をカバーする)。
DEFAULT_BRANCH_NAMES=("master" "main")

# 引数: <branch-name>
# 戻り値: 0 = デフォルトブランチ、1 = それ以外
is_default_branch() {
  local target="$1"
  local b
  for b in "${DEFAULT_BRANCH_NAMES[@]}"; do
    [ "$target" = "$b" ] && return 0
  done
  return 1
}

# 出力: カレントブランチ名 (detached HEAD 等で取得できないなら空文字列)
current_branch() {
  git symbolic-ref --short HEAD 2>/dev/null
}

# 引数: <token>
# 出力: shell quote (`"..."` / `'...'`) を 1 段だけ剥がしたトークン
#
# Claude Code が tool_input.command に渡す文字列は shell 実行前なので、`gh pr create
# --head "master"` のようにユーザーが引用符を付けた場合、token には quote 文字が残る。
# `is_default_branch "master"` のように生のまま比較すると bypass されるため、判定前に
# 1 段剥がす。多重引用 (`"\"master\""`) は cooperative 利用では出ないので未対応。
strip_shell_quotes() {
  local s="$1"
  case "$s" in
    \"*\") s="${s%\"}"; s="${s#\"}" ;;
    \'*\') s="${s%\'}"; s="${s#\'}" ;;
  esac
  printf '%s' "$s"
}

# 引数: <refspec-part>
# 出力: refspec の片側 (左/右) を branch name として完全一致比較するための正規化形
#
# Git が受け付ける refspec 形式は:
#   - `master` / `main` (bare branch name)
#   - `+master` / `+main` (force-push prefix)
#   - `refs/heads/master` / `refs/heads/main` (full ref name)
#   - `HEAD` (シンボリック)
# このうち `+master` や `refs/heads/master` を完全一致比較で見落とすと、master 更新が
# 素通りする bypass になる。`+` と `refs/heads/` を順に剥がして branch 部分を取り出す。
# `HEAD` は対称解釈 (相手側が master/main なら別チェックで拾える) なのでそのまま。
normalize_refspec_part() {
  local s="$1"
  s="${s#+}"
  s="${s#refs/heads/}"
  printf '%s' "$s"
}

# 引数: <command-string>
# 出力: shell quote (`"..."` / `'...'`) で囲まれた範囲を空白に置換した command 文字列
#
# `has_target_mismatch_prefix` のような raw text grep は `git commit -m "docs: add cd
# instructions"` の引用符内の "cd" を誤検出する。検出前に quote 内を消すことで、
# テキスト参照と本物のコマンド prefix を区別する。
#
# **順序重要**: double quote を先に剥がす。逆順だと `git commit -m "don't" && cd /other
# && git commit -m "won't"` のような英文アポストロフィを含む chained コマンドで、最初の
# `'` から最後の `'` までを 1 つの single-quoted region と誤認し、間に挟まれた
# `&& cd /other &&` ごと食ってしまう。結果として `cd /other` が消えて
# `has_target_mismatch_prefix` の cd 検出を素通りさせる致命的バイパスになる。
# pre-commit-review/block-pre-commit.sh の同種 sed と同順で揃える。
#
# sed が行単位で動く都合上、複数行にまたがる double-quoted heredoc 等は対象外だが、
# has_target_mismatch_prefix の入力は単一行 command 想定なので問題なし。
strip_quoted_text() {
  printf '%s' "$1" | sed -E -e 's/"[^"]*"/ /g' -e "s/'[^']*'/ /g"
}

# 引数: <reason>
# stdout: PreToolUse deny の jq ペイロード
# 3 hook で同形だったペイロードを集約して、フィールド名やキー順のドリフトを防ぐ。
emit_deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
}

# 引数: <command-string>
# 戻り値: 0 = target-mismatch を起こす可能性ありの prefix を含む / 1 = 含まない
#
# hook は cwd の `git symbolic-ref` でカレントブランチ・対象 repo を判定するため、
# コマンドの前段が以下いずれかで cwd / カレントブランチを書き換える形だと「hook 検証時の
# target」と「実コマンドの target」が乖離する (target-mismatch):
#   - 対象 repo の切替: `cd /other && git push` / `git -C /other push` / `GIT_DIR=...`
#   - 対象 branch の切替: `git switch master && git commit` / `git checkout main && ...`
# 別 repo の master を更新する経路、別 worktree の default branch を巻き込む経路、もしくは
# default branch に切り替えてから commit/push/PR 作成する経路は、いずれも default branch
# 保護の趣旨を直接破壊するため、cwd / カレントブランチ解析よりも保守側に倒して deny する。
#
# 対象 prefix:
#   - `cd <dir>` / `pushd <dir>` / `popd`
#   - `git -C <dir> ...` / `git --git-dir=...` / `git --work-tree=...`
#   - `GIT_DIR=` / `GIT_WORK_TREE=` / `GIT_INDEX_FILE=` 環境変数 (bare assignment)
#   - 上記環境変数を `export` / `declare` / `typeset` / `readonly` でセットする形
#   - `git switch master` / `git switch main` / `git checkout master` / `git checkout main`
#     (`-c master` / `-b main` で master/main を新規作成する形も同列)
has_target_mismatch_prefix() {
  # 引用符内の文字列 (commit message やオプション値) を空白に置換してから検出する。
  # `git commit -m "docs: add cd instructions"` のような quoted text に "cd" を含む
  # ケースで raw grep が誤検出するのを防ぐため。
  local cmd
  cmd=$(strip_quoted_text "$1")
  # cd / pushd / popd
  if printf '%s' "$cmd" \
    | grep -qE '(^|[;&|[:space:]])(cd|pushd|popd)([[:space:]]|[;&|]|$)'; then
    return 0
  fi
  # bare assignment: GIT_DIR=... / GIT_WORK_TREE=... / GIT_INDEX_FILE=...
  if printf '%s' "$cmd" \
    | grep -qE '(^|[;&|[:space:]])(GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE)='; then
    return 0
  fi
  # export GIT_DIR=... / declare -x GIT_DIR=... / typeset -x ... / readonly ...
  if printf '%s' "$cmd" \
    | grep -qE '(^|[;&|[:space:]])(export|declare|typeset|readonly)[[:space:]]+(-[^[:space:];&|]+[[:space:]]+)*(GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE)([[:space:]=;&|]|$)'; then
    return 0
  fi
  # git -C <dir> / git --git-dir=... / git --work-tree=...
  if printf '%s' "$cmd" \
    | grep -qE '(^|[;&|[:space:]])git[[:space:]]+([^[:space:];&|]+[[:space:]]+)*(-C|--git-dir|--work-tree)([[:space:]=;&|]|$)'; then
    return 0
  fi
  # git switch master / git checkout master / git switch -c master / git checkout -b master 等。
  # 実コマンド前に default branch (master/main) へ切り替える chained 形式は、後続の
  # commit/push/pr が `current_branch` でなく master/main 上で実行される経路になる。
  # `git switch <flags>* <branch>` / `git checkout <flags>* <branch>` の <branch> 部分が
  # master/main の場合に deny する。`-c new-branch` / `-b new-branch` で master/main を
  # 新規作成する形も同列に扱う。
  # 終端境界は `[[:space:]]` と `;`/`&`/`|` の両方を含める (改行を `;` に正規化したコマンド
  # 列で `git switch master;git commit` の `master` の直後 `;` を境界として認識するため)。
  if printf '%s' "$cmd" \
    | grep -qE '(^|[;&|[:space:]])git[[:space:]]+(switch|checkout)([[:space:]]+-[^[:space:];&|]+)*[[:space:]]+(master|main)([[:space:]]|[;&|]|$)'; then
    return 0
  fi
  return 1
}

# 共通の deny 文言: target-mismatch prefix が原因で deny する場合のメッセージ。
# 3 hook で内容が揃うように 1 か所に readonly で置く。
readonly TARGET_MISMATCH_DENY_REASON='デフォルトブランチ保護フックは hook 実行時の cwd / カレントブランチを基に判定するため、対象 repo / branch を切り替える前段を含むコマンドは保護を素通りさせる経路になります。具体的には:
  - 対象 repo を切り替える: `cd <dir> && ...` / `git -C <dir> ...` / `GIT_DIR=...`
  - 対象 branch を切り替える: `git switch master && ...` / `git checkout main && ...`
これらは保守的に deny します。対象 repo / branch へ切り替えた後、別の Bash 呼び出しとして実コマンドを実行してください (master/main 上の commit / push / PR 作成は他の guardrail でも引き続き deny されます)。'
