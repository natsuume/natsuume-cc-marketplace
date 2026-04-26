# pre-commit-review プラグイン

`git commit` を実行する前に `/simplify` → `/codex:review --wait` を必ず実行させ、未レビューのコミットを構造的にブロックするプラグインです。`/simplify` はコード変更を伴うため先に走らせ、`/codex:review` はその後の最終形をレビューします。修正によりステージング内容が変わると **2 つのレビューマーカーが自動的に失効** するため、Claude は `/simplify` → `/codex:review` を再走させる以外に commit を通す手段がありません (= ループが構造的に強制されます)。Claude が「修正不要」と判断した時点で、再レビュー後に commit に進みます。Claude が「人間判断を仰ぐべき」と判断した場合のみユーザーへエスカレートします。PR を対象とする `/code-review:code-review` は姉妹プラグイン [post-pr-review](../post-pr-review/) が担当します。

## バージョン

v0.2.0

## 概要

`PreToolUse` フックで `Bash` ツール実行を監視し、`git commit` コマンドを検出した場合、 **2 つのレビューマーカー** (`/simplify` と `/codex:review --wait` それぞれの実行完了マーカー) が現在のステージング差分と同一ハッシュを保持していなければ `deny` を返してコミットを阻止します。マーカーは `PostToolUse` フックが各ツールの実走完了を検知して自動的に書き込みます。手動でスクリプトを呼び出す必要はありません。

### v0.1.0 → v0.2.0 の変更点 (互換性なし)

- `mark-reviewed.sh` を **削除** しました。マーカー作成は完全自動化されています。
- マーカーを **2 つに分割** (`.claude-pre-commit-simplified` / `.claude-pre-commit-codex-reviewed`) しました。両方が現在の差分と一致するときのみコミットが許可されます。
- `/codex:review` の `--background` モードは **非対応** になりました。auto-mark hook が Bash 完了時に発火する都合上、background 起動だとレビュー完了前にマーカー更新が走らずループが閉じないためです。

### v0.1.0 で残っていた構造的弱点と本バージョンでの解決

v0.1.0 では `mark-reviewed.sh` を Claude が手動で呼ぶ仕組みのため、以下の経路でループが回避できていました:

1. `/simplify` を 1 回実行
2. `/codex:review` を 1 回実行 → 指摘 N 件
3. 指摘を修正してステージング更新
4. **再レビューせずに** `mark-reviewed.sh` を呼ぶ → マーカーは「修正後の差分」のハッシュを記録
5. `git commit` → ハッシュ一致で commit が通る

この経路では「修正後の差分」が `/simplify` にも `/codex:review` にもかけられていません。v0.2.0 では「実走を hook が検知してマーカーを書く」方式に変更したため、`/simplify` と `/codex:review` を **再実行しない限り** マーカーは更新されず、修正後の差分での commit はブロックされ続けます。

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=pre-commit-review
```

## 機能一覧

### Hooks

#### 1. block-pre-commit (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-pre-commit.sh`

`git commit` を含むコマンドを検出した際、現在のステージング差分のハッシュと 2 つのレビューマーカーのハッシュを比較し、両方一致しなければ `deny` を返します。

**動作**:

- 単独実行 (`git commit -m "msg"`) と複合コマンド (`cd dir && git commit`) の双方を検出
- `git -C dir commit` や `git --git-dir=... commit` のように global option を伴う形式も検出
- `git commit-tree` 等の別コマンドは除外
- `git commit --help` / `-h` はスキップ
- 双方のマーカーが一致した場合は使い切りで両方とも削除 (再コミット時は再レビューが必要)
- ハッシュは `git diff --cached` (staged) と `git diff` (unstaged tracked) の連結に対して計算するため、`git commit -a` や `git commit <pathspec>` で未レビュー変更が紛れ込むケースもブロックされる
- `deny` 時の `permissionDecisionReason` には、各マーカーの状態 (`未実行` / `失効` / `✓ 最新の差分でレビュー済み`) と次に Claude が行うべき手順が記載される

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

