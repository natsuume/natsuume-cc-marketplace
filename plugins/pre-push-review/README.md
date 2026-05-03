# pre-push-review プラグイン

`git push` を実行する前に `/simplify` → `/codex:review --wait --scope branch` を必ず実行させ、未レビューな commit が remote に到達するのを構造的にブロックするプラグインです。`/simplify` はコード変更を伴うため先に走らせ、`/codex:review` はその後の最終形をレビューします。修正により branch 全差分が変わると **2 つのレビューマーカーが自動的に失効** するため、Claude は `/simplify` → `/codex:review` を再走させる以外に push を通す手段がありません (= ループが構造的に強制されます)。Claude が「修正不要」と判断した時点で再レビュー後に push に進みます。Claude が「人間判断を仰ぐべき」と判断した場合のみユーザーへエスカレートします。

ループが一定回数以上続いても収束しない場合、deny メッセージに **`/codex:adversarial-review`** (実装方針・設計選択への批判的レビュー) を促す案内が追加されます。表層レビューだけで収束しないループに対し「採用しているアプローチ自体が妥当か」を問い直す視点を取り入れる動線です。PR 作成直後の adversarial review は姉妹プラグイン [post-pr-review](../post-pr-review/) が誘導します。

## バージョン

v0.1.0 (前身: `pre-commit-review` v0.4.0)

## 前身プラグインからの設計変更

本プラグインは `pre-commit-review` の後継として、レビュー強制の **境界を commit から push に移したもの** です。実体は別プラグインですが、機能と loop discipline は同等で、利用者の操作感は「commit は自由 / push 直前に 1 周ループ」に変わります。

### なぜ push 境界か

pre-commit 境界の課題:

- **commit ごとにループが回る**: N-commit PR では合計 N 回ループが走るため、多 commit PR ほど時間コストが増える
- **意味的に異なる変更が 1 commit に混入**: 初期実装 / `/simplify` edits / `/codex:review` 指摘修正がすべて同じ commit に圧縮され、`git log` / `git blame` / `git bisect` の解像度が失われる
- **中間 commit が残せない**: WIP / 探索 / checkpoint のような目的別 commit が deny によって作成不能
- **長時間 uncommitted 状態が常態化**: deny → 修正 → deny の繰り返しで数十分単位で未保存。ターミナルクラッシュや誤操作で作業損失リスク

push 境界に移すことで:

- **PR 全差分に対して 1 周のループ**: 1-commit PR では同等、多 commit PR で削減 (実測ベースで 40-48% の review 回数削減見込み)
- **意味的単位の commit が許容される**: 実装 / simplify / fix が独立 commit として記録できる。bisect / blame / archaeology が効くようになる
- **WIP commit が自由に重ねられる**: 中間 checkpoint をいつでも保存可能
- **未レビューな commit を remote に到達させない**: PR 作成手段 (gh CLI / Web UI / IDE / API) のいずれを使われても **precondition (remote branch の存在) を破壊** することで構造的に gate できる。「pre-PR matcher で `gh pr create` だけを止める」設計だと人間の Web UI 操作で bypass される問題が解消する

### 境界の選択: なぜ `git push` であって `gh pr create` ではないか

PR 作成側で gate する設計だと、以下の経路が捕捉できません:

- 人間がブラウザの GitHub Web UI で「Create Pull Request」をクリックする
- GitHub Desktop / IDE 拡張 / `gh api` / API スクリプトでの PR 作成
- Claude が `gh pr create` 以外の手段で PR を作る (例: `gh api` 経由)

これらはすべて「remote branch が存在する」ことを precondition にしているため、push 境界で未レビュー commit を remote に到達させなければ、PR 作成手段に関わらず PR が成立しません。**precondition gating** 戦略により攻撃面を 1 箇所 (push) に集約しています。

## 概要

`PreToolUse` フックで `Bash` ツール実行を監視し、`git push` コマンドを検出した場合、 **2 つのレビューマーカー** (`/simplify` と `/codex:review --wait --scope branch` それぞれの実行完了マーカー) が現在の **branch 全差分 + 未コミット差分** のハッシュと一致しなければ `deny` を返して push を阻止します。マーカーは `PostToolUse` フックが各ツールの実走完了を検知して自動的に書き込みます。手動でスクリプトを呼び出す必要はありません。

