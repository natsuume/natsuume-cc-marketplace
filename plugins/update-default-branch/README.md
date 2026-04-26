# update-default-branch プラグイン

PR がマージされた旨の報告をユーザーから受けた際に、デフォルトブランチを最新化し、リモートで削除されたブランチに紐づくローカルブランチを片付けるためのプラグインです。

## バージョン

v0.1.0

## 概要

このプラグインは Skill のみで構成されています。Claude が「PR をマージした」旨の発話を検知すると Skill 内の手順に従って以下を実行します:

1. 作業ツリーが clean かを確認
2. リモートのデフォルトブランチ名を動的に取得
3. デフォルトブランチへ切り替えて `git pull origin <default>`
4. `git fetch --prune origin` でリモートから削除されたブランチに対応する remote-tracking ref を整理
5. `[gone]` 状態のローカルブランチを抽出
6. ユーザーに削除候補を提示し、了承を得てから `git branch -D` で削除

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=update-default-branch
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
2. 現在のブランチ名を `.git/.update-default-branch-state` に保存 (Bash 呼び出しをまたいでも参照できるように)
3. `git symbolic-ref refs/remotes/origin/HEAD` でデフォルトブランチを取得 (失敗時は `git remote set-head origin --auto` で再設定)
4. `git switch <default>` → `git pull --ff-only origin <default>` でデフォルトブランチを最新化 (fast-forward のみ許容)
5. `git fetch --prune origin` でリモートが消えた ref を整理
6. `git for-each-ref --format='%(refname:short) %(upstream:track)' refs/heads` の出力から `[gone]` を検出
7. ユーザーに候補を提示して了承を得る (勝手に削除しない)
8. `git branch -D <branch>` で削除
9. ステートファイルから元のブランチ名を読み戻し、残っていれば `git switch` で復帰、削除済み or detached の場合はユーザーに新ブランチ名を確認。最後にステートファイルを削除

## 設計上の注意

- **削除前に必ず確認**: `[gone]` のリストには、リネームしただけのブランチや別環境で削除されたものが含まれる可能性があります。Skill は必ずユーザーに候補を提示してから削除する手順になっています。
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

- [Claude Code Skills ドキュメント](https://docs.anthropic.com/claude-code/skills)
- [git-branch(1) — `--delete` / `-D`](https://git-scm.com/docs/git-branch)
- [git-fetch(1) — `--prune`](https://git-scm.com/docs/git-fetch)
