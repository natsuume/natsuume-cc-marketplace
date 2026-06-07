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
| [pre-push-review](#pre-push-review) | 2.0.0 | `git push` 前に `/code-review` (Anthropic read-only バグ検出) + codex review (OpenAI バグ検出 / bash wrapper `run-codex-review.sh` 経由 foreground 実行) + `pre-push-review:security-reviewer` subagent (self-contained security review) の 3 レビューを強制し、PostToolUse / wrapper script で実走完了を自動検知してマーカー化することで未レビューな commit が remote に到達するのを構造的にブロックするプラグイン (`pre-commit-review` の後継)。v2.0.0 で `/pre-push-review:review` slash command を新設し、3 レビューを **同じアシスタントメッセージで並列に** 起動する確定的フローに切替えた (Skill での自律判断による順序揺れ / 起動漏れが構造排除され、wall-clock も最遅レビュー 1 本の時間で完了する)。v1.x の `/simplify` (cleanup-only) マーカーは v2.0.0 で削除 (cleanup-only な性質上「edits が無くなるまでループ」が必要で並列化に乗らず、cleanup ステップを drop して bug 検出 + bug 検出 (OpenAI) + security の 3 軸 defense-in-depth に純化)。CC version 依存の fail-open 緩和 (`lib/first-party-review.sh`) も同時に削除し、3 マーカーは常にすべて必須 (互換破壊のため major bump)。v1.1.0 から継続する設計: codex review は wrapper 経由 foreground 起動を hardcode し、wrapper を background (`run_in_background: true` / shell-level `&` / `|`) で起動する経路は `block-bg-codex-wrapper.sh` が deny。security review は subagent 経由で self-contained 実行することで主 session の turn 終了問題と nested subagent 制約を回避。v2.0.0 で `lib/codex-companion-resolver.sh` の sort -V fallback を数値比較ベースに修正し、BSD sort 環境で codex 1.10+ release 後に古い `1.2.x` が選ばれる silent failure 経路を排除。macOS デフォルト bash 3.2.57 でも動作 |
| [update-default-branch](#update-default-branch) | 0.1.2 | PR マージ報告を契機にデフォルトブランチを最新化し、追跡先が消えたローカルブランチを片付けるプラグイン |
| [natsuume-statusline](#natsuume-statusline) | 0.5.0 | Claude Code の `statusLine` 表示を提供し、`/natsuume-statusline:setup` で `settings.json` に登録するプラグイン |
| [codex-review-customize](#codex-review-customize) | 0.3.1 | 公式 codex プラグインの `/codex:review` 定義をローカルでパッチし、Skill tool からの呼び出しを許可する setup プラグイン |
| [decompose-bash](#decompose-bash) | 0.1.1 | `SessionStart` で Bash コマンドを最小粒度に分解して独立 Bash 呼び出しとして実行するよう Claude に指示する `additionalContext` を注入し、`&&` / `\|\|` / `;` / `$(...)` 等のコマンド合成で PreToolUse hook の検知を取りこぼすのを防ぐプラグイン |
| [auto-followthrough](#auto-followthrough) | 0.2.3 | `permission_mode` が auto のとき、commit / PR 作成 / マージ完了まで自走するコンテキストを注入し、session 開始後の最初のプロンプト時点の未コミット変更については Claude に出所分析と分類確認を要求するプラグイン |

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

`git push` を実行する前に **3 レビュー** (`/code-review` = Anthropic read-only バグ検出 + codex review = OpenAI バグ検出 / bash wrapper `run-codex-review.sh` 経由 foreground 実行 + `pre-push-review:security-reviewer` subagent = self-contained security review) を必ず実行させ、未レビューな commit が remote に到達するのを構造的にブロックするプラグインです (`pre-commit-review` の後継)。Anthropic (`/code-review`) と OpenAI (codex review) の **独立した 2 つのバグレビュー** を重ねる defense-in-depth 構成で、security review が並走することで脆弱性経路も同じ最終形を観点でレビューします。

**v2.0.0 で `/pre-push-review:review` slash command を新設**し、3 レビューを **同じアシスタントメッセージで並列に** 起動する確定的フローに切替えました。これにより:

- **Skill での自律判断ではなく確定的実行**: Claude は「どのレビューを走らせるか / どの順番で / 引数は何か」を判断しません。コマンド本文に固定された 3 ツール並列発出のみが正解です。順序揺れや起動漏れによる無駄ループが構造的に排除されます。
- **3 レビューの並列実行**: wall-clock は最遅レビュー 1 本の時間で完了します (順次より大幅に高速)。3 レビューは互いに独立しているため並列化に乗ります。

v1.x の `/simplify` (cleanup-only) マーカーは v2.0.0 で削除しました。cleanup-only な性質上「edits が無くなるまでループ」が必要で並列化に乗らず、cleanup ステップを drop して bug 検出 + bug 検出 (OpenAI) + security の 3 軸に純化しています。CC version 依存の fail-open 緩和 (`lib/first-party-review.sh`) も同時に削除し、3 マーカーは常にすべて必須です (互換破壊のため major bump)。

codex review は v1.1.0 で `/codex:review` slash command 経由から bash wrapper (`run-codex-review.sh`) 経由 foreground 起動 hardcode に切り替えました (Claude が bg を選んで marker を書けず silent failure する経路を構造排除)。security review を **self-contained subagent** で実行するのは、標準 skill `/security-review` を主 session から直接呼ぶと turn が終了し、subagent 内から invoke しても nested subagent 制約に阻まれるためです。subagent は input validation / authn / crypto / injection / data exposure の各カテゴリを自前の prompt で single-pass review します。修正により branch 全差分 + 未コミット差分が変わると必須マーカーが自動失効するため、Claude は再走させる以外に push を通す手段がありません (= ループが構造的に強制されます)。

### 設計上のメリット

- **commit 履歴の意味的解像度を保てる**: 初期実装 / `/code-review`・codex review 指摘修正 / security 指摘修正をそれぞれ独立 commit として記録できる (`git log` / `blame` / `bisect` の精度が上がる)。`pre-commit-review` ではこれらすべてが 1 commit に圧縮されていた
- **WIP / checkpoint commit の自由度**: 中間 commit を自由に重ねられるため、長時間 uncommitted 状態による作業損失リスクが減る
- **Web UI / IDE 経由の PR 作成にも対応**: push 段階で gate するため、PR 作成手段 (`gh CLI` / Web UI / IDE / API) のいずれを使われても **precondition (remote branch の存在) を破壊** することで構造的に PR 成立を阻止できる
- **多 commit PR の review 回数削減**: PR 全差分に対して 1 周のループで済む (実測ベースで 40-48% の review 回数削減見込み。1-commit PR では同等)

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `block-pre-push` | PreToolUse (`Bash`) | `git push` を検知し、`/code-review` (Anthropic read-only バグ検出) / codex review (wrapper script 経由 foreground 実行) / `pre-push-review:security-reviewer` subagent の **3 マーカー** が branch 全差分 + 未コミット差分のハッシュと一致しない場合に deny を返す。3 マーカーは常にすべて必須 (v2.0.0 で simplify マーカーと CC version 依存の fail-open 緩和を廃止)。deny メッセージは `/pre-push-review:review` slash command を案内する。default branch (master/main) 上の push は git-guardrails に委譲して skip |
| `block-bg-codex-wrapper` | PreToolUse (`Bash`) | `run-codex-review.sh` wrapper を Bash tool option `run_in_background: true` または shell-level `&` / `|` で起動する経路を deny する。主 session が review 結果を観察しないまま push gate を通過する regression を防ぐ |
| `auto-mark` | PostToolUse (`*` wildcard) | `/code-review` (read-only バグ検出) の launch / `pre-push-review:security-reviewer` subagent の Agent / Task tool 完了 / `/security-review` 標準 skill (後方互換) launch を自動検知し、対応するマーカーに branch 全差分 + 未コミット差分のハッシュを書き込む。codex マーカーは wrapper script (`run-codex-review.sh`) が直接書き込む設計に統一されたため、本 hook は codex を検知しない。security マーカーは subagent **完了時** に書く (launch ではない) ことで、subagent 失敗時に silent-pass しない |

#### Agents

| Agent 名 | 説明 |
|---------|------|
| `security-reviewer` | `git push` 前のレビューループの security review ステップで起動する self-contained subagent。 input validation / authn / crypto / injection / data exposure の各カテゴリを自前の prompt で single-pass review し、 markdown report を親 session に返す。 標準 `/security-review` skill を invoke しないのは、 直接呼ぶと主 session の turn が終了し、 subagent 内から呼んでも標準 skill が要求する nested subagent (Task tool) が Claude Code の制約で動かないため |

### キーワード

`push` `review` `quality` `codex` `code-review` `security-review` `subagent` `branch-diff` `pr-diff` `parallel`

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

Claude Code の `statusLine` 表示 (カレントパス / GitHub リポジトリ / ブランチ / 変更量 / context 使用量 / レートリミット) を提供するプラグインです。`/natsuume-statusline:setup` を実行すると、`~/.claude/settings.json` の `statusLine.command` がこのプラグインの statusline に切り替わります。plugin cache 配下から実行された場合は `~/.claude/natsuume-statusline-entrypoint.sh` という安定 wrapper を経由するため、`/plugin update` 後も再 setup なしで最新版に追従します ([Claude Code bug #52079](https://github.com/anthropics/claude-code/issues/52079) の回避)。

### 表示内容

- **1 行目**: パス、GitHub repo (所有 namespace は `repo` に短縮)、branch、staged/modified、未コミット件数 / `clean`
- **2 行目**: context 使用量 (`ctx: (45%) 75.1k/1M` の数値表示。使用率・使用/最大トークン数を併記、バー無し)、5h / 7d レートリミット (パーセンテージ・リセット残時間・プログレスバー、色は使用率で変化)。横幅に収まらない場合は ⓪ 使用率の四捨五入整数化 → ① ctx% 削除 → ② バー短縮 → ③ バー削除 の順で段階的に簡略化し、情報を保ったまま縮退
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
