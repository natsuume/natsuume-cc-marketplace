# update-default-branch プラグイン

PR がマージされた旨の報告をユーザーから受けた際に、デフォルトブランチを最新化し、リモートで削除されたブランチに紐づくローカルブランチを片付けるためのプラグインです。

## バージョン

v0.4.1

## 概要

このプラグインは Skill のみで構成されています。Claude が「PR をマージした」旨の発話を検知すると Skill 内の手順に従って以下を実行します:

1. 作業ツリーが clean かを確認
2. remote-tracking refs と `origin/HEAD` を更新してデフォルトブランチ名を動的に取得
3. デフォルトブランチへ切り替えて `git pull --ff-only origin <default>`
4. `[gone]` 状態のローカルブランチを抽出
5. `git branch -D` で確認なしに削除 (リモートが既に削除済みの branch なので安全。誤削除に気づいた場合は `git reflog` で復旧可能)

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install update-default-branch@natsuume-plugins
```

本プラグインは Claude Code 専用で、Codex marketplace では配布していません。

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
3. `git fetch --prune origin` → `git remote set-head origin --auto` → `git symbolic-ref refs/remotes/origin/HEAD` の順で remote の現在値からデフォルトブランチを取得 (いずれかが失敗した場合は stale 値へ fallback せず中断)
4. `git switch <default>` でデフォルトブランチへ切り替え
5. `git pull --ff-only origin <default>` で最新化 (fast-forward のみ許容。失敗時は元のブランチへ復帰して中断)
6. `git for-each-ref --format='%(refname:short) %(upstream:track)' refs/heads` の出力から Claude が `[gone]` を抽出
7. `git branch -D <branch1> <branch2> ...` で一括削除 (リモートが既に消えている branch なので確認ステップなし)
8. 手順 2 で記憶した元のブランチへ `git switch` で復帰。削除済み or detached の場合はユーザーに新ブランチ名を確認

## 設計上の注意

- **他プラグインの hook と共存する「素朴な単一コマンド」設計**: 各手順のコマンド文字列にはコマンド置換 `$(...)` / 連結 (`&&` 等) / シェル変数 / `echo` メッセージを含めません。これらを含む合成スクリプトは、auto-lint-check の block-commit-lint hook (コマンド中に `git` + `commit` の語と `$(...)` が共存すると fail-closed で deny する) 等にブロックされ実行不能になるためです。手順間の状態は Claude が会話コンテキストで保持し、後続コマンドへリテラル値として埋め込みます。
- **`origin/HEAD` は必ず remote から再検出**: local の symbolic ref は remote の default branch rename に自動追随しません。remote-tracking refs の fetch/prune と `git remote set-head origin --auto` が成功してからだけ `symbolic-ref` を読み、失敗時は stale 値で継続しません。
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
