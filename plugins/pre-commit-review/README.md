# pre-commit-review プラグイン

`git commit` を実行する前に `/simplify` → `/codex:review --wait` を必ず実行させ、未レビューのコミットを構造的にブロックするプラグインです。`/simplify` はコード変更を伴うため先に走らせ、`/codex:review` はその後の最終形をレビューします。修正によりステージング内容が変わると **2 つのレビューマーカーが自動的に失効** するため、Claude は `/simplify` → `/codex:review` を再走させる以外に commit を通す手段がありません (= ループが構造的に強制されます)。Claude が「修正不要」と判断した時点で、再レビュー後に commit に進みます。Claude が「人間判断を仰ぐべき」と判断した場合のみユーザーへエスカレートします。

ループが一定回数以上続いても収束しない場合、deny メッセージに **`/codex:adversarial-review`** (実装方針・設計選択への批判的レビュー) を促す案内が追加されます。表層レビューだけで収束しないループに対し「採用しているアプローチ自体が妥当か」を問い直す視点を取り入れる動線です。PR を対象とする adversarial review は姉妹プラグイン [post-pr-review](../post-pr-review/) が PR 作成直後に誘導します。

## バージョン

v0.4.0

## 概要

`PreToolUse` フックで `Bash` ツール実行を監視し、`git commit` コマンドを検出した場合、 **2 つのレビューマーカー** (`/simplify` と `/codex:review --wait` それぞれの実行完了マーカー) が現在のステージング差分と同一ハッシュを保持していなければ `deny` を返してコミットを阻止します。マーカーは `PostToolUse` フックが各ツールの実走完了を検知して自動的に書き込みます。手動でスクリプトを呼び出す必要はありません。

加えて、`/codex:review --wait` が完了するたびに **ループカウンタ** が +1 され、閾値以上になると deny メッセージに `/codex:adversarial-review --wait --scope working-tree` の実行を促す案内が追加されます (commit を追加で block する判定は変えず、案内文のみ追加する設計)。閾値の現在値は `block-pre-commit.sh` の `LOOP_THRESHOLD` で定義されており、運用経験で調整できます。commit 成功時にカウンタはマーカーと一緒にリセットされます。

### v0.3.0 → v0.4.0 の変更点

- **ループカウンタ追加**: `/codex:review --wait` の完了が `LOOP_THRESHOLD` 以上になってもまだ commit に至らない場合、deny メッセージに `/codex:adversarial-review` の実行を促す案内文が追加されるようになりました。表層レビューだけで収束しない場合、根本的な実装方針・アーキテクチャ設計のミスマッチを疑う動線です。閾値以上になっても commit を追加で block する挙動は変えず、案内文のみ追加します (= adversarial review はマーカー対象外で、ループ進行は通常通り `/simplify` → `/codex:review --wait` で閉じます)。
- **deny 制約の大幅な緩和**: `cd dir && git commit ...` / `git -C ... commit` / `GIT_DIR=... git commit` / `git commit -m "$(cat <<'EOF' ... EOF)"` 等の一般的な利用形態を許容するよう deny を撤廃しました。詳細は下記「緩和した制約 (v0.4.0 で deny 解除)」を参照してください。

これらの緩和は **本プラグインが natsuume 個人 (および本人環境の Claude Code) のみで使われる前提** で行われています。adversarial bypass を試みる悪意あるプロンプト経路は防御対象から外し、cooperative な Claude Code との運用利便性を優先する判断です。loop discipline 自体 (= マーカーのハッシュが現在の差分と一致しなければ deny) は引き続き構造的に保持されています。

### v0.2.0 → v0.3.0 の変更点

- `xxx && git commit ...` のような **連結コマンド形式を許容** するようになりました (`git status && git commit -m ...`, `git add path/ && git commit -m ...` 等)。`git commit` がコマンド末尾に位置すること (postfix にシェル区切り文字を続けないこと) は引き続き構造的に強制します。
- postfix チェックの基準を「最初の `commit` 文字列」から「実際の `git ... commit` 呼び出しの位置」に修正しました。`echo commit && git commit -m fix` や `grep commit file && git commit -m fix` のように prefix に literal `commit` 文字列がある連結形式が誤検知される問題を解消しています。
- `-C` / `--git-dir` / `--work-tree` の検出範囲を **最後のシェルセグメント** に限定しました (※ v0.4.0 で deny ロジック自体が撤廃されています)。
- `git commit --help` / `-h` のスキップ判定を「最初の `git ... commit` 呼び出しが `--help`/`-h` のみで終わるか」で行うように変更しました。
- `bash -c "..."` 等のシェルラッパー経由 commit は引き続き deny します (クォート内のコマンドを本フックの文字列ベースなパーサで検証できないため)。

