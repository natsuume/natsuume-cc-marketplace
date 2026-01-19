# git-guardrails プラグイン

GitHub Flow に準拠した Git ワークフローを支援・強制するプラグインです。

## バージョン

v0.1.0

## 概要

このプラグインは、以下の機能を提供します：

- **デフォルトブランチの保護**: master/main への直接 push を禁止
- **Draft PR の強制**: PR 作成時に自動で Draft モードに設定
- **rebase ワークフロー支援**: リモートの変更を安全に取り込むガイド

## インストール

```bash
claude plugins:add git-guardrails --path /path/to/plugins/git-guardrails
```

または、`settings.json` の `plugins` 配列にパスを追加します。

## 機能一覧

### Hooks

#### 1. block-default-branch-push

**ファイル**: `hooks/scripts/block-default-branch-push.sh`

デフォルトブランチ（master/main）への直接 push をブロックします。

**動作**:
- `git push origin master` や `git push origin main` を検知
- 該当する場合、操作を拒否してエラーメッセージを表示
- working branch からの PR マージを促す

**例**:
```
❌ git push origin master
   → "デフォルトブランチ（master/main）への直接 push は禁止されています。
      working branch を作成し、そこから PR を作成してマージしてください。"

✅ git push origin feature/my-feature
   → 通常通り実行
```

#### 2. enforce-draft-pr

**ファイル**: `hooks/scripts/enforce-draft-pr.sh`

PR 作成時に自動で `--draft` フラグを付与します。

**動作**:
- `gh pr create` コマンドを検知
- `--draft` フラグがない場合、自動で追加
- 既に `--draft` がある場合は何もしない

**例**:
```
入力: gh pr create --title "新機能"
出力: gh pr create --draft --title "新機能"
```

### Skills

#### /rebase-workflow

**ファイル**: `skills/rebase-workflow/SKILL.md`

rebase を用いてリモートのデフォルトブランチの変更を作業ブランチに取り込む手順をガイドします。

**使用シーン**:
- 「リモートの変更を取り込みたい」
- 「rebase したい」
- 「master/main を取り込む」
- 「ブランチを最新にしたい」

**提供する手順**:
1. デフォルトブランチ名の動的取得
2. `git fetch origin` でリモートの変更を取得
3. `git rebase origin/<default-branch>` で変更を取り込み
4. コンフリクト発生時の対処方法
5. `git push --force-with-lease` での安全な push

## 設定

このプラグインは特別な設定なしで動作します。

hooks は `PreToolUse` イベントで `Bash` ツールの使用を監視し、該当するコマンドに対して自動的に介入します。

## ディレクトリ構成

```
git-guardrails/
├── .claude-plugin/
│   └── plugin.json       # プラグインメタデータ
├── hooks/
│   ├── hooks.json        # フック定義
│   └── scripts/
│       ├── block-default-branch-push.sh
│       └── enforce-draft-pr.sh
├── skills/
│   └── rebase-workflow/
│       └── SKILL.md
└── README.md
```

## 関連情報

- [GitHub Flow](https://docs.github.com/ja/get-started/quickstart/github-flow)
- [Claude Code Hooks ドキュメント](https://docs.anthropic.com/claude-code/hooks)
