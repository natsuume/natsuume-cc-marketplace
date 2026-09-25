# pre-merge-cross-review プラグイン

`gh pr merge` を実行する前に **codex review** (OpenAI クロスモデルレビュー) の完了を確認し、未レビューな PR が merge されるのを防ぐプラグインです。個人環境 (ChatGPT Plus の codex CLI) 向けに、`git push` の都度ではなく **merge 前に 1 回だけ** codex review を行う運用を成立させます。Fable 週次枠に余裕があるときは、codex review と並列に **Fable review** (PR 説明・関連 issue の受入基準との整合と設計境界を見るレビュー) も実行し、その report を親 session に返します。GitHub には何も書きません。単独 install で自立動作します。

レビュー済みの証拠は **merge 実行 repo の git-dir 直下にあるローカル記録** です。codex-reviewer subagent が起動する wrapper は、レビュー対象の PR 番号と head SHA を pending attestation として git-dir 直下に書き、subagent lifecycle hook (`auto-mark.sh`) が parent-safe report を検証して final attestation へ昇格します。`gh pr merge` を実行すると、merge gate が final attestation を PR 番号と現在の head SHA の両方と照合し、一致した場合だけ merge を通します。PR に commit を追加すると head SHA が変わるため、記録は自動的に「古いレビュー」となり、再レビューなしには merge が通りません。

Fable review は merge の前提条件ではありません。fable-reviewer subagent の report は親 session に返るだけで、記録は作りません。親 session は findings を分類・対応してから merge に進み、merge gate は Fable review を見ません。

## バージョン

v3.1.0

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install pre-merge-cross-review@natsuume-plugins
```

公式 codex プラグインへの依存があるため、codex review wrapper を動作させるには次も install してください:

```bash
claude plugin install codex@openai-codex
```

review cadence (Codex review 一定回数ごとの checkpoint 強制) の計数は pre-push-codex-review (v4.0.0 以上) の lifecycle hook が担います。計数対象は codex-reviewer だけで、fable-reviewer は計数しません。本 plugin は pre-push-codex-review との同時 install を前提としないため、本 plugin 単独構成 (個人環境) では review cadence は適用されません (明示的な仕様です)。

### 依存コマンド

`jq` と `gh` は merge gate の必須依存です。いずれかが見つからない環境では、未レビューの merge を通さないため `block-pre-merge.sh` が `gh pr merge` を fail-closed に deny し、インストール後の再実行を案内します。merge と無関係な Bash 呼び出しは影響を受けません。

ただし `jq` 不在時はコマンド文字列を取り出せず判定が hook payload 全体に対する粗い文字列フィルタに落ちるため、merge と無関係でも `gh` / `pr` / `merge` に類する文字列を含むコマンドが稀に deny されることがあります (`jq` を install すると解消します)。

## 機能一覧

### Hooks

#### 1. block-pre-merge (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-pre-merge.sh`

次の手順で merge を確認します:

