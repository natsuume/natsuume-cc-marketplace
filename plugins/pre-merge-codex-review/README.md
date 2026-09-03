# pre-merge-codex-review プラグイン

`gh pr merge` を実行する前に **codex review** (OpenAI クロスモデルレビュー) の完了を確認し、未レビューな PR が merge されるのを防ぐプラグインです。個人環境 (ChatGPT Plus の codex CLI) 向けに、`git push` の都度ではなく **merge 前に 1 回だけ** codex review を行う運用を成立させます。単独 install で自立動作します。

レビュー済みの証拠の正本は **GitHub 上の PR レビューコメント** であり、**それを投稿するのは merge gate** です。codex-reviewer subagent が起動する wrapper は、レビュー結果を「レビュー時の head SHA を記録した機械可読 header 付きの本文」と pending attestation として repo の git-dir 直下 (ローカル) に書くだけで投稿せず、subagent lifecycle hook (`auto-mark.sh`) が parent-safe report を検証して final attestation へ昇格します。`gh pr merge` を実行すると merge gate が「PR に現在の head SHA と一致する codex review コメントが在るか」を確認し、無ければローカルの記録を検証して PR に投稿してから merge を通します。PR に commit を追加すると head SHA が変わるため、コメントもローカルの記録も自動的に「古いレビュー」となり、再レビューなしには merge が通りません。

## バージョン

v2.1.0

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install pre-merge-codex-review@natsuume-plugins
```

公式 codex プラグインへの依存があるため、codex review wrapper を動作させるには次も install してください:

```bash
claude plugin install codex@openai-codex
```

review cadence (Codex review 一定回数ごとの checkpoint 強制) の計数は pre-push-codex-review (v2.0.0 以上) の lifecycle hook が担います。本 plugin は pre-push-codex-review との同時 install を前提としないため、本 plugin 単独構成 (個人環境) では review cadence は適用されません (明示的な仕様です)。

### 依存コマンド

`jq` と `gh` は merge gate の必須依存です。いずれかが見つからない環境では、未レビューの merge を通さないため `block-pre-merge.sh` が `gh pr merge` を fail-closed に deny し、インストール後の再実行を案内します。merge と無関係な Bash 呼び出しは影響を受けません。

ただし `jq` 不在時はコマンド文字列を取り出せず判定が hook payload 全体に対する粗い文字列フィルタに落ちるため、merge と無関係でも `gh` / `pr` / `merge` に類する文字列を含むコマンドが稀に deny されることがあります (`jq` を install すると解消します)。

## 機能一覧

### Hooks

#### 1. block-pre-merge (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-pre-merge.sh`

次の手順で merge を確認します:

