# post-pr-review プラグイン

Claude Code 経由で `gh pr create` が成功した直後に、`/codex:adversarial-review --wait --scope branch` の実行を Claude に誘導するプラグインです。

## バージョン

v0.2.0

## 概要

`PostToolUse` フックで `Bash` ツール (`gh pr create`) の実行結果を監視し、出力から PR URL を抽出して `hookSpecificOutput.additionalContext` で次のアシスタント発話に注入します。

`/codex:adversarial-review` は実装方針や設計選択そのものを **批判的にレビュー** するコマンドで、PR 作成直後 (= 1 ブランチ分の変更がまとまったタイミング) に最も活きます。read-only のコードレビューではなく、「採用しているアプローチ自体が妥当か」を問い直す視点を取り入れる位置づけです。表層的な実装レビューは `pre-commit-review` プラグインが commit 前に強制する `/codex:review` が担当しているため、本プラグインは **設計レベルの challenge** に役割を絞っています。

これは強制ではなく **誘導** であり、Web UI 等からの PR 操作には影響しません。Claude Code 経由の PR 作成時のみ機能します。draft 強制の有無 (姉妹プラグイン [enforce-draft-pr](../enforce-draft-pr/) 併用時のみ draft 化) には依存せず、ready / draft いずれの PR でもレビューを誘導します。

### v0.1.0 → v0.2.0 の変更点

- 誘導先を `/code-review:code-review <PR-URL>` から `/codex:adversarial-review --wait --scope branch` へ変更しました。本プラグインの責務は「PR 作成というタイミング」を捉えて adversarial レビューを差し込むことに絞られ、実装表層のコメント投稿系レビューは `pre-commit-review` の commit 前ループ (`/codex:review`) に任せる運用になります。
- これに伴い、旧版で nudge に含めていた「英語コメントテンプレートを日本語訳してから `gh pr comment` する」指示は削除しました。`/codex:adversarial-review` は対話 stdout でレビューを返すコマンドで、PR コメントを直接投稿しないため翻訳ステップは不要です。
- `/codex:adversarial-review` を Skill tool から起動するには姉妹プラグイン [codex-review-customize](../codex-review-customize/) v0.2.0 以降のパッチが必要になります。未適用の場合は会話入力としての `/codex:adversarial-review` を実行してください (本プラグインの誘導文にもその旨を明示しています)。

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=post-pr-review
```

## 機能一覧

### Hooks

#### nudge-pr-review

**ファイル**: `hooks/scripts/nudge-pr-review.sh`
**イベント**: PostToolUse (matcher: `Bash`)

`gh pr create` 成功時に PR URL を抽出し、`additionalContext` で `/codex:adversarial-review --wait --scope branch` の実行を促します。PR URL はメッセージの文脈情報として残しますが、`/codex:adversarial-review` 自体は git state を見るコマンドのため引数には渡しません (引数は `--wait --scope branch` で固定方針)。

**動作**:

- 単独実行 (`gh pr create --title "foo"`) と global option 付き (`gh -R owner/repo pr create ...`) を検出
- `tool_response.output` / `tool_response.stdout` から PR URL (`https://github.com/.../pull/<n>`) を抽出 (`tool_response.stderr` は `already exists` 等の関係ない URL が混じり得るため除外)
- URL が見つからない場合 (失敗ケース等) は何も出力しない
- 強制ではなく誘導 (`additionalContext`) のため、コマンド実行自体はブロックしない

`additionalContext` には次のような文言が入ります:

```
PR を作成しました: https://github.com/natsuume/.../pull/<n>

このリポジトリでは PR 作成直後に `/codex:adversarial-review --wait --scope branch` を Skill tool で実行し、現在のブランチに対する **批判的レビュー** (実装方針・設計選択・トレードオフ・前提条件への challenge) を取得する運用です。read-only のコードレビューではなく、「採用しているアプローチ自体が妥当か」を問い直すレビューです。

レビュー結果に従って:
  - 設計方針や実装アプローチに対する根本的な指摘があれば、修正方針を検討して必要なら作業ブランチに反映する
  - 影響が大きい指摘 (アーキテクチャレベルの再考が必要等) は人間判断を仰ぐ
  - 表層的な実装細部の指摘は `/codex:review` で別途確認する

`/codex:adversarial-review` は frontmatter で `disable-model-invocation: true` が指定されているため、Skill tool から呼び出すには姉妹プラグイン `codex-review-customize` の `/codex-review-customize:setup` でパッチを適用しておく必要があります。未適用の場合は会話入力としての `/codex:adversarial-review --wait --scope branch` を実行してください。
```

## ワークフロー (pre-commit-review との連携)

```
1. Claude が編集 → /simplify → /codex:review --wait (pre-commit-review が強制し、PostToolUse で両者のマーカーが自動作成される)
2. git commit (pre-commit-review が両マーカーの整合を検証して許可)
3. git push
4. gh pr create ... (姉妹プラグイン enforce-draft-pr 併用時は --draft が自動付与される)
5. PostToolUse: nudge-pr-review.sh が PR URL を抽出
6. 次のアシスタント発話に additionalContext として誘導文が注入される
7. Claude が /codex:adversarial-review --wait --scope branch を実行 (実装方針・設計選択への challenge)
8. 大きな方針転換が必要な指摘があれば修正 → pre-commit-review に戻る (commit 前ループ)
9. (draft 運用の場合のみ) レビュー完了後にユーザーが ready マーク
```

## 既知の制約

- **強制ではなく誘導**: `additionalContext` で次の発話に注入するだけなので、Claude が無視することは原理的に可能です。レビュー完了を ready 化の前提として強制したい場合は、別途 `gh pr ready` をブロックする hook を組む必要があります (本プラグインの責務外)。
- **Claude Code 経由の PR 作成のみ**: Web UI や別環境の CLI で作成された PR には介入できません (これは設計上の意図です)。
- **URL 抽出の単純さ**: `gh pr create` の標準的な出力形式 (`https://github.com/.../pull/<n>` を含むテキスト) を前提にしています。出力フォーマットが変わると抽出に失敗してフックが no-op になります (誤検知ではなく単に誘導が出ないだけ)。
- **Skill tool 起動の前提**: `/codex:adversarial-review` を Skill tool から呼び出すには [codex-review-customize](../codex-review-customize/) v0.2.0 以降の `/codex-review-customize:setup` を実行しておく必要があります。未適用環境では Claude が会話入力モード (`/codex:adversarial-review --wait --scope branch`) で起動する形になります。

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

- [pre-commit-review](../pre-commit-review/) — commit 前に `/codex:review` と `/simplify` を強制。ループ閾値到達時は `/codex:adversarial-review` を促す動線も持つ
- [codex-review-customize](../codex-review-customize/) — `/codex:adversarial-review` を Skill tool から呼べるようにパッチを当てる setup プラグイン
- [enforce-draft-pr](../enforce-draft-pr/) — `gh pr create` 時に `--draft` を自動付与 (draft 運用を採用する場合のみ)
- [git-guardrails](../git-guardrails/) — master ブランチへの直接 push を禁止

## 関連情報

- [Claude Code Hooks ドキュメント](https://docs.anthropic.com/claude-code/hooks)
