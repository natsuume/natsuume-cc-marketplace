<!--
  agent-discipline: 常時適用ルール (part 3/3、最終 part)
  常時適用ルールは part 1/3・part 2/3・part 3/3 の 3 ファイルで 1 セットを
  構成する。part 間で rule ID を重複させず、3 part の和集合を lint-prompt-sync.sh の
  EXPECTED_ALWAYS_RULE_IDS (期待 rule ID 集合) と完全一致させること (同スクリプトの
  チェック 1 が検証する)。
-->

# agent-discipline: 常時適用ルール — part 3/3

本メッセージは常時適用ルールの part 3/3 (最終 part) であり、part 1/3・part 2/3 と合わせて 1 つのルールセットを構成する。到着順序に依らず、本 part 単独でも各ルールはそのまま適用される。

<!-- rule:issue-claim -->
## 7. 連続 issue 解決時の排他制御 (claim comment の先着判定)

**適用範囲**: `/goal` のように複数 issue を順次解決するフロー、もしくは同じ repo で他 session が並列稼働している可能性がある場面に適用する。

同 issue への重複着手と他 session の作業破壊を防ぐため、以下の手順を必ず守る。

GitHub API には真の atomic compare-and-swap がほぼ無いため、`ai:in-progress` ラベル単独運用では TOCTOU race が残る (= 「ラベル確認 → ラベル付与」の間に他 session が割り込む)。そこで **claim comment の先着判定** を排他の基盤とする: GitHub が server-side で付与する `created_at` + 数値 comment id は投稿順に決まる。step 3 の待機のあいだに先に投稿された claim が一覧に反映されることを前提に、全 session が同じ先着者を導く。

**明示指示による再開**: ユーザのメッセージまたは handoff の文書が issue 番号か branch 名を挙げてその issue の継続を指示している場合 (明示指示) に限り、step 1-5 を経ずに step 6 から再開してよい。step 6 から再開する場合は、ラベルや他セッション ID の claim comment が残っていても撤退せず、削除もしない。明示指示が無ければ、既存の branch / draft PR があってもstep 1 から実行する。明示指示があっても対応する branch が無ければ、新規着手として step 1 から実行する。issue 番号だけの明示指示では、branch を issue-start skill セクション 1.1 の手順で決め、同手順で新しい名前を決めた場合も step 1 から実行する。明示指示で step 1 から実行する場合は、step 1 の早期判定から issue-start skill セクション 1.2 の手順に従う。

### 着手手順

以下を上から順に実行する。step 1〜5 は claim 用のスクリプトが行う:

- **branch 名を決める**: step 6 で使う名前を先に決めてスクリプトに渡す (= claim と branch を 1:1 で対応させる)。issue-start skill セクション 1.1 の手順で既存の branch を探して見つかった名前を使い、無ければ同手順 6 で次の規約に沿って決める (同手順の停止条件ではスクリプトを実行せず停止する)
  - branch 名規約: `<prefix>/issue-<N>-<slug>` (`<prefix>` = `feat` / `fix` / `chore` / `docs` / `refactor` 等、`<slug>` = issue タイトルから kebab-case で抽出した短縮形)
  - 例: `feat/issue-12-add-auth`, `fix/issue-25-null-deref`
- **step 1〜5 (claim 用のスクリプト)**: `'{{CLAIM_ISSUE_SCRIPT_PATH}}' <N> '<branch>'` を実行する。スクリプトは早期判定 (step 1)・claim comment の投稿 (step 2)・3 秒待機 (step 3)・先着判定 (step 4)・ラベル付与 (step 5) を行い、結果を stdout の 1 行と exit code で返す。このファイルを直接 Read したなどでパスがプレースホルダのままの場合は、配送メモの参照パス (prompts ディレクトリ) から見た `../../skills/issue-start/scripts/claim-issue.sh` を使う
  - セッション ID は環境変数 `CLAUDE_CODE_SESSION_ID` の値をスクリプトが使う。未設定の場合は `uuidgen` で生成した値を `--session-id` で渡し、同一セッション中は同じ値を使い続ける
  - 自分の claim は claim comment の `session=` の値で識別する。先着は `ts=` (自己申告) ではなく GitHub が付ける順序 (`created_at` と数値 comment id) で決まる
  - exit 0 なら確保として step 6 へ進む。stdout に `label=failed` があれば、ラベル付与の失敗をユーザに 1 行で報告する
  - exit 1 なら撤退し、撤退理由 (stdout の `reason=`) をユーザに 1 行で報告する
  - exit 2 なら停止し (投稿済みの自分の claim は残す)、stdout の `reason=` をユーザに報告する (「競合なし」と扱わない)

