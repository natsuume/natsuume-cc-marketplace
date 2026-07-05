<!--
  agent-discipline: 常時適用ルール (FABLE 版、#175)
  各ルールは「意図 (なぜ) + 短い指示 + 境界 (いつ例外か)」で記述し、パターン列挙は避ける
  (具体列挙は検知層 hook (PreToolUse type:agent) と always-sonnet.md が保持する)。
  rule:issue-claim のみ安全機構のため手順本体を省略せず全文記載する。
  ルール ID セットは always-sonnet.md と完全一致させること (受入基準)。
-->

# agent-discipline: 常時適用ルール (Fable)

以下のルールは permission_mode に依らず常時適用される。auto mode 時の commit→push→PR→merge 自走方針は別途 inject-auto.sh が配送する。

<!-- rule:bash-decompose -->
## 1. Bash コマンド分解

**なぜ**: PreToolUse hook は Bash の `command` 文字列へのパターンマッチで判定するため、複数コマンドを `&&` 等で合成すると先頭以外が検知から外れ、git-guardrails / pre-push-review 等のガードレールが素通りされうる。

**指示**: Bash コマンドは可能な限り分解し、それぞれ独立した Bash ツール呼び出しとして実行する。コマンド置換・`eval` / `bash -c` 等のラッパー・`xargs` / `find -exec` も同じ理由で分解対象。

**境界**: `cd $dir && cmd` (cwd 維持のため) と、前段の成否で後段を確実に止めたいトランザクション的合成 (例: `make build && make test`) は例外として許容する。単なる効率化目的の連結は例外に含まれない。

<!-- rule:design-approval -->
## 2. 設計 / 仕様判断の事前確認

設計・仕様の選択肢が複数成立するとき、未承認の結論や推奨を成果物 (issue body / PR 説明 / plan / commit) に書かない。書き出す前に `AskUserQuestion` でユーザの決定を取り、確定した 1 案だけを残す。理由: issue body は後続 session の唯一の信頼ソースであり、混入した独断は既決事項として実装される。

**境界**: 検討段階で複数案を比較したり推奨案を考えること自体は問題ない。checkpoint は「思考の中」ではなく「成果物に書き出す瞬間」にある。auto mode 中でもこの確認を省略しない。

<!-- rule:issue-body -->
## 3. issue 起票時の詳細化

issue body は後続 session の AI agent にとって唯一の信頼ソースであり、「ユーザが承認した契約書」である。実装時に判断や疑問が生じないよう、起票前 / 起票時に `AskUserQuestion` で詳細化し、確定した内容だけを issue body に全埋め込みする (補助 file には書かない)。

**境界**: 同じ規律は PR 説明・plan ファイル・commit message にも及ぶ。起票後に pick up した時点で不足や過去の未確認選択肢を見つけた場合は、追加確認してから実装に入る。

<!-- rule:issue-granularity -->
## 4. issue の粒度と関係性

1 issue は独立して並列作業できる粒度で起票し、1 PR で閉じられないほど大きい場合は sub-issues に分割する。issue 間の関係性は sub-issue 親子リンクと本文中の `#N` 相互参照を併用する。

詳細手順 (body template、分割基準の具体例、sub-issue 関係設定コマンド等) は `issue-plan` skill を参照。

<!-- rule:closing-keyword -->
## 5. PR 作成時の closing keyword

PR が issue を完全に解決する場合、PR body に closing keyword (`Closes #N` 等) を書いて auto-close させる。closing keyword は **default branch (master/main) 向けの PR でのみ機能する**。

**境界**: 部分対応のみの PR では closing keyword を使わず `Refs #N` / `Part of #N` と書き、issue は手動 close に残す。

<!-- rule:autonomy-boundary -->
## 6. 自律作業中の判断境界

実装フェーズでは、issue 起票時の壁打ちで決まっているはずの設計 / 仕様レベルの事項を再確認せず、issue body を信頼して進める。

**境界**: issue に明記されていない要件を発見した場合、または既存実装と矛盾する後戻りコスト大の判断が必要な場合は一度止まって `AskUserQuestion` で確認する。変数名や import 順のような軽微な判断は逐一確認しない。

<!-- rule:issue-claim -->
## 7. 連続 issue 解決時の排他制御

`/goal` のように複数 issue を順次解決するフロー、もしくは他 session が並列稼働している可能性がある場面では、同 issue への重複着手と他 session の作業破壊を防ぐため以下の手順を必ず守る (安全機構のため省略しない)。

GitHub API には真の atomic compare-and-swap がほぼ無いため、`ai:in-progress` ラベル単独運用では TOCTOU race が残る。そこで claim comment (先着判定) と branch push (確定的排他) の 2 つの排他基盤を併用する。

### 着手手順

1. **早期判定**: `gh issue view <N> --json labels,comments` を確認し、`ai:in-progress` ラベル付与済みまたは未削除の claim comment があれば撤退する
2. **claim comment を投稿**: `gh issue comment <N> --body "🔒 ai:claim branch=<prefix>/issue-<N>-<slug> ts=<UTC ISO 8601>"` (branch 名規約: `<prefix>/issue-<N>-<slug>`、例: `feat/issue-12-add-auth`)
3. **3 秒待機**: 他 session の claim comment が到着する余裕を確保する
4. **comment 再取得 + 先着判定**: 自分の claim より古い timestamp の別 session claim comment があれば競合発生。自分の claim comment を削除して撤退する (`gh api -X DELETE /repos/<owner>/<repo>/issues/comments/<comment-id>`)
5. **作業 branch 作成 + 即 push**: `git switch -c <branch>` → `git commit --allow-empty -m "wip: claim issue #<N>"` → `git push -u origin <branch>`。push 失敗 (同名 branch 既存) なら撤退、成功なら独占権確定
6. **ラベル付与**: `gh issue edit <N> --add-label ai:in-progress` (人間向けの補助的な目印)
7. 通常の implementation フローへ移行する (draft PR 作成 → 実装 → after 系の commit→push→PR→merge 自走)

### ラベル削除規律

`ai:in-progress` ラベルは対応する PR が merge された時のみ削除する。着手中断 / 撤退時はラベルを残し、claim comment と branch のみ削除する。claim comment 本文の `branch=` 値が自分の作業 branch と一致するかで「自分の claim か」を判定し、他 session の claim / branch / ラベルは絶対に削除しない。

### 撤退時のクリーンアップ手順

撤退判定が出たら (1) 自分の claim comment を削除、(2) 自分が作った branch があれば削除、(3) ユーザに撤退理由を 1 行で必ず報告する (auto mode 中でも省略しない)。

<!-- rule:ask-user-question -->
## 8. AskUserQuestion の必須化 (R6)

ユーザへの質問・確認・判断伺い・すり合わせを行う場合、自由文で尋ねて turn を終えず、必ず `AskUserQuestion` ツールを発行する。理由: 回答が構造化され、選択肢の取りこぼしを防げる。

<!-- rule:tdd-two-phase -->
## 9. TDD 2 段階の開発手順 (R3c)

軽微な修正を除き、実装は同一 PR 内で 2 段階の commit に分けて進める: Phase A (テストがある場合は失敗するテスト + 設計骨格) → push して pre-push-review のレビューを通過させ draft PR を作る → Phase B (実装本体) → push → PR を ready 化する。

**境界**: sh スクリプトや markdown のようにテストハーネスを持たない成果物では、Phase A のテストを「設計記述 commit」(ファイル構成・I/O 契約・ルール ID 一覧などを docs コメントとして含む骨格) に置き換える。

着手手順の詳細 (pick-up 分岐・軽微判定・2 段階の具体手順) は `issue-start` skill を参照。

---

進捗・完了報告はこのセッションのツール結果で裏付けられた事実のみを書く。推測や希望的観測を完了として報告しない。
