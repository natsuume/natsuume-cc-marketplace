# natsuume-writing プラグイン

natsuume の過去執筆物から抽象化した執筆ルールを使い、Markdown の技術記事・技術書について outline、draft、review の 3 フェーズを支援します。文体コアと媒体別プロファイルは `rules/writing-rules.md` を正本とし、Claude Code と Codex の Skill が同じファイルを参照します。

## バージョン

v0.5.1

### Codex v0.5.1 → v0.5.2 の変更点

- Codex manifest を `codex/hooks.json` へ分離し、GPT-5.6 Sol / Luna 向けの compact な SessionStart prompt を追加した
- Codex prompt は Goal / Context / Boundaries / Done when を先に示し、事実・筆者の検証・推測の断定度、文体、表記を Codex native な指示として整理した
- Claude Code の slash command 表記を Codex prompt へ流用せず、`$natsuume-writing:outline` / `$natsuume-writing:draft` / `$natsuume-writing:review` として案内する。Claude Code の prompt・version は変更せず、Codex version だけを patch bump した

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

Claude Code は `hooks/hooks.json` の SessionStart hook から `rules/core-summary.md` を追加 context として注入します。Codex は `codex/hooks.json` から `codex/prompts/session.md` を developer context として注入し、GPT-5.6 Sol / Luna に共通する Goal / Context / Boundaries / Done when と Codex Skill 名を明示します。どちらも詳細な執筆規則は各 Skill が必要時に `rules/writing-rules.md` から読み込み、文体ルールの正本は共有します。

Codex prompt は執筆と無関係な作業には適用せず、章立て・本文生成・レビューを依頼された場合だけ対応する `$natsuume-writing:*` Skill の詳細手順へ接続します。これにより、Claude Code 固有の slash command や質問 tool 名を Codex の常時 context へ混在させません。

## インストール

Claude Code:

```bash
claude plugin install natsuume-writing@natsuume-plugins
```

Codex:

```bash
codex plugin add natsuume-writing@natsuume-plugins
```

## 関連情報

- [Codex Prompting](https://learn.chatgpt.com/docs/prompting)
- [Codex Hooks](https://learn.chatgpt.com/docs/hooks)
- [Codex Models](https://learn.chatgpt.com/docs/models)
