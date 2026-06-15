# pre-push-review プラグイン

`git push` を実行する前に **3 レビュー** を必ず実行させ、 未レビューな commit が remote に到達するのを構造的にブロックするプラグインです (`pre-commit-review` の後継)。 3 レビューはすべて subagent 経由で実行されます:

- **`pre-push-review:code-reviewer` subagent** (self-contained correctness バグ検出 / 詳細は [Agents](#agents))
- **`pre-push-review:codex-reviewer` subagent** (codex review wrapper `run-codex-review.sh` を foreground 起動して output を report として返す / 詳細は [Agents](#agents))
- **`pre-push-review:security-reviewer` subagent** (self-contained security review / 詳細は [Agents](#agents))

の 3 軸構成で、 **Anthropic と OpenAI の独立した 2 つのバグレビュー** に security review を重ねた defense-in-depth です。 修正により branch 全差分 + 未コミット差分が変わると 3 マーカーは自動失効し、 Claude は再走させる以外に push を通す手段がありません (= ループが構造的に強制されます)。

> **v3.0.0 で 3 レビューすべてを subagent 経由に統一** (互換破壊あり): v2.x の Skill `/code-review` と Bash 直接起動の codex review wrapper を、 それぞれ `pre-push-review:code-reviewer` / `pre-push-review:codex-reviewer` subagent に置換しました。 設計のメリット:
>
> - **context isolation**: 各レビューの詳細出力 (codex の verdict / findings、 code review の詳細指摘) は subagent context に閉じ込められ、 親 session の context が圧迫されません。 親 session に返るのは markdown report (要約) だけです。
> - **失敗検知の対称性**: 3 軸とも `tool_response.is_error` / `interrupted` の同じ判定経路で silent-pass を防げます (v2.x までは Skill / Bash / Agent で 3 通りの失敗検知ロジックを抱えていました)。
> - **`/pre-push-review:review` slash command を 3 subagent 並列発出に書き換え**: deny メッセージとともに案内されます。 wall-clock は最遅レビュー 1 本の時間で完了します。

## バージョン

v3.0.0 (前身: `pre-commit-review` v0.4.0)

### v2.0.1 → v3.0.0 の変更点 (互換破壊あり)

- **`agents/code-reviewer.md` を新設**: v2.x の Skill `/code-review` (Anthropic bundled skill / read-only correctness バグ検出) に相当する self-contained subagent。 prompt は標準 skill と独立に管理 (security-reviewer と同じ理由: 主 session から直接 skill を呼ぶと turn が終了、 subagent 内から呼んでも nested subagent 制約で sub-task が動かないため)。 tools は `Bash, Read, Glob, Grep, LS` で `Skill` / `Task` を含まない (= 標準 skill を invoke できない構造的防御)。
- **`agents/codex-reviewer.md` を新設**: codex review wrapper (`run-codex-review.sh`) を foreground で 1 回起動するだけの最小 subagent。 tools は `Bash` のみで、 wrapper の output (codex review の verdict / findings) を markdown report として親 session に返す。 wrapper が atomic rename で codex-reviewed marker を書く設計は維持。
- **`commands/review.md` を 3 subagent 並列発出に書き換え**: Skill (`code-review`) + Bash (codex wrapper) + Agent (security-reviewer) の 3 経路混在を、 Agent x 3 (code-reviewer + codex-reviewer + security-reviewer) に統一しました。
- **`auto-mark.sh` の検知ロジックを Skill → Agent に移行**: PRECHECK\_RE と case 文から Skill `code-review` / `security-review` の検知を全廃。 subagent\_type が `code-reviewer` / `security-reviewer` の末尾一致 (namespace 付き / name-only 両許容) のみを検知します。 substring pre-filter は `"subagent_type"` 単独で十分になりました。 codex-reviewer subagent は auto-mark の検知対象外 (= marker は wrapper が書く設計を維持): subagent が wrapper の non-zero exit を観察してから report を返した場合に、 Agent 完了で marker を書くと「失敗した review なのに marker が書かれる」 silent-pass の経路を作るため。
- **`block-pre-push.sh` の deny メッセージを 3 Agent 案内に書き換え**: Skill (`code-review`) と Bash (codex wrapper) の fallback 起動コマンドを Agent x 3 に置換。 wrapper の絶対パス埋め込み (CODEX_WRAPPER_PATH) も削除しました (subagent 経由で起動するため不要)。
- **後方互換 / 移行**: 既存の `.claude-pre-push-code-reviewed` / `.claude-pre-push-codex-reviewed` / `.claude-pre-push-security-reviewed` marker file 名と hash 計算式は不変です。 v2.x で実行済みの marker は v3.0.0 でも hash が一致する限り有効。 v2.x ユーザは v3.0.0 アップグレード後の最初の push で「`/pre-push-review:review` を実行してください」 と案内され、 そこから 3 subagent が走ります。
- **major bump にした理由**: ユーザフロー変更 (Skill / Bash 経路の廃止、 Agent 統一) と auto-mark の検知契約変更 (Skill 検知の全廃) を伴うため major。 marker file 名と hash 計算式は不変なので、 既存 marker は hash 一致時に引き続き有効。

### 過去の変更点

詳細な経緯と過去の version 履歴は git log を参照してください。 代表的なマイルストーン:

- **v2.0.1**: post-v2 cleanup。 README 見出しレベル / `lib/exit-trap.sh` docstring / keywords を整理。 `auto-mark.sh` に substring pre-filter を追加。
- **v2.0.0**: `/pre-push-review:review` slash command 新設 (3 レビュー並列発出の確定的フロー)。 `/simplify` (cleanup-only) マーカー削除し 3 軸 defense-in-depth に純化。 CC version 依存の fail-open 緩和 (`lib/first-party-review.sh`) も削除して 3 マーカー常時必須化。 `lib/codex-companion-resolver.sh` の sort -V fallback を POSIX numeric field sort に置換。
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

#### `/pre-push-review:review` (v2.0.0 で新設 / v3.0.0 で 3 subagent 並列発出に書き換え)

**ファイル**: `commands/review.md`

push 前 3 レビューを **同じアシスタントメッセージで並列に** 3 subagent として起動する確定的フローです。 deny メッセージから案内されたら、 Claude はこのコマンドを実行し、 3 subagent (`pre-push-review:code-reviewer` + `pre-push-review:codex-reviewer` + `pre-push-review:security-reviewer`) を 1 つの assistant message 内で並列 `Agent` / `Task` tool call として発出します。 順序や引数の自律判断は構造的に排除されています。

並列発出が技術的に成立しない / 一部のレビューが失敗した場合は、 3 subagent を順次起動しても push gate の構造的保証は同じ (3 マーカーの hash 一致が成立すれば push 可)。 wall-clock が伸びるだけのトレードオフです。

### Hooks

#### 1. block-pre-push (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-pre-push.sh`

`git push` を含むコマンドを検出した際、 現在のブランチ全差分 + 未コミット差分のハッシュと **3 つのレビューマーカー** (code-reviewer / codex-reviewer / security-reviewer subagent 起因) のハッシュを比較し、 3 マーカーがすべて一致しなければ `deny` を返します。 3 マーカーは v2.0.0 から常にすべて必須 (CC version 依存の fail-open 緩和は廃止)。

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

`pre-push-review:code-reviewer` / `pre-push-review:security-reviewer` subagent の **実行完了** を PostToolUse hook で自動検知し、 対応するマーカーファイルに「現在の branch 全差分 + 未コミット差分のハッシュ」 を書き込みます。 v3.0.0 で Skill `/code-review` / `/security-review` の検知は全廃しました (Skill 経路を廃止し subagent 経由に統一)。 codex review の marker は wrapper script (`run-codex-review.sh`) が完了時に直接書き込む設計で本 hook では扱いません (codex-reviewer subagent も検知対象外。 理由は後述)。

hooks.json の matcher は `"*"` (wildcard) で、 すべての tool 完了時に本フックが呼ばれます。 フィルタリングはスクリプト側の bash 内蔵正規表現マッチが行うため、 対象外 tool は subprocess を立てずに即離脱します。

**検知ルール (v3.0.0)**:

| 検知対象                                                | tool 名 | 判定                                                                                                                                                                            | 書き込むマーカー                              |
| ------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| `pre-push-review:code-reviewer` subagent の完了 | `Agent` / `Task` | `tool_input.subagent_type` が `pre-push-review:code-reviewer` または `code-reviewer` (name-only 形式も許容) | `<git-dir>/.claude-pre-push-code-reviewed`    |
| `pre-push-review:security-reviewer` subagent の完了 | `Agent` / `Task` | `tool_input.subagent_type` が `pre-push-review:security-reviewer` または `security-reviewer` (name-only 形式も許容) | `<git-dir>/.claude-pre-push-security-reviewed` |

**subagent を completion タイミングで検知する理由**:

各 subagent は内部で標準 skill を呼ばずに self-contained でレビューを実行します。 PostToolUse hook が Agent / Task 完了で発火するように倒すことで、 subagent が **実際にレビューを完了させた** ことを確認した上でマーカーを書きます。 subagent が途中で失敗した場合 (`tool_response.is_error` / `interrupted`) はマーカーが書かれないため、 push gate がそのまま deny を返してループが続きます (silent-pass しない設計)。

**codex-reviewer subagent が検知対象外な理由**:

codex-reviewer subagent は wrapper script (`run-codex-review.sh`) を foreground で 1 回起動するだけの実装で、 wrapper 自身が完了時 (exit 0) に codex-reviewed marker を atomic rename で書きます。 もし auto-mark で subagent 完了タイミングにも marker を書く設計にすると、 「wrapper が non-zero exit したのに subagent が status report だけ返して完了した」 ケースで `tool_response.is_error` が `false` のまま auto-mark が marker を書く silent-pass の経路を作るため、 検知しない設計に倒しています (= 「codex review が exit 0 で完了したときだけ marker が書かれる」 を wrapper 内で完結させる)。

**書き込みをスキップする条件**:

- `tool_response.is_error` または `tool_response.interrupted` が `true` (失敗した review 結果でマーカーを書かない)
- `tool_input.subagent_type` が `pre-push-review:code-reviewer` / `code-reviewer` / `pre-push-review:security-reviewer` / `security-reviewer` 以外 (別の subagent 起動はマーカー対象外。 codex-reviewer もここで弾かれる)
- カレントブランチが default branch (master/main)
- default branch (origin/HEAD) が検出できない (origin が無い等)

### マーカーファイル

すべて `<git-dir>` 配下に配置 (リポジトリ単位で共有、 ブランチ単位ではない):

| ファイル | 内容 | 寿命 |
|---|---|---|
| `.claude-pre-push-code-reviewed` | `pre-push-review:code-reviewer` subagent 完了時の branch 全差分ハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-codex-reviewed` | codex review (wrapper script `run-codex-review.sh` 経由 / `pre-push-review:codex-reviewer` subagent が内部起動) 完了時の branch 全差分ハッシュ。 wrapper script 自身が書き込む | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-security-reviewed` | `pre-push-review:security-reviewer` subagent 完了時の branch 全差分ハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |

> **v2.x → v3.0.0 アップグレード時の注意**: v2.x で実行済みの 3 マーカー (code-reviewed / codex-reviewed / security-reviewed) は v3.0.0 でも hash が一致する限り有効です。 marker file 名と hash 計算式は不変なので追加の cleanup は不要です。

### Agents

#### `pre-push-review:code-reviewer` (subagent / v3.0.0 で追加)

**ファイル**: `agents/code-reviewer.md`

branch 全差分に対する correctness バグ検出を **self-contained に** 実行し、 結果のマークダウンレポートを親 session に返す subagent です。 v2.x までの Skill `/code-review` (Anthropic bundled skill / read-only correctness バグ検出) を置換するため v3.0.0 で追加されました。

**動作**:

- tools は `Bash, Read, Glob, Grep, LS` に制限 (Edit / Write / Skill / Task はすべて非許可)。 read-only でファイル改変を防ぎ、 `Skill` を外すことで標準 `/code-review` skill を invoke できないようにしている (理由は security-reviewer と同じ; 下記)。 `Task` を外すのは Claude Code が subagent からの nested subagent 起動を禁止しているため
- subagent body には logic errors / null/undefined / error handling / resource leaks / concurrency / API misuse / data corruption の各カテゴリと exclusion ルール (style / docs / perf / refactor / security / pre-existing bug 等) が prompt として含まれており、 単一 turn で review を完遂する
- 親 session は `Agent` / `Task` tool の result として markdown report を受け取り、 後続フロー (`git push` 等) を継続できる
- PostToolUse hook (auto-mark.sh) は subagent **完了時** に発火する Agent / Task tool 検知ロジックで code-reviewed マーカーを更新する
- model は `inherit` で親 session と同じモデルを使用

#### `pre-push-review:codex-reviewer` (subagent / v3.0.0 で追加)

**ファイル**: `agents/codex-reviewer.md`

codex review wrapper (`hooks/scripts/run-codex-review.sh`) を foreground で 1 回起動し、 wrapper の stdout (codex review の verdict / findings) と stderr (wrapper status) を組み立てた markdown report として親 session に返す最小 subagent です。 v2.x までの Bash 直接起動 (commands/review.md と deny メッセージに wrapper 絶対パスを案内) を置換するため v3.0.0 で追加されました。

**動作**:

- tools は `Bash` のみ (Read / Edit / Write / Skill / Task はすべて非許可)。 wrapper-only な実行サーフェスを構造的に強制
- subagent body は wrapper を `run_in_background: false` で 1 回起動し、 output を markdown report で return する単純な指示のみ
- 親 session は subagent の return として markdown report を受け取る (codex review の詳細出力は subagent context に閉じ込められる)
- codex-reviewed marker は wrapper 自身が exit 0 完了時に atomic rename で書き込む設計を維持。 subagent 完了タイミングでは marker を書かない (silent-pass 防止 / 詳細は auto-mark の節を参照)
- model は `inherit` で親 session と同じモデルを使用

#### `pre-push-review:security-reviewer` (subagent)

**ファイル**: `agents/security-reviewer.md`

branch 全差分に対するセキュリティレビューを **self-contained に** 実行し、 結果のマークダウンレポートを親 session に返す subagent です。 v0.3.0 で追加されました。

**動作**:

- tools は `Bash, Read, Glob, Grep, LS` に制限 (Edit / Write / Skill / Task はすべて非許可)。 read-only でファイル改変を防ぎ、 `Skill` を外すことで標準 `/security-review` skill を invoke できないようにしている (理由は下記)。 `Task` を外すのは Claude Code が subagent からの nested subagent 起動を禁止しているため
- subagent body には input validation / authn-authz / crypto-secrets / injection / data-exposure の各カテゴリと exclusion ルール (DoS / 既存依存 CVE / テストファイル等) が prompt として含まれており、 単一 turn で review を完遂する
- 親 session は `Agent` / `Task` tool の result として markdown report を受け取り、 後続フロー (`git push` 等) を継続できる
- PostToolUse hook (auto-mark.sh) は subagent **完了時** に発火する Agent / Task tool 検知ロジックで security マーカーを更新する (launch 時点ではなく completion で書くことで、 subagent 失敗時の silent-pass を防ぐ)
- model は `inherit` で親 session と同じモデルを使用

#### code-reviewer / security-reviewer subagent が標準 skill を invoke しない理由 (共通)

(1) 主 session の Claude が直接 `/code-review` / `/security-review` を呼ぶと skill prompt 末尾「Your final reply must contain the markdown report and nothing else.」 によって turn が終了し、 後続フロー (`git push`) まで進まない。
(2) subagent 内から invoke しても、 標準 skill 本体は内部で sub-task (Task tool) を spawn する設計だが、 Claude Code は **subagent 内での nested subagent 起動を禁止** している (公式ドキュメント `subagents cannot spawn other subagents`)。 sub-task が動かないため degraded mode で実行される。 v2.x では PostToolUse が Skill launch 時点で発火するためマーカーは書かれてしまい silent-pass の経路があったが、 v3.0.0 で Skill 検知を全廃したのでこの経路は閉じている。
(3) このため subagent は **同等のレビュー内容を self-contained な prompt として持ち**、 標準 skill を invoke しない設計に倒している。 標準 skill の prompt とは別管理になるため、 Anthropic 側の今後の改善は手動で追随する必要がある (トレードオフ)。

**呼び出しタイミング (3 subagent 共通)**: `/pre-push-review:review` slash command の指示で 3 並列 `Agent` / `Task` tool calls として起動する。 deny メッセージにも個別起動のフォールバック手順を案内している。

## 関連プラグイン

- [git-guardrails](../git-guardrails/): default branch (master/main) への直接書き込みを deny。 本プラグインは default branch 上の push を git-guardrails に委譲します
- [codex-review-customize](../codex-review-customize/): `/codex:review` を Skill tool から呼べるようにパッチを適用する setup プラグイン (v1.0.0 までの pre-push-review が Skill 経由の codex review を強制していた時代の補助プラグイン。 **v1.1.0 以降の pre-push-review は wrapper 経路を使うため本観点では不要**。 他用途で `/codex:review` を Skill 経由で使いたい場合は引き続き有用)
- [decompose-bash](../decompose-bash/): Bash コマンドを最小粒度に分解する SessionStart 注入。 本プラグインの PreToolUse hook が `&&` / `||` 等の合成で取りこぼされないよう、 Claude に各コマンドを独立 Bash 呼び出しに分けさせる