### v0.1.0 → v0.2.0 の変更点 (互換性なし)

- `mark-reviewed.sh` を **削除** しました。マーカー作成は完全自動化されています。
- マーカーを **2 つに分割** (`.claude-pre-commit-simplified` / `.claude-pre-commit-codex-reviewed`) しました。両方が現在の差分と一致するときのみコミットが許可されます。
- `/codex:review` の `--background` モードは **非対応** になりました。auto-mark hook が Bash 完了時に発火する都合上、background 起動だとレビュー完了前にマーカー更新が走らずループが閉じないためです。

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=pre-commit-review
```

## 機能一覧

### Hooks

#### 1. block-pre-commit (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-pre-commit.sh`

`git commit` を含むコマンドを検出した際、現在のステージング差分のハッシュと 2 つのレビューマーカーのハッシュを比較し、両方一致しなければ `deny` を返します。加えてループカウンタが閾値以上のときは deny メッセージに `/codex:adversarial-review` の案内文を追加します (commit の block / 通過判定自体には影響しない)。

**動作**:

- 単独実行 (`git commit -m "msg"`) と複合コマンド (`xxx && git commit ...`, `cd dir && git commit ...`) の双方を検出
- `git -C dir commit` や `git --git-dir=... commit`、`GIT_DIR=... git commit` のような target-override 形式も許容 (cooperative 利用前提で deny を撤廃)
- `git commit-tree` 等の別コマンドは除外
- `git commit --help` / `-h` はスキップ (`xxx && git commit --help` のような連結形式も末尾が `--help` のみで構成されている限りスキップ)
- 双方のマーカーが一致した場合は使い切りで両方とも削除し、ループカウンタも併せてリセット (再コミット時は再レビューが必要)
- ハッシュは `git diff --cached` (staged) と `git diff` (unstaged tracked) の連結に対して計算するため、`git commit -a` や `git commit <pathspec>` で未レビュー変更が紛れ込むケースもブロックされる
- `deny` 時の `permissionDecisionReason` には、各マーカーの状態 (`未実行` / `失効` / `✓ 最新の差分でレビュー済み`) と次に Claude が行うべき手順、ループ回数 / 閾値が記載される

**残っている deny 制約 (loop discipline 維持に必要な最小防御)**:

- `bash -c "..."` 等の **シェルラッパー** 経由 commit は引き続き deny (クォート内のコマンドを本フックの文字列パーサで解析できず、postfix scan も成立しないため)
- 単独の `&` (background) と `|` (pipeline) は deny (並列実行になりマーカー検証完了後に index が変更されたり commit へ状態が流れ込んだりする経路になるため)
- `git commit` の **後** にシェル区切り文字 (`;`, `&`, `&&`, `||`, `|`) を続ける複合コマンドは deny (1 マーカー = 1 commit 保証のため)
- 引用符で囲まれた `git commit` 文字列 (`grep "git commit" README` など) はテキスト参照とみなしフックは介入しません
- `time git commit ...` / `env git commit ...` のように本フックが認識していない wrapper を介して commit する形式は deny (postfix scan の起点が取れず未レビュー commit を素通しさせるリスクを保守的に塞ぐため)

**緩和した制約 (v0.4.0 で deny 解除)**:

- ✅ `cd dir && git commit ...`, `pushd dir && git commit ...`, `popd && git commit ...` を許容
- ✅ `builtin cd ...` / `command cd ...` / `eval cd ...` ラッパー越しの cd 前段も許容
- ✅ `(cd dir && git commit ...)` のサブシェル / `{ cd dir; git commit ...; }` のブレースグループ越しの cd も許容
- ✅ `git -C dir commit ...`, `git --git-dir=... commit ...`, `git --work-tree=... commit ...` を許容
- ✅ `GIT_DIR=... git commit ...`, `GIT_WORK_TREE=... git commit ...`, `GIT_INDEX_FILE=... git commit ...` を許容
- ✅ `export GIT_DIR=... && git commit ...`, `declare -x GIT_DIR=... && git commit ...` も許容
- ✅ `git commit -m "$(cat <<'EOF' ... EOF)"` のような heredoc / コマンド置換 / プロセス置換を含む commit message を許容

> **target-mismatch リスクについて**: 上記緩和により、hook 検証時の cwd と実際の commit が走る cwd が乖離するケース (= 別 repo / 別 worktree のマーカーで commit が通る) が原理的に発生し得ます。これは adversarial な bypass 試行に対しては脆弱ですが、cooperative な単独利用では発生しません。本プラグインは natsuume 個人環境専用の前提で、利便性を優先する判断をしています。

