---
name: update-default-branch
description: PR がマージ・リモート反映された報告を受けたあと、デフォルトブランチを最新化し、追跡先が消えた (gone) ローカルブランチを削除する。マージ完了、ブランチ最新化、不要・マージ済みブランチの片付け依頼で使う
---

# update-default-branch

PR がマージされた直後に呼び出されるワークフローです。デフォルトブランチを最新化したうえで、追跡先がリモートから消えたローカルブランチを `[gone]` 状態として検出し、まとめて削除します。

## 前提条件

- `origin` リモートが設定されていること

## 実行原則: 1 手順 = 1 つの素朴な git コマンド

本 Skill の各手順は、**単一の git コマンドを 1 回の Bash 呼び出しで実行**します。以下は手順内のコマンド文字列に **一切含めてはいけません**:

- コマンド置換 `$(...)` / バッククォート
- 連結 (`&&` / `||` / `;`)・パイプ・`if` / `while` 等の複合構文
- シェル変数の定義・参照 (`DEFAULT_BRANCH=...` や `"$BRANCH"` 等)
- `echo` によるメッセージ出力 (ユーザーへの説明はコマンドではなくあなたの応答文で行う)

**なぜ**: 同居しうる他プラグインの PreToolUse hook (auto-lint-check の block-commit-lint 等) は、コマンド文字列に `$(...)` のような「内部で何が実行されるか静的解析できない構文」を見つけると安全側 (fail-closed) で deny します。複数手順を 1 スクリプトに合成すると、git 操作とメッセージ文字列の組み合わせがこの検査に引っかかり、Skill 全体が実行不能になります。逆に「素朴な単一コマンド」は各 hook の parser が正確にトークン解析できるため、ブロックされません (decompose-bash プラグインが要求する分解方針とも一致します)。

**状態の引き継ぎ方**: 手順間で必要な値 (元のブランチ名・デフォルトブランチ名・削除対象一覧) は、シェル変数や state file ではなく **実行中のエージェントが各コマンドの出力を読んで会話コンテキストに保持**し、後続コマンドには **リテラル値として直接埋め込み**ます (例: `git switch 'master'`)。コマンド出力の判定 (空かどうか、`[gone]` はどれか) もコマンド側で加工せず、エージェントが出力を読んで行います。

**埋め込み時は必ず single quote で囲む**: git のブランチ名には `$` / `;` / `&` / `(` 等のシェルメタ文字が合法に含まれえます (`git check-ref-format` は禁止しない)。クォートなしで埋め込むと `git branch -D feature;id` が「`git branch -D feature` の後に `id` を実行」と解釈されるなど、別コマンド実行や変数展開が起きます。ブランチ名をコマンドに埋め込むときは **常に single quote で囲んで完全リテラル化**してください (例: `git switch 'feat/x'`、`git branch -D 'b1' 'b2'`)。single quote は hook の tokenizer も正しく処理するため hook-safe 性は維持されます。**ブランチ名自体に single quote (`'`) が含まれる場合** はエスケープの組み立てを試みず、実行を中止してユーザーに対処を確認してください (エスケープの組み立てミス自体が新たな事故経路になるため)。

## 手順

### 1. 作業ツリーの安全確認

```bash
git status --short
```

出力が空でない場合は **ここで中断** し、ユーザーに stash か commit を促してください (勝手に `stash` や `reset` をしてはいけません)。

### 2. 元のブランチ名を記憶

```bash
git branch --show-current
```

出力されたブランチ名を `ORIGINAL_BRANCH` として **会話コンテキストで記憶** します。手順 8 でこのブランチに戻すか、削除された場合は新しい作業ブランチを切るかの判断に使います。記憶した値は **ユーザー向けの応答文にも明記** してください (例: 「元のブランチ: feat/foo」)。途中で context が圧縮されても要約に残り、手順 8 で参照できます。

出力が空の場合は detached HEAD です。その場合も続行してよいですが、手順 8 では「ユーザーに新規作業ブランチを促す」分岐に入ります。

### 3. デフォルトブランチ名の取得

