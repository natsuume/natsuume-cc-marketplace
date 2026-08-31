# pre-push-review プラグイン

> **v6.0.0: codex review gate の分離 (互換破壊)**
>
> 本プラグインの push gate は code review / security review の **2 レビュー** で構成されます。 codex review gate は独立した `pre-push-codex-review` プラグインへ分離されました。
>
> v5.x から update すると、**push 時の codex review gate は無効になります** (codex review を伴わない push が本プラグインの gate を通過します)。 codex review gate を維持したい場合は、 update と同時に次を追加 install してください:
>
> ```bash
> claude plugin install pre-push-codex-review@natsuume-plugins
> ```
>
> 移行手順:
>
> 1. `claude plugin update pre-push-review` で v6.0.0 へ更新する
> 2. codex review gate を維持する場合は上記コマンドで `pre-push-codex-review` を install する (維持しない場合はこの手順は不要)
> 3. `.claude-pre-push-code-reviewed` / `.claude-pre-push-security-reviewed` マーカーの名前と hash 計算式は変わらないため、 既存マーカーは hash が一致する限りそのまま有効です。 `.claude-pre-push-codex-reviewed` は本プラグインからは参照されなくなります

`git push` を実行する前に **2 レビュー** を必ず実行させ、 未レビューな commit が remote に到達するのを構造的にブロックするプラグインです。 2 レビューはどちらも subagent 経由で実行されます:

- **`pre-push-review:code-reviewer` subagent** (self-contained correctness バグ検出 / 詳細は [Agents](#agents))
- **`pre-push-review:security-reviewer` subagent** (self-contained security review / 詳細は [Agents](#agents))

correctness バグ検出に security review を重ねた defense-in-depth です。 修正や commit 列の変更 (add→revert / amend / rebase 含む) により hash が変わると 2 マーカーは自動失効し、 Claude は再走させる以外に push を通す手段がありません (= ループが構造的に強制されます)。

2 レビューをどちらも subagent 経由に統一していることの意味:

- **context isolation**: reviewer は raw stdout / stderr、実行可能な command、具体的な再現手順を subagent context に留め、親 session には severity / location / impact / verification / fix direction / disposition を保持した parent-safe report だけを返します。これは agent prompt と contract test で固定する **instruction contract** であり、auto-mark が report 本文を機械検査して情報流出を遮断する **hard security boundary** ではありません。
- **起動・marker 発行経路の単一化**: 親 session は 2 軸とも同じ `Agent` / `Task` tool で起動し、2 marker とも auto-mark.sh が SubagentStart の launch attestation と SubagentStop での parent-safe report 検証を経て発行します。
- **`/pre-push-review:review` slash command が 2 subagent を並列発出**: deny メッセージとともに案内されます。 wall-clock は最遅レビュー 1 本の時間で完了します。

Linked worktree では marker、launch attestation、tombstone を main `.git` 直下ではなく、`git rev-parse --absolute-git-dir` が返す worktree 専用 git-dir (`.git/worktrees/<name>/`) に保存します。`block-pre-push.sh` の deny メッセージは実際の marker storage を表示するため、main `.git` の同名ファイルを見てレビュー状態を判断しないでください。

## バージョン

v6.0.0

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install pre-push-review@natsuume-plugins
```

### 依存コマンド

`jq` は push gate の必須依存です。`jq` が見つからない環境では、未レビューの push を通さないため `block-pre-push.sh` が `git push` を fail-closed に deny し、インストール後の再実行を案内します。push と無関係な Bash 呼び出しは影響を受けません。

現行の Codex runtime では安全な reviewer identity 契約を構築できないため、本プラグインは Claude Code 専用で、Codex marketplace では配布していません。

## 機能一覧

### Commands

#### `/pre-push-review:review`

**ファイル**: `commands/review.md`

push 前 2 レビューを **同じアシスタントメッセージで並列に** 2 subagent として起動する確定的フローです。 deny メッセージから案内されたら、 Claude はこのコマンドを実行し、 2 subagent (`pre-push-review:code-reviewer` + `pre-push-review:security-reviewer`) を 1 つの assistant message 内で並列 `Agent` / `Task` tool call として発出します。 順序や引数の自律判断は構造的に排除されています。

並列発出が技術的に成立しない / 一部のレビューが失敗した場合は、 2 subagent を順次起動しても push gate の構造的保証は同じ (2 マーカーの hash 一致が成立すれば push 可)。 wall-clock が伸びるだけのトレードオフです。

一部の marker のみ「未実行」 / 「失効」 の場合は、 該当 subagent だけを Agent / Task tool で単独再起動するのが正規経路です (block-pre-push.sh の deny メッセージも同じ案内をします。 完了は SubagentStop で検知されるため起動 mode は問いません)。 2 subagent 並列発出が既定であることは変わりません。

### Hooks

#### 1. block-pre-push (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-pre-push.sh`

`git push` を含むコマンドを検出した際、 commit 列 (HEAD / merge-base の OID) + ブランチ全差分 + 未コミット差分のハッシュと **2 つのレビューマーカー** (code-reviewer / security-reviewer subagent 起因) のハッシュを比較し、 2 マーカーがすべて一致しなければ `deny` を返します。 2 マーカーは常にすべて必須です。

**動作**:

- `git push --dry-run` / `git push -n` (remote ref を更新しない診断 push) は markers の状態に関わらず通す (no-op なので gate 不要)
- 単独実行 (`git push`) と複合コマンド (`xxx && git push ...`, `cd dir && git push ...`) の双方を検出
- `git -C dir push` や `git --git-dir=... push`、 `GIT_DIR=... git push` のような target-override 形式も許容 (cooperative 利用前提)
- カレントブランチが default branch (master/main) の場合は本フックでは gate せず、 `git-guardrails` の `block-default-branch-push.sh` に委譲 (重複 deny メッセージを避けるため)
- merge-base と HEAD の tree が一致し、 merge commit を含まず、 範囲内の全 commit の tree が HEAD tree と一致し、 かつ index / worktree が clean な場合は gate しない (空 push は通す。 tree OID ベースの判定)
- **working tree が dirty (staged または unstaged 変更あり) の場合は markers の状態に関わらず deny**: push される committed 部分とレビューされた working tree の乖離を防ぐため、 push 前に commit 完了を要求する
- 2 マーカーがすべて一致した場合はそのまま push を許容する (markers は明示削除しない: PreToolUse は push 成功を確認できないため、 remote rejection / 認証失敗 / ネットワーク失敗時に同じ state での再 push がレビュー必須になる無駄ループを避ける。 markers は次の編集で hash が変わったときに自然に失効する)
- ハッシュは `head <HEAD の commit OID>` 行 + `mbase <merge-base の commit OID>` 行 + `git diff <merge-base> HEAD` + `git diff --cached` + `git diff` (diff 3 種はいずれも `--no-ext-diff --no-textconv` 付き) を連結した入力に対する sha256 として計算する。 HEAD の commit OID をハッシュ入力に束縛しているため、 レビュー後に commit A を積んでから revert して戻す (net diff は review 時と同一でも commit 列は変わっている) 操作でもマーカーは自動失効する。 未コミットの edit があると `git diff --cached` / `git diff` の内容が変わりハッシュも変わるため、 markers が失効し commit + 再 review を強制できる
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
  これらは「引用符で囲まれた `git push` 例文をテキスト参照として介入しない」仕様や、認識済み `git push` の未知 wrapper / quote 付き引数を保守的に deny する仕様とは別の境界である
- 別端末・別 clone から行われる `git push` は Claude Code hook の原理的範囲外で gate できない (本気で塞ぐなら `.git/hooks/pre-push` real git hook を別レイヤーで併設)
- GitHub サーバ側で実施される操作 (Web UI のマージ / rebase 等) も Claude Code hook 範囲外
- **default branch (master/main) 上での push は本プラグイン単独では gate されない**: 本プラグインは `git-guardrails` の `block-default-branch-push.sh` が default branch push を deny する前提で gate を skip する。 `git-guardrails` を併用していない環境では default branch 上の push が review なしで通る経路が残る

> **target-mismatch の構造的解決**: 本プラグインは独自の bash command parser (`lib/cmd-parser.sh`) と target resolver (`lib/target-resolver.sh`) で `cd dir && git push` / `git -C dir push` / `GIT_DIR=path/.git git push` の **実 push target を決定的に解決** し、解決した target cwd の `.git` (`git rev-parse --git-dir`) に対して markers / hash 比較を行います。解析不能な形式 (subshell `(...)`, brace group `{...}`, `bash -c "..."`, `pushd`/`popd`, `export GIT_DIR=...`, `--work-tree=...`, `time` / `env` 等の未対応 wrapper) は **保守的に deny** します。

#### 2. auto-mark (SubagentStart / SubagentStop, matcher: `^pre-push-review:(code|security)-reviewer$`)

**ファイル**: `hooks/scripts/auto-mark.sh`

2 reviewer subagent の **実行完了** を subagent lifecycle hook (SubagentStart / SubagentStop) で自動検知し、対応するマーカーファイルに「commit 列 (HEAD / merge-base の OID) + branch 全差分 + 未コミット差分のハッシュ」 を書き込みます。Skill (`/code-review` / `/security-review`) の検知は行いません。completion 検知に PostToolUse を使わないのは、 Claude Code の Agent tool が既定で background 起動になり、 PostToolUse が起動受理時にしか発火しないためです。

マーカーが証明するのは、各 reviewer がマーカーに記録された最新差分に対してレビューを完了したことだけです。変更の approve や findings が 0 件であることは証明しません。`Status: findings` でも正規完了条件を満たせばマーカーは書かれ、finding の妥当性分類と修正判断は `/pre-push-review:review` の親 session が行います。

hooks.json の matcher は SubagentStart / SubagentStop とも `^pre-push-review:(code|security)-reviewer$` で、 2 reviewer subagent 以外では本フックは発火しません。 スクリプト側でも agent_type の完全一致を再検証します (matcher の regex 解釈には依存しない)。

**検知ルール**:

| 検知対象                                                | event | 判定                                                                                                                                                                            | 書き込むマーカー                              |
| ------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| `pre-push-review:code-reviewer` subagent の完了 | `SubagentStart` + `SubagentStop` | launch attestation の存在・一回限りの消費 + 開始時 hash と現在 hash の一致 + `last_assistant_message` 内の単一 `Status: pass\|findings` 行 | `<git-dir>/.claude-pre-push-code-reviewed`    |
| `pre-push-review:security-reviewer` subagent の完了 | `SubagentStart` + `SubagentStop` | launch attestation の存在・一回限りの消費 + 開始時 hash と現在 hash の一致 + `last_assistant_message` 内の単一 `Status: pass\|findings` 行 | `<git-dir>/.claude-pre-push-security-reviewed` |

**subagent lifecycle hook (2 段階) で検知する理由**:

各 subagent は内部で標準 skill を呼ばずに self-contained でレビューを実行します。 Claude Code の Agent tool は既定で background 起動になり、 `async_launched` で正常 return した後は subagent 完了時に PostToolUse が発火しないため、 開始 (`SubagentStart`) と完了 (`SubagentStop`) の 2 イベントに分けて検知します。 `SubagentStart` は「レビューがこの差分に対して開始された」ことを launch attestation として one-shot 記録し、 `SubagentStop` は (a) attestation の一回限りの消費 (b) 開始時 hash と現在 hash の一致 (c) `last_assistant_message` 内の単一 `Status: pass\|findings` 行 (d) `stop_hook_active == false` をすべて検証した場合のみ marker を書きます。SendMessage resume 後の再 stop・レビュー開始後の差分変更・重複 stop・`execution-failed` は fail-closed に遮断され、push gate が deny のまま残るため silent-pass しない設計です。

**書き込みをスキップする条件**:

- `stop_hook_active` が boolean `false` でない (stop hook による継続中の中間 stop)
- launch attestation が無い、regular file でない (symlink 含む)、または開始時 hash と現在 hash が不一致
- `last_assistant_message` に単一の `Status: pass` / `Status: findings` 行が無い (`execution-failed`、欠落、重複、未知値、非 string)
- `agent_type` が namespace 付き 2 reviewer 以外、または `agent_id` が `^[A-Za-z0-9._-]{1,128}$` に不一致
- カレントブランチが default branch (master/main)
- default branch (origin/HEAD) が検出できない (origin が無い等)

### マーカーファイル

すべて `<git-dir>` 配下に配置 (リポジトリ単位で共有、 ブランチ単位ではない):

| ファイル | 内容 | 寿命 |
|---|---|---|
| `.claude-pre-push-code-reviewed` | `pre-push-review:code-reviewer` subagent 完了時の commit 列 + branch 全差分のハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-security-reviewed` | `pre-push-review:security-reviewer` subagent 完了時の commit 列 + branch 全差分のハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-launch-<agent_id>` | SubagentStart が one-shot 記録するレビュー開始時の hash (launch attestation)。SubagentStop が開始時 hash と現在 hash の一致検証に使う | 最初の SubagentStop で消費 (削除)。1 日より古い残存分は次回 SubagentStart が掃除 |
| `.claude-pre-push-done-<agent_id>` | attestation 消費時に排他作成される launch tombstone。同一 agent_id での SubagentStart 再発火 (resume 等) による attestation 再鋳造を遮断する。再レビューは新規 spawn (新しい agent_id) で行う | 無期限保持 (prune しない)。resume の成立期間は transcript 保持期間 (cleanupPeriodDays で延長可能) に従うため、期限付き掃除では遮断に穴が開く。1 件 64 byte で実害なし |

マーカーは reviewer subagent の agent_type に対して発行され、実効モデルは検証しません。`CLAUDE_CODE_SUBAGENT_MODEL` は起動時の model 指定や agent frontmatter より優先されるため、この環境変数を設定した環境では既定 model での実行保証が失われます。本プラグインはこの環境変数を設定しない運用を前提とします。

### Agents

#### `pre-push-review:code-reviewer` (subagent)

**ファイル**: `agents/code-reviewer.md`

branch 全差分に対する correctness バグ検出を **self-contained に** 実行し、 結果のマークダウンレポートを親 session に返す subagent です。

**動作**:

- tools は `Bash, Read, Glob, Grep, LS` に制限 (Edit / Write / Skill / Task はすべて非許可)。 read-only でファイル改変を防ぎ、 `Skill` を外すことで標準 `/code-review` skill を invoke できないようにしている (理由は security-reviewer と同じ; 下記)。 `Task` を外すのは Claude Code が subagent からの nested subagent 起動を禁止しているため
- subagent body には logic errors / null/undefined / error handling / resource leaks / concurrency / API misuse / data corruption の各カテゴリと exclusion ルール (style / docs / perf / refactor / security / pre-existing bug 等) が prompt として含まれており、 単一 turn で review を完遂する
- 親 session は `Agent` / `Task` tool の result として parent-safe markdown report を受け取り、 後続フロー (`git push` 等) を継続できる。具体的な failure scenario は subagent context に留め、追加検証時は同じ subagent を resume する
- SubagentStop hook (auto-mark.sh) は launch attestation の開始時 hash と現在 hash の一致、および final report の単一 `Status: pass|findings` 行を確認して code-reviewed マーカーを更新する
- model は `opus` に固定、effort は指定せずセッション既定を継承

#### `pre-push-review:security-reviewer` (subagent)

**ファイル**: `agents/security-reviewer.md`

branch 全差分に対するセキュリティレビューを **self-contained に** 実行し、 結果のマークダウンレポートを親 session に返す subagent です。

**動作**:

- tools は `Bash, Read, Glob, Grep, LS` に制限 (Edit / Write / Skill / Task はすべて非許可)。 read-only でファイル改変を防ぎ、 `Skill` を外すことで標準 `/security-review` skill を invoke できないようにしている (理由は下記)。 `Task` を外すのは Claude Code が subagent からの nested subagent 起動を禁止しているため
- subagent body には input validation / authn-authz / crypto-secrets / injection / data-exposure の各カテゴリと exclusion ルール (DoS / 既存依存 CVE / テストファイル等) が prompt として含まれており、 単一 turn で review を完遂する
- 親 session は `Agent` / `Task` tool の result として parent-safe markdown report を受け取り、 後続フロー (`git push` 等) を継続できる。具体的な attack scenario は subagent context に留め、追加検証時は同じ subagent を resume する
- SubagentStop hook (auto-mark.sh) は launch attestation の開始時 hash と現在 hash の一致、および final report の単一 `Status: pass|findings` 行を確認して security マーカーを更新する (`execution-failed` / 欠落 / 重複 / 未知値では書かず、silent-pass を防ぐ)
- model は `opus` に固定、effort は指定せずセッション既定を継承

#### code-reviewer / security-reviewer subagent が標準 skill を invoke しない理由 (共通)

(1) 主 session の Claude が直接 `/code-review` / `/security-review` を呼ぶと skill prompt 末尾「Your final reply must contain the markdown report and nothing else.」 によって turn が終了し、 後続フロー (`git push`) まで進まない。
(2) subagent 内から invoke しても、 標準 skill 本体は内部で sub-task (Task tool) を spawn する設計だが、 Claude Code は **subagent 内での nested subagent 起動を禁止** している (公式ドキュメント `subagents cannot spawn other subagents`)。 sub-task が動かないため degraded mode で実行される。 auto-mark.sh は Skill 検知を行わないため、 degraded mode の完了でマーカーが書かれる silent-pass の経路は存在しない。
(3) このため subagent は **同等のレビュー内容を self-contained な prompt として持ち**、 標準 skill を invoke しない設計に倒している。 標準 skill の prompt とは別管理になるため、 Anthropic 側の今後の改善は手動で追随する必要がある (トレードオフ)。

**呼び出しタイミング (2 subagent 共通)**: `/pre-push-review:review` slash command の指示で 2 並列 `Agent` / `Task` tool calls として起動する (完了は SubagentStop で検知されるため起動 mode は問わない)。 deny メッセージにも個別起動のフォールバック手順を案内している。

## 関連プラグイン

- [git-guardrails](../git-guardrails/): default branch (master/main) への直接書き込みを deny。 本プラグインは default branch 上の push を git-guardrails に委譲します
- [decompose-bash](../decompose-bash/): Bash コマンドを最小粒度に分解する SessionStart 注入。 本プラグインの PreToolUse hook が `&&` / `||` 等の合成で取りこぼされないよう、 Claude に各コマンドを独立 Bash 呼び出しに分けさせる