加えて、`/codex:review --wait --scope branch` が完了するたびに **ループカウンタ** が +1 され、閾値以上になると deny メッセージに `/codex:adversarial-review --wait --scope branch` の実行を促す案内が追加されます (push を追加で block する判定は変えず、案内文のみ追加する設計)。閾値の現在値は `block-pre-push.sh` の `LOOP_THRESHOLD` で定義されており、運用経験で調整できます。push が PreToolUse を通過した時点でカウンタはリセットされます (マーカーは明示削除されず、次の編集で hash が変わるまで残ります)。

## インストール

```bash
claude /install-plugin https://github.com/natsuume/natsuume-cc-marketplace?plugin=pre-push-review
```

## 機能一覧

### Hooks

#### 1. block-pre-push (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-pre-push.sh`

`git push` を含むコマンドを検出した際、現在のブランチ全差分 + 未コミット差分のハッシュと 2 つのレビューマーカーのハッシュを比較し、両方一致しなければ `deny` を返します。加えてループカウンタが閾値以上のときは deny メッセージに `/codex:adversarial-review` の案内文を追加します (push の block / 通過判定自体には影響しない)。

**動作**:

- `git push --dry-run` / `git push -n` (remote ref を更新しない診断 push) は markers の状態に関わらず通す (no-op なので gate 不要)
- 単独実行 (`git push`) と複合コマンド (`xxx && git push ...`, `cd dir && git push ...`) の双方を検出
- `git -C dir push` や `git --git-dir=... push`、`GIT_DIR=... git push` のような target-override 形式も許容 (cooperative 利用前提)
- カレントブランチが default branch (master/main) の場合は本フックでは gate せず、`git-guardrails` の `block-default-branch-push.sh` に委譲 (重複 deny メッセージを避けるため)
- ブランチ全差分 + 未コミット差分が空 (= base と同一) の場合は gate しない (空 push は通す)
- **working tree が dirty (staged または unstaged 変更あり) の場合は markers の状態に関わらず deny**: push される committed 部分とレビューされた working tree の乖離を防ぐため、push 前に commit 完了を要求する
- 双方のマーカーが一致した場合はそのまま push を許容し、ループカウンタをリセット (markers は明示削除しない: PreToolUse は push 成功を確認できないため、 remote rejection / 認証失敗 / ネットワーク失敗時に同じ state での再 push がレビュー必須になる無駄ループを避ける。markers は次の編集で hash が変わったときに自然に失効する)
- ハッシュは `git diff origin/<base>...HEAD` (PR diff) と `git diff --cached`、`git diff` の連結に対して計算するため、未コミットの edit があると markers のハッシュが変わる仕組み。実際の push gate は dirty-tree 検出で行うが、ハッシュ算式に未コミット差分を含めることで「review 後に edit して push」のような経路もマーカー失効で再 review に倒せる
- `deny` 時の `permissionDecisionReason` には、各マーカーの状態 (`未実行` / `失効` / `✓ 最新の差分でレビュー済み`) と次に Claude が行うべき手順、ループ回数 / 閾値が記載される

**残っている deny 制約 (loop discipline 維持に必要な最小防御)**:

