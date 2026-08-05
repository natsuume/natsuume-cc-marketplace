# auto-lint-check プラグイン

ファイル編集時に lint ignore コメントの挿入をブロックし、`git commit` 直前に staged ファイルを linter で検査し、編集後に自動フォーマットを適用し、`git commit` 直後の HEAD を再 lint して non-blocking フィードバックを返すプラグインです。

## バージョン

v0.6.1

## 概要

このプラグインは 4 段階のコード品質ガードを提供します。

| タイミング | フック | 役割 |
|------------|--------|------|
| 編集直前 | `block-ignore-lint-comment` (PreToolUse / Write\|Edit\|MultiEdit) | ESLint/Prettier/Ruff の ignore コメント挿入を deny |
| 編集直後 | `code-format` (PostToolUse / Write\|Edit\|MultiEdit) | ESLint `--fix` / Prettier / Ruff で自動整形 |
| `git commit` 直前 | `block-commit-lint` (PreToolUse / Bash) | staged ファイルに lint エラーがあれば commit を deny |
| `git commit` 直後 | `post-commit-lint` (PostToolUse / Bash) | HEAD コミットを再 lint し、エラーがあれば non-blocking フィードバックを Claude に返す |

モノレポ構成 (例: `front/`, `server/` 配下にそれぞれ linter 設定がある) でも、対象ファイルから上向きに最寄りの設定ファイルを探索し、その所在ディレクトリを実行 CWD として linter を起動します。

## 対応 linter / formatter

- JavaScript / TypeScript (`.js .jsx .ts .tsx .mjs .cjs`)
  - ESLint (commit 直前 lint、`--fix` 事後修正)
  - Prettier (事後フォーマットのみ、`--write`)
- Python (`.py`)
  - Ruff (commit 直前 lint、`check --fix` と `format` で事後修正)

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install auto-lint-check@natsuume-plugins
```

本プラグインは Claude Code 専用で、Codex marketplace では配布していません。

## 機能一覧

### Hooks

#### 1. block-ignore-lint-comment

**ファイル**: `hooks/scripts/block-ignore-lint-comment.sh`
**イベント**: PreToolUse (matcher: `Write|Edit|MultiEdit`)

新規挿入される内容に下記の ignore コメントが含まれていたらツール実行を `deny` します。挿入の瞬間に止めることで、「commit 時に検出 → どこに入れたか探して除去」というループを回避します。

| linter / formatter | 検出パターン (抜粋) |
|--------------------|------------------|
| ESLint | `// eslint-disable`, `// eslint-disable-next-line`, `/* eslint-disable */`, `// eslint-enable` |
| Prettier | `// prettier-ignore`, `/* prettier-ignore */`, `<!-- prettier-ignore -->` |
| Ruff | `# noqa`, `# noqa: E501`, `# ruff: noqa`, `# fmt: off` / `# fmt: on` / `# fmt: skip` |

既に `old_string` や既存ファイルに含まれていた ignore コメントを保持するだけの編集は許可します (多重集合差分で「新規挿入分」だけを抽出)。例外的にどうしても必要なときは、ユーザー側で hook を一時的に無効化してください。

#### 2. block-commit-lint

**ファイル**: `hooks/scripts/block-commit-lint.sh`
**イベント**: PreToolUse (matcher: `Bash`)

Bash 経由で実行されるコマンドが `git commit` を含む場合に発火します。`git diff --cached --name-only` で staged ファイルを列挙し、対応する linter に内容を stdin で流して検査します。エラーが出たら commit を `deny` し、`permissionDecisionReason` に各ファイルの linter 出力を含めます。

**検出する commit 形式**:

- `git commit ...` / `git commit -m "..."` / `git commit --amend` 等の通常形式
- `&& git commit`、`; git commit`、`| git commit` などの連結形式
- `FOO=bar git commit ...` のような env-var prefix
- `git -c user.email=... commit ...` のような global option を挟む形式

