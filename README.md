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
| [git-guardrails](#git-guardrails) | 0.2.0 | GitHub Flow に準拠した Git ワークフロー。デフォルトブランチへの直接書き込み経路 (commit / push / PR head) をすべて deny し、変更は GitHub 上の PR merge 経由のみで取り込む。rebase ワークフロー Skill も提供 |
| [enforce-draft-pr](#enforce-draft-pr) | 0.1.0 | `gh pr create` に `--draft` を自動付与する PreToolUse フックプラグイン (任意導入) |
| [auto-lint-check](#auto-lint-check) | 0.1.0 | ファイル編集前に linter チェックを行い、編集後に自動フォーマットを適用するプラグイン |
| [pre-commit-review](#pre-commit-review) | 0.4.0 | `git commit` 前に `/simplify` → `/codex:review --wait` のループを強制し、PostToolUse で実走完了を自動検知してマーカー化することで未レビューのコミットを構造的にブロックするプラグイン。ループ回数が閾値以上に達した場合は `/codex:adversarial-review` を促す案内を deny メッセージに追加 |
| [post-pr-review](#post-pr-review) | 0.2.0 | `gh pr create` 成功直後に `/codex:adversarial-review` (実装方針・設計選択への批判的レビュー) の実行を誘導するプラグイン |
| [update-default-branch](#update-default-branch) | 0.1.0 | PR マージ報告を契機にデフォルトブランチを最新化し、追跡先が消えたローカルブランチを片付けるプラグイン |
| [natsuume-statusline](#natsuume-statusline) | 0.1.0 | Claude Code の `statusLine` 表示を提供し、`/natsuume-statusline:setup` で `settings.json` に登録するプラグイン |
| [codex-review-customize](#codex-review-customize) | 0.2.0 | 公式 codex プラグインの `/codex:review` および `/codex:adversarial-review` 定義をローカルでパッチし、Skill tool からの呼び出しを許可する setup プラグイン |

---

## git-guardrails

GitHub Flow に準拠した Git ワークフローを **構造強制** するプラグインです。「デフォルトブランチ (master/main) への変更は GitHub 上の PR merge 経由のみで取り込む」という運用を、ローカル側の write 経路 (commit / push / PR head) を 3 つの PreToolUse フックで多層防御することで保証します。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `block-default-branch-commit` | PreToolUse (`Bash`) | カレントブランチが master/main のときに `git commit` を deny。working branch を切ってから commit する運用を強制 |
| `block-default-branch-push` | PreToolUse (`Bash`) | master/main を更新するすべての push 系を deny。引数省略形 (`git push` 単独 / `git push origin`) や refspec 形式 (`feat:master`) も網羅 |
| `block-default-branch-pr` | PreToolUse (`Bash`) | `gh pr create` で head が master/main になる PR の作成を deny。`--head` 明示時もカレントブランチ判定時も両方カバー |

#### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| rebase-workflow | `/rebase-workflow` | rebase を用いてリモートのデフォルトブランチの変更を作業ブランチに取り込む |

> v0.1.0 までは `gh pr create` の `--draft` 自動付与もこのプラグインに含まれていましたが、責務分離のため [enforce-draft-pr](#enforce-draft-pr) プラグインへ切り出しました。draft 強制を使いたい場合はそちらを別途インストールしてください。

### キーワード

`git` `workflow` `github-flow` `rebase` `default-branch-protection`

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

`git commit` を実行する前に `/simplify` → `/codex:review --wait` を必ず実行させ、未レビューの状態でのコミットを構造的にブロックするプラグインです。`/simplify` はコード変更を伴うため先に走らせ、`/codex:review` はその後の最終形をレビューします。修正によりステージング内容が変わると `/simplify` と `/codex:review` のマーカーが自動失効するため、Claude は両方を再実行する以外に commit を通す手段がありません (= ループが構造的に強制されます)。

`/codex:review --wait` の完了が `LOOP_THRESHOLD` に達してもまだ commit に至らない場合、deny メッセージに `/codex:adversarial-review` (実装方針・設計選択への批判的レビュー) の実行を促す案内が追加されます。PR 作成後の adversarial レビューは姉妹プラグイン [post-pr-review](#post-pr-review) が担当します。

v0.4.0 で `cd dir && git commit ...` / `git -C dir commit ...` / heredoc 埋め込み commit message (`git commit -m "$(cat <<'EOF' ... EOF)"`) などの一般的な利用形態を許容するよう deny を緩和しました (cooperative 利用前提)。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `block-pre-commit` | PreToolUse (`Bash`) | `git commit` を検知し、`/simplify` と `/codex:review --wait` 双方のマーカーがステージング差分のハッシュと一致しない場合に deny を返す。ループカウンタが閾値以上に達していれば deny メッセージに `/codex:adversarial-review` の案内文を追加 |
| `auto-mark` | PostToolUse (`*` wildcard) | `/simplify` の launch および `/codex:review --wait` の Bash 完了を自動検知し、対応するマーカーに staged + unstaged tracked 差分のハッシュを書き込む。`/codex:review --wait` の成功完了時にはループカウンタも +1 する |

### キーワード

`commit` `review` `quality` `codex` `simplify` `adversarial-review`

---

## post-pr-review

Claude Code 経由で `gh pr create` が成功した直後に、`/codex:adversarial-review --wait --scope branch` の実行を `additionalContext` で Claude に誘導するプラグインです。`pre-commit-review` の commit 前ループ (`/codex:review` による表層レビュー) と役割を分け、本プラグインは PR タイミングに **設計レベルの challenge** (実装方針・設計選択・トレードオフ・前提条件への批判的レビュー) を差し込みます。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `nudge-pr-review` | PostToolUse | `gh pr create` 成功時に PR URL を抽出し、`additionalContext` で `/codex:adversarial-review --wait --scope branch` の実行を促す (強制ではなく誘導) |

### 注意事項

`additionalContext` 経由の誘導なので、Claude が無視することは原理的に可能です。また Web UI など Claude Code 以外で作成された PR には介入しません (これは設計上の意図)。`/codex:adversarial-review` を Skill tool から呼び出すには [codex-review-customize](#codex-review-customize) v0.2.0 以降のパッチを当てておく必要があります。

### キーワード

`pr` `review` `codex` `adversarial-review` `github`

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

公式 codex プラグインの `/codex:review` (`commands/review.md`) および `/codex:adversarial-review` (`commands/adversarial-review.md`) コマンド定義をローカルでパッチし、frontmatter の `disable-model-invocation: true` を削除して Skill tool からの呼び出しを許可する setup プラグインです。

スラッシュコマンドは `<plugin名>:<command名>` でプラグイン名空間が確定するため、別プラグインから `/codex:review` / `/codex:adversarial-review` という同名は提供できません。本プラグインはコマンド名を公式の `/codex:...` のまま保持したいユーザー向けに、公式定義のローカルパッチを setup する形を採っています。

### 機能

#### Commands

| コマンド | 説明 |
|---------|------|
| `/codex-review-customize:setup` | `apply-patch.sh` を実行し、公式 codex の `commands/review.md` と `commands/adversarial-review.md` の両方をパッチする (idempotent、backup なしで git 管理が backup を兼ねる) |

### キーワード

`codex` `review` `adversarial-review` `patch` `skill`
