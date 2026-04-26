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
| [pre-commit-review](#pre-commit-review) | 0.1.0 | `git commit` 前に `/codex:review`, `/code-review:code-review`, `/simplify` の実行を強制するプラグイン |
| [update-default-branch](#update-default-branch) | 0.1.0 | PR マージ報告を契機にデフォルトブランチを最新化し、追跡先が消えたローカルブランチを片付けるプラグイン |

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

---

## pre-commit-review

`git commit` を実行する前に `/codex:review`, `/code-review:code-review`, `/simplify` の 3 つのレビューを必ず実行させ、未レビューの状態でのコミットをブロックするプラグインです。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `block-pre-commit` | PreToolUse | `git commit` を検知し、ステージング差分のハッシュと一致するレビュー済みマーカーが無い場合に deny を返す |

#### スクリプト

| スクリプト | 用途 |
|-----------|------|
| `mark-reviewed.sh` | 3 つのレビューを完了した後、コミット直前に手動で実行してマーカーを作成する |

### キーワード

`commit` `review` `quality` `codex` `simplify`

---

## update-default-branch

PR がマージされた旨の報告をユーザーから受けた際に、デフォルトブランチを最新化し、追跡先が消えたローカルブランチ (`[gone]`) を片付けるためのプラグインです。

### 機能

#### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| update-default-branch | `/update-default-branch` | デフォルトブランチを `git pull` で最新化し、`git fetch --prune` でリモート追跡情報を整理した後、`[gone]` のローカルブランチを検出してユーザー確認後に削除する |

### キーワード

`git` `pull` `prune` `branch-cleanup` `merge`
