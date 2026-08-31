# pre-push-codex-review プラグイン

`git push` を実行する前に **codex review** (OpenAI クロスモデルレビュー) の完了を必ず実行させ、未レビューな commit が remote に到達するのを構造的にブロックするプラグインです。単独 install で自立動作し、`pre-push-review` core (code review / security review の 2 レビュー gate) と併用すると、code / codex / security の 3 レビュー構成になります。

修正や commit 列の変更 (add→revert / amend / rebase 含む) により「commit 列 (HEAD / merge-base の OID) + ブランチ全差分」のハッシュが変わると codex マーカーは自動失効し、Claude は `pre-push-codex-review:codex-reviewer` subagent を再走させる以外に push を通す手段がありません。

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

codex-advisor を併用する場合は v2.1.0 以上 (本 plugin の reviewer namespace を cadence 計数対象に含む版) を使用してください。

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
- [codex-advisor](../codex-advisor/): `hooks/scripts/lib/codex-companion-resolver.sh` のコピー先。本 plugin が canonical
- [git-guardrails](../git-guardrails/): default branch (master/main) への直接書き込みを deny。本 plugin は default branch 上の push を git-guardrails に委譲する
