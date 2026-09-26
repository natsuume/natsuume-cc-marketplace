# agent-discipline プラグイン

Claude Code の振る舞い規律 (= agent としての discipline) を配送する system prompt plugin です。規律はメインセッションのモデルに依らず、同じ内容を注入します。

## バージョン

v3.0.6
## 概要

Claude Code に「個人の開発スタイル」を一括で適用するための plugin です。機能ごとに別 plugin に分けず、1 plugin 内に複数のルール群を集約することで、個人 marketplace の plugin 数肥大化を抑えます。

次の表は Claude Code 側の配送設計です。

| レイヤ | 配送経路 | inject 条件 | 内容 |
|---|---|---|---|
| **物理層 (Bash 分解)** | `SessionStart` (inject-always.sh、`always-1.md` の part 1/3) | 常時 | Bash コマンドを最小粒度に分解して PreToolUse hook の取りこぼしを防ぐ |
| **before 系** | `SessionStart` (inject-always.sh、part 1/3: 設計 / 仕様の事前壁打ち) + `UserPromptSubmit` (inject-rules-part.sh 2、part 2/3: issue / PR 関連) | 常時 (part 2/3 は session 内初回のプロンプト処理時) | 設計 / 仕様の事前壁打ち + 「思考は自由、 成果物への固定化は要承認」 非対称ルール (2.1) + 自己検知トリガー / 名指し禁止表現、 issue 起票時の `AskUserQuestion` 詳細化 + 起票直前 / pick up 時の self-check + 過去 session 独断の遡及検出 (3.1 / 3.2 で PR / plan / commit にも適用)、 並列粒度 + sub-issue + `#N` 相互参照、 PR closing keyword 規約 |
| **during 系** | `UserPromptSubmit` (inject-rules-part.sh 2、part 2/3) | 常時 (`permission_mode` 非依存、session 内初回のプロンプト処理時) | 実装は自走、 設計 / 仕様 (= issue 起票時の壁打ちで決まっているはずの内容) の再確認では止まらない。 ただし issue 未明記の要件発見 / 大きな後戻り判断では止まる |
| **排他系** | `UserPromptSubmit` (inject-rules-part.sh 3、part 3/3) | 常時 (`permission_mode` 非依存、session 内初回のプロンプト処理時) | 連続 issue 解決フロー (例: `/goal`) や並列 session 下で同 issue への重複着手を防ぐ。 claim comment の先着判定 (GitHub が付与する `created_at` + 数値 comment id の辞書順) で確保を確定し、 claim comment 本文の `session=<セッションID>` により誰の claim かを識別する (`session=` を持たない claim は自分のものと確認できないため他 session 扱いで削除禁止) |
| **作業手順系** | `UserPromptSubmit` (inject-rules-part.sh 2 / inject-rules-part.sh 3) | 常時 (session 内初回のプロンプト処理時) | ユーザへの質問は `AskUserQuestion` で行う (part 3/3)、 軽微な修正を除き spec-first 2 段階 (Phase A: テスト / 設計骨格 → Phase B: 実装本体) で進める (part 3/3)、 説明文書には現在の内容のみを書き経緯を書かない (part 2/3) |
| **分割配送** | `SessionStart` (inject-always.sh、part 1 のみ) + `UserPromptSubmit` (inject-rules-part.sh × 2 / inject-discipline.sh) | 常時 (UserPromptSubmit 側の各要素は session ごとに at-most-once) | 常時ルールと分業規律は、メインセッションのモデルに依らず同じ 1 版を配送する。SessionStart で常時ルールの part 1 (`always-1.md`) のみ注入し、残りの part (`always-2.md` / `always-3.md`) と分業規律 (`discipline.md`) は UserPromptSubmit の最初のプロンプト処理時に別要素として個別配送する (1 要素の `additionalContext` を 8K 字以下に保つための分割)。UserPromptSubmit 側の各要素は配送済みマーカーで 1 度だけ配送し、SessionStart のたびにマーカーをリセットして再配送する |
| **検知系 (gh issue/pr body)** | `PreToolUse` (hooks.json inline `type: agent` 4 entries) | `gh issue create` / `gh issue edit` / `gh pr create` / `gh pr edit` の literal head にだけ反応し、非該当 Bash では model を起動しない | 誘導層 (before 系 2.1 / 3.1) の禁止表現を semantic 判定し違反時 block。`gh pr create` だけ closing keyword も検証する。claude-sonnet-5 pin |
| **after 系** | `UserPromptSubmit` (inject-auto.sh) | `permission_mode == "auto"` | 変更が一段落したら commit → push → PR 作成 → (4 条件 hard gate を満たしたら) マージまで自走 |

加えて、auto セッションで `UserPromptSubmit` 初回発火時に cwd の未コミット変更を分類確認する独立 hook (`check-uncommitted-on-session-start.sh`) を併走させます。

### モデル分業の前提

メインセッションと、実装・調査・一括修正等のワーカーサブエージェントは Opus 5.5 で動かします。メインセッションが Opus 5.5 なら、ワーカーは model 未指定でメインセッションのモデルを継承させます。メインセッションが Opus 系以外のモデルで動いている場合 (または env 等で継承先が Opus 以外になる場合) は、ワーカーの起動で `model: "opus"` を明示させます。Fable は `cross-model-advisor:fable-advisor-runner` の起動 (`model: "fable"` の明示) にだけ使い、`block-fable-subagent.sh` が Fable 週次枠の使用率で起動を判定します (詳細は「block-fable-subagent」参照)。Sonnet に pin するのは codex 系 runner と検知層の `type: agent` hook のような定型 runner に限ります。