**`git add` / `-a` / pathspec 同時実行時の挙動**:

本フックは Bash ツールの **実行前** に発火するため、同一コマンドの `git add` や `git commit -a` の自動 stage、`git commit <pathspec>` の working tree 直接 commit は、フック発火時点では index にまだ反映されていません。staged だけを見ると lint をすり抜けるため、コマンド文字列に以下のいずれかを検出した場合は、staged だけでなく working tree の変更 (modified + untracked) も lint 対象に含め、ソースを working tree から読み込みます。

検出パターン:

- `git add ...` / `git stage ...` を同一コマンド内に含む
- `git commit -a` / `-am` / `--all` (tracked-modified を auto-stage)
- `git commit <pathspec>` / `git commit -- <pathspec>` (pathspec form: 引数で指定したパスを working tree から commit)

これにより以下のパターンが正しく lint されます:

- `git add path && git commit -m ...`
- `git add -A && git commit -m ...`
- `git add . && git commit -m ...`
- `git commit -am ...`
- `git commit src/foo.py -m ...`
- `git commit -- src/foo.py`

pathspec の検出には Python の `shlex` でクォート対応トークン化を行うため、`git commit -m "long message with spaces"` のような引用符付きメッセージは pathspec として誤検出しません。

**lint ソースの選択**:

各ファイルは「どの set に属するか」で lint ソースを決定します:

- staged set のみ → `git show :path` (staged blob)
- working tree set のみ → working tree の現物
- 両方に属する (例: 元から staged + working tree でも変更されている) → **両方を別個に lint** し、どちらかが失敗すれば deny

これにより、元から staged にあった lint dirty なファイルが working tree で fix されたが再 stage されないまま `git add <other> && git commit` するケースで、staged blob のエラーを取りこぼしません。

過検出 (commit に含めない予定の編集まで lint) は許容しています。Claude が commit する状況では作業中ファイルだけが working tree にある運用が一般的で、不要な lint がほとんど発生しないためです。

**検出のスコープ外** (lint をスキップして通す):

- リポジトリ外での実行 (`git rev-parse --show-toplevel` 失敗)
- 対象ファイルが 0 件 (空 commit、`--allow-empty` など)
- 対応する linter 設定ファイルが見つからない
- linter バイナリが見つからない (skip し、警告を stderr に出す)

**Edge case**:

- `git -C dir commit` / `git --git-dir ... commit` / `git --work-tree ... commit` / `GIT_DIR=... git commit` のように global option / env-var で repo override する commit は、本フックが cwd の git を見るため対象 repo がズレます。`git -C . commit` のように cwd と一致する場合も静的に判別できず lint をすり抜ける経路になるため、これらの形式は **fail closed (deny) でブロック** します。対象 repo に `cd` してから別の Bash 呼び出しとして `git commit` を実行してください。
- `cd /other && git commit` のように同一コマンド内で cwd を切り替える形式も、`cd` 自体は本フックの検出対象外で、後段の `git commit` は cwd repo を対象として lint します (実行時には cwd が変わっているが hook はそれを認識できない)。同様に対象 repo に `cd` してから別の Bash 呼び出しで commit してください。
- `git add path` で stage 後、その path を working tree でさらに変更してから `git commit` (path に対する `git add` を含まない) を実行した場合、本フックは「working tree 上書き」モードに入らないため staged blob (古い内容) を lint します。実害は少ないですが、認識ズレを避けるため commit 直前に再 stage することを推奨します。

#### 3. code-format

**ファイル**: `hooks/scripts/code-format.sh`
**イベント**: PostToolUse (matcher: `Write|Edit|MultiEdit`)

編集後に対応する formatter / `--fix` を順に実行します。これにより commit 直前 lint で出る format-only エラーをあらかじめ抑制します。

| 言語 | 実行コマンド |
|-----|-------------|
| JS/TS | `eslint --fix <file>` → `prettier --write <file>` |
| Python | `ruff check --fix <file>` → `ruff format <file>` |

