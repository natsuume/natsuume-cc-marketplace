<!--
  agent-discipline: 常時適用ルール (FABLE 版) — #175 Phase A 骨格

  書き分け方針 (issue #175「2 ファイルの書き分け仕様」FABLE 側。親 issue #173 ユーザ決定事項 1/7):
  - 各ルールを「意図 (なぜ) + 短い指示 + 境界 (いつ例外か)」の 3 要素で記述し、パターンの列挙は
    避ける (具体的な列挙は検知層 hook (PreToolUse agent hook) が保持するため、誘導層は簡潔に保つ)
  - 禁止表現 8 カテゴリ (推奨マーキング / 独断の正当化 / 比較表で勝者決定 / 暗黙の決め打ち /
    「とりあえず」系 / 暫定マーク残置 / ユーザ判断の先回り代弁 / 受入基準への未承認選択埋め込み) は
    個別列挙せず、「設計・仕様の選択肢が複数成立するとき、未承認の結論や推奨を成果物 (issue body /
    PR 説明 / plan / commit) に書かない。書き出す前に AskUserQuestion でユーザの決定を取り、確定
    した 1 案だけを残す。理由: issue body は後続 session の唯一の信頼ソースであり、混入した独断は
    既決事項として実装される」という意図短文に圧縮する
  - 「進捗・完了報告はこのセッションのツール結果で裏付けられた事実のみを書く」を含める
  - 内部推論をレスポンスとして書き出させる指示は含めない

  ルール ID セットは always-sonnet.md と完全一致させること (受入基準)。本ファイルは Phase A の
  骨格であり、各ルール ID 節はまだ本文を持たない。本文は Phase B で執筆する。
-->

# agent-discipline: 常時適用ルール (Fable)

<!-- rule:bash-decompose -->
## Bash コマンド分解
<!-- 内容契約: Bash ツール呼び出しを分解する意図 (hook のパターンマッチ検知を素通りさせない) を
     短く述べ、合成を許容する境界 (cwd 維持のための cd 前置、失敗時に後続を止めるトランザクション
     的合成) を 1 文で示す。分解すべき合成パターンの列挙はしない。 -->

<!-- rule:design-approval -->
## 設計 / 仕様判断の事前確認
<!-- 内容契約: 設計・仕様の選択肢が複数成立する場面で未承認の結論や推奨を成果物 (issue body / PR
     説明 / plan / commit) に書かないこと、書き出し前に AskUserQuestion でユーザの決定を取り確定
     した 1 案だけを残すことを、意図とともに圧縮して述べる。禁止表現 8 カテゴリの個別列挙はしない。 -->

<!-- rule:issue-body -->
## issue 起票時の詳細化
<!-- 内容契約: issue body が後続 session の唯一の信頼ソースであるという意図を述べ、起票前 /
     pick up 時に AskUserQuestion で詳細化すること、PR 説明・plan・commit にも同じ規律が及ぶことを
     短く述べる。起票直前 self-check の項目列挙はしない。 -->

<!-- rule:issue-granularity -->
## issue の粒度と関係性
<!-- 内容契約: 1 issue を独立並列作業可能な粒度で起票する意図と、sub-issue 親子リンク + `#N`
     相互参照を併用する方針を短く述べる。詳細な起票手順は issue-plan skill へのポインタとする。 -->

<!-- rule:closing-keyword -->
## PR 作成時の closing keyword
<!-- 内容契約: PR が issue を完全解決する場合に closing keyword を PR body に書く意図を述べ、
     closing keyword は default branch 向け PR でのみ機能する事実を追記する。有効語の列挙はしない。 -->

<!-- rule:autonomy-boundary -->
## 自律作業中の判断境界
<!-- 内容契約: 設計 / 仕様レベルの再確認をしない一方、issue 未記載の要件を発見した場合や既存実装
     と矛盾する後戻りコスト大の判断が必要な場合は一度止まって AskUserQuestion することを短く述べる。 -->

<!-- rule:issue-claim -->
## 連続 issue 解決時の排他制御
<!-- 内容契約: 安全機構のため手順本体を Fable 版でも省略せず全文記載する (skill へのポインタ化は
     しない)。claim comment + branch push の二段排他手順、撤退時クリーンアップ手順を含める。 -->

<!-- rule:ask-user-question -->
## AskUserQuestion の必須化 (R6)
<!-- 内容契約: ユーザへの質問・確認・判断伺いを自由文で終えず必ず AskUserQuestion を発行する意図
     (回答の構造化と取りこぼし防止) を 1 文で述べる。適用場面の列挙はしない。 -->

<!-- rule:tdd-two-phase -->
## TDD 2 段階の開発手順 (R3c)
<!-- 内容契約: 設計記述 commit (Phase A) → 実装本体 commit (Phase B) の 2 段階で進める要点のみを
     短く述べる。詳細手順は issue-start skill へのポインタとする。 -->
