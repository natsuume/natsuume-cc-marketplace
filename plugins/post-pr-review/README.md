# post-pr-review プラグイン

Claude Code 経由で `gh pr create` が成功した直後に、`/code-review:code-review <PR-URL>` の実行を Claude に誘導するプラグインです。

## バージョン

v0.1.0

## 概要

`PostToolUse` フックで `Bash` ツール (`gh pr create`) の実行結果を監視し、出力から PR URL を抽出して `hookSpecificOutput.additionalContext` で次のアシスタント発話に注入します。

`/code-review:code-review` は PR を対象とするため commit 前には実行できないので、`pre-commit-review` (commit 前) と `post-pr-review` (PR 作成後) で役割を分けています。

これは強制ではなく **誘導** であり、Web UI 等からの PR 操作には影響しません。Claude Code 経由の PR 作成時のみ機能します。draft 強制の有無 (姉妹プラグイン [enforce-draft-pr](../enforce-draft-pr/) 併用時のみ draft 化) には依存せず、ready / draft いずれの PR でもレビューを誘導します。

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=post-pr-review
```

## 機能一覧

### Hooks

#### nudge-pr-review

**ファイル**: `hooks/scripts/nudge-pr-review.sh`
**イベント**: PostToolUse (matcher: `Bash`)

`gh pr create` 成功時に PR URL を抽出し、`additionalContext` でレビュー実行を促します。

**動作**:

- 単独実行 (`gh pr create --title "foo"`) と global option 付き (`gh -R owner/repo pr create ...`) を検出
- `tool_response.output` / `tool_response.stdout` から PR URL (`https://github.com/.../pull/<n>`) を抽出 (`tool_response.stderr` は `already exists` 等の関係ない URL が混じり得るため除外)
- URL が見つからない場合 (失敗ケース等) は何も出力しない
- 強制ではなく誘導 (`additionalContext`) のため、コマンド実行自体はブロックしない

`additionalContext` には次のような文言が入ります:

```
PR を作成しました: https://github.com/natsuume/.../pull/<n>

このリポジトリでは PR 作成直後に `/code-review:code-review https://github.com/.../pull/<n>` を実行してコードレビューする運用です。レビューで指摘があれば対応してください。

**注意**: `/code-review:code-review` skill のコメントテンプレートは英語でハードコードされています。本リポジトリは グローバル CLAUDE.md の「やり取りは日本語で行う」方針に従うため、PR へ投稿する直前に下記の対応訳に **すべて翻訳** してから `gh pr comment` してください。"🤖 Generated with [Claude Code]" の Trailer 行はそのまま残します。

  - `### Code review` → `### コードレビュー`
  - `Found N issues:` → `N 件の指摘が見つかりました:`
  - `No issues found. Checked for bugs and CLAUDE.md compliance.` → `指摘なし。バグおよび CLAUDE.md 準拠を確認しました。`
  - `If this code review was useful, please react with 👍. Otherwise, react with 👎.` → `このコードレビューが役に立った場合は 👍、そうでなければ 👎 でリアクションしてください。`
```

**日本語化を post-pr-review で扱う理由**: 公式 `/code-review:code-review` skill の markdown を fork すると保守負債が大きいため、PR 作成→ /code-review 起動の唯一の動線である post-pr-review の nudge で「投稿前に対応訳に翻訳する」指示を一度だけ Claude に渡しています。対応訳を hook と README で同一テキストとして保持することで、Claude が翻訳結果に揺れを生じさせないよう統一しています。

## ワークフロー (pre-commit-review との連携)

```
1. Claude が編集 → /simplify → /codex:review --wait (pre-commit-review が強制し、PostToolUse で両者のマーカーが自動作成される)
2. git commit (pre-commit-review が両マーカーの整合を検証して許可)
3. git push
4. gh pr create ... (姉妹プラグイン enforce-draft-pr 併用時は --draft が自動付与される)
5. PostToolUse: nudge-pr-review.sh が PR URL を抽出
6. 次のアシスタント発話に additionalContext として誘導文が注入される
7. Claude が /code-review:code-review <PR-URL> を実行
8. 指摘があれば修正 → pre-commit-review に戻る
10. (draft 運用の場合のみ) レビュー完了後にユーザーが ready マーク
```

## 既知の制約

- **強制ではなく誘導**: `additionalContext` で次の発話に注入するだけなので、Claude が無視することは原理的に可能です。レビュー完了を ready 化の前提として強制したい場合は、別途 `gh pr ready` をブロックする hook を組む必要があります (本プラグインの責務外)。
- **Claude Code 経由の PR 作成のみ**: Web UI や別環境の CLI で作成された PR には介入できません (これは設計上の意図です)。
- **URL 抽出の単純さ**: `gh pr create` の標準的な出力形式 (`https://github.com/.../pull/<n>` を含むテキスト) を前提にしています。出力フォーマットが変わると抽出に失敗してフックが no-op になります (誤検知ではなく単に誘導が出ないだけ)。

## ディレクトリ構成

```
post-pr-review/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       └── nudge-pr-review.sh
└── README.md
```

## 必要な実行環境

- `bash`
- `jq`

## 関連プラグイン

- [pre-commit-review](../pre-commit-review/) — commit 前に `/codex:review` と `/simplify` を強制
- [enforce-draft-pr](../enforce-draft-pr/) — `gh pr create` 時に `--draft` を自動付与 (draft 運用を採用する場合のみ)
- [git-guardrails](../git-guardrails/) — master ブランチへの直接 push を禁止

## 関連情報

- [Claude Code Hooks ドキュメント](https://docs.anthropic.com/claude-code/hooks)
- [code-review プラグイン (Claude Code 公式)](https://github.com/anthropics/claude-code-plugins)
