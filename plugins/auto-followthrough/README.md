# auto-followthrough プラグイン

Claude Code が **auto mode** で動作している間、変更の `commit` → `gh pr create` → PR マージ完了までを停止せずに遂行 (follow through) するよう、Claude にコンテキストを注入するプラグインです。

## バージョン

v0.1.0

## 概要

`UserPromptSubmit` と `PostToolBatch` の 2 つのフックで、入力 JSON の `permission_mode` が `"auto"` のときだけ `hookSpecificOutput.additionalContext` を注入します。それ以外のモード (`default` / `plan` / `acceptEdits` / `bypassPermissions`) では何も出力しません。

注入する内容は「変更が一段落したらユーザに確認を求めず、commit → push → PR 作成 → マージまで一気に進めて良い」という方針です。auto mode のときに Claude が「変更を書いて停止」してしまうのを防ぎ、PR マージ完了まで届ける運用にロックします。

ただし auto mode でも以下は引き続き禁止 / 要確認である旨を明記します:

- master / 既定ブランチへの直接 push、master 上での直接コミット
- force push / 履歴改変 / 共有データ削除等の破壊的操作
- 秘匿情報を含むファイルのコミット

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=auto-followthrough
```

## 機能一覧

### Hooks

#### inject-auto-context

**ファイル**: `hooks/scripts/inject-auto-context.sh`
**イベント**: `UserPromptSubmit`, `PostToolBatch`

**動作**:

- 入力 JSON から `permission_mode` を読み取り、`"auto"` のときのみ `additionalContext` を出力する
- それ以外のモードでは無音で `exit 0`
- `hook_event_name` を入力からそのまま読み取り、`hookSpecificOutput.hookEventName` に同じ値を設定する (UserPromptSubmit / PostToolBatch どちらの呼び出しでも同一スクリプトで処理可能)
- `jq` が無い環境では何もせず終了する (フェイルセーフ)

**なぜ 2 つのフックが必要か**:

- `UserPromptSubmit` — 新しいユーザ入力ごとにモードを再確認して方針を再注入する。ユーザが auto を on/off したタイミングを取り逃がさない。
- `PostToolBatch` — 1 ターンのツール呼び出しが**全て完了した直後**に発火する。ここでもう一度方針を注入することで、編集だけして「完了」と打ち切るのを抑止し、commit / PR / マージへの遷移を促す。

`PostToolUse` ではなく `PostToolBatch` を採用しているのは、ツール 1 件ごとに毎回介入するとノイズになるためです。`PostToolBatch` はバッチ末尾で 1 回だけ発火するので、自然な「区切り」のフックになります。

## ディレクトリ構成

```
auto-followthrough/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       └── inject-auto-context.sh
└── README.md
```

## 必要な実行環境

- `bash`
- `jq`

## 関連プラグイン

- [git-guardrails](../git-guardrails/) — master への直接 push を禁止する。auto mode 中の暴走を構造的に止める安全網として併用推奨
- [pre-commit-review](../pre-commit-review/) — commit 前にレビューループを強制。auto で commit に進むときも本プラグインの動作と矛盾せず、レビュー手順は引き続き機能する
- [post-pr-review](../post-pr-review/) — PR 作成直後に adversarial review を促す。auto mode 中も本プラグインの誘導と直交して動作する
- [update-default-branch](../update-default-branch/) — マージ完了後のデフォルトブランチ最新化を支援する Skill

## 既知の制約

- **強制ではなく誘導**: `additionalContext` を次のターンの先頭に追加するだけなので、Claude が指示を無視することは原理的に可能です。確実に止めたいケースは別途 deny 判定の hook を組む必要があります。
- **`permission_mode` の値が `"auto"` リテラルであること前提**: Claude Code 側の仕様変更で値が変わると無音になります。その場合は無効化されるだけで誤動作はしません。
- **PostToolBatch の入力 schema 依存**: `PostToolBatch` 入力に `permission_mode` が含まれない実装の場合、こちらは無音になります。`UserPromptSubmit` 経路は引き続き機能します。

## 関連情報

- [Claude Code Hooks ドキュメント](https://docs.anthropic.com/claude-code/hooks)
