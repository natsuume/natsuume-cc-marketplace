Auto mode (permission_mode = "auto") が有効です。以下の方針で**自走**してください:

- ユーザの依頼に対する変更が一段落したら、確認のために停止せず以下まで進める:
  1. 作業ブランチで変更を git commit する (master ブランチで作業中なら作業ブランチを切る)
  2. リモートに push し、`gh pr create` で PR を作成する
  3. 以下のマージ前提条件を **すべて** 満たしている場合のみ PR をマージする。
     1 つでも未充足なら手を止めて、未充足項目をユーザに報告する:
     - PR が draft ではない (ready for review)
     - リポジトリで required に設定されている CI checks が **全て成功** (`gh pr checks` で確認)
     - レビューが要求されている場合、必要な承認が揃っている (`gh pr view --json reviewDecision`)
     - ブランチ保護ルールに違反しない (`mergeable` が `MERGEABLE` かつ `mergeStateStatus` が `CLEAN`)
- 各ステップは reasonable assumption で前進し、軽微な判断は都度ユーザに聞き返さない
- ただし以下は引き続き禁止 / 要確認:
  - master / 既定ブランチへの直接 push、master 上での直接コミット
  - force push / 履歴改変 / 共有データ削除 等の破壊的操作の独断実行
  - 秘匿情報を含むファイル (.env, credentials.json 等) のコミット
  - マージ前提条件 (上記 3 の bullet 群) を満たさない PR の独断マージ
- すでに対象が commit / PR / マージ済みの場合、そのステップはスキップして次へ進む
- このリポジトリの他プラグイン (pre-push-review 等) が要求するレビュー手順は引き続き従う
