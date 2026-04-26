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
| [pre-commit-review](#pre-commit-review) | 0.1.0 | `git commit` 前に `/simplify` → `/codex:review` の順で実行を強制するプラグイン |
| [post-pr-review](#post-pr-review) | 0.1.0 | `gh pr create` 成功直後に `/code-review:code-review` の実行を誘導するプラグイン |
| [update-default-branch](#update-default-branch) | 0.1.0 | PR マージ報告を契機にデフォルトブランチを最新化し、追跡先が消えたローカルブランチを片付けるプラグイン |
| [natsuume-statusline](#natsuume-statusline) | 0.1.0 | Claude Code の `statusLine` 表示を提供し、`/natsuume-statusline:setup` で `settings.json` に登録するプラグイン |

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

`git commit` を実行する前に `/simplify` → `/codex:review` の順で必ず実行させ、未レビューの状態でのコミットをブロックするプラグインです。`/simplify` はコード変更を伴うため先に走らせ、`/codex:review` はその後の最終形をレビューします。PR 対象の `/code-review:code-review` は姉妹プラグイン [post-pr-review](#post-pr-review) が担当します。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `block-pre-commit` | PreToolUse | `git commit` を検知し、ステージング差分のハッシュと一致するレビュー済みマーカーが無い場合に deny を返す |

#### スクリプト

| スクリプト | 用途 |
|-----------|------|
| `mark-reviewed.sh` | レビュー完了後、コミット直前に手動で実行してマーカーを作成する |

### キーワード

`commit` `review` `quality` `codex` `simplify`

---

## post-pr-review

Claude Code 経由で `gh pr create` が成功した直後に、`/code-review:code-review <PR-URL>` の実行を `additionalContext` で Claude に誘導するプラグインです。`pre-commit-review` の姉妹プラグインで、PR 対象のレビューを担当します。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `nudge-pr-review` | PostToolUse | `gh pr create` 成功時に PR URL を抽出し、`additionalContext` で `/code-review:code-review <URL>` 実行を促す (強制ではなく誘導) |

### 注意事項

`additionalContext` 経由の誘導なので、Claude が無視することは原理的に可能です。また Web UI など Claude Code 以外で作成された PR には介入しません (これは設計上の意図)。

### キーワード

`pr` `review` `code-review` `github`

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

---

## natsuume-statusline

Claude Code の `statusLine` 表示 (カレントパス / GitHub リポジトリ / ブランチ / 変更量 / レートリミット) を提供するプラグインです。`/natsuume-statusline:setup` を実行すると、`~/.claude/settings.json` の `statusLine.command` がこのプラグインのエントリポイントに切り替わります。

### 表示内容

- **1 行目**: パス、GitHub repo (所有 namespace は `repo` に短縮)、branch、staged/modified、未コミット件数 / `clean`
- **2 行目**: 5h / 7d レートリミットのパーセンテージ、リセット残時間、プログレスバー (色は使用率で変化)
- **3 行目**: 将来拡張用 (現状は空)

### 機能

#### Commands

| コマンド | 説明 |
|---------|------|
| `/natsuume-statusline:setup` | `settings.json` をバックアップしたうえで `statusLine.command` をプラグインのエントリポイントに書き換える |

### キーワード

`statusline` `ui` `git` `ratelimit` `github`