配送する規律 (常時適用ルール・分業規律) は、メインセッションのモデルに依らず同一です。Fable をメインセッションのモデルとする構成は想定していません。その場合も同じ規律を配送し、Fable サブエージェントの起動はメインセッションのモデルに依らず上記の判定だけで扱います。

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install agent-discipline@natsuume-plugins
```

本プラグインは Claude Code 専用で、Codex marketplace では配布していません。

## 機能一覧

### Hooks

#### inject-subagent-rules

**ファイル**: `hooks/scripts/inject-subagent-rules.sh`
**イベント**: `SubagentStart` (Claude Code 2.0.43+)

**動作**:

- 全 subagent の起動時に `hooks/prompts/subagent-rules.md` 全文を `additionalContext` として注入する。モデル判定・agent_type 分岐を持たない静的全文注入
- 注入内容は 5 規律: bash-decompose (always-1.md と同一 rule ID。subagent の Bash もメインセッションと同じ PreToolUse hook を通るため) / 報告の事実性 / 副作用操作の default-deny / エスカレーション定型 (発動条件 4 点 + 返却フォーマット 5 点) / 説明は常に最新の内容のみ (always-2.md と同一 rule ID の comment-currency。subagent も説明文書を編集するため)
- `jq` 不在 / prompt ファイル欠落・空の場合は無音 `exit 0` (フェイルセーフ)。subagent-rules.md の rule ID 整合は `lint-prompt-sync.sh` チェック 5 (サブセット検査) が CI で担保する

#### inject-always

**ファイル**: `hooks/scripts/inject-always.sh`
**イベント**: `SessionStart`

**動作**:

- 常時ルールの part 1/3 (`always-1.md`) を、配送メモと組み合わせて `additionalContext` として注入する。組み立て順は、自己修復指示 (persisted-output として退避された場合に退避ファイルを Read で全文読了させる 1 文、スクリプト内定数 `SELF_HEAL`) + 空行 + `delivery-note.md` + 改行 + 実行時に解決した prompts ディレクトリの絶対パス 1 行 (`(参照パス) <prompts dir>`) + 空行 + `always-1.md`
- 注入内容はメインセッションのモデルに依らず同一である。stdin (hook input JSON) から読むのは `hook_event_name` と `session_id` のみで、`.model` と `transcript_path` は読まない
- 残りの part (`always-2.md` / `always-3.md`) と分業規律 (`discipline.md`) は本スクリプトの責務外で、UserPromptSubmit の `inject-rules-part.sh` / `inject-discipline.sh` が個別要素として配送する
- **配送済みマーカーのリセット**: `session_id` が非空なら、UserPromptSubmit 側の配送済みマーカー (`${TMPDIR:-/tmp}/agent-discipline-state/` 配下の `delivered-rules-2-<sid>` / `delivered-rules-3-<sid>` / `delivered-discipline-<sid>`) を SessionStart のたびに無条件で削除する (`resume` / `clear` / `compact` でも全要素を再配送するため)
- **8K ガード**: additionalContext を組み立てた後の全文文字数を計測し、8,000 字を超える場合は (i) 実パス行を落として再計測 → (ii) それでも超える場合は delivery-note 全体を落として再計測、の順で段階的に縮退する。自己修復指示とルール本文 (`always-1.md`) はいかなる場合も落とさない
- `SessionStart` は `startup` 以外に `resume` / `clear` / `compact` でも発火するため同一セッション内で複数回呼ばれる可能性があるが、注入内容は static なので重複しても害は無い (毎回コンテキストトークンを再消費する点に留意)
- 入力 JSON から `hook_event_name` を読み取り `hookSpecificOutput.hookEventName` に同じ値を設定 (誤った既定値で別 event の文脈に誘導しないため)
- `jq` 不在 / 不正 JSON 入力 / `hook_event_name` が空 / `always-1.md` が読めない・空の場合はすべて無音 `exit 0` (フェイルセーフ)。`delivery-note.md` が読めない場合は delivery-note 無しで part 1 本文のみ注入する (ペイロード単位の fail-open)

**注入内容の要約** (`always-{1,2,3}.md` の和集合のルール ID、書式は「常時適用ルールの書式」参照。part 1 = ルール 1〜2、part 2 = ルール 3〜6 と 10、part 3 = ルール 7〜9):

1. **Bash コマンド分解** (物理層、`rule:bash-decompose`): `&&` / `||` / `;` / `&` / `$(...)` / バッククォート / `eval` / `sh -c` / `xargs` / `find -exec` を分解対象、パイプライン `|` は単一論理操作のみ許容、`cd $dir && cmd` やトランザクション的合成は例外
2. **設計 / 仕様検討の事前明確化** (`rule:design-approval`): スコープ / 要件 / 受入基準 / I/O 契約 / 公開命名などの後戻りコストが大きい判断は `AskUserQuestion` で事前に詰める。軽微な実装判断は対象外。「思考は自由、成果物への固定化は要承認」非対称ルール: 検討段階での複数案比較・推奨思考は許容するが、結論を issue body / PR 説明 / plan / commit に書き出す前に必ず `AskUserQuestion` を通す
3. **issue 起票時の詳細化** (`rule:issue-body`): 実装時に判断が発生しないよう `AskUserQuestion` で詳細化。起票内容は **issue body に全埋め込み** (補助 file には書かない)。issue body は「ユーザが承認した契約書」と捉え、起票直前 / pick up 時の self-check + 遡及適用で独断 leak を塞ぐ。PR 説明 / plan / commit にも同じ禁止表現規律を拡張
4. **issue の粒度と関係性** (`rule:issue-granularity`): 独立して並列作業できる粒度で起票、大きい場合は sub-issues 分割。関係性は (a) sub-issue 親子リンク + (b) `#N` 相互参照を併用
5. **PR 作成時の closing keyword** (`rule:closing-keyword`): 完全解決時のみ PR body に `Closes #N` を書く。closing keyword は default branch 向け PR でのみ機能する。部分対応では `Refs #N` / `Part of #N` に切替
6. **自律作業中の判断境界** (`rule:autonomy-boundary`): 実装は自走、設計 / 仕様 (= issue で決まっているはずの内容) は再確認しない。ただし issue 未明記の要件発見 / 大きな後戻り判断では止まる
7. **連続 issue 解決時の排他制御** (`rule:issue-claim`): `/goal` 等の並列 session フロー向け。(a) `gh issue view` で `ai:in-progress` ラベル / claim comment 早期判定、(b) claim comment 投稿 (`session=<セッションID>` で自他判別)、(c) 3 秒待機 + REST issue comments の全ページ再取得 + `(created_at, 数値 id)` の辞書順比較による先着判定 (取得失敗・自分の claim が無い場合は停止する fail-closed)、(d) 先着と確認できた時点で確保を確定してラベル付与、(e) 作業 branch の用意 (同名 branch が無ければ最新の default branch から作成、あれば switch して再開し、local と remote が分岐していれば停止して報告)。ユーザのメッセージまたは handoff の文書が issue 番号か branch 名を挙げて継続を指示した場合 (明示指示) だけ、(a)〜(d) を経ずに (e) から再開する。撤退時の後片付けは自分の claim comment の削除と 1 行報告だけで、確保後の着手中断では自分の claim comment だけを削除し、branch・draft PR・ラベルは再開のために残す。安全機構のため手順を省略せず全文記載する
8. **AskUserQuestion の必須化** (`rule:ask-user-question`): ユーザへの質問・確認・判断伺い・すり合わせは自由文で turn を終えず必ず `AskUserQuestion` を発行する
9. **spec-first 2 段階の開発手順** (`rule:tdd-two-phase`): 軽微な修正を除き、実装は Phase A (テストがある場合は失敗するテスト + 設計骨格、テスト不能な成果物では設計記述 commit に置換) → pre-push-review のレビュー通過 → draft PR → Phase B (実装本体) → ready 化、の 2 段階で進める。正典 TDD ではなく実行可能仕様の先行固定 (spec-first) であり、局所定義・評価基準の詳細は issue-start skill が持つ
10. **説明は常に最新の内容のみ** (`rule:comment-currency`、part 2/3 に含まれる): コードコメント・docstring・README 等の説明文書には現在の内容のみを書き、版数・issue/PR 番号による過去の変更の記述や旧実装の説明を書かない。履歴は commit message・PR 説明・issue に置く。新規作成・意味変更した説明ブロックにだけ適用し (touch-time)、指示のない一括清掃は行わない

**常時適用ルールの書式**:

- `always-1.md` / `always-2.md` / `always-3.md`: 各ルールに適用範囲を明示し、否定形の指示には具体的な代替行動を併記する。ルールごとに良い例 / 悪い例を最小 1 セット添える。禁止表現 8 カテゴリは列挙を維持する。part 3/3 (`always-3.md`) の末尾に「本文書の規律は、単純な作業での思考量を増やす理由にはならない」の steering 文を置く。1 つのルールセットを rule 境界で 3 part に分割したもので、part 間で rule ID は重複しない。各 part の見出しは `# agent-discipline: 常時適用ルール — part <N>/3` で、到着順序に依らず各 part を self-contained に適用する旨を part 1 の冒頭に置く
- `rule:issue-claim` (連続 issue 解決時の排他制御、part 3/3 に含まれる) は、安全機構のため手順本体を省略せず完全記載する

#### inject-temporary

**ファイル**: `hooks/scripts/inject-temporary.sh`
**イベント**: `SessionStart` / `UserPromptSubmit`

**動作**:

- `SessionStart` では `hooks/prompts/temporary/*.md` の非空ファイルをファイル名の辞書順 (`LC_ALL=C`) で連結し、1 つの `additionalContext` として全件配送する。同時にファイル名の POSIX `cksum` (CRC + byte length) を session ごとの配送済み集合 `${TMPDIR:-/tmp}/agent-discipline-state/delivered-temporary-<session_id>` へ atomic に記録する
- `UserPromptSubmit` では配送済み集合に無い非空 md だけを同じ順序で連結し、追加後の最初のプロンプト処理時に one-shot 配送する。配送済み集合は本文 hash ではなくファイル名単位なので、temporary rule の lifecycle はファイル追加・削除で管理する
- SessionStart の全件配送は resume / clear / compact を含めて維持し、その時点の存在ファイルで配送済み集合を置き換える。temporary directory が空なら空集合を記録するため、その後に同名ファイルが追加されても次の UserPromptSubmit で配送できる
- `agent_id` 付き UserPromptSubmit は subagent 経路として無音終了し、配送済み集合も変更しない。`jq` 不在 / 不正 JSON / session_id 不正 / state directory または atomic marker 書込失敗 / 全件配送済み / directory が空の場合も無音 `exit 0` する
- SessionStart input に session_id が無い異常系では、marker 無しで全件配送する。正常系では出力 JSON を先に生成し、marker 更新に成功した後だけ stdout へ出す

