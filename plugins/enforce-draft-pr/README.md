# enforce-draft-pr プラグイン

`gh pr create` に `--draft` フラグを自動付与する PreToolUse フックプラグインです。「PR は必ず draft で起こし、レビュー後に手動で ready 化する」運用を強制したい場合に使います。

## バージョン

v0.1.0

## 概要

`PreToolUse` (matcher: `Bash`) で `gh pr create` の呼び出しを検知し、`--draft` が付いていなければコマンドを書き換えてフラグを追加します。`updatedInput.command` を返すため、Claude Code は書き換え後のコマンドで実行します。

git-guardrails プラグインの一部として提供されていましたが、責務分離のため独立プラグインに切り出しました。「draft 強制を **使いたくない**」運用を選ぶ場合はこのプラグインを **インストールしない** だけで済みます。

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=enforce-draft-pr
```

## 機能一覧

### Hooks

#### enforce-draft-pr

**ファイル**: `hooks/scripts/enforce-draft-pr.sh`
**イベント**: PreToolUse (matcher: `Bash`)

**動作**:

- `gh pr create` を含むコマンドを検出
- 既に `--draft` フラグがあれば素通し
- 無い場合は `gh pr create` 直後に `--draft` を挿入してコマンドを書き換える

**例**:

```
入力: gh pr create --title "新機能" --body-file body.md
出力: gh pr create --draft --title "新機能" --body-file body.md
```

## 既知の制約

- このプラグインの hook は **コマンドの書き換え** を行います。`gh pr create` を直接実行する用途を想定しており、シェルラッパー (`bash -c "..."`) や複雑なチェーン経由の `gh pr create` には介入しません (検出しない)。
- `gh pr create` の代わりに `gh api` で直接 PR を作成するケースでは介入できません。
- 一度作成された draft PR を ready 化するのはユーザーまたは Claude が `gh pr ready <PR>` を明示実行する必要があります (本プラグインは作成時のみ介入)。

## 関連プラグイン

- [git-guardrails](../git-guardrails/) — デフォルトブランチへの直接 push を禁止する hook + rebase ワークフロー Skill

## ディレクトリ構成

```
enforce-draft-pr/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       └── enforce-draft-pr.sh
└── README.md
```

## 必要な実行環境

- `bash`
- `jq`

## 関連情報

- [Claude Code Hooks ドキュメント](https://docs.anthropic.com/claude-code/hooks)
- [GitHub CLI - gh pr create](https://cli.github.com/manual/gh_pr_create)
