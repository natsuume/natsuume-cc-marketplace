# agent-discipline プラグイン

Claude Code の振る舞い規律 (= agent としての discipline) を統合配送する system prompt plugin です。 旧 [decompose-bash](https://github.com/natsuume/natsuume-cc-marketplace/tree/93e5e9aa0c4dadb2e2eb13fb38c87b34cf3d10e0/plugins/decompose-bash) と [auto-followthrough](https://github.com/natsuume/natsuume-cc-marketplace/tree/93e5e9aa0c4dadb2e2eb13fb38c87b34cf3d10e0/plugins/auto-followthrough) を吸収し、 「物理層 + before / during / after」 の 4 段構成で additionalContext を注入します。

## バージョン

v0.3.0

### v0.2.0 → v0.3.0 の変更点

- **セクション 2 / 3 を「思考は自由、 成果物への固定化は要承認」 非対称ルールに強化**: Claude が設計レベルで複数案 (A 案 / B 案 / C 案) を検討した際、 ユーザに `AskUserQuestion` で意思決定を委ねず、 自分で推奨を決めて issue body / PR 説明 / plan / commit message に「**A 案 (推奨)**」 のような形で固定化してしまう failure mode への構造的対策。 issue 駆動開発の前提として **issue body は後続 session の AI agent にとって唯一の信頼ソース** であり、 そこへ独断推奨が混入すると後続 session が「既決事項」 として読み取って実装が走るため、 ユーザの意思決定が完全に skip される
- 規律の checkpoint を「思考の中」 ではなく **「成果物への書き出しの瞬間」** に置く非対称構造を採用 (= 検討段階での複数案比較や推奨案の思考自体は禁止しない、 成果物へ書き出す前に必ず `AskUserQuestion` を通す)
- **セクション 2.1 (新規)**: 自己検知トリガー 8 項目を名指しで列挙 — 推奨マーキング (「(推奨)」 「(default)」 「first choice」 「望ましい」 「自然」 「Recommended: ...」) / 独断の正当化 / 比較表で勝者決定 / 暗黙の決め打ち (粒度差) / 「とりあえず」 系 / 暫定マーク残置 / ユーザ判断の先回り代弁 / 「選択点なし」 即断 (反対案 1 つを仮想して再確認)。 auto mode 中であっても設計選択点は `reasonable call` の対象外と宣言。 提示時の中立列挙 (粒度を揃える + 序列を付けない) も明文化し、 確定済み事項への rationale 記述は禁止対象外と明示
- **セクション 3.1 (新規)**: 起票直前 / pick up 時の self-check 4 項目 — セクション 2.1 禁止表現の混入チェック、 受入基準への未承認選択埋め込みチェック、 後続 session 視点の既決事項誤読余地チェック、 未決定表現の残置チェック。 **遡及適用** として過去 session が埋め込んだ独断を pick up 時に検出して再確認する規律も追加 (= 独断の世代間継承を断つ)
- **セクション 3.2 (新規)**: 同じ規律を PR 説明 / plan ファイル / commit message へ明示拡張

### v0.1.1 → v0.2.0 の変更点

- **セクション 7 (連続 issue 解決時の排他制御) を追加**: `/goal` のように複数 issue を順次解決するフローや、 他 session が並列稼働している場面で発生していた **誤着手** (= 同 issue への重複着手) と **ラベル誤削除** 事故への構造的対策。 `ai:in-progress` ラベル単独だと TOCTOU race と「誰が付けたラベルか不明」 という根本問題があったため、 (a) GitHub comment の serial ID + timestamp による先着判定 (claim comment) と (b) git server-side で確定的に排他される branch push の二段構成で排他制御する。 claim comment 本文に `branch=<prefix>/issue-<N>-<slug>` を埋め込むことで「誰の claim か」 を確定的に識別できるようになり、 他 session の claim / branch / ラベルを誤って削除する事故も構造的に排除される。 branch 名規約は `<prefix>/issue-<N>-<slug>` (例: `feat/issue-12-add-auth`) を採用

### v0.1.0 → v0.1.1 の変更点

- **during 系を `inject-always.sh` に移動**: 「実装自走の判断境界」 は `permission_mode` 非依存の行動指針なので、 auto 限定の `inject-auto.sh` から SessionStart 配送の `inject-always.sh` に移動。 default / acceptEdits などの mode でも届くようになった
- **`PostToolBatch` 経路と once-per-turn dedup logic を撤去**: 旧 auto-followthrough 由来の `PostToolBatch` 配送を削除し、 `UserPromptSubmit` 単独に変更。 per-turn 2 回 inject (UserPromptSubmit + PostToolBatch) が 1 回 (UserPromptSubmit のみ) に削減され、 トークン消費が半減 (`inject-auto.sh` 本文 ~1.5k tokens × turn 数の節約)。 once-per-turn dedup logic (`${TMPDIR:-/tmp}/.../batch-injected` marker、 case 分岐、 session_id sanitize) も同時撤去で `inject-auto.sh` が ~25 行短縮

## 概要

Claude Code に「個人の開発スタイル」 を一括で適用するための plugin です。 機能ごとに別 plugin に分けず、 1 plugin 内に複数のルール群を集約することで、 個人 marketplace の plugin 数肥大化を抑えます。

注入される規律は次の 5 レイヤに分かれます:

| レイヤ | 配送経路 | inject 条件 | 内容 |
|---|---|---|---|
| **物理層** | `SessionStart` (inject-always.sh) | 常時 | Bash コマンドを最小粒度に分解して PreToolUse hook の取りこぼしを防ぐ |
| **before 系** | `SessionStart` (inject-always.sh) | 常時 | 設計 / 仕様の事前壁打ち + 「思考は自由、 成果物への固定化は要承認」 非対称ルール (2.1) + 自己検知トリガー / 名指し禁止表現、 issue 起票時の `AskUserQuestion` 詳細化 + 起票直前 / pick up 時の self-check + 過去 session 独断の遡及検出 (3.1 / 3.2 で PR / plan / commit にも適用)、 並列粒度 + sub-issue + `#N` 相互参照、 PR closing keyword 規約 |
| **during 系** | `SessionStart` (inject-always.sh) | 常時 (`permission_mode` 非依存) | 実装は自走、 設計 / 仕様 (= issue 起票時の壁打ちで決まっているはずの内容) の再確認では止まらない。 ただし issue 未明記の要件発見 / 大きな後戻り判断では止まる |
| **排他系** (v0.2.0) | `SessionStart` (inject-always.sh) | 常時 (`permission_mode` 非依存) | 連続 issue 解決フロー (例: `/goal`) や並列 session 下で同 issue への重複着手を防ぐ。 claim comment (先着判定) + branch push (確定的排他) の二段構成で、 claim comment 本文に `branch=<prefix>/issue-<N>-<slug>` を埋め込んで誰の claim か識別可能にする |
| **after 系** | `UserPromptSubmit` (inject-auto.sh) | `permission_mode == "auto"` 時のみ | 変更が一段落したら commit → push → PR 作成 → (4 条件 hard gate を満たしたら) マージまで自走 |

加えて、 auto mode セッションで `UserPromptSubmit` 初回発火時に cwd の未コミット変更を分類確認する独立 hook (`check-uncommitted-on-session-start.sh`) を併走させます。

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install agent-discipline@natsuume-plugins
```

## 機能一覧

### Hooks

#### inject-always

**ファイル**: `hooks/scripts/inject-always.sh`
**イベント**: `SessionStart`

**動作**:

- セッション開始時に「常時適用ルール」 をまとめて `additionalContext` として注入する
- `SessionStart` は `startup` 以外に `resume` / `clear` / `compact` でも発火するため同一セッション内で複数回呼ばれる可能性があるが、 注入内容は static なので重複しても害は無い (毎回コンテキストトークンを再消費する点に留意)
- 入力 JSON から `hook_event_name` を読み取り `hookSpecificOutput.hookEventName` に同じ値を設定 (誤った既定値で別 event の文脈に誘導しないため)
- `jq` 不在 / 不正 JSON 入力ではすべて無音 `exit 0` (フェイルセーフ)

**注入内容の要約**:

1. **Bash コマンド分解** (物理層): `&&` / `||` / `;` / `&` / `$(...)` / バッククォート / `eval` / `sh -c` / `xargs` / `find -exec` を分解対象、 パイプライン `|` は単一論理操作のみ許容、 `cd $dir && cmd` やトランザクション的合成は例外
2. **設計 / 仕様検討の事前明確化**: スコープ / 要件 / 受入基準 / I/O 契約 / 公開命名などの後戻りコストが大きい判断は `AskUserQuestion` で事前に詰める。 軽微な実装判断は対象外。 **2.1 (v0.3.0 新規)** 「思考は自由、 成果物への固定化は要承認」 非対称ルール: 検討段階での複数案比較・推奨思考は許容するが、 結論を issue body / PR 説明 / plan / commit に書き出す前に必ず `AskUserQuestion` を通す。 8 つの自己検知トリガー (推奨マーキング / 独断の正当化 / 比較表で勝者決定 / 暗黙の決め打ち / 「とりあえず」 系 / 暫定マーク残置 / ユーザ判断の先回り代弁 / 「選択点なし」 即断) と中立列挙の提示規約を明示
3. **issue 起票時の詳細化**: 実装時に判断が発生しないよう `AskUserQuestion` で詳細化。 起票内容は **issue body に全埋め込み** (補助 file には書かない)。 **3.1 (v0.3.0 新規)** issue body は「ユーザが承認した契約書」 と捉え、 起票直前 / pick up 時の self-check (禁止表現混入 / 受入基準への未承認選択埋め込み / 後続 session 視点の既決事項誤読余地 / 未決定表現の残置) + 遡及適用 (過去 session 独断の検出) で独断 leak を塞ぐ。 **3.2 (v0.3.0 新規)** PR 説明 / plan / commit にも同じ禁止表現規律を拡張
4. **issue の粒度と関係性**: 独立して並列作業できる粒度で起票、 大きい場合は sub-issues 分割。 関係性は (a) sub-issue 親子リンク + (b) `#N` 相互参照 を併用
5. **PR 作成時の closing keyword**: 完全解決時のみ PR body に `Closes #N` を書く。 部分対応では `Refs #N` / `Part of #N` に切替
6. **自律作業中の判断境界** (during 系、 v0.1.1 で `inject-auto.sh` から移動): 実装は自走、 設計 / 仕様 (= issue で決まっているはずの内容) は再確認しない。 ただし issue 未明記の要件発見 / 大きな後戻り判断では止まる。 軽微な判断 (変数名 / import 順 / docstring など) は逐一確認しない (`permission_mode == "auto"` 時は reasonable assumption、 それ以外は harness の permission prompt に委ねる)
7. **連続 issue 解決時の排他制御** (排他系、 v0.2.0 で追加): `/goal` 等の並列 session フロー向け。 (a) `gh issue view` で `ai:in-progress` ラベル / claim comment 早期判定、 (b) claim comment 投稿 (`🔒 ai:claim branch=<prefix>/issue-<N>-<slug> ts=<ISO 8601>`)、 (c) 3 秒待機 + 先着 timestamp 比較で他 session 検知、 (d) 作業 branch 切って空 commit + 即 push で確定的排他、 (e) push 成功時のみ `ai:in-progress` ラベル付与。 ラベル削除規律: PR merge 時のみ、 claim comment の `branch=` 値が自分の作業 branch と一致する場合のみ削除可。 撤退時は claim comment + branch を削除し、 ラベルは残す

#### inject-auto

**ファイル**: `hooks/scripts/inject-auto.sh`
**イベント**: `UserPromptSubmit`

**動作**:

- 入力 JSON から `permission_mode` を読み取り、 `"auto"` のときのみ `additionalContext` を出力
- それ以外 (`default` / `plan` / `acceptEdits` / `bypassPermissions`) では無音 `exit 0`
- `hook_event_name` を入力からそのまま読み取り `hookSpecificOutput.hookEventName` に同じ値を設定
- `jq` 不在 / 不正 JSON 入力ではすべて無音 `exit 0` (フェイルセーフ)

**なぜ `UserPromptSubmit` か (SessionStart ではなく)**:

- `permission_mode` の動的判定が必要。 ユーザは session 中に `/permissions` 等で auto を on/off できるが、 SessionStart は session 開始時の値しか見えない。 `UserPromptSubmit` は毎ターン input に最新の `permission_mode` が乗る
- after 系の自走方針は long-running session で薄れると致命的 (= 自走パイプラインが停止する) なので、 per-turn 再注入で方針を維持する

**注入内容の要約**:

- 変更が一段落したら commit → push → PR 作成まで自走、 マージは 4 条件 hard gate を満たした場合のみ独断マージ
  - 4 条件: draft 解除済み / 必須 CI checks 全成功 / 必要な承認あり / `mergeable == MERGEABLE && mergeStateStatus == CLEAN`
- 禁止 / 要確認: master への直接 push / 破壊的操作 / 秘匿情報コミット / 4 条件未充足の独断マージ

#### check-uncommitted-on-session-start

**ファイル**: `hooks/scripts/check-uncommitted-on-session-start.sh`
**イベント**: `UserPromptSubmit` (session 内初回のみ)

**動作**:

- auto モードのセッションで cwd に未コミット変更がある場合、 **Claude にその出所分析と分類確認を要求** する `additionalContext` を注入する
- session ごとに 1 回だけ発火するよう `${TMPDIR:-/tmp}/agent-discipline-markers/<session_id>.checked` でマーカー管理
- auto モード以外、 git リポジトリ外、 `jq` 不在環境ではすべて無音 `exit 0`

> **発火タイミングの注意**: ファイル名は `-on-session-start` ですが、 `SessionStart` イベントではなく **`UserPromptSubmit` イベント** で発火します (session 内で最初に処理されたプロンプトでのみ動作)。 マーカーは `git status` 実行より前に置かれる (無限ループ回避のための意図的トレードオフ) ため、 **最初のプロンプト時点で worktree が clean だと、 同 session 中に後から発生した未コミット変更は検知しません**。 後続の dirty も拾いたい場合は新しい session を開始してください。

**Claude への指示内容 (要約)**:

ユーザに確認を丸投げするのではなく、 以下を Claude が一次分析するよう要求します:

1. `git diff` / `git log` / ファイル内容を確認して各変更の出所を推定
2. 各ファイルを 4 分類 (今回タスク関連 / 以前の残骸 / 中間状態 / 不明) に振り分け
3. 推奨アクションをまとめて簡潔にユーザに報告し、 同意を取る
4. ユーザの同意を得てから実際の git 操作 (add / commit / stash / branch 切り出し等) を行う

これにより auto mode の本来の趣旨「Claude に最大限委任する」 を維持しつつ、 意図しない変更を巻き込むリスクを抑えます。

## 旧 plugin との関係 (移行ガイド)

agent-discipline は以下の 2 plugin を吸収統合しています:

| 旧 plugin | 吸収先 | 等価機能 |
|---|---|---|
| `decompose-bash` (v0.1.1) | inject-always.sh の「物理層」 セクション | Bash コマンド分解の `additionalContext` 注入 |
| `auto-followthrough` (v0.2.3) | inject-auto.sh + check-uncommitted-on-session-start.sh | auto mode 時の commit→push→PR→merge 自走 / 未コミット分類チェック |

旧 plugin の機能はそのまま維持しています。 v0.1.0 時点では旧 `auto-followthrough` の hook 構造 (`SessionStart` + `UserPromptSubmit` + `PostToolBatch`) も継承していましたが、 v0.1.1 で `PostToolBatch` 経路を撤去 + during 系を `inject-always.sh` 側に移動し、 現在は `SessionStart` + `UserPromptSubmit` の 2 経路構成です (詳細は v0.1.1 changelog 参照)。 マーカー dir は `auto-followthrough-markers/` → `agent-discipline-markers/` に変更されており、 v0.1.1 では `inject-auto.sh` の dedup marker 自体も不要になっているため、 旧 marker は OS の tmpfs/tmp cleanup で自然に消去されます。

旧 2 plugin は本 plugin 導入時に同 PR で削除済みです。

## 設計上の選択

### なぜ統合 plugin か (vs 個別 plugin の維持)

このリポジトリは個人の Claude Code 開発スタイル marketplace です。 機能ごとに細かく plugin を分けると plugin 数が肥大化し、 enable list の見通しが悪くなります。 「物理層 + 思考層」 は抽象レイヤとしては別ですが、 個人運用では一括 on/off で問題が出ないため統合しました。

公開 marketplace でユーザに細かい on/off を提供する場合は分離が望ましいですが、 本リポジトリは個人運用前提のため統合粒度を採用しています。

### なぜ常時系と auto 系で hook event を分けるか

- **常時系 (inject-always.sh)**: 物理層 (Bash 分解) と before 系 (設計壁打ち / issue 規約 / closing keyword) と during 系 (自律作業中の判断境界) は permission_mode に依らず常に有用なので `SessionStart` で 1 回注入する。 トークンコストを抑えるため per-turn 再注入はしない
- **auto 系 (inject-auto.sh)**: after 系 (commit→push→PR→merge 自走パイプライン) は auto mode 時のみ意味があり、 long-running session で薄れると致命的 (= 自走パイプラインが止まる) なので `UserPromptSubmit` で per-turn 再注入する。 v0.1.0 では `PostToolBatch` でも併送していたが、 once-per-turn dedup を入れて 1 回に絞っていた事実が「`PostToolBatch` なしで `UserPromptSubmit` 単独で足りる」 ことを暗に示していたため、 v0.1.1 で撤去した (per-turn 2 回 inject → 1 回に削減)

### 強制ではなく誘導

`additionalContext` を Claude に渡すだけなので、 Claude が指示を無視することは原理的に可能です。 確実に止めたいケースは別途 PreToolUse の deny hook (例: `git-guardrails`, `pre-push-review`) を組む必要があります。 本 plugin は「Claude が自発的に規律に従う」 確率を上げる **誘導** であり、 強制ではありません。

## ディレクトリ構成

```
agent-discipline/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       ├── inject-always.sh
│       ├── inject-auto.sh
│       └── check-uncommitted-on-session-start.sh
└── README.md
```

## 必要な実行環境

- `bash`
- `jq`
- `git` (check-uncommitted-on-session-start.sh のみ)

## 関連プラグイン

- [git-guardrails](../git-guardrails/) — master への直接 push を禁止する PreToolUse deny hook。 本 plugin の Bash 分解規律が機能してこそ deny が正しく届く
- [pre-push-review](../pre-push-review/) — push 前にレビューループを強制する PreToolUse hook。 同じく Bash 分解規律の上で機能する
- [auto-lint-check](../auto-lint-check/) — Edit/Write 前の linter チェック。 同上
- [update-default-branch](../update-default-branch/) — マージ完了後のデフォルトブランチ最新化 Skill。 after 系の自走パイプラインから自然に呼び出される

## 既知の制約

- **強制ではなく誘導**: `additionalContext` の追加だけなので Claude が指示を無視することは原理的に可能。 確実に止めたいケースは別途 deny 判定の hook を組む必要がある
- **`permission_mode` の値が `"auto"` リテラルであること前提**: Claude Code 側の仕様変更で値が変わると inject-auto.sh は無音になる。 その場合は無効化されるだけで誤動作はしない
- **check-uncommitted の発火タイミング制約**: 最初のプロンプト時点で worktree が clean だと、 同 session 中に後から発生した未コミット変更は検知しない (上記参照)

## 関連情報

- [Claude Code Hooks ドキュメント](https://code.claude.com/docs/en/hooks)