撤去手順: Claude Code 側で AskUserQuestion preview のスクロール問題 (一定行数を超える preview が「hidden XX lines」で隠され、ユーザが全文を確認できない) が修正されたら、`hooks/prompts/temporary/` 配下の md を削除するだけで注入が消えます (スクリプトと hooks.json entry は残っても no-op)。修正されたことは、長い preview を付けた AskUserQuestion を表示し、隠れた部分をスクロールで全文確認できることで確かめます。完全撤去する場合のみ entry・スクリプト・temporary ディレクトリも削除します。いずれの場合も version bump が必要です。

#### inject-rules-part

**ファイル**: `hooks/scripts/inject-rules-part.sh`
**イベント**: `UserPromptSubmit`
**引数**: `2` または `3` (part 番号)。hooks.json に `inject-rules-part.sh 2` / `inject-rules-part.sh 3` の 2 entries で登録する。引数が `2` / `3` 以外、または欠落の場合は無音 `exit 0`

**動作**:

- 常時ルールの part 2/3 または part 3/3 (`always-2.md` / `always-3.md`) の本文そのものを、at-most-once で個別要素の `additionalContext` として配送する。注入内容はメインセッションのモデルに依らず同一である
- マーカー `${TMPDIR:-/tmp}/agent-discipline-state/delivered-rules-<n>-<session_id>` が存在すれば即 `exit 0` (毎プロンプトのオーバーヘッドをファイル存在チェックのみに抑える)
- マーカー不在時は `always-<n>.md` を注入し、出力 JSON の生成に成功した後でマーカーを書く (先にマーカーを書くと、本文読取失敗時に当該要素が session 中永久欠落する)。マーカーの書込は同一ディレクトリ内 temp file → `mv` の atomic 書込にする
- `jq` 不在 / 不正 JSON 入力 / `hook_event_name` か `session_id` が空の場合は無音 `exit 0`。`always-<n>.md` が読めない・空の場合も無音 `exit 0` でマーカーは書かない (次プロンプトで再試行)

#### inject-discipline

**ファイル**: `hooks/scripts/inject-discipline.sh`
**イベント**: `UserPromptSubmit`

**動作**:

- 分業規律を at-most-once で独立要素として配送する。注入内容は見出し `# agent-discipline: 分業規律` + 空行 + `discipline.md` の本文で、メインセッションのモデルに依らず同一である
- マーカー `${TMPDIR:-/tmp}/agent-discipline-state/delivered-discipline-<session_id>` が存在すれば (内容は問わない) 即 `exit 0`
- マーカー不在時は分業規律を注入し、出力 JSON の生成に成功した後でマーカー (内容 `delivered`) を書く。マーカーの書込は同一ディレクトリ内 temp file → `mv` の atomic 書込にする
- `jq` 不在 / 不正 JSON 入力 / `hook_event_name` か `session_id` が空の場合は無音 `exit 0`。`discipline.md` が読めない・空の場合も無音 `exit 0` でマーカーは書かない (次プロンプトで再試行)

#### inject-auto

**ファイル**: `hooks/scripts/inject-auto.sh`
**イベント**: `UserPromptSubmit`

**動作**:

- 入力 JSON から `permission_mode` を読み取り、`"auto"` のときだけ `additionalContext` を出力する
- 正規化ロジックは `hooks/scripts/lib/permission-mode.sh` を `check-uncommitted-on-session-start.sh` と共有し、2 経路の判定 drift を防ぐ
- `hook_event_name` を入力からそのまま読み取り `hookSpecificOutput.hookEventName` に同じ値を設定
- `jq` 不在 / 不正 JSON 入力ではすべて無音 `exit 0` (フェイルセーフ)

**なぜ `UserPromptSubmit` か (SessionStart ではなく)**:

- `permission_mode` の動的判定が必要。 ユーザは session 中に `/permissions` 等で auto を on/off できるが、 SessionStart は session 開始時の値しか見えない。 `UserPromptSubmit` は毎ターン input に最新の `permission_mode` が乗る
- after 系の自走方針は long-running session で薄れると致命的 (= 自走パイプラインが停止する) なので、 per-turn 再注入で方針を維持する

**注入内容の要約**:

- 変更が一段落したら commit → push → PR 作成まで自走、 マージは 4 条件 hard gate を満たした場合のみ独断マージ
  - 4 条件: draft 解除済み / 必須 CI checks 全成功 / 必要な承認あり / `mergeable == MERGEABLE && mergeStateStatus == CLEAN`
- 作業が残っている間の止まり方の禁止 (次の手順の宣言だけで turn を終える / 続行の可否を尋ねる / 残作業を妨げない判断事項の列挙 / 区切りや turn の長さを理由にした報告)。進捗報告・推奨は次のツール呼び出しと同じメッセージに含める。止まってよいのは、ユーザの入力なしに進められる作業が無い場合と、進行を妨げているものが意図的に保護されたものである場合に限る。この規定は禁止 / 要確認事項やマージ前提条件の確認を不要にしない
- 禁止 / 要確認: master への直接 push / 破壊的操作 / 秘匿情報コミット / 4 条件未充足の独断マージ

#### PreToolUse type:agent hook

**定義場所**: `hooks/hooks.json` 内に inline 定義 (= 外部スクリプト不要、 prompt 全文を JSON 内に持つ)
**イベント**: `PreToolUse`
**matcher**: `Bash`
**`if` filter**: `Bash(gh issue create:*)` / `Bash(gh issue edit:*)` / `Bash(gh pr create:*)` / `Bash(gh pr edit:*)` の **4 entries に分割**
**model**: `claude-sonnet-5` に pin (メインセッションのモデルとは独立に、コストと応答時間を抑えるため。詳細は下記「SPOF 緩和の設計」参照)
**timeout**: 60 秒 (公式 default)

**動作**:

