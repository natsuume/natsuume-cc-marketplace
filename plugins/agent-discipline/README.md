# agent-discipline プラグイン

Claude Code の振る舞い規律 (= agent としての discipline) を統合配送する system prompt plugin です。 旧 [decompose-bash](https://github.com/natsuume/natsuume-cc-marketplace/tree/93e5e9aa0c4dadb2e2eb13fb38c87b34cf3d10e0/plugins/decompose-bash) と [auto-followthrough](https://github.com/natsuume/natsuume-cc-marketplace/tree/93e5e9aa0c4dadb2e2eb13fb38c87b34cf3d10e0/plugins/auto-followthrough) を吸収し、 「物理層 + before / during / after」 の 4 段構成で additionalContext を注入します。

## バージョン

v0.1.0 (初版)

## 概要

Claude Code に「個人の開発スタイル」 を一括で適用するための plugin です。 機能ごとに別 plugin に分けず、 1 plugin 内に複数のルール群を集約することで、 個人 marketplace の plugin 数肥大化を抑えます。

注入される規律は次の 4 レイヤに分かれます:

| レイヤ | 配送経路 | inject 条件 | 内容 |
|---|---|---|---|
| **物理層** | `SessionStart` (inject-always.sh) | 常時 | Bash コマンドを最小粒度に分解して PreToolUse hook の取りこぼしを防ぐ |
| **before 系** | `SessionStart` (inject-always.sh) | 常時 | 設計 / 仕様の事前壁打ち、 issue 起票時の AskUserQuestion 詳細化、 並列粒度 + sub-issue + #N 相互参照、 PR closing keyword 規約 |
| **during 系** | `UserPromptSubmit` + `PostToolBatch` (inject-auto.sh) | `permission_mode == "auto"` 時のみ | 実装は自走、 設計 / 仕様の再確認では止まらない。 ただし issue 未明記の要件発見 / 大きな後戻り判断では止まる |
| **after 系** | `UserPromptSubmit` + `PostToolBatch` (inject-auto.sh) | `permission_mode == "auto"` 時のみ | 変更が一段落したら commit → push → PR 作成 → (4 条件 hard gate を満たしたら) マージまで自走 |

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
2. **設計 / 仕様検討の事前明確化**: スコープ / 要件 / 受入基準 / I/O 契約 / 公開命名などの後戻りコストが大きい判断は `AskUserQuestion` で事前に詰める。 軽微な実装判断は対象外
3. **issue 起票時の詳細化**: 実装時に判断が発生しないよう `AskUserQuestion` で詳細化。 起票内容は **issue body に全埋め込み** (補助 file には書かない)
4. **issue の粒度と関係性**: 独立して並列作業できる粒度で起票、 大きい場合は sub-issues 分割。 関係性は (a) sub-issue 親子リンク + (b) `#N` 相互参照 を併用
5. **PR 作成時の closing keyword**: 完全解決時のみ PR body に `Closes #N` を書く。 部分対応では `Refs #N` / `Part of #N` に切替

#### inject-auto

**ファイル**: `hooks/scripts/inject-auto.sh`
**イベント**: `UserPromptSubmit`, `PostToolBatch`

**動作**:

- 入力 JSON から `permission_mode` を読み取り、 `"auto"` のときのみ `additionalContext` を出力
- それ以外 (`default` / `plan` / `acceptEdits` / `bypassPermissions`) では無音 `exit 0`
- `hook_event_name` を入力からそのまま読み取り `hookSpecificOutput.hookEventName` に同じ値を設定 (UserPromptSubmit / PostToolBatch どちらの呼び出しでも同一スクリプトで処理可能)
- `PostToolBatch` の once-per-turn 制御: `${TMPDIR:-/tmp}/agent-discipline-markers/<session_id>.batch-injected` をマーカーとして、 同一ターン内の重複注入を防止 (transcript 肥大化と古い文脈の埋没を防ぐ)
- `jq` 不在 / 不正 JSON 入力ではすべて無音 `exit 0` (フェイルセーフ)

**なぜ 2 つのイベントが必要か**:

- `UserPromptSubmit` — 新しいユーザ入力ごとにモードを再確認して方針を再注入する。 ユーザが auto を on/off したタイミングを取り逃がさない
- `PostToolBatch` — 1 ターンのツール呼び出しが**全て完了した直後**に発火。 ここで再注入することで「編集だけして完了と打ち切る」 のを抑止し、 commit / PR / マージへの遷移を促す

**注入内容の要約**:

- **during 系**: 実装は自走。 設計 / 仕様 (= issue 起票時の壁打ちで決まっているはずの内容) を再確認する場面で止まらない。 ただし issue 未明記の要件発見 / 大きな後戻り判断では止まる
- **after 系**: 変更が一段落したら commit → push → PR 作成まで自走、 マージは 4 条件 hard gate を満たした場合のみ独断マージ
  - 4 条件: draft 解除済み / 必須 CI checks 全成功 / 必要な承認あり / `mergeable == MERGEABLE && mergeStateStatus == CLEAN`
- **禁止 / 要確認**: master への直接 push / 破壊的操作 / 秘匿情報コミット / 4 条件未充足の独断マージ

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

旧 plugin の hook 構造 (`SessionStart` / `UserPromptSubmit` + `PostToolBatch`) と機能はそのまま維持しています。 マーカー dir のみ `auto-followthrough-markers/` → `agent-discipline-markers/` に変更されているため、 移行直後は旧 marker が孤児として残りますが、 OS の tmpfs/tmp cleanup で自然に消去されます。

旧 2 plugin は本 plugin 導入時に同 PR で削除済みです。

## 設計上の選択

### なぜ統合 plugin か (vs 個別 plugin の維持)

このリポジトリは個人の Claude Code 開発スタイル marketplace です。 機能ごとに細かく plugin を分けると plugin 数が肥大化し、 enable list の見通しが悪くなります。 「物理層 + 思考層」 は抽象レイヤとしては別ですが、 個人運用では一括 on/off で問題が出ないため統合しました。

公開 marketplace でユーザに細かい on/off を提供する場合は分離が望ましいですが、 本リポジトリは個人運用前提のため統合粒度を採用しています。

### なぜ常時系と auto 系で hook event を分けるか

- **常時系 (inject-always.sh)**: 物理層 (Bash 分解) と before 系 (設計壁打ち / issue 規約) は permission_mode に依らず常に有用なので `SessionStart` で 1 回注入する。 トークンコストを抑えるため per-turn 再注入はしない
- **auto 系 (inject-auto.sh)**: during/after は auto mode 時のみ意味があり、 long-running session で薄れると致命的 (= 自走パイプラインが止まる) なので `UserPromptSubmit` + `PostToolBatch` で per-turn 再注入する

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
- **`PostToolBatch` の入力 schema 依存**: `PostToolBatch` 入力に `permission_mode` が含まれない実装の場合、 こちらは無音になる。 `UserPromptSubmit` 経路は引き続き機能する
- **check-uncommitted の発火タイミング制約**: 最初のプロンプト時点で worktree が clean だと、 同 session 中に後から発生した未コミット変更は検知しない (上記参照)

## 関連情報

- [Claude Code Hooks ドキュメント](https://code.claude.com/docs/en/hooks)
