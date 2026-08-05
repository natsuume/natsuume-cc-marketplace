# natsuume-writing プラグイン

natsuume の過去執筆物から抽象化した執筆ルールを使い、Markdown の技術記事・技術書について outline、draft、review の 3 フェーズを支援します。文体コアと媒体別プロファイルは `rules/writing-rules.md` を正本とし、Skill が同じファイルを参照します。

## バージョン

v0.6.2

## 構成

| Skill | 役割 |
|---|---|
| `outline` | 壁打ちで構成を確定し、見出しと HTML コメントのスケルトンを作る |
| `draft` | 未執筆セクションを一括で本文化し、未検証事項を TODO として残す |
| `review` | 文体・構成・技術的正確さ・表記の 4 観点で読み取り専用レビューを行う |

`hooks/hooks.json` の SessionStart hook から `rules/core-summary.md` を追加 context として注入します。詳細な執筆規則は各 Skill が必要時に `rules/writing-rules.md` から読み込みます。

## インストール

```bash
claude plugin install natsuume-writing@natsuume-plugins
```

本プラグインは Claude Code 専用で、Codex marketplace では配布していません。