各コマンドは個別に成否を吸収し、失敗しても hook 全体は `exit 0` で終了します。対象ファイルが Git worktree 外にある場合は、無関係な祖先 config による formatter 起動を避けるため何も実行せず終了します。worktree 内の untracked file は自動整形の対象です。

#### 4. post-commit-lint

**ファイル**: `hooks/scripts/post-commit-lint.sh`
**イベント**: PostToolUse (matcher: `Bash`)

Bash 経由で `git commit` が実行された **直後** に発火する非ブロッキングなセーフティネットです。`block-commit-lint` (PreToolUse) を何らかの理由で bypass した経路 (例: プラグインを一時的に無効化したまま commit、subagent からの commit、ローカルにない linter rule が remote で適用される ケース など) に対して、commit 後にもう 1 度 lint を回してフィードバックを返します。

**発火条件**:

- `tool_name == "Bash"`
- コマンド文字列に `git commit` が含まれ、`parse-commit-command.py` が cwd repo に対する実 commit invocation を検出 (`--dry-run` / `--help` / repo override は skip)
- Bash 全体の `tool_response.exit_code` は **見ない**: `git commit -m msg && git push` のように commit は成功するが後続コマンド (push) が失敗するケースでも、現 HEAD は新 commit になっているため lint 対象とする

**lint 対象**:

- `git diff-tree --no-commit-id --name-only -r -m --root HEAD --diff-filter=ACMR` で取得した現 HEAD コミットの変更ファイル
- 内容は `git show HEAD:<path>` で blob を取り出して stdin から linter に流す (working tree のレース状態に依存しない)
- `--root` を付けることで初回 commit (parentless) でも全ファイルが列挙される
- `-m` を付けることで merge commit (2 parent 以上) でも各 parent との diff が列挙される (これがないと merge commit は空を返す)

**現 HEAD ベースのセマンティクス**:

PostToolUse hook では「Bash 実行前後の HEAD SHA 差分」を知る術がないため、「この Bash 実行で作られた commit」を厳密判定する手段はありません。本 hook は **「現在の HEAD コミットを再 lint する」セマンティクス**で動作します:

- `git commit` 実行で HEAD が動いた場合 → 直前に作られた commit を lint
- `git commit` が pre-commit reject 等で失敗し HEAD が動かなかった場合 → 前回 commit を再 lint (空振り clean なら無害、dirty なら "前から残っていた lint エラー" を notify する副次効果)
- `git commit && git reset --hard HEAD~1` のように同一 Bash 内で HEAD を巻き戻した場合 → 巻き戻し後の HEAD を lint (作られた commit ではなく最終状態)

reason 文面は「現在の HEAD コミット」に対する lint であることを明示し、直前 commit との因果を断定しないようになっています。

**出力**:

- lint エラーが 1 件もなければ何も出力せず `exit 0`
- lint エラーがあれば `{"decision": "block", "reason": "<HEAD SHA + lint 出力>"}` を返す
  - PostToolUse の `decision: "block"` は tool 自体は既に実行済みのため **rollback はしない**。reason が Claude のターン context に注入され、`git commit --amend` などの修正アクションを促す **non-blocking なフィードバック** として機能する

**fail policy**:

- 必須ツール (jq / python3 / git) が欠ける場合は silent skip (stderr に warning は出す)
- `block-commit-lint` のような fail-closed deny は PostToolUse では使えないため、本 hook は best-effort feedback として位置付け、検出漏れは `block-commit-lint` 側で fail-closed する設計に依存する

**検出のスコープ外**:

- repo override (`git -C` / `--git-dir` / `--work-tree` / `GIT_DIR=` / `cd dir &&` 等) を伴う commit
  - cwd repo の HEAD は commit 対象 repo の HEAD と異なる可能性があるため、誤検出を避けて skip
