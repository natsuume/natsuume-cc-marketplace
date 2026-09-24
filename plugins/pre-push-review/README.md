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
> 移行手順 (この順序で行うと、 review cadence の欠落と codex gate の空白のどちらも生じません):
>
> 1. review cadence (Codex review 一定回数ごとの advisor checkpoint 強制) を維持したい場合: 旧 reviewer 識別子の cadence 互換受理は cross-model-advisor v2.1.0〜v2.x のみが持っていました。v3.0.0 以降は review cadence 自体が pre-push-codex-review (v2.0.0 以降) へ移設されているため、cadence を維持するには pre-push-codex-review v2.0.0 以降を install してください (次の手順 2 と同じ install で満たされます)
> 2. push 時の codex review gate を維持する場合、 `claude plugin install pre-push-codex-review@natsuume-plugins` を実行する
> 3. `claude plugin update pre-push-review` で v6.0.0 へ更新する
> 4. `.claude-pre-push-code-reviewed` / `.claude-pre-push-security-reviewed` マーカーの名前と hash 計算式は変わらないため、 既存マーカーは hash が一致する限りそのまま有効です。 `.claude-pre-push-codex-reviewed` は本プラグインからは参照されなくなります (マーカーの名前・格納先は不変のため移行操作は不要です)

`git push` を実行する前に **2 レビュー** を必ず実行させ、 未レビューな commit が remote に到達するのを構造的にブロックするプラグインです。 2 レビューはどちらも subagent 経由で実行されます:

