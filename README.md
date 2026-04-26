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
| [git-guardrails](#git-guardrails) | 0.1.0 | GitHub Flow に準拠した Git ワークフロー (default branch への直接 push 禁止 + rebase Skill) |
| [enforce-draft-pr](#enforce-draft-pr) | 0.1.0 | `gh pr create` に `--draft` を自動付与する PreToolUse フックプラグイン (任意導入) |
| [auto-lint-check](#auto-lint-check) | 0.1.0 | ファイル編集前に linter チェックを行い、編集後に自動フォーマットを適用するプラグイン |
| [pre-commit-review](#pre-commit-review) | 0.1.0 | `git commit` 前に `/simplify` → `/codex:review` (修正が落ち着くまでループ) を強制するプラグイン |
| [post-pr-review](#post-pr-review) | 0.1.0 | `gh pr create` 成功直後に `/code-review:code-review` の実行を誘導するプラグイン |
| [update-default-branch](#update-default-branch) | 0.1.0 | PR マージ報告を契機にデフォルトブランチを最新化し、追跡先が消えたローカルブランチを片付けるプラグイン |
| [natsuume-statusline](#natsuume-statusline) | 0.1.0 | Claude Code の `statusLine` 表示を提供し、`/natsuume-statusline:setup` で `settings.json` に登録するプラグイン |
| [codex-review-customize](#codex-review-customize) | 0.1.0 | 公式 codex プラグインの `/codex:review` 定義をローカルでパッチし、Skill 呼び出し許可 + 出力日本語化を setup するプラグイン |

---

## git-guardrails

GitHub Flow に準拠した Git ワークフローを支援・強制するプラグインです。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `block-default-branch-push` | PreToolUse | デフォルトブランチ（master/main）への直接 push を禁止し、PR 経由を強制する |

#### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| rebase-workflow | `/rebase-workflow` | rebase を用いてリモートのデフォルトブランチの変更を作業ブランチに取り込む |

> v0.1.0 までは `gh pr create` の `--draft` 自動付与もこのプラグインに含まれていましたが、責務分離のため [enforce-draft-pr](#enforce-draft-pr) プラグインへ切り出しました。draft 強制を使いたい場合はそちらを別途インストールしてください。

### キーワード

`git` `workflow` `github-flow` `rebase`

---

## enforce-draft-pr

`gh pr create` に `--draft` フラグを自動付与する `PreToolUse` フックプラグインです。「PR は必ず draft で起こし、レビュー後に手動で ready 化する」運用を強制したい場合に使います。git-guardrails から切り出した独立プラグインで、利用は任意 (使いたくない場合はインストールしないだけ) です。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `enforce-draft-pr` | PreToolUse | `gh pr create` 実行時に自動的に `--draft` フラグを付与する |

### キーワード

`git` `pr` `draft` `github` `github-flow`

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

`git commit` を実行する前に `/simplify` → `/codex:review` を必ず実行させ、未レビューの状態でのコミットをブロックするプラグインです。`/simplify` はコード変更を伴うため先に走らせ、`/codex:review` はその後の最終形をレビューします。修正によりステージング内容が変わった場合は `/simplify` から再度ループし、Claude の判断で「修正不要」となった時点で commit に進みます。PR 対象の `/code-review:code-review` は姉妹プラグイン [post-pr-review](#post-pr-review) が担当します。

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
| update-default-branch | `/update-default-branch` | デフォルトブランチを `git pull --ff-only` で最新化し、`git fetch --prune` でリモート追跡情報を整理した後、`[gone]` のローカルブランチを検出して確認なしに削除する (リモートが既に削除済みの branch なので安全) |

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

---

## codex-review-customize

公式 codex プラグインの `/codex:review` コマンド定義 (`commands/review.md`) をローカルでパッチし、以下 2 点を上書きする setup プラグインです。

1. frontmatter の `disable-model-invocation: true` を削除 → Skill tool から呼び出し可能に
2. 末尾に「Codex の出力を日本語に翻訳して提示する」指示を追記

スラッシュコマンドは `<plugin名>:<command名>` でプラグイン名空間が確定するため、別プラグインから `/codex:review` という同名は提供できません。本プラグインはコマンド名を `/codex:review` のまま保持したいユーザー向けに、公式定義のローカルパッチを setup する形を採っています。

### 機能

#### Commands

| コマンド | 説明 |
|---------|------|
| `/codex-review-customize:setup` | `apply-patch.sh` を実行し、公式 codex の `commands/review.md` をパッチする (idempotent、backup なしで git 管理が backup を兼ねる) |

### キーワード

`codex` `review` `patch` `i18n` `japanese` `skill`
