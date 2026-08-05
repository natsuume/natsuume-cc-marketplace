# agent-discipline プラグイン

Claude Code の振る舞い規律 (= agent としての discipline) を配送する system prompt plugin です。旧 [decompose-bash](https://github.com/natsuume/natsuume-cc-marketplace/tree/93e5e9aa0c4dadb2e2eb13fb38c87b34cf3d10e0/plugins/decompose-bash) と [auto-followthrough](https://github.com/natsuume/natsuume-cc-marketplace/tree/93e5e9aa0c4dadb2e2eb13fb38c87b34cf3d10e0/plugins/auto-followthrough) を吸収しています。Fable / Sonnet / Opus 向けの既存規律を注入します。

## バージョン

v0.25.2

## 概要

Claude Code に「個人の開発スタイル」を一括で適用するための plugin です。機能ごとに別 plugin に分けず、1 plugin 内に複数のルール群を集約することで、個人 marketplace の plugin 数肥大化を抑えます。

次の表は Claude Code 側の配送設計です (v0.5.0 でモデル別 2 ファイル分離 + one-shot 補正を追加)。

| レイヤ | 配送経路 | inject 条件 | 内容 |
|---|---|---|---|
| **物理層 (Bash 分解)** | `SessionStart` (inject-always.sh、モデル別に `always-fable.md` / `always-sonnet-1.md` の part 1 を配送) | 常時 | Bash コマンドを最小粒度に分解して PreToolUse hook の取りこぼしを防ぐ |
| **before 系** | `SessionStart` (同上) | 常時 | 設計 / 仕様の事前壁打ち + 「思考は自由、 成果物への固定化は要承認」 非対称ルール (2.1) + 自己検知トリガー / 名指し禁止表現、 issue 起票時の `AskUserQuestion` 詳細化 + 起票直前 / pick up 時の self-check + 過去 session 独断の遡及検出 (3.1 / 3.2 で PR / plan / commit にも適用)、 並列粒度 + sub-issue + `#N` 相互参照、 PR closing keyword 規約、 AskUserQuestion の必須化 (R6、 v0.5.0 新設)、 spec-first 2 段階の開発手順 (R3c、 v0.5.0 新設・v0.18.0 で TDD 2 段階から rename) |
| **during 系** | `SessionStart` (同上) | 常時 (`permission_mode` 非依存) | 実装は自走、 設計 / 仕様 (= issue 起票時の壁打ちで決まっているはずの内容) の再確認では止まらない。 ただし issue 未明記の要件発見 / 大きな後戻り判断では止まる |
| **排他系** (v0.2.0) | `SessionStart` (同上) | 常時 (`permission_mode` 非依存) | 連続 issue 解決フロー (例: `/goal`) や並列 session 下で同 issue への重複着手を防ぐ。 claim comment (先着判定) + branch push (確定的排他) の二段構成で、 claim comment 本文の `session=<セッションID>` により誰の claim かを識別する (`session=` の無い旧形式 claim は他 session 扱いで削除禁止、 v0.14.0) |
| **モデル判定 / 分割配送** (v0.5.0 新設、分業規律の連結配送は v0.8.0/v0.9.0、issue #236 (v0.15.0) で要素分割に再設計) | `SessionStart` (inject-always.sh の fallback chain、part 1 のみ) + `UserPromptSubmit` (inject-rules-part.sh × 2 / inject-discipline.sh / resolve-model-on-prompt.sh) | 常時 (各要素は at-most-once、判定不能セッションのみ one-shot 補正が追加発火) | stdin.model → transcript 解析 → state file → 判定不能、の順で決定論的にモデルを判定し `always-fable.md` / `always-sonnet-1.md` を出し分けて SessionStart で part 1 のみ注入する。残りの part (`always-sonnet-2.md` / `always-sonnet-3.md`) と分業規律 (discipline-\*.md、モデル別) は UserPromptSubmit の最初のプロンプト処理時に別要素として個別配送する (8K 閾値超過を避けるための分割、詳細は `inject-always.sh` ヘッダの配送マトリクス参照)。判定不能時は自己ゲート付きで暫定配送し、後続の `UserPromptSubmit` で transcript から確定したら常時ルール確定版 (resolve-model-on-prompt.sh) と分業規律確定版 (inject-discipline.sh) をそれぞれ 1 度だけ再注入する |
| **検知系 (gh issue/pr body)** (v0.4.0、Closes 検証 Step は v0.7.0) | `PreToolUse` (hooks.json inline `type: agent` 4 entries) | `gh issue create` / `gh issue edit` / `gh pr create` / `gh pr edit` の literal head にだけ反応し、非該当 Bash では model を起動しない | 誘導層 (before 系 2.1 / 3.1) の禁止表現を semantic 判定し違反時 block。`gh pr create` だけ closing keyword も検証する。claude-sonnet-5 pin |
| **after 系** | `UserPromptSubmit` (inject-auto.sh) | `permission_mode == "auto"` | 変更が一段落したら commit → push → PR 作成 → (4 条件 hard gate を満たしたら) マージまで自走 |

加えて、auto セッションで `UserPromptSubmit` 初回発火時に cwd の未コミット変更を分類確認する独立 hook (`check-uncommitted-on-session-start.sh`) を併走させます。

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

**動作** (v0.13.0 新設、issue #221):

- 全 subagent の起動時に `hooks/prompts/subagent-rules.md` 全文を `additionalContext` として注入する。モデル判定・agent_type 分岐を持たない静的全文注入
- 注入内容は 4 規律: bash-decompose (always-sonnet-1.md と同一 rule ID。subagent の Bash もメインセッションと同じ PreToolUse hook を通るため) / 報告の事実性 / 副作用操作の default-deny / エスカレーション定型 (発動条件 4 点 + 返却フォーマット 5 点)
- `jq` 不在 / prompt ファイル欠落・空の場合は無音 `exit 0` (フェイルセーフ)。subagent-rules.md の rule ID 整合は `lint-prompt-sync.sh` チェック 5 (サブセット検査) が CI で担保する

#### inject-always

**ファイル**: `hooks/scripts/inject-always.sh`
**イベント**: `SessionStart`

**動作** (v0.5.0 でモデル別 fallback chain 判定に変更、issue #236 (v0.15.0) で part 1 のみの注入に縮小):

- セッションのモデルを決定論的 fallback chain で判定し、`delivery-note.md` (+ 実行時に解決した prompts ディレクトリの絶対パス 1 行) に続けて、`always-fable.md` (Fable 向け) または `always-sonnet-1.md` (Sonnet / それ以外、常時ルールの part 1/3) を `additionalContext` として注入する。残りの part (`always-sonnet-2.md` / `always-sonnet-3.md`) と分業規律 (discipline-\*.md) は本スクリプトの責務外で、UserPromptSubmit の `inject-rules-part.sh` / `inject-discipline.sh` が個別要素として配送する。詳細は本スクリプトヘッダの配送マトリクス参照
- **fallback chain** (先に確定した段階で判定を打ち切る、本スクリプトのみが実行する):
  1. stdin (hook input JSON) の `.model` フィールド
  2. transcript 解析: `.transcript_path` に対し `jq -r 'select(.type=="assistant" and .isSidechain != true) | .message.model // empty' | tail -n 1` で最後の main-chain assistant 行のモデル ID を取得 (セッション中の `/model` 切替後も最新の観測値が得られる、state file より常に新鮮な情報源)
  3. state file `${TMPDIR:-/tmp}/agent-discipline-state/model-<session_id>` (同一セッションの過去 SessionStart で確定した値のキャッシュ。transcript が空 (/clear 直後等) / 読めない場合の最後の砦で、`/model` 切替を跨ぐと stale になりうるため transcript より後に置く)
  4. いずれも空 → 判定不能
- **適用規則**: モデル ID (小文字化) が `fable` を含む → `always-fable.md`。`sonnet` を含む、または非空でそのいずれでもない (opus / haiku 等) → `always-sonnet-1.md`。判定不能 → `preamble-self-gate.md` + `always-sonnet-1.md` を注入し、state file の代わりに pending マーカー `${TMPDIR:-/tmp}/agent-discipline-state/pending-model-<session_id>` を作成する (後続の `resolve-model-on-prompt.sh` / `inject-rules-part.sh` / `inject-discipline.sh` が判定不能セマンティクスで扱う)
- **state file の atomic 書込と成否確認** (v0.15.0 で追加): state file は同一ディレクトリ内 temp file → `mv` で atomic に書き込み、成否を確認する。モデルが確定したのに書込に失敗した場合は pending マーカー作成にフォールバックし、判定不能セマンティクス (自己ゲート付き配送 + `resolve-model-on-prompt.sh` の one-shot 補正) に縮退する。pending の作成にも失敗した場合は state 変更なしで注入のみ継続する (既知の制約参照)。state 書込が成功した場合のみ、過去の判定不能 SessionStart が残した pending マーカーを削除する
- **配送済みマーカーのリセット** (v0.15.0 で追加): UserPromptSubmit 側の配送済みマーカー (`delivered-rules-2-<sid>` / `delivered-rules-3-<sid>` / `delivered-discipline-<sid>`) を SessionStart のたびに無条件で削除する (`resume` / `clear` / `compact` でも全要素を再配送するセマンティクスを維持するため)
- **8K ガード** (v0.15.0 で追加): additionalContext を組み立てた後の全文文字数を計測し、8,000 字を超える場合は (i) 実パス行を落として再計測 → (ii) それでも超える場合は delivery-note 全体を落として再計測、の順で段階的に縮退する。self-gate 前置き (`preamble-self-gate.md`) とルール本文 (`always-fable.md` / `always-sonnet-1.md`) はいかなる場合も落とさない
- `SessionStart` は `startup` 以外に `resume` / `clear` / `compact` でも発火するため同一セッション内で複数回呼ばれる可能性があるが、注入内容は static なので重複しても害は無い (毎回コンテキストトークンを再消費する点に留意)
- 入力 JSON から `hook_event_name` を読み取り `hookSpecificOutput.hookEventName` に同じ値を設定 (誤った既定値で別 event の文脈に誘導しないため)
- `jq` 不在 / 不正 JSON 入力 / 注入対象の part 1 本文 (`always-fable.md` / `always-sonnet-1.md` / `preamble-self-gate.md`) が読めない場合はすべて無音 `exit 0` (フェイルセーフ)。`delivery-note.md` が読めない場合は delivery-note 無しで part 1 本文のみ注入する (ペイロード単位の fail-open)

**注入内容の要約** (`always-fable.md` と `always-sonnet-{1,2,3}.md` の和集合で共通のルール ID、詳細な書き分けは「モデル別ファイルの書き分け」参照。part 1 = ルール 1〜2、part 2 = ルール 3〜6、part 3 = ルール 7〜9):

1. **Bash コマンド分解** (物理層、`rule:bash-decompose`): `&&` / `||` / `;` / `&` / `$(...)` / バッククォート / `eval` / `sh -c` / `xargs` / `find -exec` を分解対象、パイプライン `|` は単一論理操作のみ許容、`cd $dir && cmd` やトランザクション的合成は例外
2. **設計 / 仕様検討の事前明確化** (`rule:design-approval`): スコープ / 要件 / 受入基準 / I/O 契約 / 公開命名などの後戻りコストが大きい判断は `AskUserQuestion` で事前に詰める。軽微な実装判断は対象外。「思考は自由、成果物への固定化は要承認」非対称ルール: 検討段階での複数案比較・推奨思考は許容するが、結論を issue body / PR 説明 / plan / commit に書き出す前に必ず `AskUserQuestion` を通す
3. **issue 起票時の詳細化** (`rule:issue-body`): 実装時に判断が発生しないよう `AskUserQuestion` で詳細化。起票内容は **issue body に全埋め込み** (補助 file には書かない)。issue body は「ユーザが承認した契約書」と捉え、起票直前 / pick up 時の self-check + 遡及適用で独断 leak を塞ぐ。PR 説明 / plan / commit にも同じ禁止表現規律を拡張
4. **issue の粒度と関係性** (`rule:issue-granularity`): 独立して並列作業できる粒度で起票、大きい場合は sub-issues 分割。関係性は (a) sub-issue 親子リンク + (b) `#N` 相互参照を併用
5. **PR 作成時の closing keyword** (`rule:closing-keyword`): 完全解決時のみ PR body に `Closes #N` を書く。closing keyword は default branch 向け PR でのみ機能する。部分対応では `Refs #N` / `Part of #N` に切替
6. **自律作業中の判断境界** (`rule:autonomy-boundary`): 実装は自走、設計 / 仕様 (= issue で決まっているはずの内容) は再確認しない。ただし issue 未明記の要件発見 / 大きな後戻り判断では止まる
7. **連続 issue 解決時の排他制御** (`rule:issue-claim`): `/goal` 等の並列 session フロー向け。(a) `gh issue view` で `ai:in-progress` ラベル / claim comment 早期判定、(b) claim comment 投稿 (`session=<セッションID>` で自他判別)、(c) 3 秒待機 + REST issue comments の全ページ再取得 + `(created_at, 数値 id)` の辞書順比較による先着判定、(d) 作業 branch 切ってセッション ID 入りの空 commit + 即 push で確定的排他、(e) push 成功時のみラベル付与。安全機構のため両ファイルとも手順を省略せず全文記載する
8. **AskUserQuestion の必須化** (`rule:ask-user-question`、v0.5.0 新設・R6): ユーザへの質問・確認・判断伺い・すり合わせは自由文で turn を終えず必ず `AskUserQuestion` を発行する
9. **spec-first 2 段階の開発手順** (`rule:tdd-two-phase`、v0.5.0 新設・R3c、v0.18.0 で TDD 2 段階から rename): 軽微な修正を除き、実装は Phase A (テストがある場合は失敗するテスト + 設計骨格、テスト不能な成果物では設計記述 commit に置換) → pre-push-review のレビュー通過 → draft PR → Phase B (実装本体) → ready 化、の 2 段階で進める。正典 TDD ではなく実行可能仕様の先行固定 (spec-first) であり、局所定義・評価基準の詳細は issue-start skill が持つ

**モデル別ファイルの書き分け**:

- `always-fable.md`: 各ルールを「意図 (なぜ) + 短い指示 + 境界 (いつ例外か)」で記述しパターン列挙を避ける。禁止表現 8 カテゴリは意図短文に圧縮する。進捗・完了報告はこのセッションのツール結果で裏付けられた事実のみを書く旨を含める
- `always-sonnet-1.md` / `always-sonnet-2.md` / `always-sonnet-3.md`: 各ルールに適用範囲を明示し、否定形の指示には具体的な代替行動を併記する。ルールごとに良い例 / 悪い例を最小 1 セット添える。禁止表現 8 カテゴリは列挙を維持する。part 3/3 (`always-sonnet-3.md`) の末尾に「単純な作業では深い思考を要さない」の steering 文を置く。issue #236 (v0.15.0) で単一ファイル (`always-sonnet.md`) を rule 境界で 3 分割したもので、rule 本文・rule ID マーカーは分割前から無変更 (各 part 冒頭のヘッダコメント・`part n/3` 表記・1〜2 文の説明のみが分割に伴う追加)
- `rule:issue-claim` (連続 issue 解決時の排他制御、part 3/3 に含まれる) のみ、安全機構のため `always-fable.md` / `always-sonnet-3.md` とも手順本体を省略せず完全記載する

#### inject-temporary

**ファイル**: `hooks/scripts/inject-temporary.sh`
**イベント**: `SessionStart` / `UserPromptSubmit`

**動作** (v0.12.0 新設、issue #237 (v0.19.0) で実行中 session の追い配送を追加):

- `SessionStart` では従来どおり `hooks/prompts/temporary/*.md` の非空ファイルをファイル名の辞書順 (`LC_ALL=C`) で連結し、1 つの `additionalContext` として全件配送する。同時にファイル名の POSIX `cksum` (CRC + byte length) を session ごとの配送済み集合 `${TMPDIR:-/tmp}/agent-discipline-state/delivered-temporary-<session_id>` へ atomic に記録する
- `UserPromptSubmit` では配送済み集合に無い非空 md だけを同じ順序で連結し、追加後の最初のプロンプト処理時に one-shot 配送する。配送済み集合は本文 hash ではなくファイル名単位なので、temporary rule の lifecycle は従来どおりファイル追加・削除で管理する
- SessionStart の全件配送は resume / clear / compact を含めて維持し、その時点の存在ファイルで配送済み集合を置き換える。temporary directory が空なら空集合を記録するため、その後に同名ファイルが追加されても次の UserPromptSubmit で配送できる
- `agent_id` 付き UserPromptSubmit は subagent 経路として無音終了し、配送済み集合も変更しない。`jq` 不在 / 不正 JSON / session_id 不正 / state directory または atomic marker 書込失敗 / 全件配送済み / directory が空の場合も無音 `exit 0` する
- SessionStart input に session_id が無い異常系だけは v0.12.0 からの既存挙動を保つため、marker 無しで全件配送する。正常系では出力 JSON を先に生成し、marker 更新に成功した後だけ stdout へ出す

撤去手順: Claude Code 側で AskUserQuestion preview のスクロール問題 (一定行数を超える preview が「hidden XX lines」で隠され、ユーザが全文を確認できない) が修正されたら、`hooks/prompts/temporary/` 配下の md を削除するだけで注入が消えます (スクリプトと hooks.json entry は残っても no-op)。修正されたことは、長い preview を付けた AskUserQuestion を表示し、隠れた部分をスクロールで全文確認できることで確かめます。完全撤去する場合のみ entry・スクリプト・temporary ディレクトリも削除します。いずれの場合も version bump が必要です。

#### resolve-model-on-prompt

**ファイル**: `hooks/scripts/resolve-model-on-prompt.sh`
**イベント**: `UserPromptSubmit`

**動作** (v0.5.0 新設、one-shot 補正。issue #236 (v0.15.0) で分業規律ブロックを分離):

- `inject-always.sh` が判定不能分岐で作成した pending マーカー `${TMPDIR:-/tmp}/agent-discipline-state/pending-model-<session_id>` が存在しない session では即 `exit 0` (通常時のオーバーヘッドをマーカー存在チェック 1 回に抑える)
- pending マーカーが存在する場合のみ、`.transcript_path` に対し `inject-always.sh` と同じ transcript 解析コマンドを実行し、最後の main-chain assistant 行のモデル ID を取得する
- assistant 行がまだ無い (transcript 解析結果が空) 場合は何もせず、pending マーカーを残したまま次回の `UserPromptSubmit` で再試行する
- assistant 行が見つかりモデルが確定したら、state file への atomic 書込 (同一ディレクトリ内 temp file → `mv`) を試みる:
  - **書込成功**: pending マーカーを削除する (TOCTOU の隙間を作らない、#155 の教訓)。モデル ID が `fable` を含む場合のみ、常時ルール確定版 (prefix + `always-fable.md`) を `additionalContext` で 1 度だけ再注入する。prefix は「常時適用ルールの確定版を優先し、セッション冒頭の自己ゲート付き注入は破棄すること」に加え「分業規律の Fable 版補正は別要素 (inject-discipline.sh) で届く」旨に触れる。それ以外 (sonnet / opus / haiku 等) は自己ゲート時に `always-sonnet-1.md` (と `inject-rules-part.sh` が配送する part 2/3) を注入済みと同内容のため再注入しない
  - **書込失敗**: pending マーカーを削除せず、注入も行わず無音 `exit 0` する (次回 `UserPromptSubmit` で再試行。stale state を残したまま pending を消すと、後続スクリプトが誤った変種を確定配送するため)
- 分業規律 (discipline-\*.md) の Fable 補正は本スクリプトの責務外で、`inject-discipline.sh` の `sonnet-gate` → `final` マーカー遷移が別要素として担う (v0.15.0 でここから分離)
- `jq` 不在 / 不正 JSON 入力 / `transcript_path` が読めない / `always-fable.md` が読めない場合はすべて無音 `exit 0` (フェイルセーフ)

#### inject-rules-part

**ファイル**: `hooks/scripts/inject-rules-part.sh`
**イベント**: `UserPromptSubmit`
**引数**: `2` または `3` (part 番号)。hooks.json に `inject-rules-part.sh 2` / `inject-rules-part.sh 3` の 2 entries で登録する。引数が `2` / `3` 以外、または欠落の場合は無音 `exit 0`

**動作** (v0.15.0 新設、issue #236):

- 常時ルール (Sonnet 版) の part 2/3 または part 3/3 (`always-sonnet-2.md` / `always-sonnet-3.md`) を at-most-once で個別要素として配送する。マーカー `delivered-rules-<n>-<session_id>` が存在すれば即 `exit 0` (毎プロンプトのオーバーヘッドをファイル存在チェックのみに抑える)
- マーカー不在時は **pending 優先・state 次点** の優先規則 (`pending-model-<session_id>` が存在する間は `model-<session_id>` を信頼しない) で分岐する:
  - pending あり → `part-self-gate.md` (part 番号非依存の自己ゲート行) + `always-sonnet-<n>.md` を注入し、マーカーを書く
  - pending 無し + state が fable → 配送不要 (fable は part 1 = `always-fable.md` 全文で完結)。マーカーのみ書く
  - pending 無し + state が非 fable → `always-sonnet-<n>.md` を注入し、マーカーを書く
  - pending も state も無い (SessionStart hook が失敗した異常系) → 判定不能と同じ自己ゲート付き配送にフォールバックし、マーカーを書く
- マーカーの書き込みは注入本文と出力 JSON の生成に成功した後に行う (先にマーカーを書くと、本文読取失敗時に当該要素が session 中永久欠落する)。マーカー自体の書込も同一ディレクトリ内 temp file → `mv` の atomic 書込にする
- `jq` 不在 / 不正 JSON 入力 / `always-sonnet-<n>.md` (または自己ゲート付き配送時の `part-self-gate.md`) が読めない場合は無音 `exit 0` でマーカーは書かない (次プロンプトで再試行)

#### inject-discipline

**ファイル**: `hooks/scripts/inject-discipline.sh`
**イベント**: `UserPromptSubmit`

**動作** (v0.15.0 新設、issue #236。v0.21.0 で fable / opus / その他非 fable の 3-way 配送に拡張):

- 分業規律 (discipline-\*.md、モデル別) を独立要素として配送する。マーカー `delivered-discipline-<session_id>` は内容として 3 状態を持つ: **無し** / `sonnet-gate` / `final`
- **マーカー無し**: pending 優先・state 次点で分岐する:
  - pending あり → 見出し「# agent-discipline: 分業規律 (Sonnet)」+ `discipline-preamble-self-gate.md` + `discipline-sonnet.md` を注入し、マーカーを `sonnet-gate` にする
  - pending 無し + state が fable → 見出し「# agent-discipline: 分業規律 (Fable セッション)」+ `discipline-preamble-fable.md` + `discipline-fable.md` (分業規律 fable 版) を注入し、マーカーを `final` にする (fable 判定は常に最優先)
  - pending 無し + state が非 fable かつ opus → 見出し「# agent-discipline: 分業規律 (Opus)」+ `discipline-opus.md` (分業規律 Opus 版) を注入し、マーカーを `final` にする
  - pending 無し + state が非 fable かつ非 opus (haiku 等) → 見出し「# agent-discipline: 分業規律 (Sonnet)」+ `discipline-sonnet.md` (分業規律 sonnet 版) を注入し、マーカーを `final` にする
  - pending も state も無い異常系 → pending 時と同じ自己ゲート付き配送、マーカーを `sonnet-gate` にする
- **マーカー `sonnet-gate`** (判定不能時の自己ゲート付き分業規律を配送済み、one-shot 補正の対象):
  - pending 無し + state が fable に確定していた → 補正前置き (自己ゲート付きで配送済みの Sonnet 版分業規律を破棄し本要素を優先する旨) + 分業規律 fable 版を注入し、マーカーを `final` に更新する
  - pending 無し + state が非 fable かつ opus に確定していた → 補正前置き (fable 補正と同型の Opus 版) + 分業規律 Opus 版を注入し、マーカーを `final` に更新する
  - pending 無し + state が非 fable かつ非 opus に確定していた → 注入なしでマーカーを `final` に更新する (配送済みの Sonnet 版がそのまま確定内容のため)
  - pending あり、または state 無し → 何もしない (`resolve-model-on-prompt.sh` の state 書込 → pending 削除の完了待ち。同一 event 内の並列実行で本スクリプトが先に読んだ場合、最大 1 プロンプトの補正遅延が生じる。既知の制約参照)
- **マーカー `final`**: 即 `exit 0`
- マーカーの書き込みは注入本文と出力 JSON の生成に成功した後に行う (`sonnet-gate` → `final` の「注入なし」更新は本文生成が無いため直接書く)。マーカーの読み書きも同一ディレクトリ内 temp file → `mv` の atomic 書込にする
- `jq` 不在 / 不正 JSON 入力 / 配送対象のペイロード (`discipline-opus.md` を含む) が読めない場合は無音 `exit 0` でマーカーは書かない (次プロンプトで再試行)

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
- 禁止 / 要確認: master への直接 push / 破壊的操作 / 秘匿情報コミット / 4 条件未充足の独断マージ

#### PreToolUse type:agent hook (v0.4.0 新設)

**定義場所**: `hooks/hooks.json` 内に inline 定義 (= 外部スクリプト不要、 prompt 全文を JSON 内に持つ)
**イベント**: `PreToolUse`
**matcher**: `Bash`
**`if` filter**: `Bash(gh issue create:*)` / `Bash(gh issue edit:*)` / `Bash(gh pr create:*)` / `Bash(gh pr edit:*)` の **4 entries に分割**
**model**: `claude-sonnet-5` (= 実装系メインセッションおよび全 subagent と同系列に pin。v0.7.0 で `claude-opus-4-7` から変更、詳細は下記「SPOF 緩和の設計」参照)
**timeout**: 60 秒 (公式 default)

**動作**:

- 各 hook の `if` field で target command にのみ反応する物理 prefilter を構成。 該当 Bash 呼び出しで初めて agent subagent が起動し、 それ以外の Bash (= `ls` / `git status` / `rg` / `gh issue view` などの非対象) では agent は **起動さえしない** (= LLM 呼び出しゼロ、 latency 影響ゼロ、 SPOF 露出なし)
- agent 起動時は **Step 0 で defense-in-depth command guard を実行**: prompt 冒頭で再度 command head を確認し、 当該 hook entry の `if` filter と一致する literal で始まらなければ semantic 検証せず即 `{"ok": true}` で通す。 これは Claude Code が複雑な command (`$(...)` 置換、 env var prefix、 多段 pipeline、 quoting) を parse できず `if` が fail-permissive で fall through した場合の偽 trigger 対策 (codex P2 指摘への対処)
- Step 0 を通過した場合、 prompt 内で body content を抽出する:
  - `--body 'inline string'` / `--body "inline string"` (heredoc 含む) → inline 文字列を body content とする
  - `--body-file PATH` → Read tool で PATH のファイル内容を取得 (= `type: agent` を採用した直接の理由)
  - どちらも無い (= editor 起動経路) / `--body-file -` (stdin) → 判定不能として `{"ok": true}` で通過 (= 誘導層に委ねる)
- body content に対し、 inject-always.sh セクション 2.1 / 3.1 の禁止カテゴリ (推奨マーキング / 独断の正当化 / 比較表で勝者決定 / 暗黙の決め打ち = 粒度差 / 「とりあえず」 系 / 暫定マーク残置 / ユーザ判断の先回り代弁 / 受入基準への未承認選択埋め込み) を semantic 判定
- 該当なし → `{"ok": true}`、 該当あり → `{"ok": false, "reason": "違反箇所の引用 + カテゴリ名 + 修正方針 (= AskUserQuestion でユーザの decision を取り、 確定 1 案だけを残す)"}` で block

**Closes 検証 Step (`gh pr create` entry のみ、v0.7.0 新設)**:

`gh pr create` entry の prompt にのみ、 上記 Step 2 (禁止カテゴリの semantic 判定) の直後・Step 3 (返り値、 v0.7.0 で Step 4 に繰り下げ) の前に追加の判定 Step 3 を挿入する (#151/#153 対応、親 issue #173 決定事項 9)。 他 3 entries (`gh issue create` / `gh issue edit` / `gh pr edit`) の prompt はこの Step を持たない。 branch 名からの issue 推定は PR 作成時にのみ意味を持つ判定のため、 4 entries の prompt 完全 duplicate は維持しつつ本 Step だけ 1 entry に閉じる (= entry を増やさず model pin の保守対象も増やさない)。

判定手順 (codex review P2 指摘 2 件を反映した最終形):

1. まず `<cwd>/.git` を Read tool で読む
2. 読み取れた内容が `gitdir: <path>` 形式 (worktree) の場合: `<path>` が相対パスであれば、 `.git` ファイルの所在ディレクトリ (= `cwd` そのもの) を基準に解決したうえで、 解決後の `<path>/HEAD` を Read tool で読む (linked worktree では `.git` 自体が `gitdir:` ファイルであり `<cwd>/.git/HEAD` を先に読む実装は常に fail-open するバグだったため、 `.git` を先に読んでから分岐する順序に修正した)
3. `<cwd>/.git` の Read が「ディレクトリである」ことを理由に失敗する場合 (= worktree ではない通常のリポジトリ): `<cwd>/.git/HEAD` を Read tool で読む
4. 上記いずれの経路でも HEAD が取得できない場合、 または取得できた内容が `ref: refs/heads/<branch>` 形式でない場合 (detached HEAD 等) は、 本 Step を判定不能として通過する (fail-open で誘導層の `rule:closing-keyword` に委ねる)。 `.git` 自体が存在しない bare リポジトリも本 Step の対象外として同様に通過する
5. branch 名が `*/issue-<数字>-*` パターンに一致しない場合は本 Step を通過する
6. 一致する場合、 パターンから issue 番号 `N` を抽出する。 branch 名に `issue-<数字>-` 形式の断片が複数含まれる場合は、 **最初に出現した断片の数字** を `N` として採用する (branch 名規約 `<prefix>/issue-<N>-<slug>` では prefix 直後の先頭断片が規約上の issue 番号。 例: `feat/issue-12-fix-issue-34-regression` では N=12。 v0.7.3 / #188)。 Step 1 で抽出済みの body content に、 以下のいずれかが `N` そのものを参照している場合のみ本 Step を通過する (境界一致で判定する: `#12` は `#123` にマッチしない、 すなわち `#N` の直後が数字でないことを確認する。 先頭ゼロの同一視はしない。 branch 名規約は issue 番号をそのまま埋めるため通常は先頭ゼロが発生しないが、 発生した場合は不一致として block 側に倒す):
   - closing keyword (`Closes` / `Close` / `Closed` / `Fix` / `Fixes` / `Fixed` / `Resolve` / `Resolves` / `Resolved`、 case-insensitive、 colon 許容 = `Closes:` 等も可) + `#N` または `owner/repo#N`
   - 部分対応表記 (`Refs` / `Part of`、 case-insensitive) + `#N` または `owner/repo#N`
7. 上記いずれにも該当しない場合 (= 他 issue への参照のみが併記されている場合を含む) は `{"ok": false, "reason": "branch 名から issue #<N> の作業と推定されるが、 PR body に issue #<N> を参照する closing keyword (例: Closes #<N>) も部分対応表記 (例: Refs #<N>) も無い。 完全解決なら Closes #<N> を、 部分対応なら Refs #<N> を body に追記して再実行する"}` で block する (reason 内の `<N>` は Step 6 で抽出した実際の issue 番号に置換する)。 該当する場合は Step 4 に進む

editor 経路 / `--body-file -` (stdin 経路) は既存 Step 1 の扱いのまま判定不能として通過する (= body content 自体が取得できないケースを本 Step が追加で救済することはない)。 worktree (相対 `gitdir:` の解決を含む) / detached HEAD / bare リポジトリ (対象外で通過) / branch 名不一致 / issue 番号一致の keyword あり / 番号不一致または keyword なし の各ケースについて、 上記手順から期待判定 (通過多数 + block は「番号一致の keyword が body に無い」場合のみ) が一意に導ける設計としている。

**なぜ 4 entries に分けて prompt を duplicate しているか**:

- `if` field は単一 command pattern (`Bash(prefix:*)` 形式) のみで、 alternation (`Bash(gh (issue|pr) (create|edit):*)`) は公式 syntax では非対応
- 1 entry に `if: "Bash(gh issue:*)"` のような broader filter を置くと、 `gh issue view` / `gh issue list` / `gh issue close` 等にも agent が起動して narrow scope が損なわれる
- 4 つの target command (`create` / `edit` × `issue` / `pr`) ごとに個別 entry を持ち、 prompt は 4× 完全 duplicate という maintenance トレードオフを受け入れる代わりに、 真の narrow scope (= 非 target command では agent 完全非起動) を確保している
- prompt 更新時は 4 箇所同期する必要あり (`jq` で各 entry の `.prompt` を抽出して比較する scripts での lint が将来必要になり得る)

**なぜ `type: agent` か (vs `type: prompt`)**:

- `--body-file PATH` 形式では PATH のファイル内容を読み取らないと判定できない。 `type: prompt` はツール使用不可なので Read できず、 `--body-file` 経路を取りこぼす
- 一方、 既存の `block-commit-lint` plugin が PR body に `--body-file` 経路を強制している repo policy のため、 prompt hook 単独だと PR 作成経路で必ず取りこぼす穴になる
- 公式の使い分け規範 ("Use prompt hooks when the hook input data alone is enough to make a decision. Use agent hooks when you need to verify something against the actual state of the codebase.") に照らすと、 `--body-file` で参照されるファイル状態を verify する用途は agent hooks が first choice

**SPOF 緩和の設計**:

- 旧 `llm-default-branch-push-poc` 廃止教訓: 全 Bash 発火 prompt hook が暗黙の default model (haiku) ダウン時に全 Bash を PreToolUse error にする非対称 SPOF があった (memory: `reference_prompt_hook_model_spof.md`)
- 今回はこれを 2 段で緩和:
  - **narrow scope (物理層)**: 個別 hook の `if: "Bash(gh <cmd>:*)"` filter で target command にだけ反応するよう **hook config 段階で** 物理 prefilter。 prompt 内 early return ではなく hook config 段階の filter なので、 非該当 Bash 呼び出しでは agent subagent が **そもそも起動しない**。 結果として LLM 不可用時の影響は「`gh issue/pr create/edit` のみ失敗」 に narrow され、 通常の Bash 呼び出し (= `ls` / `git status` / `rg` 等) は影響ゼロ
  - **model pin**: `model` field を明示的に `claude-sonnet-5` に固定 (v0.7.0、#151/#174 V2 実測による変更。 従来は `claude-opus-4-7` に pin していた)。 #174 V2 の実測検証で、 hooks.json の `type: agent` hook の `model` field は `CLAUDE_CODE_SUBAGENT_MODEL` env var の影響を受けず pin 値がそのまま dispatch されることが確認された (= 「env var が優先され pin は env 未設定環境向けの既定として機能する」 という当初の想定は誤りで、 pin は env 設定の有無に関わらず常に有効な確定値)。 sonnet 5 は実装系メインセッション (= Sonnet ベースの Claude Code session) およびこの環境の全 subagent (`CLAUDE_CODE_SUBAGENT_MODEL=sonnet` によりこの環境の Agent tool 経由の Task 委任は実質 sonnet 固定) と同系列のため、 Sonnet がダウンした場合は subagent への委任自体が同時に止まっており hook 単独の新規障害面にはならない (= 「hook だけが落ちて他は動く」 非対称を避ける従来方針を維持)。 ただし **Fable メインセッション時はこの対称性が崩れる** (= メインセッションは Fable で正常動作していても、 hook は Sonnet 側の障害時に落ちうる非対称が残る。 下記「既知の制約」参照)

これにより SPOF は「実装系メインセッション (Sonnet) が動いている時は hook も動く」 という対称構造に閉じる (Fable メインセッション時のみ非対称が残存)。 個別 call の transient error (rate limit / network blip) は残るが、 これは Claude Code 通常使用の背景ノイズと同レベル

**設計の変遷** (codex review からの修正):

v0.4.0 当初は単一 hook entry (matcher `Bash` のみ) + prompt 内で「`gh (issue|pr) (create|edit)` 以外は即 ok:true」 という early return 構成だった。 これは codex review で「prompt 内 early return は agent subagent が **既に起動済み** の状態で起こるため、 全 Bash 呼び出しで Opus subagent が起動してしまい narrow blast radius が成立せず、 ordinary commands (tests / git status / rg 等) の latency / cost / model 可用性依存が増える」 と P1 指摘された (該当指摘の解は「`if` filter または lightweight command prefilter」)。 この指摘を受けて、 hook config 段階で物理 prefilter する `if` field (公式 plugin `claude-plugins-official/security-guidance` と同じ syntax) を採用し、 4 entries に分割した現在の設計に変更した。

さらに codex P2 指摘 (= 複雑な Bash command で Claude Code parser が `if` を fail-permissive で fall through した場合に偶発通過した unrelated command が semantic 検証されて誤 block される可能性) への対処として、 各 prompt 冒頭に **defense-in-depth command guard (Step 0)** を追加した。 第一の narrow scope は引き続き `if` field の hook config 段階だが、 prompt 内 guard が二段目として偽 trigger を catch する非対称設計

**fail-closed の原則**:

- 違反疑い検出時は `{"ok": false}` で block を返す。 silent pass は構造的に不可逆 (= 後続 session が既決事項として読む leak が成立) なため、 false positive (= 正当な記述を誤って block) の方が recovery 可能であり、 fail-closed が論理的に正しい
- block された Claude は reason を読み、 AskUserQuestion でユーザの decision を取り、 確定した 1 案だけを body に残して再試行する

#### block-fable-subagent (v0.8.0 新設)

**ファイル**: `hooks/scripts/block-fable-subagent.sh`
**イベント**: `PreToolUse`
**matcher**: `Agent|Task`

**動作** (v0.8.0 で fable-discipline から移設。判定ロジック・deny/allow の判定順序は無変更。`STATE_DIR` のみ `${TMPDIR:-/tmp}/agent-discipline-state` に一本化し、`inject-always.sh` が書く session model state と共有する):

- サブエージェントが Fable で実行される経路を deny する防波堤。判定順序は Claude Code のモデル解決順序 (`CLAUDE_CODE_SUBAGENT_MODEL` env > `tool_input.model` 明示指定 > agent frontmatter > メインセッション継承) と一致させ、すべて deterministic な文字列判定で行う (LLM 評価は使わない)
  0. env が fable を指す → `tool_input.model` の値に依らず無条件 deny (env は明示指定より優先されるため)
  1. `tool_input.model` に fable が明示指定されている → deny
  2. `tool_input.model` が非 fable の具体指定 → allow (Step 0 より env は非 fable 確定)
  3. `tool_input.model` 未指定 (= メインセッション継承経路): env が非空なら allow (env が継承を非 fable モデルへ上書きするため安全)。env 不在時は `inject-always.sh` が SessionStart で記録した session model state (`${TMPDIR:-/tmp}/agent-discipline-state/model-<session_id>`) が fable の場合のみ deny。state file が読めず判定不能な場合は、`inject-always.sh` が判定不能分岐で作成する pending マーカー (`${TMPDIR:-/tmp}/agent-discipline-state/pending-model-<session_id>`) の存在を確認し、**存在すれば deny** (継承先が Fable になりうる判定不能期間のため、#200 で実装)、存在しなければ真の情報ゼロとして従来どおり fail-open (allow)
- `"inherit"` (case-insensitive) は「未指定」に正規化する。session_id が特定できない場合や、state file・pending マーカーのいずれも無い場合は fail-open (allow)
- 主防御はあくまで `CLAUDE_CODE_SUBAGENT_MODEL` env 設定。本 hook はその defense-in-depth + deny メッセージによる自己修正誘導が役割
- 既知の制約 3 点 (agent frontmatter の model 判定不能 / Workflow 内部の `agent()` 捕捉不能 / セッション途中の `/model` 切替検知不能) は下記「既知の制約」セクション参照

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

issue の起票・分解フェーズの手順をガイドします: 起票前の壁打ち、body template (背景 / 受入基準 / I/O 契約 / 制約 / 想定ファイル / 関連 issue の 6 セクション)、分割基準、関係設定コマンド (gh v2.94+ ネイティブ経路 + 旧版 fallback)、`#N` 相互参照と issue types 不使用の理由、親 issue の close 規約。

**使用シーン**:

- 「issue を起票する」
- 「issue に分解する」
- 「sub-issue を作る」

#### /issue-start

**ファイル**: `skills/issue-start/SKILL.md`

issue の着手・実装開始フェーズの手順をガイドします: pick-up 分岐 (既存の branch / PR 状態確認)、排他制御 (`rule:issue-claim` への参照)、軽微判定 (2 段構え)、spec-first 2 段階の具体コマンド手順 (局所定義・provisional 契約・Phase A 評価基準・成果物粒度・Phase B 内の進め方の 4.1〜4.5 を含む)、closing keyword。

**使用シーン**:

- 「issue に着手する」
- 「issue の実装を始める」
- 「issue を pick up する」

### CI (lint)

モデル別プロンプトファイルと `hooks.json` の 4 entries は内容を手で同期する必要があるため、うっかり片方だけ更新して drift する事故を CI で検出します (v0.7.1 新設、#178。v0.7.2 で検出カバレッジの穴 3 件を修正、#184 配下の #185 #186 #187。v0.13.0 でチェック 5 追加、issue #221。v0.15.0 でチェック 1/5 の母集合を 3 part の和集合に変更、issue #236)。

#### lint-prompt-sync.sh

**ファイル**: `scripts/lint-prompt-sync.sh` (plugin 直下、`hooks/` 配下ではない)
**呼び出し元**: `.github/workflows/agent-discipline-prompt-lint.yml`

**動作** (v0.7.2 で 2 チェック構成から 3 チェック構成に拡張、v0.13.0 でチェック 4/5 を追加した 5 チェック構成):

- **チェック 1 (ルール ID 一致)**: `hooks/prompts/always-fable.md` と `hooks/prompts/always-sonnet-{1,2,3}.md` の和集合から `<!-- rule:<id> -->` コメントの ID 集合を抽出し、順序に依らず完全一致するか検証する (issue #236、v0.15.0 で単一ファイルから 3 part の和集合へ変更)。片方にのみ存在する ID があれば diff 形式で報告して fail する。和集合を作る前に、まず各 part ファイル単体で rule ID マーカーが重複していないこと (`uniq -d` で検出。part 間ペアワイズ検査は自分自身と比較しないため単一ファイル内の重複を検出できず、和集合化がそれを無音で吸収してしまう盲点への対処、codex review P2 指摘) を検証し、次に 3 part 間で rule ID が重複していないこと (part 分割は rule 境界で行う契約) をペアワイズに検証する。いずれかで重複があれば fail する。ルール本文の表現差 (意味的ドリフト) は検出対象外とし、PR レビューでの目視確認に委ねる
- **チェック 2 (hooks.json 4 entries 共通ブロック一致)**: 抽出・正規化・比較より前に **前提検証** (v0.7.2 新設、#186) を行う — `hooks/hooks.json` の `type: agent` entry 数がスクリプト内定数 `EXPECTED_AGENT_ENTRIES` (= 4) と一致すること、および各 entry の `.prompt` が非空文字列であることを検証し、いずれか不成立なら fail する (entry 数の増減や prompt 欠落という前提崩壊時に、空同士の一致などで pass 側へ倒れることを防ぐ)。前提検証を通過した後、4 つの `type: agent` entry (`gh issue create` / `gh issue edit` / `gh pr create` / `gh pr edit`) の `prompt` から、entry 固有部分を除いた「共通ブロック」が一致するか検証する。entry 固有部分として除去する対象は 3 種類:
  1. 対象コマンド名の記載箇所 (`if` フィールドから機械導出した `gh <cmd>` をプレースホルダに置換)
  2. `gh pr create` のみが持つ Closes 検証 Step (Step 3) と、それに伴う「返り値」Step の番号繰り下がり (Step 4 → Step 3 相当への読み替え)。**除去 (v0.7.2、#187)** より前に、除去対象の Step 3 ブロックが実在することを検証し、実在しなければ fail する
  3. `gh pr create` / `gh pr edit` が共有する PR 固有の判定原則追加文 (「PR body で commit/discussion 経由でユーザ承認が明示されている文脈は禁止対象外」)。**除去 (v0.7.2、#187)** より前に、除去対象の文言が PR 系 2 entries それぞれに実在することを検証し、実在しなければ fail する
  正規化後の 4 entries が byte-identical でなければ diff 形式で乖離箇所を報告して fail する
- **チェック 3 (gh pr create Step 3 ブロック構造チェック、v0.7.2 新設、#185)**: チェック 2 の `norm_b` は `gh pr create` entry 固有の Step 3 (Closes 検証) ブロックを共通ブロック比較の対象外とするため丸ごと除去する。そのため Step 3 の判定手順がどのように破損しても、開始・終了の見出しパターンさえ残っていれば除去は成功し共通ブロック比較 (チェック 2) は pass してしまう (false pass)。これを埋めるため、除去される前の raw prompt から Step 3 ブロックを独立に抽出し、スクリプト内定数の必須キーワードリスト (`` `<cwd>/.git` ``、`gitdir:`、`ref: refs/heads/`、`issue-<数字>`、`closing keyword`、`境界一致`、`fail-open で誘導層の`、の 7 要素) をすべて含むかを検証する。期待構造のソース・オブ・トゥルースは README 等の外部文書ではなくスクリプト内定数とし (#185 の合意事項)、判定ロジックの意味的な等価性までは検証しない構造スモークチェックである旨を明記している (欠落があれば diff ではなく欠落キーワードの一覧を報告して fail する)
- **チェック 4 (分業規律 3 ファイルの rule ID 一致、#195。v0.21.0 で discipline-opus.md を追加し 3 ファイル総当たりへ拡張)**: `hooks/prompts/discipline-fable.md` を基準に `discipline-sonnet.md` / `discipline-opus.md` それぞれとの `<!-- rule:<id> -->` ID 集合 diff (2 diff、fable を hub にした推移律で 3 ファイルの完全一致を保証) を、チェック 1 と同じ抽出方式で検証する
- **チェック 5 (subagent-rules.md の rule ID サブセット検査、#221。v0.15.0 で母集合を和集合化)**: `hooks/prompts/subagent-rules.md` の rule ID 集合が `always-sonnet-{1,2,3}.md` の和集合 (チェック 1 で抽出・重複検査済みの集合) に含まれるかを片方向で検証する。含まれない ID があれば fail する (sonnet 側にのみ存在する ID は「subagent に配送しない」意図的な選択のため検査しない)
- **引数**: なし。**実行位置**: リポジトリルートを前提とする (それ以外や前提ファイル欠如は fail-closed で exit 1)。**依存**: `jq` (CI・ローカルとも前提。不在時は明確なエラーメッセージで exit 1)。**exit code**: 全チェック (1〜5) pass で 0、いずれか fail または実行時エラーで 1
- POSIX sh (`#!/bin/sh`) で記述しており `dash` でも動作する。ローカルでリポジトリルートから直接実行できる (`./plugins/agent-discipline/scripts/lint-prompt-sync.sh`)

#### agent-discipline-prompt-lint (workflow)

**ファイル**: `.github/workflows/agent-discipline-prompt-lint.yml`

`always-fable.md` / `always-sonnet-{1,2,3}.md` / `hooks.json` / `discipline-fable.md` / `discipline-sonnet.md` / `discipline-opus.md` / `subagent-rules.md` / lint スクリプト自身 / 本 workflow 自身のいずれかが変更された `push` (master 向け) / `pull_request` でのみ発火し、`ubuntu-latest` 上で `actions/checkout@v4` の後に `lint-prompt-sync.sh` を実行する。ubuntu-latest には `jq` が標準搭載されているため追加のセットアップ step は無い。

## 旧 plugin との関係 (移行ガイド)

agent-discipline は以下の 2 plugin を吸収統合しています:

| 旧 plugin | 吸収先 | 等価機能 |
|---|---|---|
| `decompose-bash` (v0.1.1) | inject-always.sh の「物理層」 セクション | Bash コマンド分解の `additionalContext` 注入 |
| `auto-followthrough` (v0.2.3) | inject-auto.sh + check-uncommitted-on-session-start.sh | auto mode 時の commit→push→PR→merge 自走 / 未コミット分類チェック |

旧 plugin の機能はそのまま維持しています。 v0.1.0 時点では旧 `auto-followthrough` の hook 構造 (`SessionStart` + `UserPromptSubmit` + `PostToolBatch`) も継承していましたが、 v0.1.1 で `PostToolBatch` 経路を撤去 + during 系を `inject-always.sh` 側に移動し、 現在は `SessionStart` + `UserPromptSubmit` の 2 経路構成です。 マーカー dir は `auto-followthrough-markers/` → `agent-discipline-markers/` に変更されており、 v0.1.1 では `inject-auto.sh` の dedup marker 自体も不要になっているため、 旧 marker は OS の tmpfs/tmp cleanup で自然に消去されます。

旧 2 plugin は本 plugin 導入時に同 PR で削除済みです。

## 設計上の選択

### なぜ統合 plugin か (vs 個別 plugin の維持)

このリポジトリは個人の Claude Code 開発スタイル marketplace です。 機能ごとに細かく plugin を分けると plugin 数が肥大化し、 enable list の見通しが悪くなります。 「物理層 + 思考層」 は抽象レイヤとしては別ですが、 個人運用では一括 on/off で問題が出ないため統合しました。

公開 marketplace でユーザに細かい on/off を提供する場合は分離が望ましいですが、 本リポジトリは個人運用前提のため統合粒度を採用しています。

### なぜ常時系と auto 系で hook event を分けるか

- **常時系 (inject-always.sh)**: 物理層 (Bash 分解) と before 系 (設計壁打ち / issue 規約 / closing keyword) と during 系 (自律作業中の判断境界) は permission_mode に依らず常に有用なので `SessionStart` で 1 回注入する。 トークンコストを抑えるため per-turn 再注入はしない
- **auto 系 (inject-auto.sh)**: after 系 (commit→push→PR→merge 自走パイプライン) は auto でのみ自動注入し、long-running session で薄れないよう `UserPromptSubmit` で per-turn 再注入する。v0.1.0 では `PostToolBatch` でも併送していたが、v0.1.1 で撤去した (per-turn 2 回 inject → 1 回に削減)

### 誘導層と検知層の defense-in-depth (v0.4.0 で物理層を追加)

v0.3.0 までは `additionalContext` 注入のみで Claude の自発的な遵守を期待する **誘導 (nudge)** だけでした。 v0.4.0 で PreToolUse type:agent hook を追加し、 issue / PR body に関しては「Claude が忘れたら hook が物理的に catch」 する **検知層** を追加しました。 両者は defense-in-depth として階層化されています:

| レイヤ | 機構 | 効き目 | 対象 leak 経路 |
|---|---|---|---|
| 誘導層 | SessionStart で additionalContext 注入 | Claude が自発的に self-check する確率を上げる | issue body / PR 説明 / plan / commit message / 実装コード (= 全 leak 経路) |
| 検知層 (v0.4.0) | PreToolUse type:agent hook 4 entries | `gh issue/pr create/edit` 経路の物理 intercept (誘導層の取りこぼし防止)。literal head prefilter により非該当 Bash では model を起動しない | `gh issue create/edit` / `gh pr create/edit` のうち `--body inline` / `--body-file PATH` 形式 |

検知層は対象範囲を限定的にしています (= `gh api` 直接叩き / editor 起動経路 / 実装コード内のコメント等は cover しない)。 これは誘導層 (= Claude の自発遵守) を主、 検知層を補助とする非対称設計です。 全 leak 経路を物理層で塞ぐと regex / semantic 判定の網羅が困難になり false positive / false negative が増えるため、 「Claude 自身に最も書きやすい経路 (`gh issue/pr create/edit`)」 だけを物理 catch する戦略を採っています。

なお、 master への直接 push のように **強い deny で構造的に止めるべきケース** は引き続き別 plugin (例: `git-guardrails`, `pre-push-review`) が担当します。 本 plugin の検知層は推奨マーク等の semantic 判定対象に限定されているため、 deny 系 hook を完全代替するものではありません。

## ディレクトリ構成

```
agent-discipline/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   ├── prompts/
│   │   ├── always-fable.md
│   │   ├── always-sonnet-1.md
│   │   ├── always-sonnet-2.md
│   │   ├── always-sonnet-3.md
│   │   ├── auto-mode.md
│   │   ├── delivery-note.md
│   │   ├── discipline-fable.md
│   │   ├── discipline-preamble-fable.md
│   │   ├── discipline-preamble-self-gate.md
│   │   ├── discipline-sonnet.md
│   │   ├── part-self-gate.md
│   │   ├── preamble-self-gate.md
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
│       ├── lib/
│       │   └── permission-mode.sh
│       └── resolve-model-on-prompt.sh
├── skills/
│   ├── issue-plan/
│   │   └── SKILL.md
│   └── issue-start/
│       └── SKILL.md
├── scripts/
│   └── lint-prompt-sync.sh
└── README.md
```

`always-sonnet-1.md` / `always-sonnet-2.md` / `always-sonnet-3.md` は issue #236 (v0.15.0) で単一ファイル `always-sonnet.md` を rule 境界で 3 分割したもの。`delivery-note.md` / `part-self-gate.md` も同 issue で新設した (それぞれ SessionStart / UserPromptSubmit の自己ゲート・配送前置き用)。`temporary/` は問題修正までの一時規律ディレクトリで、中身の md を削除すると注入が消える (v0.12.0)。

`.github/workflows/agent-discipline-prompt-lint.yml` (リポジトリ直下、plugin 配布に含まれない CI 専用 workflow) が `scripts/lint-prompt-sync.sh` を呼び出します。

## 必要な実行環境

- `bash`
- `jq`
- POSIX `sh` (`lint-prompt-sync.sh` の実行、CI (`ubuntu-latest`) およびローカル)
- `git` (check-uncommitted-on-session-start.sh の worktree 解決)

## 関連プラグイン

- [git-guardrails](../git-guardrails/) — master への直接 push を禁止する PreToolUse deny hook。 本 plugin の Bash 分解規律が機能してこそ deny が正しく届く
- [pre-push-review](../pre-push-review/) — push 前にレビューループを強制する PreToolUse hook。 同じく Bash 分解規律の上で機能する
- [auto-lint-check](../auto-lint-check/) — Edit/Write 前の linter チェック。 同上
- [update-default-branch](../update-default-branch/) — マージ完了後のデフォルトブランチ最新化 Skill。 after 系の自走パイプラインから自然に呼び出される

## 既知の制約

- **誘導層は強制ではない**: `additionalContext` の追加だけなので Claude が指示を無視することは原理的に可能。 v0.4.0 で gh issue/pr 経路のみ検知層 (PreToolUse type:agent hook) を追加したが、 それ以外の leak 経路 (`gh api` 直接叩き / editor 起動経路 / 実装コード内コメント等) は誘導層のみ
- **検知層の `if` filter は literal prefix match のみ対応で、 一部の gh CLI 呼び出し形式は bypass する**: 公式 syntax `Bash(prefix:*)` は先頭固定 prefix match のため、 以下の形式は検知層を bypass する (= agent hook が発火せず誘導層のみが防衛):
  - **global option を subcommand 前に置く形式**: `gh -R owner/repo issue create ...` / `gh --repo owner/repo pr create ...` (= cross-repo 操作で頻出するが、 通常は `cd` で repo に入って操作するため Claude のデフォルト出力では稀)
  - **env-prefix 形式**: `GH_TOKEN=... gh issue create ...` / `GH_REPO=... gh pr create ...` (= auth 切替や repo override で稀に使う)
  - **wrapper 経路**: `eval "gh issue create ..."` / `bash -c "..."` / `xargs gh ...` (= 既に section 1 Bash 分解規律で禁止されているため、 規律遵守時には発生しない)
  - **compound command 経路 (`cd dir` prefix)**: `cd repo && gh issue create ...` のような形式は section 1 Bash 分解規律で「cwd 制約の場合の例外」 として allowed だが、 検知層の Step 0 は先頭 literal 一致のみ判定するため bypass される (= 上流の section 1 例外と検知層の strict head check が非対称、 当該 compound 形式は誘導層のみが上流防衛)
  - **PreToolUse の構造的 TOCTOU (`cat ... && gh ... -F body.md` 系)**: heredoc 等で body file を生成する compound (例: `cat > body.md <<'EOF' ... EOF && gh issue create -F body.md`) は PreToolUse hook が Bash 実行 **前** に発火するため、 body file は hook 時点で未生成 → Read tool で取得不能。 仮に検知層が compound を catch しても validate 不能 (TOCTOU 構造)。 そもそも section 1 の「不関連 command の連結」 禁止規律で発生抑制される
  - これらは誘導層 (section 2.1 / 3.1 の禁止表現規範) が上流防衛として catch する想定。 完全に塞ぐには parser-backed command hook (= 別 plugin として再設計) が必要だが、 v0.4.0 の小修正範囲を超えるため意図的に既知制約として残している
- **検知層の SPOF**: 検知層は LLM 呼び出しに依存するため、 hook の model (`claude-sonnet-5`) が API 不可用な状況では `gh issue/pr create/edit` が PreToolUse error で失敗する。 narrow scope と model pin で「実装系メインセッションが動いている時は hook も動く」 対称構造に閉じているが、 個別 call の transient エラー (rate limit / network blip) は残る
- **model pin は env var の影響を受けない** (#151/#174 V2 実測、v0.7.0): `CLAUDE_CODE_SUBAGENT_MODEL` env var は hooks.json の `type: agent` hook の `model` field を上書きしない。 pin 値は env var の設定有無に関わらず常に dispatch される確定値であり、 「env 未設定環境向けの既定」 ではない。 実測の詳細は #174 のコメント参照
- **Fable メインセッション時は model pin の対称性が崩れる** (#151、v0.7.0): 検知層の model pin (`claude-sonnet-5`) は実装系メインセッション (Sonnet) およびこの環境の全 subagent と同系列だが、 メインセッションが Fable の場合はこの対称性が成立しない (= メインセッションは Fable で正常動作していても、 hook は Sonnet 側の障害時に落ちうる)。 発生確率は Fable メインセッションでの `gh issue/pr create|edit` 実行頻度に依存するが、 構造的には未解消の非対称として残る
- **検知層は公式ドキュメント上 experimental な type:agent hook に依存** (#153、v0.7.0): PreToolUse `type: agent` hook は Claude Code 公式ドキュメントで experimental (実験的機能) と位置付けられており、 将来の仕様変更で挙動が変わる、 または廃止される可能性がある。 検知層全体 (4 entries すべて) がこの機能に依存しているため、 仕様変更時は検知層が機能しなくなりうる (= その場合は誘導層のみが防衛する状態に自然縮退する。 fail-open 設計のため縮退時に semantic 誤 block が発生することはない)
- **検知層の model pin は手動メンテナンス**: Claude Code 自体の session model を upgrade した場合 (例: sonnet-5 → sonnet-6)、 `hooks/hooks.json` の `model` field も手動同期しないと SPOF 構造が再来する (= 古い model のみダウン時に hook だけ落ちる経路が復活)
- **check-uncommitted の発火タイミング制約**: 最初のプロンプト時点で worktree が clean だと、 同 session 中に後から発生した未コミット変更は検知しない (上記参照)
- **`model` フィールド欠落条件は compaction 後が公式未記載** (v0.5.0、#174 V3 実測調査): 公式ドキュメントは `/clear` 後と conversation recovery でセッションが復元された場合の 2 つを model 欠落条件として明記するが、`SessionStart (source=compact)` 時の扱いは明記していない (欠落しない保証も無い)。いずれの場合も fallback chain (transcript 解析 → state file) が source 非依存に欠落を吸収するため、実装上の場合分けは発生しない
- **セッション途中の `/model` 切替は次の SessionStart まで反映されない** (#157 と同型の制約。v0.8.0 で統合した `block-fable-subagent.sh` も同種の制約を持つ、本セクション内の該当項目を参照): fallback chain の判定は `SessionStart` (startup / resume / clear / compact) でのみ行われるため、`/model` で切替えても注入済みプロンプトは次の SessionStart まで旧モデル向けのまま。次の SessionStart では、`.model` があればその値で、無くても transcript に切替後の main-chain assistant 行があれば transcript 解析 (fallback chain 2 段目) で新モデルが反映される。transcript も空 / 読めない場合に限り state file キャッシュに落ちるため、その経路でのみ旧モデル向け注入が継続しうる
- **one-shot 補正は判定不能セッションの最初の assistant 応答が生成されるまで暫定適用が続く**: pending マーカーが存在し transcript に main-chain assistant 行が現れて初めて確定するため、それまでの `UserPromptSubmit` では自己ゲート付きの `always-sonnet-1.md` + `always-sonnet-2.md`/`always-sonnet-3.md` (常時ルール、`resolve-model-on-prompt.sh` / `inject-rules-part.sh` が判定) と `discipline-preamble-self-gate.md` + `discipline-sonnet.md` (分業規律、v0.9.0。issue #236 以降は `inject-discipline.sh` が配送) が暫定適用され続ける
- **state file / pending マーカーは OS の tmp cleanup による自然消去のみ**: `${TMPDIR:-/tmp}/agent-discipline-state/` 配下に明示的なリトジ (retention) 処理は無く、`check-uncommitted-on-session-start.sh` が使う `agent-discipline-markers/` とは別 namespace を使う
- **`block-fable-subagent.sh` は agent 定義 frontmatter の `model` を判定できない** (v0.8.0): frontmatter の `model` は `tool_input` に現れないため、env 不在 + model 未指定 + frontmatter が fable を指す構成は本 hook では捕捉不能 (env 側でカバー)。fork subagent (model 指定を無視して親モデルを継承する型) も同様に deny しない (誘導層の「原則使用しない」文言のみで運用する設計判断)
- **`block-fable-subagent.sh` は Workflow ツール内部の `agent()` 呼び出しを PreToolUse で捕捉できない** (v0.8.0): PreToolUse はメインループのツール呼び出しにのみ発火するため、Workflow スクリプト内部のサブエージェントスポーンは本 hook の対象外 (env 側でカバー)
- **`block-fable-subagent.sh` はセッション途中の `/model` 切替を検知できない** (v0.8.0): model を含む hook 入力は `SessionStart` のみで、`$CLAUDE_MODEL` 環境変数も存在しない。env 不在時は state file が次の `SessionStart` まで stale になり、fable への切替は素通り (旧 state で allow)、fable からの切替は誤 deny になる (deny メッセージの model 明示誘導で自己修復可能)
- **compact 直後のギャップ** (issue #236、v0.15.0): `SessionStart(source=compact)` 後、次のユーザプロンプトまでは part 1 要素 (delivery-note + `always-fable.md` / `always-sonnet-1.md`) のみが再注入され、残りの要素 (part 2/3・分業規律) は再配送されない (`UserPromptSubmit` はユーザプロンプトでしか発火しないため)。compact 後に agentic loop が自動継続する経路では、この間の推論は part 1 の delivery-note (自己修復指示) と compact summary 内の痕跡に依存する。従来設計でも同経路では persisted-output (2KB プレビュー) しか届いていなかったため、劣化ではない
- **判定不能 → Fable / Opus 確定の補正遅延** (issue #236、v0.15.0。v0.21.0 で Opus 系にも拡張): 分業規律の Fable / Opus 補正は `resolve-model-on-prompt.sh` の state 書込と `inject-discipline.sh` の読み取りが同一 event 内で並列競合した場合、最大 1 プロンプト遅れて配送される (誤配送はしない)
- **exactly-once は保証しない** (issue #236、v0.15.0): hook 出力に配送 ACK が無いため、マーカー書込後に配送が失われた場合の再送はできない (SessionStart での全マーカーリセットが回復手段)。逆に TMPDIR 掃除等でマーカーが消えた場合は再配送される (重複は無害)
- **state / pending の両方が書けない持続障害下の床** (issue #236、v0.15.0): `inject-always.sh` で state 書込と pending 作成が両方失敗した場合 (TMPDIR が持続的に書込不能等)、後続スクリプトは旧 state (読めれば) または両不在フォールバックに基づいて配送する。`/model` 切替を跨いだ旧 state が残っていると誤ったモデル変種が配送されうるが、この露出は state 書込失敗を無視していた v0.14.0 以前にも存在する
- **pending 削除失敗時の補正遅延** (issue #236、v0.15.0。v0.21.0 で Opus 系にも拡張): `resolve-model-on-prompt.sh` が state 書込に成功した後の pending 削除に失敗した場合、優先規則 (pending 優先) により分業規律の Fable / Opus 補正は次の SessionStart (マーカーリセット + pending 掃除) まで配送されない。常時ルールの Fable 確定版 (prefix + `always-fable.md`) は配送済み (Opus 系は常時ルールが元々 Sonnet 版のまま変わらない) のため、規律の主要部は欠落しない

## 関連情報

- [Claude Code Hooks ドキュメント](https://code.claude.com/docs/en/hooks)