リポジトリによって `master` / `main` / `develop` などが異なるため、remote の現在値から動的に取得します。local の `origin/HEAD` は remote 側の default branch が変更されても自動更新されず、旧 branch が残っていると stale な値を正常終了で返します。そのため、`symbolic-ref` を読む前に次の 3 コマンドを必ず順番どおり実行します。

```bash
git fetch --prune origin
```

この fetch で、remote の新しい default branch を含む remote-tracking refs を更新し、削除済み refs も整理します。失敗した場合は中断し、origin の到達性をユーザーに報告してください。stale な `origin/HEAD` を fallback として読んではいけません。

次に remote が広告する現在の default branch へ `origin/HEAD` を更新します。

```bash
git remote set-head origin --auto
```

失敗した場合は中断し、`git remote -v` と origin の到達性、remote に default branch が設定されているかの確認をユーザーに依頼してください。既存の `origin/HEAD` は成功扱いしません。

更新に成功した場合だけ、default branch 名を読み取ります。

```bash
git symbolic-ref refs/remotes/origin/HEAD
```

出力 `refs/remotes/origin/<name>` の `<name>` 部分を `DEFAULT_BRANCH` として記憶します (prefix の除去はあなたが読み取りで行う。`sed` へのパイプは不要)。失敗した場合は中断し、`origin/HEAD` の状態をユーザーに報告してください。

### 4. デフォルトブランチへ切り替え

`<DEFAULT_BRANCH>` は手順 3 で記憶した実際の名前に置き換えます (以下同様。single quote は残す)。

```bash
git switch '<DEFAULT_BRANCH>'
```

`already used by worktree` で失敗した場合はトラブルシューティング節「worktree 競合」を参照してください。

> **注意**: デフォルトブランチでの作業が禁止されている場合、ここでの切り替えは「最新化のための一時的な遷移」です。手順 8 で必ず元のブランチへ戻るか、新しい作業ブランチを切り直してください。

### 5. fast-forward で最新化

`--ff-only` で fast-forward しか許さないように制御します。`pull.rebase` / `merge.ff` などのユーザー設定に依存して意図せず merge commit がデフォルトブランチに作られるのを避けるためです。

```bash
git pull --ff-only origin '<DEFAULT_BRANCH>'
```

失敗した場合 (ローカルが分岐している) は削除フェーズ (手順 6〜7) に進まず **ここで中断** し、トラブルシューティング節に従ってユーザーに状況を報告します。中断前に **デフォルトブランチに居着かない** よう、**手順 8 と同じ分岐表に従って復帰** してください: `ORIGINAL_BRANCH` が空 (detached HEAD) / デフォルトブランチと同一 / 不明の場合は switch せずユーザーに次の作業ブランチを確認し、それ以外は以下で復帰します。

```bash
git switch '<ORIGINAL_BRANCH>'
```

### 6. `[gone]` 状態のローカルブランチを検出

```bash
git for-each-ref --format='%(refname:short) %(upstream:track)' refs/heads
```

出力の各行を **あなたが読んで**、2 列目が `[gone]` の行のブランチ名を削除対象として抽出します (`awk` へのパイプは不要)。`DEFAULT_BRANCH` と現在チェックアウト中のブランチは対象から除外します。

### 7. ローカルブランチを削除

`[gone]` 状態のブランチは追跡先のリモートが既に削除済み (PR マージ後に GitHub がブランチを削除した、または別環境でクリーンアップされた) なので、ローカルの後始末を確認なしで実行します。承認ステップは認知負荷を増やすだけで実質的なリスク低減にならないため、検出と削除を一気に行い、削除結果を事後に表示します。

削除対象が複数ある場合も **1 回の呼び出しに列挙** します (該当がなければこの手順をスキップして手順 8 へ)。各ブランチ名は個別に single quote で囲みます。

```bash
git branch -D '<branch1>' '<branch2>' ...
```

`-D` を使うのは、`[gone]` 状態では追跡先が無くマージ判定が成立しない、かつ squash/rebase マージでは feature branch のコミット ID が default branch に存在しないため `-d` が「未マージ」として拒否してしまうためです。