> **順序の意図**: `/simplify` はコード変更を適用するため先に走らせ、`/codex:review` はその後の最終形を対象にレビューします。逆順だと codex が simplify によって書き換わる前のコードを見ることになり、レビュー結果が陳腐化します。マーカー方式上は順序を強制していませんが、修正後の差分で 2 マーカーを揃えるためには結局両方を走らせる必要があり、無駄を減らすには `/simplify` を先にする運用が合理的です。

> **`--wait` 限定の理由**: `/codex:review --background` だと Bash tool の `run_in_background: true` 起動直後に PostToolUse が発火し、レビューが完了する前に auto-mark.sh が呼ばれます。auto-mark.sh は background 起動を検知してマーカー更新をスキップするため、background 経由ではマーカーが永遠に更新されず commit が通りません。pre-commit-review の文脈では必ず `--wait` を渡してください。単体の `/codex:review` は通常 `AskUserQuestion` で実行方式を確認しますが、本プラグインの `permissionDecisionReason` で明示的に `--wait` を指示することでユーザーへの確認をスキップさせます。

> **ループの意図**: 修正を加えた瞬間、その修正自体は未レビューになります。`/codex:review` の指摘を修正した結果として `/simplify` の対象 (重複・冗長コメント等) が新規発生する可能性も、`/simplify` の修正により `/codex:review` の新規指摘が出る可能性も、いずれもゼロではないため、修正があれば `/simplify` から再度ループします。プラグインは「マーカーのハッシュ = `git commit` 時のステージング差分」だけを検証するため、ループ回数の commit 強制ブロックは行いません。

> **終端の判断 / ループ回数の閾値**: ループ回数による commit 強制ブロックは行いません。ただし `/codex:review --wait` の完了が `LOOP_THRESHOLD` に達した段階で deny メッセージに `/codex:adversarial-review` の案内が追加され、Claude に「実装方針そのものに無理はないか」を再考する選択肢を提示します。これは強制ではなく **追加の選択肢の提示** です。Claude は自身の判断で、表層的な修正を継続するか、adversarial レビューを取得するか、人間判断を仰ぐかを選びます。閾値は `block-pre-commit.sh` 内で 1 か所に集約された定数で、運用経験で調整する余地を残してあります。

> **`/codex:review` と `/codex:rescue` の混同に注意**: 公式 codex プラグインには `/codex:review` (read-only コードレビュー) と `/codex:rescue` (修正・調査を delegate する subagent) の両方があり、用途が完全に別です。本プラグインが要求するのは前者です。Claude が誤って `/codex:rescue` を選ぶケースが報告されているため、運用時はコマンド名を明示的に確認してください。`/codex:review` (および閾値到達時に促される `/codex:adversarial-review`) は frontmatter で `disable-model-invocation: true` が指定されており本来 Skill tool から呼び出せませんが、姉妹プラグイン [codex-review-customize](../codex-review-customize/) を導入してパッチを適用すると Skill tool 経由でも呼び出し可能になります。

#### 2. auto-mark (PostToolUse, matcher: `*` — wildcard)

**ファイル**: `hooks/scripts/auto-mark.sh`

`/simplify` と `/codex:review --wait` の実行完了を PostToolUse hook で自動検知し、対応するマーカーファイルに「現在の staged + unstaged tracked 差分のハッシュ」を書き込みます。`/codex:review --wait` の成功完了時にはループカウンタも +1 します (`/simplify` 側はカウントしません — Skill PostToolUse は launch 時点で発火するため完了 signal としては不正確で、cooperative にカウントが膨らむ経路になるため)。

hooks.json の matcher は `"*"` (wildcard) で、すべての tool 完了時に本フックが呼ばれます。`Skill` matcher の挙動が公式ドキュメント上完全に明記されていないため、tool 名に依存しない構造にしてあります。フィルタリングはスクリプト側の bash 内蔵正規表現マッチが行うため、対象外 tool は subprocess を立てずに即離脱します。

**検知ルール**:

| 検知対象                                 | tool 名 | 判定                                                                                                          | 書き込むマーカー                              | 副作用                  |
| ---------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------- | --------------------------------------------- | ----------------------- |
| `/simplify` skill の launch              | `Skill` | `tool_input.skill == "simplify"`                                                                              | `<git-dir>/.claude-pre-commit-simplified`     | (なし)                  |
| `/codex:review --wait` の Bash 完了      | `Bash`  | コマンドが `^node` で始まる (env-prefix 許容) / `codex-companion.m[jt]s review` を含む / `run_in_background == false` / 失敗・中断ではない | `<git-dir>/.claude-pre-commit-codex-reviewed` | ループカウンタ +1       |

