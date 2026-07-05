---
name: issue-start
description: issue に着手する・issue の実装を始める・issue を pick up する際の手順 (pick-up 分岐・軽微判定・TDD 2 段階・closing keyword) を提供する
user-invocable: true
when_to_use: |
  ユーザーが以下のようなリクエストをした場合に使用:
  - 「issue に着手する」
  - 「issue の実装を始める」
  - 「issue を pick up する」
---

# issue-start

issue 駆動開発における **着手・実装開始フェーズ** の詳細手順です。常時注入 (SessionStart) が配送する原則 (`rule:issue-claim` / `rule:tdd-two-phase` / `rule:closing-keyword`) を前提に、本 skill はそれらを実行するための分岐判定・具体的な手順を提供します。常時注入ルールの手順本体、特に安全機構である `rule:issue-claim` の排他制御はここでは複製せず、参照するに留めます。

## 1. pick-up 分岐

issue に着手する前に、既存の作業状態を確認します。

```bash
gh issue view <N>
git ls-remote --heads origin '*issue-<N>-*'
gh pr list --head <branch>
```

- issue に対応する branch / open PR が既に存在し、Phase A (テスト or 設計記述 commit) が完了済みの場合 → Phase B (本文実装) から再開します
- 対応する branch / open PR が存在しない場合 → 新規着手として、セクション 2 の排他制御手順に進みます

## 2. 排他制御の参照

新規着手と判定した場合の排他制御 (claim comment → 3 秒待機 → 先着判定 → branch push による確定 → ラベル付与) は、常時注入ルール `rule:issue-claim` の手順本体をそのまま実行してください。安全機構のため本 skill 側では手順を複製しません。

本 skill が担当するのはセクション 1 の pick-up 分岐判定までで、判定後の排他制御の実施責任は `rule:issue-claim` 側にあります。

## 3. 軽微判定 (2 段構え)

実装が「軽微な修正」に該当するかどうかを、以下の 2 段階で判定します。

**第 1 段 (軽微側列挙)**: 以下のいずれかに該当すれば軽微と判定し、TDD 2 段階を省略して通常フローで実装します。

- typo 修正
- ドキュメント・コメントのみの変更
- 定数・設定値のみの変更
- ユーザと合意済みの特定 1 箇所修正

**第 2 段 (性質判定、第 1 段に非該当の場合のみ実施)**: 以下のいずれか 1 つでも該当すれば TDD 2 段階を適用します。全て非該当なら軽微と判定します。

- (a) 制御フロー・分岐ロジックの追加・変更を含む
- (b) 複数ファイルの変更を含む
- (c) 公開インタフェース・仕様・データ形式の変更を含む

判定は第 1 段から順に行います。第 1 段に該当した時点で軽微と確定し、第 2 段の判定は行いません。

## 4. TDD 2 段階手順

軽微と判定されなかった実装は、同一 PR 内で以下の 2 段階に分けて進めます。

1. **Phase A**: テストコード + 型・関数シグネチャ・インタフェース・データ設計を commit します。実装本体は書きません。テストは red (失敗) の状態で構いません
2. `git push` を試行します。この時点で pre-push-review のレビューループが発動し、通過するまで push は deny されます
3. push 成功後、draft PR を作成します
4. **Phase B**: 実装本体を commit し、テストを green 化します
5. 再度 `git push` します (再度レビューループが発動します)
6. レビュー通過後、PR を ready 化します

**例外**: TDD 2 段階の対象だがテスト可能な成果物が無い場合 (テストハーネスの無い sh スクリプトや markdown 等)、Phase A を「設計記述 commit」(インタフェース・データ設計・受入基準の文書化を含む骨格 commit) に置き換えます。詳細は常時注入ルール `rule:tdd-two-phase` の境界節を参照してください (本 skill では手順本体を複製しません)。

pre-push-review のレビューループ自体が何をレビューし何を deny 条件とするかは、本 skill のスコープ外です。当該プラグインのドキュメントを参照してください。

## 5. closing keyword

PR body には、issue を完全に解決する場合 `Closes #N`、部分対応の場合 `Refs #N` を書きます。詳細な規約 (有効なキーワード一覧・書式など) は常時注入ルール `rule:closing-keyword` を参照してください (本 skill では手順本体を複製しません)。

closing keyword による auto-close は **default branch (master/main) 向けの PR でのみ機能**します。feature branch 向けの PR では効果がないため注意してください。
