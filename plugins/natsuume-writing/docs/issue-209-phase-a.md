# issue #209 Phase A 設計契約 (Phase B で本ファイルを削除する)

`/natsuume-writing:draft` skill の設計記述。受入基準・境界/異常系の正は issue #209 body であり、
本ファイルは実装用のファイル構成・SKILL.md の章立て・文言契約の展開のみを持つ。

## 1. 変更ファイル

```
plugins/natsuume-writing/
├── skills/draft/SKILL.md          # 新設 (本体)
├── rules/core-summary.md          # 「詳細ルールへの案内」に /natsuume-writing:draft を追記 (review は未提供の明示を維持)
└── .claude-plugin/plugin.json     # 0.2.0 → 0.3.0 (minor bump)
.claude-plugin/marketplace.json    # version 同期 + description を「outline / draft を提供。review は今後追加予定 (#210)」へ
README.md                          # version 同期 + Skills 表に draft 行追加 (完全修飾形 /natsuume-writing:draft) + 提供範囲の段落更新
```

## 2. SKILL.md の frontmatter 契約

```yaml
name: draft
description: スケルトン付き記事ファイル (outline skill の成果物) から、執筆ルールに準拠したたたき台を一括生成する。未検証事項は TODO コメントで明示する
user-invocable: true
when_to_use: |
  ユーザーが以下のようなリクエストをした場合に使用:
  - 「たたき台を書いて」「本文を生成して」
  - 「スケルトンから記事を書いて」
  - 「ドラフトを作成する」
```

## 3. SKILL.md 本文の章立て

issue #209 受入基準をそのまま手順化する:

1. **入力の確認と前提解決**
   - 対象ファイルを読み、outline コメント (`<!-- outline: 媒体=... / 記事タイプ=... / 想定読者=... -->`) を探す
   - 境界: outline コメントが無い → AskUserQuestion で媒体・記事タイプを確認し、**確認結果を outline コメントとしてファイル先頭に追記してから**生成する (次回以降の draft / review が再質問しないため)
   - 境界: 見出しもコメントも無い (スケルトン不在) → `/natsuume-writing:outline` の実行を促して終了する (勝手に構成を決めて書き始めない)
2. **執筆ルールの読み込み**
   - `${CLAUDE_PLUGIN_ROOT}/rules/writing-rules.md` を読み、共通コア + outline コメントの媒体・記事タイプに対応するプロファイル差分を適用する
3. **一括生成**
   - スケルトン全体を一括で本文化する (セクションごとの逐次確認はしない)
   - 各セクションの HTML コメント (書くべき内容の指示) は本文化とともに削除する
   - 境界: 既存本文があるセクションは一切変更しない。コメント付き (または空の) セクションのみ本文化する
   - 境界: すべてのセクションに本文がある → 「生成対象がない」ことを報告して何も書き込まずに終了する
   - 未検証事項 (動作確認の結果・計測値・体験談・他章/他記事参照・具体的な日付やバージョン番号) は執筆ルールのセクション 10 に従い `<!-- TODO: 要検証: 何を確認すべきか -->` + ＜プレースホルダー＞ で明示し、事実として書かない
4. **生成後の報告**
   - 残した TODO (要検証事項) の一覧をセッション出力にまとめて提示する
5. **制約の明記**
   - 対象は Markdown のみ。執筆はメインセッション自身が行う (subagent へ委任しない)。既存本文の変更・削除は行わない

## 4. I/O 契約 (issue #209 と同一)

- 入力: outline skill (#208) が定めるスケルトン schema。媒体 ∈ {書籍, 企業ブログ, 個人ブログ}、記事タイプ ∈ {技術解説, 検証レポート, 体験レポート}
- 出力: 同一ファイルへの本文書き込み + セッション出力の TODO 一覧
- TODO コメント形式: `<!-- TODO: 要検証: ... -->` (review skill #210 が残存数を数える対象)

## 5. core-summary.md への追記契約

「詳細ルールへの案内」ブロックを更新する:

- 「章立ての壁打ちは `/natsuume-writing:outline` を、スケルトンからのたたき台生成は `/natsuume-writing:draft` を使用してください。review skill は今後のバージョンで追加予定であり、現時点では存在しません（存在しないコマンドを提案・実行しないでください）」の趣旨
- 追記後も全体 1,500 字以内を維持 (`wc -m` で確認)

## 6. version bump / 登録の契約

- plugin.json / marketplace.json / README テーブルの 3 点を 0.3.0 に同期
- description の skill 提供状況を「outline (章立ての壁打ち) / draft (たたき台一括生成) を提供。review skill は今後追加予定 (#210)」の趣旨に更新
- README Skills 表に draft 行を追加 (コマンド列は完全修飾形 `/natsuume-writing:draft`。#212 で確立した表記)

## 7. Phase B の検証手順

- SKILL.md の frontmatter が既存 skill (skills/outline/SKILL.md) と同じキー構成
- core-summary.md が 1,500 字以内、未提供 skill (review) について「存在しない」明示を維持
- `grep -n 'natsuume-writing:review' plugins/natsuume-writing/rules/core-summary.md` がヒットしない
- version 3 点同期 (0.3.0)
- jq で plugin.json / marketplace.json が valid
