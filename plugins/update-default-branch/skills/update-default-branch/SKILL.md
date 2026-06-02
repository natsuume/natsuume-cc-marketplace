---
name: update-default-branch
description: PR がマージされた報告を受けたあと、デフォルトブランチを最新化し、追跡先が消えた (gone) ローカルブランチを削除する
user-invocable: true
when_to_use: |
  ユーザーが以下のような報告/依頼をした場合に使用:
  - 「PR がマージされた」「マージしました」「merge しました」
  - 「PR をマージしたよ」「マージ完了」
  - 「PR が取り込まれた」「リモートに反映された」
  - 「デフォルトブランチを最新にしたい」
  - 「不要なブランチを削除したい」「マージ済みブランチを片付けたい」
---

# update-default-branch

PR がマージされた直後に呼び出されるワークフローです。デフォルトブランチを最新化したうえで、追跡先がリモートから消えたローカルブランチを `[gone]` 状態として検出し、まとめて削除します。

## 前提条件

- `origin` リモートが設定されていること

## 手順

### 1. 作業ツリーの安全確認

未コミット/未ステージの変更があると後続の `git switch` が失敗するため、まず `git status --short` を実行します。出力が空でない場合はユーザーに stash か commit を促してから次へ進みます (勝手に `stash` や `reset` をしてはいけません)。

### 2. 元のブランチ名を保存

デフォルトブランチへ切り替える前に、現在のブランチ名を `.git/.update-default-branch-state` に永続化します。手順 8 でこのブランチに戻すか、`[gone]` で削除された場合は新しい作業ブランチを切るかを判断するために使います。

各ステップが別々の Bash 呼び出しで実行されてもブランチ名を引き継げるようファイルを使います (シェル変数では呼び出し間で消えるため、復帰や失敗時の switch-back が機能しなくなる)。

```bash
GIT_DIR=$(git rev-parse --git-dir)
git branch --show-current > "$GIT_DIR/.update-default-branch-state"
```

`git branch --show-current` は detached HEAD のとき空文字を返します。その場合はファイルが空になり、手順 8 で「ユーザーに新規作業ブランチを促す」分岐に入ります。

### 3. デフォルトブランチ名の取得

リポジトリによって `master` / `main` / `develop` などが異なるため、動的に取得します。

```bash
DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
```

取得できない場合は以下で設定してから再取得します。

```bash
git remote set-head origin --auto
DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@')
```

### 4. デフォルトブランチへ切り替えて最新化

`--ff-only` で fast-forward しか許さないように制御します。これによりローカルが分岐している場合は pull が失敗するため、トラブルシューティング節の手順に従って状況確認できます。`pull.rebase` / `merge.ff` などのユーザー設定に依存して意図せず merge commit がデフォルトブランチに作られるのを避けるためです。

pull が失敗した場合は **デフォルトブランチに居着かない** よう、state file から元のブランチを読み出して復帰したうえで終了します。

```bash
git switch "$DEFAULT_BRANCH"
if ! git pull --ff-only origin "$DEFAULT_BRANCH"; then
  echo "fast-forward 不可。手動対処が必要です。" >&2
  GIT_DIR=$(git rev-parse --git-dir)
  ORIGINAL_BRANCH=$(cat "$GIT_DIR/.update-default-branch-state" 2>/dev/null || true)
  if [ -n "$ORIGINAL_BRANCH" ] && [ "$ORIGINAL_BRANCH" != "$DEFAULT_BRANCH" ]; then
    git switch "$ORIGINAL_BRANCH"
  fi
  exit 1
fi
```

> **注意**: デフォルトブランチでの作業が禁止されている場合、ここでの切り替えは「最新化のための一時的な遷移」です。手順 8 で必ず元のブランチへ戻るか、`git switch -c <branch>` で新しい作業ブランチを切り直してください。

### 5. リモート追跡情報を整理

`--prune` で、リモートから削除されたブランチに対応する remote-tracking ref をクリーンアップします。

```bash
git fetch --prune origin
```