- 各 hook の `if` field で target command に反応する prefilter を構成する。 `if` filter は best-effort であり、 compound command の各 subcommand と env-prefix を剥がした command は正規に評価される一方、 `$()` / バッククォート / `$VAR` を含む Bash では非対象 (= `ls` / `git status` / `rg` / `gh issue view` など) でも agent subagent が起動しうる。 非対象 Bash で起動した場合は **Step 0 の defense-in-depth command guard** が semantic 検証をせず即 `{"ok": true}` を返して影響を最小化する
- Step 0 はまず command 全体を見て、 shell が実際に実行する `$()` / バッククォートの中で command として実行される対象 command と、 command 語の位置で引用符に分断された literal (`"gh" issue create` 等) を、 body を静的に判定できないため `{"ok": false}` で block する。 single quote の内側や `<<'EOF'` heredoc 本文の中の言及は shell が実行しないため対象外とする。 それ以外は行末の `\` による行継続を連結してから command を subcommand に分割し、 各 subcommand の env-prefix (`VAR=value`) と wrapper (`command` / `env` / `sudo`) を剥がした後、 当該 hook entry の `gh <cmd>` literal で始まる subcommand を検証対象とする (複数あればすべて検証する)
- Step 0 を通過した場合、 prompt 内で body content を抽出する:
  - `--body 'inline string'` / `--body "inline string"` (heredoc 含む) → inline 文字列を body content とする
  - `--body-file PATH` → Read tool で PATH のファイル内容を取得 (= `type: agent` を採用した直接の理由)
  - どちらも無い (= editor 起動経路) / `--body-file -` (stdin) → その subcommand は判定不能として以降の検証をスキップし、 残りの対象 subcommand の検証を続ける (= 誘導層に委ねる)。 すべての対象 subcommand が通過またはスキップになった場合に `{"ok": true}` となる
- body content に対し、 inject-always.sh セクション 2.1 / 3.1 の禁止カテゴリ (推奨マーキング / 独断の正当化 / 比較表で勝者決定 / 暗黙の決め打ち = 粒度差 / 「とりあえず」 系 / 暫定マーク残置 / ユーザ判断の先回り代弁 / 受入基準への未承認選択埋め込み) を semantic 判定
- 該当なし → `{"ok": true}`、 該当あり → `{"ok": false, "reason": "違反箇所の引用 + カテゴリ名 + 修正方針 (= AskUserQuestion でユーザの decision を取り、 確定 1 案だけを残す)"}` で block

**Closes 検証 Step (`gh pr create` entry のみ)**:

`gh pr create` entry の prompt にのみ、 上記 Step 2 (禁止カテゴリの semantic 判定) の直後に追加の判定 Step 3 を置き、 返り値を Step 4 とする。 他 3 entries (`gh issue create` / `gh issue edit` / `gh pr edit`) の prompt はこの Step を持たない。 branch 名からの issue 推定は PR 作成時にのみ意味を持つ判定のため、 4 entries の prompt 完全 duplicate は維持しつつ本 Step だけ 1 entry に閉じる (= entry を増やさず model pin の保守対象も増やさない)。

判定の起点 `<cwd>` は、 hook input の `cwd` を起点に、 同じ command 内で対象 subcommand より前にある `cd <dir>` の subcommand を先頭から順にすべて適用した dir (各 `<dir>` が相対パスならその時点の dir を基準に解決する) とする (`cd` が無ければ hook input の `cwd`)。

判定手順:

1. まず `<cwd>/.git` を Read tool で読む
2. 読み取れた内容が `gitdir: <path>` 形式 (worktree) の場合: `<path>` が相対パスであれば、 `.git` ファイルの所在ディレクトリ (= `<cwd>` そのもの) を基準に解決したうえで、 解決後の `<path>/HEAD` を Read tool で読む (linked worktree では `.git` 自体が `gitdir:` ファイルであり、 `<cwd>/.git/HEAD` を先に読むと常に読み取れず fail-open するため、 `.git` を先に読んでから分岐する)
3. `<cwd>/.git` の Read が「ディレクトリである」ことを理由に失敗する場合 (= worktree ではない通常のリポジトリ): `<cwd>/.git/HEAD` を Read tool で読む
4. 上記いずれの経路でも HEAD が取得できない場合、 または取得できた内容が `ref: refs/heads/<branch>` 形式でない場合 (detached HEAD 等) は、 本 Step を判定不能として通過する (fail-open で誘導層の `rule:closing-keyword` に委ねる)。 `.git` 自体が存在しない bare リポジトリも本 Step の対象外として同様に通過する
5. branch 名が `*/issue-<数字>-*` パターンに一致しない場合は本 Step を通過する
6. 一致する場合、 パターンから issue 番号 `N` を抽出する。 branch 名に `issue-<数字>-` 形式の断片が複数含まれる場合は、 **最初に出現した断片の数字** を `N` として採用する (branch 名規約 `<prefix>/issue-<N>-<slug>` では prefix 直後の先頭断片が規約上の issue 番号。 例: `feat/issue-12-fix-issue-34-regression` では N=12)。 Step 1 で抽出済みの body content に、 以下のいずれかが `N` そのものを参照している場合のみ本 Step を通過する (境界一致で判定する: `#12` は `#123` にマッチしない、 すなわち `#N` の直後が数字でないことを確認する。 先頭ゼロの同一視はしない。 branch 名規約は issue 番号をそのまま埋めるため通常は先頭ゼロが発生しないが、 発生した場合は不一致として block 側に倒す):
   - closing keyword (`Closes` / `Close` / `Closed` / `Fix` / `Fixes` / `Fixed` / `Resolve` / `Resolves` / `Resolved`、 case-insensitive、 colon 許容 = `Closes:` 等も可) + `#N` または `owner/repo#N`
   - 部分対応表記 (`Refs` / `Part of`、 case-insensitive) + `#N` または `owner/repo#N`
7. 上記いずれにも該当しない場合 (= 他 issue への参照のみが併記されている場合を含む) は `{"ok": false, "reason": "branch 名から issue #<N> の作業と推定されるが、 PR body に issue #<N> を参照する closing keyword (例: Closes #<N>) も部分対応表記 (例: Refs #<N>) も無い。 完全解決なら Closes #<N> を、 部分対応なら Refs #<N> を body に追記して再実行する"}` で block する (reason 内の `<N>` は Step 6 で抽出した実際の issue 番号に置換する)。 該当する場合は Step 4 に進む

editor 経路 / `--body-file -` (stdin 経路) は Step 1 の扱いのまま判定不能として通過する (= body content 自体が取得できないケースを本 Step が追加で救済することはない)。 worktree (相対 `gitdir:` の解決を含む) / detached HEAD / bare リポジトリ (対象外で通過) / branch 名不一致 / issue 番号一致の keyword あり / 番号不一致または keyword なし の各ケースについて、 上記手順から期待判定 (通過多数 + block は「番号一致の keyword が body に無い」場合のみ) が一意に導ける設計としている。

**なぜ 4 entries に分けて prompt を duplicate しているか**:

- `if` field は単一 command pattern (`Bash(prefix:*)` 形式) のみで、 alternation (`Bash(gh (issue|pr) (create|edit):*)`) は公式 syntax では非対応
- 1 entry に `if: "Bash(gh issue:*)"` のような broader filter を置くと、 `gh issue view` / `gh issue list` / `gh issue close` 等にも agent が起動して narrow scope が損なわれる
- 4 つの target command (`create` / `edit` × `issue` / `pr`) ごとに個別 entry を持ち、 prompt は 4× 完全 duplicate という maintenance トレードオフを受け入れる代わりに、 真の narrow scope (= `if` filter が target command にだけ反応するよう hook config 段階で prefilter し、 `$()` / `$VAR` 等を含むために `if` filter が通した非対象 Bash は Step 0 が即終了する) を確保している
- prompt 更新時は 4 箇所を同期する。4 entries の共通ブロックの一致は `lint-prompt-sync.sh` のチェック 2 が CI で検査する

**なぜ `type: agent` か (vs `type: prompt`)**:

- `--body-file PATH` 形式では PATH のファイル内容を読み取らないと判定できない。 `type: prompt` はツール使用不可なので Read できず、 `--body-file` 経路を取りこぼす
- 一方、 既存の `block-commit-lint` plugin が PR body に `--body-file` 経路を強制している repo policy のため、 prompt hook 単独だと PR 作成経路で必ず取りこぼす穴になる
- 公式の使い分け規範 ("Use prompt hooks when the hook input data alone is enough to make a decision. Use agent hooks when you need to verify something against the actual state of the codebase.") に照らすと、 `--body-file` で参照されるファイル状態を verify する用途は agent hooks が first choice

**SPOF 緩和の設計**:

- 全 Bash で発火する LLM hook は、 hook の model が不可用になると全 Bash を PreToolUse error にする非対称 SPOF を持つ
- 検知層はこれを 2 段で緩和する:
  - **narrow scope (物理層 + Step 0)**: 個別 hook の `if: "Bash(gh <cmd>:*)"` filter で target command に反応するよう **hook config 段階で** prefilter する。 `if` filter は best-effort のため、 `$()` / `$VAR` を含む非対象 Bash でも agent subagent が起動しうるが、 その場合は Step 0 guard が semantic 検証をせず即終了する。 結果として LLM 不可用時の影響は「`gh issue/pr create/edit` に加えて、 `$()` / `$VAR` を含む Bash も PreToolUse error になりうる」 範囲に narrow され、 それ以外の通常の Bash 呼び出し (= `ls` / `git status` / `rg` 等) は影響を受けない
  - **model pin**: `model` field を明示的に `claude-sonnet-5` に固定する。hooks.json の `type: agent` hook の `model` field は `CLAUDE_CODE_SUBAGENT_MODEL` env var の影響を受けず、pin 値がそのまま dispatch される確定値である。検知層は body の禁止表現を判定する定型作業のため、メインセッションのモデルとは独立に、コストと応答時間を抑えるため sonnet に pin する

これにより LLM 不可用の影響は「`gh issue/pr create|edit` (と `$()` / `$VAR` を含む Bash) が pin 先の Sonnet 障害時に PreToolUse error になる」範囲に閉じる。メインセッションは Sonnet 以外のモデルで動くため、Sonnet 側の障害時は hook だけが落ちうる。個別 call の transient error (rate limit / network blip) は残るが、これは Claude Code 通常使用の背景ノイズと同レベル

**`if` filter と prompt 内 guard の分担**:

prompt 内の early return (「対象 command 以外は即 ok:true」) だけで絞り込む構成は採らない。 prompt 内の early return は agent subagent が **既に起動済み** の状態で起こるため、 matcher `Bash` のみの単一 entry では全 Bash 呼び出しで subagent が起動して narrow blast radius が成立せず、 ordinary commands (tests / git status / rg 等) に latency / cost / model 可用性への依存が生じる。 このため hook config 段階で物理 prefilter する `if` field (公式 plugin `claude-plugins-official/security-guidance` と同じ syntax) を使い、 4 entries に分割している。