**`/simplify` を launch タイミングで検知する設計上のトレードオフ**:

`Skill` tool の `PostToolUse` は `Launching skill: simplify` を返した瞬間 (= skill body 実行 **前**) に発火します。本プラグインはこの timing でマーカーに **launch 時点の差分ハッシュ** (= simplify が見ることになる state) を書き込みます。

- メリット (loop discipline): simplify body が edits を行えば current hash は launch 時点と異なる値になります。block-pre-commit.sh はこの hash と current hash を比較するため、edit 後は marker stale → DENY となり、Claude は **修正後の state で再度 `/simplify` を呼ぶ** 必要が生じます。これにより「修正後の差分は必ず simplify を再走させる」という loop discipline が構造的に強制されます。
- 既知の限界 (lie attack): Claude が `Skill(simplify)` を呼んでも skill body の meta prompt を実際に実行せず、その後 `/codex:review --wait` を呼んで commit する経路では、両マーカーが launch 時点の hash で揃ってしまい commit が通ってしまいます。これは Claude が instructions を真摯に follow するという信頼を前提とした設計で、構造的には防げません。

**書き込みをスキップする条件**:

- `tool_response.is_error` または `tool_response.interrupted` が `true` (失敗した review 結果でマーカーを書かない / カウンタも増やさない)
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
   → PostToolUse(Bash) で auto-mark.sh が codex-reviewed マーカー書き込み + ループカウンタ +1
6. レビュー結果に指摘があれば修正し、`git add` で再ステージング
   → ステージング差分が変わるため両マーカーが自動的に失効する
7. 4〜6 を Claude が「修正不要」と判断するまで繰り返す
   (途中、ループカウンタが LOOP_THRESHOLD に達した段階で deny メッセージに
    /codex:adversarial-review の案内文が追加される。Claude は表層修正を続けるか
    adversarial レビューを取得するか人間判断を仰ぐかを判断)
8. 双方のマーカーが「✓ 最新の差分でレビュー済み」になったら `git commit` を再試行
   → ハッシュ一致で通過、両マーカーとループカウンタを自動削除
9. (PR 作成後) post-pr-review プラグインが /codex:adversarial-review を促す
```

## 注意事項

- `/codex:review` は **必ず `--wait` を付けて呼び出してください**。`--background` ではマーカーが更新されず commit が通りません。
- `/simplify` skill は本プラグインが期待するのは namespace なしの `simplify` です。`pr-review-toolkit:code-simplifier` 等の別 skill ではマーカーが更新されません。
- マーカー更新後にトラッキング済みファイルを編集すると、ハッシュが一致しなくなり再レビューが必要になります。
- 未トラッキングのファイルはハッシュ計算に含まれません。新規ファイルをコミット対象にする場合は、レビュー前に `git add` でステージングしてから `/simplify` → `/codex:review --wait` を実行してください。
- マーカーとループカウンタは `.git` ディレクトリ配下に保存されるため、リモートには影響しません。
- フックを一時的に無効化したい場合は、Claude Code 側でプラグインを無効にするか、フックの設定を一時的に変更してください。
- ループカウンタの閾値は `block-pre-commit.sh` 内の `LOOP_THRESHOLD` で 1 か所に集約しています。運用経験に応じて値を調整できます。

## ディレクトリ構成

```
pre-commit-review/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       ├── block-pre-commit.sh   (PreToolUse: git commit を検証 + 閾値到達時に adversarial review 案内)
│       ├── auto-mark.sh          (PostToolUse: 実走完了を検知してマーカー更新 + codex 完了時にループカウンタ +1)
│       └── lib/
│           ├── diff-hash.sh      (両者で共有するレビュー差分ハッシュ計算)
│           └── loop-counter.sh   (両者で共有するループカウンタファイル名と読み出しロジック)
└── README.md
```

## 必要な実行環境

- `bash`
- `jq`
- `git`
- `sha256sum` (coreutils)

## 関連プラグイン

- [codex-review-customize](../codex-review-customize/) — `/codex:review` および閾値到達時に促される `/codex:adversarial-review` を Skill tool から起動可能にする setup プラグイン
- [post-pr-review](../post-pr-review/) — PR 作成直後に `/codex:adversarial-review` (実装方針への批判的レビュー) を誘導する姉妹プラグイン

## 関連情報

- [Claude Code Hooks ドキュメント](https://docs.anthropic.com/claude-code/hooks)