1. Bash コマンド文字列に `gh pr merge` の連続列を含む場合のみ関与する。含まないコマンドには関与しない (無出力)。連続列の検出は粗い文字列判定であり、quoted な言及等で誤爆した場合はコマンドの言い換えで回避できる
2. 関与したコマンドに `--auto` / `--admin` (遅延 merge 予約・保護 bypass) または `--repo` / `-R` (別 repo の PR を merge しうる repo selector) の文字列を含む場合は deny する (粗い文字列検出でよく、レビュー記録の有無に依らない)
3. 関与したコマンドが **受理正規形** `gh pr merge [<number>] [flags...]` に完全一致するかを確認する (照合の前に、コマンド前後の空行・空白・区切りだけは落とす)。番号を置けるのは `gh pr merge` の直後の 1 語だけで、それ以降は長フラグ (`--name` / `--name=value`) と単文字の短フラグ (`-d` 等) のみを許す。短フラグの束ね形 (`-dR` 等) は受理しない (束の中に repo selector を隠すと `--repo` / `-R` の文字列検出をすり抜けるため)。一致しない関与形 — リダイレクト (`> file` / `2>&1` / `10> file` 等)、シェル演算子による連結 (`&&` / `||` / `;` / `|` / `&`)、quote、`$` 展開、フラグより後ろの数字、複数の merge、前置コマンド — は **一律 deny** する (gate の解釈と shell の実挙動が乖離しうるため。リダイレクトや連結を外した単独コマンドへの言い換えで対応する)
4. 照合する repo は hook payload の `cwd` (merge が実行されるディレクトリ) を正本とする。`cwd` の欠落・空・非絶対パス・不在ディレクトリ・移動不能はいずれも deny する (hook プロセスの cwd への fallback は持たない)
5. 対象 PR の解決と、PR 番号・現在の head SHA の取得は gh (`gh pr view --json number,headRefOid`) に委ねる (正規形で番号があればその番号、無ければ current branch の PR)。gh / jq が見つからない・PR を解決できない・取得に失敗した・PR 番号や head SHA が得られない場合はすべて deny する (fail-closed)。gh は取得だけに使い、PR のレビューやコメントは読まず、GitHub には何も書かない
6. merge 実行 repo の git-dir 直下にあるローカルのレビュー記録を検証する。final attestation (`.claude-pre-merge-codex-reviewed`) が symlink でない通常ファイルで、1 行目 `pr=<全数字>` / 2 行目 `head=<40 hex 小文字>` の形式を満たし、PR 番号と現在の head SHA の両方に一致する場合のみ無出力で終了する (既定の許可フローに委ねる)。記録は消さない (merge が失敗して再実行する場合も同じ記録で通り、head SHA が変われば自動的に失効する)
7. 次の場合はいずれも deny する: 記録が無い (pending だけの場合を含む) / git-dir を解決できない / PR 番号を取得できない / 記録が symlink・通常ファイルでない・2 行の形式を満たさない / 記録の PR 番号が merge 対象と異なる / 記録の head SHA が現在の head と異なる (両方の SHA を示して再レビューを案内する)
8. deny 時は `pre-merge-cross-review:codex-reviewer` subagent の実行を案内する。subagent がレビューを実行してローカルに記録を保存し、その後の `gh pr merge` で gate が記録を検証してから merge に進む流れを示す。レビューは current branch の PR に対して実行され記録もその PR に紐づくため、別 PR を番号指定して merge する場合は、先にその PR のブランチへ `git switch` してから subagent を起動する必要がある旨も併せて案内する
9. gate は Fable review を見ない (Fable review の有無は merge の可否に影響しない)
10. gate が出す permissionDecision は deny のみである (allow / updatedInput は出さない。通過時は無出力で既定の許可フローを維持する)

#### 2. block-bg-codex-wrapper (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-bg-codex-wrapper.sh`

codex review wrapper (`run-pre-merge-codex-review.sh`) の起動を検証する PreToolUse hook です。hook payload トップレベルの `agent_type` が `pre-merge-cross-review:codex-reviewer` (namespace 付き完全一致) でなければ fail-closed に deny します。background 起動・pipeline 経由の起動も同様に deny します。wrapper の basename を `pre-push-codex-review` の wrapper (`run-pre-push-codex-review.sh`) と別名にしているのは、両 plugin が併存する環境で互いの wrapper 検出 gate (basename ベース) が相手の wrapper 起動を deny し合う干渉を塞ぐためです。

#### 3. auto-mark (SubagentStart / PostToolUse / SubagentStop / PostToolUseFailure)

**ファイル**: `hooks/scripts/auto-mark.sh`

`pre-merge-cross-review:codex-reviewer` subagent の lifecycle を追跡し、wrapper が書いた pending attestation を final attestation へ昇格する hook です。fable-reviewer は記録を作らないため対象外です。matcher は SubagentStart / SubagentStop が `^pre-merge-cross-review:codex-reviewer$`、PostToolUse が `^SubagentHandback$` (tool 名)、PostToolUseFailure が `Agent|Task` で、script 側でも agent_type / subagent_type を完全一致で再検証します。束縛キーはローカル HEAD の full SHA (`git rev-parse HEAD`) です。

- **SubagentStart**: レビュー開始時のローカル HEAD を launch attestation (`.claude-pre-merge-launch-<agent_id>`) へ、同一ディレクトリ内 temp file + 排他 `ln` で atomic に書きます。tombstone (`.claude-pre-merge-done-<agent_id>`) が既に在る場合と launch attestation が既に在る場合は書きません (resume による attestation の再鋳造を構造的に拒否します)。agent_id が `^[A-Za-z0-9._-]{1,128}$` に一致しない場合はファイル操作を一切行いません。1 日より古い launch attestation は best-effort で掃除し、tombstone は無期限に保持します
- **PostToolUse** (`SubagentHandback`): Claude Code v2.1.271 以降の auto mode では subagent の最終 report が `SubagentHandback` tool の `message` として親に届き、SubagentStop の `last_assistant_message` には締めの文しか入りません。そのため hand-back された report の Status をここで判定し、launch attestation が存在し tombstone が無い場合のみ handback record (`.claude-pre-merge-handback-<agent_id>`) に `pass` / `findings` / `invalid` を書きます。同一 agent_id の 2 回目以降の hand-back は重複 report として `invalid` に上書きします。`tool_response.success` が false (未配信) の hand-back は記録せず、`last_assistant_message` 経路の判定に委ねます
- **SubagentStop**: `stop_hook_active` が boolean false である最初の stop でのみ消費します。launch attestation を tombstone へ不可逆に遷移させたうえで、(1) report (handback record があればその判定結果を one-shot で消費して採用し `last_assistant_message` は見ない。無ければ `last_assistant_message`) に `Status: ` で始まる行がちょうど 1 つあり `^Status: (pass|findings)$` に一致する、(2) launch attestation の HEAD が現在の HEAD と一致する、(3) pending attestation が symlink でない通常ファイルで 2 行の形式を満たし head が現在の HEAD と一致する、をすべて満たす場合のみ pending を `mv` で final へ昇格します。`Status: execution-failed`・Status 行の欠落 / 重複 / 未知値・HEAD 不一致・形式不正はいずれも昇格しません (fail-closed)
- **掃除経路**: launch attestation の無い stop (偽装 stop・resume 後の再 stop)、既存 tombstone、上記検証の不成立、PostToolUseFailure (Agent / Task 呼び出し自体の失敗) では pending attestation を破棄します。昇格済みの final attestation には触れません (完走したレビューの記録を後続の stop や失敗イベントが壊さないため)
- 環境要因の失敗 (jq / git が無い、path を解決できない等) は silent skip (exit 0) で、レビュー完了の証明だけを fail-closed に扱います

