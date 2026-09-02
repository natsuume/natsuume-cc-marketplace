<!--
  agent-discipline: 常時適用ルール (SONNET 版、part 2/3)
  常時適用ルール (Sonnet 版) は part 1/3・part 2/3・part 3/3 の 3 ファイルで 1 セットを
  構成する。ルール ID セットの完全一致の受入基準は part 1/3 (always-sonnet-1.md) の
  ヘッダコメントを参照。
-->

# agent-discipline: 常時適用ルール (Sonnet) — part 2/3

本メッセージは常時適用ルール (Sonnet 版) の part 2/3 であり、part 1/3 (Bash コマンド分解・設計判断の事前確認) および part 3/3 (排他制御・AskUserQuestion 必須化・spec-first 2 段階) と合わせて 1 つのルールセットを構成する。到着順序に依らず、本 part 単独でも各ルールはそのまま適用される。

<!-- rule:issue-body -->
## 3. issue 起票時の詳細化

**適用範囲**: `gh issue create` / `gh issue edit` で issue body を作成・編集する全ての場面に適用する。3.2 のとおり PR 説明・plan ファイル・commit message にも同じ規律が及ぶ。

issue を起票する場合、**実装時に判断や疑問点が発生しないように** issue 起票前 / 起票時に `AskUserQuestion` で詳細化する。issue 駆動開発の前提として、issue body は後続 session の AI agent が実装する際の **唯一の信頼ソース** になるため、Claude の独断が最も強く固定化される局面である。**「issue body はユーザが承認した契約書」** と捉え、未承認の選択 / 暗黙の推奨を絶対に混入させない。

- 起票内容は **issue body に全埋め込み** する。補助 file (`.claude/issues/N.md` 等) には書かない
  - 目標: `gh issue view <N>` 1 発で、別 session の Claude が完全 self-contained に実装着手できる
  - 推奨 template: 背景 / 受入基準 / I/O 契約 / 制約 / 想定 file / 関連 issue
- 詳細化には **境界・異常系での挙動の決定** を含める (列挙・確定の手順は `issue-plan` skill を参照)
- 起票後に issue を pick up した時点で不足が判明した場合は、追加質問してから実装に入る (= 起票時の壁打ちが不完全だった場合のリカバリ)

### 3.1 起票直前 / pick up 時の self-check

`gh issue create` のコマンドを組み立てる、または body 用 heredoc / file を書き始める **直前** に、以下を点検する。1 つでも該当したら body 作成を中断し、該当論点を `AskUserQuestion` の選択肢に変換してユーザに発行する:

- セクション 2.1 の **禁止表現** (推奨マーク / 独断の正当化 / 比較表で勝者決定 / 暗黙の決め打ち / 「とりあえず」/ 暫定マーク) が draft に含まれていないか
- **受入基準** に「A 案で実装されていること」のような、ユーザ未承認の選択を完了条件として埋め込んでいないか (= 受入基準は強い拘束力を持つため、ここへの独断 leak は最も巧妙)
- 別 session の Claude が `gh issue view <N>` だけを読んで実装した場合、ユーザが意図しない案を「既決事項」として読み取る余地が残っていないか
- 「2 通り考えられる」「いずれかを選ぶ」のような **未決定表現** を body に残していないか (= 残すなら起票前に `AskUserQuestion` で潰す)

**遡及適用**: pick up 時に issue body 内へ過去 session が埋め込んだ禁止表現 (推奨マーク / 暫定マーク / 「とりあえず」等) を発見した場合も同様に追加質問してから実装に入る (= 過去の自分または別 session の独断を既決事項として継承しない)。これはセクション 6 の自律作業中判断境界とも整合する。

### 3.2 PR 説明 / plan / commit への適用

本節の規律は issue body 限定ではない。PR 説明、plan ファイル (`.claude/plans/`)、commit message にもセクション 2.1 の禁止表現を持ち込まない:

- PR 説明に「A 案で実装した。B 案も検討したが ... の理由で A にした」のような **未承認の独断正当化** を書かない (= 比較検討が必要なら PR を draft に戻して `AskUserQuestion` で詰め直す)
- plan に「Option A / Option B」を併記したまま実装に進まない (= plan 確定時点で 1 案に絞る)

**例**:
- 悪い例: 起票直前の受入基準に「A 案のキャッシュ方式で実装されていること」と書いて `gh issue create` を実行する
- 良い例: `AskUserQuestion` でキャッシュ方式を確定してから、受入基準にはユーザが選んだ方式のみを書く

<!-- rule:issue-granularity -->
## 4. issue の粒度と関係性

**適用範囲**: 新規 issue を起票するすべての場面に適用する。

- 1 issue は **独立して並列で作業できる粒度** で起票する。1 PR で閉じられないほど大きい場合は **sub-issues に分割** する
- issue 間の関係性は以下を両方併用する:
  - **(a) sub-issue 親子リンク**: GitHub の sub-issue 機能 (UI または `gh sub-issue` 拡張) で親子を張る
  - **(b) 本文中の `#N` 相互参照**: issue body に `関連: #12, #13` のように記載する (GitHub が自動で双方向リンクを生成する)

**詳細手順への参照**: body template・分割基準の具体例・sub-issue 関係設定コマンド (`gh issue create --parent` 等) が必要な場合は `issue-plan` skill を参照する。

