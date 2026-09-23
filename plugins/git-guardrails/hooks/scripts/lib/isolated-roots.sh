#!/bin/bash
# isolated-roots.sh
# commit 系 hook (block-default-branch-commit.sh) が、利用者の明示した隔離ルート配下の
# repo だけを対象とする commit を検査対象から免除するための判定関数群。
#
# ## 許可ルート
#
# env `CLAUDE_ISOLATED_GIT_ROOTS` に PATH と同じコロン区切りで絶対パスを列挙する。
#   - 空文字 entry・相対パス entry・存在しないディレクトリの entry は無視する
#   - 有効な entry は `cd -P <entry> && pwd -P` で canonical 実パスに変換して比較に使う
#   - env が未設定・空、または有効な entry が 1 つも無い場合は免除しない
#
# ## 免除条件
#
# 次の全てを満たす場合のみ免除する:
#   1. コマンド内に commit invocation が 1 つ以上あり、その全てについて対象 dir を静的に
#      解決できる (解決規則は resolve_commit_target_dirs を参照)
#   2. 各対象 dir の canonical 実パスが、いずれかの許可ルート配下にある
#   3. 各対象 dir で実行した `git rev-parse --git-common-dir` の canonical 実パスが、
#      いずれかの許可ルート配下にある (許可ルート配下に置いた linked worktree / symlink
#      経由でルート外の repo を更新する経路を塞ぐ)
# 「配下」はパス境界での前方一致で判定し、ルート自身との一致も配下とみなす
# (`/a/b` は `/a/b` と `/a/b/c` を含み、`/a/bc` を含まない)。
#
# ## fail-closed
#
# 解析・パス解決・git 呼び出しのいずれかが失敗した場合、または判定に必要な情報が
# 静的に確定しない場合は「免除しない」(return 1) を返す。免除しない場合、caller は
# 従来の判定 (target-mismatch deny / default branch 上 commit の deny) をそのまま行う。
#
# ## 互換性・依存
#
# macOS 標準 bash 3.2 と Linux bash の両方で動く構文だけを使う (`declare -A` /
# `mapfile` / nameref / `${x,,}` / `realpath` / `readlink -f` を使わない)。canonical 化は
# `cd -P && pwd -P` で行い、macOS の `/tmp` → `/private/tmp` のような symlink を実体に
# 解決する。
# cmd-parser.sh (split_command / tokenize_segment / unquote_token / skip_env_assignments)
# を先に source しておくこと。

# 引数: <path>
# stdout: <path> が既存ディレクトリなら、その canonical 実パス (`cd -P <path> && pwd -P`)
# 戻り値: 0 = 解決できた / 1 = 解決できない (存在しない・ディレクトリでない・権限不足)
isolated_canonical_dir() {
  return 1
}

# 引数: なし (env `CLAUDE_ISOLATED_GIT_ROOTS` を読む)
# stdout: 有効な許可ルートの canonical 実パスを 1 行 1 件で出力する
# 戻り値: 0 = 有効な許可ルートが 1 件以上ある / 1 = 無い (未設定・空・全 entry が無効)
isolated_git_roots() {
  return 1
}

# 引数: <path> <root> (どちらも canonical 実パスであること)
# 戻り値: 0 = <path> が <root> 自身または <root> 配下 (パス境界で前方一致) / 1 = それ以外
# <root> が `/` の場合は全ての絶対パスを配下とみなす。
isolated_path_is_within() {
  return 1
}

# 引数: <dir> (canonical 実パス)
# 戻り値: 0 = <dir> と、<dir> で実行した `git rev-parse --git-common-dir` の canonical
#         実パスが、どちらもいずれかの許可ルート配下にある / 1 = それ以外
#
# `--git-common-dir` が相対パスで返る場合は <dir> 基準で解決する。rev-parse は hook
# プロセスの環境を継承して実行する (実際の commit と同じ解決結果を得るため)。
# <dir> が git repo でない・rev-parse が失敗した場合は 1 を返す。
isolated_dir_is_within_roots() {
  return 1
}

# 引数: <command> <base_dir>
#   <command>  : hook が受け取った Bash コマンド文字列 (行継続・redirection 正規化後)
#   <base_dir> : hook プロセスの cwd (commit invocation の対象 dir の初期値)
# stdout: commit invocation ごとに、静的に解決した対象 dir の canonical 実パスを
#         出現順に 1 行 1 件で出力する
# 戻り値: 0 = commit invocation が 1 つ以上あり全て解決できた / 1 = それ以外
#
# 静的解決の規則 (これ以外の形は解決不能として 1 を返す):
#   - コマンド全体に subshell / brace group (`(` / `{`)、コマンド置換・プロセス置換
#     (`$(` / バッククォート / `<(` / `>(`) が無いこと
#   - 先頭 segment から最後の commit invocation までの区切りが `&&` と `;` (改行を含む)
#     だけであること (`||` / `|` / `&` は cd の効果が commit に及ぶかを静的に確定
#     できないため解決不能)
#   - commit invocation より前の segment のうち cwd / repo 解決に影響するものは
#     `cd <path>` (引数ちょうど 1 個) だけであること。`cd` 単独・`cd -`・`pushd` /
#     `popd`・`export` / `declare` / `typeset` / `readonly` / `unset`・`source` / `.` /
#     `eval` / `exec`、および `GIT_DIR` / `GIT_WORK_TREE` / `GIT_INDEX_FILE` /
#     `GIT_COMMON_DIR` の assignment を含む segment があれば解決不能
#   - commit invocation の global option で対象を切り替えるものは `-C <path>` だけを
#     受け付ける (複数指定時は git と同じく順に相対解決する)。`--git-dir` /
#     `--work-tree` (および `=` 形式)、invocation 直前の上記 env assignment は解決不能
#   - `cd` / `-C` の <path> は quote を外した結果が静的な文字列であること。変数展開
#     (`$`)、先頭の `~`、glob 文字 (`*` / `?` / `[`)、バックスラッシュ、`-` 始まりを
#     含む場合は解決不能
#   - 相対パスは直前までに解決した dir を基準に解決し、各段階で既存ディレクトリとして
#     canonical 化できること
resolve_commit_target_dirs() {
  return 1
}

# 引数: <command> <base_dir> (意味は resolve_commit_target_dirs と同じ)
# 戻り値: 0 = 免除する (許可ルートが有効で、全 commit invocation の対象 dir が免除条件を
#         満たす) / 1 = 免除しない
# env `CLAUDE_ISOLATED_GIT_ROOTS` が未設定・空なら、コマンドを解析せずに 1 を返す。
command_commits_only_to_isolated_roots() {
  return 1
}
