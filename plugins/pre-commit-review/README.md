# pre-commit-review プラグイン

`git commit` を実行する前に `/codex:review` と `/simplify` を必ず実行させ、未レビューのコミットをブロックするプラグインです。PR を対象とする `/code-review:code-review` は姉妹プラグイン [post-pr-review](../post-pr-review/) が担当します。

## バージョン

v0.1.0

## 概要

`PreToolUse` フックで `Bash` ツール実行を監視し、`git commit` コマンドを検出した場合、レビュー済みマーカーが存在しない限り `deny` を返してコミットを阻止します。マーカーは `mark-reviewed.sh` を経由して明示的に作成する必要があり、その時点の `git diff --cached` の SHA256 ハッシュと結びついています。

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=pre-commit-review
```

## 機能一覧

### Hooks

#### 1. block-pre-commit

**ファイル**: `hooks/scripts/block-pre-commit.sh`
**イベント**: PreToolUse (matcher: `Bash`)

`git commit` を含むコマンドを検出した際、現在のステージング差分のハッシュとマーカー内のハッシュを比較し、一致しなければ `deny` を返します。

**動作**:

- 単独実行 (`git commit -m "msg"`) と複合コマンド (`cd dir && git commit`) の双方を検出
- `git -C dir commit` や `git --git-dir=... commit` のように global option を伴う形式も検出
- `git commit-tree` 等の別コマンドは除外
- `git commit --help` / `-h` はスキップ
- マーカーが一致した場合は使い切りで削除 (再コミット時は再レビューが必要)
- ハッシュは `git diff --cached` (staged) と `git diff` (unstaged tracked) の連結に対して計算するため、`git commit -a` や `git commit <pathspec>` で未レビュー変更が紛れ込むケースもブロックされる

**追加の制約 (1 マーカー = 1 commit を保証)**:

- コマンド先頭が `git ... commit` でないものは一律 deny。これにより `cd dir && git commit`, `git add . && git commit`, `git status && git commit` などのチェーン形式は別々の Bash 呼び出しに分割する必要があります
- `git -C`, `--git-dir`, `--work-tree` で対象リポジトリを切り替える形式の commit は deny (検証先と commit 先が食い違うのを防ぐため)
- `git commit` の後にシェル区切り文字 (`;`, `&`, `&&`, `||`, `|`) を続けるコマンドは deny (マーカー消費後に未レビュー commit が走るのを防ぐため)
- 引用符で囲まれた `git commit` 文字列 (`grep "git commit" README` など) はテキスト参照とみなしフックは介入しません
- `$(...)` やバッククォートによるコマンド置換を含む commit コマンドは deny (置換が commit より前に評価され、index を書き換える経路となり得るため)

**シェルラッパー経由の commit**:

- 先頭が `bash`/`sh`/`zsh`/`dash`/`ksh`/`eval` のコマンド (例: `bash -c "git commit ..."`) はクォート内に commit が隠れていてもラッパーとして検出され deny されます。Claude には `git commit` を直接実行してもらう前提です。

`deny` 時の `permissionDecisionReason` には、Claude が次に行うべき手順 (`/codex:review` と `/simplify` の実行 → 修正 → `mark-reviewed.sh` 実行) が記載されます。

### スクリプト

#### mark-reviewed.sh

**ファイル**: `hooks/scripts/mark-reviewed.sh`

レビューが完了した後、コミット直前に手動で実行するスクリプトです。`git diff --cached` の SHA256 を計算し、`<git-dir>/.claude-pre-commit-reviewed` に保存します。

```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/mark-reviewed.sh"
```

このマーカーはステージング差分のハッシュと一致した 1 回のコミットでのみ有効です。コミット後はフックが自動的にマーカーを削除します。

## ワークフロー

```
1. ユーザー: 「コミットして」
2. Claude が `git commit` を試行
3. block-pre-commit.sh が deny を返し、レビュー実行を指示
4. Claude が以下を順に実行:
   - /codex:review
   - /simplify
5. 指摘箇所を修正し、`git add` で再ステージング
6. `bash "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/mark-reviewed.sh"` を実行
7. `git commit` を再試行 → マーカー一致で通過、マーカー削除
8. (PR 作成後) post-pr-review プラグインが `/code-review:code-review` を促す
```

## 注意事項

- マーカー作成後にトラッキング済みファイルを編集すると、ハッシュが一致しなくなり再レビューが必要になります。
- 未トラッキングのファイルはハッシュ計算に含まれません。新規ファイルをコミット対象にする場合は、レビュー前に `git add` でステージングしてから `mark-reviewed.sh` を実行してください。
- マーカーは `.git` ディレクトリ配下に保存されるため、リモートには影響しません。
- フックを一時的に無効化したい場合は、Claude Code 側でプラグインを無効にするか、フックの設定を一時的に変更してください。

## ディレクトリ構成

```
pre-commit-review/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       ├── block-pre-commit.sh
│       └── mark-reviewed.sh
└── README.md
```

## 必要な実行環境

- `bash`
- `jq`
- `git`
- `sha256sum` (coreutils)

## 関連情報

- [Claude Code Hooks ドキュメント](https://docs.anthropic.com/claude-code/hooks)
