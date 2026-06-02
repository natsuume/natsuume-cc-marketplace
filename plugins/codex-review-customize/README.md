# codex-review-customize プラグイン

公式 codex プラグインの `/codex:review` コマンド定義をローカルでパッチし、Skill tool から呼び出し可能にする setup プラグインです。

## バージョン

v0.3.0

## 概要

公式 `/codex:review` は frontmatter に `disable-model-invocation: true` 指定があるため、Claude が Skill tool から呼び出せません。

本プラグインの `/codex-review-customize:setup` を実行すると、`commands/review.md` が atomic にパッチされ `disable-model-invocation: true` 行が削除されます。並行して codex の cache を削除し、次回 `/reload-plugins` で patched 版が読み込まれるようにします。

### v0.2.1 → v0.3.0 の変更点

- **パッチ対象から `commands/adversarial-review.md` を削除**: 関連プラグイン (`post-pr-review` / `pre-push-review` / `auto-followthrough`) で `/codex:adversarial-review` 連携を全廃したため、本プラグインのパッチ対象も `commands/review.md` 1 ファイルに絞った。 サイクル時間が長くなる adversarial-review はサイクルに組み込まない方針

### v0.2.0 → v0.2.1 の変更点

- `apply-patch.sh` の bash 4.0 専用 built-in (`mapfile`) を `while read` ループに置換し、macOS の default `/bin/bash` (3.2.57) でも動作するようにしました。

### v0.1.0 → v0.2.0 の変更点

- パッチ対象に **`commands/adversarial-review.md`** (`/codex:adversarial-review`) を追加しました。`pre-push-review` のループ閾値到達時の誘導や `post-pr-review` の PR 作成後レビューが Skill tool 経由で起動できるよう、共通の setup 動線に集約していました (v0.3.0 で削除)。

## なぜ「別プラグインで `/codex:review` を再定義」しないのか

スラッシュコマンドは `<plugin名>:<command名>` でプラグイン名空間が確定するため、別プラグインからは `/codex:review` という同名は提供できません。本プラグインはコマンド名を公式の `/codex:...` のまま保持したいユーザー向けに、公式定義のローカルパッチを setup する形を採っています (詳細は本リポジトリの設計議論履歴参照)。

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install codex-review-customize@natsuume-plugins
```

インストール後、Claude Code 内で実行:

```
/codex-review-customize:setup
```

その後 `/reload-plugins` を実行して codex プラグインを再 build。

## 機能一覧

### Commands

| コマンド | 説明 |
|---------|------|
| `/codex-review-customize:setup` | `apply-patch.sh` を実行し、codex の `commands/review.md` をパッチする |

### スクリプト

| ファイル | 用途 |
|---------|------|
| `scripts/apply-patch.sh` | 公式 codex の `commands/review.md` をパッチし、対応する cache を削除する。idempotent (再適用は no-op)。frontmatter 健全性チェック + atomic な書き換え (同一 FS 上の `mktemp` + `mv`)。パッチ失敗時はその時点で中断する |

## 設計上の特徴

- **backup なし**: marketplace clone は git 管理下なので、`git checkout commands/review.md` で常に復元可能。冗長な backup を残さず最小構成に保つ
- **idempotent**: 末尾マーカー (`<!-- codex-review-customize: patched -->`) で適用済み判定。複数回実行しても安全
- **アップストリーム update 検知**: codex プラグインが update されるとパッチが消える (上書きされる) ため、再適用には再度 `/codex-review-customize:setup` を実行

## 制約

- 公式 codex の `commands/review.md` の構造 (frontmatter `disable-model-invocation: true` の存在、 trailing newline 等) に依存。公式が大幅に再構成すると `sed` パターンが効かなくなり、frontmatter 健全性チェックで abort する (= 安全に失敗)
- **再現性**: 環境ごとに `/codex-review-customize:setup` を一度ずつ実行する必要あり (`natsuume-statusline:setup` と同じパターン)
- **旧バージョン (日本語化指示を含むパッチ) からの移行**: 旧版が適用済みの環境では同名マーカーで no-op 判定されるため、不要な `## 日本語出力指示` セクションが残ります。先に `git checkout commands/review.md` で原本へ戻してから `/codex-review-customize:setup` を再実行してください
- **`commands/adversarial-review.md` のパッチ残骸**: v0.2.x で adversarial-review もパッチしていた環境では、 v0.3.0 にアップデートしても adversarial-review.md のパッチは自動で剥がれません。 不要なら codex プラグインの marketplace clone で `git checkout commands/adversarial-review.md` を実行するか codex を再インストールしてください

## 必要な実行環境

- `bash`
- `sed`
- `find`
- 公式 codex プラグインがインストール済み

## 関連プラグイン

- [pre-push-review](../pre-push-review/) — `/codex:review --scope branch` を強制呼び出しする運用。本プラグインを併用すると Skill 経由でレビューを取得できる