### 6. `[gone]` 状態のローカルブランチを検出

リモートが消えた (= マージされて削除された) ローカルブランチを抽出します。デフォルトブランチや現在チェックアウト中のブランチは除外します。

```bash
GONE_BRANCHES=$(git for-each-ref \
  --format='%(refname:short) %(upstream:track)' refs/heads \
  | awk -v default_branch="$DEFAULT_BRANCH" '
      $2 == "[gone]" && $1 != default_branch { print $1 }
    ')
```

### 7. ローカルブランチを削除

`[gone]` 状態のブランチは追跡先のリモートが既に削除済み (PR マージ後に GitHub がブランチを削除した、または別環境でクリーンアップされた) なので、ローカルの後始末を確認なしで実行します。承認ステップは認知負荷を増やすだけで実質的なリスク低減にならないため、検出と削除を一気に行い、削除結果を事後に表示します。

`-D` を使うのは、`[gone]` 状態では追跡先が無くマージ判定が成立しない、かつ squash/rebase マージでは feature branch のコミット ID が default branch に存在しないため `-d` が「未マージ」として拒否してしまうためです。

> **注意 (`[gone]` の意味)**: `[gone]` は「リモート側の対応 ref が消えている」状態を指し、必ずしも「PR がマージ済み」とは限りません。リモートでの force-delete やリネームでも `[gone]` になります。`git branch -D` は merge 検査を skip するため、まれに「ローカルにのみ存在するコミットを保持する `[gone]` ブランチ」が誤削除される可能性があります。`git branch -D` の出力には削除前の SHA が表示されるので、誤削除に気づいた場合は表示された SHA を `git checkout -b <name> <sha>` で復活できます (約 30 日は `git reflog` でも遡れます)。マージ済み以外の理由で `[gone]` になる branch を残しておきたい場合は、本 Skill の手順 7 を実行する前に作業ブランチへ移しておくか、Skill の利用自体を控えてください。

```bash
while IFS= read -r branch; do
  [ -z "$branch" ] && continue
  git branch -D "$branch"
done <<< "$GONE_BRANCHES"
```

該当がない場合はその旨を伝えて手順 8 に進みます。

### 8. 元のブランチへ戻す (またはユーザーに新ブランチを促す)

手順 2 で保存した state file から `ORIGINAL_BRANCH` を読み出し、必要に応じて元の作業ブランチへ復帰させます。デフォルトブランチに居着いたまま終了させない (CLAUDE.md でデフォルトブランチ作業が禁止されている場合に重要) ことが目的です。最後に state file を削除して残骸を残しません。

| 条件 | アクション |
|-----|-----------|
| `ORIGINAL_BRANCH` が空 (detached HEAD だった) | ユーザーに次の作業ブランチ名を確認し、`git switch -c <name>` で作成 |
| `ORIGINAL_BRANCH` が削除済み (`GONE_BRANCHES` に含まれていた) | ユーザーに次の作業ブランチ名を確認し、`git switch -c <name>` で作成 |
| `ORIGINAL_BRANCH` がデフォルトブランチと同じ | (元からデフォルトブランチにいたので) 何もしない。ユーザーに作業ブランチを切るよう促す |
| それ以外 (元のブランチが残っている) | `git switch "$ORIGINAL_BRANCH"` で復帰 |

```bash
GIT_DIR=$(git rev-parse --git-dir)
STATE_FILE="$GIT_DIR/.update-default-branch-state"
ORIGINAL_BRANCH=$(cat "$STATE_FILE" 2>/dev/null || true)

if [ -z "$ORIGINAL_BRANCH" ] || [ "$ORIGINAL_BRANCH" = "$DEFAULT_BRANCH" ] \
  || printf '%s\n' "$GONE_BRANCHES" | grep -Fxq -- "$ORIGINAL_BRANCH"; then
  echo "次の作業ブランチをユーザーに確認してから git switch -c <name> で作成してください。" >&2
else
  git switch "$ORIGINAL_BRANCH"
fi

rm -f "$STATE_FILE"
```

