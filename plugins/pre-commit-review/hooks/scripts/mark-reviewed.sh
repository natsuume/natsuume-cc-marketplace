#!/bin/bash
# mark-reviewed.sh
# レビュー完了マーカーを `.git/.claude-pre-commit-reviewed` に書き出す。

set -euo pipefail

if ! GIT_DIR=$(git rev-parse --git-dir 2>/dev/null); then
  echo "[pre-commit-review] git リポジトリ外で実行されました。中止します。" >&2
  exit 1
fi

# `git commit -a` / `git commit <pathspec>` 経由で未レビュー変更が紛れ込むのを防ぐため、
# staged だけでなく unstaged tracked な差分も含めてハッシュを取る。
HASH=$( {
  git diff --cached
  git diff
} | sha256sum | awk '{print $1}')

printf '%s' "$HASH" > "$GIT_DIR/.claude-pre-commit-reviewed"
echo "[pre-commit-review] レビュー済みマーカーを作成しました ($HASH)。次の git commit が許可されます。" >&2
