# auto-lint-check プラグイン

ファイル編集時に lint ignore コメントの挿入をブロックし、`git commit` 直前に staged ファイルを linter で検査し、編集後に自動フォーマットを適用するプラグインです。

## バージョン

v0.2.0

### v0.1.1 → v0.2.0 の変更点

- **lint 検査のタイミングを「ファイル編集前 (PreToolUse / Edit)」から「`git commit` 直前 (PreToolUse / Bash)」に移行**
  - 旧 `auto-lint-check.sh` (Edit/Write/MultiEdit 単位の編集後予測 lint) を廃止
  - 新 `block-commit-lint.sh` を追加: Bash 経由で `git commit` が実行される直前に staged ファイルを lint する
  - これにより「中間状態が lint clean にならない一連の編集」が deny で stuck する問題が解消される (関連する変更を 1 つの MultiEdit にまとめる必要が無くなる)
  - 編集予測ロジック (`predict-content.py`) を削除
- 編集時の ignore コメント挿入禁止 (`block-ignore-lint-comment.sh`) と編集後の自動フォーマット (`code-format.sh`) は従来通り PreToolUse / PostToolUse で動作する

## 概要

このプラグインは 3 段階のコード品質ガードを提供します。

| タイミング | フック | 役割 |
|------------|--------|------|
| 編集直前 | `block-ignore-lint-comment` (PreToolUse / Write\|Edit\|MultiEdit) | ESLint/Prettier/Ruff の ignore コメント挿入を deny |
| 編集直後 | `code-format` (PostToolUse / Write\|Edit\|MultiEdit) | ESLint `--fix` / Prettier / Ruff で自動整形 |
| `git commit` 直前 | `block-commit-lint` (PreToolUse / Bash) | staged ファイルに lint エラーがあれば commit を deny |

モノレポ構成 (例: `front/`, `server/` 配下にそれぞれ linter 設定がある) でも、対象ファイルから上向きに最寄りの設定ファイルを探索し、その所在ディレクトリを実行 CWD として linter を起動します。

## 対応 linter / formatter

- JavaScript / TypeScript (`.js .jsx .ts .tsx .mjs .cjs`)
  - ESLint (commit 直前 lint、`--fix` 事後修正)
  - Prettier (事後フォーマットのみ、`--write`)
- Python (`.py`)
  - Ruff (commit 直前 lint、`check --fix` と `format` で事後修正)

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=auto-lint-check
```

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

**`git add` / `-a` 同時実行時の挙動**:

本フックは Bash ツールの **実行前** に発火するため、同一コマンドの `git add` がまだ走っていない時点では index が古いまま見える。これを避けるため、コマンド文字列に `git add` / `git stage` / `git commit -a` / `--all` のいずれかを検出した場合は、staged だけでなく working tree の変更 (modified + untracked) も lint 対象に含め、ソースを working tree から読み込みます。

これにより以下のパターンが正しく lint されます:

- `git add path && git commit -m ...`
- `git add -A && git commit -m ...`
- `git add . && git commit -m ...`
- `git commit -am ...`

過検出 (commit に含めない予定の編集まで lint) は許容しています。Claude が commit する状況では作業中ファイルだけが working tree にある運用が一般的で、不要な lint がほとんど発生しないためです。

**検出のスコープ外** (lint をスキップして通す):

- リポジトリ外での実行 (`git rev-parse --show-toplevel` 失敗)
- 対象ファイルが 0 件 (空 commit、`--allow-empty` など)
- 対応する linter 設定ファイルが見つからない
- linter バイナリが見つからない (skip し、警告を stderr に出す)

**Edge case**:

- `git -C dir commit` や `cd /other && git commit` のような cwd を切り替える形式では、本フックは現在の cwd の git を見るため、対象 repo がズレる可能性があります。明示的にプロジェクトルートで commit する運用を推奨します。
- `git add path` で stage 後、その path を working tree でさらに変更してから `git commit` (path に対する `git add` を含まない) を実行した場合、本フックは「working tree 上書き」モードに入らないため staged blob (古い内容) を lint します。実害は少ないですが、認識ズレを避けるため commit 直前に再 stage することを推奨します。

#### 3. code-format

**ファイル**: `hooks/scripts/code-format.sh`
**イベント**: PostToolUse (matcher: `Write|Edit|MultiEdit`)

編集後に対応する formatter / `--fix` を順に実行します。これにより commit 直前 lint で出る format-only エラーをあらかじめ抑制します。

| 言語 | 実行コマンド |
|-----|-------------|
| JS/TS | `eslint --fix <file>` → `prettier --write <file>` |
| Python | `ruff check --fix <file>` → `ruff format <file>` |

各コマンドは個別に成否を吸収し、失敗しても hook 全体は `exit 0` で終了します。

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
| ESLint | `eslint.config.{js,mjs,cjs,ts}`, `.eslintrc.{js,cjs,json,yml,yaml}`, `package.json` の `eslintConfig` |
| Prettier | `.prettierrc*`, `prettier.config.{js,cjs,mjs}`, `package.json` の `prettier` |
| Ruff | `ruff.toml`, `.ruff.toml`, `pyproject.toml` の `[tool.ruff]` セクション |

`.git` ディレクトリ (またはファイル) に到達したら探索を打ち切ります。

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
│       └── lib/
│           ├── common.sh
│           ├── find-config-root.sh
│           └── detect-new-ignores.py
└── README.md
```

## 必要な実行環境

- `bash`
- `jq`
- `git`
- `python3` (block-ignore-lint-comment.sh の差分検出のみ)
- 利用したい linter / formatter (`eslint`, `prettier`, `ruff` または `uvx`)

## 関連情報

- [ESLint ドキュメント](https://eslint.org/docs/)
- [Prettier ドキュメント](https://prettier.io/docs/)
- [Ruff ドキュメント](https://docs.astral.sh/ruff/)
- [Claude Code Hooks ドキュメント](https://docs.anthropic.com/claude-code/hooks)
