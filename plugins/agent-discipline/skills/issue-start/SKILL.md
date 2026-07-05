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

<!--
  agent-discipline: issue-start skill (#176 Phase A: 設計記述 commit)

  本ファイルは章構成・トリガー語彙・各章の内容契約のみを定義する骨格である。
  各章本文の具体的な手順・コマンド例・説明文は Phase B で追記する (このファイルはまだ書かない)。

  スコープの境界 (受入基準):
  - frontmatter の description / when_to_use は「着手・実装開始側」のトリガー語彙のみを含む
    (「issue に着手する」「issue の実装を始める」「issue を pick up する」)。
    起票・分解側の語彙 (issue-plan が担当する「起票する」「分解する」「sub-issue を作る」等) は
    一切含めない。2 skill 間で同じ動詞を共有しないこと。
  - 常時注入 (hooks/prompts/always-fable.md, always-sonnet.md) が既に持つルール本体
    (rule:issue-claim, rule:tdd-two-phase, rule:closing-keyword) は本 skill 側で重複記載せず、
    「参照する」形の記述に留める。特に rule:issue-claim の排他制御手順は安全機構であり全文が
    常時注入側に既にあるため、本 skill では手順本体を絶対に複製しない。
-->

# issue-start

<!--
  内容契約 (Phase B):
  この段落には skill 全体の要約 (1-2 文) を書く。「issue 駆動開発における着手・実装開始フェーズの詳細手順を
  progressive disclosure で配送する」主旨と、常時注入との役割分担 (常時注入 = 原則・安全機構本体、
  本 skill = 分岐判定と手順詳細) を明記する。
-->

## 1. pick-up 分岐

<!--
  内容契約 (Phase B):
  - 既存の作業状態を確認するコマンド群を明記する: `gh issue view <N>` /
    `git ls-remote --heads origin '*issue-<N>-*'` / `gh pr list --head <branch>`
  - 分岐条件 1: issue に対応する branch / open PR が既に存在し Phase A (設計記述 or テスト) commit が
    完了済みなら、Phase B (本文実装) から再開する
  - 分岐条件 2: 対応する branch / open PR が存在しなければ新規着手する (rule:issue-claim の排他制御へ進む)
-->

## 2. 排他制御の参照

<!--
  内容契約 (Phase B):
  - 常時注入の rule:issue-claim (claim comment → 3 秒待機 → 先着判定 → branch push 確定 → ラベル付与) への
    参照のみを書き、手順本体 (コマンド例・撤退時のクリーンアップ手順等) を再掲しない
  - 本 skill が担当する範囲は「pick-up 分岐の判定後にどちらへ進むか」であり、排他制御の実施責任は
    rule:issue-claim 側にある、という役割分担を明記する
-->

## 3. 軽微判定 (2 段構え)

<!--
  内容契約 (Phase B):
  - 第 1 段 (軽微側列挙): 以下のいずれかに該当すれば軽微とみなし TDD 2 段階を省略して通常フローで実装する
    - typo 修正
    - ドキュメント・コメントのみの変更
    - 定数・設定値のみの変更
    - ユーザと合意済みの特定 1 箇所修正
  - 第 2 段 (性質判定、第 1 段に非該当の場合のみ実施): 以下のいずれか 1 つでも該当すれば TDD 2 段階を適用する。
    全て非該当なら軽微とみなす
    - (a) 制御フロー・分岐ロジックの追加・変更を含む
    - (b) 複数ファイルの変更を含む
    - (c) 公開インタフェース・仕様・データ形式の変更を含む
  - 判定の適用順序 (第 1 段が先、非該当の場合のみ第 2 段へ進む) を明記する
-->

## 4. TDD 2 段階手順

<!--
  内容契約 (Phase B):
  - 同一 PR 内での手順を明記する: Phase A (テストコード + 型・関数シグネチャ・インタフェース・データ設計。
    実装本体は書かない。テストは red で可) を commit → `git push` を試行 (この時点で pre-push-review の
    レビューループが発動し、通過するまで push は deny される) → push 成功後に draft PR を作成 →
    Phase B (実装、テスト green 化) を commit → push (再度レビューループ) → PR を ready 化
  - 例外を明記する: TDD 2 段階の対象だがテスト可能な成果物が無い場合 (テストハーネスの無い sh スクリプト等)、
    Phase A を「設計記述 commit」(インタフェース・データ設計・受入基準の文書化を含む骨格 commit) に置換する
    (rule:tdd-two-phase の境界節を参照し手順本体を複製しない)
  - pre-push-review のレビューループ自体の内部手順 (何がレビューされ何が deny 条件か) は本 skill の
    スコープ外とし、参照に留める旨を明記する
-->

## 5. closing keyword

<!--
  内容契約 (Phase B):
  - PR body に完全解決なら `Closes #N`、部分対応なら `Refs #N` を書く規律を明記する
    (rule:closing-keyword を参照し手順本体を複製しない)
  - default branch (master/main) 向けの PR でのみ auto-close が機能する、という事実を明記する
-->