各 prompt 冒頭には **defense-in-depth command guard (Step 0)** を置いている。 `if` filter は best-effort であり、 `$()` / `$VAR` を含む Bash では対象外でも hook が起動しうるため、 unrelated command が semantic 検証されて誤 block されないよう、 prompt 内 guard が二段目として subcommand 単位で検証対象を決める。 `if` filter が通した非対象 Bash は即 `{"ok": true}` で通し、 静的判定不能な形 (command 置換内の対象 command、 引用符で分断された literal) は `{"ok": false}` で受け止める。 第一の narrow scope は `if` field の hook config 段階で、 prompt 内 guard が二段目を担う非対称設計

**fail-closed の原則**:

- 違反疑い検出時は `{"ok": false}` で block を返す。 silent pass は構造的に不可逆 (= 後続 session が既決事項として読む leak が成立) なため、 false positive (= 正当な記述を誤って block) の方が recovery 可能であり、 fail-closed が論理的に正しい
- block された Claude は reason を読み、 AskUserQuestion でユーザの decision を取り、 確定した 1 案だけを body に残して再試行する

#### block-fable-subagent

**ファイル**: `hooks/scripts/block-fable-subagent.sh`
**イベント**: `PreToolUse`
**matcher**: `Agent|Task`

Fable サブエージェントは、`model: "fable"` を明示し、かつ Fable 週次枠の使用率が閾値以下の場合に限り許可します (cross-model-advisor の fable-advisor-runner を Fable で起動するため)。用途を advisor に限る規律は分業規律 (`discipline.md`) が担い、本 hook は許可 agent の一覧を持ちません。メインセッションのモデルは判定に使いません。

fork サブエージェントを止める主防御は、利用者の settings (`~/.claude/settings.json` 等) に置く `permissions.deny` の rule です。本 hook はそれを補う二重防御で、permission rule が捕捉しない経路 (サブエージェント内からの継承・env による上書き) の検知と、deny メッセージによる自己修正誘導を担います。

```json
{
  "permissions": {
    "deny": ["Agent(fork)"]
  }
}
```

`Agent(model:fable)` は permission rule に置かないでください。置くと `model: "fable"` の明示がすべて止まり、週次枠判定付きの許可経路も使えなくなります。`Agent(model:fable)` を設定済みの場合は削除してください。

**動作**:

- Claude Code のモデル解決順序は 明示 `model` > agent 定義の frontmatter > `CLAUDE_CODE_SUBAGENT_MODEL` > メインセッション継承 で、`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` (`1` / `true`) が設定されている場合のみ env (未設定ならメインセッションのモデル) が全てを上書きする。`subagent_type` が `fork` のサブエージェントは model 指定にも env にも依らずメインセッションのモデルを継承する。本 hook はこの順序に沿って上から判定し、すべて deterministic な文字列判定で行う (LLM 評価は使わない)
  1. `fork` → サブエージェント内 (入力に `agent_id` がある) からの起動なら deny (nested guard、下記)。メインセッションからの起動なら allow
  2. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` が有効 → 実効モデルは env (非空ならその値、空ならメインセッションのモデル)。env が fable なら model の明示に依らず deny し、model の明示では直せないことを deny 理由に書く。それ以外 (env 空を含む) は allow
  3. `tool_input.model` に fable が明示指定されている (alias `fable` / full ID `claude-fable-5-1` 等、大文字小文字を無視した部分一致) → 下記の使用率判定で利用可なら allow、利用不可 (閾値超過・使用率不明) なら deny。超過時の deny 理由には使用率・閾値・reset 時刻 (cache にあれば) を含める。deny 理由では、fable-advisor-runner は再起動せずスキップし、それ以外の委任では非 Fable の model (例: `model: "opus"`) を明示するよう案内する
  4. `tool_input.model` が非 fable の具体指定 → allow (明示は env より優先されるため)
  5. `tool_input.model` 未指定 (= 継承経路): env が非空なら fable のとき deny・それ以外は allow。env 不在でサブエージェント内 (入力に `agent_id` がある) からの起動は deny する (nested guard、下記)。それ以外 (env 不在のメインセッションからの起動) は allow
- **nested guard**: サブエージェント内 (入力に `agent_id` がある) からの model 未指定 (`inherit` を含む)・`fork` の起動は deny し、model の明示を求める。継承先は起動元サブエージェントのモデルになり、週次枠判定を通った Fable サブエージェントの子が判定なしで Fable を継承しうるため。`CLAUDE_CODE_SUBAGENT_MODEL` が非空なら子の実効モデルは env で決まるため env で判定する
- **Fable 週次枠の使用率判定** (3):
  - 入力は natsuume-statusline が書く `${XDG_CACHE_HOME:-$HOME/.cache}/natsuume-statusline/weekly-scoped.json`。本 hook は読むだけで書き込まず、OAuth usage API も呼ばない
  - 閾値は env `FABLE_WEEKLY_MAX_PERCENT` (前後空白を trim した 0〜100 の 10 進整数)。未設定・空・範囲外・非整数は既定値 `80`
  - `weekly_scoped[]` のうち `display_name` が大文字小文字を無視して `fable` を含み `percent` が数値の entry の最大 `percent` で判定し、`percent <= 閾値` なら利用可 (ちょうど閾値は利用可)。比較は小数を扱えるよう jq で行う
  - cache が symlink / 通常ファイルでない / 存在しない / 読めない / JSON document が 1 つでない / `fetched_at` が欠落・非数値 / `now - fetched_at > 1800` (stale) / `weekly_scoped` が欠落・非配列・空 / Fable entry が無い / 現在時刻を取得できない場合は、使用率不明として deny する (fail-closed)。deny 理由では cache の producer (natsuume-statusline) の構成手順を案内する。`fetched_at` が未来時刻でも stale とはみなさない
- `"inherit"` (case-insensitive) は「未指定」に正規化する
- permission rule と本 hook のどちらでも捕捉できない経路 (agent 定義 frontmatter の `model` / Workflow 内部の `agent()`) は下記「既知の制約」セクション参照

#### check-uncommitted-on-session-start

**ファイル**: `hooks/scripts/check-uncommitted-on-session-start.sh`
**イベント**: `UserPromptSubmit` (session 内初回のみ)

**動作**:

- literal `auto` セッションで cwd に未コミット変更がある場合、**agent にその出所分析と分類確認を要求**する `additionalContext` を注入する
- session ごとに 1 回だけ発火するよう `${TMPDIR:-/tmp}/agent-discipline-markers/<session_id>.checked` でマーカー管理
- 対象外 permission mode、 git リポジトリ外、 `jq` 不在環境ではすべて無音 `exit 0`

> **発火タイミングの注意**: ファイル名は `-on-session-start` ですが、 `SessionStart` イベントではなく **`UserPromptSubmit` イベント** で発火します (session 内で最初に処理されたプロンプトでのみ動作)。 マーカーは `git status` 実行より前に置かれる (無限ループ回避のための意図的トレードオフ) ため、 **最初のプロンプト時点で worktree が clean だと、 同 session 中に後から発生した未コミット変更は検知しません**。 後続の dirty も拾いたい場合は新しい session を開始してください。

**Claude への指示内容 (要約)**:

ユーザに確認を丸投げするのではなく、 以下を Claude が一次分析するよう要求します:

1. `git diff` / `git log` / ファイル内容を確認して各変更の出所を推定
2. 各ファイルを 4 分類 (今回タスク関連 / 以前の残骸 / 中間状態 / 不明) に振り分け
3. 推奨アクションをまとめて簡潔にユーザに報告し、 同意を取る
4. ユーザの同意を得てから実際の git 操作 (add / commit / stash / branch 切り出し等) を行う

これにより auto mode の本来の趣旨「Claude に最大限委任する」 を維持しつつ、 意図しない変更を巻き込むリスクを抑えます。

### Skills

常時注入ルール (before 系 / 排他系) が「原則」を配送するのに対し、Skills は具体的な手順・安全境界を progressive disclosure で配送します。`issue-plan` / `issue-start` は issue 駆動開発を担当します。

#### /issue-plan

**ファイル**: `skills/issue-plan/SKILL.md`

issue の起票・分解フェーズの手順をガイドします: 起票前の壁打ち、body template (背景 / 受入基準 / I/O 契約 / 制約 / 想定ファイル / 関連 issue の 6 セクション)、分割基準、関係設定コマンド (gh v2.94+ ネイティブ経路 + v2.94 未満向け fallback)、`#N` 相互参照と issue types 不使用の理由、親 issue の close 規約。

