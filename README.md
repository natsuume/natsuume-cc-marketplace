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
| [git-guardrails](#git-guardrails) | 0.4.1 | GitHub Flow に準拠した Git ワークフロー。デフォルトブランチへの直接書き込み経路 (commit / push / PR head) をすべて deny し、変更は GitHub 上の PR merge 経由のみで取り込む。rebase ワークフロー Skill も提供。v0.4.0 で quote/heredoc 内のコマンド例文への誤 deny を修正 (segment/token ベース検出へ移行) |
| [enforce-draft-pr](#enforce-draft-pr) | 0.2.3 | `gh pr create` に `--draft` を自動付与する PreToolUse フックプラグイン (任意導入)。v0.2.1 で env-skip ループ境界越境による重複 `--draft` 挿入 parser bug を修正 |
| [auto-lint-check](#auto-lint-check) | 0.4.0 | ignore コメント挿入を編集時に禁止し、git commit 直前に staged ファイルを lint し、編集後に自動フォーマットを適用し、commit 直後に HEAD を再 lint して non-blocking フィードバックを返すプラグイン |
| [pre-push-review](#pre-push-review) | 3.0.3 | `git push` 前に 3 subagent (`pre-push-review:code-reviewer` self-contained correctness バグ検出 + `pre-push-review:codex-reviewer` codex review wrapper foreground 起動 + `pre-push-review:security-reviewer` self-contained security review) によるレビューを強制し、 PostToolUse / wrapper script で実走完了を自動検知してマーカー化することで未レビューな commit が remote に到達するのを構造的にブロックするプラグイン (`pre-commit-review` の後継)。 `/pre-push-review:review` slash command で 3 subagent を **同じアシスタントメッセージで並列に** 起動する確定的フローを提供し、 自律判断による順序揺れ / 起動漏れを構造排除して wall-clock を最遅レビュー 1 本の時間に短縮。 v3.0.0 で 3 レビューすべてを subagent 経由に統一 (互換破壊): v2.x の Skill `/code-review` と Bash 直接起動の codex wrapper を `pre-push-review:code-reviewer` / `pre-push-review:codex-reviewer` subagent に置換し、 context isolation と失敗検知の対称性を獲得 (auto-mark.sh は Skill 検知を全廃して subagent_type のみを検知)。 v2.x までに継続する設計: 3 マーカー常時必須 / wrapper の atomic rename marker / `block-bg-codex-wrapper.sh` の bg 起動 deny / macOS デフォルト bash 3.2.57 動作 |
| [update-default-branch](#update-default-branch) | 0.2.0 | PR マージ報告を契機にデフォルトブランチを最新化し、追跡先が消えたローカルブランチを片付けるプラグイン。v0.2.0 で実行モデルを「1 手順 = 1 つの素朴な git コマンド」に再設計し、同居プラグインの PreToolUse hook (auto-lint-check 等) に deny されず実行できるようにした |
| [natsuume-statusline](#natsuume-statusline) | 0.5.0 | Claude Code の `statusLine` 表示を提供し、`/natsuume-statusline:setup` で `settings.json` に登録するプラグイン |
| [agent-discipline](#agent-discipline) | 0.11.0 | Claude Code の振る舞い規律を統合配送する system prompt plugin + gh issue/pr 物理層検知。 SessionStart で常時適用ルール (物理層 = Bash コマンド分解 / before 系 = 設計事前壁打ち + issue 詳細化 + sub-issue + #N 相互参照 + PR closing keyword 規約 / during 系 = 自律作業中の判断境界 / 排他系 = 連続 issue 解決時の claim comment + branch push 二段排他制御) を 1 回 inject、 auto mode 時のみ UserPromptSubmit で after 系 (commit→push→PR→merge 自走 + マージ前提条件 hard gate) を per-turn inject。 v0.4.0 で PreToolUse type:agent hook を追加し、 gh issue\|pr create\|edit の body (--body inline / --body-file PATH 両対応) をセクション 2.1 / 3.1 の禁止表現規範で semantic 検証 → 違反時 block。 各 hook に `if: "Bash(gh <cmd>:*)"` filter を付与した 4 entries 構成で hook config 段階の物理 prefilter を実現 (= 非該当 Bash では agent subagent を起動しない真の narrow scope)。 加えて各 prompt 冒頭に defense-in-depth command guard を追加し、 複雑な command で `if` filter が fail-permissive で fall through した場合の偽 trigger を二段目で catch (= codex P2 指摘への対処)。 model は実装系メインセッションおよび全 subagent と同系列の claude-sonnet-5 に pin して旧 llm-default-branch-push-poc 型の非対称 SPOF を構造的に排除。 v0.7.1 でモデル別 2 プロンプトファイルと hooks.json 4 entries の同期ドリフトを検出する構造 lint (`lint-prompt-sync.sh` + `agent-discipline-prompt-lint.yml` CI) を追加。 v0.7.2 で lint の検出カバレッジの穴 3 件 (前提検証欠如 / Step 3 ブロック内容の未検証 / 除去対象の実在性未検証) を修正。 v0.7.3 で Closes 検証の issue 番号採用ルール (複数 `issue-<数字>-` 断片は先頭を採用) を一意化。 v0.7.4 で always-sonnet.md を公式 Sonnet 5 prompting guide 準拠で精緻化 (末尾 steering の両面較正 + 進捗報告グラウンディング追記 / 仮想反対案の具体条件化 / rule 8 の質問頻度非変更の明確化 / AskUserQuestion 提示を推奨可に統一) + hooks.json 4 prompts の規範参照先を inject-always.sh → hooks/prompts/always-sonnet.md に修正。 v0.8.0 で fable-discipline を統合 — モデル別分業規律を常時ルールと同一 hook で併載し、 block-fable-subagent.sh (PreToolUse Agent\|Task) を移設、 model 判定基盤 (4 段 fallback + state file + self-gate + one-shot 補正) を 1 実装に一本化。 v0.9.0 で Sonnet 5 向け分業規律 discipline-sonnet.md を新設 — 非 Fable セッション (opus / haiku 含む) と判定不能セッションにも分業規律を配送 (verifier 委任義務 = 非自明な全成果物、 委任根拠はコンテキスト分離 + fresh context の検証独立性)。 v0.10.0 で lint にチェック 4 (分業規律 2 ファイルの rule ID セット一致検証) を追加し、 CI paths filter に discipline-*.md を追加。 v0.10.1 で block-fable-subagent.sh に pending マーカー認識を追加 — モデル判定不能期間の model 未指定継承のみ deny (真の情報ゼロは fail-open 維持)。 v0.11.0 で issue 詳細化に境界・異常系の挙動決定を組み込み — issue-plan skill に列挙・確定手順 (要求側) とテストケース展開の Phase A 委譲 (禁止側) を追加し、 rule:issue-body 両版に skill 参照 1 文を追記 (軸 =「決定は issue・導出は Phase A」) |
| [ui-discipline](#ui-discipline) | 0.1.0 | UI (フロントエンド) 実装時の規律を配送するプラグイン。SessionStart で 10 ルール (層別の component 共通化基準、composition 実装様式、実装前の既存探索、表示/非表示・disabled の決定表、レイアウト安定、design token 経由のスタイル指定、アクセシビリティ基本則、非同期状態の網羅、フォントサイズ・ビューポート頑健性、視覚方向の明示的選択) を常時注入し、対応するコード例・チェックリストを ui-patterns skill として提供する。UI を持つプロジェクトでのみ enable して使う |
| [natsuume-writing](#natsuume-writing) | 0.4.1 | テックブログ・技術書執筆を支援するプラグイン。natsuume の過去執筆物から抽象化した執筆ルール (文体コア + 媒体プロファイル) を配置し、SessionStart でコア要点を常時注入する。outline skill (章立ての壁打ち + インファイルスケルトン書き込み)、draft skill (スケルトンからのたたき台一括生成 + 未検証事項の TODO 明示)、review skill (文体・構成・技術的正確さ・表記の 4 観点レビュー) を提供。執筆プロジェクトでのみ enable して使う |

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

ignore コメント挿入を編集時に禁止し、 `git commit` 直前に staged ファイルを lint し、 編集後に自動フォーマットを適用し、 commit 直後に HEAD を再 lint して non-blocking フィードバックを返すプラグインです。 モノレポ構成のサブディレクトリにある linter / formatter 設定 (`pyproject.toml [tool.ruff]` / `package.json` の `eslintConfig` / `prettier` 等) も自動的に検出します。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `block-ignore-lint-comment` | PreToolUse (Edit/Write/MultiEdit) | ESLint / Prettier / Ruff の suppress コメント (eslint-disable 系、 prettier-ignore 系、 noqa 系、 ruff: noqa 系) の新規挿入を deny する |
| `code-format` | PostToolUse (Edit/Write/MultiEdit) | 編集直後に `eslint --fix` / `prettier --write` / `ruff check --fix` / `ruff format` を実行する |
| `block-commit-lint` | PreToolUse (Bash) | `git commit` を検知し、 staged ファイルを `eslint` / `ruff check --stdin-filename` で lint。 違反があれば commit を deny する (repo override 失敗は fail-closed deny) |
| `post-commit-lint` | PostToolUse (Bash) | commit 直後に HEAD の変更ファイルを再 lint し、 残った警告を non-blocking で stderr に通知 (commit 自体は許容) |

### 対応 linter / formatter

- JavaScript / TypeScript: ESLint, Prettier
- Python: Ruff (`check --fix` および `format`)

### 設定検出

`pyproject.toml` の `[tool.ruff]` / `[tool.ruff.lint]`、 `package.json` の `eslintConfig` / `prettier`、 `eslint.config.{js,mjs,cjs,ts,mts,cts}`、 `prettier.config.{js,mjs,cjs,ts,mts,cts}`、 `.prettierrc.{json,json5,yaml,yml,ts}` 等を編集対象ファイルから直近祖先方向に探索して採用します。

### キーワード

`lint` `format` `eslint` `prettier` `ruff` `quality`

---

## pre-push-review

`git push` を実行する前に **3 subagent によるレビュー** (`pre-push-review:code-reviewer` = self-contained correctness バグ検出 + `pre-push-review:codex-reviewer` = codex review wrapper を foreground 起動して output を report として返す + `pre-push-review:security-reviewer` = self-contained security review) を必ず実行させ、未レビューな commit が remote に到達するのを構造的にブロックするプラグインです (`pre-commit-review` の後継)。Anthropic 系 (code-reviewer) と OpenAI 系 (codex-reviewer) の **独立した 2 つのバグレビュー** を重ねる defense-in-depth 構成で、security review が並走することで脆弱性経路も同じ最終形を観点でレビューします。

**v3.0.0 で 3 レビューすべてを subagent 経由に統一**しました (互換破壊あり)。v2.x の Skill `/code-review` と Bash 直接起動の codex review wrapper を、それぞれ `pre-push-review:code-reviewer` / `pre-push-review:codex-reviewer` subagent に置換しています。これにより:

- **context isolation**: 各レビューの詳細出力 (codex の verdict / findings、code review の詳細指摘) は subagent context に閉じ込められ、親 session の context が圧迫されません。親 session に返るのは markdown report (要約) だけです。
- **起動経路の単一化**: 3 軸とも `Agent` / `Task` tool で起動します。v2.x までは Skill / Bash / Agent の 3 通りで経路がばらけていました。marker 書き込み経路は 2 軸 (code / security) が `auto-mark.sh` の Agent 完了検知、1 軸 (codex) が wrapper の exit 0 内部書き込みで意図的に非対称が残ります (wrapper の dirty 検知と verdict 非依存 atomic rename の防御を維持するため)。
- **`auto-mark.sh` の簡略化と namespace prefix 必須化**: PRECHECK_RE / case 文は subagent_type が `pre-push-review:code-reviewer` / `pre-push-review:security-reviewer` (**namespace prefix 必須**) の完全一致のみを検知する形に縮みました。v2.x の name-only 受理は廃止 (他 plugin の同名 subagent が push gate marker を誤って書く bypass 経路を構造排除)。Skill 検知も全廃。
- **`/pre-push-review:review` slash command は 3 subagent 並列発出に書き換え**: deny メッセージとともに案内され、Claude はコマンド本文に固定された 3 `Agent` / `Task` tool call を 1 つのアシスタントメッセージ内で並列発出するだけです。順序揺れや起動漏れによる無駄ループが構造的に排除されます。wall-clock は最遅レビュー 1 本の時間で完了します。

codex review は v1.1.0 で `/codex:review` slash command 経由から bash wrapper (`run-codex-review.sh`) 経由 foreground 起動 hardcode に切り替えました (Claude が bg を選んで marker を書けず silent failure する経路を構造排除)。v3.0.0 では wrapper の起動を `pre-push-review:codex-reviewer` subagent 内に閉じ込めることで、wrapper output の context isolation を追加しています。security-reviewer / code-reviewer subagent を **self-contained** に保つのは、標準 skill (`/security-review` / `/code-review`) を主 session から直接呼ぶと turn が終了し、subagent 内から invoke しても nested subagent 制約に阻まれるためです。各 subagent は自前の prompt で single-pass review します。修正により branch 全差分 + 未コミット差分が変わると必須マーカーが自動失効するため、Claude は再走させる以外に push を通す手段がありません (= ループが構造的に強制されます)。

v1.x の `/simplify` (cleanup-only) マーカーは v2.0.0 で削除済みです。cleanup-only な性質上「edits が無くなるまでループ」が必要で並列化に乗らず、cleanup ステップを drop して bug 検出 + bug 検出 (OpenAI) + security の 3 軸に純化しています。CC version 依存の fail-open 緩和 (`lib/first-party-review.sh`) も同時に削除し、3 マーカーは常にすべて必須です。

### 設計上のメリット

- **commit 履歴の意味的解像度を保てる**: 初期実装 / `/code-review`・codex review 指摘修正 / security 指摘修正をそれぞれ独立 commit として記録できる (`git log` / `blame` / `bisect` の精度が上がる)。`pre-commit-review` ではこれらすべてが 1 commit に圧縮されていた
- **WIP / checkpoint commit の自由度**: 中間 commit を自由に重ねられるため、長時間 uncommitted 状態による作業損失リスクが減る
- **Web UI / IDE 経由の PR 作成にも対応**: push 段階で gate するため、PR 作成手段 (`gh CLI` / Web UI / IDE / API) のいずれを使われても **precondition (remote branch の存在) を破壊** することで構造的に PR 成立を阻止できる
- **多 commit PR の review 回数削減**: PR 全差分に対して 1 周のループで済む (実測ベースで 40-48% の review 回数削減見込み。1-commit PR では同等)

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `block-pre-push` | PreToolUse (`Bash`) | `git push` を検知し、`pre-push-review:code-reviewer` / `pre-push-review:codex-reviewer` / `pre-push-review:security-reviewer` subagent の **3 マーカー** が branch 全差分 + 未コミット差分のハッシュと一致しない場合に deny を返す。3 マーカーは常にすべて必須 (v2.0.0 で simplify マーカーと CC version 依存の fail-open 緩和を廃止)。deny メッセージは `/pre-push-review:review` slash command を案内する。default branch (master/main) 上の push は git-guardrails に委譲して skip |
| `block-bg-codex-wrapper` | PreToolUse (`Bash`) | `run-codex-review.sh` wrapper を Bash tool option `run_in_background: true` または shell-level `&` / `|` で起動する経路を deny する。v3.0.0 では wrapper は通常 `pre-push-review:codex-reviewer` subagent 内から foreground 起動されるが、subagent 内 Bash でも本 hook は発火するため bg 起動防御は引き続き有効 |
| `auto-mark` | PostToolUse (`*` wildcard) | `pre-push-review:code-reviewer` / `pre-push-review:security-reviewer` subagent の Agent / Task tool 完了を自動検知し、対応するマーカーに branch 全差分 + 未コミット差分のハッシュを書き込む。codex マーカーは wrapper script (`run-codex-review.sh`) が直接書き込む設計のため本 hook は codex-reviewer subagent を検知しない (wrapper の non-zero exit と subagent 完了タイミングが乖離する silent-pass 経路を作らないため)。各マーカーは subagent **完了時** に書く (launch ではない) ことで、subagent 失敗時に silent-pass しない |

#### Agents

| Agent 名 | 説明 |
|---------|------|
| `code-reviewer` | `git push` 前のレビューループの code review (correctness バグ検出) ステップで起動する self-contained subagent (v3.0.0 で追加)。 logic errors / null/undefined / error handling / resource leaks / concurrency / API misuse / data corruption の各カテゴリを自前の prompt で single-pass review し、 markdown report を親 session に返す。 v2.x までの Skill `/code-review` を置換 |
| `codex-reviewer` | `git push` 前のレビューループの codex review ステップで起動する最小 subagent (v3.0.0 で追加)。 内部で `hooks/scripts/run-codex-review.sh` wrapper を foreground で 1 回起動し、 wrapper の output (codex review の verdict / findings) を markdown report として親 session に返す。 codex-reviewed marker は wrapper 自身が atomic rename で書く設計を維持。 v2.x までの Bash 直接起動を置換 |
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

## agent-discipline

Claude Code の振る舞い規律 (= agent としての discipline) を統合配送する system prompt plugin + gh issue/pr 物理層検知です。 旧 `decompose-bash` と `auto-followthrough` を吸収し、 「物理層 + before / during / 排他 / 検知 / after」 の 6 段構成 (v0.4.0 で検知層を追加) で `additionalContext` の注入と `PreToolUse type:agent hook` での semantic 検証を提供します。 個人 marketplace の plugin 数肥大化を抑えるため、 機能ごとに別 plugin に分けず 1 plugin 内に複数のルール群を集約しています。

v0.4.0 で PreToolUse `type:agent` hook を追加し、 `gh (issue|pr) (create|edit)` の body content をセクション 2.1 / 3.1 の禁止表現規範で semantic 検証する **検知層** を新設しました。 `--body inline` と `--body-file PATH` の両形式に対応 (`block-commit-lint` plugin が PR body に `--body-file` を強制している repo policy との整合上、 ファイル読み取りが必要なため `type: agent` を採用)。 `model` は明示的に `claude-sonnet-5` (= 実装系メインセッションおよび全 subagent と同系列) に pin し、 各 hook の `if: "Bash(gh <cmd>:*)"` filter (公式 plugin `claude-plugins-official/security-guidance` と同じ syntax) で 4 entries (`gh issue create` / `gh issue edit` / `gh pr create` / `gh pr edit`) に分割した hook config 段階の物理 prefilter と組み合わせて、 旧 `llm-default-branch-push-poc` 廃止教訓の非対称 SPOF (= 全 Bash 発火の hook が暗黙 default = haiku ダウン時に全 Bash を PreToolUse error にする経路) を構造的に排除しました。 非該当 Bash 呼び出し (= `ls` / `git status` / `rg` / `gh issue view` 等の大半) では agent subagent がそもそも起動しません。 これにより誘導層 (v0.3.0 までの additionalContext 注入) と検知層 (v0.4.0 の物理 intercept) の defense-in-depth が成立しています。

v0.3.0 でセクション 2 / 3 を「思考は自由、 成果物への固定化は要承認」 非対称ルールへ強化しました。 Claude が設計レベルで複数案 (A 案 / B 案 / C 案) を検討した結果、 ユーザに `AskUserQuestion` で意思決定を委ねず自分で推奨を選んで issue body / PR 説明 / plan / commit に固定化してしまう failure mode を、 名指し禁止表現 (8 つの自己検知トリガー: 推奨マーキング / 独断の正当化 / 比較表で勝者決定 / 暗黙の決め打ち = 粒度差 / 「とりあえず」 系 / 暫定マーク残置 / ユーザ判断の先回り代弁 / 「選択点なし」 即断) + 起票直前 / pick up 時 self-check + 過去 session 独断の遡及検出で塞ぎます。 規律の checkpoint を「思考の中」 ではなく「成果物への書き出しの瞬間」 に置く非対称構造により、 検討段階での比較・推奨思考自体は禁止せず、 issue 駆動開発の前提「issue body = ユーザ承認済み契約書」 のみを守る形に整理されました。 v0.2.0 で `/goal` 等の並列 session フロー向けに claim comment (先着判定) + branch push (確定的排他) + ラベル削除規律をセクション 7 として追加 (誤着手 / ラベル誤削除事故への構造的対策)。 v0.1.1 で during 系を `inject-always.sh` に移動 (= permission_mode 非依存化、 default / acceptEdits などでも届く) し、 PostToolBatch 経路と once-per-turn dedup logic を撤去 (per-turn 2 回 inject → 1 回)。

### 配送される 6 レイヤ (v0.4.0 で検知層を追加)

| レイヤ | 配送経路 | inject / 発火条件 | 内容 |
|---|---|---|---|
| **物理層 (Bash 分解)** | `SessionStart` (`inject-always.sh`) | 常時 | Bash コマンドを最小粒度に分解して PreToolUse hook の取りこぼしを防ぐ |
| **before 系** | `SessionStart` (`inject-always.sh`) | 常時 | 設計 / 仕様の事前壁打ち + 「思考は自由、 成果物への固定化は要承認」 非対称ルール (2.1) + 自己検知トリガー 8 項目 / 名指し禁止表現 (v0.3.0)、 issue 起票時の `AskUserQuestion` 詳細化 + 起票直前 / pick up 時 self-check + 過去 session 独断の遡及検出 + PR / plan / commit にも同規律を適用 (3.1 / 3.2、 v0.3.0)、 並列粒度 + sub-issue + `#N` 相互参照、 PR closing keyword 規約 |
| **during 系** | `SessionStart` (`inject-always.sh`) | 常時 (`permission_mode` 非依存) | 実装は自走、 設計 / 仕様の再確認では止まらない (= issue 起票時に決まっているはず)。 ただし issue 未明記の要件発見 / 大きな後戻り判断では止まる |
| **排他系** (v0.2.0) | `SessionStart` (`inject-always.sh`) | 常時 (`permission_mode` 非依存) | 連続 issue 解決フロー (例: `/goal`) や並列 session 下で同 issue への重複着手を防ぐ。 claim comment (先着判定) + branch push (確定的排他) の二段構成。 branch 名規約 `<prefix>/issue-<N>-<slug>`。 ラベル削除は PR merge 時かつ `branch=` 値が自分の作業 branch と一致する場合のみ |
| **検知系 (gh issue/pr body)** (v0.4.0) | `PreToolUse` (`hooks.json` 内に inline 定義の type:agent hook を 4 entries) | Bash ツール呼び出し時、 個別 hook の `if: "Bash(gh <cmd>:*)"` filter で `gh issue create` / `gh issue edit` / `gh pr create` / `gh pr edit` 該当時のみ agent subagent を起動 (= hook config 段階の物理 prefilter、 非該当 Bash には影響ゼロ) | 誘導層 (before 系 2.1 / 3.1) で禁止された推奨マーキング / 独断の正当化 / 比較表で勝者決定 / 暗黙の決め打ち = 粒度差 / 「とりあえず」 系 / 暫定マーク残置 / ユーザ判断の先回り代弁 / 受入基準への未承認選択埋め込み を、 `--body inline` / `--body-file PATH` の双方から抽出して semantic 判定 → 違反時 `{"ok": false}` で block。 model は実装系メインセッションおよび全 subagent と同系列の `claude-sonnet-5` に pin (= SPOF を session 同期化、 narrow scope と組合せて blast radius も narrow) |
| **after 系** | `UserPromptSubmit` (`inject-auto.sh`) | `permission_mode == "auto"` 時のみ | 変更が一段落したら commit → push → PR 作成 → (4 条件 hard gate を満たしたら) マージまで自走 |

加えて、 auto mode セッションの `UserPromptSubmit` 初回発火時に cwd の未コミット変更を分類確認する独立 hook (`check-uncommitted-on-session-start.sh`) を併走させます。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `inject-always` | SessionStart | 常時適用ルール (物理層 + before 系 + closing keyword 規約 + during 系 + 排他系) を `additionalContext` として一括注入する。 内訳: (1) Bash 分解、 (2) 設計 / 仕様事前壁打ち + 「思考は自由、 成果物への固定化は要承認」 非対称ルール (2.1) と自己検知トリガー 8 項目 / 名指し禁止表現 (v0.3.0)、 (3) issue 詳細化と body 全埋め込み規約 + 起票直前 / pick up 時 self-check + 過去 session 独断の遡及検出 + PR / plan / commit へも同規律適用 (3.1 / 3.2、 v0.3.0)、 (4) issue 粒度と sub-issue + `#N` 関係性、 (5) PR closing keyword、 (6) 自律作業中の判断境界、 (7) 連続 issue 解決時の claim comment + branch push 排他制御 |
| **(inline) type:agent hook × 4 entries** (v0.4.0) | PreToolUse (matcher: Bash + 各 entry の `if: "Bash(gh <cmd>:*)"` filter) | `gh issue create` / `gh issue edit` / `gh pr create` / `gh pr edit` の 4 つの target command にだけ反応するよう hook config 段階で物理 prefilter (= 非該当 Bash では agent subagent 起動なし)。 起動時は body content を semantic 判定: `--body inline` / `--body-file PATH` 両対応 (後者は Read tool でファイル読み取り)。 禁止カテゴリ (推奨マーキング / 独断の正当化 / 比較表で勝者決定 / 暗黙の決め打ち = 粒度差 / 「とりあえず」 系 / 暫定マーク残置 / ユーザ判断の先回り代弁 / 受入基準への未承認選択埋め込み) に該当時 `{"ok": false}` で block。 model は `claude-sonnet-5` に pin (= 実装系メインセッションおよび全 subagent と同系列に揃え非対称 SPOF を排除) |
| `inject-auto` | UserPromptSubmit | `permission_mode == "auto"` 時のみ after 系 (commit→push→PR→merge 自走パイプライン + マージ 4 条件 hard gate + 禁止事項) を注入する。 v0.1.1 で旧 `PostToolBatch` 経路と once-per-turn dedup logic を撤去 |
| `check-uncommitted-on-session-start` | UserPromptSubmit (session 内初回のみ) | auto mode セッションで cwd に未コミット変更があれば、 出所分析と 4 分類 (今回タスク関連 / 以前の残骸 / 中間状態 / 不明) を Claude に要求する |

### 統合経緯 (旧 plugin との関係)

| 旧 plugin | 吸収先 | 等価機能 |
|---|---|---|
| `decompose-bash` (v0.1.1) | `inject-always.sh` の「物理層」 セクション | Bash コマンド分解の `additionalContext` 注入 |
| `auto-followthrough` (v0.2.3) | `inject-auto.sh` + `check-uncommitted-on-session-start.sh` | auto mode 時の commit→push→PR→merge 自走 / 未コミット分類チェック |

旧 2 plugin は本 plugin 導入時に同 PR で削除されています。 hook 構造と機能はそのまま維持されており、 マーカー dir のみ `auto-followthrough-markers/` → `agent-discipline-markers/` に変更されています (移行直後の旧 marker は OS の tmpfs cleanup で自然消去)。

### キーワード

`system-prompt` `discipline` `auto` `issue-driven` `bash` `decompose` `askuserquestion` `permission-mode` `hook` `guardrail`

### fable-discipline からの移行 (v0.8.0)

- `fable-discipline` は v0.8.0 で `agent-discipline` に統合され、本リポジトリから削除されました。モデル別分業規律の配送 (`inject-always.sh` / `resolve-model-on-prompt.sh`) とサブエージェントの Fable 実行防止 (`block-fable-subagent.sh`、PreToolUse `Agent|Task`) は `agent-discipline` の hook として提供されます。
- `fable-discipline` を install 済みの利用者は、当該 plugin を uninstall したうえで `agent-discipline` を update してください。
- 旧 state dir (`${TMPDIR:-/tmp}/fable-discipline-state`) は本統合後は参照されなくなり、OS の tmpfs cleanup により自然消去されます (手動削除は不要)。

---

## ui-discipline

UI (フロントエンド) 実装時の規律を配送するプラグインです。UI を持つプロジェクトでのみ enable して使います。共通化すべきか / 表示・非表示をどう決めるか / レイアウトが崩れないか、といった UI 実装で繰り返し発生する判断基準を 10 ルールとして常時配送し、判断のぶれによる重複 component や CLS (Cumulative Layout Shift)、a11y 欠落を防ぎます。

常時注入層 (`SessionStart`) が 10 ルールのコンパクト版 (意図 + 短い指示 + 境界) を配送し、ui-patterns skill が対応する具体的なコード例・チェックリストを提供する 2 層構成です。UI 実装規律は UI を持つプロジェクトでのみ意味を持つため agent-discipline には統合せず、plugin の enable 単位をそのまま適用範囲の単位とする独立 plugin としています。10 ルールはモデルに依存しない UI 実装上の判断基準であるため、モデル別の prompt 分岐は持ちません。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `inject-ui-rules` | SessionStart | `hooks/prompts/ui-rules.md` の全文を `additionalContext` として常時注入する。モデル判定・permission_mode 判定等の条件分岐は持たない |

#### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| ui-patterns | `/ui-patterns` | 常時注入される 10 ルールに対応する具体的なコード例・チェックリストを提供する |

### キーワード

`ui` `frontend` `accessibility` `design-tokens` `layout-shift` `component` `system-prompt` `hook` `skill`

---

## natsuume-writing

テックブログ・技術書執筆を支援するプラグインです。natsuume の過去執筆物から抽象化した執筆ルール (文体コア + 媒体プロファイル) を `rules/writing-rules.md` に配置し、SessionStart でそこから抽出したコア要点 (`rules/core-summary.md`) を常時注入します。詳細ルール全文と常時注入されるコア要点の 2 層構成により、地の文の断定度・文末表現・表記といった判断基準を常に手元に置きつつ、context を圧迫しない形で執筆規律を届けます。

現時点では rules 配置 + SessionStart コア注入 hook + outline skill (章立ての壁打ち + インファイルスケルトン書き込み) + draft skill (スケルトンからのたたき台一括生成 + 未検証事項の TODO 明示) + review skill (文体・構成・技術的正確さ・表記の 4 観点レビュー) を提供します。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `inject-core` | SessionStart | `rules/core-summary.md` の全文を `additionalContext` として常時注入する。モデル判定・permission_mode 判定等の条件分岐は持たない |

#### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| outline | `/natsuume-writing:outline` | 壁打ちで技術記事・技術書の章立て・セクション構成を決め、記事ファイルにインファイルスケルトン (見出し + HTML コメント) を書き込む |
| draft | `/natsuume-writing:draft` | スケルトン付き記事ファイル (outline skill の成果物) から、執筆ルールに準拠したたたき台を一括生成する。未検証事項は TODO コメントで明示する |
| review | `/natsuume-writing:review` | 技術記事・技術書の原稿を 4 観点 (文体ルール準拠 / 構成・論理展開 / 技術的正確さ / 誤字脱字・表記ゆれ) でレビューし、severity 付きの指摘一覧を提示する。ファイルは変更しない |

### キーワード

`writing` `tech-blog` `technical-writing` `style-guide` `system-prompt` `hook` `skill`
