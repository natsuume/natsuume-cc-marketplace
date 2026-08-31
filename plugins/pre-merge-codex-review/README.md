# pre-merge-codex-review プラグイン

`gh pr merge` を実行する前に **codex review** (OpenAI クロスモデルレビュー) の完了を必ず実行させ、未レビューな PR が merge されるのを構造的にブロックするプラグインです。個人環境 (ChatGPT Plus の codex CLI) 向けに、`git push` の都度ではなく **merge 前に 1 回だけ** codex review を行う運用を成立させます。単独 install で自立動作します。

レビュー対象は PR の merge-base..head 全差分です。marker は「リポジトリ identity + PR 番号 + merge-base OID + head OID + 全差分 hash」の 5 key を束縛しており、修正・追加 commit・base の force-push 等でこれらのいずれかが変わると marker は自動失効し、Claude は `pre-merge-codex-review:codex-reviewer` subagent を再走させる以外に merge を通す手段がありません。

## バージョン

v1.0.0

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install pre-merge-codex-review@natsuume-plugins
```

公式 codex プラグインへの依存があるため、codex review wrapper を動作させるには次も install してください:

```bash
claude plugin install codex@openai-codex
```

codex-advisor を併用する場合は v2.2.0 以上 (本 plugin の reviewer namespace を cadence 計数対象に含む版) を使用してください。

### 依存コマンド

`jq` は merge gate の必須依存です。`gh` は PR metadata の取得に必須です。いずれかが見つからない環境では、未レビューの merge を通さないため `block-pre-merge.sh` が `gh pr merge` を fail-closed に deny し、インストール後の再実行を案内します。merge と無関係な Bash 呼び出しは影響を受けません。

## 機能一覧

### Hooks

#### 1. block-pre-merge (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-pre-merge.sh`

`gh pr merge` を含む Bash コマンドを検出した際、次の手順で merge を許可するかを判定します:

1. 粗フィルタ (`gh` / `pr` / `merge` の存在) を通過したコマンドを `lib/cmd-parser.sh` でトークン化し、`gh pr merge <PR 番号 | URL 等>` 相当の invocation を検出する。解析不能な形 (未対応の quote / heredoc / indirection 構文等) は保守的に deny する
2. `--auto` (auto-merge 予約) を検出した場合は marker の有無に関わらず常に deny する。`--auto` は GitHub 側が条件成立を待って後から実 merge するため、予約時点のローカル gate 判定と実 merge 実行のタイミングが分離し、ローカル gate の射程外になる。この経路は fail-closed に deny する
3. marker (`.claude-pre-merge-codex-reviewed`) を読み、`repo` / `pr` / `merge_base` / `head` / `diff_hash` の 5 key が揃っているかを確認する。marker が無い、または key が欠けていれば deny する
4. `gh pr view [<対象指定>] --json <fields>` で merge 対象 PR の実 metadata (PR 番号 / head commit OID / リポジトリ identity) を取得する。`gh pr merge` の対象指定 (PR 番号 / URL / branch / 省略) は位置引数としてそのまま転送し、`-R` / `--repo` による repo 指定の値も転送する。取得に失敗したら deny する
5. merge 対象 PR の `isMergeQueueEnabled` (base branch が merge queue を要求するか) を `gh api graphql` で取得し、true なら marker の状態に関わらず deny する (このフィールドは `gh pr view --json` では取得できないため GraphQL を直接使う)。gh は merge queue 必須 branch への `gh pr merge` を `--auto` の有無に依らず即時 merge せず遅延実行 (checks 未完了なら auto-merge 有効化、完了済みなら enqueue) に倒すため、`--auto` と同じ理由でローカル gate の射程外になる
6. marker の `repo` / `pr` / `head` を実 metadata と照合し、1 つでも不一致なら deny する
7. marker の `merge_base` が base branch との現在の merge-base OID と一致するかを検証する
8. marker の `diff_hash` を、`lib/diff-hash.sh` の `compute_review_hash_in` と同じ計算式で merge-base..head 全差分から再計算した hash と照合する
9. ローカル branch HEAD が remote PR head と一致することを検証する
10. 1〜9 のいずれかで不一致・取得失敗・判定不能があれば deny する (fail-closed)。すべて一致した場合のみ allow し、merge コマンドの `merge` サブコマンドトークン末尾直後へ ` --match-head-commit <レビュー済み head OID>` を自動挿入する (enforce-draft-pr と同型の offset 挿入 + updatedInput 方式。挿入以外は 1 バイトも変更しない)。コマンドが既に `--match-head-commit` (分離形 / `=` 連結形) を指定している場合は、その値がレビュー済み head OID と一致するときのみ allow してコマンドを書き換えず、異なる OID を指定しているときは deny する。gate 検証後から実 merge までの間に remote head が更新される TOCTOU は、GitHub 側の `--match-head-commit` 検証が遮断する

#### 2. block-bg-codex-wrapper (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-bg-codex-wrapper.sh`

