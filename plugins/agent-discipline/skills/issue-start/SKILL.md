---
name: issue-start
description: 「issue に着手する」「issue の実装を始める」「issue を pick up する」際の手順 (pick-up 分岐・軽微判定・spec-first 2 段階・closing keyword) を提供する
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

**第 1 段 (軽微側列挙)**: 以下のいずれかに該当すれば軽微と判定し、spec-first 2 段階を省略して通常フローで実装します。

- typo 修正
- ドキュメント・コメントのみの変更
- 定数・設定値のみの変更
- ユーザと合意済みの特定 1 箇所修正

**第 2 段 (性質判定、第 1 段に非該当の場合のみ実施)**: 以下のいずれか 1 つでも該当すれば spec-first 2 段階を適用します。全て非該当なら軽微と判定します。

- (a) 制御フロー・分岐ロジックの追加・変更を含む
- (b) 複数ファイルの変更を含む
- (c) 公開インタフェース・仕様・データ形式の変更を含む

判定は第 1 段から順に行います。第 1 段に該当した時点で軽微と確定し、第 2 段の判定は行いません。

## 4. spec-first 2 段階手順

軽微と判定されなかった実装は、同一 PR 内で以下の 2 段階に分けて進めます。

1. **Phase A**: テストコード + 型・関数シグネチャ・インタフェース・データ設計を commit します。実装本体は書きません。テストは red (失敗) の状態で構いません
2. `git push` を試行します。この時点で pre-push-review のレビューループが発動し、通過するまで push は deny されます
3. push 成功後、draft PR を作成します
4. **Phase B**: 実装本体を commit し、テストを green 化します
5. 再度 `git push` します (再度レビューループが発動します)
6. レビュー通過後、PR を ready 化します

**例外**: spec-first 2 段階の対象だがテスト可能な成果物が無い場合 (テストハーネスの無い sh スクリプトや markdown 等)、Phase A を「設計記述 commit」(インタフェース・データ設計・受入基準の文書化を含む骨格 commit) に置き換えます。詳細は常時注入ルール `rule:tdd-two-phase` の境界節を参照してください (本 skill では手順本体を複製しません)。

pre-push-review のレビューループ自体が何をレビューし何を deny 条件とするかは、本 skill のスコープ外です。当該プラグインのドキュメントを参照してください。

### 4.1 局所定義 (spec-first の位置づけ)

本ワークフローの 2 段階構成は正典 TDD (1 テストずつ red-green-refactor を回す進め方) ではなく、実行可能仕様の先行固定 — spec-first (ATDD / Specification by Example の系譜) です。名称に依らず、手続きは次の操作的記述がすべてです: Phase A では受入基準を検証するテスト一式と公開契約 (シグネチャ・インタフェース・データ設計) を作ってレビューを受け、Phase B ではその契約を守ったまま実装本体を作ります (契約の改訂が必要になったら 4.2 の手順で Phase A に戻ります)。

この構成には AI agent 特有の正当化が 2 点あります:

- テストをユーザ承認済みの契約として先行固定することで、「実装がテストを通らないときテスト側を書き換える」failure mode を構造的に抑止する
- pre-push-review が有効な環境では、diff hash (branch 全差分 + staged + unstaged) により Phase B でテストを変更するとレビュー済み marker が自動失効し、契約の変更が再審査になる

### 4.2 Phase A テストの provisional 契約

Phase A のテストは「承認済みだが改訂可能な契約」です。実装に接触して初めて分かる不自然さや実装不可能性が判明したら、Phase B の途中でテストを黙って書き換えるのではなく、実装を止めて Phase A に戻り、テストを改訂して再レビュー (pre-push-review が有効な環境では push 時の diff hash 失効により自動的に再審査になります) を経てから Phase B を再開します。

### 4.3 Phase A の評価基準

Phase A の質は次の 3 点で評価します:

- (a) **test matrix**: 受入基準と境界・異常系から導いたケースの網羅表になっているか
- (b) **seam の選定**: どの seam (public boundary) から挙動を観測するか、その選定理由を説明できるか
- (c) **実装詳細への非結合**: private 関数・内部の呼び出し回数など実装詳細に結合していないか

### 4.4 成果物粒度

Phase A で固定するもの: テストコード・型・関数シグネチャ・インタフェース・データ設計 (= 公開契約に必要なもの)。Phase B に委ねるもの: private helper の構成・内部アルゴリズムの選択・ファイル内の局所的な分割。

### 4.5 Phase B 内の進め方

承認済みテスト集合を 1 つずつ green 化して進めます。途中の学習で契約の欠陥 (テストの誤り・仕様の穴) が見えたら、4.2 の Phase A ループへ戻ります。

## 5. closing keyword

PR body には、issue を完全に解決する場合 `Closes #N`、部分対応の場合 `Refs #N` を書きます。詳細な規約 (有効なキーワード一覧・書式など) は常時注入ルール `rule:closing-keyword` を参照してください (本 skill では手順本体を複製しません)。

closing keyword による auto-close は **default branch (master/main) 向けの PR でのみ機能**します。feature branch 向けの PR では効果がないため注意してください。
