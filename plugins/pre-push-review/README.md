# pre-push-review プラグイン

`git push` を実行する前に **3 レビュー** を必ず実行させ、 未レビューな commit が remote に到達するのを構造的にブロックするプラグインです (`pre-commit-review` の後継)。 3 レビューはすべて subagent 経由で実行されます:

- **`pre-push-review:code-reviewer` subagent** (self-contained correctness バグ検出 / 詳細は [Agents](#agents))
- **`pre-push-review:codex-reviewer` subagent** (codex review wrapper `run-codex-review.sh` を foreground 起動し、結果を parent-safe report に抽象化 / 詳細は [Agents](#agents))
- **`pre-push-review:security-reviewer` subagent** (self-contained security review / 詳細は [Agents](#agents))

の 3 軸構成で、 **Anthropic と OpenAI の独立した 2 つのバグレビュー** に security review を重ねた defense-in-depth です。 修正や commit 列の変更 (add→revert / amend / rebase 含む) により hash が変わると 3 マーカーは自動失効し、 Claude は再走させる以外に push を通す手段がありません (= ループが構造的に強制されます)。

> **v3.0.0 で 3 レビューすべてを subagent 経由に統一** (互換破壊あり): v2.x の Skill `/code-review` と Bash 直接起動の codex review wrapper を、 それぞれ `pre-push-review:code-reviewer` / `pre-push-review:codex-reviewer` subagent に置換しました。 設計のメリット:
>
> - **context isolation**: reviewer は raw stdout / stderr、実行可能な command、具体的な再現手順を subagent context に留め、親 session には severity / location / impact / verification / fix direction / disposition を保持した parent-safe report だけを返します。これは agent prompt と contract test で固定する **instruction contract** であり、auto-mark が report 本文を機械検査して情報流出を遮断する **hard security boundary** ではありません。
> - **起動・marker 発行経路の単一化**: 親 session は 3 軸とも同じ `Agent` / `Task` tool で起動し、3 marker とも auto-mark.sh が SubagentStart の launch attestation と SubagentStop での parent-safe report 検証を経て発行します。Codex wrapper は review 開始時点の hash を pending attestation に束縛し、auto-mark が report 成功後に final marker へ昇格します。
> - **`/pre-push-review:review` slash command を 3 subagent 並列発出に書き換え**: deny メッセージとともに案内されます。 wall-clock は最遅レビュー 1 本の時間で完了します。

Linked worktree では marker、launch attestation、tombstone を main `.git` 直下ではなく、`git rev-parse --absolute-git-dir` が返す worktree 専用 git-dir (`.git/worktrees/<name>/`) に保存します。`block-pre-push.sh` の deny メッセージは実際の marker storage を表示するため、main `.git` の同名ファイルを見てレビュー状態を判断しないでください。

## バージョン

v5.3.3

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install pre-push-review@natsuume-plugins
```

公式 codex プラグインへの依存があるため、 codex review wrapper を動作させるには次も install してください:

```bash
claude plugin install codex@openai-codex
```

### 依存コマンド

`jq` は push gate の必須依存です。`jq` が見つからない環境では、未レビューの push を通さないため `block-pre-push.sh` が `git push` を fail-closed に deny し、インストール後の再実行を案内します。push と無関係な Bash 呼び出しは影響を受けません。

現行の Codex runtime では安全な reviewer identity 契約を構築できないため、本プラグインは Claude Code 専用で、Codex marketplace では配布していません。

## 機能一覧

### Commands

#### `/pre-push-review:review` (v2.0.0 で新設 / v3.0.0 で 3 subagent 並列発出に書き換え)

**ファイル**: `commands/review.md`

push 前 3 レビューを **同じアシスタントメッセージで並列に** 3 subagent として起動する確定的フローです。 deny メッセージから案内されたら、 Claude はこのコマンドを実行し、 3 subagent (`pre-push-review:code-reviewer` + `pre-push-review:codex-reviewer` + `pre-push-review:security-reviewer`) を 1 つの assistant message 内で並列 `Agent` / `Task` tool call として発出します。 順序や引数の自律判断は構造的に排除されています。

並列発出が技術的に成立しない / 一部のレビューが失敗した場合は、 3 subagent を順次起動しても push gate の構造的保証は同じ (3 マーカーの hash 一致が成立すれば push 可)。 wall-clock が伸びるだけのトレードオフです。

一部の marker のみ「未実行」 / 「失効」 の場合は、 該当 subagent だけを Agent / Task tool で単独再起動するのが正規経路です (v4.0.0 で正規化。 block-pre-push.sh の deny メッセージも同じ案内をします。 完了は SubagentStop で検知されるため起動 mode は問いません)。 3 subagent 並列発出が既定であることは変わりません。

### Hooks

#### 1. block-pre-push (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-pre-push.sh`

`git push` を含むコマンドを検出した際、 commit 列 (HEAD / merge-base の OID) + ブランチ全差分 + 未コミット差分のハッシュと **3 つのレビューマーカー** (code-reviewer / codex-reviewer / security-reviewer subagent 起因) のハッシュを比較し、 3 マーカーがすべて一致しなければ `deny` を返します。 3 マーカーは v2.0.0 から常にすべて必須 (CC version 依存の fail-open 緩和は廃止)。

**動作**:

- `git push --dry-run` / `git push -n` (remote ref を更新しない診断 push) は markers の状態に関わらず通す (no-op なので gate 不要)
- 単独実行 (`git push`) と複合コマンド (`xxx && git push ...`, `cd dir && git push ...`) の双方を検出
- `git -C dir push` や `git --git-dir=... push`、 `GIT_DIR=... git push` のような target-override 形式も許容 (cooperative 利用前提)
- カレントブランチが default branch (master/main) の場合は本フックでは gate せず、 `git-guardrails` の `block-default-branch-push.sh` に委譲 (重複 deny メッセージを避けるため)
- merge-base と HEAD の tree が一致し、 merge commit を含まず、 範囲内の全 commit の tree が HEAD tree と一致し、 かつ index / worktree が clean な場合は gate しない (空 push は通す。 tree OID ベースの判定)
- **working tree が dirty (staged または unstaged 変更あり) の場合は markers の状態に関わらず deny**: push される committed 部分とレビューされた working tree の乖離を防ぐため、 push 前に commit 完了を要求する
- 3 マーカーがすべて一致した場合はそのまま push を許容する (markers は明示削除しない: PreToolUse は push 成功を確認できないため、 remote rejection / 認証失敗 / ネットワーク失敗時に同じ state での再 push がレビュー必須になる無駄ループを避ける。 markers は次の編集で hash が変わったときに自然に失効する)
- ハッシュは `head <HEAD の commit OID>` 行 + `mbase <merge-base の commit OID>` 行 + `git diff <merge-base> HEAD` + `git diff --cached` + `git diff` (diff 3 種はいずれも `--no-ext-diff --no-textconv` 付き) を連結した入力に対する sha256 として計算する。 HEAD の commit OID をハッシュ入力に束縛したことで、 レビュー後に commit A を積んでから revert して戻す (net diff は review 時と同一でも commit 列は変わっている) 操作でもマーカーが自動失効するようになった (issue #126 の修正: 従来は branch 全差分 + 未コミット差分のみで計算していたため、 net diff が戻ると失効しているべきマーカーが復活してしまっていた)。 未コミットの edit があると `git diff --cached` / `git diff` の内容が変わりハッシュも変わる点は従来どおりで、 markers が失効し commit + 再 review を強制できる
- `deny` 時の `permissionDecisionReason` には、 各マーカーの状態 (`未実行` / `失効` / `✓ 最新の差分でレビュー済み`) と `/pre-push-review:review` を記載する

**残っている deny 制約 (loop discipline 維持に必要な最小防御)**:

- `bash -c "..."`、stdin / interactive / init file を使う **シェルラッパー** 経由 push は引き続き deny。通常の positional script path や引数に `push` が含まれるだけなら介入しない
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

- **意図的に command token を難読化した push / wrapper 起動**は cooperative 利用前提の範囲外 (#134)。本 plugin は Claude が通常生成する direct command の誤操作を防ぐ review gate であり、任意の shell 入力を解析・封じ込める security sandbox ではない。bash 実行時には通常形と等価でも、次の形は粗フィルタまたは token 比較より前で対象 command として認識されず、gate が介入しない:
  - command keyword 内の quote fragment / escape (`git pu"sh"` / `g\it push` / `$'git' push`)
  - command line 内で注入した git alias (`git -c alias.p=push p`)。hook は実行時の git config / alias を展開しない
  - Codex wrapper file 名を quote fragment で分断する形 (`bash run-cod"e"x-review.sh &`)。wrapper gate の対象判定は実 command line に連続した `run-codex-review.sh` を要求する
  これらは「引用符で囲まれた `git push` 例文をテキスト参照として介入しない」仕様や、認識済み `git push` の未知 wrapper / quote 付き引数を保守的に deny する仕様とは別の境界である
- 別端末・別 clone から行われる `git push` は Claude Code hook の原理的範囲外で gate できない (本気で塞ぐなら `.git/hooks/pre-push` real git hook を別レイヤーで併設)
- GitHub サーバ側で実施される操作 (Web UI のマージ / rebase 等) も Claude Code hook 範囲外
- **default branch (master/main) 上での push は本プラグイン単独では gate されない**: 本プラグインは `git-guardrails` の `block-default-branch-push.sh` が default branch push を deny する前提で gate を skip する。 `git-guardrails` を併用していない環境では default branch 上の push が review なしで通る経路が残る

> **target-mismatch の構造的解決**: 本プラグインは独自の bash command parser (`lib/cmd-parser.sh`) と target resolver (`lib/target-resolver.sh`) で `cd dir && git push` / `git -C dir push` / `GIT_DIR=path/.git git push` の **実 push target を決定的に解決** し、解決した target cwd の `.git` (`git rev-parse --git-dir`) に対して markers / hash 比較を行います。解析不能な形式 (subshell `(...)`, brace group `{...}`, `bash -c "..."`, `pushd`/`popd`, `export GIT_DIR=...`, `--work-tree=...`, `time` / `env` 等の未対応 wrapper) は **保守的に deny** します。

#### 2. block-bg-codex-wrapper (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-bg-codex-wrapper.sh`

`run-codex-review.sh` wrapper の起動を検証する PreToolUse hook です。 **v4.0.0 で agent_type 検証 gate を追加** (issue #267): wrapper を含む Bash 実行の hook payload トップレベル `agent_type` が `pre-push-review:codex-reviewer` (namespace 付き完全一致) でなければ **fail-closed に deny** します。 `agent_type` 欠落はメインセッションからの直接実行、 または `agent_type` を hook payload に含めない旧 Claude Code を意味します。 本 gate は **Claude Code 2.1.211 で実機検証済み** (= 動作要件の検証済み最低 version) で、 それより古く `agent_type` を送らない Claude Code では、 正規フロー (`/pre-push-review:review` や codex-reviewer subagent 経由起動) からの wrapper 起動も deny されるため、 2.1.211 以上への更新が必要です。

**gate の発火対象は実行形コマンドのみ**: command が `run-codex-review.sh` の substring を含んでいても、 wrapper を実行せず言及するだけの read-only 検査コマンド (`cat` / `grep` / `git diff` 等) は agent_type gate を skip して許可します。 **v5.3.1 で分類方式を read-only allowlist 方式から 14-step の順序付き決定表による executable 位置分類へ転換しました** (issue #339)。

実行形として gate するのは次の形です: コマンド置換等の indirection (`$(...)` / バッククォート / `<(...)` / `>(...)`)、 解析不能な segment / token (複数コマンドの merge、 複数 shell word の merge、 ANSI-C quoting、 静的に決定できない展開を含む token)、 segment 先頭の `NAME=VALUE` 代入 slot (値によらず。 `GIT_EXTERNAL_DIFF` / `RIPGREP_CONFIG_PATH` / `LESSOPEN` 等の間接実行面を変数名の列挙なしで塞ぐため)、 bash keyword / shell builtin の静的 superset、 head の basename が wrapper 名または shell interpreter (`bash` / `sh` / `dash` / `zsh` / `ksh`) に一致する形、 launcher の path 修飾形、 script / 対話内実行面を持つコマンド (`sed` / `awk` / `xargs` / `less` / `more` / `parallel`)、 危険 option (`find` の `-exec` 系 / `rg` の `--pre` 系・`--hostname-bin` 系 / `sort` の `--compress-program` 系 / `git` の `--ext-diff`・`--textconv`)。

**allow 側 (mention 候補) は 3 つだけです**: 無害 builtin (`echo` / `true` / `false` / `pwd` / `type`)、 `git` の縮小 subcommand 特例 (`diff` / `log` / `show` / `status` / `ls-files` / `rev-parse` / `cat-file`)、 および **外部コマンド形の不明 head による引数・quoted 文字列としての参照**です。 最後の 1 経路だけが fail-open であり (issue #339 で確定した意図的転換)、 それ以外は従来どおり fail-closed を維持します。 したがって未知の launcher (`frobnicate <wrapper>` 等) に対する完全性は保証しません。 **本 hook は cooperative 利用前提の補助 gate であり、 真の push gate は fail-closed の marker hash 検証を行う `block-pre-push.sh` です。**

コマンド置換等の間接実行を含む場合は、 `&` / `|` が wrapper 呼び出しに隣接していなくても位置を問わず deny します。 分類の正本は `tests/test_pre_push_bg_codex_wrapper.py` の `BlockBgCodexWrapperExecPositionClassificationTest` docstring にある 14-step 決定表で、 各 step の根拠と受容境界は `block-bg-codex-wrapper.sh` のファイルヘッダ「検知ロジック」節を参照してください。

agent_type gate を通過した後は、 従来どおり次の 2 経路の background 起動を検知します:

- Bash tool option `run_in_background: true` で wrapper を起動
- shell-level の `&` (background) や `|` (pipeline) で wrapper を連結

理由: 上記経路で起動すると **codex-reviewer subagent (ひいては親 session) は wrapper の stdout / stderr (= codex review の verdict / findings) を観察しない / 途中でしか観察しない** ため、正しい parent-safe report を組み立てられません。v4.0.1 以降は pending attestation があっても report 成功前に final marker へ昇格しないため push gate bypass にはなりませんが、無駄な review cycle と不正 report を防ぐため foreground を引き続き強制します。 jq 不在等の環境失敗時は本 hook 全体として fail-open に倒れますが、 agent_type gate 自体は fail-closed です。

#### 3. auto-mark (SubagentStart / SubagentStop, matcher: `^pre-push-review:(code|codex|security)-reviewer$`)

**ファイル**: `hooks/scripts/auto-mark.sh`

3 reviewer subagent の **実行完了** を subagent lifecycle hook (SubagentStart / SubagentStop) で自動検知し、対応するマーカーファイルに「commit 列 (HEAD / merge-base の OID) + branch 全差分 + 未コミット差分のハッシュ」 を書き込みます。v3.0.0 で Skill `/code-review` / `/security-review` の検知は全廃しました。v4.1.0 で completion 検知を PostToolUse から subagent lifecycle hook へ完全移行しました (Claude Code v2.1.198 以降、 Agent tool は既定で background 起動になり、 PostToolUse は起動受理時にしか発火しないため)。Codex は wrapper が review 時 hash の pending attestation を書き、本 hook が parent-safe report 成功後に final marker へ昇格します。PostToolUseFailure でも本 script を呼び、残った Codex pending を破棄します。

マーカーが証明するのは、各 reviewer がマーカーに記録された最新差分に対してレビューを完了したことだけです。変更の approve や findings が 0 件であることは証明しません。`Status: findings` でも正規完了条件を満たせばマーカーは書かれ、finding の妥当性分類と修正判断は `/pre-push-review:review` の親 session が行います。

hooks.json の matcher は SubagentStart / SubagentStop とも `^pre-push-review:(code|codex|security)-reviewer$` で、 3 reviewer subagent 以外では本フックは発火しません。 スクリプト側でも agent_type の完全一致を再検証します (matcher の regex 解釈には依存しない)。

**検知ルール (v4.1.0)**:

| 検知対象                                                | event | 判定                                                                                                                                                                            | 書き込むマーカー                              |
| ------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| `pre-push-review:code-reviewer` subagent の完了 | `SubagentStart` + `SubagentStop` | launch attestation の存在・一回限りの消費 + 開始時 hash と現在 hash の一致 + `last_assistant_message` 内の単一 `Status: pass\|findings` 行 | `<git-dir>/.claude-pre-push-code-reviewed`    |
| `pre-push-review:codex-reviewer` subagent の完了 | `SubagentStart` + `SubagentStop` | 上記 completion 条件 + wrapper pending hash が current hash と一致 | `<git-dir>/.claude-pre-push-codex-reviewed` |
| `pre-push-review:security-reviewer` subagent の完了 | `SubagentStart` + `SubagentStop` | launch attestation の存在・一回限りの消費 + 開始時 hash と現在 hash の一致 + `last_assistant_message` 内の単一 `Status: pass\|findings` 行 | `<git-dir>/.claude-pre-push-security-reviewed` |

**subagent lifecycle hook (2 段階) で検知する理由**:

各 subagent は内部で標準 skill を呼ばずに self-contained でレビューを実行します。 Claude Code の Agent tool は既定で background 起動になり、 `async_launched` で正常 return した後は subagent 完了時に PostToolUse が発火しないため、 開始 (`SubagentStart`) と完了 (`SubagentStop`) の 2 イベントに分けて検知します。 `SubagentStart` は「レビューがこの差分に対して開始された」ことを launch attestation として one-shot 記録し、 `SubagentStop` は (a) attestation の一回限りの消費 (b) 開始時 hash と現在 hash の一致 (c) `last_assistant_message` 内の単一 `Status: pass\|findings` 行 (d) `stop_hook_active == false` をすべて検証した場合のみ marker を書きます。SendMessage resume 後の再 stop・レビュー開始後の差分変更・重複 stop・`execution-failed` は fail-closed に遮断され、push gate が deny のまま残るため silent-pass しない設計です。

**Codex pending attestation を挟む理由**:

wrapper exit 0 だけで final marker を書くと、その後の report 正規化が失敗・中断しても push gate が通り得ます。一方、report 完了時の current hash だけで marker を書くと review 中に branch state が変わった場合に「Codex が見ていない差分」をレビュー済みと誤認します。このため wrapper は review 対象 hash を pending に atomic write し、auto-mark は正規 report 成功と pending/current hash 一致の両方を確認して final marker へ atomic rename します。

**書き込みをスキップする条件**:

- `stop_hook_active` が boolean `false` でない (stop hook による継続中の中間 stop)
- launch attestation が無い、regular file でない (symlink 含む)、または開始時 hash と現在 hash が不一致
- `last_assistant_message` に単一の `Status: pass` / `Status: findings` 行が無い (`execution-failed`、欠落、重複、未知値、非 string)
- `agent_type` が namespace 付き 3 reviewer 以外、または `agent_id` が `^[A-Za-z0-9._-]{1,128}$` に不一致
- codex-reviewer では pending attestation が無い、regular file でない、または current hash と不一致
- カレントブランチが default branch (master/main)
- default branch (origin/HEAD) が検出できない (origin が無い等)

### マーカーファイル

すべて `<git-dir>` 配下に配置 (リポジトリ単位で共有、 ブランチ単位ではない):

| ファイル | 内容 | 寿命 |
|---|---|---|
| `.claude-pre-push-code-reviewed` | `pre-push-review:code-reviewer` subagent 完了時の commit 列 + branch 全差分のハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-codex-reviewed` | codex review + parent-safe report 完了時の commit 列 + branch 全差分のハッシュ。wrapper pending を auto-mark が昇格する | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-codex-reviewed.pending` | wrapper が束縛した review 対象 hash。final report 成功時だけ marker へ rename | report 失敗・hash mismatch・次回 wrapper 起動で削除。codex-reviewer の terminal な拒否 stop (attestation 無し / 既存 tombstone) でも破棄 (orphan 化の遮断) |
| `.claude-pre-push-security-reviewed` | `pre-push-review:security-reviewer` subagent 完了時の commit 列 + branch 全差分のハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-launch-<agent_id>` | SubagentStart が one-shot 記録するレビュー開始時の hash (launch attestation、v4.1.0 新設)。SubagentStop が開始時 hash と現在 hash の一致検証に使う | 最初の SubagentStop で消費 (削除)。1 日より古い残存分は次回 SubagentStart が掃除 |
| `.claude-pre-push-done-<agent_id>` | attestation 消費時に排他作成される launch tombstone (v4.1.0 新設)。同一 agent_id での SubagentStart 再発火 (resume 等) による attestation 再鋳造を遮断する。再レビューは新規 spawn (新しい agent_id) で行う | 無期限保持 (prune しない)。resume の成立期間は transcript 保持期間 (cleanupPeriodDays で延長可能) に従うため、期限付き掃除では遮断に穴が開く。1 件 64 byte で実害なし |

> **v2.x → v3.0.0 アップグレード時の注意**: v2.x で実行済みの 3 マーカー (code-reviewed / codex-reviewed / security-reviewed) は v3.0.0 でも hash が一致する限り有効です。 marker file 名と hash 計算式は不変なので追加の cleanup は不要です。

マーカーは reviewer subagent の agent_type に対して発行され、実効モデルは検証しません。`CLAUDE_CODE_SUBAGENT_MODEL` は起動時の model 指定や agent frontmatter より優先されるため、この環境変数を設定した環境では既定 model での実行保証が失われます。本プラグインはこの環境変数を設定しない運用を前提とします。

### Agents

#### `pre-push-review:code-reviewer` (subagent / v3.0.0 で追加)

**ファイル**: `agents/code-reviewer.md`

branch 全差分に対する correctness バグ検出を **self-contained に** 実行し、 結果のマークダウンレポートを親 session に返す subagent です。 v2.x までの Skill `/code-review` (Anthropic bundled skill / read-only correctness バグ検出) を置換するため v3.0.0 で追加されました。

**動作**:

- tools は `Bash, Read, Glob, Grep, LS` に制限 (Edit / Write / Skill / Task はすべて非許可)。 read-only でファイル改変を防ぎ、 `Skill` を外すことで標準 `/code-review` skill を invoke できないようにしている (理由は security-reviewer と同じ; 下記)。 `Task` を外すのは Claude Code が subagent からの nested subagent 起動を禁止しているため
- subagent body には logic errors / null/undefined / error handling / resource leaks / concurrency / API misuse / data corruption の各カテゴリと exclusion ルール (style / docs / perf / refactor / security / pre-existing bug 等) が prompt として含まれており、 単一 turn で review を完遂する
- 親 session は `Agent` / `Task` tool の result として parent-safe markdown report を受け取り、 後続フロー (`git push` 等) を継続できる。具体的な failure scenario は subagent context に留め、追加検証時は同じ subagent を resume する
- SubagentStop hook (auto-mark.sh) は launch attestation の開始時 hash と現在 hash の一致、および final report の単一 `Status: pass|findings` 行を確認して code-reviewed マーカーを更新する
- model は `opus` に固定、effort は指定せずセッション既定を継承 (v4.3.0 まで model は `inherit`、v5.2.0 までは `effort: medium` も固定していた)

#### `pre-push-review:codex-reviewer` (subagent / v3.0.0 で追加)

**ファイル**: `agents/codex-reviewer.md`

codex review wrapper (`hooks/scripts/run-codex-review.sh`) を foreground で 1 回起動し、 wrapper の stdout / stderr を subagent context 内で評価して parent-safe markdown report に抽象化する最小 subagent です。 v2.x までの Bash 直接起動 (commands/review.md と deny メッセージに wrapper 絶対パスを案内) を置換するため v3.0.0 で追加され、v4.0.1 で verbatim relay を廃止しました。

**動作**:

- tools は `Bash, TaskOutput, Read` (v5.2.0 で拡張。 Edit / Write / Skill / Task はすべて非許可)。 TaskOutput / Read は Bash timeout による background 移行後の回収専用 (下記) で、 wrapper-only な実行サーフェスは構造的に維持される
- **v5.2.0 で background-move recovery を追加 (issue #337)**: Bash tool が timeout してラッパー実行を background へ自動移行させても、 codex review 自体は完走していることが多い。 subagent は Bash 結果が background 移行を報告した直後に task ID と output file path を記録し、 同一 task ID に対して `TaskOutput (block=true)` で terminal state まで bounded に poll する (初回自動回収は TaskOutput 呼び出し 5 回・各呼び出しはツールの最大 timeout までを予算とする)。 report が truncated な場合のみ、 記録済み output file path に限定して Read で補完する。 回収成功時は既存の parent-safe report 契約へ normalize し、 task ID 喪失・回収予算超過・task 見失いの 3 境界では `Status: execution-failed` を返す (予算超過時は診断専用の resume 経路を report に案内するが、 resume は marker を昇格できず push gate を満たすには新規 reviewer run が必要)。 background 移行はそれ自体では wrapper failure (non-zero exit) 扱いにしない
- subagent body は wrapper を `run_in_background: false` で 1 回起動し、raw output を final reply へコピーせず parent-safe report に変換する
- 親 session は finding の priority / location / impact / verification / fix direction / disposition を受け取る。実行可能な command、payload、環境値、段階的な再現・回避手順、raw stdout / stderr は subagent context に閉じ込められる
- exact detail を使った追加確認が必要な場合は同一 codex-reviewer を resume し、検証結果だけを再度 parent-safe report で受け取る
- wrapper は exit 0 完了時に hash-bound pending attestation を atomic write し、auto-mark が subagent の正規 `pass/findings` report と current hash 一致を確認して codex-reviewed marker へ昇格する
- model は `sonnet` に固定 (v4.3.0 まで `inherit` で親 session と同じモデルを使用していた)
- **v4.0.0 で frontmatter `description` を起動条件中心に縮小**: 呼び出しタイミング (deny メッセージがどのマーカーを指摘したときか) と `subagent_type="pre-push-review:codex-reviewer"` の呼び出し方だけを記載し、 wrapper path や marker/attestation 実装詳細はメインセッションへ直接開示しない (実行手順・report 形式は引き続き body に定義)

#### `pre-push-review:security-reviewer` (subagent)

**ファイル**: `agents/security-reviewer.md`

branch 全差分に対するセキュリティレビューを **self-contained に** 実行し、 結果のマークダウンレポートを親 session に返す subagent です。 v0.3.0 で追加されました。

**動作**:

- tools は `Bash, Read, Glob, Grep, LS` に制限 (Edit / Write / Skill / Task はすべて非許可)。 read-only でファイル改変を防ぎ、 `Skill` を外すことで標準 `/security-review` skill を invoke できないようにしている (理由は下記)。 `Task` を外すのは Claude Code が subagent からの nested subagent 起動を禁止しているため
- subagent body には input validation / authn-authz / crypto-secrets / injection / data-exposure の各カテゴリと exclusion ルール (DoS / 既存依存 CVE / テストファイル等) が prompt として含まれており、 単一 turn で review を完遂する
- 親 session は `Agent` / `Task` tool の result として parent-safe markdown report を受け取り、 後続フロー (`git push` 等) を継続できる。具体的な attack scenario は subagent context に留め、追加検証時は同じ subagent を resume する
- SubagentStop hook (auto-mark.sh) は launch attestation の開始時 hash と現在 hash の一致、および final report の単一 `Status: pass|findings` 行を確認して security マーカーを更新する (`execution-failed` / 欠落 / 重複 / 未知値では書かず、silent-pass を防ぐ)
- model は `opus` に固定、effort は指定せずセッション既定を継承 (v4.3.0 まで model は `inherit`、v5.2.0 までは `effort: medium` も固定していた)

#### code-reviewer / security-reviewer subagent が標準 skill を invoke しない理由 (共通)

(1) 主 session の Claude が直接 `/code-review` / `/security-review` を呼ぶと skill prompt 末尾「Your final reply must contain the markdown report and nothing else.」 によって turn が終了し、 後続フロー (`git push`) まで進まない。
(2) subagent 内から invoke しても、 標準 skill 本体は内部で sub-task (Task tool) を spawn する設計だが、 Claude Code は **subagent 内での nested subagent 起動を禁止** している (公式ドキュメント `subagents cannot spawn other subagents`)。 sub-task が動かないため degraded mode で実行される。 v2.x では PostToolUse が Skill launch 時点で発火するためマーカーは書かれてしまい silent-pass の経路があったが、 v3.0.0 で Skill 検知を全廃したのでこの経路は閉じている。
(3) このため subagent は **同等のレビュー内容を self-contained な prompt として持ち**、 標準 skill を invoke しない設計に倒している。 標準 skill の prompt とは別管理になるため、 Anthropic 側の今後の改善は手動で追随する必要がある (トレードオフ)。

**呼び出しタイミング (3 subagent 共通)**: `/pre-push-review:review` slash command の指示で 3 並列 `Agent` / `Task` tool calls として起動する (完了は SubagentStop で検知されるため起動 mode は問わない)。 deny メッセージにも個別起動のフォールバック手順を案内している。

## 関連プラグイン

- [git-guardrails](../git-guardrails/): default branch (master/main) への直接書き込みを deny。 本プラグインは default branch 上の push を git-guardrails に委譲します
- [decompose-bash](../decompose-bash/): Bash コマンドを最小粒度に分解する SessionStart 注入。 本プラグインの PreToolUse hook が `&&` / `||` 等の合成で取りこぼされないよう、 Claude に各コマンドを独立 Bash 呼び出しに分けさせる
