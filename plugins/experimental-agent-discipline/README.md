# experimental-agent-discipline プラグイン

[agent-discipline](../agent-discipline/README.md) の実験的 fork です。agent-discipline が全経路で deny している Fable サブエージェントを、effort low 固定の専用 agent への明示委任に限り、Fable 週次枠の使用率が閾値以下のあいだだけ許可します。それ以外の規律配送・検知機能は agent-discipline と同一です。

## バージョン

v0.1.0

## 位置づけ

- agent-discipline の全ファイルを複製した fork です。差分は Fable サブエージェント関連 (hook の判定・分業規律本文・専用 agent 定義) に限定しています。
- **agent-discipline との同時 enable は非サポート**です。両方を enable すると、PreToolUse hook は 1 つでも deny すれば起動が止まるため agent-discipline 側の無条件 deny が優先され、Fable 委任は許可されません。加えて常時適用ルール・分業規律が二重に注入されます。どちらか一方だけを enable してください。
- state ディレクトリ (`${TMPDIR:-/tmp}/agent-discipline-state`) と marker 名は agent-discipline と共通です。切り替えてもセッション state は引き継がれます。
- fork 元 (agent-discipline v0.25.2) の更新の取り込みは手動です。agent-discipline を更新したときは、差分ファイル以外を fork へ反映する作業が別途必要です。

## インストールと切り替え

```bash
claude plugin install experimental-agent-discipline@natsuume-plugins
```

enable / disable は `~/.claude/settings.json` の `enabledPlugins` で切り替えます。

experimental へ切り替える (agent-discipline を止めて experimental を使う):

```json
{
  "enabledPlugins": {
    "agent-discipline@natsuume-plugins": false,
    "experimental-agent-discipline@natsuume-plugins": true
  }
}
```

agent-discipline へ戻す:

```json
{
  "enabledPlugins": {
    "agent-discipline@natsuume-plugins": true,
    "experimental-agent-discipline@natsuume-plugins": false
  }
}
```

切り替えは Claude Code の再起動 (新しいセッションの開始) 後に反映されます。

## 依存・前提

- **natsuume-statusline が statusline として構成済みであること**。Fable 週次枠の使用率は natsuume-statusline が `${XDG_CACHE_HOME:-$HOME/.cache}/natsuume-statusline/weekly-scoped.json` に書く cache から読みます。本 plugin はこの cache を書かず、OAuth usage API も直接呼びません。未構成の場合、cache が存在しないため Fable 委任は常に deny されます。`/natsuume-statusline:setup` で構成してください。
- **`CLAUDE_CODE_SUBAGENT_MODEL` が設定されていないこと**。この env は model の明示指定より優先されるため、設定されている環境では専用 agent が Fable 以外のモデルで effort low のまま実行されてしまいます。そのため、env が非空の環境では専用 agent への Fable 委任も deny されます (env が fable を指す場合も従来どおり deny)。Fable 委任を使うときは env を解除してください。
- **`CLAUDE_CODE_EFFORT_LEVEL` が未設定か `low` であること**。この env は agent 定義 frontmatter の `effort: low` より優先されるため、low 以外に設定されている環境では専用 agent が高 effort で Fable 枠を消費してしまいます。そのため、env が low 以外の環境では専用 agent への Fable 委任は deny されます。Fable 委任を使うときは env を解除するか low にしてください。
- `jq` が使えること (agent-discipline と同じ前提。jq 不在時は hook が何もせず通過します)。
- **natsuume-statusline の cache 更新が続いていること**。natsuume-statusline は、Claude Code が statusline の stdin にモデル別枠 (`rate_limits.model_scoped[]`) を渡し始めると、その公式経路を優先して `weekly-scoped.json` の更新を止めます。その状態では cache が stale (fetched_at が 1800 秒より前) になり、本 plugin の Fable 委任は `/natsuume-statusline:setup` を実行しても deny のままになります。この条件は本 plugin の既知の制約であり、gate が公式経路の値を読む対応は未実装です。

## 使い方

### 専用 agent

| agent | subagent_type | tools | 用途 |
|---|---|---|---|
| `fable-low-worker` | `experimental-agent-discipline:fable-low-worker` | 親から全て継承 | 仕様が確定した実装・機械的な一括修正・レビュー指摘の反映 |
| `fable-low-explorer` | `experimental-agent-discipline:fable-low-explorer` | `Bash, Read, Glob, Grep, LS` (読み取り専用) | 実装箇所の特定・既存パターンの洗い出し・仮説の裏取り |

両 agent とも定義の frontmatter で `model: fable` / `effort: low` に固定されています。effort は Agent 呼び出しごとには指定できないため、呼び出し側は effort を扱いません。

### 起動契約

Agent ツールで `subagent_type` に上記のいずれかを指定し、**あわせて `model: "fable"` を明示**します。model を省略すると継承経路として deny されます (継承経路では使用率判定を通らずに frontmatter の `model: fable` が適用されてしまうため)。

### 閾値の変更

環境変数 `EXPERIMENTAL_FABLE_SUBAGENT_MAX_PERCENT` に 0〜100 の 10 進整数を設定すると、許可する使用率の上限が変わります。未設定・空・範囲外・非整数のときは既定値 50 を使います。使用率が閾値ちょうどのときは許可し、超過で deny します。

### deny 理由ごとの対処

