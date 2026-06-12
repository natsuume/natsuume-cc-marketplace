# update-default-branch プラグイン

PR がマージされた旨の報告をユーザーから受けた際に、デフォルトブランチを最新化し、リモートで削除されたブランチに紐づくローカルブランチを片付けるためのプラグインです。

## バージョン

v0.2.0

### v0.1.2 → v0.2.0 の変更点

- **実行モデルを「1 手順 = 1 つの素朴な git コマンド」に再設計**: 旧版の「一連のスクリプト例」(複数手順を `$(...)` / `if` / シェル変数で合成した単一 Bash スクリプト) は、同居する他プラグインの PreToolUse hook (auto-lint-check の block-commit-lint 等) に fail-closed で deny され実行不能だった。コマンド置換やメッセージ文字列中の `commit` という単語が、hook の「静的解析できない構文は安全側で deny する」検査に構造的に引っかかるため。新版は各手順を単独の git コマンドとして実行し、手順間の状態 (元ブランチ名・デフォルトブランチ名・削除対象) は Claude が会話コンテキストで保持してリテラル値を埋め込む方式に変更 (decompose-bash プラグインの分解方針とも整合)。
- **state file (`.git/.update-default-branch-state`) を廃止**: シェル変数が Bash 呼び出し間で消える問題への対処として導入していたが、状態を会話コンテキストで保持する新方式では不要になった。
- `awk` / `sed` への依存を撤廃 (出力の抽出・整形は Claude が直接行う)。
- ブランチ名のリテラル埋め込みは **single quote 必須** とし、シェルメタ文字を含む合法なブランチ名 (`feature;id` 等) での誤実行を防止 (codex review P2 対応)。`'` を含むブランチ名は実行中止 + ユーザー確認。

### v0.1.1 → v0.1.2 の変更点

- **ルート README の version 表記を本プラグインの最新と同期** (#47b00d6 / #0360f07 / #d0e5225 系の一括フォローアップ): ルート `README.md` の plugin 一覧の version が `0.1.2` に揃った。 本 README 直下の version 見出しが `v0.1.0` のまま残っていた drift を解消。
- SKILL.md の frontmatter 修正と内部リンク整備 (動作影響なし、 documentation only)。

### v0.1.0 → v0.1.1 の変更点

- **SKILL.md のステップを独立化して堅牢化**: worktree 競合 / detached HEAD / `symbolic-ref` 失敗の各エッジケースをハンドリングし、 失敗時のトラブルシュート手順を専用節に整理。 手順 4 (`git fetch --prune`) と手順 5 (`[gone]` 抽出) を `if` 連結から独立 step に分離して中断時の復旧経路を明確化。

## 概要

このプラグインは Skill のみで構成されています。Claude が「PR をマージした」旨の発話を検知すると Skill 内の手順に従って以下を実行します:

1. 作業ツリーが clean かを確認
2. リモートのデフォルトブランチ名を動的に取得
3. デフォルトブランチへ切り替えて `git pull --ff-only origin <default>`
4. `git fetch --prune origin` でリモートから削除されたブランチに対応する remote-tracking ref を整理
5. `[gone]` 状態のローカルブランチを抽出
6. `git branch -D` で確認なしに削除 (リモートが既に削除済みの branch なので安全。誤削除に気づいた場合は `git reflog` で復旧可能)

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install update-default-branch@natsuume-plugins
```

## 機能一覧

### Skills

#### update-default-branch

**ファイル**: `skills/update-default-branch/SKILL.md`

**トリガーとなる発話例**:

- 「PR がマージされた」「マージしました」「merge しました」
- 「PR をマージしたよ」「マージ完了」
- 「PR が取り込まれた」「リモートに反映された」
- 「デフォルトブランチを最新にしたい」
- 「不要なブランチを削除したい」「マージ済みブランチを片付けたい」

**実行手順** (各手順は単一の git コマンドを 1 回の Bash 呼び出しで実行):

1. `git status --short` で作業ツリーの clean を確認
2. `git branch --show-current` で現在のブランチ名を取得し、Claude が会話コンテキストで記憶
3. `git symbolic-ref refs/remotes/origin/HEAD` でデフォルトブランチを取得 (失敗時は `git remote set-head origin --auto` で再設定)
4. `git switch <default>` でデフォルトブランチへ切り替え
5. `git pull --ff-only origin <default>` で最新化 (fast-forward のみ許容。失敗時は元のブランチへ復帰して中断)
6. `git fetch --prune origin` でリモートが消えた ref を整理
7. `git for-each-ref --format='%(refname:short) %(upstream:track)' refs/heads` の出力から Claude が `[gone]` を抽出
8. `git branch -D <branch1> <branch2> ...` で一括削除 (リモートが既に消えている branch なので確認ステップなし)
9. 手順 2 で記憶した元のブランチへ `git switch` で復帰。削除済み or detached の場合はユーザーに新ブランチ名を確認

## 設計上の注意

- **他プラグインの hook と共存する「素朴な単一コマンド」設計**: 各手順のコマンド文字列にはコマンド置換 `$(...)` / 連結 (`&&` 等) / シェル変数 / `echo` メッセージを含めません。これらを含む合成スクリプトは、auto-lint-check の block-commit-lint hook (コマンド中に `git` + `commit` の語と `$(...)` が共存すると fail-closed で deny する) 等にブロックされ実行不能になるためです。手順間の状態は Claude が会話コンテキストで保持し、後続コマンドへリテラル値として埋め込みます。
- **埋め込むブランチ名は必ず single quote で囲む**: git のブランチ名には `$` / `;` / `&` 等のシェルメタ文字が合法に含まれうるため、クォートなし埋め込みは別コマンド実行や変数展開の事故経路になります。single quote で完全リテラル化し、ブランチ名自体に `'` が含まれる場合は実行を中止してユーザーに確認します。
- **`[gone]` 削除に確認ステップなし**: 追跡先が消えている branch はリモート側で既に削除済み (PR マージ後の自動削除等) で、ローカル削除は安全な後始末でしかないため、確認ステップは挟みません。
- **`[gone]` ≠ "merged"**: ただし `[gone]` には PR マージ以外の経路 (リモートでの force-delete / リネーム等) も含まれます。`git branch -D` は merge 検査を skip するため、ローカルにのみ存在するコミットを抱えた `[gone]` branch は誤削除されえます。削除前の SHA は `git branch -D` の出力に表示されるので、誤削除に気づいたら `git checkout -b <name> <sha>` で復活できます (約 30 日は `git reflog` でも遡れます)。「未マージなのに `[gone]` になっている」branch を温存したい場合、本 Skill 実行前に別 branch へ退避するか、Skill 自体を実行しないでください。
- **デフォルトブランチに居着かない**: ユーザーの CLAUDE.md でデフォルトブランチでの作業が禁止されている場合に備え、開始時に元のブランチ名を Claude が記憶し、終了時に状況に応じて復帰させる手順になっています。
- **未コミット変更がある場合は中断**: `git status --short` の出力が空でない場合、stash / commit のいずれかをユーザーに依頼してから再実行する設計です。

## ディレクトリ構成

```
update-default-branch/
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   └── update-default-branch/
│       └── SKILL.md
└── README.md
```

## 必要な実行環境

- `bash`
- `git`
- `origin` リモートが設定されているリポジトリ

## 関連情報

- [Claude Code Skills ドキュメント](https://code.claude.com/docs/en/skills)
- [git-branch(1) — `--delete` / `-D`](https://git-scm.com/docs/git-branch)
- [git-fetch(1) — `--prune`](https://git-scm.com/docs/git-fetch)
