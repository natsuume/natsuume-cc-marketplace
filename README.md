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

## 旧 Codex 配布からの移行

本リポジトリはかつて OpenAI Codex CLI 向けにも plugin を配布していましたが、2026-07 に廃止しました (fable-risk-labeler plugin は Codex 実行を前提としていたため plugin ごと廃止)。marketplace の更新だけでは Codex 側に install 済みの plugin の local config / cache は削除されないため、旧配布を利用していた場合は以下で撤去してください:

```bash
# Codex 側: install 済みの各 plugin を削除 (config / cache も削除される)
# <plugin-name> は実際の plugin 名に置換する (例: codex plugin remove agent-discipline@natsuume-plugins)
codex plugin remove <plugin-name>@natsuume-plugins

# Codex 側: marketplace 登録を削除
codex plugin marketplace remove natsuume-plugins
```

Claude Code 側で fable-risk-labeler を install していた場合は、plugin 自体が廃止されたため `claude plugin uninstall fable-risk-labeler@natsuume-plugins` で削除してください。

## プラグイン一覧

| プラグイン | バージョン | 説明 |
|-----------|------------:|------|
| [git-guardrails](#git-guardrails) | 0.6.1 | GitHub Flow を構造強制するプラグイン。デフォルトブランチ (master/main) への直接書き込み経路 (commit / push / master/main を head とする PR 作成) を PreToolUse hook で deny し、変更を GitHub 上の PR merge 経由のみに限定する |
| [enforce-draft-pr](#enforce-draft-pr) | 0.5.0 | `gh pr create` に `--draft` を自動付与する PreToolUse hook プラグイン (任意導入)。PR を常に draft として作成させ、レビューを経て ready 化する運用を支える |
| [auto-lint-check](#auto-lint-check) | 0.6.0 | 編集後の自動フォーマット適用、git commit 直前の staged ファイル lint、commit 直後の HEAD 再 lint を行うプラグイン。lint の ignore コメント挿入も編集時に禁止する |
| [pre-push-review](#pre-push-review) | 5.3.0 | `git push` 前に 3 つのレビュー (code review / codex review / security review) の完了を強制するプラグイン。レビュー済みマーカーと「commit 列 (HEAD / merge-base の OID) + ブランチ全差分」の同一性検証により、未レビューの commit が remote に到達するのを構造的にブロックする |
| [update-default-branch](#update-default-branch) | 0.4.0 | PR マージ報告を契機にデフォルトブランチを最新化し、追跡先が消えたローカルブランチを片付けるプラグイン |
| [natsuume-statusline](#natsuume-statusline) | 0.10.0 | Claude Code の statusLine 表示 (パス / repo / branch / 変更量 / context 使用量 / レートリミット) を提供するプラグイン。`/natsuume-statusline:setup` で `~/.claude/settings.json` に登録する |
| [agent-discipline](#agent-discipline) | 0.23.0 | 作業規律を SessionStart / SubagentStart prompt で配送し、gh issue/pr body の未決定事項を PreToolUse で検知するプラグイン |
| [ui-discipline](#ui-discipline) | 0.4.1 | UI 実装の 10 規律を SessionStart / SubagentStart prompt で常時注入するプラグイン。具体例は ui-patterns Skill が提供する |
| [natsuume-writing](#natsuume-writing) | 0.6.1 | natsuume の文体規則でテックブログ・技術書の執筆を支援するプラグイン |
| [codex-advisor](#codex-advisor) | 2.0.1 | Codex rescue / review / advisor を role 固有 foreground subagent に閉じ込め、追跡喪失から復旧する。pre-pushを含むCodex review 5サイクルごとの根本方針 advisor checkpointも強制する (要 openai-codex plugin + Codex CLI) |
| [rate-limit](#rate-limit) | 0.5.0 | Claude 自身がサブスクリプション usage limit (5h/週次の使用率と reset 時刻) を自律取得する `/rate-limit:status` Skill と、codex (OpenAI) の rate limit (週次枠使用率・reset 時刻) を取得する `/rate-limit:codex-status` Skill を提供するプラグイン。`/rate-limit:setup` で statusline キャッシュ連携を登録する |
| [session-handoff](#session-handoff) | 0.3.0 | context 使用率が閾値を超えたら handoff ドキュメントの作成を促し、次のセッション (`/clear`・起動直後) にその内容を自動注入するプラグイン。`/session-handoff:setup` で natsuume-statusline のキャッシュ連携を登録する |
| [repo-analytics](#repo-analytics) | 0.2.0 | GitHub の issue/PR タイムラインから AI タスクのリードタイム (着手→PR ready) を分析し、生存バイアス・サイズ交絡を統制した推移レポート (Artifact + ターミナルサマリ) を生成するプラグイン |

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
| `block-ignore-lint-comment` | PreToolUse (Edit/Write/MultiEdit/apply_patch) | ESLint / Prettier / Ruff の suppress コメント (eslint-disable 系、 prettier-ignore 系、 noqa 系、 ruff: noqa 系) の新規挿入を deny する |
| `code-format` | PostToolUse (Edit/Write/MultiEdit/apply_patch) | 編集直後に `eslint --fix` / `prettier --write` / `ruff check --fix` / `ruff format` を実行する。apply_patch の複数 path も個別に処理する |
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

`git push` を実行する前に **3 subagent によるレビュー** (`pre-push-review:code-reviewer` = self-contained correctness バグ検出 + `pre-push-review:codex-reviewer` = codex review wrapper を foreground 起動して結果を parent-safe report に抽象化 + `pre-push-review:security-reviewer` = self-contained security review) を必ず実行させ、未レビューな commit が remote に到達するのを構造的にブロックするプラグインです (`pre-commit-review` の後継)。Anthropic 系 (code-reviewer) と OpenAI 系 (codex-reviewer) の **独立した 2 つのバグレビュー** を重ねる defense-in-depth 構成で、security review が並走することで脆弱性経路も同じ最終形を観点でレビューします。

**v3.0.0 で 3 レビューすべてを subagent 経由に統一**しました (互換破壊あり)。v2.x の Skill `/code-review` と Bash 直接起動の codex review wrapper を、それぞれ `pre-push-review:code-reviewer` / `pre-push-review:codex-reviewer` subagent に置換しています。これにより:

- **context isolation**: reviewer は raw stdout / stderr、実行可能な command、具体的な再現手順を subagent context に留め、親 session には severity / location / impact / verification / fix direction / disposition を保持した parent-safe report だけを返します。追加検証が必要な場合は同じ subagent を resume し、raw detail を親へ流さず結果だけを再要約します。これは agent prompt と contract test で固定する **instruction contract** であり、report 本文を機械検査して情報流出を遮断する **hard security boundary** ではありません。
- **起動・marker 発行経路の単一化**: 3 軸とも `Agent` / `Task` tool で起動し、`auto-mark.sh` が SubagentStart の launch attestation (開始時 hash の one-shot 記録) と SubagentStop の parent-safe report・hash 束縛を検証して marker を発行します (v4.1.0 で PostToolUse 検知から移行。background 起動でも完了を捕捉)。Codex wrapper は review 開始時点の hash を pending attestation に束縛し、正規 report 完了後にだけ final marker へ昇格します。
- **`auto-mark.sh` の簡略化と namespace prefix 必須化**: PRECHECK_RE / case 文は subagent_type が `pre-push-review:code-reviewer` / `pre-push-review:security-reviewer` (**namespace prefix 必須**) の完全一致のみを検知する形に縮みました。v2.x の name-only 受理は廃止 (他 plugin の同名 subagent が push gate marker を誤って書く bypass 経路を構造排除)。Skill 検知も全廃。
- **`/pre-push-review:review` slash command は 3 subagent 並列発出に書き換え**: deny メッセージとともに案内され、Claude はコマンド本文に固定された 3 `Agent` / `Task` tool call を 1 つのアシスタントメッセージ内で並列発出するだけです。順序揺れや起動漏れによる無駄ループが構造的に排除されます。wall-clock は最遅レビュー 1 本の時間で完了します。

codex review は v1.1.0 で `/codex:review` slash command 経由から bash wrapper (`run-codex-review.sh`) 経由 foreground 起動 hardcode に切り替えました (Claude が bg を選んで marker を書けず silent failure する経路を構造排除)。v3.0.0 では wrapper の起動を `pre-push-review:codex-reviewer` subagent 内に閉じ込め、v4.0.1 では wrapper output を親へ verbatim relay せず parent-safe report に抽象化する契約を追加しています。security-reviewer / code-reviewer subagent を **self-contained** に保つのは、標準 skill (`/security-review` / `/code-review`) を主 session から直接呼ぶと turn が終了し、subagent 内から invoke しても nested subagent 制約に阻まれるためです。各 subagent は自前の prompt で single-pass review します。修正により branch 全差分 + 未コミット差分が変わると必須マーカーが自動失効するため、Claude は再走させる以外に push を通す手段がありません (= ループが構造的に強制されます)。

Linked worktree では marker、launch attestation、tombstone を main `.git` 直下ではなく、`git rev-parse --absolute-git-dir` が返す worktree 専用 git-dir (`.git/worktrees/<name>/`) に保存します。push deny メッセージは実際の marker storage を表示するため、main `.git` に残る別 worktree の marker と取り違えずに状態を確認できます。

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
| `block-pre-push` | PreToolUse (`Bash`) | `git push` を検知し、3 マーカーが commit 列 (HEAD / merge-base の OID) + branch 全差分 + 未コミット差分のハッシュと一致しない場合に deny を返す。deny メッセージは Claude Code の `/pre-push-review:review` を案内する。default branch (master/main) 上の push は git-guardrails に委譲して skip |
| `block-bg-codex-wrapper` | PreToolUse (`Bash`) | `run-codex-review.sh` wrapper を Bash tool option `run_in_background: true` または shell-level `&` / `|` で起動する経路を deny する。v3.0.0 では wrapper は通常 `pre-push-review:codex-reviewer` subagent 内から foreground 起動されるが、subagent 内 Bash でも本 hook は発火するため bg 起動防御は引き続き有効 |
| `auto-mark` | SubagentStart / SubagentStop (reviewer matcher) + PostToolUseFailure (`Agent\|Task`) | 3 reviewer subagent の開始時に launch attestation (開始時 hash の one-shot 記録) を書き、完了時 (SubagentStop) に agent_type・attestation の一回限りの消費・parent-safe report の単一 `Status: pass\|findings` 行・開始時 hash と現在 hash の一致をすべて検証して対応するマーカーへハッシュを書き込む (v4.1.0 で PostToolUse 検知から移行。background 起動でも完了を捕捉し、resume 後の再 stop・レビュー開始後の差分変更は fail-closed に遮断)。codex マーカーは wrapper の pending attestation の一致も要求して final marker へ昇格。PostToolUseFailure では残った codex pending を破棄する |

#### Agents

| Agent 名 | 説明 |
|---------|------|
| `code-reviewer` | `git push` 前のレビューループの code review (correctness バグ検出) ステップで起動する self-contained subagent (v3.0.0 で追加)。 logic errors / null/undefined / error handling / resource leaks / concurrency / API misuse / data corruption の各カテゴリを自前の prompt で single-pass review し、 markdown report を親 session に返す。 v2.x までの Skill `/code-review` を置換 |
| `codex-reviewer` | `git push` 前のレビューループの codex review ステップで起動する最小 subagent (v3.0.0 で追加)。内部で `hooks/scripts/run-codex-review.sh` wrapper を foreground で 1 回起動し、raw output は subagent context に留めて parent-safe report へ抽象化する。wrapper の pending attestation は正規 report 完了後に `auto-mark.sh` が codex-reviewed marker へ昇格する。v2.x までの Bash 直接起動を置換 |
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
- **2 行目**: モデル名 + reasoning effort (`Fable 5 (high)` 形式。effort 非対応モデル・古い Claude Code では effort が stdin に来ないためモデル名のみ)、context 使用量 (`ctx: (45%) 75.1k/1M` の数値表示。使用率・使用/最大トークン数を併記、バー無し)、5h レートリミット (パーセンテージ・リセット残時間・プログレスバー、色は使用率で変化)。横幅に収まらない場合は ⓪ 使用率の四捨五入整数化 → ① ctx% 削除 → ② バー短縮 → ③ バー削除 の順で段階的に簡略化し、情報を保ったまま縮退
- **3 行目**: 7d レートリミット + モデル別週次枠 (`7d(Fable)` 等。stdin の公式経路 `rate_limits.model_scoped` があればそれを、無ければ OAuth usage API 由来のキャッシュを使用)。縮退の挙動は 2 行目と同じ

### 機能

#### Commands

| コマンド | 説明 |
|---------|------|
| `/natsuume-statusline:setup` | `settings.json` をバックアップし、安定 wrapper を設置したうえで `statusLine.command` をその wrapper (cache 配下実行時) または entrypoint に書き換える |

### キーワード

`statusline` `ui` `git` `ratelimit` `github`

---

## agent-discipline

Claude Code の振る舞い規律 (= agent としての discipline) を配送する system prompt plugin + gh issue/pr 物理層検知です。旧 `decompose-bash` と `auto-followthrough` を吸収し、個人 marketplace の plugin 数肥大化を抑えるため、機能ごとに別 plugin に分けず 1 plugin 内に複数のルール群を集約しています。

以下の 6 レイヤと v0.4.0 以降の説明は配送設計です。

v0.4.0 で PreToolUse `type:agent` hook を追加し、 `gh (issue|pr) (create|edit)` の body content をセクション 2.1 / 3.1 の禁止表現規範で semantic 検証する **検知層** を新設しました。 `--body inline` と `--body-file PATH` の両形式に対応 (`block-commit-lint` plugin が PR body に `--body-file` を強制している repo policy との整合上、 ファイル読み取りが必要なため `type: agent` を採用)。 `model` は明示的に `claude-sonnet-5` (= 実装系メインセッションおよび全 subagent と同系列) に pin し、 各 hook の `if: "Bash(gh <cmd>:*)"` filter (公式 plugin `claude-plugins-official/security-guidance` と同じ syntax) で 4 entries (`gh issue create` / `gh issue edit` / `gh pr create` / `gh pr edit`) に分割した hook config 段階の物理 prefilter と組み合わせて、 旧 `llm-default-branch-push-poc` 廃止教訓の非対称 SPOF (= 全 Bash 発火の hook が暗黙 default = haiku ダウン時に全 Bash を PreToolUse error にする経路) を構造的に排除しました。 非該当 Bash 呼び出し (= `ls` / `git status` / `rg` / `gh issue view` 等の大半) では agent subagent がそもそも起動しません。 これにより誘導層 (v0.3.0 までの additionalContext 注入) と検知層 (v0.4.0 の物理 intercept) の defense-in-depth が成立しています。

v0.3.0 でセクション 2 / 3 を「思考は自由、 成果物への固定化は要承認」 非対称ルールへ強化しました。 Claude が設計レベルで複数案 (A 案 / B 案 / C 案) を検討した結果、 ユーザに `AskUserQuestion` で意思決定を委ねず自分で推奨を選んで issue body / PR 説明 / plan / commit に固定化してしまう failure mode を、 名指し禁止表現 (8 つの自己検知トリガー: 推奨マーキング / 独断の正当化 / 比較表で勝者決定 / 暗黙の決め打ち = 粒度差 / 「とりあえず」 系 / 暫定マーク残置 / ユーザ判断の先回り代弁 / 「選択点なし」 即断) + 起票直前 / pick up 時 self-check + 過去 session 独断の遡及検出で塞ぎます。 規律の checkpoint を「思考の中」 ではなく「成果物への書き出しの瞬間」 に置く非対称構造により、 検討段階での比較・推奨思考自体は禁止せず、 issue 駆動開発の前提「issue body = ユーザ承認済み契約書」 のみを守る形に整理されました。 v0.2.0 で `/goal` 等の並列 session フロー向けに claim comment (先着判定) + branch push (確定的排他) + ラベル削除規律をセクション 7 として追加 (誤着手 / ラベル誤削除事故への構造的対策)。 v0.1.1 で during 系を `inject-always.sh` に移動 (= permission_mode 非依存化、 default / acceptEdits などでも届く) し、 PostToolBatch 経路と once-per-turn dedup logic を撤去 (per-turn 2 回 inject → 1 回)。

### 配送される 6 レイヤ (v0.4.0 で検知層を追加)

| レイヤ | 配送経路 | inject / 発火条件 | 内容 |
|---|---|---|---|
| **物理層 (Bash 分解)** | `SessionStart` (`inject-always.sh`) | 常時 | Bash コマンドを最小粒度に分解して PreToolUse hook の取りこぼしを防ぐ |
| **before 系** | `SessionStart` (`inject-always.sh`) | 常時 | 設計 / 仕様の事前壁打ち + 「思考は自由、 成果物への固定化は要承認」 非対称ルール (2.1) + 自己検知トリガー 8 項目 / 名指し禁止表現 (v0.3.0)、 issue 起票時の `AskUserQuestion` 詳細化 + 起票直前 / pick up 時 self-check + 過去 session 独断の遡及検出 + PR / plan / commit にも同規律を適用 (3.1 / 3.2、 v0.3.0)、 並列粒度 + sub-issue + `#N` 相互参照、 PR closing keyword 規約 |
| **during 系** | `SessionStart` (`inject-always.sh`) | 常時 (`permission_mode` 非依存) | 実装は自走、 設計 / 仕様の再確認では止まらない (= issue 起票時に決まっているはず)。 ただし issue 未明記の要件発見 / 大きな後戻り判断では止まる |
| **排他系** (v0.2.0) | `SessionStart` (`inject-always.sh`) | 常時 (`permission_mode` 非依存) | 連続 issue 解決フロー (例: `/goal`) や並列 session 下で同 issue への重複着手を防ぐ。 claim comment (先着判定) + branch push (確定的排他) の二段構成。 branch 名規約 `<prefix>/issue-<N>-<slug>`。 merge による issue close 後の完了時クリーンアップ (ラベル + claim comment の削除) は必須ではなく、行う場合は claim comment の `session=` 値が自分のセッション ID と一致する場合のみ (`session=` の無い旧形式 claim は他 session 扱いで削除禁止) |
| **検知系 (gh issue/pr body)** (v0.4.0) | `PreToolUse` (`hooks.json` 内に inline 定義の type:agent hook を 4 entries) | Bash ツール呼び出し時、 個別 hook の `if: "Bash(gh <cmd>:*)"` filter で `gh issue create` / `gh issue edit` / `gh pr create` / `gh pr edit` 該当時のみ agent subagent を起動 (= hook config 段階の物理 prefilter、 非該当 Bash には影響ゼロ) | 誘導層 (before 系 2.1 / 3.1) で禁止された推奨マーキング / 独断の正当化 / 比較表で勝者決定 / 暗黙の決め打ち = 粒度差 / 「とりあえず」 系 / 暫定マーク残置 / ユーザ判断の先回り代弁 / 受入基準への未承認選択埋め込み を、 `--body inline` / `--body-file PATH` の双方から抽出して semantic 判定 → 違反時 `{"ok": false}` で block。 model は実装系メインセッションおよび全 subagent と同系列の `claude-sonnet-5` に pin (= SPOF を session 同期化、 narrow scope と組合せて blast radius も narrow) |
| **after 系** | `UserPromptSubmit` (`inject-auto.sh`) | `permission_mode == "auto"` 時のみ | 変更が一段落したら commit → push → PR 作成 → (4 条件 hard gate を満たしたら) マージまで自走 |

加えて、 auto mode セッションの `UserPromptSubmit` 初回発火時に cwd の未コミット変更を分類確認する独立 hook (`check-uncommitted-on-session-start.sh`) を併走させます。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `inject-always` | SessionStart | 常時適用ルール (物理層 + before 系 + closing keyword 規約 + during 系 + 排他系) を `additionalContext` として一括注入する。 内訳: (1) Bash 分解、 (2) 設計 / 仕様事前壁打ち + 「思考は自由、 成果物への固定化は要承認」 非対称ルール (2.1) と自己検知トリガー 8 項目 / 名指し禁止表現 (v0.3.0)、 (3) issue 詳細化と body 全埋め込み規約 + 起票直前 / pick up 時 self-check + 過去 session 独断の遡及検出 + PR / plan / commit へも同規律適用 (3.1 / 3.2、 v0.3.0)、 (4) issue 粒度と sub-issue + `#N` 関係性、 (5) PR closing keyword、 (6) 自律作業中の判断境界、 (7) 連続 issue 解決時の claim comment + branch push 排他制御 |
| **(inline) type:agent hook × 4** | PreToolUse (matcher: Bash) | 4本の pinned agent hook が対象4 commandを独立 read-only process で評価し、違反時のみ deny する |
| `inject-auto` | UserPromptSubmit | `permission_mode == "auto"` 時だけ after 系を注入する |
| `check-uncommitted-on-session-start` | UserPromptSubmit (session 内初回のみ) | auto で未コミット変更を4分類する |

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

常時注入層 (`SessionStart`) が 10 ルールの compact 版を配送し、ui-patterns Skill が具体的なコード例・チェックリストを提供する 2 層構成です。`hooks/prompts/ui-rules.md` と subagent 前置きを使います。UI 実装規律は UI を持つプロジェクトでのみ意味を持つため agent-discipline には統合せず、plugin の enable 単位をそのまま適用範囲の単位としています。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `inject-ui-rules` | SessionStart | 10 rule ID を `additionalContext` として常時注入する |
| `inject-ui-rules-subagent` | SubagentStart | 前置き注記 + 共通 prompt を連結して注入する |

#### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| ui-patterns | `/ui-patterns` | 常時注入される 10 ルールに対応する具体的なコード例・チェックリストを提供する |

### キーワード

`ui` `frontend` `accessibility` `design-tokens` `layout-shift` `component` `system-prompt` `hook` `skill`

---

## natsuume-writing

テックブログ・技術書執筆を支援するプラグインです。natsuume の過去執筆物から抽象化した執筆ルール (文体コア + 媒体プロファイル) を `rules/writing-rules.md` に配置します。`rules/core-summary.md` を SessionStart で常時注入します。詳細ルールは共有 Skills が同じ正本から読みます。

現時点では rules 配置 + SessionStart コア注入 hook + outline skill (章立ての壁打ち + インファイルスケルトン書き込み) + draft skill (スケルトンからのたたき台一括生成 + 未検証事項の TODO 明示) + review skill (文体・構成・技術的正確さ・表記の 4 観点レビュー) を提供します。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `inject-core` | SessionStart | `rules/core-summary.md` を `additionalContext` として常時注入する |

#### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| outline | `/natsuume-writing:outline` | 壁打ちで技術記事・技術書の章立て・セクション構成を決め、記事ファイルにインファイルスケルトン (見出し + HTML コメント) を書き込む |
| draft | `/natsuume-writing:draft` | スケルトン付き記事ファイルから、執筆ルールに準拠したたたき台を一括生成する。未検証事項は TODO コメントで明示する |
| review | `/natsuume-writing:review` | 原稿を文体・構成・技術的正確さ・表記の 4 観点で読み取り専用レビューし、severity 付きの指摘一覧を提示する |

### キーワード

`writing` `tech-blog` `technical-writing` `style-guide` `system-prompt` `hook` `skill`

---

## codex-advisor

Anthropic の [Advisor tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool) パターン (実行役のモデルが戦略的な岐路で別の高知能モデルに相談し、plan / course-correction の助言を受け取って続行する構成) を Claude Code に移植し、OpenAI Codex を助言役として利用するプラグインです。本家は Anthropic API のサーバーサイド機能で advisor が Claude モデル限定のため、hook + skill + wrapper script として再構成しています。

Codex は read-only sandbox でリポジトリを自分で読んで裏取りしたうえで助言を返します (ファイル変更は行いません)。reasoning effort は `xhigh` 固定です。助言と手元の証拠が衝突したときは、衝突を明示した再相談 (reconcile call) で解消する規律を含みます。設計/仕様の決定はユーザ専権のままで、助言は AskUserQuestion の代替にしません。advisor 相談自体をコード差分の finding 取得へ転用せず、一般の `/codex:review` は review runner、push gate は [pre-push-review](#pre-push-review) が担当します。

v1.1.0 では `pre-push-review:codex-reviewer` の正常終了と成功した一般 Codex review を session ごとに合算し、5 サイクル完了後は main session の Stop と次の一般 / pre-push Codex review 起動を block します。解除には、元の Goal / 制約、5 サイクルの findings と修正の傾向、現在の仮説を材料に根本方針・問題設定・設計境界・検証戦略を問い直す advisor checkpoint が必要です。通常の advisor 相談ではカウンターを解除しません。

v1.0.0 では rescue / review / advisor を `codex-advisor:rescue-runner` / `review-runner` / `advisor-runner` の role 固有 foreground subagent に統一しました。main session や通常 subagent から companion / wrapper を直接実行すると PreToolUse hook が deny し、Stop hook が対応 runner への reroute、active Agent の completion 回収、1 回だけの retry を要求します。

runner は Codex 起動前の companion job 集合を保持します。rescue / advisor は detached task の job ID を追跡し、review は長時間 Bash の tracking を失った場合に起動前後の job 集合差分から review job を一意に特定します。いずれも `status` / `result` で terminal output を回収するため、Claude の Bash / TaskOutput tracking が失われても companion の永続 state から復旧できます。候補が 0 件または複数件なら別 job を推測しません。

通常 subagent が相談を必要とする場合、wrapper を直接実行せず self-contained な request を親へ返します。親が advisor runner を起動できるのは、委任指示が codex-advisor の使用を明示的に許可した場合だけです (相談は課金を伴う外部呼び出しのため)。

v0.2.0 からは相談規律に加えて `/codex:rescue` の thread 選択規律 (`rule:rescue-thread`) も注入します。rescue 起動時の `--resume` / `--fresh` を Claude が自律決定して常に付与し、thread 選択の質問で自走を止めません (`--resume` は「直前の rescue と同一論点の続き + 対象がセッション内最新の再開可能 task と確実に分かる場合」のみ、それ以外・迷ったら `--fresh`。ユーザのフラグ明示指定が最優先)。openai-codex plugin の「フラグ指定時は質問しない」挙動 (v1.0.6) を前提とするため、外部 plugin 側は無変更です。

Claude Code からの利用には [公式 codex plugin](https://github.com/openai/codex-plugin-cc) (`claude plugin install codex@openai-codex`) と Codex CLI + 認証が必要です。この plugin は Claude Code から異種モデルの Codex へ相談するためのものなので、Codex marketplace では配布しません。Claude Code marketplace の hook、Skill、wrapper は従来どおり利用できます。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `inject-advisor-rules` | SessionStart | メインセッション向けの相談・rescue thread・role 固有 runner 規律を `additionalContext` として常時注入する |
| `inject-advisor-rules-subagent` | SubagentStart | 通常 subagent 向けの許可境界と、直接 wrapper ではなく相談 request を親へ返す規律を注入する |
| `manage-codex-runners` | SessionStart / SessionEnd / PreToolUse / SubagentStart / SubagentStop / Stop | 直接実行 gate、UID + session-scoped state、active 回収、bounded retry、一般 / pre-push共通の5 reviewごとの根本方針 checkpoint、stale cleanupを管理する |

#### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| consult | Claude: `/codex-advisor:consult` | self-contained な相談プロンプトを組み立て、`codex-advisor:advisor-runner` を foreground 起動して助言を回収する |

### キーワード

`codex` `advisor` `second-opinion` `system-prompt` `hook` `skill` `openai`

---

## rate-limit

Claude (エージェント自身) が、セッション内でサブスクリプションの usage limit (5 時間セッション枠・週次枠の使用率と reset 時刻) をユーザ操作なしで取得できる `/rate-limit:status` Skill を提供します。加えて、codex (OpenAI) の rate limit (週次枠使用率・reset 時刻・plan 種別) を `codex app-server` RPC 経由で取得する `/rate-limit:codex-status` Skill も提供します。

取得経路は①→②の順でフォールバックします。① は Claude Code の statusLine に渡される公式データ (`rate_limits` フィールド) を wrapper がキャッシュに保存したもので、60 秒以内ならこちらを優先します。② は `GET https://api.anthropic.com/api/oauth/usage` (OAuth token 認証) を都度呼び出す経路で、① が古い・存在しない場合のみ使われます。② は **非公式・undocumented** な API で、関連 issue (anthropics/claude-code#31021, #31637) は Anthropic 自身により invalid / not planned としてクローズされており、予告なく動作しなくなる可能性があります。

`/rate-limit:setup` を実行すると、statusline の出力を横取りしてキャッシュへ書き出す安定 launcher (`~/.claude/rate-limit-statusline-launcher.sh`) を設置し、既存の `statusLine.command` (natsuume-statusline 等) をこの launcher で包みます (既存 statusline の表示は変化しません)。**setup は必須ではなく**、未 setup でも経路② 単独で `/rate-limit:status` は動作します。

経路② は `~/.claude/.credentials.json` (macOS では Keychain) の OAuth access token を読み取りますが、送信先は `https://api.anthropic.com` のみに固定しており、token をログ・stderr・プロセス一覧・一時ファイルに露出させない実装です。macOS の Keychain 分岐は開発環境 (Linux/WSL2) では実機未検証です。

### 機能

#### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| status | `/rate-limit:status` | `scripts/fetch-rate-limit.sh` を実行し、5h/週次の使用率と reset 時刻を報告する |
| codex-status | `/rate-limit:codex-status` | `scripts/codex-rate-limit.sh` を実行し、codex app-server RPC で codex の rate limit (週次枠使用率・reset 時刻・plan 種別) を報告する。`--max-used-percent <N>` で閾値判定 (exit 0/1/2) |

#### Commands

| コマンド | 説明 |
|---------|------|
| `/rate-limit:setup` | statusline キャッシュ連携 (安定 launcher) を `~/.claude/settings.json` に登録する |

### 依存

`jq` (必須)、`curl` と `claude` CLI (経路② のみ)、`codex` CLI (`/rate-limit:codex-status` のみ)

### キーワード

`rate-limit` `usage-limit` `statusline` `oauth` `skill` `codex`

---

## session-handoff

context 使用率が閾値 (既定 60%) を超えたら handoff ドキュメントの作成を Claude に促し、次のセッション (`/clear` または起動直後) にその内容を自動注入するプラグインです。長時間セッションが context 圧縮や `/clear` を挟んでも、直前までの背景・進行中の作業・残作業を新セッションへ引き継げるようにします。

検知 (`detect-context-threshold`, PostToolUse) と注入 (`inject-pending-handoff`, SessionStart) を使います。検知は 1 セッション 1 回のみ通知し (marker は「通知発行済み」の意味で「handoff 保存済み」ではありません)、注入は rename の atomic 性で **at-most-once** を保証します (24 時間を超えた pending は注入せず、30 日を超えたファイルは削除します)。

検知 hook が読む context 使用率は自プラグインでは取得できず、natsuume-statusline (v0.6.0+) が書き出すキャッシュ (`${TMPDIR:-/tmp}/natsuume-context-cache-<uid>/<session_id>.json`) に依存します。natsuume-statusline を使わない場合は、`/session-handoff:setup` で cache 専用の安定 launcher を登録できます。setup skill は既存の statusline 設定を分類し (natsuume-statusline 導入済み / 自 launcher 導入済み / 他 statusline / 未設定)、他の statusline を包む前には 1 段の連鎖検査 (自 launcher への平文参照、または `INNER_COMMAND_B64` 等の既知形式 base64 代入行を decode した中身への参照を検出) を行って二重ラップ・循環を防ぎます。連鎖検査をすり抜けた循環構成に対しては、launcher 自身が実行時の env 再帰ガードで無限再帰を切断します (rate-limit と同型の launcher パターン)。

### 機能

#### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `detect-context-threshold` | PostToolUse (`*`) | context 使用率が閾値を超えたことを検知し、handoff 作成指示を注入する (1 セッション 1 回) |
| `inject-pending-handoff` | SessionStart (`clear\|startup`) | 直近の pending を自動注入する (at-most-once) |

#### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| setup | `/session-handoff:setup` | context 使用率キャッシュの producer (natsuume-statusline または安定 launcher) を構成する |

### 環境変数

| 変数 | 意味 | 既定値 |
|---|---|---|
| `SESSION_HANDOFF_THRESHOLD` | 検知 hook が使う context 使用率の閾値 (1〜99 の整数) | `60` |

### スコープ外

Stop hook による handoff 作成の強制、transcript のパースによる使用率算出、同一セッション内の再警告は行いません。

### キーワード

`session-handoff` `context-window` `handoff` `session-start` `statusline` `cache` `hook` `skill`

---

## repo-analytics

GitHub の issue/PR タイムラインから AI タスクのリードタイム (着手→PR ready) を分析するプラグインです。`gh` CLI で取得した issue/PR のラベル・コメント・close/reopen イベントから着手時刻・PR ready 時刻・merge 時刻を推定し、まだ着手中・未マージのタスクを打ち切り (censoring) として扱う、PR サイズ (追加+削除行数) を帯分けして交絡を統制する、といった処理を経て、週次推移・区間統計・イベント年表を含む Artifact レポートとターミナルサマリを生成します。

Skill `leadtime` は `/repo-analytics:leadtime` で呼び出します。対象は省略時カレントの git リポジトリ、ディレクトリパス指定で配下リポジトリの再帰探索、`owner/repo` のカンマ区切りリストのいずれかを受け付け、`since=YYYY-MM-DD` で集計開始日を絞り込めます。副作用は `gh` CLI の read-only query のみで、中間ファイルはプロジェクト内に作成せずセッションの scratchpad にのみ保存します。

### 機能

#### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| leadtime | `/repo-analytics:leadtime` | GitHub issue/PR のタイムラインを収集し、生存バイアス・サイズ交絡を統制したリードタイム推移レポート (Artifact) とターミナルサマリを生成する |

### キーワード

`analytics` `leadtime` `github` `metrics` `report`

---

## Contributing

このリポジトリは Claude Code 単一の plugin marketplace です。共有 metadata の正本は `.claude-plugin/marketplace.json` と各 `plugins/<plugin>/.claude-plugin/plugin.json` で、plugin 一覧・version は両ファイルと本 README の一覧テーブル、各 `plugins/<plugin>/README.md` の `## バージョン` の 4 箇所で常に一致させます。

```bash
python3 scripts/check_plugin_versions.py <base_revision>
python3 -m unittest discover -s tests -p 'test_*.py'
```

`check_plugin_versions.py` は CI (PR / master push) で、変更 plugin の version bump 漏れ、4 箇所の表示不一致、marketplace と `plugins/` ディレクトリの集合不一致を検査します。plugin 配下 (hooks / commands / agents / skills / scripts / lib 等、および `README.md`) を変更したら version を bump してください (bump 幅は `.claude/CLAUDE.md` の semver 規約に従います)。`master` への merge が marketplace の配布更新になるため、write 権限を持つ release bot は使用しません。