| deny 理由 | 対処 |
|---|---|
| `CLAUDE_CODE_SUBAGENT_MODEL` が設定されている | env を解除して再実行する。env はセッションを超える設定のため、解除はユーザに依頼する。急ぐ場合は sonnet / opus へ通常委任する |
| `CLAUDE_CODE_EFFORT_LEVEL` が low 以外に設定されている | env を解除するか low にして再実行する。env はセッションを超える設定のため、変更はユーザに依頼する。急ぐ場合は sonnet / opus へ通常委任する |
| subagent_type が専用 agent 2 種のいずれでもない | subagent_type を専用 agent に変えるか、model に sonnet / opus (機械的作業なら haiku) を明示して通常委任する |
| model 未指定 (継承) で専用 agent を起動した | `model: "fable"` を明示して再実行する |
| 使用率 cache が無い・読めない・壊れている・古い (1800 秒超) | `/natsuume-statusline:setup` で natsuume-statusline を構成する。構成済みなら statusline が動作しているか確認する。復旧までは sonnet / opus へ通常委任する |
| 使用率 cache に Fable の週次枠 entry が無い | Fable 枠が使用率 API に現れているかを確認する。現れない間は sonnet / opus へ通常委任する |
| 使用率が閾値を超えている | deny メッセージの `resets_at` (枠のリセット時刻) まで待つか、sonnet / opus へ通常委任する。閾値自体を上げる場合は `EXPERIMENTAL_FABLE_SUBAGENT_MAX_PERCENT` を設定する |

## hook の判定順序

`hooks/scripts/block-fable-subagent.sh` (PreToolUse、matcher `Agent|Task`) が上から順に評価し、最初に該当した結果を返します。すべて deterministic な文字列判定で、LLM 評価は使いません。

0. `CLAUDE_CODE_SUBAGENT_MODEL` が fable を指す → deny (`tool_input.model` の値に依らない)
1. `tool_input.model` に fable が指定されている
   - a. `subagent_type` が専用 agent 2 種に完全一致 (namespace prefix 必須、前後空白は trim、大文字小文字は区別)
     - `CLAUDE_CODE_SUBAGENT_MODEL` が非空 → deny (env 解除の案内)
     - `CLAUDE_CODE_EFFORT_LEVEL` が low 以外 → deny (env の解除または low への変更の案内)
     - どちらも未設定 → Fable 週次枠の使用率判定へ
   - b. それ以外の `subagent_type` → deny (専用 agent への切り替え、または sonnet / opus への通常委任の案内)
2. `tool_input.model` が非 fable の具体指定 → allow
3. `tool_input.model` 未指定 (継承経路)
   - a. `subagent_type` が専用 agent 2 種に完全一致 → deny (`model: "fable"` の明示を案内)
   - b. それ以外 → env が非空なら allow。env 不在で subagent 内 (hook 入力に `agent_id` あり) からの起動なら deny (継承先が起動元 subagent のモデルになり、専用 agent から Fable を継承しうるため)。env 不在ならセッションの model state が fable のとき deny、state 不明かつ pending マーカーがあるとき deny、それ以外は allow

Fable 週次枠の使用率判定 (Step 1a):

- `weekly_scoped[]` のうち `display_name` が大文字小文字を無視して fable を含み、`percent` が数値の entry を対象とし、複数あれば `percent` の最大値で判定する
- `percent <= 閾値` なら allow
- 閾値超過、および cache 不在・通常ファイルでない (symlink を含む)・読めない・JSON として parse できない・`fetched_at` 欠落/非数値・`fetched_at` が 1800 秒より古い・`weekly_scoped` 欠落/非配列/空・Fable entry 無し・`percent` が全て非数値 のときは deny する (fail-closed)
- `fetched_at` が未来の時刻でも stale とはみなさない (時計ずれを許容する)
- この判定は Agent / Task tool による**起動時の入場判定**です。起動後の継続 (SendMessage による同一 agent の再開)、Workflow tool 内部の `agent()` 呼び出し、fork subagent は PreToolUse `Agent|Task` で観測できないため再判定しません。起動時に閾値以下だった専用 agent が動作中に閾値を超えても停止しません

## agent-discipline との差分ファイル

以下以外のファイルは agent-discipline と同一です。

fork 側にのみ存在するファイル:

- `agents/fable-low-worker.md`
- `agents/fable-low-explorer.md`

内容が異なるファイル:

- `.claude-plugin/plugin.json` (name / version / description)
- `README.md` (本ファイル)
- `hooks/hooks.json` (`description` のみ。`hooks` 配下の event / matcher / command は同一)
- `hooks/scripts/block-fable-subagent.sh` (条件付き許可ロジック)
- `hooks/scripts/inject-subagent-rules.sh` (ヘッダコメントの前提説明のみ。挙動は同一)
- `hooks/prompts/discipline-fable.md` / `discipline-sonnet.md` / `discipline-opus.md` (rule:delegation-rules 節の Fable 委任条件と effort の記述のみ)
- `scripts/lint-prompt-sync.sh` (検査対象 path が本 plugin 配下を指す。検査内容は同一)

## その他の機能

常時適用ルールの配送 (`inject-always.sh` / `inject-rules-part.sh` / `inject-discipline.sh` / `inject-temporary.sh` / `resolve-model-on-prompt.sh` / `inject-auto.sh` / `check-uncommitted-on-session-start.sh` / `inject-subagent-rules.sh`)、`gh issue/pr create|edit` の body 検知 hook、`issue-plan` / `issue-start` skill は agent-discipline と同一です。skill は `experimental-agent-discipline:issue-plan` / `experimental-agent-discipline:issue-start` の namespace で提供されます。詳細は [agent-discipline の README](../agent-discipline/README.md) を参照してください。

## キーワード

`experimental` `system-prompt` `discipline` `fable` `subagent` `effort` `rate-limit` `hook` `guardrail`
