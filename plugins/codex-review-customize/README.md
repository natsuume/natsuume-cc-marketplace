# codex-review-customize プラグイン

公式 codex プラグインの `/codex:review` コマンド定義をローカルでパッチし、Skill tool から呼び出し可能にする setup プラグインです。

## バージョン

v0.3.2

## 概要

公式 `/codex:review` は frontmatter に `disable-model-invocation: true` 指定があるため、Claude が Skill tool から呼び出せません。

本プラグインの `/codex-review-customize:setup` を実行すると、`commands/review.md` が atomic にパッチされ `disable-model-invocation: true` 行が削除されます。並行して codex の cache を削除し、次回 `/reload-plugins` で patched 版が読み込まれるようにします。

> **v1.1.0+ の pre-push-review と本プラグインの関係**: pre-push-review は v1.1.0 で codex review を bash wrapper (`run-codex-review.sh`) 経由 foreground 実行に切替え、 v2.0.0 で `/pre-push-review:review` slash command による 3 ツール並列起動の確定的フローに移行しました。 そのため pre-push-review の codex review 経路は本プラグインのパッチ (= `/codex:review` の `disable-model-invocation` 削除) に **依存しません**。 本プラグインは「主 session から ad-hoc に `/codex:review` を Skill tool で呼びたい」 「他用途で Skill 経由 codex review を使いたい」 ユースケース向けの補助という位置づけになります。

### v0.3.1 → v0.3.2 の変更点 (cross-plugin sync)

- **README の version 表記を plugin.json / marketplace.json と再同期**: v0.3.0 → v0.3.1 commit (#35f84d8) で plugin.json / marketplace.json は 0.3.1 に bump したが README 内 version 見出しの更新が漏れていた drift を解消し、 同 commit の v0.3.0 → v0.3.1 changelog エントリを backfill
- **関連プラグイン (pre-push-review) の説明を v2.0.0 仕様に同期**: 本プラグインのパッチに依存しない経路 (wrapper + slash command) への移行を反映
- **v0.2.0 changelog 内の `post-pr-review` 言及を historical 注記化**: 当該プラグインは現在マーケットプレイスに存在しない

### v0.3.0 → v0.3.1 の変更点

- **`apply-patch.sh` の EXIT trap が関数 local の temp を参照していた bug を修正**: エラー経路で未バインド変数の noise / temp 残留を解消。 idempotent と atomic な性質は不変

### v0.2.1 → v0.3.0 の変更点

- **パッチ対象から `commands/adversarial-review.md` を削除**: 関連プラグイン (`pre-push-review` / `auto-followthrough` ほか、 当時存在した `post-pr-review` を含む) で `/codex:adversarial-review` 連携を全廃したため、本プラグインのパッチ対象も `commands/review.md` 1 ファイルに絞った。 サイクル時間が長くなる adversarial-review はサイクルに組み込まない方針

### v0.2.0 → v0.2.1 の変更点

- `apply-patch.sh` の bash 4.0 専用 built-in (`mapfile`) を `while read` ループに置換し、macOS の default `/bin/bash` (3.2.57) でも動作するようにしました。

### v0.1.0 → v0.2.0 の変更点

- パッチ対象に **`commands/adversarial-review.md`** (`/codex:adversarial-review`) を追加しました。`pre-push-review` の旧版で adversarial-review を Skill 経由で起動できるよう共通 setup 動線に集約していました (`post-pr-review` プラグインは当時存在しましたが現在は削除済み / `/codex:adversarial-review` 連携自体も v0.3.0 で全廃)。

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

- [pre-push-review](../pre-push-review/) — v2.0.0 以降は `/pre-push-review:review` slash command で 3 レビュー (`/code-review` + codex review wrapper + `pre-push-review:security-reviewer` subagent) を並列起動。 codex 経路は bash wrapper (`run-codex-review.sh`) 経由 foreground 起動を hardcode しており、 本プラグインのパッチには **依存しません**。 主 session から ad-hoc に `/codex:review` を Skill 経由で呼びたい場合のみ本プラグインが有用 (v1.0.0 以前は本プラグインのパッチが pre-push-review の動作前提でしたが、 v1.1.0 で wrapper 化されてから依存関係は解消されました)