#### 4. inject-merge-order-rules (SessionStart)

**ファイル**: `hooks/scripts/inject-merge-order-rules.sh`

`hooks/prompts/merge-order-rules.md` の全文を毎セッションの SessionStart で additionalContext として注入します。注入文は、PR のマージ前提条件を確認した後・`gh pr merge` を実行する前に、Fable 週次枠の判定コマンドを実行し、codex-reviewer subagent と (判定が `available` のとき) fable-reviewer subagent を同一メッセージで並列に起動する手順、各 reviewer の起動 prompt の定型文、`--delete-branch` を付けない merge の形、起動が classifier に拒否された場合に `AskUserQuestion` でユーザの許可を得る手順を定めます (背景は「auto mode での利用」節)。permission mode やモデルによる分岐はなく常に同一内容を注入します。`jq` 不在・prompt ファイルの欠落・空・読み取り不能のいずれでも無出力で exit 0 とし (fail-open)、セッションを壊しません。

### Hooks module (Claude Mods)

**ファイル**: `hooks/module/register.ts` (エントリ)、`hooks/module/tool-check-policy.mjs` (判定ロジック)

`hooks/hooks.json` の `"modules"` で宣言する hooks module です。auto mode で、merge gate を通過した単独の `gh pr merge` と、単独の `gh pr view` / `gh pr checks` を `tool.check` イベントで allow に引き上げ、auto mode classifier の判定を経ずに実行させます。背景は「auto mode での利用」節を参照してください。

`tool.check` では下位の判定 (permissions ルール・classic PreToolUse hook を含む) を先に得て、次の **すべて** を満たすときだけ `allow` を返します。それ以外は下位の判定をそのまま返します。

1. 下位の判定が `ask` で、settings のルールによるものではない。`deny` (merge gate の deny・`permissions.deny` を含む)、`allow`、`permissions.ask` ルールによる `ask` は変更しません
2. 現在の permission mode が `auto` である。mode が分からないときは引き上げません
3. Bash tool の command が、次のいずれかの正規形の単独呼び出しである
   - `gh pr merge [<番号>] <--squash|--merge|--rebase>` (戦略フラグはちょうど 1 つ。他の引数を含まない)
   - `gh pr view [<番号>|<branch>]` に `--json <fields>` / `--jq <式>` / `-q <式>` / `--comments` / `-c` を付けたもの
   - `gh pr checks [<番号>|<branch>]` に `--json <fields>` / `--jq <式>` / `-q <式>` / `--watch` / `--interval <秒>` / `-i <秒>` / `--required` / `--fail-fast` を付けたもの

