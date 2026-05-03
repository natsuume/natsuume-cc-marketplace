# git-guardrails プラグイン

GitHub Flow に準拠した Git ワークフローを **構造強制** するプラグインです。

## バージョン

v0.2.0

## 概要

「デフォルトブランチ (master/main) への変更は、他ブランチからの **GitHub 上の PR merge** 経由のみで取り込む」という運用を構造的に保証します。ローカル側の write 経路 (commit / push / PR head) を 3 つの PreToolUse フックで多層防御し、cooperative 利用前提で誤操作・横紙破りを deny に倒します。

加えて、rebase によるリモート default branch 取り込みワークフローを Skill として提供します。

### v0.1.0 → v0.2.0 の変更点

- **commit / push / PR の 3 経路を全て deny 対象に拡張** (旧版は push のみ):
  - `git commit` (デフォルトブランチ上で実行された場合)
  - `git push` (引数の有無に関わらず、デフォルトブランチを更新する経路すべて)
  - `gh pr create` (head が master/main になるケース、`--head` 明示時も含む)
- 旧版の push 検出 regex は `git push <remote> master` のような明示引数形式しか拾えず、`git push` 単独 / `git push origin` 等の引数省略形 (= upstream 設定で master を更新する形) を素通りさせていました。v0.2.0 ではカレントブランチを `git symbolic-ref --short HEAD` で取得し「master/main 上にいるなら全 push 系を deny」「他ブランチからは引数の master/main トークンで判定」の 2 軸で網羅的に検出します。push 検出は `git -C dir push` / `git -c push.default=current push` のような git global option 経由の形式も拾えるよう OPT-aware にしています。
- **target-mismatch prefix の保守的 deny**: 対象 repo / branch を切り替える前段を含むコマンドは、hook 実行時の cwd / カレントブランチと実コマンドの target が乖離して default branch 保護を素通りさせる経路になります。3 hook すべてで `has_target_mismatch_prefix` 検出を入れ、対象 repo / branch に切り替えた上で別 Bash 呼び出しとして実行するよう促します:
  - 対象 repo を切り替える形: `cd /other && git push` / `git -C /other push` / `GIT_DIR=/foo/.git git push`
  - 対象 branch を切り替える形: `git switch master && git commit` / `git checkout main && git push` / `git switch -c master && ...` (新規作成も同列)
  - subshell / brace group / command substitution 越し: `(cd /other; git commit)` / `{ cd /other; git push; }` / `$(cd /other; git ...)` (`()` / `{}` を空白に正規化してから検出)
  - redirection 演算子直結: `cd>/dev/null /other && git push` / `cd</dev/null /other && ...` (cd の右境界に `<>` を含める)
  
  pre-push-review (v0.1.0 で pre-commit-review v0.4.0 から push 境界に移行) の cd 許容方針とは別軸の防御で、本プラグインは default branch 保護が責務。
- 共通 lib (`hooks/scripts/lib/default-branch.sh`) に `current_branch` / `is_default_branch` / `emit_deny` / `has_target_mismatch_prefix` / `TARGET_MISMATCH_DENY_REASON` を集約し、3 hook 間で判定ロジック・deny ペイロード形式・メッセージ文言がドリフトしないようにしています。

> v0.1.0 までは `gh pr create` への `--draft` 自動付与もこのプラグインに含まれていましたが、責務分離のため [enforce-draft-pr](../enforce-draft-pr/) プラグインに切り出しました。draft 強制を使いたい場合はそちらを別途インストールしてください。

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=git-guardrails
```

## 機能一覧

### Hooks

#### 1. block-default-branch-commit (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-default-branch-commit.sh`

カレントブランチが master/main のときに `git commit` 系コマンドを deny します。

**動作**:

- 入力に `commit` 文字列が無ければ即離脱 (粗フィルタ)
- `git ... commit` パターンで commit 実行を検出 (git の global option `-C` / `-c key=val` を伴う形式も含む)
- `git symbolic-ref --short HEAD` でカレントブランチを取得
- master/main なら deny、それ以外なら exit 0
- detached HEAD (cherry-pick / rebase 中など) はブランチ名が空なので自然に通る
- **chained 形式**: `git commit -m a && cd /other && git commit -m b` のように複数の commit を連結したコマンドは、各 commit 呼び出しを独立に target-mismatch 検査します。最初の commit が問題なくても、その後の cd で対象 repo を切り替えて 2 つ目の commit を行う経路は detect されて deny

**deny 例**:

```
master ブランチで `git commit -m "fix"` を実行
  → "デフォルトブランチ (master) 上での git commit は禁止されています。
     working branch を切ってから commit してください
     (例: git switch -c feat/my-change)。"
```

#### 2. block-default-branch-push (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-default-branch-push.sh`

デフォルトブランチを更新するすべての push 系コマンドを deny します。

**動作**:

- `git ... push` パターンで push 実行を検出 (git の global option `-C` / `-c key=val` 等を伴う形式も含む)
- カレントブランチが master/main の場合: 引数の有無に関わらず deny (引数省略形 `git push` / `git push origin` でも upstream 経由で master を更新するため)
- それ以外のブランチの場合: 引数の各 token を `+` / `refs/heads/` プレフィックス剥がし後に完全一致比較し、master/main が現れたら deny
  - `git push origin master` (明示)
  - `git push origin feat:master` (refspec の右側) / `git push origin master:feat` (refspec の左側)
  - `git push --force-with-lease origin master`
  - `git push origin +master` / `git push origin refs/heads/master` / `git push origin HEAD:refs/heads/main` (refspec normalize 後に完全一致)
  - `git push origin "master"` (shell quote 剥がし後に完全一致)
- **`--all` / `--mirror` も deny**: refspec を明示せず全ローカル branch を push するモードは、master/main が local に存在すれば自動的にそれを更新するため、token 完全一致比較を素通りする bypass になる。default branch 保護のため一律 deny
- **chained 形式**: `git push origin feature && git push origin master` のように `&&` / `||` / `;` / `&` / `|` で連結された複数の push を含むコマンドは、各 push 呼び出しを独立に検査します。最初の push が APPROVE でも 2 つ目が master を更新するならその時点で deny

**通す例**:

- master 以外のブランチからの `git push` (引数なし、master を更新しない)
- `git push origin feature` (master トークンを含まない)
- `git push origin feature/main` (working branch 名は完全一致しないので APPROVE)

#### 3. block-default-branch-pr (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-default-branch-pr.sh`

`gh pr create` で master/main を head (= source) とする PR を deny します。head が master とは「master の中身を別ブランチに PR で入れる」変則的な経路で、cooperative 運用では発生せず防御対象とします。

**動作**:

- `gh ... pr create` パターンで PR 作成を検出 (連結 prefix `cd repo && gh pr create ...` / `xxx ; gh pr create ...` も対象)
- head 指定の以下 5 形式を抽出して評価し、master/main なら deny:
  - `--head <branch>` (別トークン long)
  - `--head=<branch>` (`=` 付き long)
  - `-H <branch>` (別トークン short)
  - `-H=<branch>` (`=` 付き short)
  - `-Hbranch` (cluster short)
- 値が `owner:branch` 形式 (cross-fork PR) なら owner プレフィックスを剥がした branch 部分で判定。shell quote (`"master"`/`'main'`) も剥がしてから比較
- 明示がなければカレントブランチを評価し、master/main なら deny
- それ以外は exit 0
- **chained 形式**: `gh pr create --head feat && gh pr create --head master` のように複数の `gh pr create` を連結した場合、各 PR 作成を独立に検査します。最初の PR が APPROVE でも 2 つ目で master を head にする形なら detect されて deny

**通す例**:

- working branch から `gh pr create --base master` (= 通常の PR)
- master 上から `gh pr create --head feature` (head 明示で master を head にしない)

### Skills

#### /rebase-workflow

**ファイル**: `skills/rebase-workflow/SKILL.md`

rebase を用いてリモートのデフォルトブランチの変更を作業ブランチに取り込む手順をガイドします。

**使用シーン**:

- 「リモートの変更を取り込みたい」
- 「rebase したい」
- 「master/main を取り込む」
- 「ブランチを最新にしたい」

## 共通 lib

| ファイル | 用途 |
|---|---|
| `hooks/scripts/lib/default-branch.sh` | デフォルトブランチ名集合 (`master`/`main`) と `current_branch` / `is_default_branch` の判定関数を集約。3 hook が source して使う |

## 既知の制約 (cooperative 利用前提)

- ブランチ名は `master` と `main` をハードコード対象としています。`develop` 等を保護対象に加えたい場合は `lib/default-branch.sh` の `DEFAULT_BRANCH_NAMES` 配列に追記してください
- push hook の master/main 検出は push 引数 token を完全一致比較するため、`git push origin origin/master` のように remote-tracking ref を直接引数に書く稀な経路は false negative で通ります (cooperative 利用では発生しにくく、`feature/main` のような working branch 名や `git push ... && git switch main` の連結後段を誤検出しないことを優先)
- `gh pr create` 以外の経路 (Web UI 等での PR 作成) は介入できません。これは設計上の意図です

## ディレクトリ構成

```
git-guardrails/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       ├── block-default-branch-commit.sh
│       ├── block-default-branch-push.sh
│       ├── block-default-branch-pr.sh
│       └── lib/
│           └── default-branch.sh
├── skills/
│   └── rebase-workflow/
│       └── SKILL.md
└── README.md
```

## 必要な実行環境

- `bash`
- `jq`
- `git`

## 関連プラグイン

- [enforce-draft-pr](../enforce-draft-pr/) — `gh pr create` 時に `--draft` を自動付与 (任意導入)
- [pre-push-review](../pre-push-review/) — push 前に `/simplify` → `/codex:review --scope branch` を強制 (本プラグインのブランチ判定とは別軸の防御)

## 関連情報

- [GitHub Flow](https://docs.github.com/ja/get-started/quickstart/github-flow)
- [Claude Code Hooks ドキュメント](https://docs.anthropic.com/claude-code/hooks)