**使用シーン**:

- 「issue を起票する」
- 「issue に分解する」
- 「sub-issue を作る」

#### /issue-start

**ファイル**: `skills/issue-start/SKILL.md`

issue の着手・実装開始フェーズの手順をガイドします: pick-up 分岐 (既存の branch / PR 状態確認)、排他制御 (`rule:issue-claim` への参照)、軽微判定 (2 段構え)、spec-first 2 段階の具体コマンド手順 (局所定義・provisional 契約と契約改訂時のレビュー入力の隔離・Phase A 評価基準・成果物粒度・Phase B 内の進め方の 4.1〜4.5 を含む)、closing keyword。

**使用シーン**:

- 「issue に着手する」
- 「issue の実装を始める」
- 「issue を pick up する」

### CI (lint)

プロンプトファイルの rule ID 構成と `hooks.json` の 4 entries は内容を手で同期する必要があるため、うっかり片方だけ更新して drift する事故を CI で検出します。

#### lint-prompt-sync.sh

**ファイル**: `scripts/lint-prompt-sync.sh` (plugin 直下、`hooks/` 配下ではない)
**呼び出し元**: `.github/workflows/agent-discipline-prompt-lint.yml`

**動作** (5 チェック構成):

- **チェック 1 (常時適用ルール 3 part の rule ID セット)**: `hooks/prompts/always-{1,2,3}.md` から `<!-- rule:<id> -->` コメントの ID 集合を抽出する。いずれかの part からマーカーが 1 件も抽出できなければ fail する。まず各 part ファイル単体で rule ID マーカーが重複していないこと (`uniq -d` で検出。part 間ペアワイズ検査は自分自身と比較しないため単一ファイル内の重複を検出できず、和集合化がそれを無音で吸収してしまう盲点への対処) を検証し、次に 3 part 間で rule ID が重複していないこと (part 分割は rule 境界で行う契約) をペアワイズに検証する。いずれかで重複があれば fail する。さらに 3 part の和集合を、スクリプト内定数 `EXPECTED_ALWAYS_RULE_IDS` (常時適用ルールが持つべき rule ID 10 個の正本) と順序に依らず比較し、欠落・過剰のどちらかがあれば差分 ID を列挙して fail する。3 part の和集合はチェック 5 の母集合になる。ルール本文の表現差 (意味的ドリフト) は検出対象外とし、PR レビューでの目視確認に委ねる
- **チェック 2 (hooks.json 4 entries 共通ブロック一致)**: 抽出・正規化・比較より前に **前提検証** を行う — `hooks/hooks.json` の `type: agent` entry 数がスクリプト内定数 `EXPECTED_AGENT_ENTRIES` (= 4) と一致すること、および各 entry の `.prompt` が非空文字列であることを検証し、いずれか不成立なら fail する (entry 数の増減や prompt 欠落という前提崩壊時に、空同士の一致などで pass 側へ倒れることを防ぐ)。前提検証を通過した後、4 つの `type: agent` entry (`gh issue create` / `gh issue edit` / `gh pr create` / `gh pr edit`) の `prompt` から、entry 固有部分を除いた「共通ブロック」が一致するか検証する。entry 固有部分として除去する対象は 3 種類:
  1. 対象コマンド名の記載箇所 (`if` フィールドから機械導出した `gh <cmd>` をプレースホルダに置換)
  2. `gh pr create` のみが持つ Closes 検証 Step (Step 3) と、それに伴う「返り値」Step の番号繰り下がり (Step 4 → Step 3 相当への読み替え)。除去より前に、除去対象の Step 3 ブロックが実在することを検証し、実在しなければ fail する
  3. `gh pr create` / `gh pr edit` が共有する PR 固有の判定原則追加文 (「PR body で commit/discussion 経由でユーザ承認が明示されている文脈は禁止対象外」)。除去より前に、除去対象の文言が PR 系 2 entries それぞれに実在することを検証し、実在しなければ fail する
  正規化後の 4 entries が byte-identical でなければ diff 形式で乖離箇所を報告して fail する
- **チェック 3 (gh pr create Step 3 ブロック構造チェック)**: チェック 2 の `norm_b` は `gh pr create` entry 固有の Step 3 (Closes 検証) ブロックを共通ブロック比較の対象外とするため丸ごと除去する。そのため Step 3 の判定手順がどのように破損しても、開始・終了の見出しパターンさえ残っていれば除去は成功し共通ブロック比較 (チェック 2) は pass してしまう (false pass)。これを埋めるため、除去される前の raw prompt から Step 3 ブロックを独立に抽出し、スクリプト内定数の必須キーワードリスト (`` `<cwd>/.git` ``、`gitdir:`、`ref: refs/heads/`、`issue-<数字>`、`closing keyword`、`境界一致`、`fail-open で誘導層の`、の 7 要素) をすべて含むかを検証する。期待構造のソース・オブ・トゥルースは README 等の外部文書ではなくスクリプト内定数とし、判定ロジックの意味的な等価性までは検証しない構造スモークチェックである旨を明記している (欠落があれば diff ではなく欠落キーワードの一覧を報告して fail する)
- **チェック 4 (分業規律の rule ID セット)**: `hooks/prompts/discipline.md` から、チェック 1 と同じ抽出方式で `<!-- rule:<id> -->` の ID 集合を抽出する。ファイル内で rule ID が重複していないことを検証し、ID 集合をスクリプト内定数 `EXPECTED_DISCIPLINE_RULE_IDS` (`role-split` / `delegation-rules` / `delegation-instruction` / `escalation` の 4 個。分業規律が持つべき rule ID の正本) と順序に依らず比較する。重複・欠落・過剰のいずれかがあれば fail する
- **チェック 5 (subagent-rules.md の rule ID サブセット検査)**: `hooks/prompts/subagent-rules.md` の `rule:` プレフィクスの ID 集合が `always-{1,2,3}.md` の和集合 (チェック 1 で抽出・重複検査済みの集合) に含まれるかを片方向で検証する。含まれない ID があれば fail する (常時適用ルールにのみ存在する ID は「subagent に配送しない」意図的な選択のため検査しない。`subagent-rule:` プレフィクスのマーカーは対象外)
- **引数**: なし。**実行位置**: リポジトリルートを前提とする (それ以外や前提ファイル欠如は fail-closed で exit 1)。**依存**: `jq` (CI・ローカルとも前提。不在時は明確なエラーメッセージで exit 1)。**exit code**: 全チェック (1〜5) pass で 0、いずれか fail または実行時エラーで 1
- POSIX sh (`#!/bin/sh`) で記述しており `dash` でも動作する。ローカルでリポジトリルートから直接実行できる (`./plugins/agent-discipline/scripts/lint-prompt-sync.sh`)

#### lint-payload-size.sh

**ファイル**: `scripts/lint-payload-size.sh` (plugin 直下、`hooks/` 配下ではない)
**呼び出し元**: `.github/workflows/agent-discipline-prompt-lint.yml`

**目的**: Claude Code は hook の `additionalContext` 1 要素が inline 閾値 (約 9〜10K 文字) を超えると本文をファイルへ退避し、先頭 2KB のプレビューしか context に載せません。これを防ぐため、`additionalContext` を出力する全注入スクリプトを模擬 hook input で実際に実行し、各要素の文字数を実測して 8,000 字以下に保たれていることを検査します。prompt ファイル単体の静的サイズではなくスクリプトの実出力を測るため、前置き・連結・分岐といった組み立てロジックの変更にも自動で追従します。

**動作**:

