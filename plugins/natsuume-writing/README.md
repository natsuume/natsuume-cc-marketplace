# natsuume-writing プラグイン

執筆ルールを 2 層で提供します。

| 層 | ルールファイル | 適用対象 |
|---|---|---|
| 文章作成一般のルール | `rules/general-writing.md` | 成果物として書く日本語の文章すべて (ドキュメント・README・コードコメント・PR や issue の本文・コミットメッセージ・メール・記事など)。チャットでの応答は対象外 |
| natsuume 名義の技術文書のルール | `rules/writing-rules.md` | natsuume 名義の技術文書 (テックブログ・技術書) の地の文。文章作成一般のルールを前提とし、natsuume の過去執筆物から抽象化した文体コアと媒体別プロファイルを加える |

文章作成一般のルールは、生成 AI の普及前後の技術記事の比較から得た傾向 (論理を整えて見せる型・読解体験の過剰設計・太字や直訳的な言い回しの多用) を、文体や分野に依存しない形で定めたものです。文体・人称・表記の基準は定めず、既存文書の書式やプロジェクトの規約を優先します。

Markdown の技術記事・技術書については、outline、draft、review の 3 フェーズを Skill で支援します。review は技術文書以外の文章も扱い、その場合は文章作成一般のルールで判定します。

文書ファイル (Markdown やプレーンテキストのドキュメント・README・記事など) と PR・issue の本文を書いたときは、完了とする前に review を 1 回実行し、指摘を反映するよう文章作成一般のルールで定めています。PR・issue の本文は投稿前の下書きをファイルに書き出してレビューします。コードコメントとコミットメッセージは対象外です。

## バージョン

v0.11.0

## 構成

| Skill | 役割 |
|---|---|
| `outline` | 壁打ちで構成を確定し、見出しと HTML コメントのスケルトンを作る |
| `draft` | 未執筆セクションを一括で本文化し、未検証事項を TODO として残す |
| `review` | 文体・構成・技術的/事実の正確さ・表記の 4 観点で読み取り専用レビューを行う。技術文書以外の文章も対象にする |

`rules/expression-watchlist.md` は、生成 AI の普及後に増えた直訳調・比喩的な語と抽象的な漢語・評価語を見直し候補として列挙した参照ファイルです。技術記事での増え方と、人間の文章 (普及前の技術記事と小説) での使われ方から、語を 4 区分 (強く抑制 / 適度に抑制 / 物語では自然 / 技術文書では自然) に分け、区分ごとに扱いを変えています。文章作成一般のルールから参照し、draft と review も読み込みます。強く抑制する語は SessionStart で注入する要点にも載せています。

`hooks/hooks.json` の SessionStart hook から、2 層の要点をまとめた `rules/core-summary.md` を追加 context として注入します。注入本文の末尾には `rules/` ディレクトリの絶対パスを `(参照パス)` 行として付け足し、Skill の外でまとまった文章を書くときにも `general-writing.md` と `expression-watchlist.md` の全文を読めるようにしています。技術文書の詳細な執筆規則は、各 Skill が必要時に `rules/writing-rules.md` から読み込みます。

## インストール

```bash
claude plugin install natsuume-writing@natsuume-plugins
```

本プラグインは Claude Code 専用で、Codex marketplace では配布していません。

各 Skill の SKILL.md はパス参照に `${CLAUDE_PLUGIN_ROOT}` を使うため、plugin 経由でのインストールでの利用のみをサポートします。SKILL.md を plugin 外 (`~/.claude/skills/` 等) へコピーした場合、`${CLAUDE_PLUGIN_ROOT}` は置換されません。
