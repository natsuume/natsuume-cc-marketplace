# auto-followthrough プラグイン

Claude Code が **auto mode** で動作している間、変更の `commit` → `gh pr create` → PR マージ完了までを停止せずに遂行 (follow through) するよう、Claude にコンテキストを注入するプラグインです。

## バージョン

v0.2.1

## 概要

`UserPromptSubmit` と `PostToolBatch` の 2 つのフックで、入力 JSON の `permission_mode` が `"auto"` のときだけ `hookSpecificOutput.additionalContext` を注入します。それ以外のモード (`default` / `plan` / `acceptEdits` / `bypassPermissions`) では何も出力しません。

注入する内容は「変更が一段落したらユーザに確認を求めず、commit → push → PR 作成 → (前提条件を満たした上で) マージまで進めて良い」という方針です。auto mode のときに Claude が「変更を書いて停止」してしまうのを防ぎ、PR マージ完了まで届ける運用にロックします。

### マージ前提条件 (hard gate)

注入文では **マージのみ** に hard gate を設けています。以下の **すべて** が満たされている場合に限り、PR を独断マージしてよい旨を明記します。1 つでも未充足ならユーザに報告して手を止めます。

- PR が draft ではない (ready for review)
- リポジトリで required に設定された CI checks が全て成功 (`gh pr checks` で検証)
- レビューが要求されている場合、必要な承認が揃っている (`gh pr view --json reviewDecision` で検証)
- post-pr-review が促す `/codex:adversarial-review --wait --scope branch` の verdict が `approve` (もしくは `needs-attention` の場合は severity を問わず**すべての** finding が解消済み / ユーザが明示的に waive 済み)
- ブランチ保護ルールに違反しない (`mergeable` が `MERGEABLE` かつ `mergeStateStatus` が `CLEAN`)

各 bullet には `gh` CLI で検証する具体的なコマンドを併記してあります。Claude は注入文を見て gate 評価を実行できます (例: `gh pr checks <pr>` で失敗があれば停止)。

### その他の禁止事項

auto mode でも以下は引き続き禁止 / 要確認である旨を明記します:

- master / 既定ブランチへの直接 push、master 上での直接コミット
- force push / 履歴改変 / 共有データ削除等の破壊的操作
- 秘匿情報を含むファイルのコミット
- マージ前提条件を満たさない PR の独断マージ

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=auto-followthrough
```

## 機能一覧

### Hooks

#### inject-auto-context

**ファイル**: `hooks/scripts/inject-auto-context.sh`
**イベント**: `UserPromptSubmit`, `PostToolBatch`

**動作**:

- 入力 JSON から `permission_mode` を読み取り、`"auto"` のときのみ `additionalContext` を出力する
- それ以外のモードでは無音で `exit 0`
- `hook_event_name` を入力からそのまま読み取り、`hookSpecificOutput.hookEventName` に同じ値を設定する (UserPromptSubmit / PostToolBatch どちらの呼び出しでも同一スクリプトで処理可能)
- `jq` が無い環境では何もせず終了する (フェイルセーフ)

**なぜ 2 つのフックが必要か**:

- `UserPromptSubmit` — 新しいユーザ入力ごとにモードを再確認して方針を再注入する。ユーザが auto を on/off したタイミングを取り逃がさない。
- `PostToolBatch` — 1 ターンのツール呼び出しが**全て完了した直後**に発火する。ここでもう一度方針を注入することで、編集だけして「完了」と打ち切るのを抑止し、commit / PR / マージへの遷移を促す。

`PostToolUse` ではなく `PostToolBatch` を採用しているのは、ツール 1 件ごとに毎回介入するとノイズになるためです。`PostToolBatch` はバッチ末尾で 1 回だけ発火するので、自然な「区切り」のフックになります。

**v0.2.1 追加: PostToolBatch の once-per-turn 制御**

`PostToolBatch` は同一ユーザターン内に複数回発火する (Claude が複数の model invocation を経由する場合) ため、放置すると同じ static な context が transcript に重複して積まれ、context 肥大化と古い文脈 (例: dirty-worktree 警告) の埋没を招きます。

これを回避するため `${TMPDIR:-/tmp}/auto-followthrough-markers/<session_id>.batch-injected` を per-turn dedup マーカーとして使い、以下の挙動を実装しています:

- `UserPromptSubmit` 発火時: マーカーを削除 (新ターンの signal)
- `PostToolBatch` 発火時: マーカーが既に存在すれば skip、なければ context を出力してマーカーを set

これにより 1 ユーザターンあたり `PostToolBatch` 経路の注入は最大 1 回に制限されます (`UserPromptSubmit` 経路は従来どおり毎ターン発火します)。

#### check-uncommitted-on-session-start (v0.2.0 追加)

**ファイル**: `hooks/scripts/check-uncommitted-on-session-start.sh`
**イベント**: `UserPromptSubmit`

**動作**:

- auto モードのセッションで cwd に未コミット変更がある場合、**Claude にその出所分析と分類確認を要求** する `additionalContext` を注入する
- session ごとに 1 回だけ発火するよう `${TMPDIR:-/tmp}/auto-followthrough-markers/<session_id>.checked` でマーカー管理
- auto モード以外、git リポジトリ外、`jq` 不在環境ではすべて無音 `exit 0`

**なぜ独立した hook が必要か**:

`inject-auto-context` の注入文に caveat を積み続けると追加情報のたびに文章量が肥大化し、Claude が指示を取りこぼしやすくなります。本フックは「未コミット変更が dirty な状態」のチェックを **静的な注入文ではなく実コマンド (`git status --porcelain`)** で行い、検出時のみ動的に警告を出すため、auto モード本体の注入文を簡潔に保てます。

**Claude への指示内容 (要約)**:

ユーザに確認を丸投げするのではなく、以下を Claude が一次分析するよう要求します:

1. `git diff` / `git log` / ファイル内容を確認して各変更の出所を推定
2. 各ファイルを 4 分類 (今回タスク関連 / 以前の残骸 / 中間状態 / 不明) に振り分け
3. 推奨アクションをまとめて簡潔にユーザに報告し、同意を取る
4. ユーザの同意を得てから実際の git 操作 (add / commit / stash / branch 切り出し等) を行う

これにより auto mode の本来の趣旨「Claude に最大限委任する」を維持しつつ、意図しない変更を巻き込むリスクを抑えます。

## ディレクトリ構成

```
auto-followthrough/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       ├── inject-auto-context.sh
│       └── check-uncommitted-on-session-start.sh
└── README.md
```

## 必要な実行環境

- `bash`
- `jq`

## 関連プラグイン

- [git-guardrails](../git-guardrails/) — master への直接 push を禁止する。auto mode 中の暴走を構造的に止める安全網として併用推奨
- [pre-push-review](../pre-push-review/) — push 前にレビューループを強制。auto で push に進むときも本プラグインの動作と矛盾せず、レビュー手順は引き続き機能する
- [post-pr-review](../post-pr-review/) — PR 作成直後に adversarial review を促す。auto mode 中も本プラグインの誘導と直交して動作する
- [update-default-branch](../update-default-branch/) — マージ完了後のデフォルトブランチ最新化を支援する Skill

## 既知の制約

- **強制ではなく誘導**: `additionalContext` を次のターンの先頭に追加するだけなので、Claude が指示を無視することは原理的に可能です。確実に止めたいケースは別途 deny 判定の hook を組む必要があります。
- **`permission_mode` の値が `"auto"` リテラルであること前提**: Claude Code 側の仕様変更で値が変わると無音になります。その場合は無効化されるだけで誤動作はしません。
- **PostToolBatch の入力 schema 依存**: `PostToolBatch` 入力に `permission_mode` が含まれない実装の場合、こちらは無音になります。`UserPromptSubmit` 経路は引き続き機能します。

## 関連情報

- [Claude Code Hooks ドキュメント](https://docs.anthropic.com/claude-code/hooks)
