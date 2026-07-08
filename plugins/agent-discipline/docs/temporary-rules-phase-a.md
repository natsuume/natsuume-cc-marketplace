# 設計契約: 暫定ルール (temporary rules) の分離配送機構 — Phase A

このファイルは TDD 2 段階 (rule:tdd-two-phase) の Phase A 設計記述であり、Phase B (実装本体)
のマージ時に削除する。配送対象の md に to-be-deleted コメントを混入させないための docs/ 隔離
(PR #204 の指摘に基づく運用)。

## 背景と目的

AskUserQuestion の preview 機能に「表示内容がスクロールできず、一定行数以上が
『hidden XX lines』で隠される」問題がある。問題が Claude Code 側で修正されるまでの
**暫定対応**として「preview を使わず、AskUserQuestion の前にテキスト応答で内容を説明する」
ルールを配送したい。

暫定ルールは恒久規律 (always-*.md / discipline-*.md) と性質が異なり、
**問題修正後にいつでも外せること**が第一要件である。既存ファイルへの追記は以下の理由で採らない
(ユーザ decision 2026-07-08、案 1a を採用):

- always-fable.md / always-sonnet.md は lint-prompt-sync.sh (チェック 1) が rule ID セットの
  完全一致を強制しており、追記・撤去とも 2 ファイル同時編集が必要になる
- inject-always.sh の連結ペイロードには「分業規律ブロックを additionalContext の末尾に置く」
  制約があり (self-gate の無視射程が「見出し〜メッセージ末尾」で定義されているため)、
  挿入位置の調整と one-shot 補正 (resolve-model-on-prompt.sh) の同修正が必要になる

## 確定済みユーザ decision (2026-07-08)

1. **配送方式**: 案 1a — 独立 SessionStart hook (inject-temporary.sh) が
   `hooks/prompts/temporary/` 配下の `*.md` を連結して注入する汎用機構
2. **preview の適用範囲**: 全面不使用 (行数に依らず preview を一切使わない)

## ファイル構成 (Phase B 完成形)

```
plugins/agent-discipline/
├── hooks/
│   ├── hooks.json                  # SessionStart に inject-temporary.sh entry を追加
│   ├── prompts/
│   │   └── temporary/              # 暫定ルール置き場 (新設)
│   │       └── askuserquestion-preview-workaround.md
│   └── scripts/
│       └── inject-temporary.sh     # 本 Phase A で契約ヘッダ + no-op 骨格を新設
└── docs/
    └── temporary-rules-phase-a.md  # 本ファイル (Phase B で削除)
```

## inject-temporary.sh の I/O 契約

契約全文は `hooks/scripts/inject-temporary.sh` のヘッダコメントに記載する
(inject-always.sh と同じ様式)。要点:

- stdin: hook input JSON。`hook_event_name` のみ使用 (モデル判定は行わない —
  暫定ルールはモデル・permission_mode に依らず全セッション共通で配送する)
- 注入対象: `hooks/prompts/temporary/*.md` をファイル名の辞書順 (`LC_ALL=C` で固定) に連結
- 出力: inject-always.sh と同形の `{"hookSpecificOutput": {"hookEventName", "additionalContext"}}`
- fail-open 条件: jq 不在 / stdin 不正 / hook_event_name 空 / temporary ディレクトリ不在 /
  `*.md` が 0 件 / 連結結果が空。いずれも無音終了 (exit 0、出力なし)
- **撤去手順が成立する根拠**: temporary/ 配下の md を削除するだけで注入が消える
  (スクリプトと hooks.json entry は残っても no-op)。完全撤去する場合のみ
  entry・スクリプト・temporary ディレクトリを削除する。いずれの場合も version bump は必要
- Linux (WSL2) / macOS 両対応: bash + POSIX 準拠のツール使用のみ (プロジェクト CLAUDE.md 要件)

## hooks.json の変更契約

- `hooks.SessionStart[0].hooks[]` に `${CLAUDE_PLUGIN_ROOT}/hooks/scripts/inject-temporary.sh`
  の command entry を **inject-always.sh の後ろに** 1 件追加する (別 hook = 別メッセージとして
  注入されるため、inject-always.sh 側の self-gate 射程「見出し〜メッセージ末尾」には影響しない)
- description に暫定ルール配送経路の 1 文を追記する
- PreToolUse 側は無変更 (lint-prompt-sync.sh チェック 2 の前提検証は
  `.hooks.PreToolUse[0].hooks[]` の type:agent entry 数のみを数えるため影響なし)

## 暫定ルール md の内容契約 (askuserquestion-preview-workaround.md)

- 冒頭 HTML コメント: 暫定である旨・撤去条件 (Claude Code 側で preview のスクロール問題が
  修正されたら本ファイルを削除する)・撤去手順を記載する (sh スクリプトのコメントと同様、
  ここは配送されないメタ情報として許容する — 配送対象はコメントを除く本文だが、HTML コメントは
  additionalContext にそのまま含まれるため、撤去条件の明示自体が受信側 Claude にも有益な情報
  として意図的に残す)
- 本文: 恒久ルールと同じ「なぜ + 指示 + 境界」形式で 1 ルールのみ:
  - なぜ: preview がスクロールできず「hidden XX lines」で隠れ、ユーザが全文を確認できない
  - 指示: AskUserQuestion で選択肢の `preview` フィールドを使わない (行数に依らず全面不使用)。
    比較に必要な内容 (コード案・mockup・設定例等) は AskUserQuestion 発行前のテキスト応答で
    説明し、そのうえで preview 無しの AskUserQuestion で質問する
  - 境界: 例外なし。内容が長大な場合も preview ではなく応答本文の code block で提示する
- rule ID マーカー (`<!-- rule:... -->`) は付与しない (lint の同期チェック対象は
  always-*.md / discipline-*.md のみであり、暫定ルールを恒久ルールの ID 体系に混ぜない)

## 受入基準 (Phase B)

1. temporary md が 1 件ある状態で inject-temporary.sh に SessionStart 相当の JSON を与えると、
   md 本文を含む正しい JSON が出力される
2. temporary ディレクトリを空にする / 削除すると出力なし (exit 0) になる
3. jq 不在・不正 stdin で出力なし (exit 0)
4. `bash scripts/lint-prompt-sync.sh` (リポジトリルートから) が引き続き全チェック pass
5. version bump: 0.11.0 → 0.12.0 (minor、後方互換のある機能追加) を
   plugin.json / marketplace.json / リポジトリ README の 3 箇所で同期
6. 本ファイル (docs/temporary-rules-phase-a.md) を Phase B で削除