> **順序の意図**: `/simplify` はコード変更を適用するため先に走らせ、`/codex:review` はその後の最終形を対象にレビューします。逆順だと codex が simplify によって書き換わる前のコードを見ることになり、レビュー結果が陳腐化します。マーカー方式上は順序を強制していませんが、修正後の差分で 2 マーカーを揃えるためには結局両方を走らせる必要があり、無駄を減らすには `/simplify` を先にする運用が合理的です。

> **`--wait` 限定の理由**: `/codex:review --background` だと Bash tool の `run_in_background: true` 起動直後に PostToolUse が発火し、レビューが完了する前に auto-mark.sh が呼ばれます。auto-mark.sh は background 起動を検知してマーカー更新をスキップするため、background 経由ではマーカーが永遠に更新されず commit が通りません。pre-commit-review の文脈では必ず `--wait` を渡してください。単体の `/codex:review` は通常 `AskUserQuestion` で実行方式を確認しますが、本プラグインの `permissionDecisionReason` で明示的に `--wait` を指示することでユーザーへの確認をスキップさせます。

> **ループの意図**: 修正を加えた瞬間、その修正自体は未レビューになります。`/codex:review` の指摘を修正した結果として `/simplify` の対象 (重複・冗長コメント等) が新規発生する可能性も、`/simplify` の修正により `/codex:review` の新規指摘が出る可能性も、いずれもゼロではないため、修正があれば `/simplify` から再度ループします。プラグインは「マーカーのハッシュ = `git commit` 時のステージング差分」だけを検証するため、ループ回数は強制せず Claude の判断に委ねます。

> **終端の判断**: ループ回数の上限は設けません。Claude が「修正不要」または「人間判断を仰ぐべき」と判断したタイミングでのみ進行 / エスカレートします。固定回数で打ち切るような恣意的な制限はかけず、Claude の自主性に委ねる設計です。

> **`/codex:review` と `/codex:rescue` の混同に注意**: 公式 codex プラグインには `/codex:review` (read-only コードレビュー) と `/codex:rescue` (修正・調査を delegate する subagent) の両方があり、用途が完全に別です。本プラグインが要求するのは前者です。Claude が誤って `/codex:rescue` を選ぶケースが報告されているため、運用時はコマンド名を明示的に確認してください。`/codex:review` は frontmatter で `disable-model-invocation: true` が指定されており本来 Skill tool から呼び出せませんが、姉妹プラグイン [codex-review-customize](../codex-review-customize/) を導入してパッチを適用すると Skill tool 経由でも呼び出し可能になります。

#### 2. auto-mark (PostToolUse, matcher: `*` — wildcard)

**ファイル**: `hooks/scripts/auto-mark.sh`

`/simplify` と `/codex:review --wait` の実行完了を PostToolUse hook で自動検知し、対応するマーカーファイルに「現在の staged + unstaged tracked 差分のハッシュ」を書き込みます。

hooks.json の matcher は `"*"` (wildcard) で、すべての tool 完了時に本フックが呼ばれます。`Skill` matcher の挙動が公式ドキュメント上完全に明記されていないため、tool 名に依存しない構造にしてあります。フィルタリングはスクリプト側の bash 内蔵正規表現マッチが行うため、対象外 tool は subprocess を立てずに即離脱します。

**検知ルール**:

| 検知対象                                 | tool 名 | 判定                                                                                                          | 書き込むマーカー                            |
| ---------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| `/simplify` skill の launch              | `Skill` | `tool_input.skill == "simplify"`                                                                              | `<git-dir>/.claude-pre-commit-simplified`   |
| `/codex:review --wait` の Bash 完了      | `Bash`  | コマンドが `^node` で始まる (env-prefix 許容) / `codex-companion.m[jt]s review` を含む / `run_in_background == false` / 失敗・中断ではない | `<git-dir>/.claude-pre-commit-codex-reviewed` |

**`/simplify` を launch タイミングで検知する設計上のトレードオフ**:

`Skill` tool の `PostToolUse` は `Launching skill: simplify` を返した瞬間 (= skill body 実行 **前**) に発火します。本プラグインはこの timing でマーカーに **launch 時点の差分ハッシュ** (= simplify が見ることになる state) を書き込みます。

