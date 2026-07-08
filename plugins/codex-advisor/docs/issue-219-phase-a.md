# codex-advisor Phase A 設計契約 (issue #219)

本ファイルは TDD 2 段階の Phase A (設計記述 commit) として、Phase B が実装すべき契約を固定する。
**Phase B 完了時に本ファイルは削除する** (設計の正本は issue #219 body)。

## 移植対象

Anthropic Advisor tool (https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool) のパターン移植。
API 機能 (`advisor_20260301`) は advisor が Claude モデル限定のため使わず、以下 3 点を Claude Code plugin として再構成する:

1. executor (Claude メインセッション) / advisor (Codex) の分離 — advisor は助言のみ、実行しない
2. 呼び出しタイミングの規律 (公式 timing block の移植、SessionStart 常時注入)
3. 助言の重み付けと reconcile call (衝突時は黙って従わず/無視せず、衝突を明示して再相談)

## 確定済み設計判断 (ユーザ承認済み、issue #219 参照)

- 誘導強度: 常時注入 + 手動起動。hard rule (「最初の write 前に必須」等) は入れない
- 呼び出し経路: 公式 codex plugin の `codex-companion.mjs task` サブコマンド (foreground)
- reasoning effort: `--effort xhigh` を wrapper にハードコード (上書きフラグなし)
- model: 未指定 (Codex 既定)。`--write` は付けない (read-only sandbox 固定)
- plugin 名: codex-advisor、version 0.1.0

## Phase B が実装するファイル構成

```
plugins/codex-advisor/
├── .claude-plugin/plugin.json         # 本 commit で作成済み (0.1.0)
├── README.md                          # 概要・依存・機構説明・トラブルシュート
├── hooks/
│   ├── hooks.json                     # SessionStart → inject-advisor-rules.sh
│   ├── prompts/advisor-rules.md       # 注入本文。rule ID: advisor-timing / advisor-weight / advisor-boundary
│   └── scripts/inject-advisor-rules.sh # #!/bin/sh、jq で additionalContext 出力、jq 不在は fail-open
├── skills/
│   └── consult/SKILL.md               # user-invocable: true。相談プロンプト組み立て + wrapper 起動手順
└── scripts/
    ├── run-codex-advisor.sh           # wrapper 本体 (I/O 契約は下記)
    └── lib/codex-companion-resolver.sh # pre-push-review 由来のパス解決ロジックのコピー
```

## rule ID 一覧 (advisor-rules.md)

| rule ID | 内容 |
|---|---|
| `rule:advisor-timing` | いつ相談するか: (a) 実質的な作業前 (オリエンテーションは含まない) (b) 完了宣言前 (成果物を durable にしてから) (c) 行き詰まり (d) 方針転換の検討時。境界: 短い反応的タスクは相談不要 |
| `rule:advisor-weight` | 助言は重く扱う。一次証拠と矛盾したら適応してよいが self-test 通過は反証にならない。証拠と助言の衝突は reconcile call で明示的に解消する |
| `rule:advisor-boundary` | 設計/仕様の決定はユーザ専権 (助言は AskUserQuestion の代替でない)。レビュー用途は pre-push-review が担当。advisor 不通時は相談なしで続行しユーザ報告 |

## wrapper (run-codex-advisor.sh) I/O 契約

- 入力: 相談プロンプトを piped stdin でのみ受け取る。TTY / 空はき usage + exit 1。引数フラグなし
- 処理: resolver で companion 解決 → `node "$COMPANION" task --effort xhigh` を foreground 実行 (stdin 継承)。`--write` / `--model` / `--background` / `--resume-last` は使わない
- 出力: stdout = Codex 助言テキストそのまま、stderr = wrapper 状態メッセージ (分離は run-codex-review.sh と同一設計)
- 終了コード: companion 正常終了 0 / それ以外は fail() で人間可読メッセージ + 1
- `#!/bin/bash` + `set -e`。Linux (WSL2) / macOS 両対応 (`sort -V` 禁止)
- git repo 外でも動く (相談は git 状態非依存、dirty tree 検査なし)

## 異常系 (確定、issue #219 の表と同一)

- companion 不在 → fail、stderr に `claude plugin install codex@openai-codex` 誘導
- codex CLI 未インストール / 未認証 → fail、stderr に `/codex:setup` 誘導
- 空プロンプト → usage + exit 1
- Bash timeout (10 分、skill が timeout 600000 を明示指定) 超過 → Claude は相談なしで続行 + 報告
- node 不在 → fail (Node.js 必要と明示)

## 受入基準 (Phase B 完了条件)

issue #219 の受入基準チェックリストに従う (bash -n / 両 OS 規約 / 注入 JSON 妥当性 /
stdin なし fail / resolver 実環境解決 / エンドツーエンド相談 1 件成功 / version 同期 0.1.0 /
プロンプトガイド準拠)。加えて本ファイルの削除と marketplace.json / README.md への登録を行う。