6. **作業 branch を用意**し、通常の implementation フローへ移行する (`rule:tdd-two-phase` に従い、Phase A の commit を push した後に draft PR を作る。既存の draft PR があればそれを使う)。`git fetch --prune origin` の後 (失敗したら停止してユーザに報告)、同名の branch の有無で分ける (remote 側は prune 後の remote-tracking ref `origin/<branch>` で判定する):
   - 無い: 中断した別 issue の commit を引き継がないよう、最新の default branch を起点に作る: `git switch -c <prefix>/issue-<N>-<slug> --no-track origin/<default-branch>` (`--no-track` は upstream を default branch にしないため)
   - remote だけにある: `git switch -c <branch> --track origin/<branch>` で再開する
   - local だけにある: `git switch <branch>` でそのまま再開する
   - 両方にある: `git switch <branch>` の後、local が古ければ `git merge --ff-only origin/<branch>`、新しければそのまま再開し、分岐していれば local / remote のどちらも変更せず停止してユーザに報告する

### ラベル削除規律 (誤削除事故防止)

- 対応 PR の merge により issue が close された後の**完了時クリーンアップ** (`ai:in-progress` ラベルと claim comment の削除) は**必須ではない** — issue の open/close 状態を完了管理の一次情報とする (別 session が Phase B から正規に引き継いで merge した場合、引き継ぎ session は `session=` 不一致で削除できないが、残置してよい)
- 完了時クリーンアップを行う場合は、claim comment の `session=` 値が自分のセッション ID と一致する場合に限り、ラベルと claim comment を**一組として**削除する (片方だけ削除しない)
- 撤退時・着手中断時のどちらも、自分の claim comment のみ削除する。どちらの場合もラベルは削除しない
  - ラベルを残す理由: 「中断したが復帰予定」の状態が人間に見える + 後続 session が `ai:in-progress` を見て撤退 → 二重着手の保険として機能
  - 古い stale なラベルは人間が判定して手動削除する運用に委ねる
- **他 session の claim comment / branch / ラベルは絶対に削除しない**
- 「自分の claim か」の判定基準: claim comment 本文の `session=` 値が **自分のセッション ID と一致するか**
  - 一致 → 自分の claim、削除可
  - 不一致、または `session=` キーが無い → 他 session の claim として扱い、削除禁止 (`session=` の無い claim は自分のものと確認できないため)

### 撤退と着手中断の後片付け

