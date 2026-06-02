# update-default-branch プラグイン

PR がマージされた旨の報告をユーザーから受けた際に、デフォルトブランチを最新化し、リモートで削除されたブランチに紐づくローカルブランチを片付けるためのプラグインです。

## バージョン

v0.1.0

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

**実行手順**:

1. `git status --short` で作業ツリーの clean を確認
2. 現在のブランチ名を `.git/.update-default-branch-state` に保存 (Bash 呼び出しが分かれても引き継げるよう)
3. `git symbolic-ref refs/remotes/origin/HEAD` でデフォルトブランチを取得 (失敗時は `git remote set-head origin --auto` で再設定)
4. `git switch <default>` → `git pull --ff-only origin <default>` でデフォルトブランチを最新化 (fast-forward のみ許容)
5. `git fetch --prune origin` でリモートが消えた ref を整理
6. `git for-each-ref --format='%(refname:short) %(upstream:track)' refs/heads` の出力から `[gone]` を検出
7. `git branch -D <branch>` で削除 (リモートが既に消えている branch なので確認ステップなし)
8. state file から元のブランチ名を読み戻し、残っていれば `git switch` で復帰、削除済み or detached の場合はユーザーに新ブランチ名を確認。最後に state file を削除

## 設計上の注意

- **`[gone]` 削除に確認ステップなし**: 追跡先が消えている branch はリモート側で既に削除済み (PR マージ後の自動削除等) で、ローカル削除は安全な後始末でしかないため、確認ステップは挟みません。
- **`[gone]` ≠ "merged"**: ただし `[gone]` には PR マージ以外の経路 (リモートでの force-delete / リネーム等) も含まれます。`git branch -D` は merge 検査を skip するため、ローカルにのみ存在するコミットを抱えた `[gone]` branch は誤削除されえます。削除前の SHA は `git branch -D` の出力に表示されるので、誤削除に気づいたら `git checkout -b <name> <sha>` で復活できます (約 30 日は `git reflog` でも遡れます)。「未マージなのに `[gone]` になっている」branch を温存したい場合、本 Skill 実行前に別 branch へ退避するか、Skill 自体を実行しないでください。
- **デフォルトブランチに居着かない**: ユーザーの CLAUDE.md でデフォルトブランチでの作業が禁止されている場合に備え、開始時に元のブランチを `ORIGINAL_BRANCH` として保存し、終了時に状況に応じて復帰させる手順になっています。
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
- `awk`
- `origin` リモートが設定されているリポジトリ

## 関連情報

- [Claude Code Skills ドキュメント](https://code.claude.com/docs/en/skills)
- [git-branch(1) — `--delete` / `-D`](https://git-scm.com/docs/git-branch)
- [git-fetch(1) — `--prune`](https://git-scm.com/docs/git-fetch)