> **注意 (`[gone]` の意味)**: `[gone]` は「リモート側の対応 ref が消えている」状態を指し、必ずしも「PR がマージ済み」とは限りません。リモートでの force-delete やリネームでも `[gone]` になります。`git branch -D` は merge 検査を skip するため、まれに「ローカルにのみ存在するコミットを保持する `[gone]` ブランチ」が誤削除される可能性があります。`git branch -D` の出力には削除前の SHA が表示されるので、削除結果 (ブランチ名と SHA) は必ずユーザーに事後報告してください。誤削除に気づいた場合は表示された SHA を `git checkout -b '<name>' <sha>` で復活できます (約 30 日は `git reflog` でも遡れます)。マージ済み以外の理由で `[gone]` になる branch を残しておきたい場合は、本 Skill の手順 7 を実行する前に作業ブランチへ移しておくか、Skill の利用自体を控えてください。

### 8. 元のブランチへ戻す (またはユーザーに新ブランチを促す)

手順 2 で記憶した `ORIGINAL_BRANCH` に応じて分岐します。デフォルトブランチに居着いたまま終了させない (CLAUDE.md でデフォルトブランチ作業が禁止されている場合に重要) ことが目的です。

| 条件 | アクション |
|-----|-----------|
| `ORIGINAL_BRANCH` が空 (detached HEAD だった) | ユーザーに次の作業ブランチ名を確認し、`git switch -c '<name>'` で作成 |
| `ORIGINAL_BRANCH` が手順 7 の削除対象に含まれていた | ユーザーに次の作業ブランチ名を確認し、`git switch -c '<name>'` で作成 |
| `ORIGINAL_BRANCH` がデフォルトブランチと同じ | (元からデフォルトブランチにいたので) 何もしない。ユーザーに作業ブランチを切るよう促す |
| 会話の経緯から `ORIGINAL_BRANCH` が特定できない (context 圧縮等) | ユーザーに戻り先を確認する (推測で switch しない) |
| それ以外 (元のブランチが残っている) | 以下で復帰 |

```bash
git switch '<ORIGINAL_BRANCH>'
```

`DEFAULT_BRANCH` が不明になった場合は手順 3 のコマンドを再実行して機械的に再導出して構いません (`ORIGINAL_BRANCH` は git から再導出できないため、不明ならユーザーに確認します)。

## トラブルシューティング

### `git pull --ff-only` が失敗する

ローカルのデフォルトブランチに独自コミットが残っており、fast-forward できません。`git log <DEFAULT_BRANCH>..origin/<DEFAULT_BRANCH>` および `git log origin/<DEFAULT_BRANCH>..<DEFAULT_BRANCH>` で状況を確認し、ユーザーに相談してから対処してください (rebase / reset --hard などは独断で行わない)。

### `[gone]` のブランチが想定より多い

過去に `git branch -m` でリネームしたブランチや、別環境で削除されたブランチが含まれることがあります。削除自体は確認なしで実行されますが、削除結果はユーザーに事後表示し、想定外のブランチが消えていないかを確認できるようにします (誤削除に気づいたら `git reflog` で復旧可能)。

### worktree が紐づいているブランチを削除しようとした

`git branch -D` は worktree にチェックアウトされたブランチを削除できません。`git worktree list` で確認し、不要なら `git worktree remove <path>` してから再度削除してください。

### `git switch` がデフォルトブランチへの切り替えに失敗する (worktree 競合)

リンク worktree から実行し、デフォルトブランチ (master/main) が **別の worktree に既にチェックアウト済み** の場合、手順 4 の `git switch '<DEFAULT_BRANCH>'` は `fatal: '<branch>' is already used by worktree at ...` で失敗します。git は同一ブランチを複数 worktree で同時にチェックアウトできないためです。`git worktree list` で確認し、デフォルトブランチを保持している worktree 上で最新化するか、別 worktree 運用を見直してください (この Skill は単一 worktree での実行を想定しています)。