codex review wrapper (`run-pre-merge-codex-review.sh`) の起動を検証する PreToolUse hook です。hook payload トップレベルの `agent_type` が `pre-merge-codex-review:codex-reviewer` (namespace 付き完全一致) でなければ fail-closed に deny します。background 起動・pipeline 経由の起動も同様に deny します。wrapper の basename を `pre-push-codex-review` の wrapper (`run-pre-push-codex-review.sh`) と別名にしているのは、両 plugin が併存する環境で互いの wrapper 検出 gate (basename ベース) が相手の wrapper 起動を deny し合う干渉を塞ぐためです。

#### 3. auto-mark (SubagentStart / SubagentStop / PostToolUseFailure)

**ファイル**: `hooks/scripts/auto-mark.sh`

`pre-merge-codex-review:codex-reviewer` subagent の実行完了を subagent lifecycle hook (SubagentStart / SubagentStop) で検知し、codex マーカーに 5 key を書き込みます。`SubagentStart` はレビュー開始時の束縛情報を launch attestation として one-shot 記録し、`SubagentStop` は attestation の一回限りの消費・開始時点と現在の一致・`last_assistant_message` 内の単一 `Status: pass|findings` 行・wrapper が書いた pending attestation との一致をすべて検証した場合のみ marker を書きます。`PostToolUseFailure` では残った pending attestation を破棄します。

マーカーが証明するのは、codex review が marker に記録された PR head / merge-base / diff に対して完了したことだけです。merge の approve や findings が 0 件であることは証明しません。

### マーカーファイル

すべて `<git-dir>` 配下に配置 (リポジトリ単位で共有、ブランチ単位ではない):

| ファイル | 内容 | 寿命 |
|---|---|---|
| `.claude-pre-merge-codex-reviewed` | 改行区切りの key=value で 5 key を束縛する codex review 完了 marker: `repo=<owner>/<name>` (merge 対象 PR のリポジトリ identity)・`pr=<number>` (PR 番号)・`merge_base=<OID>` (base branch との merge-base commit OID)・`head=<OID>` (レビュー時の PR head commit OID)・`diff_hash=<sha256>` (merge-base..head 全差分の hash) | 5 key のいずれかが実 PR metadata・現在の merge-base・現在の diff と一致しなくなると失効 (明示削除しない)。base tip だけが進み head / merge-base が不変なら失効しない |
| `.claude-pre-merge-codex-reviewed.pending` | wrapper が束縛した review 対象の 5 key (pending attestation) | report 失敗・不一致・次回 wrapper 起動で削除。codex-reviewer の terminal な拒否 stop でも破棄 |
| `.claude-pre-merge-launch-<agent_id>` | SubagentStart が one-shot 記録するレビュー開始時の束縛情報 (launch attestation)。SubagentStop が開始時点と現在の一致検証に使う | 最初の SubagentStop で消費 (削除) |
| `.claude-pre-merge-done-<agent_id>` | attestation 消費時に排他作成される launch tombstone。同一 agent_id での SubagentStart 再発火による attestation 再鋳造を遮断する | 無期限保持 (prune しない) |

`pre-push-review` core や `pre-push-codex-review` が発行するマーカー (`.claude-pre-push-*`) は本 plugin の関知対象外です。逆に他 plugin は本 plugin の `.claude-pre-merge-*` マーカーを検証しません。

### Agents

#### `pre-merge-codex-review:codex-reviewer` (subagent)

**ファイル**: `agents/codex-reviewer.md`

codex review wrapper (`hooks/scripts/run-pre-merge-codex-review.sh`) を foreground で 1 回起動し、wrapper の stdout / stderr を subagent context 内で評価して parent-safe markdown report に抽象化する最小 subagent です。tools は `Bash, TaskOutput, Read` に制限され (Edit / Write / Skill / Task はすべて非許可)、model は `sonnet` に固定されます。wrapper は exit 0 完了時に 5 key を pending attestation として atomic write し、auto-mark が subagent の正規 `pass/findings` report と現在の状態の一致を確認して codex-reviewed マーカーへ昇格します。

## 既知の制約

- **`--auto` は常に deny**: `gh pr merge --auto` は GitHub 側が条件成立を待って後から実 merge を行うため、ローカル gate が判定できるのは予約操作の時点だけであり、実際の merge 実行を検証できません。この経路はサポート外として常に deny します
- **merge queue 必須の base branch への merge も常に deny**: gh は merge queue が必須の branch への `gh pr merge` を `--auto` の有無に依らず即時 merge せず遅延実行 (checks 未完了なら auto-merge 有効化、完了済みなら enqueue) に倒すため、予約時点と実 merge 実行が分離し、`--auto` と同じ理由でサポート外として deny します。判定には gh 自身が enqueue 判定に使う PR の GraphQL field `isMergeQueueEnabled` を用います (marker の有無に依らず deny)。merge queue をバイパスする `--admin` を付けた場合も同様に deny します
- **base tip の前進のみでは marker は失効しない**: base branch の tip だけが進み、PR の head commit と merge-base OID が変わらない場合 (= marker の 5 key がすべて維持される場合) は marker を失効させません。この状態で他の変更が base に同時に merge されたことによる semantic conflict (テキスト上は衝突しないが意味的に矛盾する変更) は本 plugin の検証範囲外です