- **対象と分岐**: 対象スクリプト × 分岐 × 期待 (出力あり / 出力なし) の対応表をスクリプト内定数 `CASE_TABLE` として持つ。対象は `inject-always.sh` / `inject-rules-part.sh` (part 2・part 3) / `inject-discipline.sh` / `inject-temporary.sh` / `inject-subagent-rules.sh` / `inject-auto.sh` / `check-uncommitted-on-session-start.sh`。additionalContext を出力しない `block-fable-subagent.sh` は検査対象外リスト `EXCLUDED_SCRIPTS` に置く。`inject-rules-part.sh` / `inject-discipline.sh` は初回配送 (出力あり) と配送済みマーカー存在時 (出力なし) の分岐を検査する。`inject-temporary.sh` は `hooks/prompts/temporary/*.md` の実在ファイルを連結した現物を測り、temporary md が 0 件なら「出力なし」を期待する
- **閾値 (2 段階)**: 要素が 8,000 字 (`PAYLOAD_LIMIT_CHARS`) を超えたら FAIL (exit 1)。7,800 字 (`PAYLOAD_WARN_CHARS`) を超え 8,000 字以下なら WARN を出すが exit code には影響しない。文字数は Unicode code point 数 (`wc -m` を UTF-8 ロケールで実行した値と同じ) で数える
- **fail-closed**: 対応表で「出力あり」の分岐で出力が無い・JSON として parse できない・`additionalContext` が空、「出力なし」の分岐で出力がある、対象スクリプトが exit 0 以外で終わる、といった場合はサイズ 0 として pass させず FAIL にする。加えて、`hooks.json` の `type: command` エントリと対応表 (と検査対象外リスト `EXCLUDED_SCRIPTS`) を照合し、注入スクリプトの追加・登録解除に対応表が追従していない場合も FAIL にする
- **隔離**: `mktemp -d` の隔離ディレクトリをケースごとの `TMPDIR` として対象スクリプトを実行し、実システムの `${TMPDIR:-/tmp}/agent-discipline-state` には読み書きしない。隔離ディレクトリは終了時に削除する
- **引数**: なし。**実行位置**: リポジトリルート。**依存**: `jq` / `git` / `bash`。**exit code**: 全ケース pass (WARN のみを含む場合も) で 0、FAIL が 1 件以上または前提エラーで 1
- **出力**: ケースごとに `OK:` / `WARN:` / `FAIL:` + 対象スクリプト・ケース ID・実測値 (または不成立理由) を 1 行で出す
- **実行**: `./plugins/agent-discipline/scripts/lint-payload-size.sh`。POSIX sh で記述しており `dash` でも動作する。`inject-always.sh` のランタイム 8K ガードと同じ判定にするため UTF-8 ロケールで実行する
- **スコープ外**: prompts ディレクトリの絶対パスや cwd・git status 行など実行環境に依存する可変部の長さは、lint 実行環境のパスと fixture で測った値のみを検査する。`inject-always.sh` はランタイム 8K ガードの適用後の実出力を測る

#### agent-discipline-prompt-lint (workflow)

**ファイル**: `.github/workflows/agent-discipline-prompt-lint.yml`

`always-1.md` / `always-2.md` / `always-3.md` / `hooks.json` / `discipline.md` / `subagent-rules.md` / `hooks/prompts/` 配下の全 md (`temporary/` を含む) / `hooks/scripts/` 配下の全ファイル / lint スクリプト 2 本 / 本 workflow 自身のいずれかが変更された `push` (master 向け) / `pull_request` でのみ発火し、`ubuntu-latest` 上で `actions/checkout@v4` の後に `lint-prompt-sync.sh` と `lint-payload-size.sh` を実行する。ubuntu-latest には `jq` と `git` が標準搭載されているため追加のセットアップ step は無い。

## 設計上の選択

### なぜ 1 plugin に集約するか (vs 機能ごとの個別 plugin)

このリポジトリは個人の Claude Code 開発スタイル marketplace です。 機能ごとに細かく plugin を分けると plugin 数が肥大化し、 enable list の見通しが悪くなります。 「物理層 + 思考層」 は抽象レイヤとしては別ですが、 個人運用では一括 on/off で問題が出ないため 1 plugin に集約しています。

公開 marketplace でユーザに細かい on/off を提供する場合は分離が望ましいですが、 本リポジトリは個人運用前提のため統合粒度を採用しています。

### なぜ常時系と auto 系で hook event を分けるか

- **常時系 (inject-always.sh / inject-rules-part.sh)**: 物理層 (Bash 分解) と before 系 (設計壁打ち / issue 規約 / closing keyword) と during 系 (自律作業中の判断境界) と排他系は permission_mode に依らず常に有用なので、 session ごとに 1 回だけ注入する。 part 1/3 は `SessionStart` で、 part 2/3・part 3/3 は `UserPromptSubmit` の最初のプロンプト処理時に配送済みマーカーで at-most-once 配送する (SessionStart のたびにマーカーをリセットして再配送する)。 トークンコストを抑えるため per-turn 再注入はしない
- **auto 系 (inject-auto.sh)**: after 系 (commit→push→PR→merge 自走パイプライン) は auto でのみ自動注入し、long-running session で薄れないよう `UserPromptSubmit` で per-turn 再注入する

### 誘導層と検知層の defense-in-depth

`additionalContext` 注入で Claude の自発的な遵守を促す **誘導 (nudge)** に加え、 issue / PR body に関しては「Claude が忘れたら hook が物理的に catch」 する **検知層** を PreToolUse type:agent hook で持ちます。 両者は defense-in-depth として階層化されています:

| レイヤ | 機構 | 効き目 | 対象 leak 経路 |
|---|---|---|---|
| 誘導層 | SessionStart (part 1/3) と UserPromptSubmit (part 2/3・part 3/3、session 内 1 回) で additionalContext 注入 | Claude が自発的に self-check する確率を上げる | issue body / PR 説明 / plan / commit message / 実装コード (= 全 leak 経路) |
| 検知層 | PreToolUse type:agent hook 4 entries | `gh issue/pr create/edit` 経路の物理 intercept (誘導層の取りこぼし防止)。literal head prefilter により非該当 Bash では model を起動しない | `gh issue create/edit` / `gh pr create/edit` のうち `--body inline` / `--body-file PATH` 形式 |

検知層は対象範囲を限定的にしています (= `gh api` 直接叩き / editor 起動経路 / 実装コード内のコメント等は cover しない)。 これは誘導層 (= Claude の自発遵守) を主、 検知層を補助とする非対称設計です。 全 leak 経路を物理層で塞ぐと regex / semantic 判定の網羅が困難になり false positive / false negative が増えるため、 「Claude 自身に最も書きやすい経路 (`gh issue/pr create/edit`)」 だけを物理 catch する戦略を採っています。

なお、 master への直接 push のように **強い deny で構造的に止めるべきケース** は別 plugin (例: `git-guardrails`, `pre-push-review`) が担当します。 本 plugin の検知層は推奨マーク等の semantic 判定対象に限定されているため、 deny 系 hook を完全代替するものではありません。

## ディレクトリ構成

```
agent-discipline/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   ├── prompts/
│   │   ├── always-1.md
│   │   ├── always-2.md
│   │   ├── always-3.md
│   │   ├── auto-mode.md
│   │   ├── delivery-note.md
│   │   ├── discipline.md
│   │   ├── subagent-rules.md
│   │   ├── temporary/
│   │   │   └── askuserquestion-preview-workaround.md
│   │   └── uncommitted-check.md
│   └── scripts/
│       ├── block-fable-subagent.sh
│       ├── check-uncommitted-on-session-start.sh
│       ├── inject-always.sh
│       ├── inject-auto.sh
│       ├── inject-discipline.sh
│       ├── inject-rules-part.sh
│       ├── inject-subagent-rules.sh
│       ├── inject-temporary.sh
│       └── lib/
│           └── permission-mode.sh
├── skills/
│   ├── issue-plan/
│   │   └── SKILL.md
│   └── issue-start/
│       └── SKILL.md
├── scripts/
│   ├── lint-payload-size.sh
│   └── lint-prompt-sync.sh
└── README.md
```

`always-1.md` / `always-2.md` / `always-3.md` は 1 つの常時適用ルールセットを rule 境界で 3 分割したもので、part 1 を SessionStart、part 2 / 3 を UserPromptSubmit で配送します。`discipline.md` は分業規律で、UserPromptSubmit で配送します。いずれもメインセッションのモデルに依らず同じファイルを配送します。`delivery-note.md` は SessionStart で part 1 の前に置く配送メモ (分割配送の告知と、context に見当たらない場合の自己修復手順) です。`temporary/` は問題修正までの一時規律ディレクトリで、中身の md を削除すると注入が消えます。

