# pre-push-codex-review プラグイン

`git push` を実行する前に **codex review** (OpenAI クロスモデルレビュー) の完了を必ず実行させ、未レビューな commit が remote に到達するのを構造的にブロックするプラグインです。単独 install で自立動作し、`pre-push-review` core (code review / security review の 2 レビュー gate) と併用すると、code / codex / security の 3 レビュー構成になります。

修正や commit 列の変更 (add→revert / amend / rebase 含む) により「commit 列 (HEAD / merge-base の OID) + ブランチ全差分」のハッシュが変わると codex マーカーは自動失効し、Claude は `pre-push-codex-review:codex-reviewer` subagent を再走させる以外に push を通す手段がありません。

本 plugin は Codex review の **review cadence** も enforcement します。`pre-push-codex-review:codex-reviewer` / `pre-merge-cross-review:codex-reviewer` の成功 review と `cross-model-advisor:codex-review-runner` の成功 native / adversarial review を session ごとに合算し、前回の根本方針 checkpoint から 5 回完了すると、次の review 起動と main session の停止を block します。checkpoint の実行 (`cross-model-advisor` の consult skill による `cross-model-advisor:codex-advisor-runner` の起動と attestation の発行) 自体は cross-model-advisor plugin が担います。詳細は [review cadence](#review-cadence) を参照してください。

## バージョン

v4.1.0
## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install pre-push-codex-review@natsuume-plugins
```

公式 codex プラグインへの依存があるため、codex review wrapper を動作させるには次も install してください:

```bash
claude plugin install codex@openai-codex
```

3 レビュー構成にするには `pre-push-review` core も併せて install します:

```bash
claude plugin install pre-push-review@natsuume-plugins
```

checkpoint 実行の companion として `cross-model-advisor` を併用する場合は **v5.0.0 以上**を使用してください。companion の役割は checkpoint の実行 (`cross-model-advisor:codex-advisor-runner` の起動と attestation の発行) のみであり、review cadence の計数・enforcement は本 plugin が単独で担います。

### 依存コマンド

`jq` は push gate の必須依存です。`jq` が見つからない環境では、未レビューの push を通さないため `block-pre-push-codex.sh` が `git push` を fail-closed に deny し、インストール後の再実行を案内します。push と無関係な Bash 呼び出しは影響を受けません。

## 機能一覧

### Commands

#### `/pre-push-codex-review:review`

**ファイル**: `commands/review.md`

push 前レビューを **同じアシスタントメッセージで並列に** subagent として起動する確定的フローです。`pre-push-review` core が install されている環境では、本 plugin の `pre-push-codex-review:codex-reviewer` と core の `pre-push-review:code-reviewer` / `pre-push-review:security-reviewer` を合わせた 3 subagent を 1 つのアシスタントメッセージで並列発出します。core が未 install の環境では `pre-push-codex-review:codex-reviewer` のみを起動します。

参照方向は本 plugin → core の単方向です。core の `commands/review.md` は本 plugin の存在を前提とせず、`pre-push-review:code-reviewer` / `pre-push-review:security-reviewer` の 2 subagent のみを並列発出します。

### Hooks

#### 1. block-pre-push-codex (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-pre-push-codex.sh`

`git push` を含むコマンドを検出した際、「commit 列 (HEAD / merge-base の OID) + ブランチ全差分 + 未コミット差分」のハッシュと codex マーカー (`pre-push-codex-review:codex-reviewer` subagent 起因) のハッシュを比較し、一致しなければ `deny` を返します。push 検出・複合コマンド解析・target 解決・dirty-tree gate・空 push 判定・default branch 上での skip (git-guardrails への委譲) は、本 plugin が単独 install でも自立動作できるよう、`pre-push-review` core の `block-pre-push.sh` と同等の判定を本スクリプトが独立に実装します。deny メッセージは codex マーカーの状態と `pre-push-codex-review:codex-reviewer` subagent への案内のみを記載し、core の 2 マーカーには言及しません。branch の変更がテキスト系文書だけの push は、空 push と同じくマーカーを要求せずに通します (定義とレビュー必須とする条件は [テキスト系文書のみの変更](#テキスト系文書のみの変更))。

#### 2. block-bg-codex-wrapper (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-bg-codex-wrapper.sh`

codex review wrapper (`run-pre-push-codex-review.sh`) の起動を検証する PreToolUse hook です。hook payload トップレベルの `agent_type` が `pre-push-codex-review:codex-reviewer` (namespace 付き完全一致) でなければ fail-closed に deny します。wrapper の basename を `pre-merge-cross-review` の wrapper (`run-pre-merge-codex-review.sh`) と別名にしているのは、両 plugin が併存する環境で、互いの wrapper 検出 gate (basename ベース) が相手の wrapper 起動を deny し合う干渉を塞ぐためです。foreground 起動を強制する理由は、background 起動では `pre-push-codex-review:codex-reviewer` subagent が wrapper の stdout / stderr (= codex review の verdict / findings) を完全に観察できず、正しい parent-safe report を組み立てられないためです。

#### 3. auto-mark (SubagentStart / PostToolUse / SubagentStop)

**ファイル**: `hooks/scripts/auto-mark.sh`

`pre-push-codex-review:codex-reviewer` subagent の実行完了を subagent lifecycle hook (SubagentStart / SubagentStop) で検知し、codex マーカーに「commit 列 + branch 全差分 + 未コミット差分のハッシュ」を書き込みます。matcher は SubagentStart / SubagentStop が `^pre-push-codex-review:codex-reviewer$`、PostToolUse が `^SubagentHandback$` (tool 名) で、script 側でも agent_type を完全一致で再検証します。`SubagentStart` はレビュー開始時の hash を launch attestation として one-shot 記録し、`PostToolUse` (`SubagentHandback`) は auto mode で hand-back された report (`tool_input.message`) の Status を handback record (`.claude-pre-push-handback-<agent_id>`) に記録し、`SubagentStop` は (a) attestation の一回限りの消費 (b) 開始時 hash と現在 hash の一致 (c) report (handback record があればその判定結果、無ければ `last_assistant_message`) 内の単一 `Status: pass|findings` 行 (d) wrapper が書いた pending attestation と現在 hash の一致、をすべて検証した場合のみマーカーを書きます。`PostToolUseFailure` では残った Codex pending attestation を破棄します。

Claude Code v2.1.271 以降の auto mode では subagent の最終 report が `SubagentHandback` tool 経由で親に届き、SubagentStop の `last_assistant_message` には締めの文しか入らないため、report の判定は PostToolUse で記録した handback record を優先します。同一 agent_id で `SubagentHandback` が 2 回以上呼ばれた場合は重複 report として無効 (fail-closed) です。`tool_response.success` が false (未配信) の hand-back は記録せず、`last_assistant_message` 経路の判定に委ねます。

マーカーが証明するのは、codex review が marker に記録された最新差分に対して完了したことだけです。変更の approve や findings が 0 件であることは証明しません。

#### 4. inject-review-cadence-rules (SessionStart)

**ファイル**: `hooks/scripts/inject-review-cadence-rules.sh`

session 開始のたびに `hooks/prompts/review-cadence-rules.md` の全文を additionalContext として注入する hook です。`jq` 不在や prompt ファイル欠落時は注入をスキップして fail-open に exit 0 します。

#### 5. manage-review-cadence (PreToolUse / SubagentStart / SubagentStop / PostToolUseFailure / PermissionDenied / Stop / SessionEnd)

**ファイル**: `hooks/scripts/manage-review-cadence.mjs`

review cadence の state 管理と enforcement を担う node script です。詳細は [review cadence](#review-cadence) を参照してください。

- **PreToolUse** (matcher: `Bash`): checkpoint 要求中 (完了 review が 5 回に達している間) に review 起動形 (`codex-companion.mjs review|adversarial-review`、`run-pre-push-codex-review.sh`、`run-codex-job.sh review`) を検出すると deny する
- **SubagentStart** (matcher: `^(pre-push-codex-review:codex-reviewer|pre-merge-cross-review:codex-reviewer)$`): 起動した agent_id を review cadence state へ記録する
- **PostToolUse** (matcher: `^SubagentHandback$`): 計数対象 2 reviewer・`cross-model-advisor:codex-review-runner`・`cross-model-advisor:codex-advisor-runner` が auto mode で hand-back した report (`tool_input.message`) を解析し、Status 行 / footer / attestation の解析値 (本文は保存しない) を agent_id ごとに state へ記録する。SubagentStop がそれを one-shot で消費する
- **SubagentStop** (matcher: `^(pre-push-codex-review:codex-reviewer|pre-merge-cross-review:codex-reviewer|cross-model-advisor:codex-(review|advisor)-runner)$`): 計数対象 reviewer の成功 review を加算し、`cross-model-advisor:codex-advisor-runner` の checkpoint 充足 attestation でカウンターを reset する
- **PostToolUseFailure** (matcher: `Agent|Task`): checkpoint 相談 (起動 request の `tool_input.prompt` に `<review_cycle_checkpoint>` を含む) の `cross-model-advisor:codex-advisor-runner` 起動失敗を fail-open で checkpoint 充足とみなし、checkpoint 要求中ならカウンターを reset する。同じ `cross-model-advisor:codex-advisor-runner` でも通常の advisor 相談の起動失敗は reset しない
- **PermissionDenied** (matcher: `Agent|Task`): auto mode の classifier が checkpoint 相談 (条件は PostToolUseFailure と同じ) の起動を拒否したとき、1 回目は拒否回数を state に記録して `{"hookSpecificOutput": {"hookEventName": "PermissionDenied", "retry": true}}` を返し、ユーザ確認後の再起動を促す。同じ checkpoint 要求中の 2 回目の拒否で fail-open としてカウンターを reset する。条件に合わない拒否は state を変えず、拒否回数にも数えない
- **Stop**: checkpoint 要求中は main session の停止を block し、`cross-model-advisor:codex-advisor-runner` の起動と、自動 reset が届かない場合の state ファイル削除による脱出手順を案内する
- **SessionEnd**: この session の review cadence state を削除する

### マーカーファイル

すべて `<git-dir>` 配下に配置 (リポジトリ単位で共有、ブランチ単位ではない):

| ファイル | 内容 | 寿命 |
|---|---|---|
| `.claude-pre-push-codex-reviewed` | codex review + parent-safe report 完了時の commit 列 + branch 全差分のハッシュ (final)。wrapper pending を auto-mark が昇格する | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-codex-reviewed.pending` | wrapper が束縛した review 対象 hash (pending attestation) | report 失敗・hash mismatch・次回 wrapper 起動で削除。codex-reviewer の terminal な拒否 stop でも破棄 |
| `.claude-pre-push-launch-<agent_id>` | SubagentStart が one-shot 記録するレビュー開始時の hash (launch attestation)。SubagentStop が開始時 hash と現在 hash の一致検証に使う | 最初の SubagentStop で消費 (削除) |
| `.claude-pre-push-done-<agent_id>` | attestation 消費時に排他作成される launch tombstone。同一 agent_id での SubagentStart 再発火による attestation 再鋳造を遮断する | 無期限保持 (prune しない) |
| `.claude-pre-push-handback-<agent_id>` | PostToolUse (`SubagentHandback`) が記録する hand-back された report の Status 判定結果 (`pass` / `findings` / `invalid`) | 最初の SubagentStop で消費 (削除)。1 日より古い残存分は次回 SubagentStart が掃除 |

`pre-push-review` core が発行する `.claude-pre-push-code-reviewed` / `.claude-pre-push-security-reviewed` は本 plugin の関知対象外です。逆に core は本 plugin の codex マーカーを検証しません。

### Agents

#### `pre-push-codex-review:codex-reviewer` (subagent)

**ファイル**: `agents/codex-reviewer.md`

codex review wrapper (`hooks/scripts/run-pre-push-codex-review.sh`) を foreground で 1 回起動し、wrapper の stdout / stderr を subagent context 内で評価して parent-safe markdown report に抽象化する最小 subagent です。

**動作**:

- tools は `Bash, Read` に制限 (Edit / Write / Skill / Task はすべて非許可)。`Read` は Bash timeout による background 移行後の回収専用 (wrapper が書く terminal sentinel と、その run の output file だけを読む) で、wrapper-only な実行サーフェスを構造的に維持する
- subagent body は wrapper を plain な foreground Bash 1 回で起動し、raw output を final reply へコピーせず parent-safe report に変換する
- 親 session は finding の priority / location / impact / verification / fix direction / disposition を受け取る。実行可能な command、payload、環境値、段階的な再現・回避手順、raw stdout / stderr は subagent context に閉じ込められる
- exact detail を使った追加確認が必要な場合は同一 codex-reviewer を resume し、検証結果だけを再度 parent-safe report で受け取る
- wrapper は exit 0 完了時に hash-bound pending attestation を atomic write し、auto-mark が subagent の正規 `pass/findings` report と current hash 一致を確認して codex-reviewed マーカーへ昇格する
- 変更がテキスト系文書だけの場合、wrapper は codex review を実行せず、stdout に `Review skipped: text-only changes` の 1 行だけを出して exit 0 で終了する (pending attestation は書かず、stderr には terminal sentinel の案内行以外を出さない)。gitleaks が秘匿情報の候補を検出した場合や実行に失敗した場合は skip せず、通常どおり codex review を実行する。subagent は skip のとき `Status: skipped` / `Reason: text-only changes` の report を返す。stdout と stderr が混ざって届いても判定できるよう、subagent は出力全体に skip の行がちょうど 1 行あり、他の行が terminal sentinel の案内行・終了行・空行だけであることを条件にする。auto-mark は `Status: skipped` ではマーカーを書かず、review cadence も数えない (push gate はこの push にマーカーを要求しない)
- model は `sonnet` に固定

## テキスト系文書のみの変更

branch の変更がテキスト系文書だけの push は、空 push と同じく codex マーカーを要求せずに通します。push gate が deny しないため、codex-reviewer を起動する必要もありません。どの push が該当するかは push gate が判定します。codex-reviewer を起動した場合も、wrapper は codex review を実行せず、codex-reviewer は `Status: skipped` を返します。

この skip では codex review も行いません。PATH に gitleaks がある環境では、skip の前に範囲内の commit を gitleaks で検査し (`gitleaks git --redact --ignore-gitleaks-allow` 等)、秘匿情報の候補を検出した場合や gitleaks の実行に失敗した場合 (timeout を含む) は skip せず、従来どおり codex マーカーを要求します (wrapper も通常どおり codex review を実行します)。このとき deny の理由文にその旨を 1 文加えます (認証情報の内容や gitleaks の出力は含めません)。`--ignore-gitleaks-allow` を付けるのは、文書中の `gitleaks:allow` コメントで検出を抑止させないためです。gitleaks の実行は 25 秒で打ち切り、gitleaks の実行に失敗した場合として扱います。

gitleaks の設定 (`.gitleaks.toml` / `.gitleaksignore`) は、working tree ではなく範囲の起点 (base の commit) の tree から読みます。working tree に未追跡の設定ファイルを置いて検出を抑止できないようにするためです。また、gitleaks が内部で実行する `git log` に `--text --no-textconv --no-color` を付け、ローカルの git の属性 (binary の指定・textconv) や `color.ui` の設定で文書の差分が検査から隠れないようにしています。base の tree に `.gitleaks.toml` が無ければ gitleaks の既定のルールだけを使い、`.gitleaksignore` が無ければ ignore は空にします。環境変数 `GITLEAKS_CONFIG` / `GITLEAKS_CONFIG_TOML` を設定している場合は、利用者自身の設定としてそちらを使います。

gitleaks が無い環境では、文書に書き込まれた token などの認証情報は検査されません。秘匿情報の混入を決定的に止めたい場合は、gitleaks 等の secret scanner を pre-commit / pre-push hook として併用することを推奨します。

**テキスト系文書** は、basename の拡張子 (最後の `.` より後ろ) が大文字小文字を区別せずに `md` / `markdown` / `rst` / `adoc` のいずれかであるファイルです。拡張子の無いファイルは含みません。`txt` は依存宣言 (`requirements.txt` 等) やビルド定義 (`CMakeLists.txt`) に使われ、`mdx` はコードを埋め込めるため、どちらも対象外です。Claude Code や他の coding agent は特定の場所・名前の Markdown を指示として読み込むため、次に列挙した場所・名前の Markdown はコードとして扱います (列挙に無いものの扱いは「既知の制約」を参照):

- basename を除くディレクトリ要素のどれかが `agents` / `commands` / `skills` / `rules` / `output-styles` / `.claude` / `.agents` / `.cursor` / `.gemini` / `.codex` / `.windsurf` / `.clinerules` / `.roo` / `.kiro` / `.continue` / `.junie` / `.amazonq` に一致する (深さは問わない)
- 連続する 2 つのディレクトリ要素が `hooks` → `prompts`、`.github` → `instructions`、`.github` → `prompts`、`.github` → `chatmodes` のいずれかの順に並ぶ
- basename が `CLAUDE.md` / `CLAUDE.local.md` / `AGENTS.md` / `AGENTS.override.md` / `GEMINI.md` / `copilot-instructions.md` に一致する

これらの照合は ASCII の大文字小文字を区別しません。macOS 既定の filesystem では `Agents/` や `claude.md` も `agents/` や `CLAUDE.md` と同じファイルとして解決され、agent に読み込まれるためです。同じ理由で、大文字小文字の変換で ASCII の文字になる非 ASCII 文字や、HFS+ が名前の比較で無視する不可視の文字 (U+017F の `ſ`、U+212A の `K`、U+200C など。全件は `hooks/scripts/lib/text-paths.sh` のヘッダに記載) を path に含むファイルは、場所を問わずテキスト系文書として扱いません。

判定できない場合はレビュー必須として扱います。次のいずれかに当たる push はテキスト系文書のみとは扱わず、従来どおり codex マーカーを要求します:

- `origin/<base>..HEAD` に commit が無い、または merge commit を含む
- 範囲の commit が 100 を超える、または判定に 15 秒を超えてかかる (hook の時間内に判定を終えるため、判定を打ち切ってレビュー必須として扱う)
- 範囲内の各 commit とその親との差分、または merge-base と HEAD の差分に、テキスト系文書以外のファイルの変更がある。rename は検出せず、削除と追加として変更前後の両方の path を検査する
- 範囲内に親を持たない commit がある
- git の graft ファイル (`.git/info/grafts`、または環境変数 `GIT_GRAFT_FILE` が指すファイル) がある (graft は commit の親を書き換え、範囲の途中の commit を判定から隠せるため)
- 変更前後のファイル mode に通常ファイル (`100644`) 以外を含む (symlink・submodule・実行ビット付きファイル)
- index か working tree に未コミットの変更がある
- 判定に使う git コマンドが失敗した

net diff ではなく範囲内の各 commit を検査するため、コードを追加した commit を後の commit で revert した branch も、push される履歴にコードが残るのでマーカーを要求します。

判定は `hooks/scripts/lib/text-paths.sh` が行います (canonical は `pre-push-review` core。下記「共有 lib の同一性」を参照)。push gate と wrapper は同じ判定関数を使うため、wrapper が skip した branch では push gate もマーカーを要求しません。

## review cadence

`pre-push-codex-review:codex-reviewer` / `pre-merge-cross-review:codex-reviewer` の成功 review、`cross-model-advisor:codex-review-runner` の成功 native / adversarial review を 1 サイクルと数え、前回の根本方針 checkpoint から合計 5 サイクル完了すると、次の review 起動と main session の停止を block する enforcement です。

### 計数対象

- `pre-push-codex-review:codex-reviewer` / `pre-merge-cross-review:codex-reviewer` の SubagentStop で、report (auto mode では PostToolUse で記録した `SubagentHandback` の hand-back report、それ以外は `last_assistant_message`) に `Status: pass|findings` 行がちょうど 1 行ある場合
- `cross-model-advisor:codex-review-runner` の SubagentStop で、report (所在は上と同じ) の実質末尾 3 行の footer (`Codex-Runner-Operation: review` / `Codex-Runner-Status: success` / `Codex-Runner-Job-ID: <id>`) が揃っている場合。同一 agent_id は 1 回だけ計数する (resume 再 stop で footer 付きの報告が再び来ても加算しない)

### checkpoint

5 サイクル完了後、次の review 起動は PreToolUse hook が deny し、main session の停止は Stop hook が block します。checkpoint の実行主体は本 plugin ではなく `cross-model-advisor` plugin です。`cross-model-advisor:consult` skill の「Codex review 5 サイクルごとの根本方針 checkpoint」節の手順が `cross-model-advisor:codex-advisor-runner` を `model: "sonnet"` で起動し、`<review_cycle_checkpoint>` (Goal と受入基準・制約 / 直近 5 サイクルの review 履歴 / 現在の方針と不確実性 / course-correction の問い) を材料に根本方針の壁打ちを行います。起動 mode は Claude Code が決める (対話セッションでは background が既定) ため指定せず、助言は completion notification (SubagentHandback / SubagentStop) 経由で届きます。

カウンターの reset は次の 4 経路に限られます:

1. advisor runner が checkpoint request の成功を `Codex-Advisor-Review-Cadence: satisfied` で証明したとき
2. advisor runner が checkpoint request を完了できないことを `unavailable` で証明したとき (terminal-failure)
3. checkpoint 相談 (相談 request に `<review_cycle_checkpoint>` を含む) の `cross-model-advisor:codex-advisor-runner` の起動失敗が PostToolUseFailure として本 script に到達したとき (fail-open)。1 回の到達で reset する。ユーザ interrupt による abort (`is_interrupt: true`) はこの経路に含まれない
4. 同じ checkpoint 相談の起動拒否が PermissionDenied として本 script に **2 回** 到達したとき (fail-open)。1 回目の拒否では reset せず、`retry: true` を返してユーザ確認後の再起動を促す。拒否回数は state と一緒に保持されるため、reset 後の新しい checkpoint 要求では再び 1 回目から数える

`<review_cycle_checkpoint>` を含まない通常の advisor 相談の起動失敗・拒否は、経路 3・4 のいずれにも含まれません (拒否回数にも数えません)。

fail-open はどちらも「起動失敗・拒否が hook に届いた」ことが前提です。`cross-model-advisor` plugin が install されていない環境では `subagent_type` 自体が解決できず、PostToolUseFailure も PermissionDenied も発火しないため自動解除されません。起動を試みても block が解除されない場合は、Stop の block 文言が示す state ファイルを削除して脱出してください ([state](#state) 参照)。

### state

review cadence の state は session ごとに 1 ファイル (ファイル名は session ID の sha256 hex + `.json`) として `$TMPDIR/pre-push-codex-review-<uid>/cadence-state/` (既定値) に保存します。root は環境変数 `PRE_PUSH_CODEX_REVIEW_CADENCE_STATE_ROOT` で差し替えられます。state に prompt や Codex 出力は保存しません。

手動で checkpoint 要求を解除するには、該当 session の state ファイルを削除してください。SessionEnd hook が session 終了時に自動削除するため、通常は手動削除は不要です。

### cross-model-advisor 連携

checkpoint の実行には `cross-model-advisor` plugin の install が必要です。まず必ず `cross-model-advisor:codex-advisor-runner` の起動を試みてください。相談 request に `<review_cycle_checkpoint>` を含む起動であれば、起動失敗 (未認証・timeout 等) が PostToolUseFailure として本 script に到達したとき、または auto mode の classifier による起動拒否が PermissionDenied として 2 回到達したときに、fail-open としてカウンターを reset するため block は解除されて続行できます。PermissionDenied の 1 回目は `retry: true` が返るので、ユーザに起動の可否を確認したうえで同じ相談をもう一度起動してください。起動を試みた後も block が解除されない場合は、cross-model-advisor plugin の install が必要であることをユーザに報告したうえで、[state](#state) の手動 reset (state ファイル削除) で解除してください。`pre-merge-cross-review` の `codex-reviewer` subagent も本 plugin の review cadence の計数対象です。

## pre-push-review core との併用設計

本 plugin は `pre-push-review` core との併用を前提としつつ、単独 install でも自立動作します。そのため次の設計を採ります:

- **共通 gate の独立実装**: push 検出・複合コマンド解析・target 解決・dirty-tree gate・`push.default=matching` 検出・空 push 判定などの共通ロジックは、本 plugin と core がそれぞれ独立に実装します (単独 install の自立動作要件のため。共有ライブラリとして切り出す場合の同一性維持は下記「共有 lib の同一性」を参照)
- **deny 文の独立性**: 各 plugin の deny メッセージは自分が検証するマーカーのみに言及します。本 plugin の deny 文は codex マーカーの状態と `pre-push-codex-review:codex-reviewer` への案内のみを含み、core の code / security マーカーには言及しません。core の deny 文も同様に codex マーカーには言及しません
- **AND 合成**: 両 plugin を併用した場合、`git push` を含む Bash 呼び出しは両方の PreToolUse hook を通過します。どちらか一方でも deny を返せば push は成立しません。3 レビューすべてのマーカーが最新の差分と一致して初めて push が通ります

## codex CLI 更新前の broker の停止

openai-codex plugin の companion は workspace ごとに常駐 broker を起動して再利用します。broker は起動時の `codex app-server` を抱え続けるため、codex CLI を更新しても、更新前に起動した broker は古いバイナリのまま review を実行します。

wrapper (`run-pre-push-codex-review.sh`) は companion の `review` を起動する直前に `hooks/scripts/lib/stale-broker-guard.mjs` を実行します。guard は broker 配下で動く codex app-server の実行ファイルが broker の起動より後に更新されているか、実行ファイルが無くなっていれば、その broker を止め、止めた broker の記録が残っていれば消して、止めた旨を stderr に 1 行出します。その後に起動する companion は、現行のバイナリで新しい broker を起動します。同じ broker で実行中の別の job は中断されます。

検出や停止に失敗した場合 (companion の内部 module を読み込めない、`ps` が失敗する、記録の pid が companion の broker でない、broker が停止しない等) は stderr に警告を 1 行出し、review をそのまま続行します。

## 共有 lib の同一性

`hooks/scripts/lib/cmd-parser.sh` / `target-resolver.sh` / `diff-hash.sh` は `pre-push-review` core (`plugins/pre-push-review/hooks/scripts/lib/`) が canonical で、本 plugin はその byte-identical なコピーを保持します。同一性は `tests/test_shared_lib_copies.py` の契約テストと `.github/workflows/sync-shared-libs.yml` が検査します。

`hooks/scripts/lib/text-paths.sh` (テキスト系文書のみの変更の判定) も `pre-push-review` core が canonical で、本 plugin と `pre-merge-cross-review` (`plugins/pre-merge-cross-review/hooks/scripts/lib/text-paths.sh`) が byte-identical なコピーを保持します。同一性は `tests/test_shared_lib_copies.py` と `tests/test_pre_merge_lib_copies.py` が検査します。

逆に `hooks/scripts/lib/codex-companion-resolver.sh` は本 plugin が canonical で、`cross-model-advisor` (`plugins/cross-model-advisor/scripts/lib/codex-companion-resolver.sh`) がそのコピーを保持し追従します。

`hooks/scripts/lib/stale-broker-guard.mjs` も本 plugin が canonical で、`pre-merge-cross-review` (`plugins/pre-merge-cross-review/hooks/scripts/lib/stale-broker-guard.mjs`) と `cross-model-advisor` (`plugins/cross-model-advisor/scripts/lib/stale-broker-guard.mjs`) が byte-identical なコピーを保持します。同一性は `tests/test_shared_lib_copies.py` と `tests/test_pre_merge_lib_copies.py` が検査します。

`hooks/scripts/lib/markers.sh` は plugin ごとにマーカー集合が異なる (本 plugin は codex マーカーのみ、core は code / security マーカーのみ) ため、同一性検査の対象外です。

## ファイル構成

| パス | 役割 |
|---|---|
| `hooks/hooks.json` | フック配送経路の定義 |
| `hooks/scripts/block-pre-push-codex.sh` | push gate 本体 (PreToolUse) |
| `hooks/scripts/block-bg-codex-wrapper.sh` | codex review wrapper の background 起動検知 (PreToolUse) |
| `hooks/scripts/auto-mark.sh` | codex マーカーの自動発行 (SubagentStart / PostToolUse / SubagentStop / PostToolUseFailure) |
| `hooks/scripts/manage-review-cadence.mjs` | review cadence の state 管理と enforcement (PreToolUse / SubagentStart / PostToolUse / SubagentStop / PostToolUseFailure / Stop / SessionEnd) |
| `hooks/scripts/inject-review-cadence-rules.sh` | review cadence 規律の SessionStart 注入 |
| `hooks/prompts/review-cadence-rules.md` | review cadence 規律の本文 (SessionStart additionalContext) |
| `hooks/scripts/run-pre-push-codex-review.sh` | codex review wrapper 本体 (basename は `pre-merge-cross-review` の wrapper と別名) |
| `hooks/scripts/lib/cmd-parser.sh` | Bash command のセグメント分割・tokenize (core からの byte-identical コピー) |
| `hooks/scripts/lib/target-resolver.sh` | push target cwd の解決 (core からの byte-identical コピー) |
| `hooks/scripts/lib/diff-hash.sh` | レビューハッシュ計算・空 push 判定 (core からの byte-identical コピー) |
| `hooks/scripts/lib/text-paths.sh` | テキスト系文書のみの変更の判定 (core からの byte-identical コピー) |
| `hooks/scripts/lib/markers.sh` | 本 plugin のマーカーファイル名の単一ソース |
| `hooks/scripts/lib/exit-trap.sh` | 予期せぬエラー時の診断 trap |
| `hooks/scripts/lib/codex-companion-resolver.sh` | codex companion 解決ロジック (本 plugin が canonical) |
| `hooks/scripts/lib/stale-broker-guard.mjs` | codex CLI 更新前に起動した companion の broker を検出して止める guard (本 plugin が canonical) |
| `agents/codex-reviewer.md` | `pre-push-codex-review:codex-reviewer` subagent 定義 |
| `commands/review.md` | `/pre-push-codex-review:review` コマンド定義 |

## 既知の制約

- **gate の観測範囲は Bash tool のみ**: PreToolUse hook の matcher が `Bash` であるため、PowerShell tool (`CLAUDE_CODE_USE_POWERSHELL_TOOL=1` で Linux / macOS でも有効化できる) および Monitor tool 経由で発行された `git push` を gate は観測しない。これらの tool を有効にした環境はサポート外
- **古い broker の判定は `PATH` と引数列に依存する**: guard はコマンド名だけの `codex` を guard 自身の `PATH` で解決する。codex を複数の場所にインストールしていて、broker を起動した環境と guard を実行する環境で `PATH` の順序が違うと、broker が使っているものとは別の実行ファイルの時刻を見る。また、実行ファイルのパスに空白が含まれると codex の app-server を特定できず、警告を出して続行する
- **列挙に無い場所にある agent の指示ファイルはテキスト系文書と判定される**: テキスト系文書のみの変更の判定でコード扱いにするのは、列挙した場所・名前の Markdown だけである。それ以外の場所にある agent の指示ファイルは、テキスト系文書としてレビュー無しで push される
- **除外対象から間接的に読み込まれる文書は検出しない**: `CLAUDE.md` から `@path` で import される文書など、除外対象のファイルが読み込む文書も、その場所・名前が列挙に当たらなければテキスト系文書と判定される
- **実行される文書もテキスト系文書と判定される**: doctest や文書のビルドでコードが実行される文書も、拡張子が `md` / `markdown` / `rst` / `adoc` であればテキスト系文書としてレビュー無しで push される
- **git dir の object の改ざんは検出しない**: テキスト系文書のみの変更の判定は、replace ref・graft・commit-graph の影響を受けないようにしているが、git dir にある object の file そのものが書き換えられた場合は検出しない

## 関連プラグイン

- [pre-push-review](../pre-push-review/): code review / security review の 2 マーカーを gate する core。本 plugin と併用すると 3 レビュー構成になる
- [cross-model-advisor](../cross-model-advisor/): `hooks/scripts/lib/codex-companion-resolver.sh` のコピー先 (本 plugin が canonical) であり、[review cadence](#review-cadence) の checkpoint 実行主体でもある
- [git-guardrails](../git-guardrails/): default branch (master/main) への直接書き込みを deny。本 plugin は default branch 上の push を git-guardrails に委譲する
