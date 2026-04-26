# natsuume's Claude Code Plugin Marketplace

natsuume が作成・公開している Claude Code プラグインのマーケットプレイスリポジトリです。

## インストール方法

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace
```

特定のプラグインのみをインストールする場合：

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=git-guardrails
```

## プラグイン一覧

| プラグイン | バージョン | 説明 |
|-----------|-----------|------|
| [git-guardrails](#git-guardrails) | 0.1.0 | GitHub Flow に準拠した Git ワークフローを支援・強制するプラグイン |
| [auto-lint-check](#auto-lint-check) | 0.1.0 | ファイル編集前に linter チェックを行い、編集後に自動フォーマットを適用するプラグイン |

---

## git-guardrails

GitHub Flow に準拠した Git ワークフローを支援・強制するプラグインです。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `block-default-branch-push` | PreToolUse | デフォルトブランチ（master/main）への直接 push を禁止し、PR 経由を強制する |
| `enforce-draft-pr` | PreToolUse | `gh pr create` 実行時に自動的に `--draft` フラグを付与する |

#### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| rebase-workflow | `/rebase-workflow` | rebase を用いてリモートのデフォルトブランチの変更を作業ブランチに取り込む |

### キーワード

`git` `workflow` `github-flow` `rebase`

---

## auto-lint-check

ファイル編集前に linter による事前チェックを行い、ignore コメントの挿入をブロックし、編集後に自動でフォーマットを適用するプラグインです。モノレポ構成のサブディレクトリにある linter 設定も自動的に検出します。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `auto-lint-check` | PreToolUse | Edit/Write/MultiEdit の編集後予測内容を ESLint / Ruff の stdin に流し、エラーがあれば実行を deny する |
| `block-ignore-lint-comment` | PreToolUse | `// eslint-disable`, `// prettier-ignore`, `# noqa`, `# ruff: noqa` 等の ignore コメント挿入を deny する |
| `code-format` | PostToolUse | 編集後に `eslint --fix` / `prettier --write` / `ruff check --fix` / `ruff format` を実行する |

### 対応 linter / formatter

- JavaScript / TypeScript: ESLint, Prettier
- Python: Ruff (`check --fix` および `format`)

### キーワード

`lint` `format` `eslint` `prettier` `ruff` `quality`