`.github/workflows/agent-discipline-prompt-lint.yml` (リポジトリ直下、plugin 配布に含まれない CI 専用 workflow) が `scripts/lint-prompt-sync.sh` と `scripts/lint-payload-size.sh` を呼び出します。

## 必要な実行環境

- `bash`
- `jq`
- POSIX `sh` (`lint-prompt-sync.sh` / `lint-payload-size.sh` の実行、CI (`ubuntu-latest`) およびローカル)
- `git` (check-uncommitted-on-session-start.sh の worktree 解決)

## 関連プラグイン

- [git-guardrails](../git-guardrails/) — master への直接 push を禁止する PreToolUse deny hook。 本 plugin の Bash 分解規律が機能してこそ deny が正しく届く
- [pre-push-review](../pre-push-review/) — push 前にレビューループを強制する PreToolUse hook。 同じく Bash 分解規律の上で機能する
- [auto-lint-check](../auto-lint-check/) — Edit/Write 前の linter チェック。 同上
- [update-default-branch](../update-default-branch/) — マージ完了後のデフォルトブランチ最新化 Skill。 after 系の自走パイプラインから自然に呼び出される

## 既知の制約

- **誘導層は強制ではない**: `additionalContext` の追加だけなので Claude が指示を無視することは原理的に可能。 検知層 (PreToolUse type:agent hook) が cover するのは gh issue/pr 経路のみで、 それ以外の leak 経路 (`gh api` 直接叩き / editor 起動経路 / 実装コード内コメント等) は誘導層のみ
- **検知層は一部の gh CLI 呼び出し形式を bypass し、 静的判定不能な形は拒否する**: `if` filter (`Bash(prefix:*)`) は best-effort であり、 compound command の各 subcommand と env-prefix を剥がした command は評価され、 Step 0 も subcommand 単位で env-prefix を剥がして判定する。 ただし以下の形式は検知層を bypass する (= agent hook が発火しない、 または Step 0 が検証対象外とし、 誘導層のみが防衛) か、 静的判定不能として拒否される:
  - **global option を subcommand 前に置く形式**: `gh -R owner/repo issue create ...` / `gh --repo owner/repo pr create ...` (= cross-repo 操作で頻出するが、 通常は `cd` で repo に入って操作するため Claude のデフォルト出力では稀)
  - **wrapper 経路**: `eval "gh issue create ..."` / `bash -c "..."` / `xargs gh ...` (= 既に section 1 Bash 分解規律で禁止されているため、 規律遵守時には発生しない)
  - **command 置換内の起票**: shell が実際に実行する `$()` / バッククォートの内側で `gh issue create` 等を実行する形式と、 command 語の位置で引用符に literal を分断する形式 (`"gh" issue create` 等) は body を静的に判定できないため、 Step 0 が静的判定不能として `{"ok": false}` で拒否する。 single quote の内側や `<<'EOF'` heredoc 本文の中で command 名に言及しているだけの文字列 (commit message 本文等) は対象外
  - **PreToolUse の構造的 TOCTOU (`cat ... && gh ... -F body.md` 系)**: 同じ command 内で生成する body file (例: `cat > body.md <<'EOF' ... EOF && gh issue create -F body.md`) は、 PreToolUse hook が Bash 実行 **前** に発火するため hook 時点で存在しない。 検知層はこれを静的判定不能として `{"ok": false}` で拒否するため、 body file は別の Bash 呼び出しで先に生成するか、 `--body` の静的文字列で渡す。 相対 PATH は、 hook input の `cwd` に同じ command 内で先行する `cd <dir>` を先頭から順にすべて適用した dir を基準に解決してから存在を判定する
  - bypass する形式は誘導層 (section 2.1 / 3.1 の禁止表現規範) が上流防衛として catch する想定。 完全に塞ぐには parser-backed command hook (= 別 plugin としての再設計) が必要なため、 既知制約としている
- **検知層の SPOF**: 検知層は LLM 呼び出しに依存するため、 hook の model (`claude-sonnet-5`) が API 不可用な状況では `gh issue/pr create/edit` が PreToolUse error で失敗する。 narrow scope で影響範囲を `gh issue/pr create|edit` (と `$()` / `$VAR` を含む Bash) に閉じているが、 pin 先の Sonnet 障害時はメインセッションが動いていても hook だけが落ちうる。 個別 call の transient エラー (rate limit / network blip) も残る
- **model pin は env var の影響を受けない** (実測で確認): `CLAUDE_CODE_SUBAGENT_MODEL` env var は hooks.json の `type: agent` hook の `model` field を上書きしない。 pin 値は env var の設定有無に関わらず常に dispatch される確定値であり、 「env 未設定環境向けの既定」 ではない
- **検知層は公式ドキュメント上 experimental な type:agent hook に依存**: PreToolUse `type: agent` hook は Claude Code 公式ドキュメントで experimental (実験的機能) と位置付けられており、 将来の仕様変更で挙動が変わる、 または廃止される可能性がある。 検知層全体 (4 entries すべて) がこの機能に依存しているため、 仕様変更時は検知層が機能しなくなりうる (= その場合は誘導層のみが防衛する状態に自然縮退する。 fail-open 設計のため縮退時に semantic 誤 block が発生することはない)
- **検知層の model pin は手動メンテナンス**: pin 先の Sonnet を upgrade する場合 (例: sonnet-5 → sonnet-6)、 `hooks/hooks.json` の `model` field を手動で同期する
- **check-uncommitted の発火タイミング制約**: 最初のプロンプト時点で worktree が clean だと、 同 session 中に後から発生した未コミット変更は検知しない (上記参照)
- **配送済みマーカーは OS の tmp cleanup による自然消去のみ**: `${TMPDIR:-/tmp}/agent-discipline-state/` 配下の配送済みマーカーに明示的な保持期間 (retention) 処理は無く、`check-uncommitted-on-session-start.sh` が使う `agent-discipline-markers/` とは別 namespace を使う
- **permission rule と `block-fable-subagent.sh` の捕捉範囲**: `model: "fable"` の明示 (alias `fable` / full model ID `claude-fable-5-1` とも) は hook が部分一致で捕捉し、Fable 週次枠の使用率で判定する。permission rule の `Agent(model:fable)` はこの許可経路も止めるため置かない。`Agent(fork)` は fork サブエージェントの起動自体を止める (fork は model 指定にも env にも依らず起動元のモデルを継承する。fork を許可する構成では、hook はサブエージェント内からの fork だけを deny する)。agent 定義 frontmatter の `model` は `tool_input` に現れないため permission rule でも hook でも捕捉できず、frontmatter が fable を指す agent への model 未指定の委任は、`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` の併用で実効モデルが env 側に固定される場合を除いて素通りする
- **`block-fable-subagent.sh` は Workflow ツール内部の `agent()` 呼び出しを PreToolUse で捕捉できない**: PreToolUse はメインループのツール呼び出しにのみ発火するため、Workflow スクリプト内部のサブエージェントスポーンは本 hook の対象外
- **compact 直後のギャップ**: `SessionStart(source=compact)` 後、次のユーザプロンプトまでは part 1 要素 (delivery-note + `always-1.md`) のみが再注入され、残りの要素 (part 2/3・分業規律) は再配送されない (`UserPromptSubmit` はユーザプロンプトでしか発火しないため)。compact 後に agentic loop が自動継続する経路では、この間の推論は part 1 の delivery-note (自己修復指示) と compact summary 内の痕跡に依存する。常時ルールと分業規律を単一要素に連結する構成でも同経路では persisted-output (2KB プレビュー) しか届かないため、分割配送による劣化ではない
- **exactly-once は保証しない**: hook 出力に配送 ACK が無いため、マーカー書込後に配送が失われた場合の再送はできない (SessionStart での全マーカーリセットが回復手段)。逆に TMPDIR 掃除等でマーカーが消えた場合は再配送される (重複は無害)

## 関連情報

- [Claude Code Hooks ドキュメント](https://code.claude.com/docs/en/hooks)