- `bash -c "..."` 等の **シェルラッパー** 経由 push は引き続き deny (クォート内のコマンドを本フックの文字列パーサで解析できず、postfix scan も成立しないため)
- 単独の `&` (background) と `|` (pipeline) は deny (並列実行になりマーカー検証完了後に状態が変更される経路になるため)
- `git push` の **後** にシェル区切り文字 (`;`, `&`, `&&`, `||`, `|`) を続ける複合コマンドは deny (1 マーカー = 1 push 保証のため)
- 引用符で囲まれた `git push` 文字列 (`grep "git push" README` など) はテキスト参照とみなしフックは介入しません
- **`git push` の引数に引用符 (`"` / `'`) が含まれる形** は deny (例: `git push origin "other-branch"`)。本フックの parser は引用符付き引数を確実に解析できないため、refspec/オプションチェックを素通りさせる経路を保守的に塞ぐ。引用符なしで `git push origin feat/x` のように渡す運用
- `time git push ...` / `env git push ...` のように本フックが認識していない wrapper を介して push する形式は deny (postfix scan の起点が取れず未レビュー push を素通しさせるリスクを保守的に塞ぐため)
- **`--all` / `--mirror` / `--tags`** は deny (複数参照 / tag 一括 push でマーカー検証対象外のコミットが混入するため)
  - tag を push したい場合は、tag が指す commit を含むブランチを通常通りレビューして push し、別の Bash 呼び出しで `git push origin <tag-name>` のように個別 tag を push する運用
- **現在ブランチと一致しない refspec を明示する形 (`git push origin other-branch` 等)** は deny (本プラグインは現在ブランチの差分でマーカー検証するため、別ブランチを引数指定する形では未レビュー commit が remote に到達する。push したい場合は `git switch` で切り替えてから `git push` する運用)
  - `git push` / `git push origin` / `git push origin HEAD` / `git push -u origin <現在ブランチ名>` は引き続き許容 (いずれも現在ブランチを push する形)
  - `git push origin :branch` (削除、source 空) はローカルレビュー対象外なので許容
  - `git push --delete origin <branch>` / `git push -d origin <branch>` (削除フラグ) は新規 commit を送らないので許容
  - `git push origin <tag-name>` (個別 tag push) は cooperative 前提で許容 (tag が指す commit は通常 push 済みブランチ上にあるはず)
- **working tree が dirty (staged または unstaged 変更あり) のまま push** は deny (push される committed 部分とレビューされた working tree の乖離を防ぐ。`git status` で変更を確認 → `git add` / `git commit` してから再 review → push)
- **`git config push.default=matching` 環境での refspec 省略 push** は deny (`matching` モードでは bare push が複数ブランチを一括送信するため、現在ブランチ以外の未レビュー commit が gate を素通りする。`git push origin HEAD` で明示するか、`git config push.default simple` に変更する運用)
  - 現代の git デフォルト (`simple`, 2014 年以降) では bare push は現在ブランチのみ送るため影響なし。明示的に `matching` を設定している環境のみ deny する

**サポート外 (本プラグインの範囲外で別レイヤーが必要)**:

- 別端末・別 clone から行われる `git push` は Claude Code hook の原理的範囲外で gate できない (本気で塞ぐなら `.git/hooks/pre-push` real git hook を別レイヤーで併設)
- GitHub サーバ側で実施される操作 (Web UI のマージ / rebase 等) も Claude Code hook 範囲外
- **default branch (master/main) 上での push は本プラグイン単独では gate されない**: 本プラグインは `git-guardrails` の `block-default-branch-push.sh` が default branch push を deny する前提で gate を skip する。 `git-guardrails` を併用していない環境では default branch 上の push が review なしで通る経路が残る。 default branch 保護を確実にしたい場合は `git-guardrails` を必ず併用すること
- **個別 tag push が指す commit は cooperative 信頼**: `git push origin <tag-name>` は HAS_REAL_PUSH=0 として gate を skip する。 tag が現在ブランチの commit (= レビュー済) を指している通常運用を前提とする。 tag が別ブランチの未レビュー commit を指す場合、push 経由で remote に到達する経路が残る (= cooperative slip リスク)。 tag は必ずレビュー済 commit に対して作成する運用を厳守すること

> **target-mismatch の構造的解決**: 本プラグインは独自の bash command parser (`lib/cmd-parser.sh`) と target resolver (`lib/target-resolver.sh`) で `cd dir && git push` / `git -C dir push` / `GIT_DIR=path/.git git push` の **実 push target を決定的に解決** し、 解決した target cwd の `.git` 配下に対して markers / hash 比較を行う。 「hook 検証時の cwd と実 push 時の cwd が乖離」する旧来の問題は、 positive list 設計 (実 target を取り出して直接検証) によって構造的に塞がれている。 解析不能な形式 (subshell `(...)`, brace group `{...}`, `bash -c "..."`, `pushd`/`popd`, `export GIT_DIR=...`, `--work-tree=...`, `time` / `env` 等の未対応 wrapper) は **保守的に deny** する (parser が target を確定できないため)。

