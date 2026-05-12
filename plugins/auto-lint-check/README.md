# auto-lint-check プラグイン

ファイル編集前に linter による事前チェックを行い、ignore コメントの挿入をブロックし、編集後に自動フォーマットを適用するプラグインです。

## バージョン

v0.1.1

### v0.1.0 → v0.1.1 の変更点

- 編集前 lint の deny メッセージに「PreToolUse は Edit/Write/MultiEdit 単位で発火するため、中間状態が lint clean にならない一連の編集は MultiEdit でまとめる必要がある」旨の案内を追加

## 概要

このプラグインは Claude Code が `Edit` / `Write` / `MultiEdit` ツールでファイルを変更する際の品質ガードを 3 つ提供します。

- **編集前 lint**: 編集後の予測内容を ESLint / Ruff の stdin に流し、エラーがあればツール実行を `deny` する
- **ignore コメントの新規挿入を禁止**: `// eslint-disable`, `// prettier-ignore`, `# noqa`, `# ruff: noqa` 等を含む変更を `deny` する
- **編集後 auto-format**: 対応する formatter / `linter --fix` を実行してコードを自動整形する

モノレポ構成 (例: `front/`, `server/` 配下にそれぞれ linter 設定がある) でも、編集対象ファイルから上向きに最寄りの設定ファイルを探索し、その所在ディレクトリを実行 CWD として linter を起動します。

## 対応 linter / formatter

- JavaScript / TypeScript (`.js .jsx .ts .tsx .mjs .cjs`)
  - ESLint (`--stdin` で事前チェック、`--fix` で事後修正)
  - Prettier (事後フォーマットのみ、`--write`)
- Python (`.py`)
  - Ruff (`check --stdin-filename` で事前チェック、`check --fix` と `format` で事後修正)

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=auto-lint-check
```

## 機能一覧

### Hooks

#### 1. auto-lint-check

**ファイル**: `hooks/scripts/auto-lint-check.sh`
**イベント**: PreToolUse (matcher: `Write|Edit|MultiEdit`)

編集後の予測内容を構築し、対応する linter に stdin 経由で渡してチェックします。エラーが出たらツール実行を `deny` し、`permissionDecisionReason` に linter の出力を含めます。

**動作**:

- `Write` → `tool_input.content` をそのまま linter に流す
- `Edit` → 実ファイルを読み、`old_string` → `new_string` を 1 回 (`replace_all: true` のときは全置換) 適用した結果を流す
- `MultiEdit` → 実ファイルを読み、`edits[]` を順に適用した結果を流す

設定ファイルが見つからなかったり linter バイナリが利用できない場合は何もせずスキップします (false positive 抑制)。

**編集単位の制約**:

PreToolUse は Edit/Write/MultiEdit ごとに発火し、その都度「適用後の予測内容」に対して lint します。そのため一連の編集が最終的に lint clean になるとしても、**各 tool 呼び出し時点での予測内容が lint clean** でなければ deny されます。例えば「新規 import を追加する Edit」と「import を使用する箇所を追加する Edit」を分けて呼ぶと、1 つ目の Edit が未使用 import 検出で deny され、2 つ目の Edit に進めず stuck します。このような場合は関連する変更を 1 つの `MultiEdit` にまとめ、すべての変更を適用した予測内容に対して lint が通るようにしてください。deny メッセージにも同様の案内を含めています。

#### 2. block-ignore-lint-comment

**ファイル**: `hooks/scripts/block-ignore-lint-comment.sh`
**イベント**: PreToolUse (matcher: `Write|Edit|MultiEdit`)

新規挿入される内容に下記の ignore コメントが含まれていたらツール実行を `deny` します。

| linter / formatter | 検出パターン (抜粋) |
|--------------------|------------------|
| ESLint | `// eslint-disable`, `// eslint-disable-next-line`, `/* eslint-disable */`, `// eslint-enable` |
| Prettier | `// prettier-ignore`, `/* prettier-ignore */`, `<!-- prettier-ignore -->` |
| Ruff | `# noqa`, `# noqa: E501`, `# ruff: noqa`, `# fmt: off` / `# fmt: on` / `# fmt: skip` |

例外的にどうしても必要なときは、ユーザー側で hook を一時的に無効化してください。

#### 3. code-format

**ファイル**: `hooks/scripts/code-format.sh`
**イベント**: PostToolUse (matcher: `Write|Edit|MultiEdit`)

編集後に対応する formatter / `--fix` を順に実行します。

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

編集対象ファイルから上向きに以下の設定ファイル/フィールドを探索し、最初に見つかったディレクトリを実行 CWD にします。

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
│       ├── auto-lint-check.sh
│       ├── block-ignore-lint-comment.sh
│       ├── code-format.sh
│       └── lib/
│           ├── common.sh
│           ├── find-config-root.sh
│           └── predict-content.py
└── README.md
```

## 必要な実行環境

- `bash`
- `jq`
- `python3` (auto-lint-check.sh の編集後予測のみ)
- 利用したい linter / formatter (`eslint`, `prettier`, `ruff` または `uvx`)

## 関連情報

- [ESLint ドキュメント](https://eslint.org/docs/)
- [Prettier ドキュメント](https://prettier.io/docs/)
- [Ruff ドキュメント](https://docs.astral.sh/ruff/)
- [Claude Code Hooks ドキュメント](https://docs.anthropic.com/claude-code/hooks)