## pre-push-review / pre-push-codex-review との併用設計

本 plugin は単独 install で自立動作し、`pre-push-review` core (`git push` 前の code review / security review gate) と併用しても push gate に一切影響しません。push gate (`git push`) と merge gate (`gh pr merge`) は独立した PreToolUse hook であり、互いのマーカー・判定に関知しません。

本 plugin は個人環境 (ChatGPT Plus の codex CLI) 向けに「merge 前に 1 回だけ codex review」を運用する設計です。push の都度 codex review を要求する会社環境向け `pre-push-codex-review` との併用は前提としていません。両者を併用すると、`git push` は `pre-push-codex-review` の gate で都度 codex review を要求され、`gh pr merge` は本 plugin の gate で追加の codex review を要求されるため、同一差分に対して重複した codex review が発生します。会社環境では `pre-push-codex-review` のみを install してください。

## 共有 lib の同一性

`hooks/scripts/lib/cmd-parser.sh` / `target-resolver.sh` / `diff-hash.sh` は `pre-push-review` core (`plugins/pre-push-review/hooks/scripts/lib/`) が canonical で、本 plugin はその byte-identical なコピーを保持します。

`hooks/scripts/lib/codex-companion-resolver.sh` は `pre-push-codex-review` (`plugins/pre-push-codex-review/hooks/scripts/lib/`) が canonical で、本 plugin はその byte-identical なコピーを保持します。

reviewer 一式 3 ファイル (`hooks/scripts/block-bg-codex-wrapper.sh` / `hooks/scripts/auto-mark.sh` / `agents/codex-reviewer.md`) は `pre-push-codex-review` の対応ファイルと、namespace 文字列 (`pre-push-codex-review` ↔ `pre-merge-codex-review`)・wrapper basename (`run-pre-push-codex-review.sh` ↔ `run-pre-merge-codex-review.sh`)・marker prefix (`.claude-pre-push-` ↔ `.claude-pre-merge-`) の置換を除き同型です。

codex review wrapper (`hooks/scripts/run-pre-merge-codex-review.sh`) は、review 対象の決定 (default base 検出ではなく実 PR base) と attestation の内容 (単一 hash ではなく 5 key) が pre-push 系と本質的に異なるため、同一性検査の対象外として pre-merge 専用に独立実装します (実質差分を持つ部品を無理に共通化しない)。

`hooks/scripts/lib/markers.sh` は plugin ごとにマーカー集合が異なるため、同一性検査の対象外です。

以上の同一性は `tests/test_pre_merge_lib_copies.py` の契約テストが検査します。

## ファイル構成

| パス | 役割 |
|---|---|
| `hooks/hooks.json` | フック配送経路の定義 |
| `hooks/scripts/block-pre-merge.sh` | merge gate 本体 (PreToolUse) |
| `hooks/scripts/block-bg-codex-wrapper.sh` | codex review wrapper の background 起動検知 (PreToolUse) |
| `hooks/scripts/auto-mark.sh` | codex マーカーの自動発行 (SubagentStart / SubagentStop / PostToolUseFailure) |
| `hooks/scripts/run-pre-merge-codex-review.sh` | codex review wrapper 本体 (pre-merge 専用の独立実装。basename は `pre-push-codex-review` の wrapper と別名) |
| `hooks/scripts/lib/cmd-parser.sh` | Bash command のセグメント分割・tokenize (`pre-push-review` からの byte-identical コピー) |
| `hooks/scripts/lib/target-resolver.sh` | (`pre-push-review` からの byte-identical コピー。本 plugin では merge target の解決に用いる) |
| `hooks/scripts/lib/diff-hash.sh` | レビューハッシュ計算 (`pre-push-review` からの byte-identical コピー) |
| `hooks/scripts/lib/markers.sh` | 本 plugin のマーカーファイル名の単一ソース |
| `hooks/scripts/lib/exit-trap.sh` | 予期せぬエラー時の診断 trap |
| `hooks/scripts/lib/codex-companion-resolver.sh` | codex companion 解決ロジック (`pre-push-codex-review` からの byte-identical コピー) |
| `agents/codex-reviewer.md` | `pre-merge-codex-review:codex-reviewer` subagent 定義 |

## 関連プラグイン

- [pre-push-review](../pre-push-review/): `git push` 前の push gate (code review / security review の 2 マーカー)。本 plugin の merge gate とは独立に動作し、併用しても互いの gate に影響しません
- [pre-push-codex-review](../pre-push-codex-review/): 会社環境向けの push 毎 codex review gate。本 plugin と同時に install しない前提です
- [codex-advisor](../codex-advisor/): review cadence の計数対象に `pre-merge-codex-review:codex-reviewer` の namespace を含みます (v2.2.0 以上)
