---
name: rebase-workflow
description: rebase を用いてリモートのデフォルトブランチの変更を作業ブランチに取り込む
user-invocable: true
when-to-use: |
  ユーザーが以下のようなリクエストをした場合に使用:
  - 「リモートの変更を取り込みたい」
  - 「rebase したい」
  - 「master/main を取り込む」
  - 「sync」「同期」
  - 「ブランチを最新にしたい」
  - 「デフォルトブランチの変更を反映」
---

# Rebase Workflow

リモートのデフォルトブランチの変更を、rebase を使って安全に作業ブランチに取り込む手順です。

## 前提条件

- 作業ブランチで作業中であること（デフォルトブランチではないこと）
- コミットしていない変更がないこと（`git status` で確認）

## 手順

### 1. デフォルトブランチ名の取得

デフォルトブランチ名はリポジトリによって異なります（master, main, develop など）。以下のコマンドで動的に取得します：

```bash
git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@'
```

取得できない場合は、以下で設定できます：

```bash
git remote set-head origin --auto
```

### 2. リモートの変更を取得

```bash
git fetch origin
```

### 3. rebase で変更を取り込む

```bash
git rebase origin/<default-branch>
```

例（デフォルトブランチが master の場合）：

```bash
git rebase origin/master
```

### 4. コンフリクト発生時の対処

rebase 中にコンフリクトが発生した場合：

1. **状況確認**
   ```bash
   git status
   ```
   コンフリクトしているファイルが表示されます。

2. **コンフリクトを解決**
   - 各ファイルを開き、`<<<<<<<`, `=======`, `>>>>>>>` マーカーを探す
   - 適切な内容に修正
   - 修正後、ステージに追加：
     ```bash
     git add <file>
     ```

3. **rebase を継続**
   ```bash
   git rebase --continue
   ```

4. **rebase を中止する場合（元の状態に戻す）**
   ```bash
   git rebase --abort
   ```

### 5. リモートへの push

rebase 後は履歴が書き換わるため、通常の `git push` は失敗します。
`--force-with-lease` を使って安全に push します：

```bash
git push --force-with-lease
```

> **注意**: `--force-with-lease` は、他の誰かがリモートブランチに push した場合に失敗します。
> これにより、他人の変更を誤って上書きすることを防げます。
> 単なる `--force` は絶対に使用しないでください。

## 一連のコマンド例

```bash
# デフォルトブランチ名を取得
DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')

# リモートの変更を取得
git fetch origin

# rebase で取り込み
git rebase origin/$DEFAULT_BRANCH

# リモートへ push（必要な場合）
git push --force-with-lease
```

## トラブルシューティング

### デフォルトブランチ名が取得できない

```bash
# リモートの HEAD を自動設定
git remote set-head origin --auto

# 再度取得を試みる
git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@'
```

### rebase 中に大量のコンフリクトが発生

小さな単位で rebase を行うか、`git rebase --abort` で中止してマージを検討してください。

### push が拒否された

`--force-with-lease` が失敗する場合、他の誰かがリモートブランチを更新しています。
再度 `git fetch` して状況を確認してください。