1. Bash コマンド文字列に `gh pr merge` の連続列を含む場合のみ関与する。含まないコマンドには関与しない (無出力)。連続列の検出は粗い文字列判定であり、quoted な言及等で誤爆した場合はコマンドの言い換えで回避できる
2. 関与したコマンドに `--auto` / `--admin` (遅延 merge 予約・保護 bypass) または `--repo` / `-R` (別 repo の PR を merge しうる repo selector) の文字列を含む場合は deny する (粗い文字列検出でよく、レビューコメントの有無に依らない)
3. 関与したコマンドが **受理正規形** `gh pr merge [<number>] [flags...]` に完全一致するかを確認する (照合の前に、コマンド前後の空行・空白・区切りだけは落とす)。番号を置けるのは `gh pr merge` の直後の 1 語だけで、それ以降は長フラグ (`--name` / `--name=value`) と単文字の短フラグ (`-d` 等) のみを許す。短フラグの束ね形 (`-dR` 等) は受理しない (束の中に repo selector を隠すと `--repo` / `-R` の文字列検出をすり抜けるため)。一致しない関与形 — リダイレクト (`> file` / `2>&1` / `10> file` 等)、シェル演算子による連結 (`&&` / `||` / `;` / `|` / `&`)、quote、`$` 展開、フラグより後ろの数字、複数の merge、前置コマンド — は **一律 deny** する (gate の解釈と shell の実挙動が乖離しうるため。リダイレクトや連結を外した単独コマンドへの言い換えで対応する)
4. 照合する repo は hook payload の `cwd` (merge が実行されるディレクトリ) を正本とする。`cwd` の欠落・空・非絶対パス・不在ディレクトリ・移動不能はいずれも deny する (hook プロセスの cwd への fallback は持たない)
5. 対象 PR の解決・番号・head SHA の取得は gh に委ねる (正規形で番号があればその番号、無ければ current branch の PR)。PR 上のレビューコメントのうち、**本文の先頭行**が機械可読 header (`<!-- codex-review: head=<full head SHA> status=pass|findings -->`) で始まり head SHA が完全一致するものが存在すれば無出力で終了する (既定の許可フローに委ねる。同じ PR 番号・head SHA のローカル記録が残っていれば best-effort で掃除する)。gh / jq が見つからない・PR を解決できない・取得や照合に失敗した・head SHA が得られない場合はすべて deny する (fail-closed)
6. 一致コメントが無い場合は、merge 実行 repo の git-dir 直下にあるローカルのレビュー記録を検証する。final attestation (`.claude-pre-merge-codex-reviewed`) が symlink でない通常ファイルで、1 行目 `pr=<全数字>` / 2 行目 `head=<40 hex 小文字>` の形式を満たし、PR 番号と現在の head SHA の両方に一致し、投稿用本文 (`.claude-pre-merge-codex-comment.md`) が通常ファイルとして在り、その先頭行が同じ head SHA の header (`status=pass` または `status=findings`) に完全一致する場合のみ、`gh pr review <番号> --comment --body-file <本文>` で投稿する。投稿に成功したら final / pending / 本文を掃除して無出力で終了し (既定の許可フローに委ねる)、投稿に失敗したら記録を保持したまま deny する (`gh pr merge` の再実行で投稿を再試行する)
7. 次の場合はいずれも deny する: 記録が無い (pending だけの場合を含む) / git-dir を解決できない / PR 番号を取得できない / 記録が symlink・通常ファイルでない・2 行の形式を満たさない / 記録の PR 番号が merge 対象と異なる / 記録の head SHA が現在の head と異なる (両方の SHA を示して再レビューを案内する) / 投稿用本文が無い・先頭行の header が現在の head SHA と一致しない
8. deny 時は `pre-merge-codex-review:codex-reviewer` subagent の実行を案内する。subagent がレビューを実行してローカルに記録を保存し、その後の `gh pr merge` で gate が記録を投稿してから merge に進む流れを示す。レビューは current branch の PR に対して実行され記録もその PR に紐づくため、別 PR を番号指定して merge する場合は、先にその PR のブランチへ `git switch` してから subagent を起動する必要がある旨も併せて案内する
9. gate が出す permissionDecision は deny のみである (allow / updatedInput は出さない。通過時は無出力で既定の許可フローを維持する)

#### 2. block-bg-codex-wrapper (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-bg-codex-wrapper.sh`

codex review wrapper (`run-pre-merge-codex-review.sh`) の起動を検証する PreToolUse hook です。hook payload トップレベルの `agent_type` が `pre-merge-codex-review:codex-reviewer` (namespace 付き完全一致) でなければ fail-closed に deny します。background 起動・pipeline 経由の起動も同様に deny します。wrapper の basename を `pre-push-codex-review` の wrapper (`run-pre-push-codex-review.sh`) と別名にしているのは、両 plugin が併存する環境で互いの wrapper 検出 gate (basename ベース) が相手の wrapper 起動を deny し合う干渉を塞ぐためです。

#### 3. auto-mark (SubagentStart / SubagentStop / PostToolUseFailure)

**ファイル**: `hooks/scripts/auto-mark.sh`

`pre-merge-codex-review:codex-reviewer` subagent の lifecycle を追跡し、wrapper が書いた pending attestation を final attestation へ昇格する hook です。matcher は SubagentStart / SubagentStop が `^pre-merge-codex-review:codex-reviewer$`、PostToolUseFailure が `Agent|Task` で、script 側でも agent_type / subagent_type を完全一致で再検証します。束縛キーはローカル HEAD の full SHA (`git rev-parse HEAD`) です。

