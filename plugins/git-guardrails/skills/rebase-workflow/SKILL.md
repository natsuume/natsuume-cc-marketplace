---
name: rebase-workflow
description: rebase を用いてリモートのデフォルトブランチの変更を作業ブランチに取り込む。「リモート変更を取り込む」「rebase」「master/main を同期」「ブランチを最新にする」依頼で使う
---

# Rebase Workflow

リモートのデフォルトブランチの変更を、rebase を使って安全に作業ブランチに取り込む手順です。

## 前提条件

- 作業ブランチで作業中であること（デフォルトブランチではないこと）
- コミットしていない変更がないこと（`git status` で確認）

## 手順

### 1. リモート状態の更新とデフォルトブランチ名の取得

デフォルトブランチ名はリポジトリによって異なります（master, main, develop など）。ローカルの `origin/HEAD` はリモート側のデフォルトブランチ変更に自動追従せず、古い値を正常終了で返しうるため、参照する前に次の 2 コマンドを順に実行してリモート状態を更新します：

```bash
git fetch --prune origin
```

```bash
git remote set-head origin --auto
```

そのうえでデフォルトブランチ名を取得します：

```bash
git symbolic-ref refs/remotes/origin/HEAD
```

出力 `refs/remotes/origin/<name>` の `<name>` 部分がデフォルトブランチ名です (prefix の `refs/remotes/origin/` は出力を読み取って除きます)。

### 2. rebase で変更を取り込む

```bash
git rebase origin/<default-branch>
```

例（デフォルトブランチが master の場合）：

```bash
git rebase origin/master
```

### 3. コンフリクト発生時の対処

rebase 中にコンフリクトが発生した場合は、各ファイルのコンフリクトを解決して `git add <file>` でステージし `git rebase --continue` で継続し、断念する場合は `git rebase --abort` で rebase 前の状態に戻します。

### 4. リモートへの push

rebase 後は履歴が書き換わるため、通常の `git push` は失敗します。
`--force-with-lease` を使って安全に push します：

```bash
git push --force-with-lease
```

> **注意**: `--force-with-lease` は、他の誰かがリモートブランチに push した場合に失敗します。
> これにより、他人の変更を誤って上書きすることを防げます。
> 単なる `--force` は絶対に使用しないでください。

## トラブルシューティング

### デフォルトブランチ名が取得できない

手順 1 の 2 コマンド (fetch --prune / set-head --auto) を実行済みでも取得できない場合は、リモートとの接続と HEAD branch を確認してください：

```bash
git remote show origin
```

### push が拒否された

`--force-with-lease` が失敗する場合、他の誰かがリモートブランチを更新しています。
再度 `git fetch` して状況を確認してください。