- **`pre-push-review:code-reviewer` subagent** (self-contained correctness バグ検出 / 詳細は [Agents](#agents))
- **`pre-push-review:security-reviewer` subagent** (self-contained security review / 詳細は [Agents](#agents))

correctness バグ検出に security review を重ねた defense-in-depth です。 修正や commit 列の変更 (add→revert / amend / rebase 含む) により hash が変わると 2 マーカーは自動失効し、 Claude は再走させる以外に push を通す手段がありません (= ループが構造的に強制されます)。

2 レビューをどちらも subagent 経由に統一していることの意味:

- **context isolation**: reviewer は raw stdout / stderr、実行可能な command、具体的な再現手順を subagent context に留め、親 session には severity / location / impact / verification / fix direction / disposition を保持した parent-safe report だけを返します。これは agent prompt と contract test で固定する **instruction contract** であり、auto-mark が report 本文を機械検査して情報流出を遮断する **hard security boundary** ではありません。
- **起動・marker 発行経路の単一化**: 親 session は 2 軸とも同じ `Agent` / `Task` tool で起動し、2 marker とも auto-mark.sh が SubagentStart の launch attestation、PostToolUse (`SubagentHandback`) での hand-back された report の記録、SubagentStop での parent-safe report 検証を経て発行します。
- **`/pre-push-review:review` slash command が 2 subagent を並列発出**: deny メッセージとともに案内されます。 wall-clock は最遅レビュー 1 本の時間で完了します。

Linked worktree では marker、launch attestation、tombstone を main `.git` 直下ではなく、`git rev-parse --absolute-git-dir` が返す worktree 専用 git-dir (`.git/worktrees/<name>/`) に保存します。`block-pre-push.sh` の deny メッセージは実際の marker storage を表示するため、main `.git` の同名ファイルを見てレビュー状態を判断しないでください。

## バージョン

v6.2.1

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

push 前 2 レビューを **同じアシスタントメッセージで並列に** 2 subagent として起動する確定的フローです。 deny メッセージから案内されたら、 Claude はこのコマンドを実行し、 2 subagent (`pre-push-review:code-reviewer` + `pre-push-review:security-reviewer`) を 1 つの assistant message 内で並列 `Agent` / `Task` tool call として発出します。 発出の前に判定コマンド `pre-push-review-reviewer-model` を 1 回実行し、出力 1 行目の model (`fable` / `opus`、下記「起動 model の判定」参照) で 2 subagent を起動します。 順序や引数の自律判断は構造的に排除されています。

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

#### 2. auto-mark (SubagentStart / PostToolUse / SubagentStop)

**ファイル**: `hooks/scripts/auto-mark.sh`

2 reviewer subagent の **実行完了** を subagent lifecycle hook (SubagentStart / SubagentStop) で自動検知し、対応するマーカーファイルに「commit 列 (HEAD / merge-base の OID) + branch 全差分 + 未コミット差分のハッシュ」 を書き込みます。Skill (`/code-review` / `/security-review`) の検知は行いません。completion 検知に Agent tool の PostToolUse を使わないのは、 Claude Code の Agent tool が既定で background 起動になり、 PostToolUse が起動受理時にしか発火しないためです。

Claude Code v2.1.271 以降の auto mode では、subagent の最終 report は `SubagentHandback` tool の `message` として親に届き、SubagentStop の `last_assistant_message` には hand-back 後の締めの文しか入りません。そのため PostToolUse (matcher: `^SubagentHandback$`) で `tool_input.message` の Status を判定して handback record (`.claude-pre-push-handback-<agent_id>`) に記録し、SubagentStop がその record を one-shot で消費します。record がある場合は `last_assistant_message` を見ません (record が優先)。record が無い場合 (非 auto mode 等) に限り `last_assistant_message` を report として判定します。

マーカーが証明するのは、各 reviewer がマーカーに記録された最新差分に対してレビューを完了したことだけです。変更の approve や findings が 0 件であることは証明しません。`Status: findings` でも正規完了条件を満たせばマーカーは書かれ、finding の妥当性分類と修正判断は `/pre-push-review:review` の親 session が行います。

hooks.json の matcher は SubagentStart / SubagentStop とも `^pre-push-review:(code|security)-reviewer$` で、 2 reviewer subagent 以外では本フックは発火しません。 PostToolUse の matcher は tool 名 (`^SubagentHandback$`) のため、 スクリプト側で agent_type の完全一致を検証してから record を書きます。 いずれの event でもスクリプト側で agent_type の完全一致を再検証します (matcher の regex 解釈には依存しない)。

**検知ルール**:

| 検知対象                                                | event | 判定                                                                                                                                                                            | 書き込むマーカー                              |
| ------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| `pre-push-review:code-reviewer` subagent の完了 | `SubagentStart` + `PostToolUse` (`SubagentHandback`) + `SubagentStop` | launch attestation の存在・一回限りの消費 + 開始時 hash と現在 hash の一致 + report (handback record、無ければ `last_assistant_message`) 内の単一 `Status: pass\|findings` 行 | `<git-dir>/.claude-pre-push-code-reviewed`    |
| `pre-push-review:security-reviewer` subagent の完了 | `SubagentStart` + `PostToolUse` (`SubagentHandback`) + `SubagentStop` | launch attestation の存在・一回限りの消費 + 開始時 hash と現在 hash の一致 + report (handback record、無ければ `last_assistant_message`) 内の単一 `Status: pass\|findings` 行 | `<git-dir>/.claude-pre-push-security-reviewed` |

**subagent lifecycle hook (2 段階) で検知する理由**:

各 subagent は内部で標準 skill を呼ばずに self-contained でレビューを実行します。 Claude Code の Agent tool は既定で background 起動になり、 `async_launched` で正常 return した後は subagent 完了時に PostToolUse が発火しないため、 開始 (`SubagentStart`) と完了 (`SubagentStop`) の 2 イベントに分けて検知します。 `SubagentStart` は「レビューがこの差分に対して開始された」ことを launch attestation として one-shot 記録し、 `SubagentStop` は (a) attestation の一回限りの消費 (b) 開始時 hash と現在 hash の一致 (c) report (auto mode では `PostToolUse` が `SubagentHandback` の `tool_input.message` から記録した handback record、それ以外は `last_assistant_message`) 内の単一 `Status: pass\|findings` 行 (d) `stop_hook_active == false` をすべて検証した場合のみ marker を書きます。SendMessage resume 後の再 stop・レビュー開始後の差分変更・重複 stop・`execution-failed` は fail-closed に遮断され、push gate が deny のまま残るため silent-pass しない設計です。

**書き込みをスキップする条件**:

- `stop_hook_active` が boolean `false` でない (stop hook による継続中の中間 stop)
- launch attestation が無い、regular file でない (symlink 含む)、または開始時 hash と現在 hash が不一致
- report (handback record があればその判定結果、無ければ `last_assistant_message`) に単一の `Status: pass` / `Status: findings` 行が無い (`execution-failed`、欠落、重複、未知値、非 string)。同一 agent_id で `SubagentHandback` が 2 回以上呼ばれた場合も重複 report として無効。`tool_response.success` が false (未配信) の hand-back は記録されず、`last_assistant_message` 経路の判定に委ねる
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
| `.claude-pre-push-handback-<agent_id>` | PostToolUse (`SubagentHandback`) が記録する hand-back された report の Status 判定結果 (`pass` / `findings` / `invalid` の 1 語)。launch attestation が存在し tombstone が無い場合のみ書かれる | 最初の SubagentStop で消費 (削除)。1 日より古い残存分は次回 SubagentStart が掃除 |

マーカーは reviewer subagent の agent_type に対して発行され、実効モデルは検証しません。明示 model / agent frontmatter が `CLAUDE_CODE_SUBAGENT_MODEL` より優先されます。`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` 設定時のみ env が全てを上書きするため、この環境変数を設定した環境では既定 model での実行保証が失われます。本プラグインはこの環境変数を設定しない運用を前提とします。

### Agents

#### `pre-push-review:code-reviewer` (subagent)

**ファイル**: `agents/code-reviewer.md`

branch 全差分に対する correctness バグ検出を **self-contained に** 実行し、 結果のマークダウンレポートを親 session に返す subagent です。

**動作**:

- tools は `Bash, Read, Glob, Grep` に制限 (Edit / Write / Skill / Agent はすべて非許可)。 read-only でファイル改変を防ぎ、 `Skill` を外すことで標準 `/code-review` skill を invoke できないようにしている (理由は security-reviewer と同じ; 下記)。 `Agent` を外すことで nested subagent も起動せず、 reviewer を read-only に保つ (nested subagent は既定で起動できるが本 reviewer は使わない)
- subagent body には logic errors / null/undefined / error handling / resource leaks / concurrency / API misuse / data corruption の各カテゴリと exclusion ルール (style / docs / perf / refactor / security / pre-existing bug 等) が prompt として含まれており、 単一 turn で review を完遂する
- 親 session は `Agent` / `Task` tool の result として parent-safe markdown report を受け取り、 後続フロー (`git push` 等) を継続できる。具体的な failure scenario は subagent context に留め、追加検証時は同じ subagent を resume する
- SubagentStop hook (auto-mark.sh) は launch attestation の開始時 hash と現在 hash の一致、および final report (auto mode では PostToolUse が `SubagentHandback` から記録した report) の単一 `Status: pass|findings` 行を確認して code-reviewed マーカーを更新する
- agent 定義 frontmatter の model は `opus`。起動時は下記「起動 model の判定」に従って `model: "fable"` / `model: "opus"` を明示する。effort は指定せずセッション既定を継承

#### `pre-push-review:security-reviewer` (subagent)

**ファイル**: `agents/security-reviewer.md`

branch 全差分に対するセキュリティレビューを **self-contained に** 実行し、 結果のマークダウンレポートを親 session に返す subagent です。

**動作**:

- tools は `Bash, Read, Glob, Grep` に制限 (Edit / Write / Skill / Agent はすべて非許可)。 read-only でファイル改変を防ぎ、 `Skill` を外すことで標準 `/security-review` skill を invoke できないようにしている (理由は下記)。 `Agent` を外すことで nested subagent も起動せず、 reviewer を read-only に保つ (nested subagent は既定で起動できるが本 reviewer は使わない)
- subagent body には input validation / authn-authz / crypto-secrets / injection / data-exposure の各カテゴリと exclusion ルール (DoS / 既存依存 CVE / テストファイル等) が prompt として含まれており、 単一 turn で review を完遂する
- net diff に加えて `origin/HEAD..HEAD` の per-commit patch (`git log -p --cc`。merge commit で加えられた変更も含む) を読み、 後続 commit で削除・revert されて net diff に残らない中間 commit の秘匿情報・危険コードも検査する。 push すると branch の全 commit が remote 履歴に載るためである。 中間 commit の秘匿情報は、 削除 commit を積むのではなく履歴から除去し、 露出の可能性があればローテーションする修正方針で報告する。 code-reviewer は net diff のみを対象とする
- 親 session は `Agent` / `Task` tool の result として parent-safe markdown report を受け取り、 後続フロー (`git push` 等) を継続できる。具体的な attack scenario は subagent context に留め、追加検証時は同じ subagent を resume する
- SubagentStop hook (auto-mark.sh) は launch attestation の開始時 hash と現在 hash の一致、および final report (auto mode では PostToolUse が `SubagentHandback` から記録した report) の単一 `Status: pass|findings` 行を確認して security マーカーを更新する (`execution-failed` / 欠落 / 重複 / 未知値では書かず、silent-pass を防ぐ)
- agent 定義 frontmatter の model は `opus`。起動時は下記「起動 model の判定」に従って `model: "fable"` / `model: "opus"` を明示する。effort は指定せずセッション既定を継承

#### code-reviewer / security-reviewer subagent が標準 skill を invoke しない理由 (共通)

(1) confidence / severity 付きの parent-safe report 契約を reviewer 側に固定し、 親 session が同じ書式で findings を分類できるようにする。
(2) SubagentStart / SubagentStop / SubagentHandback の lifecycle hook で reviewer の実行を marker として検知する。 auto-mark.sh は Skill 検知を行わないため、 reviewer subagent を経ない完了でマーカーが書かれる silent-pass の経路は存在しない。
(3) `tools` から `Agent` を除外して reviewer を read-only に保つ (nested subagent は既定で起動できるが本 reviewer は使わない)。
このため subagent は **同等のレビュー内容を self-contained な prompt として持ち**、 標準 skill を invoke しない設計に倒している。 標準 skill の prompt とは別管理になるため、 Anthropic 側の今後の改善は手動で追随する必要がある (トレードオフ)。

**呼び出しタイミング (2 subagent 共通)**: `/pre-push-review:review` slash command の指示で 2 並列 `Agent` / `Task` tool calls として起動する (完了は SubagentStop で検知されるため起動 mode は問わない)。 deny メッセージにも個別起動のフォールバック手順を案内している。

#### 起動 model の判定 (2 subagent 共通)

reviewer は Fable 週次枠の使用率に余裕がある間は Fable で、そうでない場合は Opus で起動する。判定は plugin 同梱のコマンド `bin/pre-push-review-reviewer-model` (plugin が有効な間は Bash の PATH に載る) と deny 文が、共通の `hooks/scripts/lib/fable-weekly-usage.sh` で行う。`/pre-push-review:review` は起動前にこのコマンドを 1 回実行し、出力 1 行目の model で 2 reviewer を起動する。

- 入力は natsuume-statusline が書く `${XDG_CACHE_HOME:-$HOME/.cache}/natsuume-statusline/weekly-scoped.json`。読むだけで書き込まず、OAuth usage API も呼ばない
- 閾値は env `FABLE_WEEKLY_MAX_PERCENT` (前後空白を trim した 0〜100 の 10 進整数)。未設定・空・範囲外・非整数は既定値 `80`
- `weekly_scoped[]` のうち `display_name` が大文字小文字を無視して `fable` を含み `percent` が数値の entry の最大 `percent` が閾値以下 (ちょうど閾値を含む) なら `fable`、超過なら `opus` (理由に使用率と閾値を併記)
- cache が symlink / 通常ファイルでない / 存在しない / 読めない / JSON document が 1 つでない / `fetched_at` が欠落・非数値 / `now - fetched_at > 1800` (stale) / `weekly_scoped` が欠落・非配列・空 / Fable entry が無い / 現在時刻を取得できない / jq が無い場合は使用率不明として `opus` (理由に使用率を確認できない旨を併記)。`fetched_at` が未来時刻でも stale とはみなさない
- コマンドの出力は常に 2 行 (1 行目 = `fable` / `opus`、2 行目 = 判定理由) で exit 0。コマンドが見つからない・実行できない場合、`/pre-push-review:review` は `opus` で起動する
- agent 定義 frontmatter の model は `opus` のまま据え置く。frontmatter は agent-discipline の hook から見えず、frontmatter を fable にすると model 未指定の起動が使用率判定を経ずに Fable で走るため
- `model: "fable"` の起動が agent-discipline の hook に deny された場合 (Fable メインのセッション、判定後に使用率が閾値を超えた等) は、同じ reviewer を `model: "opus"` で再起動する (deny 文と `/pre-push-review:review` が案内する)。marker は subagent の agent_type に対して発行されるため、どちらの model で走っても同じく機能する

## 既知の制約

- **中間 commit の秘匿情報検出は LLM レビューに依存する**: security-reviewer は中間 commit の per-commit patch も検査するが、 LLM による検出であり見落としがありうる。 秘匿情報の混入を決定的に止めたい場合は、 gitleaks 等の secret scanner を pre-commit / pre-push hook として併用することを推奨する
- **gate の観測範囲は Bash tool のみ**: PreToolUse hook の matcher が `Bash` であるため、PowerShell tool (`CLAUDE_CODE_USE_POWERSHELL_TOOL=1` で Linux / macOS でも有効化できる) および Monitor tool 経由で発行された `git push` を gate は観測しない。これらの tool を有効にした環境はサポート外

## 関連プラグイン

- [git-guardrails](../git-guardrails/): default branch (master/main) への直接書き込みを deny。 本プラグインは default branch 上の push を git-guardrails に委譲します
- [decompose-bash](../decompose-bash/): Bash コマンドを最小粒度に分解する SessionStart 注入。 本プラグインの PreToolUse hook が `&&` / `||` 等の合成で取りこぼされないよう、 Claude に各コマンドを独立 Bash 呼び出しに分けさせる