> **順序の意図**: `/simplify` はコード変更を適用するため先に走らせ、`/codex:review` はその後の最終形を対象にレビューします。逆順だと codex が simplify によって書き換わる前のコードを見ることになり、レビュー結果が陳腐化します。マーカー方式上は順序を強制していませんが、修正後の差分で 2 マーカーを揃えるためには結局両方を走らせる必要があり、無駄を減らすには `/simplify` を先にする運用が合理的です。

> **`--wait` 限定の理由**: `/codex:review --background` だと Bash tool の `run_in_background: true` 起動直後に PostToolUse が発火し、レビューが完了する前に auto-mark.sh が呼ばれます。auto-mark.sh は background 起動を検知してマーカー更新をスキップするため、background 経由ではマーカーが永遠に更新されず push が通りません。pre-push-review の文脈では必ず `--wait` を渡してください。

> **`--scope branch` 限定の理由**: pre-push-review が gate するのは「branch の commit 列 (= PR diff)」の品質保証で、 `--scope working-tree` (staged+unstaged のみレビュー / committed 部分を見ない) や `--scope auto` (dirty 時に working-tree にフォールバック) では PR diff の review 保証として不十分です。auto-mark.sh は `--scope branch` を含む codex 起動のみマーカーを更新します。

> **ループの意図**: 修正を加えた瞬間、その修正自体は未レビューになります。`/codex:review` の指摘を修正した結果として `/simplify` の対象 (重複・冗長コメント等) が新規発生する可能性も、`/simplify` の修正により `/codex:review` の新規指摘が出る可能性も、いずれもゼロではないため、修正があれば `/simplify` から再度ループします。プラグインは「マーカーのハッシュ = `git push` 時の branch 全差分 + 未コミット差分」だけを検証するため、ループ回数の push 強制ブロックは行いません。

> **終端の判断 / ループ回数の閾値**: ループ回数による push 強制ブロックは行いません。ただし `/codex:review --wait --scope branch` の完了が `LOOP_THRESHOLD` に達した段階で deny メッセージに `/codex:adversarial-review` の案内が追加され、Claude に「実装方針そのものに無理はないか」を再考する選択肢を提示します。これは強制ではなく **追加の選択肢の提示** です。Claude は自身の判断で、表層的な修正を継続するか、adversarial レビューを取得するか、人間判断を仰ぐかを選びます。

> **`/codex:review` と `/codex:rescue` の混同に注意**: 公式 codex プラグインには `/codex:review` (read-only コードレビュー) と `/codex:rescue` (修正・調査を delegate する subagent) の両方があり、用途が完全に別です。本プラグインが要求するのは前者です。Claude が誤って `/codex:rescue` を選ぶケースが報告されているため、運用時はコマンド名を明示的に確認してください。`/codex:review` (および閾値到達時に促される `/codex:adversarial-review`) は frontmatter で `disable-model-invocation: true` が指定されており本来 Skill tool から呼び出せませんが、姉妹プラグイン [codex-review-customize](../codex-review-customize/) を導入してパッチを適用すると Skill tool 経由でも呼び出し可能になります。

#### 2. auto-mark (PostToolUse, matcher: `*` — wildcard)

**ファイル**: `hooks/scripts/auto-mark.sh`

`/simplify` と `/codex:review --wait --scope branch` の実行完了を PostToolUse hook で自動検知し、対応するマーカーファイルに「現在の branch 全差分 + 未コミット差分のハッシュ」を書き込みます。`/codex:review --wait --scope branch` の成功完了時にはループカウンタも +1 します (`/simplify` 側はカウントしません — Skill PostToolUse は launch 時点で発火するため完了 signal としては不正確で、cooperative にカウントが膨らむ経路になるため)。