- **SubagentStart**: レビュー開始時のローカル HEAD を launch attestation (`.claude-pre-merge-launch-<agent_id>`) へ、同一ディレクトリ内 temp file + 排他 `ln` で atomic に書きます。tombstone (`.claude-pre-merge-done-<agent_id>`) が既に在る場合と launch attestation が既に在る場合は書きません (resume による attestation の再鋳造を構造的に拒否します)。agent_id が `^[A-Za-z0-9._-]{1,128}$` に一致しない場合はファイル操作を一切行いません。1 日より古い launch attestation は best-effort で掃除し、tombstone は無期限に保持します
- **SubagentStop**: `stop_hook_active` が boolean false である最初の stop でのみ消費します。launch attestation を tombstone へ不可逆に遷移させたうえで、(1) `last_assistant_message` に `Status: ` で始まる行がちょうど 1 つあり `^Status: (pass|findings)$` に一致する、(2) launch attestation の HEAD が現在の HEAD と一致する、(3) pending attestation が symlink でない通常ファイルで 2 行の形式を満たし head が現在の HEAD と一致する、(4) 投稿用本文が通常ファイルで先頭行が同じ head の header である、をすべて満たす場合のみ pending を `mv` で final へ昇格します。`Status: execution-failed`・Status 行の欠落 / 重複 / 未知値・HEAD 不一致・形式不正はいずれも昇格しません (fail-closed)
- **掃除経路**: launch attestation の無い stop (偽装 stop・resume 後の再 stop)、既存 tombstone、上記検証の不成立、PostToolUseFailure (Agent / Task 呼び出し自体の失敗) では pending attestation を破棄します。**投稿用本文は final attestation が存在しない場合にのみ削除します** — final と本文は gate が投稿に使う対であり、昇格済みの対を後続の stop や失敗イベントが壊さないためです。昇格に成功した場合も本文は残します (gate が `--body-file` として使います)
- 環境要因の失敗 (jq / git が無い、path を解決できない等) は silent skip (exit 0) で、レビュー完了の証明だけを fail-closed に扱います

#### 4. inject-merge-order-rules (SessionStart)

**ファイル**: `hooks/scripts/inject-merge-order-rules.sh`

`hooks/prompts/merge-order-rules.md` の全文を毎セッションの SessionStart で additionalContext として注入します。注入文は、PR のマージ前提条件を確認した後・`gh pr merge` を実行する前に codex-reviewer subagent を起動する手順、起動 prompt の定型文、`--delete-branch` を付けない merge の形、起動が classifier に拒否された場合に `AskUserQuestion` でユーザの許可を得る手順を定めます (背景は「auto mode での利用」節)。permission mode やモデルによる分岐はなく常に同一内容を注入します。`jq` 不在・prompt ファイルの欠落・空・読み取り不能のいずれでも無出力で exit 0 とし (fail-open)、セッションを壊しません。

### レビューコメント形式とローカル記録

wrapper はレビュー完了時に投稿用の本文をローカル (git-dir 直下) に書き、merge gate が `gh pr review --comment --body-file` で対象 PR の PR レビューとして投稿します。本文は次の形式です:

```
<!-- codex-review: head=<レビュー対象の full head SHA> status=pass|findings -->
# Codex Review

(findings の markdown report)
```

- header の `head=` はレビュー対象となった PR head の full SHA (40 hex)
- `status=pass|findings` は `lib/review-status.sh` の `detect_review_status` が判定する。
  report 全体に finding 記述 (`## Finding` / `- Severity:` / 前後が英数字でない
  `P0`〜`P3`) が 1 行でもあれば `findings`。無ければ report の末尾 10 行について
  各行を markdown 装飾・強調記号の除去と小文字化で正規化し、「no findings」「no
  material/actionable findings」「no issues found/identified」「no
  regression(s) found/identified」や `no (material|actionable|significant)
  issue(s)/regression(s)/findings` といった表現に行全体または行末が一致する行が 1 つでも
  あれば `pass`、無ければ `findings` (判定できない場合も findings に倒す)。否定・不確実・保留を
  表す語 (`cannot` / `not` / `unable` / `unclear` / `whether` / `if` / `may` / `should` /
  `likely` / `seems` / `pending` / `needed` / `confirm` 等) を含む行は肯定の結論ではないため
  一致とみなさない