- `git commit` の `--dry-run` / `--help` (実 commit が無い)
- HEAD コミットが無い (空 repo) のケース

### linter バイナリの解決順序

JS/TS 系は以下の順で利用可能なものを採用します。

1. `<config-root>/node_modules/.bin/eslint` (または `prettier`)
2. `pnpm exec eslint`
3. `npx --no-install eslint`
4. グローバル PATH の `eslint`

Ruff は `uvx ruff` を最優先、次にグローバル PATH の `ruff` を使用します。

## モノレポ対応

対象ファイルから上向きに以下の設定ファイル/フィールドを探索し、最初に見つかったディレクトリを実行 CWD にします。

| linter | 設定ファイル / フィールド |
|--------|------------------------|
| ESLint | `eslint.config.{js,mjs,cjs,ts,mts,cts}`, `.eslintrc.{js,cjs,json,yml,yaml}`, `package.json` の `eslintConfig` |
| Prettier | `.prettierrc`, `.prettierrc.{json,json5,yml,yaml,js,cjs,mjs,ts,toml}`, `prettier.config.{js,cjs,mjs,ts,mts,cts}`, `package.json` の `prettier` |
| Ruff | `ruff.toml`, `.ruff.toml`, `pyproject.toml` の `[tool.ruff]` セクション |

設定ファイル名は上記の**固定リスト**の存在判定で探索します (グロブではないため、一覧外の拡張子は検出しません)。加えて `package.json` の `eslintConfig` / `prettier` フィールド、`pyproject.toml` の `[tool.ruff]` セクションも config-root として検出します。探索開始前に対象ファイルの親を実パスへ解決するため、workspace package への symlink 経由でもリンク先のディレクトリ階層を探索します。`.git` ディレクトリ (またはファイル) に到達したら探索を打ち切ります。

## ディレクトリ構成

```
auto-lint-check/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       ├── block-ignore-lint-comment.sh
│       ├── block-commit-lint.sh
│       ├── code-format.sh
│       ├── post-commit-lint.sh
│       └── lib/
│           ├── common.sh
│           ├── find-config-root.sh
│           ├── build-lint-plan.py
│           ├── detect-new-ignores.py
│           └── parse-commit-command.py
└── README.md
```

## 必要な実行環境

- `bash` (macOS 標準の `/bin/bash` 3.2 でも動作。lint plan 構築は `lib/build-lint-plan.py` に委譲しており、shell 側は連想配列等の bash 4+ 専用機能を使いません)
- `jq` (block-commit-lint.sh の input 解析 / deny メッセージ JSON 整形に必須 — 不在時は fail closed で commit を deny します。post-commit-lint.sh では silent skip に倒します)
- `git`
- `python3` (block-ignore-lint-comment.sh の差分検出、block-commit-lint.sh / post-commit-lint.sh の commit コマンド解析および lint plan 構築。block-commit-lint では不在時 fail closed で deny、post-commit-lint では silent skip)
- 利用したい linter / formatter (`eslint`, `prettier`, `ruff` または `uvx`)

## 既知の制約

- ignore コメント検知と commit 前 lint は Bash コマンド文字列と staged ファイルの静的解析ベースのため、alias・PATH 操作・sourced script 経由の function 継承のような間接経路は検出できません。threat model は典型的なバグや不注意による未 lint commit の防止であり、攻撃者意図を持った adversarial bypass の完全防御は対象外です。
- `block-commit-lint` の deny は stdout JSON が担い、exit code ではありません。deny 判定より前に hook 自体が crash した場合は fail-open に倒れます (SIGKILL / OOM では trap も実行されません)。crash 経路は可視化のみを行います。

## 関連情報

- [ESLint ドキュメント](https://eslint.org/docs/)
- [Prettier ドキュメント](https://prettier.io/docs/)
- [Ruff ドキュメント](https://docs.astral.sh/ruff/)
- [Claude Code Hooks ドキュメント](https://code.claude.com/docs/en/hooks)
