# natsuume's Claude Code Plugin Marketplace

natsuume が作成・公開している Claude Code プラグインのマーケットプレイスリポジトリです。

## インストール方法

まずこのマーケットプレイスを追加します（marketplace 名は `natsuume-plugins`）：

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
```

次に使いたいプラグインを `<plugin-name>@natsuume-plugins` の形式でインストールします：

```bash
claude plugin install git-guardrails@natsuume-plugins
```

> Claude Code セッション内からは `/plugin marketplace add natsuume/natsuume-cc-marketplace` → `/plugin install <plugin-name>@natsuume-plugins` でも同じ操作ができます。

## プラグイン一覧

| プラグイン | バージョン | 説明 |
|-----------|-----------|------|
| [git-guardrails](#git-guardrails) | 0.3.0 | GitHub Flow に準拠した Git ワークフロー。デフォルトブランチへの直接書き込み経路 (commit / push / PR head) をすべて deny し、変更は GitHub 上の PR merge 経由のみで取り込む。rebase ワークフロー Skill も提供 |
| [enforce-draft-pr](#enforce-draft-pr) | 0.2.0 | `gh pr create` に `--draft` を自動付与する PreToolUse フックプラグイン (任意導入) |
| [auto-lint-check](#auto-lint-check) | 0.4.0 | ignore コメント挿入を編集時に禁止し、git commit 直前に staged ファイルを lint し、編集後に自動フォーマットを適用し、commit 直後に HEAD を再 lint して non-blocking フィードバックを返すプラグイン |
| [pre-push-review](#pre-push-review) | 0.8.5 | `git push` 前に `/code-review` (Claude Code v2.1.146 で `/simplify` からリネーム) → `/codex:review --wait --scope branch` → `pre-push-review:security-reviewer` subagent (self-contained に security review を実行) のループを強制し、PostToolUse で実走完了を自動検知してマーカー化することで未レビューな commit が remote に到達するのを構造的にブロックするプラグイン (pre-commit-review の後継)。security review を self-contained subagent で実行するのは、 標準 `/security-review` を直接呼ぶと主 session の turn が終了し、 subagent 経由でも nested subagent 制約で機能しないため。中間 commit を許容しつつ push 境界で gate するため commit 履歴の意味的解像度を保てる。macOS デフォルト bash 3.2.57 でも動作し、 hook 実装の予期せぬエラーは stderr にノンブロッキングで通知 |
| [update-default-branch](#update-default-branch) | 0.1.2 | PR マージ報告を契機にデフォルトブランチを最新化し、追跡先が消えたローカルブランチを片付けるプラグイン |
| [natsuume-statusline](#natsuume-statusline) | 0.2.0 | Claude Code の `statusLine` 表示を提供し、`/natsuume-statusline:setup` で `settings.json` に登録するプラグイン |
| [codex-review-customize](#codex-review-customize) | 0.3.1 | 公式 codex プラグインの `/codex:review` 定義をローカルでパッチし、Skill tool からの呼び出しを許可する setup プラグイン |
| [decompose-bash](#decompose-bash) | 0.1.1 | `SessionStart` で Bash コマンドを最小粒度に分解して独立 Bash 呼び出しとして実行するよう Claude に指示する `additionalContext` を注入し、`&&` / `\|\|` / `;` / `$(...)` 等のコマンド合成で PreToolUse hook の検知を取りこぼすのを防ぐプラグイン |
| [auto-followthrough](#auto-followthrough) | 0.2.3 | `permission_mode` が auto のとき、commit / PR 作成 / マージ完了まで自走するコンテキストを注入し、session 開始後の最初のプロンプト時点の未コミット変更については Claude に出所分析と分類確認を要求するプラグイン |
| [llm-default-branch-push-poc](#llm-default-branch-push-poc) | 0.2.2 | デフォルトブランチ (master/main) への直接 push を LLM (prompt hook) で判定する POC プラグイン。既存 git-guardrails と並行運用し、`bash -c` / `eval` / `$(...)` 等の shell parser では諦めていた経路を LLM の自然言語解釈でカバーできるか検証する |

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

## pre-push-review

`git push` を実行する前に `/code-review` → `/codex:review --wait --scope branch` → `pre-push-review:security-reviewer` subagent (self-contained に security review を実行) を必ず実行させ、未レビューな commit が remote に到達するのを構造的にブロックするプラグインです (`pre-commit-review` の後継)。`/code-review` (Claude Code v2.1.146 で旧名 `/simplify` からリネームされた bundled skill。 v2.1.145 以下のユーザーは旧名のまま使用可能で検出ロジックも両方を受け付ける) はコード変更を伴うため先に走らせ、`/codex:review --scope branch` はその後の最終形を branch 全差分で (= PR diff のセマンティクスで) 品質観点でレビューし、 security review は同じ最終形を security 観点でレビューします。 security review を **self-contained subagent** で実行するのは、 標準 skill `/security-review` を主 session から直接呼ぶと skill prompt の終端指示「マークダウンレポートだけで応答せよ」で turn が終了して後続フロー (`git push`) が進まず、 subagent 内から invoke しても標準 skill 本体が nested subagent (Task tool) を要求して Claude Code の制約に阻まれるためです。 subagent は標準 skill を invoke せず、 input validation / authn / crypto / injection / data exposure の各カテゴリを自前の prompt で single-pass review します。修正により branch 全差分 + 未コミット差分が変わると 3 つのマーカーが自動失効するため、Claude は再走させる以外に push を通す手段がありません (= ループが構造的に強制されます)。

### 設計上のメリット

- **commit 履歴の意味的解像度を保てる**: 初期実装 / `/code-review` edits / `/codex:review` 指摘修正をそれぞれ独立 commit として記録できる (`git log` / `blame` / `bisect` の精度が上がる)。`pre-commit-review` ではこれらすべてが 1 commit に圧縮されていた
- **WIP / checkpoint commit の自由度**: 中間 commit を自由に重ねられるため、長時間 uncommitted 状態による作業損失リスクが減る
- **Web UI / IDE 経由の PR 作成にも対応**: push 段階で gate するため、PR 作成手段 (`gh CLI` / Web UI / IDE / API) のいずれを使われても **precondition (remote branch の存在) を破壊** することで構造的に PR 成立を阻止できる
- **多 commit PR の review 回数削減**: PR 全差分に対して 1 周のループで済む (実測ベースで 40-48% の review 回数削減見込み。1-commit PR では同等)

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `block-pre-push` | PreToolUse (`Bash`) | `git push` を検知し、`/code-review` (旧名 `/simplify` も後方互換で検知) / `/codex:review --wait --scope branch` / `pre-push-review:security-reviewer` subagent の 3 つのマーカーが branch 全差分 + 未コミット差分のハッシュと一致しない場合に deny を返す。default branch (master/main) 上の push は git-guardrails に委譲して skip |
| `block-bg-codex-review` | PreToolUse (`Bash`) | `/codex:review` の background 起動を deny する。 Bash tool の `run_in_background: true` および codex companion の `--background` フラグは PostToolUse 発火時点で review 本体が未完了のため marker が書かれない (silent failure) → 後の push で deny されて review をやり直す羽目になるのを、 起動自体を止めることで防ぐ |
| `auto-mark` | PostToolUse (`*` wildcard) | `/code-review` (旧名 `/simplify` も後方互換で検知) の launch、 `/codex:review --wait --scope branch` の Bash 完了、 `pre-push-review:security-reviewer` subagent の Agent / Task tool 完了を自動検知し、対応するマーカーに branch 全差分 + 未コミット差分のハッシュを書き込む。`--scope branch` を含まない codex 起動は markers を更新しない (PR diff レビュー保証として不十分なため)。 security マーカーは subagent **完了時** に書く (launch ではない) ことで、 subagent 失敗時に silent-pass しない |

#### Agents

| Agent 名 | 説明 |
|---------|------|
| `security-reviewer` | `git push` 前のレビューループの security review ステップで起動する self-contained subagent。 input validation / authn / crypto / injection / data exposure の各カテゴリを自前の prompt で single-pass review し、 markdown report を親 session に返す。 標準 `/security-review` skill を invoke しないのは、 直接呼ぶと主 session の turn が終了し、 subagent 内から呼んでも標準 skill が要求する nested subagent (Task tool) が Claude Code の制約で動かないため |

### キーワード

`push` `review` `quality` `codex` `code-review` `simplify` `security-review` `subagent` `branch-diff` `pr-diff`

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

Claude Code の `statusLine` 表示 (カレントパス / GitHub リポジトリ / ブランチ / 変更量 / レートリミット) を提供するプラグインです。`/natsuume-statusline:setup` を実行すると、`~/.claude/settings.json` の `statusLine.command` がこのプラグインの statusline に切り替わります。plugin cache 配下から実行された場合は `~/.claude/natsuume-statusline-entrypoint.sh` という安定 wrapper を経由するため、`/plugin update` 後も再 setup なしで最新版に追従します ([Claude Code bug #52079](https://github.com/anthropics/claude-code/issues/52079) の回避)。

### 表示内容

- **1 行目**: パス、GitHub repo (所有 namespace は `repo` に短縮)、branch、staged/modified、未コミット件数 / `clean`
- **2 行目**: 5h / 7d レートリミットのパーセンテージ、リセット残時間、プログレスバー (色は使用率で変化)
- **3 行目**: 将来拡張用 (現状は空)

### 機能

#### Commands

| コマンド | 説明 |
|---------|------|
| `/natsuume-statusline:setup` | `settings.json` をバックアップし、安定 wrapper を設置したうえで `statusLine.command` をその wrapper (cache 配下実行時) または entrypoint に書き換える |

### キーワード

`statusline` `ui` `git` `ratelimit` `github`

---

## codex-review-customize

公式 codex プラグインの `/codex:review` (`commands/review.md`) コマンド定義をローカルでパッチし、frontmatter の `disable-model-invocation: true` を削除して Skill tool からの呼び出しを許可する setup プラグインです。

スラッシュコマンドは `<plugin名>:<command名>` でプラグイン名空間が確定するため、別プラグインから `/codex:review` という同名は提供できません。本プラグインはコマンド名を公式の `/codex:...` のまま保持したいユーザー向けに、公式定義のローカルパッチを setup する形を採っています。

### 機能

#### Commands

| コマンド | 説明 |
|---------|------|
| `/codex-review-customize:setup` | `apply-patch.sh` を実行し、公式 codex の `commands/review.md` をパッチする (idempotent、backup なしで git 管理が backup を兼ねる) |

### キーワード

`codex` `review` `patch` `skill`

---

## decompose-bash

`SessionStart` で Bash コマンドを最小粒度に分解して独立した Bash ツール呼び出しとして実行するよう Claude に指示する `additionalContext` を注入するプラグインです。`git add ... && git commit ... && git push` のようなコマンド合成によって `PreToolUse` hook の検知が取りこぼされる事故を防ぎます。

Claude Code の `PreToolUse` hook は Bash ツールの `command` 文字列に対するパターンマッチで判定されるため、`A && B && C` のような合成コマンドは先頭以外の部分が hook 検知を取りこぼす可能性があります。本プラグインは Claude がこの種の合成を避けるよう方針を注入することで、[git-guardrails](#git-guardrails) / [pre-push-review](#pre-push-review) / [auto-lint-check](#auto-lint-check) などの PreToolUse hook の信頼性を補強します。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `inject-decompose-context` | SessionStart | セッション開始時に Bash コマンドの分解方針を `additionalContext` として注入する。`&&` / `\|\|` / `;` / `$(...)` / バッククォート / `xargs` / `find -exec` を分解対象、パイプライン `\|` を単一論理操作の場合のみ許容、`cd $dir && cmd` やトランザクション的合成を例外として明記 |

### キーワード

`bash` `hook` `sessionstart` `decompose` `preToolUse` `guardrail`

---

## auto-followthrough

`permission_mode` が `auto` のときに、変更の commit → PR 作成 → マージ完了までを Claude が確認停止せず自走するための方針コンテキストを注入するプラグインです。さらに session 開始後の最初のプロンプトで未コミット変更があれば、その出所を分析し分類（既存作業の継続か / 別作業の混入か）をユーザーに確認するよう求めます。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `inject-auto-context` | UserPromptSubmit / PostToolBatch | auto mode 時に commit / PR / マージまでの自走方針を `additionalContext` として注入する。毎ターンの再注入 (UserPromptSubmit) と、ツール実行後の継続抑止 (PostToolBatch) の 2 経路で配送する |
| `check-uncommitted-on-session-start` | UserPromptSubmit (session 内初回のみ) | session 開始後の最初のプロンプト時点で未コミット変更があれば、出所分析と分類確認を Claude に要求する |

### キーワード

`auto` `automation` `followthrough` `permission-mode` `hook` `userpromptsubmit` `posttoolbatch`

---

## llm-default-branch-push-poc

デフォルトブランチ (master/main) への直接 push を、shell parser ではなく **LLM (prompt hook)** で判定する PoC プラグインです。既存の [git-guardrails](#git-guardrails) と並行運用し、`bash -c` / `eval` / subshell / `$(...)` など決定論パーサでは諦めていた経路を、LLM の自然言語解釈でカバーできるかを検証します。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| (prompt hook) | PreToolUse (matcher: `Bash`) | Bash コマンドがデフォルトブランチへの直接 push か否かを LLM prompt で判定し、該当すれば deny する。push と無関係なコマンドは prompt 内 early-return で軽量に通す |

> **PoC につき既知の制約**: 全 Bash 呼び出しで LLM 判定が走るためレイテンシ / コストが発生します。詳細は本プラグインの README を参照してください。

### キーワード

`git` `push` `default-branch-protection` `llm` `prompt-hook` `poc`