- **header は本文の先頭行に置かれ、gate も先頭行の header だけを attestation として受理する**。report 本文が header 形の文字列を含む場合 (レビュー対象の差分から引用した場合等) は、wrapper が本文を書く時点で `<!-- codex-review (quoted):` へ書き換えて無害化する
- 本文が長い場合は wrapper が行単位で切り詰める (GitHub の PR コメント本文上限に対する安全側の閾値。header 行は先頭にあるため常に残る)
- 記録と投稿はレビュー完了の記録であり、merge の approve や findings 0 件の証明ではない (status=findings でも「レビュー済み」として成立する。findings への対応判断は通常のレビューフローで行う)

ローカルの記録は merge 実行 repo の git-dir 直下に置かれ、次の 5 種類です (名前の単一ソースは `hooks/scripts/lib/markers.sh`):

| ファイル | 書き手 | 役割 |
|---|---|---|
| `.claude-pre-merge-codex-reviewed` | auto-mark (SubagentStop) | final attestation。gate はこれと本文の対を検証して投稿する |
| `.claude-pre-merge-codex-reviewed.pending` | wrapper | pending attestation。report 検証を通ると final へ昇格する |
| `.claude-pre-merge-codex-comment.md` | wrapper | 投稿用の本文 (先頭行が機械可読 header) |
| `.claude-pre-merge-launch-<agent_id>` | auto-mark (SubagentStart) | レビュー開始時のローカル HEAD |
| `.claude-pre-merge-done-<agent_id>` | auto-mark (SubagentStop) | attestation を消費した記録 (one-shot 保証。無期限に保持する) |

pending / final attestation の内容は次の 2 行で、昇格は rename のみで内容を書き換えません:

```
pr=<PR 番号>
head=<レビュー対象の full head SHA (40 hex 小文字)>
```

ファイル名の prefix はすべて `.claude-pre-merge-` で、`pre-push-codex-review` が使う `.claude-pre-push-*` とは衝突しません。

### Agents

#### `pre-merge-codex-review:codex-reviewer` (subagent)

**ファイル**: `agents/codex-reviewer.md`

codex review wrapper (`hooks/scripts/run-pre-merge-codex-review.sh`) を foreground で 1 回起動し、wrapper の stdout / stderr を subagent context 内で評価して parent-safe markdown report に抽象化する最小 subagent です。wrapper は **current branch の PR** を gh で解決し、その PR の実 base との merge-base..head 全差分に対して codex review を実行して、完了時に投稿用の本文と pending attestation を git-dir 直下に書きます (PR への投稿は行いません。ローカル HEAD が PR の head SHA と一致しない場合は、記録する head SHA と実際にレビューした内容が食い違うため実行せず中断します)。別の PR をレビューさせたい場合は、その PR のブランチへ `git switch` してから起動してください。レビュー範囲と対象内容は wrapper 側で束縛します:

- **working tree が dirty なら中断**: codex のレビューは working tree を含む差分を見るため、未コミット変更があると head SHA を記録しながら別内容をレビューすることになります (commit / stash を案内します)
- **base の妥当性**: PR が記録する base commit (`baseRefOid`) がローカルの `origin/<base>` から到達可能 (ancestor) であることを確認します。到達不能なら 1 度だけ明示 refspec (`git fetch origin +refs/heads/<base>:refs/remotes/origin/<base>`) で fetch して再判定し、それでも到達不能ならレビューも記録の書き込みも行いません。base branch は PR 作成後も進むため完全一致は要求せず、レビュー範囲の anchor は `git merge-base HEAD origin/<base>` (GitHub の PR diff と同じ範囲) を使います
- **空 diff の中断**: merge-base..HEAD の差分が空の場合も、何も見ていない「レビュー済み」記録を残さないため中断します
- **起動時の stale 記録の掃除**: 実行開始時に git-dir 直下の final / pending attestation と投稿用本文を削除します (前回の中断が残した記録で merge が通る経路を残さないため。削除できない場合は中断します)
- **記録を書く直前の再検証**: codex review 完了後・記録を書く前に HEAD と working tree の状態を再確認し、レビュー実行中に変化していれば記録を書かず中断します (レビューした内容と記録する head SHA の乖離を残さないため)

