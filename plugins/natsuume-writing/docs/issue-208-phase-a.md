# issue #208 Phase A 設計契約 (Phase B で本ファイルを削除する)

`/natsuume-writing:outline` skill の設計記述。受入基準・境界/異常系の正は issue #208 body であり、
本ファイルは実装用のファイル構成・SKILL.md の章立て・文言契約の展開のみを持つ。

## 1. 変更ファイル

```
plugins/natsuume-writing/
├── skills/outline/SKILL.md        # 新設 (本体)
├── rules/core-summary.md          # 「詳細ルールへの案内」に /natsuume-writing:outline を追記
└── .claude-plugin/plugin.json     # 0.1.0 → 0.2.0 (minor bump)
.claude-plugin/marketplace.json    # version 同期 + description の「今後追加予定」から outline を提供済みへ
README.md                          # version 同期 + natsuume-writing セクションに Skills 表を追加
```

## 2. SKILL.md の frontmatter 契約

既存 skill (agent-discipline/issue-plan, ui-discipline/ui-patterns) と同形式:

```yaml
name: outline
description: 壁打ちで技術記事・技術書の章立て・セクション構成を決め、記事ファイルにインファイルスケルトン (見出し + HTML コメント) を書き込む
user-invocable: true
when_to_use: |
  ユーザーが以下のようなリクエストをした場合に使用:
  - 「記事の構成を考えたい」「章立てを決めたい」
  - 「執筆の壁打ちをしたい」
  - 「記事のアウトラインを作る」
```

## 3. SKILL.md 本文の章立て

issue #208 受入基準の 3 段階フローをそのまま手順化する:

1. **前提の確定** — 対象ファイル (引数) の確認と、テーマ・想定読者・媒体・記事タイプの AskUserQuestion。
   会話・引数から自明な項目は質問省略可。値域は issue #208 I/O 契約のとおり
   (媒体 ∈ {書籍, 企業ブログ, 個人ブログ} / 記事タイプ ∈ {技術解説, 検証レポート, 体験レポート}、書籍では記事タイプ省略可)
2. **壁打ち** — `${CLAUDE_PLUGIN_ROOT}/rules/writing-rules.md` を読み、記事タイプ別の導入パターン・
   構成の型 (数の予告→列挙→回収 / 章冒頭定型) を反映した構成案を複数提示し、AskUserQuestion で段階収束
3. **スケルトン書き込み** — 確定後に 1 回だけ書き込む。書式は下記 4

境界・異常系 (issue #208 受入基準の転記; 実装はこの契約でなく issue を正とする):

- 既存本文 (見出し・HTML コメント・空行以外のテキスト) があるファイル → 書き込まず AskUserQuestion (中止 / 既存構成を尊重した追記)
- 対象ファイル未指定 → ファイル名を提案し確認後に新規作成
- 壁打ち途中の中断 → 何も書き込まない

## 4. スケルトン書式契約 (issue #208 I/O 契約と同一。draft #209 / review #210 が読む)

```markdown
# 記事タイトル

<!-- outline: 媒体=個人ブログ / 記事タイプ=技術解説 / 想定読者=◯◯ -->

## セクション見出し
<!-- このセクションで書くべき内容・意図・入れる予定の要素 (図・コード・比較表など) -->
```

- outline コメントはファイル先頭 (タイトル直後) に 1 つだけ
- 媒体=書籍 のとき「記事タイプ=」は省略可

## 5. core-summary.md への追記契約

「詳細ルールへの案内」ブロックの skill 言及を更新する。outline のみ提供済みになるため:

- 「執筆支援 skill（outline / draft / review）は今後のバージョンで追加予定であり、現時点では存在しません」
  → 「章立ての壁打ちは `/natsuume-writing:outline` を使用してください。draft / review skill は今後のバージョンで
  追加予定であり、現時点では存在しません（存在しないコマンドを提案・実行しないでください）」の趣旨に変更
- 追記後も全体 1,500 字以内を維持 (`wc -m` で確認)

## 6. version bump / 登録の契約

- plugin.json / marketplace.json / README テーブルの 3 点を 0.2.0 に同期
- marketplace.json と plugin.json の description の「outline / draft / review skill は今後追加予定」を
  「outline skill を提供。draft / review は今後追加予定 (#209 / #210)」の趣旨に更新
- README の natsuume-writing セクションに Skills 表 (skill 名 / コマンド / 説明) を追加し、
  「現時点では〜のみ提供」の記述を outline 込みに更新

## 7. Phase B の検証手順

- SKILL.md の frontmatter が既存 skill と同形式 (name / description / user-invocable / when_to_use)
- core-summary.md が 1,500 字以内、未提供 skill (draft / review) を「存在しない」と明示したまま
- version 3 点同期 (0.2.0)
- jq で plugin.json / marketplace.json が valid