`<fields>` は英数字・`_`・`,`、`<秒>` は整数、`<式>` はシングルクォートで囲んだ 1 語か英数字・`_`・`.` だけの語、`<branch>` は英数字・`.`・`_`・`/`・`-` だけの語 (先頭は `-` 以外) に限ります。`-R` / `--repo`・URL・`:` を含む指定は、別ホストへの通信になりうるため対象外です。quote の内側を含めて `;` `&` `|` `<` `>` バッククォート `$(` `${` 改行を含む command、シングルクォートの外に `$` `"` `\` を含む command、`gh` の前に env 代入・ラッパー (`env` / `bash -c` / `eval` / `xargs` 等) がある command も対象外です。対象外の command は従来どおり classifier の審査を受けます。

merge gate (`block-pre-merge.sh`) は classic PreToolUse として `tool.check` より先に評価され、その deny は `tool.check` の下位判定として渡されます。module は deny を上書きしないため、codex review の記録が無い merge は従来どおり gate の deny で止まります。module 自身は review 記録を検証しません。

`tool.check` の入力は permission mode を持たないため、permission mode を持つ classic イベント (SessionStart / UserPromptSubmit / PostToolUse / PostToolUseFailure) の入力から最新の mode を記録して使います。

**読み込まれる条件**: Claude Code プロセスの env に `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` があり、workspace trust を承諾済みであることが必要です。managed settings の `disableAllHooks` / `allowManagedHooksOnly`、`--bare`、Safe mode では読み込まれません。読み込まれない環境でも、本 plugin の他の機能は従来どおり動作します。env は次の setup skill で設定できます。

### Setup skill

**ファイル**: `skills/setup/SKILL.md`、`bin/pre-merge-cross-review-enable-function-hooks`

`/pre-merge-cross-review:setup` は、user settings (`${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json`) の `env` に `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS: "1"` を書き込みます。既存の他のキーは保持し、既に設定済みなら書き込みません。settings.json が JSON として解析できない・top-level が object でない・`env` が object でない場合は書き込まずに中止します。設定は次に起動する Claude Code から有効になります。

### Fable 週次枠の判定コマンド

**ファイル**: `bin/pre-merge-cross-review-fable-usage`

fable-reviewer を起動するかを Fable 週次枠の使用率から判定するコマンドです。plugin の `bin/` に置くため、plugin が有効な間は Bash の PATH に載り、注入文の手順に従って main session が reviewer を起動する前に 1 回実行します。出力は常に 2 行で exit 0 です。1 行目は `available` (fable-reviewer を起動する) / `over` / `unknown` (どちらも Fable review をスキップする)、2 行目は判定理由です。

判定は `hooks/scripts/lib/fable-weekly-usage.sh` に委ねます。natsuume-statusline が書く使用率 cache (`${XDG_CACHE_HOME:-$HOME/.cache}/natsuume-statusline/weekly-scoped.json`) を読むだけで書き込まず、Fable の週次枠の使用率が閾値 (env `FABLE_WEEKLY_MAX_PERCENT`、既定 80) 以下なら `available` です。cache が無い・古い・読めない場合と lib を読み込めない場合は `unknown` です。

### ローカル記録

ローカルの記録は merge 実行 repo の git-dir 直下に置かれ、次の 5 種類です (名前の単一ソースは `hooks/scripts/lib/markers.sh`)。GitHub には何も書きません:

| ファイル | 書き手 | 役割 |
|---|---|---|
| `.claude-pre-merge-codex-reviewed` | auto-mark (SubagentStop) | final attestation。gate はこれが PR 番号と現在の head SHA に一致するかを検証する |
| `.claude-pre-merge-codex-reviewed.pending` | wrapper | pending attestation。report 検証を通ると final へ昇格する |
| `.claude-pre-merge-launch-<agent_id>` | auto-mark (SubagentStart) | レビュー開始時のローカル HEAD |
| `.claude-pre-merge-done-<agent_id>` | auto-mark (SubagentStop) | attestation を消費した記録 (one-shot 保証。無期限に保持する) |
| `.claude-pre-merge-handback-<agent_id>` | auto-mark (PostToolUse) | `SubagentHandback` で hand-back された report の Status 判定結果 (`pass` / `findings` / `invalid`)。SubagentStop が消費する |

pending / final attestation の内容は次の 2 行で、昇格は rename のみで内容を書き換えません:

```
pr=<PR 番号>
head=<レビュー対象の full head SHA (40 hex 小文字)>
```

- 記録はレビュー完了の記録であり、merge の approve や findings 0 件の証明ではない (findings があっても「レビュー済み」として成立する。findings への対応判断は review report を受け取った親 session が行う)
- review report の本文はファイルに残さない (codex-reviewer が parent-safe report として親 session に返す)

ファイル名の prefix はすべて `.claude-pre-merge-` で、`pre-push-codex-review` が使う `.claude-pre-push-*` とは衝突しません。

### Agents

#### `pre-merge-cross-review:codex-reviewer` (subagent)

**ファイル**: `agents/codex-reviewer.md`

codex review wrapper (`hooks/scripts/run-pre-merge-codex-review.sh`) を foreground で 1 回起動し、wrapper の stdout / stderr を subagent context 内で評価して parent-safe markdown report に抽象化する最小 subagent です。wrapper は **current branch の PR** を gh で解決し、その PR の実 base との merge-base..head 全差分に対して codex review を実行して、完了時に pending attestation を git-dir 直下に書きます (GitHub には何も書きません。ローカル HEAD が PR の head SHA と一致しない場合は、記録する head SHA と実際にレビューした内容が食い違うため実行せず中断します)。別の PR をレビューさせたい場合は、その PR のブランチへ `git switch` してから起動してください。レビュー範囲と対象内容は wrapper 側で束縛します:

- **working tree が dirty なら中断**: codex のレビューは working tree を含む差分を見るため、未コミット変更があると head SHA を記録しながら別内容をレビューすることになります (commit / stash を案内します)
- **base の妥当性**: PR が記録する base commit (`baseRefOid`) がローカルの `origin/<base>` から到達可能 (ancestor) であることを確認します。到達不能なら 1 度だけ明示 refspec (`git fetch origin +refs/heads/<base>:refs/remotes/origin/<base>`) で fetch して再判定し、それでも到達不能ならレビューも記録の書き込みも行いません。base branch は PR 作成後も進むため完全一致は要求せず、レビュー範囲の anchor は `git merge-base HEAD origin/<base>` (GitHub の PR diff と同じ範囲) を使います
- **空 diff の中断**: merge-base..HEAD の差分が空の場合も、何も見ていない「レビュー済み」記録を残さないため中断します
- **起動時の stale 記録の掃除**: 実行開始時に git-dir 直下の final / pending attestation を削除します (前回の中断が残した記録で merge が通る経路を残さないため。削除できない場合は中断します)
- **記録を書く直前の再検証**: codex review 完了後・記録を書く前に HEAD と working tree の状態を再確認し、レビュー実行中に変化していれば記録を書かず中断します (レビューした内容と記録する head SHA の乖離を残さないため)

tools は `Bash, Read` に制限され (`Read` は Bash timeout による background 移行後の回収専用で、wrapper が書く terminal sentinel とその run の output file だけを読みます)、model は `sonnet` に固定されます。

wrapper が exit 0 で完了した場合、parent-safe report の `Status` は **Codex の report 本文** から決めます (wrapper が非 0 で終了した場合は本文の内容に関わらず `Status: execution-failed` です)。本文に finding の記述 (`## Finding` 節・`Severity:` 行・番号付き / 箇条書きの個別指摘) が 1 つも無く「指摘なし」の趣旨で結ばれている場合は `Status: pass` / `Findings: 0` を返し、個別の指摘が 1 つでもあれば `Status: findings` を返します。finding として返せるのは Codex の report 本文に存在する指摘のみで、wrapper の挙動・記録の書き込みの成否・subagent 自身の観測範囲の限界は finding にしません (`Status: execution-failed` の Failure class で表現します)。本文が finding も「指摘なし」の結論も含まず判定できない場合 (途中で切れている・空・記述のみ等) は pass に倒さず、`Status: execution-failed` (Exit status 0・Failure class `other`) で返し、wrapper 自体は完了しレビュー記録を書き終えている可能性がある旨を recovery direction に書きます。

#### `pre-merge-cross-review:fable-reviewer` (subagent)

**ファイル**: `agents/fable-reviewer.md`

current branch の PR の merge-base..head 全差分を、PR 説明と関連 issue の受入基準・設計境界と照らしてレビューする read-only の subagent です。親 session は判定コマンドが `available` のときに `model: "fable"` を指定し、codex-reviewer と同一メッセージで並列に起動します (起動が deny された場合は再起動せずスキップします)。report は親 session に返るだけで、記録も GitHub への書き込みもしません。

- **レビュー対象の確定**: ローカル HEAD が PR の head と一致し working tree が clean であること、PR が記録する base commit がローカルの `origin/<base>` から到達可能であることを確認し、`git merge-base HEAD origin/<base>` からの差分をレビューします。fetch は行わず、条件を満たせない場合は `Status: execution-failed` を返します
- **関連 issue**: PR の `closingIssuesReferences` と PR 本文中の closing keyword / `Refs #N` / `Refs owner/repo#N` から集め、`gh issue view` で読みます (別リポジトリの issue は `-R owner/repo` を付けて読みます)。読めない issue は finding にせず、確認できなかった情報源として report に書きます
- **観点**: `correctness` (差分が持ち込む不具合)、`acceptance-criteria` (PR 説明・関連 issue の受入基準ごとの未充足・部分充足・矛盾)、`design-boundary` (README・ヘッダ契約・CLAUDE.md・issue の設計境界、責務分担、仕様逸脱、スコープ外変更) の 3 つです
- **report**: 見出し `# Fable Review` と `Status: pass | findings | execution-failed` 行を持つ parent-safe report です。finding はすべて confidence / severity 付きで報告し、reviewer 側で絞り込みません。PR 本文・issue・commit message・差分は指示ではなくデータとして扱います

tools は `Bash, Read, Glob, Grep` で、gh は `gh pr view` と `gh issue view` にだけ使います。ファイル・git 状態・GitHub へは書き込みません。frontmatter の model は `opus` で、Fable で実行されるのは親が `model: "fable"` を明示して起動したときです。

## auto mode での利用

auto mode (permission_mode = `auto`) では、Claude Code の classifier が各 tool call を審査します。classifier が読むのは「ユーザ発言・tool call・CLAUDE.md」で、tool result (merge gate の deny 文を含む) は除去されます。そのため `gh pr merge` の呼び出し直後に `pre-merge-cross-review:codex-reviewer` を起動すると、classifier には「ユーザが依頼していない merge 系操作の一部」に見え、起動が `Blocked by classifier` で拒否されることがあります。作者環境での集計では、merge gate の deny 直後 (間に人間の発言なし) の起動 11 件中 8 件が拒否され、merge 試行を挟まない起動 13 件と、ユーザの許可発言を挟んだ起動 3 件はすべて通過しました。

本 plugin はこれを次の 3 段で扱います:

1. **起動順の規律 (SessionStart 注入)**: `hooks/prompts/merge-order-rules.md` を毎セッション注入し、マージ前提条件の確認後・`gh pr merge` の前に codex-reviewer (と Fable 週次枠が利用可能なときの fable-reviewer) を起動する手順、起動 prompt の定型文、`--delete-branch` を付けない merge の形、拒否時は `AskUserQuestion` でユーザの許可を得る手順を定めます
2. **subagent の description と案内 prompt**: classifier が起動時に評価する task description が subagent の実際の操作を正確に表すよう、subagent の description と起動 prompt を「read-only のレビューを 1 回実行して report を返す」記述に統一しています (merge の可否を判定するのは gate であり、subagent ではありません)
3. **classifier の allow 設定 (任意)**: 恒久的に解消するには、`~/.claude/settings.json` の `autoMode.allow` に次のルールを追加します。`"$defaults"` を残さないと組み込みルールが失われるので必ず併記してください

```json
{
  "autoMode": {
    "allow": [
      "$defaults",
      "Launching the pre-merge-cross-review:codex-reviewer or pre-merge-cross-review:fable-reviewer subagent while preparing to merge the current branch's pull request is allowed, including immediately after a gh pr merge attempt: each only runs a read-only review of the pull request diff and returns a report; the codex review result is kept as a local record under the repository's .git directory. They do not write to GitHub, merge, push, or delete anything."
    ]
  }
}
```

この allow ルールが緩めるのは subagent の**起動**の審査だけです。subagent が実行中に行う各 tool call は引き続き classifier が親 session と同じ規則で審査し、完了時にも行動履歴が審査されます。

classifier は project settings (`.claude/settings.json` / `.claude/settings.local.json`) の `autoMode` を読まないため、ユーザ設定 (`~/.claude/settings.json`) に書く必要があります。classifier は CLAUDE.md も読むため、プロジェクトの CLAUDE.md に同趣旨の 1 文を書く方法でも代替できます。設定なしで拒否された場合は、ユーザが「マージ前レビューとマージを実行してよい」と発言すれば次の起動は通ります (classifier は明示的なユーザ意図で soft block を解除します)。

`gh pr merge` 自体や、merge 直後の `gh pr view` も classifier に `[Merge Without Review]` で拒否されることがあります。codex review の結果は subagent の report として届くため、classifier からは review 済みであることが見えません。hooks module (「Hooks module (Claude Mods)」節) を有効にすると、merge gate を通過した単独の `gh pr merge` と、単独の `gh pr view` / `gh pr checks` は classifier を経ずに実行されます。hooks module を有効にしていない環境では、ユーザ自身がマージを指示する発言をすると、次の実行は通ります。

`--delete-branch` は remote branch の削除として組み込み soft_deny の対象になるため、merge は `gh pr merge <番号> --squash` 等の単独正規形で実行し、branch の掃除は merge 後に別コマンドで行ってください。hooks module も戦略フラグ以外の引数を含む merge は allow に引き上げません。

## 既知の制約

- **レビュー済みの証拠はローカル記録**: gate が見るのは merge 実行 repo の git-dir 直下の記録だけです。別マシン・別 clone・記録を消した環境から merge する場合は、そこで codex-reviewer を起動して再レビューする必要があります。記録は PR 上に残らないため、他の人がレビュー済みかを PR から確かめることもできません
- **ローカル記録の偽装は防がない**: final attestation を手で置く等の偽装は防ぎません (cooperative 利用前提)。lifecycle hook の検証 (Status 行・HEAD 一致・one-shot 消費) が守るのは「subagent が実際に完走したレビューだけを昇格する」ことであり、ファイルを直接書ける利用者に対する防御ではありません
- **gate の観測範囲は Bash tool の `gh pr merge` (連続列を含む形) のみ**: `gh api` による直接 merge 呼び出し、gh alias、意図的な難読化、非 Bash の tool 経路、Web UI や他 client からの merge は観測できません
- **TOCTOU 窓は防がない**: gate 確認後から実 merge までの間に head が更新される競合窓は防ぎません (ローカル記録の SHA は gate 確認時点の head と照合されます)
- **`--auto` / `--admin` は常に deny**: 遅延 merge 予約 (gate 確認と実 merge の分離) と保護 bypass はサポート外です。必要な場合は plugin を無効化して実行してください
- **hooks module は early access の API に依存する**: Claude Mods (function hooks) の API は Claude Code のリリース間で予告なく変わりえます。`tool.check` の allow が classifier の判定を省略する挙動は Claude Code 2.1.282 の実装で確認したもので、公式ドキュメントには記述がありません
- **hooks module が参照する permission mode は直近の classic イベント時点の値**: ターンの途中で permission mode を切り替えた直後の 1 回のツール呼び出しには、切り替え前の mode が使われます
- **粗い検出による誤爆**: `gh pr merge` の連続列を quoted な文字列として含むだけのコマンド (コミットメッセージへの言及等) も関与対象になります。誤 deny された場合はコマンドを言い換えて回避してください
- **連続列判定はフラグ介在形に一致しない**: サブコマンドの語間にフラグが入る呼び出し形 (`gh -R owner/repo pr merge 123` 等) は `gh pr merge` の連続列を含まないため gate が関与せず、この形の merge は観測できません。別 repo の PR を merge する場合はその repo のディレクトリへ移動し、`gh pr merge` を先頭に置いた単独コマンドとして番号指定 (`gh pr merge 123`) か current branch 指定 (`gh pr merge --squash`) で実行してください (repo selector 付きの形は gate が deny します)
- **レビュー記録は current branch の PR にのみ紐づく**: codex-reviewer subagent が実行する wrapper は current branch の PR を対象にレビューし、その PR 番号を記録に書きます。別 PR を番号指定した merge が deny されたときは、先にその PR のブランチへ `git switch` してから subagent を起動してください (別ブランチのまま起動すると、記録が current branch の PR のものになり、codex の利用枠だけを消費して目的の merge は deny のままになります)
- **base 変更 (retarget) は失効として検出しない**: レビュー記録の照合は PR 番号と head SHA のみで行います。PR の base branch を変更しても head SHA は変わらないため、レビュー対象の差分 (merge-base..head) が変わっても既存のレビュー記録は有効なまま扱われます
- **受理正規形の外側は拡張しない (stop rule)**: gate が解釈するのは `gh pr merge [<number>] [flags...]` の単独正規形だけです。正規形外の形 (リダイレクト・連結・quote・変数展開等) を許可する parser 拡張は行いません。「除去して近似する」処理は shell の実挙動との乖離を生み、未レビュー merge を通す穴になるためで、必要な操作は単独コマンドへの言い換えで対応してください
- **照合 repo は hook payload の `cwd` に従う**: gate は payload の `cwd` が指す repo で PR とレビュー記録を照合します。payload の `cwd` が実際にコマンドを実行する shell の cwd と乖離する環境では、gate は payload 側の repo を照合し、その乖離自体は検出できません
- **remote `origin` = PR の repository が前提 (fork 構成は非対応)**: wrapper はレビュー範囲の base をローカルの `origin/<base>` で解決するため、remote `origin` が PR の属する repository を指す個人環境を前提とします。fork からの PR (origin と PR の repository が異なる構成) には対応しません
- **merge queue による暗黙の遅延 merge は検出しない**: merge queue が有効な base branch では `--auto` を付けなくても merge が queue 経由の遅延実行になりえますが、gate はこれを検出しません (遅延 merge はサポート外です)
- **Fable で実行されたかは検証しない**: fable-reviewer が実際に Fable で実行されたかを hook は検証しません (cooperative 利用前提)。親が `model: "fable"` を指定せずに起動した場合も、report は Fable review として親 session に返ります
- **PowerShell tool / Monitor tool 経由の `gh pr merge` は観測しない**: PreToolUse hook の matcher が `Bash` であるため、PowerShell tool (`CLAUDE_CODE_USE_POWERSHELL_TOOL=1` で Linux / macOS でも有効化できる) および Monitor tool 経由で発行された `gh pr merge` を gate は観測しません。これらの tool を有効にした環境はサポート外です

## pre-push-review / pre-push-codex-review との併用設計

本 plugin は単独 install で自立動作し、`pre-push-review` core (`git push` 前の code review / security review gate) と併用しても push gate に一切影響しません。push gate (`git push`) と merge gate (`gh pr merge`) は独立した PreToolUse hook であり、互いの判定に関知しません。

本 plugin は個人環境 (ChatGPT Plus の codex CLI) 向けに「merge 前に 1 回だけ codex review」を運用する設計です。push の都度 codex review を要求する会社環境向け `pre-push-codex-review` との併用は前提としていません。会社環境では codex 系 2 plugin のうち `pre-push-codex-review` の側を install し、本 plugin は install しないでください (`pre-push-review` core は会社環境でもそのまま併用します)。

## 共有 lib の同一性

本 plugin は次の 2 つの lib を、canonical の byte-identical なコピーとして保持します。この同一性は `tests/test_pre_merge_lib_copies.py` の契約テストが検査します。

- `hooks/scripts/lib/codex-companion-resolver.sh`: `pre-push-codex-review` (`plugins/pre-push-codex-review/hooks/scripts/lib/`) が canonical (codex review の実行機構は両 plugin で同一のため)
- `hooks/scripts/lib/fable-weekly-usage.sh`: `cross-model-advisor` (`plugins/cross-model-advisor/scripts/lib/`) が canonical (Fable 週次枠の使用率判定は、Fable を使う subagent を起動するかを決める plugin 間で同一のため)

それ以外の lib コピーは持ちません。reviewer 一式 (wrapper / subagent 定義 / hook script 群) は pre-merge 専用の実装であり、pre-push 系との文字列同一性契約は設けません。`lib/markers.sh` (レビュー記録のファイル名と内容契約) も pre-merge 専用の lib であり、この同一性契約の対象外です (`lib/markers.sh` は pre-push 系の同名 lib とは別の名前空間・別の束縛キーを扱います)。

## ファイル構成

| パス | 役割 |
|---|---|
| `hooks/hooks.json` | フック配送経路の定義 |
| `hooks/scripts/block-pre-merge.sh` | 軽量 merge gate 本体 (PreToolUse)。ローカルのレビュー記録を PR 番号と head SHA に照合する |
| `hooks/scripts/block-bg-codex-wrapper.sh` | codex review wrapper の起動検証 (PreToolUse) |
| `hooks/scripts/auto-mark.sh` | subagent lifecycle hook (SubagentStart / PostToolUse / SubagentStop / PostToolUseFailure)。codex review の pending attestation を final へ昇格する |
| `hooks/scripts/inject-merge-order-rules.sh` | SessionStart hook。merge 前 cross review の起動順規律を additionalContext として注入する |
| `hooks/prompts/merge-order-rules.md` | 注入する起動順規律の本文 |
| `hooks/scripts/run-pre-merge-codex-review.sh` | codex review wrapper 本体 (レビュー実行 + ローカル記録の書き込み。basename は `pre-push-codex-review` の wrapper と別名) |
| `hooks/scripts/lib/codex-companion-resolver.sh` | codex companion 解決ロジック (`pre-push-codex-review` からの byte-identical コピー) |
| `hooks/scripts/lib/fable-weekly-usage.sh` | Fable 週次枠の使用率判定 (`cross-model-advisor` からの byte-identical コピー) |
| `hooks/scripts/lib/markers.sh` | レビュー記録のファイル名と内容契約の単一ソース |
| `agents/codex-reviewer.md` | `pre-merge-cross-review:codex-reviewer` subagent 定義 |
| `agents/fable-reviewer.md` | `pre-merge-cross-review:fable-reviewer` subagent 定義 |
| `bin/pre-merge-cross-review-fable-usage` | Fable 週次枠の判定コマンド (fable-reviewer を起動するか) |
| `hooks/module/register.ts` | hooks module のエントリ。permission mode を記録し、tool.check で判定ロジックを呼ぶ |
| `hooks/module/tool-check-policy.mjs` | tool.check の判定ロジック (純関数) |
| `skills/setup/SKILL.md` | hooks module を有効化する setup skill |
| `bin/pre-merge-cross-review-enable-function-hooks` | user settings の env に `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` を書き込むコマンド |

## 関連プラグイン

- [pre-push-review](../pre-push-review/): `git push` 前の push gate (code review / security review の 2 マーカー)。本 plugin の merge gate とは独立に動作し、併用しても互いの gate に影響しません
- [pre-push-codex-review](../pre-push-codex-review/): 会社環境向けの push 毎 codex review gate。本 plugin と同時に install しない前提です。review cadence の計数対象に `pre-merge-cross-review:codex-reviewer` の namespace を含みます (v4.0.0 以上。fable-reviewer は計数しません) が、本 plugin 単独構成では review cadence 自体が適用されません
- [cross-model-advisor](../cross-model-advisor/): Fable 週次枠の使用率判定 lib (`fable-weekly-usage.sh`) の canonical を持ちます