tools は `Bash, TaskOutput, Read` に制限され、model は `sonnet` に固定されます。

wrapper が exit 0 で完了した場合、parent-safe report の `Status` は **Codex の report 本文** から決めます (wrapper が非 0 で終了した場合は本文の内容に関わらず従来どおり `Status: execution-failed` です)。本文に finding の記述 (`## Finding` 節・`Severity:` 行・番号付き / 箇条書きの個別指摘) が 1 つも無く「指摘なし」の趣旨で結ばれている場合は、wrapper が記録した header の `status=` 値に関わらず `Status: pass` / `Findings: 0` を返します。header の status と本文の結論が食い違う場合は finding にせず、report に `Note:` 1 行 (header の値・本文の結論・merge gate の判定には影響しない旨) を添えます。finding として返せるのは Codex の report 本文に存在する指摘のみで、wrapper の挙動・header の値・記録の書き込みの成否・subagent 自身の観測範囲の限界は finding にしません (`Status: execution-failed` の Failure class か `Note:` で表現します)。本文が finding も「指摘なし」の結論も含まず判定できない場合 (途中で切れている・空・記述のみ等) は pass に倒さず、`Status: execution-failed` (Exit status 0・Failure class `other`) で返し、wrapper 自体は完了しレビュー記録を書き終えている可能性がある旨を recovery direction に書きます。

## auto mode での利用

auto mode (permission_mode = `auto`) では、Claude Code の classifier が各 tool call を審査します。classifier が読むのは「ユーザ発言・tool call・CLAUDE.md」で、tool result (merge gate の deny 文を含む) は除去されます。そのため `gh pr merge` の呼び出し直後に `pre-merge-codex-review:codex-reviewer` を起動すると、classifier には「ユーザが依頼していない merge 系操作の一部」に見え、起動が `Blocked by classifier` で拒否されることがあります。作者環境での集計では、merge gate の deny 直後 (間に人間の発言なし) の起動 11 件中 8 件が拒否され、merge 試行を挟まない起動 13 件と、ユーザの許可発言を挟んだ起動 3 件はすべて通過しました。

本 plugin はこれを次の 3 段で扱います:

1. **起動順の規律 (SessionStart 注入)**: `hooks/prompts/merge-order-rules.md` を毎セッション注入し、マージ前提条件の確認後・`gh pr merge` の前に codex-reviewer を起動する手順、起動 prompt の定型文、`--delete-branch` を付けない merge の形、拒否時は `AskUserQuestion` でユーザの許可を得る手順を定めます
2. **subagent の description と案内 prompt**: classifier が起動時に評価する task description が subagent の実際の操作を正確に表すよう、subagent の description と gate の deny 文が案内する prompt を「read-only の codex review を 1 回実行して report を返す」記述に統一しています (PR への投稿と merge を行うのは gate であり、subagent ではありません)
3. **classifier の allow 設定 (任意)**: 恒久的に解消するには、`~/.claude/settings.json` の `autoMode.allow` に次のルールを追加します。`"$defaults"` を残さないと組み込みルールが失われるので必ず併記してください

```json
{
  "autoMode": {
    "allow": [
      "$defaults",
      "Launching the pre-merge-codex-review:codex-reviewer subagent while preparing to merge the current branch's pull request is allowed, including immediately after a gh pr merge attempt: it only runs a read-only cross-model code review of the pull request diff and writes a local record under the repository's .git directory. It does not post to GitHub, merge, push, or delete anything."
    ]
  }
}
```

この allow ルールが緩めるのは subagent の**起動**の審査だけです。subagent が実行中に行う各 tool call は引き続き classifier が親 session と同じ規則で審査し、完了時にも行動履歴が審査されます。

classifier は project settings (`.claude/settings.json` / `.claude/settings.local.json`) の `autoMode` を読まないため、ユーザ設定 (`~/.claude/settings.json`) に書く必要があります。classifier は CLAUDE.md も読むため、プロジェクトの CLAUDE.md に同趣旨の 1 文を書く方法でも代替できます。設定なしで拒否された場合は、ユーザが「マージ前レビューとマージを実行してよい」と発言すれば次の起動は通ります (classifier は明示的なユーザ意図で soft block を解除します)。

