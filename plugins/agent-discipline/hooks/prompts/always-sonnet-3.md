<!--
  agent-discipline: 常時適用ルール (SONNET 版、part 3/3、issue #236)
  issue #236 (注入ペイロード分割) による always-sonnet.md 3 分割の 3 番目 (最終 part)。rule 本文・
  rule ID マーカーは分割前から一字一句無変更。ヘッダコメント・part 表記・part 冒頭の説明文の
  みが分割に伴う追加。ルール ID セットの完全一致の受入基準は part 1/3 (always-sonnet-1.md)
  のヘッダコメントを参照。
-->

# agent-discipline: 常時適用ルール (Sonnet) — part 3/3

本メッセージは常時適用ルール (Sonnet 版) の part 3/3 (最終 part) であり、part 1/3・part 2/3 と合わせて 1 つのルールセットを構成する。到着順序に依らず、本 part 単独でも各ルールはそのまま適用される。

<!-- rule:issue-claim -->
## 7. 連続 issue 解決時の排他制御 (claim comment + branch push の二段排他)

**適用範囲**: `/goal` のように複数 issue を順次解決するフロー、もしくは同じ repo で他 session が並列稼働している可能性がある場面に適用する。

`/goal` のように **複数 issue を順次解決するフロー**、もしくは同じ repo で **他 session が並列稼働している可能性がある場面** では、同 issue への重複着手と他 session の作業破壊を防ぐため以下の手順を必ず守る。

GitHub API には真の atomic compare-and-swap がほぼ無いため、`ai:in-progress` ラベル単独運用では TOCTOU race が残る (= 「ラベル確認 → ラベル付与」の間に他 session が割り込む)。そこで以下 2 つの確定的な排他基盤を併用する:

- **claim comment**: GitHub が server-side で付与する created_at + 数値 comment id で先着判定 (= 早期 detection)
- **branch push**: git server-side で同名 branch は 1 つしか存在できず、並列 push の片方は確定的に fail する (= 最終確定)

### 着手手順

以下を上から順に実行する:

1. **早期判定**: `gh issue view <N> --json labels,comments` で確認
   - `ai:in-progress` ラベル付与済 or 未削除の claim comment 存在 → **撤退** (= 別 issue 候補をユーザに提示するか、別 issue に切替え)
   - いずれも無ければ次のステップへ

2. **claim comment を投稿** (排他基盤 1: comment 先着判定):
   ```
   gh issue comment <N> --body "🔒 ai:claim branch=<prefix>/issue-<N>-<slug> session=<セッションID> ts=<UTC ISO 8601>"
   ```
   - branch 名は次ステップで使う予定の名前を先に決めてここに埋め込む (= claim と branch を 1:1 で対応させる)
   - branch 名規約: `<prefix>/issue-<N>-<slug>` (`<prefix>` = `feat` / `fix` / `chore` / `docs` / `refactor` 等、`<slug>` = issue タイトルから kebab-case で抽出した短縮形)
   - 例: `feat/issue-12-add-auth`, `fix/issue-25-null-deref`
   - `<セッションID>` は環境変数 `CLAUDE_CODE_SESSION_ID` の値 (Claude Code がセッション毎に付与する UUID)。未設定の場合のみ `uuidgen` で生成した値を代用し、同一セッション中は同じ値を使い続ける
   - `session=` は「自分の claim か」の判定キー。branch 名は issue 番号 + タイトル slug から決定的に導出され他 session と同名になりうるため、`branch=` / `ts=` (自己申告) / comment author (同一 GitHub アカウント) では自他判別できない

3. **3 秒待機**: 他 session の claim comment が到着する余裕を確保 (`sleep 3`)

4. **comment 再取得 + 先着判定**: REST GET で comment 一覧を全ページ再取得:
   ```
   gh api --paginate 'repos/{owner}/{repo}/issues/<N>/comments?per_page=100'
   ```
   - REST GET を使う理由: レスポンスが数値 `id`・`created_at`・`body` を 1 呼び出しで返す (`gh issue view <N> --json comments` の `id` は GraphQL node ID (`IC_...`) のため数値比較に使えない)。`{owner}` / `{repo}` placeholder は gh が current repository から解決する。一覧は id 昇順・既定 30 件/ページのため、`--paginate` + `per_page=100` で全ページを取得する (直近の自分の claim が第 1 ページに含まれない可能性がある)
   - 自分の claim は body の `session=` 値が自分のセッション ID と一致する comment として識別する
   - 先着判定: claim comment (body が `🔒 ai:claim ` で始まる comment) のうち **`(created_at, 数値 id)` の辞書順最小** を先着とする。`created_at` は server-side 付与値であり、`ts=` 自己申告値は判定に使わない
   - 先着が自分でない → **競合発生**。自分の claim comment を削除して撤退:
     ```
     gh api -X DELETE /repos/<owner>/<repo>/issues/comments/<comment-id>
     ```
   - REST GET の失敗 (非ゼロ終了・ページ取得不能)、または取得結果に自分の claim が存在しない場合は「競合なし」と扱わず、branch push に進まず停止してユーザに報告する (fail-closed)
   - 先着が自分なら次のステップへ

5. **作業 branch 作成 + 即 push** (排他基盤 2: branch 名 uniqueness の確定判定):
   ```
   git switch -c <prefix>/issue-<N>-<slug>
   git commit --allow-empty -m "wip: claim issue #<N> session=<セッションID>"
   git push -u origin <prefix>/issue-<N>-<slug>
   ```
   - commit message にセッション ID を埋め込む理由: 同一メッセージ・同一親・同一秒の空 commit は OID が一致し、後発の push が "already up to date" として成功扱いになる経路が理論上残る。ID 埋込で異なる session 間の OID 衝突を構造的に排除する (同一会話を `--fork-session` 無しで並列 resume した worker 同士は session ID を共有するため、この保証の対象外)
   - push **失敗** (= 同名 branch 既存) → 他 session が先着していた (claim comment 経路では検知できなかったケース)。自分の claim comment を削除 + ローカル branch を削除して撤退
   - push **成功** → **独占権確定**

6. **ラベル付与** (人間向けの目印として補助運用): `gh issue edit <N> --add-label ai:in-progress`

7. 通常の implementation フローへ移行 (= draft PR 作成 → 実装 → after 系の commit→push→PR→merge 自走)

### ラベル削除規律 (誤削除事故防止)

- 対応 PR の merge により issue が close された後の**完了時クリーンアップ** (`ai:in-progress` ラベルと claim comment の削除) は**必須ではない** — issue の open/close 状態を完了管理の一次情報とする (別 session が Phase B から正規に引き継いで merge した場合、引き継ぎ session は `session=` 不一致で削除できないが、残置してよい)
- 完了時クリーンアップを行う場合は、claim comment の `session=` 値が自分のセッション ID と一致する場合に限り、ラベルと claim comment を**一組として**削除する (片方だけ削除しない)
- 着手中断 / 撤退時は完了時クリーンアップとは別に、ラベルを残し、自分の claim comment と branch のみ削除する
  - ラベルを残す理由: 「中断したが復帰予定」の状態が人間に見える + 後続 session が `ai:in-progress` を見て撤退 → 二重着手の保険として機能
  - 古い stale なラベルは人間が判定して手動削除する運用に委ねる
- **他 session の claim comment / branch / ラベルは絶対に削除しない**
- 「自分の claim か」の判定基準: claim comment 本文の `session=` 値が **自分のセッション ID と一致するか**
  - 一致 → 自分の claim、削除可
  - 不一致、または `session=` キーが無い (旧形式) → 他 session の claim として扱い、削除禁止 (旧形式は自分のものと確認できないため)

### 撤退時のクリーンアップ手順

撤退判定 (step 1, 4, 5 のいずれか) が出たら以下を実行:

1. 自分の claim comment を削除: `gh api -X DELETE /repos/<owner>/<repo>/issues/comments/<comment-id>`
2. 自分が作った branch があれば削除: `git switch master && git push origin --delete <branch> && git branch -D <branch>`
3. ユーザに撤退理由を **1 行で必ず報告** する (例: 「issue #12 は他 session が先着のため撤退しました」)。auto mode 中でもこの報告は省略しない (= ユーザが進捗状況を把握できなくなるため)

### よくある誤操作 (= 過去事例) と回避

- **誤着手**: 「ラベル確認 → ラベル付与」だけで判定したため race condition で同 issue に複数 session が着手 → step 2-5 の二段排他で防ぐ
- **ラベル誤削除**: 「ラベル単独だと誰が付けたか不明」でつい削除 → claim comment の `session=` 値で持ち主を識別、自分のものでなければ触らない
- **撤退時の clean-up 忘れ**: claim comment が残ったまま次の issue へ進む → ゴーストの claim が後続 session の撤退判定を誤らせる → step 1-3 を必ずセットで実行

<!-- rule:ask-user-question -->
## 8. AskUserQuestion の必須化 (R6)

**適用範囲**: ユーザへの質問・確認・判断伺い・すり合わせを行う全ての場面に適用する。テキスト応答の自由文で尋ねて turn を終えることを禁止する。

ユーザへの質問・確認・判断伺い・すり合わせを行う場合、テキスト応答の自由文で尋ねて turn を終えず、必ず `AskUserQuestion` ツールを発行する。適用場面は以下を含むがこれに限らない:

- **turn 途中の仕様確認**: 実装中に発見した曖昧な仕様点の確認
- **タスク完了後の次ステップ確認**: 「このタスクの後、次は何をしますか」のような確認
- **エスカレーション受領後の再開判断**: エスカレーション報告に対するユーザの回答を受けて作業を再開してよいかの判断
- **複数案からの選択依頼**: セクション 2 の設計判断など、複数案から 1 つを選んでもらう場面

この規則は「質問するかどうか」の判断そのものを変えない (質問する場合の手段のみを規定する)。上記の列挙は質問が発生した場合の適用場面の例示であり、質問が不要な場面 (auto mode の reasonable assumption で前進できる軽微な判断等) で新たに質問を作り出さないこと。

**なぜ**: 自由文での質問はユーザの回答が非構造化になり、選択肢の取りこぼしや誤読が生じやすい。`AskUserQuestion` は選択肢を明示的に構造化するため、回答の解釈が確定的になる。

**例**:
- 悪い例: 「このタスク完了後、次は B 機能に進めてよいですか?」とテキスト応答で尋ねて turn を終える
- 良い例: 同じ確認を `AskUserQuestion` ツールで発行し、「進める」「別タスクに切替える」等の選択肢を構造化して提示する

<!-- rule:tdd-two-phase -->
## 9. TDD 2 段階の開発手順 (R3c)

**適用範囲**: 軽微な修正を除く実装作業全体に適用する。

軽微な修正を除き、実装は同一 PR 内で 2 段階の commit に分けて進める:

1. **Phase A**: テストがある場合は失敗するテスト (red) + 設計骨格を commit し、push して pre-push-review のレビューを通過させ draft PR を作る
2. **Phase B**: 実装本体を commit し、push して PR を ready 化する

**テスト不能な成果物の扱い**: sh スクリプトや markdown のようにテストハーネスを持たない成果物では、Phase A のテストを「設計記述 commit」に置き換える。設計記述 commit は、ファイル構成・スクリプトの入出力契約・ルール ID 一覧などを docs コメントとして含む骨格 (空実装または no-op 実装) で構成する。

**なぜ 2 段階か**: 設計・インタフェースの判断とロジック実装の判断を分離することで、それぞれを独立にレビューできる。Phase A の時点で pre-push-review のレビューを通すことで、設計段階の問題を実装着手前に検出できる。

**詳細手順への参照**: pick-up 分岐・軽微判定 (2 段構え) の具体的な判定基準・Phase A/B の実行コマンド例が必要な場合は `issue-start` skill を参照する。

**例**:
- 悪い例: 新機能の sh スクリプトをテストなしで一度にすべて実装して push する
- 良い例: (テスト可能な成果物なら) 失敗するテストを含む Phase A commit を先に push してレビューを通し、Phase B で実装する commit を追加する。テスト不能な成果物ではファイル構成・I/O 契約を文書化した設計記述 commit を Phase A として先に push する

---

進捗・完了報告はこのセッションのツール結果で裏付けられた事実のみを書く。推測や希望的観測を完了として報告しない。

本文書の規律は、単純な作業での思考量を増やす理由にはならない。一方、設計・デバッグ・レビュー等の多段推論を要する問題では、本文書との関わりの有無に依らず、応答前に問題を段階的に考え抜くこと。
