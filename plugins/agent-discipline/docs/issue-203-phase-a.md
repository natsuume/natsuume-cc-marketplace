<!--
  ⚠ このファイルは issue #203 の Phase A (設計記述 commit) の一時的な設計記録であり、
  恒久ドキュメントではない。Phase B (本文実装) で削除する。
  runtime に配送されるファイルではない: inject-always.sh / inject-auto.sh は特定の
  prompts/*.md のみを cat し、skill は SKILL.md のみを配送する。docs/ はどの経路からも
  読まれない (この隔離が Phase A 設計記録をここに置く理由。配送対象ファイルへの
  to-be-deleted scaffolding 混入は PR #203 レビューで code review High / codex P2 として
  指摘され、本ファイルへの移設で対処した)。
-->

# issue #203 Phase A 設計記録

issue 起票時の詳細化に「境界・異常系での挙動の決定」を組み込む変更 (詳細な契約は issue #203 body を参照)。線引きの軸は「決定は issue・導出は Phase A (rule:tdd-two-phase)」。

## 変更 1: skills/issue-plan/SKILL.md セクション 1 (起票前の壁打ち)

セクション 1 の末尾に「境界・異常系の挙動の列挙・確定」の手順を追加する。

- **要求側**: I/O 契約の各入力・状態の定義域の端 (空・0 件・枯渇・上限・重複・不正値等) から境界・異常系の状況を列挙し、それぞれの挙動を受入基準に明記する。挙動が一意に決まらないものは `AskUserQuestion` で確定する
- **禁止側**: 確定した挙動をテストケースへ展開する作業 (ケースの網羅列挙・fixture・検証方法の指定) は issue には書かず、実装時の Phase A (`rule:tdd-two-phase`) に委ねる

文言要件 (issue #203 の I/O 契約):

1. 例示列挙 (空・0 件・枯渇・上限・重複・不正値) は網羅ではなく導出の起点であり、列挙外の境界も I/O 契約の各入力の定義域から導く、と読める文にする (列挙外導出則)
2. 禁止側のスコープは「検証手段への展開」に限定し、挙動の観点列挙を妨げる文言にしない

## 変更 2: skills/issue-plan/SKILL.md セクション 2 (body template)

template 表の「受入基準」行の記載内容を「完了と判定できる具体的な条件 (曖昧な形容詞を避ける)。境界・異常系の挙動を含める」に更新する。

## 変更 3: hooks/prompts/always-fable.md (rule:issue-body)

本文段落の末尾に 1 文を追加する:

> 詳細化には境界・異常系での挙動の決定を含める (列挙・確定の手順は `issue-plan` skill を参照)。

手順本体は skill 側のみに置き、常時注入はこの 1 文に留める (常時注入を lean に保つ既存原則)。rule マーカー (`<!-- rule:... -->`) の追加はしない。

## 変更 4: hooks/prompts/always-sonnet.md (rule:issue-body)

セクション 3 の全埋め込み bullet 群に、変更 3 と同旨の bullet を 1 つ追加する (Sonnet 版の文体・適用範囲の書き方に合わせる。`issue-plan` skill 参照を付す)。rule マーカーの追加はしない。

## 変更 5: version bump (Phase B で実施)

後方互換の機能追加として minor bump: `plugin.json` 0.10.1 → 0.11.0。`.claude-plugin/marketplace.json` とリポジトリ直下 README.md の plugin 一覧テーブルの version を同期する。

## 受入条件 (Phase B 完了判定)

- 変更 1〜5 が反映され、本ファイルが削除されている
- `plugins/agent-discipline/scripts/lint-prompt-sync.sh` の全チェックが通る (rule ID セットは不変)
- 配送対象ファイル (hooks/prompts/*.md、skills/*/SKILL.md) に to-be-deleted な scaffolding コメントが残っていない