`gh pr merge` 自体が classifier に拒否されることもあります。`--delete-branch` は remote branch の削除として組み込み soft_deny の対象になるため、merge は `gh pr merge <番号> --squash` 等の単独正規形で実行し、branch の掃除は merge 後に別コマンドで行ってください。

## 既知の制約

- **レビュー済み確認の正本は PR 上のコメント**: コメントの偽装 (codex review を実行せずに同形式のコメントを投稿する等) は防ぎません (cooperative 利用前提)。gate が投稿の材料にするローカル記録の偽装 (final attestation と投稿用本文を手で置く等) も同様に防ぎません。lifecycle hook の検証 (Status 行・HEAD 一致・one-shot 消費) が守るのは「subagent が実際に完走したレビューだけを昇格する」ことであり、ファイルを直接書ける利用者に対する防御ではありません
- **gate の観測範囲は Bash tool の `gh pr merge` (連続列を含む形) のみ**: `gh api` による直接 merge 呼び出し、gh alias、意図的な難読化、非 Bash の tool 経路、Web UI や他 client からの merge は観測できません
- **TOCTOU 窓は防がない**: gate 確認後から実 merge までの間に head が更新される競合窓は防ぎません (レビューコメントの SHA は gate 確認時点の head と照合されます)。gate がローカル記録を PR に投稿してから実 merge に進むまでの間も同じ窓であり、投稿時点の head と実 merge 時点の head が食い違う可能性は残ります
- **`--auto` / `--admin` は常に deny**: 遅延 merge 予約 (gate 確認と実 merge の分離) と保護 bypass はサポート外です。必要な場合は plugin を無効化して実行してください
- **粗い検出による誤爆**: `gh pr merge` の連続列を quoted な文字列として含むだけのコマンド (コミットメッセージへの言及等) も関与対象になります。誤 deny された場合はコマンドを言い換えて回避してください
- **連続列判定はフラグ介在形に一致しない**: サブコマンドの語間にフラグが入る呼び出し形 (`gh -R owner/repo pr merge 123` 等) は `gh pr merge` の連続列を含まないため gate が関与せず、この形の merge は観測できません。別 repo の PR を merge する場合はその repo のディレクトリへ移動し、`gh pr merge` を先頭に置いた単独コマンドとして番号指定 (`gh pr merge 123`) か current branch 指定 (`gh pr merge --squash`) で実行してください (repo selector 付きの形は gate が deny します)
- **レビュー記録は current branch の PR にのみ紐づく**: codex-reviewer subagent が実行する wrapper は current branch の PR を対象にレビューし、その PR 番号を記録に書きます。別 PR を番号指定した merge が deny されたときは、先にその PR のブランチへ `git switch` してから subagent を起動してください (別ブランチのまま起動すると、記録が current branch の PR のものになり、codex の利用枠だけを消費して目的の merge は deny のままになります)
- **base 変更 (retarget) は失効として検出しない**: レビューコメントの照合は head SHA のみで行います。PR の base branch を変更しても head SHA は変わらないため、レビュー対象の差分 (merge-base..head) が変わっても既存のレビューコメントは有効なまま扱われます
- **受理正規形の外側は拡張しない (stop rule)**: gate が解釈するのは `gh pr merge [<number>] [flags...]` の単独正規形だけです。正規形外の形 (リダイレクト・連結・quote・変数展開等) を許可する parser 拡張は行いません。「除去して近似する」処理は shell の実挙動との乖離を生み、未レビュー merge を通す穴になるためで、必要な操作は単独コマンドへの言い換えで対応してください
- **照合 repo は hook payload の `cwd` に従う**: gate は payload の `cwd` が指す repo でレビューコメントを照合します。payload の `cwd` が実際にコマンドを実行する shell の cwd と乖離する環境では、gate は payload 側の repo を照合し、その乖離自体は検出できません
- **remote `origin` = PR の repository が前提 (fork 構成は非対応)**: wrapper はレビュー範囲の base をローカルの `origin/<base>` で解決するため、remote `origin` が PR の属する repository を指す個人環境を前提とします。fork からの PR (origin と PR の repository が異なる構成) には対応しません
- **merge queue による暗黙の遅延 merge は検出しない**: merge queue が有効な base branch では `--auto` を付けなくても merge が queue 経由の遅延実行になりえますが、gate はこれを検出しません (遅延 merge はサポート外です)

