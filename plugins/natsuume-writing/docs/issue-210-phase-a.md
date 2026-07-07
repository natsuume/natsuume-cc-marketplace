# issue #210 Phase A 設計契約 (Phase B で本ファイルを削除する)

`/natsuume-writing:review` skill の設計記述。受入基準・境界/異常系の正は issue #210 body であり、
本ファイルは実装用のファイル構成・SKILL.md の章立て・文言契約の展開のみを持つ。

## 1. 変更ファイル

```
plugins/natsuume-writing/
├── skills/review/SKILL.md         # 新設 (本体)
├── rules/core-summary.md          # 「詳細ルールへの案内」に /natsuume-writing:review を追記し「存在しません」文を削除
└── .claude-plugin/plugin.json     # 0.3.0 → 0.4.0 (minor bump)
.claude-plugin/marketplace.json    # version 同期 + description を「outline / draft / review の 3 skill を提供」へ
README.md                          # version 同期 + Skills 表に review 行追加 (完全修飾形) + 提供範囲の段落を全機能提供に更新
```

## 2. SKILL.md の frontmatter 契約

```yaml
name: review
description: 技術記事・技術書の原稿を 4 観点 (文体ルール準拠 / 構成・論理展開 / 技術的正確さ / 誤字脱字・表記ゆれ) でレビューし、severity 付きの指摘一覧を提示する。ファイルは変更しない
user-invocable: true
when_to_use: |
  ユーザーが以下のようなリクエストをした場合に使用:
  - 「記事をレビューして」「原稿をチェックして」
  - 「推敲を手伝って」
  - 「文体・表記を確認して」
```

## 3. SKILL.md 本文の章立て

issue #210 受入基準をそのまま手順化する:

1. **入力の確認** — 対象ファイルを読み、前提を解決する
   - 境界: 対象ファイルが空・本文が無い → レビュー対象が無いことを報告して終了
   - 境界: outline コメント (媒体・記事タイプ) が無い → AskUserQuestion で確認する。**ファイルへの追記はしない** (追記は draft skill の責務。review は読み取り専用)
   - 対象ファイルに `<!-- TODO: 要検証: ... -->` が残っている場合、指摘一覧の先頭で残存数と内容を報告する
2. **4 観点レビューの実施** — 観点ごとの実施方法:
   1. 文体ルール準拠: `${CLAUDE_PLUGIN_ROOT}/rules/writing-rules.md` を読み、対象の媒体プロファイルに照らした逸脱 (文末・語彙・表記・構成の型) を検出する
   2. 構成・論理展開: outline コメント・見出し構成との乖離、説明順序の飛躍、重複・欠落を検出する
   3. 技術的正確さ: 原稿中の技術的主張を列挙し、(a) 自身の調査 (WebSearch 利用可) と (b) codex plugin の `codex:codex-rescue` agent への調査委任を併用し、両者の findings を突き合わせる。委任指示には対象ファイルパス・検証してほしい主張の一覧・読み取り専用である旨を含める。codex には修正をさせない
      - 境界: codex plugin 未導入・実行失敗 → 自身の調査のみで実施し、codex 併用ができなかった旨を指摘一覧に明記する (エラーで停止しない)
   4. 誤字脱字・表記ゆれ: プロジェクトに textlint/prh の設定 (.textlintrc* / prh.yml 等) があり実行可能なら実行して結果を取り込む。執筆ルールの表記基準 (ひらき・長音・算用数字) は textlint の有無に依らず常に自身でチェックする
      - 境界: textlint 未設定・実行失敗 → 自身のチェックのみで実施し、その旨を明記する
3. **指摘一覧の提示** — 出力形式 (下記 4)。**全件報告する** (重要度による自己フィルタをしない。選別は書き手が行う)。**対象ファイルは変更しない** (修正はユーザの明示的な指示があってから)

## 4. 出力形式契約 (issue #210 I/O 契約と同一)

セッション出力として、先頭に TODO 残存報告、続けて観点ごとの見出し配下に指摘を列挙する:

- 指摘 1 件 = severity (high / medium / low) / 該当箇所の引用または行番号 / 指摘内容 / 根拠
- 観点 3 は Claude / codex の findings の一致・相違が分かる形で報告する

## 5. core-summary.md への追記契約

「詳細ルールへの案内」ブロックを更新する。3 skill が揃うため「存在しません」の注意書きは役目を終える:

- 「章立ての壁打ちは `/natsuume-writing:outline` を、スケルトンからのたたき台生成は `/natsuume-writing:draft` を、原稿のレビューは `/natsuume-writing:review` を使用してください」の趣旨に変更し、「〜は存在しません」文を削除する
- 更新後も全体 1,500 字以内を維持 (`wc -m` で確認)

## 6. version bump / 登録の契約

- plugin.json / marketplace.json / README テーブルの 3 点を 0.4.0 に同期
- description を「outline (章立ての壁打ち) / draft (たたき台一括生成) / review (4 観点レビュー) の 3 skill を提供」の趣旨に更新し、「今後追加予定」の文言を削除する
- README Skills 表に review 行を追加 (コマンド列は完全修飾形 `/natsuume-writing:review`)。提供範囲の段落から「追加予定」を削除する

## 7. Phase B の検証手順

- SKILL.md の frontmatter が既存 skill (outline / draft) と同じキー構成
- core-summary.md が 1,500 字以内で、「存在しません」文が残っていない
- version 3 点同期 (0.4.0)
- jq で plugin.json / marketplace.json が valid
- README・plugin.json・marketplace.json に「追加予定」の残存が無い (`grep -n '追加予定' README.md` の natsuume-writing 該当行と両 json で確認)
