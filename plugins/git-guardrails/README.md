# git-guardrails プラグイン

GitHub Flow に準拠した Git ワークフローを **構造強制** するプラグインです。

## バージョン

v0.3.1

## 概要

「デフォルトブランチ (master/main) への変更は、他ブランチからの **GitHub 上の PR merge** 経由のみで取り込む」という運用を構造的に保証します。ローカル側の write 経路 (commit / push / PR head) を 3 つの PreToolUse フックで多層防御し、cooperative 利用前提で誤操作・横紙破りを deny に倒します。

加えて、rebase によるリモート default branch 取り込みワークフローを Skill として提供します。

### v0.3.0 → v0.3.1 の変更点 (#114, cross-plugin sync)

- **関連プラグイン pre-push-review の記述を v2.0.0 仕様に同期**: `## 関連プラグイン` の pre-push-review 行が「push 前に `/code-review` (旧名 `/simplify`) → `/codex:review --scope branch` を強制」 という v1.x 以前の旧記述だったため、 v2.0.0 の「`/pre-push-review:review` slash command で 3 レビュー (`/code-review` + codex review wrapper + `pre-push-review:security-reviewer` subagent) を並列起動」 に書き換え。 documentation のみの修正で hook / 動作は不変

### v0.2.4 → v0.3.0 の変更点 (#61, #62, #63, #60)

- **3 hook に診断 EXIT trap を追加 (#61)**: sibling の pre-push-review (`lib/exit-trap.sh`) と同型の `install_exit_trap` を `lib/exit-trap.sh` として導入。hook が予期せず非ゼロで終了 (jq クラッシュ / signal / シェル展開失敗等) した場合に stderr へ通知し、PreToolUse 仕様「その他 exit code で続行」による default branch 保護の無音 fail-open を可視化する。trap は exit code を変えないため deny/allow 挙動は不変
- **false-positive deny を既知の制約に明記 (#62)**: `git checkout <default> -- <pathspec>` (ファイル復元) と remote 名が `master`/`main` の push が誤って deny される件を文書化 (security gate は保守側に倒す方針のため修正せず明記)
- **共通 lib テーブル / ディレクトリ構成を実態に合わせ更新 (#63)**: `default-branch.sh` の全関数を列挙し、ベンダリングしている `cmd-parser.sh` と新規 `exit-trap.sh` を追記。cmd-parser.sh は pre-push-review から byte-identical でベンダリングしている旨を明記
- **README の version 見出し / changelog を実 version に追随 (#60)**: 見出しが v0.2.0 のまま実 version とドリフトしていたため v0.3.0 に更新し、欠落していた v0.2.1〜v0.2.4 の changelog を backfill

### v0.2.3 → v0.2.4 の変更点 (#92)

- rebase-workflow の `SKILL.md` frontmatter の無効なキー `when-to-use` (kebab-case) を有効な `when_to_use` (snake_case) に修正

### v0.2.2 → v0.2.3 の変更点 (#47, cross-plugin)

- **末尾 `\<LF>` (line continuation) による検知 bypass を修正**: bash の `$(...)` が trailing newline を trim する仕様で `"command":"git push origin master\<LF>"` 等が取得時点で壊れ default branch 保護を素通りする経路を、jq 取得直後の復元処理で 3 hook (commit/push/pr) に塞いだ。あわせて line continuation 正規化を macOS bash 3.2 互換実装に統一

### v0.2.1 → v0.2.2 の変更点 (#46)

- 共通 lib `cmd-parser.sh` を pre-push-review v0.8.0 の canonical 実装に sync (byte-identical ベンダリングの同期)

### v0.2.0 → v0.2.1 の変更点 (#42)

- macOS 互換性 fix (cmd-parser.sh canonical) への追随 version bump、およびプラグイン配下を変更したら version を必ず bump するルールを CLAUDE.md に追加

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
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install git-guardrails@natsuume-plugins
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

3 つの hook (`block-default-branch-{commit,push,pr}.sh`) が source する共有ライブラリ:

| ファイル | 用途 |
|---|---|
| `hooks/scripts/lib/default-branch.sh` | デフォルトブランチ名集合 (`master`/`main`) と、`is_default_branch` / `current_branch` / `strip_shell_quotes` / `normalize_refspec_part` / `strip_quoted_text` / `emit_deny` / `has_target_mismatch_prefix` の関数群 + `readonly TARGET_MISMATCH_DENY_REASON` を集約。3 hook が source して使う |
| `hooks/scripts/lib/cmd-parser.sh` | pre-push-review から **byte-identical でベンダリング** している共有パーサ。git-guardrails の 3 hook が実際に呼ぶのは `normalize_line_continuations_to_space` のみだが、canonical 実装とのドリフト防止のためファイル全体を丸ごとベンダリングしている (ヘッダコメントが pre-push-review を指すのはこのため) |
| `hooks/scripts/lib/exit-trap.sh` | 予期せぬ非ゼロ終了を stderr に可視化する `install_exit_trap`。3 hook が冒頭で呼ぶ (#61) |

## 既知の制約 (cooperative 利用前提)

- ブランチ名は `master` と `main` をハードコード対象としています。`develop` 等を保護対象に加えたい場合は `lib/default-branch.sh` の `DEFAULT_BRANCH_NAMES` 配列に追記してください
- push hook の master/main 検出は push 引数 token を完全一致比較するため、`git push origin origin/master` のように remote-tracking ref を直接引数に書く稀な経路は false negative で通ります (cooperative 利用では発生しにくく、`feature/main` のような working branch 名や `git push ... && git switch main` の連結後段を誤検出しないことを優先)
- **以下は誤検知 (false-positive deny) として `deny` に倒れます** (#62)。いずれも security gate を「保守側に倒す」方針の結果で、cooperative 利用では実害が小さいため修正せず明記しています。回避するには対象操作を別の方法で行ってください:
  - `git checkout <default> -- <pathspec>` を **commit/push/PR と連結した** chained コマンド (例: `git checkout main -- file.txt && git commit -m x`): `-- file.txt` は branch を切り替えず特定ファイルを作業ツリーに復元するだけですが、後続 invocation の target-mismatch 前段検査 (`has_target_mismatch_prefix`) が `checkout main` 部分を branch 切替とみなし、その commit/push/PR を deny します (単独の `git checkout main -- file.txt` は commit/push/PR を含まず各 hook の粗フィルタを通らないため deny されません)。回避するには checkout を別の Bash 呼び出しに分けてから commit してください
  - remote 名が `master` / `main` の push (例: `git push master feature`): `master` という名前の **remote** へ feature を push する操作ですが、push hook は引数 token を remote/refspec の区別なく比較するため、その `master` を default branch token と誤認して deny します
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
│           ├── default-branch.sh
│           ├── cmd-parser.sh    # pre-push-review から byte-identical ベンダリング
│           └── exit-trap.sh
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
- [pre-push-review](../pre-push-review/) — push 前に 3 レビュー (`/code-review` + codex review wrapper + `pre-push-review:security-reviewer` subagent) を `/pre-push-review:review` slash command で並列起動して強制 (本プラグインのブランチ判定とは別軸の防御)

## 関連情報

- [GitHub Flow](https://docs.github.com/ja/get-started/quickstart/github-flow)
- [Claude Code Hooks ドキュメント](https://code.claude.com/docs/en/hooks)