hooks.json の matcher は `"*"` (wildcard) で、すべての tool 完了時に本フックが呼ばれます。`Skill` matcher の挙動が公式ドキュメント上完全に明記されていないため、tool 名に依存しない構造にしてあります。フィルタリングはスクリプト側の bash 内蔵正規表現マッチが行うため、対象外 tool は subprocess を立てずに即離脱します。

**検知ルール**:

| 検知対象                                                | tool 名 | 判定                                                                                                                                                                            | 書き込むマーカー                              | 副作用                  |
| ------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- | ----------------------- |
| `/simplify` skill の launch                             | `Skill` | `tool_input.skill == "simplify"`                                                                                                                                                | `<git-dir>/.claude-pre-push-simplified`       | (なし)                  |
| `/codex:review --wait --scope branch` の Bash 完了      | `Bash`  | コマンドが `^node` で始まる (env-prefix 許容) / `codex-companion.m[jt]s review` を含む / `--scope branch` を含む / `run_in_background == false` / 失敗・中断ではない                | `<git-dir>/.claude-pre-push-codex-reviewed`   | ループカウンタ +1       |

**`/simplify` を launch タイミングで検知する設計上のトレードオフ**:

`Skill` tool の `PostToolUse` は `Launching skill: simplify` を返した瞬間 (= skill body 実行 **前**) に発火します。本プラグインはこの timing でマーカーに **launch 時点の差分ハッシュ** (= simplify が見ることになる state) を書き込みます。

- メリット (loop discipline): simplify body が edits を行えば current hash は launch 時点と異なる値になります。block-pre-push.sh はこの hash と current hash を比較するため、edit 後は marker stale → DENY となり、Claude は **修正後の state で再度 `/simplify` を呼ぶ** 必要が生じます。これにより「修正後の差分は必ず simplify を再走させる」という loop discipline が構造的に強制されます。
- 既知の限界 (lie attack): Claude が `Skill(simplify)` を呼んでも skill body の meta prompt を実際に実行せず、その後 `/codex:review --wait --scope branch` を呼んで push する経路では、両マーカーが launch 時点の hash で揃ってしまい push が通ってしまいます。これは Claude が instructions を真摯に follow するという信頼を前提とした設計で、構造的には防げません。

**書き込みをスキップする条件**:

- `tool_response.is_error` または `tool_response.interrupted` が `true` (失敗した review 結果でマーカーを書かない / カウンタも増やさない)
- `tool_input.run_in_background` が `true` (background 起動は完了タイミングを捉えられないため)
- `tool_input.skill` が `simplify` 以外 (namespace 付き skill は別物として扱う)
- Bash codex 起動でコマンドに `--scope branch` が含まれていない (PR diff レビュー保証として不十分)
- **Bash codex 起動時に working tree が dirty (staged または unstaged 変更あり)** (`/codex:review --scope branch` は committed 部分のみ review するため、dirty 状態で marker を書くと commit 後のハッシュと衝突して未レビュー commit を通す経路ができる。clean なときに review してから marker を書く運用に倒す)
- カレントブランチが default branch (master/main)
- default branch (origin/HEAD) が検出できない (origin が無い等)

## マーカーファイル

すべて `<git-dir>` 配下に配置 (リポジトリ単位で共有、ブランチ単位ではない):

| ファイル | 内容 | 寿命 |
|---|---|---|
| `.claude-pre-push-simplified` | `/simplify` 実行時の branch 全差分ハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-codex-reviewed` | `/codex:review --wait --scope branch` 完了時の branch 全差分ハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-codex-loop-count` | `/codex:review --wait --scope branch` 連続実行回数 | push 通過時にリセット |

## 関連プラグイン

- [post-pr-review](../post-pr-review/): PR 作成直後の adversarial レビュー誘導
- [codex-review-customize](../codex-review-customize/): `/codex:review` と `/codex:adversarial-review` を Skill tool から呼べるようにパッチを適用する setup プラグイン
- [git-guardrails](../git-guardrails/): default branch (master/main) への直接書き込みを deny。本プラグインは default branch 上の push を git-guardrails に委譲します