**撤退** (スクリプトが exit 1 を返した場合) は作業 branch を作る前で、自分で削除するものは無い: 早期判定の撤退ではまだ claim comment を投稿しておらず、先着判定で負けた (lost-race) 場合の自分の claim comment はスクリプトが削除済みである。ユーザに撤退理由を **1 行で必ず報告** する (例: 「issue #12 は他 session が先着のため撤退しました」)。auto mode 中でもこの報告は省略しない (= ユーザが進捗状況を把握できなくなるため)

**着手中断** (確保の確定後に作業をやめる場合) は、自分の claim comment だけを削除する (`gh api -X DELETE 'repos/{owner}/{repo}/issues/comments/<comment-id>'`。id はスクリプトの stdout の `comment_id=`)。作業 branch (local / remote)・draft PR・ラベルは再開のために残す。確保の判定に branch を使わないため、branch を削除する必要は無い。

### よくある誤操作と回避

- **誤着手**: 「ラベル確認 → ラベル付与」だけで判定したため race condition で同 issue に複数 session が着手 → step 2-4 の claim comment 先着判定で防ぐ
- **ラベル誤削除**: 「ラベル単独だと誰が付けたか不明」でつい削除 → claim comment の `session=` 値で持ち主を識別、自分のものでなければ触らない
- **撤退時の clean-up 忘れ**: claim comment が残ったまま次の issue へ進む → ゴーストの claim が後続 session の撤退判定を誤らせる → 撤退の後片付けを必ずセットで実行

<!-- rule:ask-user-question -->
## 8. AskUserQuestion の必須化

**適用範囲**: ユーザへの質問・確認・判断伺い・すり合わせを行う全ての場面に適用する。テキスト応答の自由文で尋ねて turn を終えることを禁止する。

ユーザへの質問・確認・判断伺い・すり合わせを行う場合、テキスト応答の自由文で尋ねて turn を終えず、必ず `AskUserQuestion` ツールを発行する。適用場面は以下を含むがこれに限らない:

- **turn 途中の仕様確認**: 実装中に発見した曖昧な仕様点の確認
- **タスク完了後の次ステップ確認**: 「このタスクの後、次は何をしますか」のような確認
- **エスカレーション受領後の再開判断**: エスカレーション報告に対するユーザの回答を受けて作業を再開してよいかの判断
- **複数案からの選択依頼**: セクション 2 の設計判断など、複数案から 1 つを選んでもらう場面

この規則は「質問するかどうか」の判断そのものを変えない (質問する場合の手段のみを規定する)。上記の列挙は質問が発生した場合の適用場面の例示であり、質問が不要な場面 (auto mode の reasonable assumption で前進できる軽微な判断等) で新たに質問を作り出さないこと。

**同一ターン接続**: 判断材料の説明テキストと `AskUserQuestion` は同一ターンで接続し、テキストのみで turn を終えて返信を待つ分割をしない。説明があっても質問文・選択肢は自己完結に書く。説明が見えない事象が起きたら分割手順に戻さず、ユーザに報告して確認を取る。

**なぜ**: 自由文での質問はユーザの回答が非構造化になり、選択肢の取りこぼしや誤読が生じやすい。`AskUserQuestion` は選択肢を明示的に構造化するため、回答の解釈が確定的になる。

**例**:
- 悪い例: 「このタスク完了後、次は B 機能に進めてよいですか?」とテキスト応答で尋ねて turn を終える
- 良い例: 同じ確認を `AskUserQuestion` ツールで発行し、「進める」「別タスクに切替える」等の選択肢を構造化して提示する

<!-- rule:tdd-two-phase -->
## 9. spec-first 2 段階の開発手順

**適用範囲**: 軽微な修正を除く実装作業全体に適用する。

軽微な修正を除き、実装は同一 PR 内で 2 段階の commit に分けて進める:

1. **Phase A**: テストがある場合は失敗するテスト (red) + 設計骨格を commit し、push して pre-push-review のレビューを通過させ draft PR を作る
2. **Phase B**: 実装本体を commit し、push して PR を ready 化する

**テスト不能な成果物の扱い**: sh スクリプトや markdown のようにテストハーネスを持たない成果物では、Phase A のテストを「設計記述 commit」に置き換える。設計記述 commit は、ファイル構成・スクリプトの入出力契約・ルール ID 一覧などを docs コメントとして含む骨格 (空実装または no-op 実装) で構成する。

**なぜ 2 段階か**: 設計・インタフェースの判断とロジック実装の判断を分離することで、それぞれを独立にレビューできる。Phase A の時点で pre-push-review のレビューを通すことで、設計段階の問題を実装着手前に検出できる。本手順は正典 TDD (1 テストずつの red-green) を要求する規律ではなく、実行可能仕様の先行固定 (spec-first) である。

**詳細手順への参照**: pick-up 分岐・軽微判定 (2 段構え) の具体的な判定基準・Phase A/B の実行コマンド例が必要な場合は `issue-start` skill を参照する。

**例**:
- 悪い例: 新機能の sh スクリプトをテストなしで一度にすべて実装して push する
- 良い例: (テスト可能な成果物なら) 失敗するテストを含む Phase A commit を先に push してレビューを通し、Phase B で実装する commit を追加する。テスト不能な成果物ではファイル構成・I/O 契約を文書化した設計記述 commit を Phase A として先に push する

---

進捗・完了報告はこのセッションのツール結果で裏付けられた事実のみを書く。推測や希望的観測を完了として報告しない。

