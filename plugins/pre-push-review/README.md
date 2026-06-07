# pre-push-review プラグイン

`git push` を実行する前に **3 レビュー** を必ず実行させ、 未レビューな commit が remote に到達するのを構造的にブロックするプラグインです (`pre-commit-review` の後継)。 3 レビューは:

- **`/code-review`** (Anthropic bundled skill / read-only correctness バグ検出)
- **codex review** (OpenAI バグ検出 / bash wrapper `run-codex-review.sh` 経由 foreground 実行)
- **`pre-push-review:security-reviewer` subagent** (self-contained security review / 詳細は [Agents](#agents))

の 3 軸構成で、 **Anthropic と OpenAI の独立した 2 つのバグレビュー** に security review を重ねた defense-in-depth です。 修正により branch 全差分 + 未コミット差分が変わると 3 マーカーは自動失効し、 Claude は再走させる以外に push を通す手段がありません (= ループが構造的に強制されます)。

> **v2.0.0 で `/pre-push-review:review` slash command を新設**: 3 レビューを **同じアシスタントメッセージで並列に** 起動する確定的フローに切替えました。 これにより:
>
> - **Skill での自律判断ではなく確定的実行**: Claude は「どのレビューを走らせるか / どの順番で / 引数は何か」 を判断しません。 コマンド本文に固定された 3 ツール並列発出のみが正解です。 順序揺れや起動漏れによる無駄ループが構造的に排除されます。
> - **3 レビューの並列実行**: wall-clock は最遅レビュー 1 本の時間で完了します (順次より大幅に高速)。 3 レビューは互いに独立しているため並列化に乗ります。
>
> v1.x の `/simplify` (cleanup-only) マーカーは v2.0.0 で削除しました。 cleanup-only な性質上「edits が無くなるまでループ」 が必要で並列化に乗らず、 cleanup ステップを drop して 3 軸に純化しています。

## バージョン

v2.0.0 (前身: `pre-commit-review` v0.4.0)

### v1.1.0 → v2.0.0 の変更点 (互換破壊あり)

- **`/pre-push-review:review` slash command を新設** (`commands/review.md`): deny メッセージはこの slash command を案内する形に集約され、 Claude は 3 レビューを「同じアシスタントメッセージで並列に」 起動するだけになります。 順序や引数の自律判断は構造的に排除されました。
- **`/simplify` (cleanup-only) マーカーを削除**: 4 マーカー → 3 マーカーに減らしました。 削除理由は (1) cleanup-only な性質上「edits が無くなるまでループ」 が必要で並列化に乗らない、 (2) `/simplify` は CC v2.1.154+ のみで併存する bundled skill で旧 version 帯では存在せず fail-open 緩和の SPOF 回避ロジックが複雑化していた、 (3) cleanup を drop しても bug 検出 (Anthropic) + bug 検出 (OpenAI codex) + security の 3 軸 defense-in-depth は維持される、 の 3 点。
- **`lib/first-party-review.sh` を全削除**: 第一者 (Anthropic) レビュー要件の version 依存ロジック (`pre_push_review_detect_cc_version` / `pre_push_review_require_both_first_party`) は `/simplify` 削除に伴い不要となりました。 3 マーカーは常にすべて必須です (CC version に関わらず)。
- **`block-pre-push.sh` の deny メッセージを刷新**: v1.x の long deny (100+ 行 / 8 段階手順 / version 分岐解説) は `/pre-push-review:review` slash command 案内中心に大幅短縮されました。 codex / security 個別の起動コマンドはフォールバックとして残しています。
- **`auto-mark.sh` から `simplify` skill 検知を削除**: PRECHECK\_RE は `(code-review|security-review)` のみを許容します。
- **`lib/markers.sh` から `SIMPLIFIED_MARKER_NAME` / `simplified_marker_path` を削除**: 3 マーカー定数のみを保持します。
- **`lib/codex-companion-resolver.sh` の sort -V fallback を数値比較ベースに修正 (bug fix)**: v1.x までの `sort -V -r 2>/dev/null || sort -r` chain は GNU `sort -V` が無い環境 (macOS Ventura 以前の BSD sort) で lex 順 fallback に倒れ、 codex 1.10 以降 release 時に古い `1.2.x` を最新と誤判定する silent failure 経路がありました。 v2.0.0 で `_pre_push_review_semver_desc_sort_dirs` ヘルパを導入し、 GNU `sort -V` が失敗した場合に awk で `major.minor.patch` を 6 桁 zero-padding key として lex 順にエンコードする数値比較 fallback に置換しました。
- **後方互換 / 移行**: 既存の `.claude-pre-push-code-reviewed` / `.claude-pre-push-codex-reviewed` / `.claude-pre-push-security-reviewed` marker file 名は変更なし。 v1.x で実行済みの marker は v2.0.0 でも hash が一致する限り有効。 古い `.claude-pre-push-simplified` ファイルが disk に残っても v2.0.0 のコードからは参照されないため無害 (気になる場合は `rm <git-dir>/.claude-pre-push-simplified` で手動削除可)。 v1.x ユーザは v2.0.0 アップグレード後に最初の push で「`/pre-push-review:review` を実行してください」 と案内され、 そこから 3 レビューが走ります。
- **major bump にした理由**: マーカー数の変更 (4→3) と deny メッセージ案内の構造変化 (slash command 経由化) はユーザフロー変更を伴うため major。 marker file 名と hash 計算式は不変なので、 既存 marker は hash 一致時に引き続き有効。

### 過去の変更点

詳細な経緯と過去の version 履歴は git log を参照してください。 v1.x までの代表的なマイルストーン:

- **v1.1.0**: codex review を `/codex:review` slash command 経由から bash wrapper (`run-codex-review.sh`) 経由に切替え、 bg 起動による silent failure 経路を構造排除。 wrapper を `run_in_background: true` / shell-level `&` `|` で起動する経路は `block-bg-codex-wrapper.sh` が deny。
- **v1.0.0**: Claude Code の bundled skill 分岐 (v2.1.147 で `/code-review` が read-only バグ検出器に分離、 v2.1.154 で `/simplify` が cleanup-only として再導入) に追随し、 push gate を 3 → 4 マーカーに拡張。 第一者 (Anthropic) レビューを CC version 依存の fail-open 緩和で実装。
- **v0.x**: pre-commit 境界から push 境界への移行、 redirection / pipeline / wrapper / target-override などの parser 強化、 macOS bash 3.2 互換、 EXIT trap による silent failure 可視化、 tag reachability check 等。

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install pre-push-review@natsuume-plugins
```

公式 codex プラグインへの依存があるため、 codex review wrapper を動作させるには次も install してください:

```bash
claude plugin install codex@openai-codex
```

## 機能一覧

### Commands

#### `/pre-push-review:review` (v2.0.0 で新設)

**ファイル**: `commands/review.md`

push 前 3 レビューを **同じアシスタントメッセージで並列に** 起動する確定的フローです。 deny メッセージから案内されたら、 Claude はこのコマンドを実行し、 3 レビュー (code-review skill + security-reviewer subagent + codex review wrapper) を 1 つの assistant message 内で並列 tool call として発出します。 順序や引数の自律判断は構造的に排除されています。

並列発出が技術的に成立しない / 一部のレビューが失敗した場合は、 3 ツールを順次起動しても push gate の構造的保証は同じ (3 マーカーの hash 一致が成立すれば push 可)。 wall-clock が伸びるだけのトレードオフです。

### Hooks

#### 1. block-pre-push (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-pre-push.sh`

`git push` を含むコマンドを検出した際、 現在のブランチ全差分 + 未コミット差分のハッシュと **3 つのレビューマーカー** (`/code-review` / codex review / security) のハッシュを比較し、 3 マーカーがすべて一致しなければ `deny` を返します。 3 マーカーは v2.0.0 から常にすべて必須 (CC version 依存の fail-open 緩和は廃止)。

**動作**:

- `git push --dry-run` / `git push -n` (remote ref を更新しない診断 push) は markers の状態に関わらず通す (no-op なので gate 不要)
- 単独実行 (`git push`) と複合コマンド (`xxx && git push ...`, `cd dir && git push ...`) の双方を検出
- `git -C dir push` や `git --git-dir=... push`、 `GIT_DIR=... git push` のような target-override 形式も許容 (cooperative 利用前提)
- カレントブランチが default branch (master/main) の場合は本フックでは gate せず、 `git-guardrails` の `block-default-branch-push.sh` に委譲 (重複 deny メッセージを避けるため)
- ブランチ全差分 + 未コミット差分が空 (= base と同一) の場合は gate しない (空 push は通す)
- **working tree が dirty (staged または unstaged 変更あり) の場合は markers の状態に関わらず deny**: push される committed 部分とレビューされた working tree の乖離を防ぐため、 push 前に commit 完了を要求する
- 3 マーカーがすべて一致した場合はそのまま push を許容する (markers は明示削除しない: PreToolUse は push 成功を確認できないため、 remote rejection / 認証失敗 / ネットワーク失敗時に同じ state での再 push がレビュー必須になる無駄ループを避ける。 markers は次の編集で hash が変わったときに自然に失効する)
- ハッシュは `git diff origin/<base>...HEAD` (PR diff) と `git diff --cached`、 `git diff` の連結に対して計算するため、 未コミットの edit があると markers のハッシュが変わる仕組み。 実際の push gate は dirty-tree 検出で行うが、 ハッシュ算式に未コミット差分を含めることで「review 後に edit して push」 のような経路もマーカー失効で再 review に倒せる
- `deny` 時の `permissionDecisionReason` には、 各マーカーの状態 (`未実行` / `失効` / `✓ 最新の差分でレビュー済み`) と `/pre-push-review:review` slash command の案内が記載される

**残っている deny 制約 (loop discipline 維持に必要な最小防御)**:

- `bash -c "..."` 等の **シェルラッパー** 経由 push は引き続き deny (クォート内のコマンドを本フックの文字列パーサで解析できず、 postfix scan も成立しないため)
- 単独の `&` (background) と `|` (pipeline) は deny (並列実行になりマーカー検証完了後に状態が変更される経路になるため)
- `git push` の **後** にシェル区切り文字 (`;`, `&`, `&&`, `||`, `|`) を続ける複合コマンドは deny (1 マーカー = 1 push 保証のため)
- 引用符で囲まれた `git push` 文字列 (`grep "git push" README` など) はテキスト参照とみなしフックは介入しません
- **`git push` の引数に引用符 (`"` / `'`) が含まれる形** は deny (例: `git push origin "other-branch"`)。 本フックの parser は引用符付き引数を確実に解析できないため、 refspec/オプションチェックを素通りさせる経路を保守的に塞ぐ
- `time git push ...` / `env git push ...` のように本フックが認識していない wrapper を介して push する形式は deny
- **`--all` / `--mirror` / `--tags`** は deny (複数参照 / tag 一括 push でマーカー検証対象外のコミットが混入するため)
  - tag を push したい場合は、 tag が指す commit を含むブランチを通常通りレビューして push し、 別の Bash 呼び出しで `git push origin <tag-name>` のように個別 tag を push する運用
- **現在ブランチと一致しない refspec を明示する形 (`git push origin other-branch` 等)** は deny
  - `git push` / `git push origin` / `git push origin HEAD` / `git push -u origin <現在ブランチ名>` は引き続き許容
  - `git push origin :branch` (削除、 source 空) はローカルレビュー対象外なので許容
  - `git push --delete origin <branch>` / `git push -d origin <branch>` (削除フラグ) は新規 commit を送らないので許容
  - `git push origin <tag-name>` (個別 tag push) は 2 段階の reachability check で扱う
- **working tree が dirty のまま push** は deny
- **`git config push.default=matching` 環境での refspec 省略 push** は deny

**サポート外 (本プラグインの範囲外で別レイヤーが必要)**:

- 別端末・別 clone から行われる `git push` は Claude Code hook の原理的範囲外で gate できない (本気で塞ぐなら `.git/hooks/pre-push` real git hook を別レイヤーで併設)
- GitHub サーバ側で実施される操作 (Web UI のマージ / rebase 等) も Claude Code hook 範囲外
- **default branch (master/main) 上での push は本プラグイン単独では gate されない**: 本プラグインは `git-guardrails` の `block-default-branch-push.sh` が default branch push を deny する前提で gate を skip する。 `git-guardrails` を併用していない環境では default branch 上の push が review なしで通る経路が残る

> **target-mismatch の構造的解決**: 本プラグインは独自の bash command parser (`lib/cmd-parser.sh`) と target resolver (`lib/target-resolver.sh`) で `cd dir && git push` / `git -C dir push` / `GIT_DIR=path/.git git push` の **実 push target を決定的に解決** し、 解決した target cwd の `.git` 配下に対して markers / hash 比較を行います。 解析不能な形式 (subshell `(...)`, brace group `{...}`, `bash -c "..."`, `pushd`/`popd`, `export GIT_DIR=...`, `--work-tree=...`, `time` / `env` 等の未対応 wrapper) は **保守的に deny** します。

#### 2. block-bg-codex-wrapper (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-bg-codex-wrapper.sh`

`run-codex-review.sh` wrapper を background で起動しようとする操作を deny します。 検知対象は次の 2 経路:

- Bash tool option `run_in_background: true` で wrapper を起動
- shell-level の `&` (background) や `|` (pipeline) で wrapper を連結

理由: wrapper 自身は foreground で codex review を実行して marker を書きますが、 上記経路で起動すると **主 Claude session は wrapper の stdout / stderr (= codex review の verdict / findings) を観察しません**。 主 session は marker の存在だけで push gate を通過するため、 review 指摘が修正されないまま push が成立する **foreground review 要件の regression** になります。 v2.0.0 でも v1.1.0 と同じ regression 防御を継続しています。

#### 3. auto-mark (PostToolUse, matcher: `*` — wildcard)

**ファイル**: `hooks/scripts/auto-mark.sh`

`/code-review` skill / `pre-push-review:security-reviewer` subagent / `/security-review` 標準 skill (後方互換) の実行完了を PostToolUse hook で自動検知し、 対応するマーカーファイルに「現在の branch 全差分 + 未コミット差分のハッシュ」 を書き込みます。 v2.0.0 で `/simplify` の検知ケースは削除しました。 codex review の marker は wrapper script (`run-codex-review.sh`) が完了時に直接書き込む設計で本 hook では扱いません。

hooks.json の matcher は `"*"` (wildcard) で、 すべての tool 完了時に本フックが呼ばれます。 フィルタリングはスクリプト側の bash 内蔵正規表現マッチが行うため、 対象外 tool は subprocess を立てずに即離脱します。

**検知ルール (v2.0.0)**:

| 検知対象                                                | tool 名 | 判定                                                                                                                                                                            | 書き込むマーカー                              |
| ------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| `/code-review` skill の launch (read-only バグ検出) | `Skill` | `tool_input.skill == "code-review"` | `<git-dir>/.claude-pre-push-code-reviewed`    |
| `pre-push-review:security-reviewer` subagent の完了 (推奨) | `Agent` / `Task` | `tool_input.subagent_type` が `pre-push-review:security-reviewer` または `security-reviewer` (name-only 形式も許容) | `<git-dir>/.claude-pre-push-security-reviewed` |
| `/security-review` skill の launch (後方互換)        | `Skill` | `tool_input.skill == "security-review"` (主 session 直接呼び出しのみ。 subagent は tools から Skill を外しているため呼べない) | `<git-dir>/.claude-pre-push-security-reviewed` |

**skill を launch タイミングで検知する設計上のトレードオフ**:

`Skill` tool の `PostToolUse` は `Launching skill: <name>` を返した瞬間 (= skill body 実行 **前**) に発火します。 本プラグインはこの timing でマーカーに **launch 時点の差分ハッシュ** を書き込みます。 修正で差分が変わると marker stale → DENY となり、 Claude は再走を要求されます。 既知の限界: Claude が `Skill(code-review)` を呼んでも skill body の meta prompt を実際に実行せず、 その後 push する経路では、 マーカーが launch 時点の hash で揃ってしまい push が通ってしまいます。 これは Claude が instructions を真摯に follow するという信頼を前提とした設計で、 構造的には防げません。

**security-reviewer subagent を completion タイミングで検知する理由**:

subagent は内部で `/security-review` 標準 skill を呼ばずに self-contained でレビューを実行します。 PostToolUse hook が Skill launch ではなく Task 完了で発火するように倒すことで、 subagent が **実際にレビューを完了させた** ことを確認した上でマーカーを書きます。 subagent が途中で失敗した場合 (`tool_response.is_error` / `interrupted`) はマーカーが書かれないため、 push gate がそのまま deny を返してループが続きます (silent-pass しない設計)。

**書き込みをスキップする条件**:

- `tool_response.is_error` または `tool_response.interrupted` が `true` (失敗した review 結果でマーカーを書かない)
- `tool_input.skill` が `code-review` / `security-review` 以外 (namespace 付き skill `code-review:code-review` 等は別物として扱う)
- `tool_input.subagent_type` が `pre-push-review:security-reviewer` / `security-reviewer` 以外 (別の subagent 起動はマーカー対象外)
- カレントブランチが default branch (master/main)
- default branch (origin/HEAD) が検出できない (origin が無い等)

### マーカーファイル

すべて `<git-dir>` 配下に配置 (リポジトリ単位で共有、 ブランチ単位ではない):

| ファイル | 内容 | 寿命 |
|---|---|---|
| `.claude-pre-push-code-reviewed` | `/code-review` (Anthropic read-only バグ検出) 実行時の branch 全差分ハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-codex-reviewed` | codex review (wrapper script `run-codex-review.sh` 経由) 完了時の branch 全差分ハッシュ。 wrapper script 自身が書き込む | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-security-reviewed` | `pre-push-review:security-reviewer` subagent 完了時の branch 全差分ハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |

> **v1.x → v2.0.0 アップグレード時の注意**: `.claude-pre-push-simplified` ファイルが残っている環境では、 v2.0.0 のコードからは参照されないため無害です (気になる場合は `rm <git-dir>/.claude-pre-push-simplified` で手動削除可)。 v1.x で実行済みの 3 マーカー (code-reviewed / codex-reviewed / security-reviewed) は v2.0.0 でも hash が一致する限り有効です。

### Agents

#### `pre-push-review:security-reviewer` (subagent)

**ファイル**: `agents/security-reviewer.md`

branch 全差分に対するセキュリティレビューを **self-contained に** 実行し、 結果のマークダウンレポートを親 session に返す subagent です。 v0.3.0 で追加されました。

**動作**:

- tools は `Bash, Read, Glob, Grep, LS` に制限 (Edit / Write / Skill / Task はすべて非許可)。 read-only でファイル改変を防ぎ、 `Skill` を外すことで標準 `/security-review` skill を invoke できないようにしている (理由は下記)。 `Task` を外すのは Claude Code が subagent からの nested subagent 起動を禁止しているため
- subagent body には input validation / authn-authz / crypto-secrets / injection / data-exposure の各カテゴリと exclusion ルール (DoS / 既存依存 CVE / テストファイル等) が prompt として含まれており、 単一 turn で review を完遂する
- 親 session は `Agent` / `Task` tool の result として markdown report を受け取り、 後続フロー (`git push` 等) を継続できる
- PostToolUse hook (auto-mark.sh) は subagent **完了時** に発火する Agent / Task tool 検知ロジックで security マーカーを更新する (launch 時点ではなく completion で書くことで、 subagent 失敗時の silent-pass を防ぐ)
- model は `inherit` で親 session と同じモデルを使用

**標準 `/security-review` skill を invoke しない理由**:

(1) 主 session の Claude が直接呼ぶと skill prompt 末尾「Your final reply must contain the markdown report and nothing else.」 によって turn が終了し、 後続フロー (`git push`) まで進まない。
(2) subagent 内から invoke しても、 標準 skill 本体は内部で sub-task (Task tool) を spawn する設計だが、 Claude Code は **subagent 内での nested subagent 起動を禁止** している (公式ドキュメント `subagents cannot spawn other subagents`)。 sub-task が動かないため degraded mode で実行されるが、 PostToolUse は Skill launch 時点で発火するためマーカーは書かれてしまい、 silent-pass の経路ができる。
(3) このため subagent は **同等のレビュー内容を self-contained な prompt として持ち**、 標準 skill を invoke しない設計に倒している。 標準 skill の prompt とは別管理になるため、 Anthropic 側の今後の改善は手動で追随する必要がある (トレードオフ)

**呼び出しタイミング**: `/pre-push-review:review` slash command の指示で 3 並列 tool calls の 1 つとして起動する。 deny メッセージにも個別起動のフォールバック手順を案内している。

## 関連プラグイン

- [git-guardrails](../git-guardrails/): default branch (master/main) への直接書き込みを deny。 本プラグインは default branch 上の push を git-guardrails に委譲します
- [codex-review-customize](../codex-review-customize/): `/codex:review` を Skill tool から呼べるようにパッチを適用する setup プラグイン (v1.0.0 までの pre-push-review が Skill 経由の codex review を強制していた時代の補助プラグイン。 **v1.1.0 以降の pre-push-review は wrapper 経路を使うため本観点では不要**。 他用途で `/codex:review` を Skill 経由で使いたい場合は引き続き有用)
- [decompose-bash](../decompose-bash/): Bash コマンドを最小粒度に分解する SessionStart 注入。 本プラグインの PreToolUse hook が `&&` / `||` 等の合成で取りこぼされないよう、 Claude に各コマンドを独立 Bash 呼び出しに分けさせる