## pre-push-review / pre-push-codex-review との併用設計

本 plugin は単独 install で自立動作し、`pre-push-review` core (`git push` 前の code review / security review gate) と併用しても push gate に一切影響しません。push gate (`git push`) と merge gate (`gh pr merge`) は独立した PreToolUse hook であり、互いの判定に関知しません。

本 plugin は個人環境 (ChatGPT Plus の codex CLI) 向けに「merge 前に 1 回だけ codex review」を運用する設計です。push の都度 codex review を要求する会社環境向け `pre-push-codex-review` との併用は前提としていません。会社環境では codex 系 2 plugin のうち `pre-push-codex-review` の側を install し、本 plugin は install しないでください (`pre-push-review` core は会社環境でもそのまま併用します)。

## 共有 lib の同一性

`hooks/scripts/lib/codex-companion-resolver.sh` は `pre-push-codex-review` (`plugins/pre-push-codex-review/hooks/scripts/lib/`) が canonical で、本 plugin はその byte-identical なコピーを保持します (codex review の実行機構は両 plugin で同一のため)。この同一性は `tests/test_pre_merge_lib_copies.py` の契約テストが検査します。

それ以外の lib コピーは持ちません。reviewer 一式 (wrapper / subagent 定義 / hook script 群) は pre-merge 専用の実装であり、pre-push 系との文字列同一性契約は設けません。`lib/review-status.sh` (status 判定) と `lib/markers.sh` (レビュー記録のファイル名と内容契約) も pre-merge 専用の lib であり、この同一性契約の対象外です (`lib/markers.sh` は pre-push 系の同名 lib とは別の名前空間・別の束縛キーを扱います)。

## ファイル構成

| パス | 役割 |
|---|---|
| `hooks/hooks.json` | フック配送経路の定義 |
| `hooks/scripts/block-pre-merge.sh` | 軽量 merge gate 本体 (PreToolUse)。レビュー記録の検証と PR への投稿も行う |
| `hooks/scripts/block-bg-codex-wrapper.sh` | codex review wrapper の起動検証 (PreToolUse) |
| `hooks/scripts/auto-mark.sh` | subagent lifecycle hook (SubagentStart / SubagentStop / PostToolUseFailure)。pending attestation を final へ昇格する |
| `hooks/scripts/inject-merge-order-rules.sh` | SessionStart hook。merge 前 codex review の起動順規律を additionalContext として注入する |
| `hooks/prompts/merge-order-rules.md` | 注入する起動順規律の本文 |
| `hooks/scripts/run-pre-merge-codex-review.sh` | codex review wrapper 本体 (レビュー実行 + ローカル記録の書き込み。basename は `pre-push-codex-review` の wrapper と別名) |
| `hooks/scripts/lib/codex-companion-resolver.sh` | codex companion 解決ロジック (`pre-push-codex-review` からの byte-identical コピー) |
| `hooks/scripts/lib/markers.sh` | レビュー記録のファイル名と内容契約の単一ソース |
| `hooks/scripts/lib/review-status.sh` | status 判定 (`detect_review_status`)。report の末尾 10 行から pass/findings を判定する |
| `agents/codex-reviewer.md` | `pre-merge-codex-review:codex-reviewer` subagent 定義 |

## 関連プラグイン

- [pre-push-review](../pre-push-review/): `git push` 前の push gate (code review / security review の 2 マーカー)。本 plugin の merge gate とは独立に動作し、併用しても互いの gate に影響しません
- [pre-push-codex-review](../pre-push-codex-review/): 会社環境向けの push 毎 codex review gate。本 plugin と同時に install しない前提です。review cadence の計数対象に `pre-merge-codex-review:codex-reviewer` の namespace を含みます (v2.0.0 以上) が、本 plugin 単独構成では review cadence 自体が適用されません