- メリット (loop discipline): simplify body が edits を行えば current hash は launch 時点と異なる値になります。block-pre-commit.sh はこの hash と current hash を比較するため、edit 後は marker stale → DENY となり、Claude は **修正後の state で再度 `/simplify` を呼ぶ** 必要が生じます。これにより「修正後の差分は必ず simplify を再走させる」という loop discipline が構造的に強制されます。
- 既知の限界 (lie attack): Claude が `Skill(simplify)` を呼んでも skill body の meta prompt を実際に実行せず、その後 `/codex:review --wait` を呼んで commit する経路では、両マーカーが launch 時点の hash で揃ってしまい commit が通ってしまいます。これは Claude が instructions を真摯に follow するという信頼を前提とした設計で、構造的には防げません。

(代替案: Stop event でマーカーを finalize する設計も検討しましたが、その場合 simplify 完了後の codex review 修正分も「simplified 済み」と誤判定されるため、loop discipline を破壊する副作用がありました。本プラグインは loop discipline を優先する判断をしています。)

**書き込みをスキップする条件**:

- `tool_response.is_error` または `tool_response.interrupted` が `true` (失敗した review 結果でマーカーを書かない)
- `tool_input.run_in_background` が `true` (background 起動は完了タイミングを捉えられないため)
- `tool_input.skill` が `simplify` 以外 (namespace 付き skill は別物として扱う)
- `git rev-parse --git-dir` に失敗 (リポジトリ外)

書き込まれるハッシュは `block-pre-commit.sh` が検証するハッシュと完全に同じ計算式 (staged + unstaged tracked の連結) です。

## ワークフロー

```
1. ユーザー: 「コミットして」
2. Claude が `git commit` を試行
3. block-pre-commit.sh が deny を返し、レビュー実行を指示 (両マーカー未実行のため)
4. Claude が /simplify を Skill tool で呼び出す (コード変更が起こり得るため先)
   → PostToolUse(Skill) で auto-mark.sh が simplified マーカーを書き込む
5. Claude が /codex:review --wait を Skill tool で呼び出す
   → 内部で Bash(codex-companion.mjs review) が走る
   → PostToolUse(Bash) で auto-mark.sh が codex-reviewed マーカーを書き込む
6. レビュー結果に指摘があれば修正し、`git add` で再ステージング
   → ステージング差分が変わるため両マーカーが自動的に失効する
7. 4〜6 を Claude が「修正不要」と判断するまで繰り返す
8. 双方のマーカーが「✓ 最新の差分でレビュー済み」になったら `git commit` を再試行
   → ハッシュ一致で通過、両マーカーを自動削除
9. (PR 作成後) post-pr-review プラグインが `/code-review:code-review` を促す
```

## 注意事項

- `/codex:review` は **必ず `--wait` を付けて呼び出してください**。`--background` ではマーカーが更新されず commit が通りません。
- `/simplify` skill は本プラグインが期待するのは namespace なしの `simplify` です。`pr-review-toolkit:code-simplifier` 等の別 skill ではマーカーが更新されません。
- マーカー更新後にトラッキング済みファイルを編集すると、ハッシュが一致しなくなり再レビューが必要になります。
- 未トラッキングのファイルはハッシュ計算に含まれません。新規ファイルをコミット対象にする場合は、レビュー前に `git add` でステージングしてから `/simplify` → `/codex:review --wait` を実行してください。
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
│       ├── block-pre-commit.sh   (PreToolUse: git commit を検証)
│       ├── auto-mark.sh          (PostToolUse: 実走完了を検知してマーカー更新)
│       └── lib/
│           └── diff-hash.sh      (両者で共有するレビュー差分ハッシュ計算)
└── README.md
```

## 必要な実行環境

- `bash`
- `jq`
- `git`
- `sha256sum` (coreutils)

## 関連情報

- [Claude Code Hooks ドキュメント](https://docs.anthropic.com/claude-code/hooks)