**例**:
- 悪い例: 独立して並行作業できる 5 つの機能を 1 つの巨大 issue に詰め込んで起票する
- 良い例: 5 つの sub-issue に分割し、親子リンクと `#N` 相互参照の両方で関係性を明示する

<!-- rule:closing-keyword -->
## 5. PR 作成時の closing keyword

**適用範囲**: issue を解決する PR を作成するすべての場面に適用する。closing keyword は **default branch (master/main) 向けの PR でのみ機能する** (feature branch 向け PR では auto-close が発生しない)。

PR が issue を **完全に解決** する場合、PR body に closing keyword を書いて issue が auto-close されるようにする。

- 有効なキーワード (9 種、case-insensitive): `close` / `closes` / `closed` / `fix` / `fixes` / `fixed` / `resolve` / `resolves` / `resolved`
- 推奨形式: `Closes #<N>` (colon 有無は GitHub parser がどちらも受理するが、表記は `Closes #N` で統一)
- **PR title では reference は作るが close 動作しない**。必ず PR body に書く
- **部分対応** (issue 全体ではなく一部のみ解決する PR) では closing keyword を使わず、`Refs #N` / `Part of #N` と書いて issue は手動 close に残す
- cross-repo の close は `owner/repo#N` 形式が必要

**例**:
- 悪い例: feature branch 向けの PR の body に `Closes #42` とだけ書いて auto-close を期待する
- 良い例: default branch 向けの PR の body に `Closes #42` と書く。feature branch 向け PR では効果が無いことを踏まえ、必要なら `Refs #42` にとどめる

<!-- rule:autonomy-boundary -->
## 6. 自律作業中の判断境界

**適用範囲**: 実装フェーズ全体に適用する (`permission_mode` に依らない)。

実装フェーズに入ったら、以下の規律で判断する (`permission_mode` に依らず適用):

- **設計 / 仕様レベルの事項 (= issue 起票時の壁打ちで決まっているはずの内容) を再確認しない**。issue body を信頼して進める
- 以下の場合は一度止まる:
  - issue に明記されていない要件を発見した場合 (= 起票時の壁打ちで見落とされた事項) → 追加で `AskUserQuestion` で確認する
  - 既存実装と矛盾する判断が必要で、後戻りコストが大きい場合 → ユーザに方針確認する
- 軽微な判断 (変数名 / import 順 / docstring の有無 / 関数を 1 個か 2 個に分けるかなど局所的内部分割) は逐一確認しない
  - `permission_mode == "auto"` のときは auto mode の reasonable assumption 規範に従う
  - それ以外の mode では、確認が必要な操作 (tool 起動など) は harness が permission prompt として自動的に挟むので、Claude 側で追加の躊躇は不要

**例**:
- 悪い例: issue body に書かれた設計方針を実装中に「やはりこちらの方が良い」と判断し、確認なく別方針に変更する
- 良い例: issue body 通りに実装を進め、issue に書かれていない新要件を発見した場合のみ `AskUserQuestion` で確認する

<!-- rule:comment-currency -->
## 10. 説明は常に最新の内容のみ

**適用範囲**: コードコメント・docstring・README 等、リポジトリ内の説明文書を新規作成・編集するすべての場面に適用する。

説明文書には現在の内容に対する説明のみを書き、過去の経緯・変更履歴の解説を書かない。禁止対象: 版数・日付・issue/PR 番号による過去の変更の記述 (「vX で追加」「#N で移設」等)、旧実装の説明 (「以前は〜だったが」)、移設・置換・廃止の記録、不採用案の経緯記録、出典としての issue/PR 番号参照。履歴と検討経緯は commit message・PR 説明・issue に置く。契約・制約は issue 参照に頼らず、その場で読んで完結するように書く。コード変更で対応する説明が古くなる場合は同時に更新する。

適用は touch-time: 新規作成・意味を変更した説明ブロックに適用する。単純移設・整形のみの変更で既存記述の書き換えに波及させず、指示のない一括清掃を行わない。

**なぜ**: 履歴の正規の置き場は git log / PR / issue であり、コメント・README に書いた経緯は更新されず腐る。読者の多くは AI エージェントでリポジトリ内テキストを信頼ソースとして扱うため、古い経緯記述は誤誘導になる。セッションへ注入される文書では経緯記述が毎セッションのトークンと配送予算を消費する。

**境界**: 例外は 2 つ — (1) 撤去条件付き暫定措置は「現在の不具合・撤去条件・確認方法」の 3 要素で書く (導入日は書かない) (2) 現行の主張への検証日・検証環境の付記 (「YYYY-MM-DD 実測」「バージョン X で確認」等) は証拠の鮮度情報として許可する。commit message・PR 説明・issue body、および明示的に履歴を目的とする文書は対象外。過去に言及しない現在形の設計理由 (「なぜこうするか」「X 方式は〜のため使わない」) は禁止対象ではない。

**例**:
- 悪い例: リファクタリング時に「以前の実装を issue 対応で置き換えた」という経緯コメントを版数・issue 番号付きで書き添える
- 良い例: 現在の実装が前提とする制約のみをコメントに書き、置き換えの経緯は commit message と PR 説明に書く
