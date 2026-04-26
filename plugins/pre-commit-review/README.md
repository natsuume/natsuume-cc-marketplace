# pre-commit-review プラグイン

`git commit` を実行する前に `/simplify` → `/codex:review` を必ず実行させ、未レビューのコミットをブロックするプラグインです。`/simplify` はコード変更を伴うため先に走らせ、`/codex:review` はその後の最終形をレビューします。修正によりステージング内容が変わった場合は `/simplify` → `/codex:review` を最初から再実行 (両方再走) し、Claude が「修正不要」と判断した時点で commit に進みます。Claude が「人間判断を仰ぐべき」と判断した場合のみユーザーへエスカレートします。PR を対象とする `/code-review:code-review` は姉妹プラグイン [post-pr-review](../post-pr-review/) が担当します。

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

**既知の制約 (受容)**:

- `git -c foo=a\ commit -C ../other commit` のように **オプション値に backslash-escape された空白 + `commit` 文字列が含まれる** ケースでは、COMMIT_DETECT_REGEX が commit を検出できず hook が早期スキップする可能性があります。Claude が意図的にバイパスを試みる adversarial シナリオであり、cooperative な利用では発生しないため受容しています。完全な防御が必要な場合はシェルパーサ (Python `shlex` 等) ベースの再実装が必要です。

`deny` 時の `permissionDecisionReason` には、Claude が次に行うべき手順が記載されます。手順は **修正が落ち着くまで `/simplify` → `/codex:review` の両方をループ** する設計で、Claude が「修正不要」と判断したタイミングで `mark-reviewed.sh` → `git commit` に進みます。

> **順序の意図**: `/simplify` はコード変更を適用するため先に走らせ、`/codex:review` はその後の最終形を対象にレビューします。逆順だと codex が simplify によって書き換わる前のコードを見ることになり、レビュー結果が陳腐化します。

> **ループの意図**: 修正を加えた瞬間、その修正自体は未レビューになります。`/codex:review` の指摘を修正した結果として `/simplify` の対象 (重複・冗長コメント等) が新規発生する可能性も、`/simplify` の修正により `/codex:review` の新規指摘が出る可能性も、いずれもゼロではないため、修正があれば `/simplify` から再度ループします。プラグインは「マーカー作成時のステージング差分 = `git commit` 時のステージング差分」だけを検証するため、ループ回数は強制せず Claude の判断に委ねます。

> **終端の判断**: ループ回数の上限は設けません。Claude が「修正不要」または「人間判断を仰ぐべき」と判断したタイミングでのみ進行 / エスカレートします。固定回数で打ち切るような恣意的な制限はかけず、Claude の自主性に委ねる設計です。

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
4. Claude が /simplify を実行 (コード変更が起こり得るため先)
5. Claude が /codex:review を実行
6. 指摘があれば修正し、`git add` で再ステージング
7. ステージング内容が変わったら 4〜5 を再実行 (Claude が「修正不要」と判断するまで `/simplify` → `/codex:review` の両方をループ)
8. `bash "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/mark-reviewed.sh"` を実行
9. `git commit` を再試行 → マーカー一致で通過、マーカー削除
10. (PR 作成後) post-pr-review プラグインが `/code-review:code-review` を促す
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
