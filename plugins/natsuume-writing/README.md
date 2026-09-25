# natsuume-writing プラグイン

natsuume の過去執筆物から抽象化した執筆ルールを使い、Markdown の技術記事・技術書について outline、draft、review の 3 フェーズを支援します。文体コアと媒体別プロファイルは `rules/writing-rules.md` を正本とし、Skill が同じファイルを参照します。

## バージョン

v0.8.3

## 構成

| Skill | 役割 |
|---|---|
| `outline` | 壁打ちで構成を確定し、見出しと HTML コメントのスケルトンを作る |
| `draft` | 未執筆セクションを一括で本文化し、未検証事項を TODO として残す |
| `review` | 文体・構成・技術的正確さ・表記の 4 観点で読み取り専用レビューを行う |

`rules/expression-watchlist.md` は、生成 AI の普及後の技術記事で増えた直訳調・比喩的な語と抽象的な漢語・評価語を見直し候補として列挙した参照ファイルで、draft と review が読み込みます。

`hooks/hooks.json` の SessionStart hook から `rules/core-summary.md` を追加 context として注入します。詳細な執筆規則は各 Skill が必要時に `rules/writing-rules.md` から読み込みます。

## インストール

```bash
claude plugin install natsuume-writing@natsuume-plugins
```

本プラグインは Claude Code 専用で、Codex marketplace では配布していません。

各 Skill の SKILL.md はパス参照に `${CLAUDE_PLUGIN_ROOT}` を使うため、plugin 経由でのインストールでの利用のみをサポートします。SKILL.md を plugin 外 (`~/.claude/skills/` 等) へコピーした場合、`${CLAUDE_PLUGIN_ROOT}` は置換されません。
