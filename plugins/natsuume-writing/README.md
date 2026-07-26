# natsuume-writing プラグイン

natsuume の過去執筆物から抽象化した執筆ルールを使い、Markdown の技術記事・技術書について outline、draft、review の 3 フェーズを支援します。文体コアと媒体別プロファイルは `rules/writing-rules.md` を正本とし、Skill が同じファイルを参照します。

## バージョン

v0.6.1

### v0.6.0 → v0.6.1 の変更点

draft skill の一括生成手順に、セクションごとの分量をコメントの指示量に比例させる較正 (目安: 論点 1 つにつき 1〜2 段落) を追加した。

### v0.5.1 → v0.6.0 の変更点

Codex 配布対応 (marketplace 移植) を廃止した。`codex/` 配下の manifest・prompt・inject script を削除し、Claude Code 版の SessionStart hook と 3 Skills は無変更。

### v0.5.0 → v0.5.1 の変更点

- macOS の一時ディレクトリで正規化前後のパス表記が異なる場合も、Codex marketplace の同期検証が正しく動作するようにした

### v0.4.2 → v0.5.0 の変更点

- Codex plugin manifest を追加し、3 Skills と SessionStart hook を Codex marketplace からも配布できるようにした
- Skill の plugin root 解決を SKILL.md の実パス基準に変更し、outline 呼出・ユーザー質問・独立調査 subagent の surface 差分を Claude Code / Codex 共通手順に明示した

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