## 一連のスクリプト例

検出から削除、ブランチ復帰まで一気に実行できます。`[gone]` ブランチはリモートが既に削除済み (PR マージ後の自動削除等) なので、ローカル削除に確認ステップは不要です。

```bash
# 1. 状態確認
if [ -n "$(git status --short)" ]; then
  echo "未コミットの変更があります。stash または commit してから再実行してください。" >&2
  exit 1
fi

# 2. 元のブランチを state file に永続化 (Bash 呼び出しが分割されても引き継ぐため)
GIT_DIR=$(git rev-parse --git-dir)
STATE_FILE="$GIT_DIR/.update-default-branch-state"
git branch --show-current > "$STATE_FILE"

# 3-4. デフォルトブランチへ移動して最新化
DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
if [ -z "$DEFAULT_BRANCH" ]; then
  git remote set-head origin --auto
  DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@')
fi
git switch "$DEFAULT_BRANCH"
# fast-forward 不可なら以降の削除フェーズに進ませない。
# 中断する場合も「デフォルトブランチに居着かない」ポリシーを守るため
# ORIGINAL_BRANCH へ復帰し、state file は手動対処の検証用に残しておく。
if ! git pull --ff-only origin "$DEFAULT_BRANCH"; then
  echo "fast-forward 不可。手動対処が必要です。" >&2
  ORIGINAL_BRANCH=$(cat "$STATE_FILE" 2>/dev/null || true)
  if [ -n "$ORIGINAL_BRANCH" ] && [ "$ORIGINAL_BRANCH" != "$DEFAULT_BRANCH" ]; then
    git switch "$ORIGINAL_BRANCH"
  fi
  exit 1
fi

# 5. リモート追跡情報の整理
git fetch --prune origin

# 6. [gone] ブランチの検出
GONE_BRANCHES=$(git for-each-ref \
  --format='%(refname:short) %(upstream:track)' refs/heads \
  | awk -v default_branch="$DEFAULT_BRANCH" '
      $2 == "[gone]" && $1 != default_branch { print $1 }
    ')

# 7. ローカル削除 (確認なし。リモートが消えた branch は安全に削除可)
while IFS= read -r branch; do
  [ -z "$branch" ] && continue
  git branch -D "$branch"
done <<< "$GONE_BRANCHES"

# 8. 元のブランチへ戻す or ユーザーに新ブランチを促す
ORIGINAL_BRANCH=$(cat "$STATE_FILE" 2>/dev/null || true)
if [ -z "$ORIGINAL_BRANCH" ] || [ "$ORIGINAL_BRANCH" = "$DEFAULT_BRANCH" ] \
  || printf '%s\n' "$GONE_BRANCHES" | grep -Fxq -- "$ORIGINAL_BRANCH"; then
  echo "次の作業ブランチをユーザーに確認してから git switch -c <name> で作成してください。" >&2
else
  git switch "$ORIGINAL_BRANCH"
fi

rm -f "$STATE_FILE"
```

## トラブルシューティング

### `git pull --ff-only` が失敗する

ローカルのデフォルトブランチに独自コミットが残っており、fast-forward できません。`git log $DEFAULT_BRANCH..origin/$DEFAULT_BRANCH` および `git log origin/$DEFAULT_BRANCH..$DEFAULT_BRANCH` で状況を確認し、ユーザーに相談してから対処してください (rebase / reset --hard などは独断で行わない)。

### `[gone]` のブランチが想定より多い

過去に `git branch -m` でリネームしたブランチや、別環境で削除されたブランチが含まれることがあります。削除自体は確認なしで実行されますが、削除結果はユーザーに事後表示し、想定外のブランチが消えていないかを確認できるようにします (誤削除に気づいたら `git reflog` で復旧可能)。

### worktree が紐づいているブランチを削除しようとした

`git branch -D` は worktree にチェックアウトされたブランチを削除できません。`git worktree list` で確認し、不要なら `git worktree remove <path>` してから再度削除してください。
