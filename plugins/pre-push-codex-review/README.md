# pre-push-codex-review プラグイン

`git push` を実行する前に **codex review** (OpenAI クロスモデルレビュー) の完了を必ず実行させ、未レビューな commit が remote に到達するのを構造的にブロックするプラグインです。単独 install で自立動作し、`pre-push-review` core (code review / security review の 2 レビュー gate) と併用すると、code / codex / security の 3 レビュー構成になります。

修正や commit 列の変更 (add→revert / amend / rebase 含む) により「commit 列 (HEAD / merge-base の OID) + ブランチ全差分」のハッシュが変わると codex マーカーは自動失効し、Claude は `pre-push-codex-review:codex-reviewer` subagent を再走させる以外に push を通す手段がありません。

本 plugin は Codex review の **review cadence** も enforcement します。`pre-push-codex-review:codex-reviewer` / `pre-merge-codex-review:codex-reviewer` の成功 review と `codex-advisor:review-runner` の成功 native / adversarial review (旧 `pre-push-review:codex-reviewer` は計数対象外) を session ごとに合算し、前回の根本方針 checkpoint から 5 回完了すると、次の review 起動と main session の停止を block します。checkpoint の実行 (`codex-advisor` の consult skill による `codex-advisor:advisor-runner` の起動と attestation の発行) 自体は codex-advisor plugin が担います。詳細は [review cadence](#review-cadence) を参照してください。

## バージョン

v2.0.0

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

checkpoint 実行の companion として `codex-advisor` を併用する場合は **v3.0.0 以上**を使用してください。companion の役割は checkpoint の実行 (`codex-advisor:advisor-runner` の起動と attestation の発行) のみであり、review cadence の計数・enforcement は本 plugin (v2.0.0 以上) が単独で担います。cadence を自身で持つ v2.x 系の `codex-advisor` と本 plugin を併用すると、独立した cadence カウンターが二重に走り、それぞれが review 起動 deny / main session の Stop block を行うため非推奨です。

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

`git push` を含むコマンドを検出した際、「commit 列 (HEAD / merge-base の OID) + ブランチ全差分 + 未コミット差分」のハッシュと codex マーカー (`pre-push-codex-review:codex-reviewer` subagent 起因) のハッシュを比較し、一致しなければ `deny` を返します。push 検出・複合コマンド解析・target 解決・dirty-tree gate・空 push 判定・default branch 上での skip (git-guardrails への委譲) は、本 plugin が単独 install でも自立動作できるよう、`pre-push-review` core の `block-pre-push.sh` と同等の判定を本スクリプトが独立に実装します。deny メッセージは codex マーカーの状態と `pre-push-codex-review:codex-reviewer` subagent への案内のみを記載し、core の 2 マーカーには言及しません。

#### 2. block-bg-codex-wrapper (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-bg-codex-wrapper.sh`

codex review wrapper (`run-pre-push-codex-review.sh`) の起動を検証する PreToolUse hook です。hook payload トップレベルの `agent_type` が `pre-push-codex-review:codex-reviewer` (namespace 付き完全一致) でなければ fail-closed に deny します。wrapper の basename を `pre-push-review` core の wrapper (`run-codex-review.sh`) と別名にしているのは、codex gate を持つ版の core と本 plugin が併存する環境で、互いの wrapper 検出 gate (basename ベース) が相手の wrapper 起動を deny し合う干渉を塞ぐためです。foreground 起動を強制する理由は、background 起動では `pre-push-codex-review:codex-reviewer` subagent が wrapper の stdout / stderr (= codex review の verdict / findings) を完全に観察できず、正しい parent-safe report を組み立てられないためです。

#### 3. auto-mark (SubagentStart / SubagentStop, matcher: `^pre-push-codex-review:codex-reviewer$`)

**ファイル**: `hooks/scripts/auto-mark.sh`

`pre-push-codex-review:codex-reviewer` subagent の実行完了を subagent lifecycle hook (SubagentStart / SubagentStop) で検知し、codex マーカーに「commit 列 + branch 全差分 + 未コミット差分のハッシュ」を書き込みます。`SubagentStart` はレビュー開始時の hash を launch attestation として one-shot 記録し、`SubagentStop` は (a) attestation の一回限りの消費 (b) 開始時 hash と現在 hash の一致 (c) `last_assistant_message` 内の単一 `Status: pass|findings` 行 (d) wrapper が書いた pending attestation と現在 hash の一致、をすべて検証した場合のみマーカーを書きます。`PostToolUseFailure` では残った Codex pending attestation を破棄します。

マーカーが証明するのは、codex review が marker に記録された最新差分に対して完了したことだけです。変更の approve や findings が 0 件であることは証明しません。

#### 4. inject-review-cadence-rules (SessionStart)

**ファイル**: `hooks/scripts/inject-review-cadence-rules.sh`

session 開始のたびに `hooks/prompts/review-cadence-rules.md` の全文を additionalContext として注入する hook です。`jq` 不在や prompt ファイル欠落時は注入をスキップして fail-open に exit 0 します。

#### 5. manage-review-cadence (PreToolUse / SubagentStart / SubagentStop / PostToolUseFailure / Stop / SessionEnd)

**ファイル**: `hooks/scripts/manage-review-cadence.mjs`

review cadence の state 管理と enforcement を担う node script です。詳細は [review cadence](#review-cadence) を参照してください。

- **PreToolUse** (matcher: `Bash`): checkpoint 要求中 (完了 review が 5 回に達している間) に review 起動形 (`codex-companion.mjs review|adversarial-review`、`run-pre-push-codex-review.sh`、`run-codex-job.sh review`) を検出すると deny する
- **SubagentStart** (matcher: `^(pre-push-codex-review:codex-reviewer|pre-merge-codex-review:codex-reviewer)$`): 起動した agent_id を review cadence state へ記録する
- **SubagentStop** (matcher: `^(pre-push-codex-review:codex-reviewer|pre-merge-codex-review:codex-reviewer|codex-advisor:(review|advisor)-runner)$`): 計数対象 reviewer の成功 review を加算し、`codex-advisor:advisor-runner` の checkpoint 充足 attestation でカウンターを reset する
- **PostToolUseFailure** (matcher: `Agent|Task`): checkpoint 相談 (起動 request の `tool_input.prompt` に `<review_cycle_checkpoint>` を含む) の `codex-advisor:advisor-runner` 起動失敗を fail-open で checkpoint 充足とみなし、checkpoint 要求中ならカウンターを reset する。同じ `codex-advisor:advisor-runner` でも通常の advisor 相談の起動失敗は reset しない
- **Stop**: checkpoint 要求中は main session の停止を block し、`codex-advisor:advisor-runner` の foreground 起動を案内する
- **SessionEnd**: この session の review cadence state を削除する

### マーカーファイル

すべて `<git-dir>` 配下に配置 (リポジトリ単位で共有、ブランチ単位ではない):

| ファイル | 内容 | 寿命 |
|---|---|---|
| `.claude-pre-push-codex-reviewed` | codex review + parent-safe report 完了時の commit 列 + branch 全差分のハッシュ (final)。wrapper pending を auto-mark が昇格する | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-codex-reviewed.pending` | wrapper が束縛した review 対象 hash (pending attestation) | report 失敗・hash mismatch・次回 wrapper 起動で削除。codex-reviewer の terminal な拒否 stop でも破棄 |
| `.claude-pre-push-launch-<agent_id>` | SubagentStart が one-shot 記録するレビュー開始時の hash (launch attestation)。SubagentStop が開始時 hash と現在 hash の一致検証に使う | 最初の SubagentStop で消費 (削除) |
| `.claude-pre-push-done-<agent_id>` | attestation 消費時に排他作成される launch tombstone。同一 agent_id での SubagentStart 再発火による attestation 再鋳造を遮断する | 無期限保持 (prune しない) |

`pre-push-review` core が発行する `.claude-pre-push-code-reviewed` / `.claude-pre-push-security-reviewed` は本 plugin の関知対象外です。逆に core は本 plugin の codex マーカーを検証しません。

### Agents

#### `pre-push-codex-review:codex-reviewer` (subagent)

**ファイル**: `agents/codex-reviewer.md`

codex review wrapper (`hooks/scripts/run-pre-push-codex-review.sh`) を foreground で 1 回起動し、wrapper の stdout / stderr を subagent context 内で評価して parent-safe markdown report に抽象化する最小 subagent です。

**動作**:

- tools は `Bash, TaskOutput, Read` に制限 (Edit / Write / Skill / Task はすべて非許可)。TaskOutput / Read は Bash timeout による background 移行後の回収専用で、wrapper-only な実行サーフェスを構造的に維持する
- subagent body は wrapper を `run_in_background: false` で 1 回起動し、raw output を final reply へコピーせず parent-safe report に変換する
- 親 session は finding の priority / location / impact / verification / fix direction / disposition を受け取る。実行可能な command、payload、環境値、段階的な再現・回避手順、raw stdout / stderr は subagent context に閉じ込められる
- exact detail を使った追加確認が必要な場合は同一 codex-reviewer を resume し、検証結果だけを再度 parent-safe report で受け取る
- wrapper は exit 0 完了時に hash-bound pending attestation を atomic write し、auto-mark が subagent の正規 `pass/findings` report と current hash 一致を確認して codex-reviewed マーカーへ昇格する
- model は `sonnet` に固定

## review cadence

`pre-push-codex-review:codex-reviewer` / `pre-merge-codex-review:codex-reviewer` の成功 review、`codex-advisor:review-runner` の成功 native / adversarial review を 1 サイクルと数え、前回の根本方針 checkpoint から合計 5 サイクル完了すると、次の review 起動と main session の停止を block する enforcement です。旧 `pre-push-review:codex-reviewer` (codex gate 分離前の namespace) は計数対象に含まれません。

### 計数対象

- `pre-push-codex-review:codex-reviewer` / `pre-merge-codex-review:codex-reviewer` の SubagentStop で、`last_assistant_message` に `Status: pass|findings` 行がちょうど 1 行ある場合
- `codex-advisor:review-runner` の SubagentStop で、実質末尾 3 行の footer (`Codex-Runner-Operation: review` / `Codex-Runner-Status: success` / `Codex-Runner-Job-ID: <id>`) が揃っている場合

### checkpoint

5 サイクル完了後、次の review 起動は PreToolUse hook が deny し、main session の停止は Stop hook が block します。checkpoint の実行主体は本 plugin ではなく `codex-advisor` plugin です。`codex-advisor:consult` skill の review cadence mode が `codex-advisor:advisor-runner` を `model: "sonnet"`, `run_in_background: false` で foreground 起動し、`<review_cycle_checkpoint>` (Goal と受入基準・制約 / 直近 5 サイクルの review 履歴 / 現在の方針と不確実性 / course-correction の問い) を材料に根本方針の壁打ちを行います。

カウンターの reset は次の 3 経路に限られます:

1. advisor runner が checkpoint request の成功を `Codex-Advisor-Review-Cadence: satisfied` で証明したとき
2. advisor runner が checkpoint request を完了できないことを `unavailable` で証明したとき (terminal-failure)
3. checkpoint 相談 (相談 request に `<review_cycle_checkpoint>` を含む) の `codex-advisor:advisor-runner` の起動失敗が PostToolUseFailure として本 script に到達したとき (fail-open — codex-advisor 未 install 環境で checkpoint が解除不能な block にならないようにする)。`<review_cycle_checkpoint>` を含まない通常の advisor 相談の起動失敗、およびユーザ interrupt による abort (`is_interrupt: true`) はこの経路に含まれない。起動失敗が本 script に到達しない失敗形・環境では自動解除されないため、その場合は [state](#state) の手動 reset (state ファイル削除) で解除する

### state

review cadence の state は session ごとに 1 ファイル (ファイル名は session ID の sha256 hex + `.json`) として `$TMPDIR/pre-push-codex-review-<uid>/cadence-state/` (既定値) に保存します。root は環境変数 `PRE_PUSH_CODEX_REVIEW_CADENCE_STATE_ROOT` で差し替えられます。state に prompt や Codex 出力は保存しません。

手動で checkpoint 要求を解除するには、該当 session の state ファイルを削除してください。SessionEnd hook が session 終了時に自動削除するため、通常は手動削除は不要です。

### codex-advisor 連携

checkpoint の実行には `codex-advisor` plugin の install が必要です。まず必ず `codex-advisor:advisor-runner` の foreground 起動を試みてください。起動失敗 (未認証・timeout・plugin 未 install 等) が PostToolUseFailure として本 script に到達すれば (相談 request に `<review_cycle_checkpoint>` を含む場合のみ)、fail-open としてカウンターを reset するため block は解除されて続行できます。起動を試みた後も block が解除されない場合は、codex-advisor plugin の install が必要であることをユーザに報告したうえで、[state](#state) の手動 reset (state ファイル削除) で解除してください。`pre-merge-codex-review` の `codex-reviewer` subagent も本 plugin の review cadence の計数対象です。

## pre-push-review core との併用設計

本 plugin は `pre-push-review` core との併用を前提としつつ、単独 install でも自立動作します。そのため次の設計を採ります:

- **共通 gate の独立実装**: push 検出・複合コマンド解析・target 解決・dirty-tree gate・`push.default=matching` 検出・空 push 判定などの共通ロジックは、本 plugin と core がそれぞれ独立に実装します (単独 install の自立動作要件のため。共有ライブラリとして切り出す場合の同一性維持は下記「共有 lib の同一性」を参照)
- **deny 文の独立性**: 各 plugin の deny メッセージは自分が検証するマーカーのみに言及します。本 plugin の deny 文は codex マーカーの状態と `pre-push-codex-review:codex-reviewer` への案内のみを含み、core の code / security マーカーには言及しません。core の deny 文も同様に codex マーカーには言及しません
- **AND 合成**: 両 plugin を併用した場合、`git push` を含む Bash 呼び出しは両方の PreToolUse hook を通過します。どちらか一方でも deny を返せば push は成立しません。3 レビューすべてのマーカーが最新の差分と一致して初めて push が通ります

## 共有 lib の同一性

`hooks/scripts/lib/cmd-parser.sh` / `target-resolver.sh` / `diff-hash.sh` は `pre-push-review` core (`plugins/pre-push-review/hooks/scripts/lib/`) が canonical で、本 plugin はその byte-identical なコピーを保持します。同一性は `tests/test_shared_lib_copies.py` の契約テストと `.github/workflows/sync-shared-libs.yml` が検査します。

逆に `hooks/scripts/lib/codex-companion-resolver.sh` は本 plugin が canonical で、`codex-advisor` (`plugins/codex-advisor/scripts/lib/codex-companion-resolver.sh`) がそのコピーを保持し追従します。

`hooks/scripts/lib/markers.sh` は plugin ごとにマーカー集合が異なる (本 plugin は codex マーカーのみ、core は code / security マーカーのみ) ため、同一性検査の対象外です。

## ファイル構成

| パス | 役割 |
|---|---|
| `hooks/hooks.json` | フック配送経路の定義 |
| `hooks/scripts/block-pre-push-codex.sh` | push gate 本体 (PreToolUse) |
| `hooks/scripts/block-bg-codex-wrapper.sh` | codex review wrapper の background 起動検知 (PreToolUse) |
| `hooks/scripts/auto-mark.sh` | codex マーカーの自動発行 (SubagentStart / SubagentStop / PostToolUseFailure) |
| `hooks/scripts/manage-review-cadence.mjs` | review cadence の state 管理と enforcement (PreToolUse / SubagentStart / SubagentStop / PostToolUseFailure / Stop / SessionEnd) |
| `hooks/scripts/inject-review-cadence-rules.sh` | review cadence 規律の SessionStart 注入 |
| `hooks/prompts/review-cadence-rules.md` | review cadence 規律の本文 (SessionStart additionalContext) |
| `hooks/scripts/run-pre-push-codex-review.sh` | codex review wrapper 本体 (basename は core の wrapper と別名) |
| `hooks/scripts/lib/cmd-parser.sh` | Bash command のセグメント分割・tokenize (core からの byte-identical コピー) |
| `hooks/scripts/lib/target-resolver.sh` | push target cwd の解決 (core からの byte-identical コピー) |
| `hooks/scripts/lib/diff-hash.sh` | レビューハッシュ計算・空 push 判定 (core からの byte-identical コピー) |
| `hooks/scripts/lib/markers.sh` | 本 plugin のマーカーファイル名の単一ソース |
| `hooks/scripts/lib/exit-trap.sh` | 予期せぬエラー時の診断 trap |
| `hooks/scripts/lib/codex-companion-resolver.sh` | codex companion 解決ロジック (本 plugin が canonical) |
| `agents/codex-reviewer.md` | `pre-push-codex-review:codex-reviewer` subagent 定義 |
| `commands/review.md` | `/pre-push-codex-review:review` コマンド定義 |

## 関連プラグイン

- [pre-push-review](../pre-push-review/): code review / security review の 2 マーカーを gate する core。本 plugin と併用すると 3 レビュー構成になる
- [codex-advisor](../codex-advisor/): `hooks/scripts/lib/codex-companion-resolver.sh` のコピー先 (本 plugin が canonical) であり、[review cadence](#review-cadence) の checkpoint 実行主体でもある
- [git-guardrails](../git-guardrails/): default branch (master/main) への直接書き込みを deny。本 plugin は default branch 上の push を git-guardrails に委譲する
