# fable-discipline

Fable (Mythos 級モデル) をメインセッションで使う際の **分業規律** を配送するプラグインです。Fable は高性能ですが、サブエージェントまで Fable で実行されると (特に dynamic workflow で大量スポーンされる場面で) コストが膨張します。本プラグインは「Fable = 司令塔、実作業 = Sonnet/Opus」という分業を、誘導層 (additionalContext 注入) と防波堤層 (PreToolUse deny) の 2 層で支えます。

## 前提: 主防御は CLAUDE_CODE_SUBAGENT_MODEL

サブエージェントのモデル強制の **主防御は本プラグインではなく** `CLAUDE_CODE_SUBAGENT_MODEL` 環境変数です (プラグインは env を設定できないため、利用者が `settings.json` の `env` に設定します):

```json
{
  "env": {
    "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"
  }
}
```

この env は Claude Code 公式のモデル解決順序で最優先であり、CC 2.1.201 + Fable メインセッションでの実測で以下を確認済みです:

| 経路 | env 未設定時の挙動 | env = sonnet での実測 |
|---|---|---|
| Agent ツール・model 未指定 | メインセッション (Fable) を継承 | claude-sonnet-5 |
| Agent ツール・model 明示指定 (opus) | opus | claude-sonnet-5 (**明示指定も上書き**) |
| Workflow 内部の `agent()`・model 未指定 | メインセッションを継承 | claude-sonnet-5 (**hook が届かない経路もカバー**) |

つまり env が設定されている限り fable 継承は構造的に発生せず、本プラグインの hook は **env 設定が外れた場合の defense-in-depth** として機能します。逆に env が `sonnet` の間はサブエージェントに opus を使う手段もない (明示指定が黙って sonnet に落ちる) 点は運用上のトレードオフです。

## 配送される 2 レイヤ

| レイヤ | 配送経路 | 発火条件 | 内容 |
|---|---|---|---|
| **誘導層 (分業規律)** | `SessionStart` (`inject-fable-role.sh`) | stdin の `model` が fable = 無条件文 / `model` 欠落 (/clear 直後等) = 自己ゲート文 / 非 fable = 注入なし | Fable の担当 (曖昧さの分解・他モデル向け指示書作成・全体設計検討・検収) とサブエージェントへの委任対象 (明確化済み仕様の実装・具体調査・機械的作業) を規定。委任時は model 明示 + self-contained 指示 + 実作業者が意思決定不要な粒度。数行規模の自明修正のみ直接編集可。fork は原則禁止 |
| **防波堤層 (物理 deny)** | `PreToolUse` (matcher: `Agent\|Task`, `block-fable-subagent.sh`) | Agent / Task ツール呼び出し時 | 判定順序をモデル解決順序 (env > 明示指定 > 継承) と一致させる: (0) `CLAUDE_CODE_SUBAGENT_MODEL` が fable を指す場合は model の値に依らず無条件 deny (env は明示指定より優先のため)。(1) `tool_input.model` の fable 明示指定を deny。(2) 非 fable の具体指定は allow。(3) model 未指定 (`"inherit"` は trim + case-insensitive で未指定に正規化) は env が非 fable なら allow、env 不在時は SessionStart が記録した session model state が fable の場合のみ deny。判定不能はすべて fail-open |

## 機能

### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `inject-fable-role` | SessionStart | 分業規律をハイブリッド方式で `additionalContext` 注入。model 判定値を state file (`${TMPDIR:-/tmp}/fable-discipline-state/model-<session_id>`) に記録し、防波堤層と共有する。SessionStart は startup / resume / clear / compact の全 source で発火するため、compact 後も規律が再注入される |
| `block-fable-subagent` | PreToolUse (`Agent\|Task`) | env の fable 強制・fable 明示指定・「model 未指定 + fable セッション継承」の 3 経路を deterministic な文字列判定で deny。LLM 評価型 hook は使わない (モデル可用性 SPOF の回避)。deny メッセージが sonnet / opus の明示 (env 起因の場合は env 設定の修正) を促すため、Claude は自己修正して再実行できる |

## 設計判断と既知の制約

- **自己ゲート文が成立する理由**: SessionStart の `model` フィールドは公式に「保証なし」(欠落しうる) だが、受信側のモデルは自身の system prompt (Environment セクション) で自分が Fable かを確実に判別できるため、「Fable の場合のみ適用」という自己ゲート文は fail-safe として機能する。
- **Workflow 内部の `agent()` は hook で捕捉できない**: PreToolUse はメインループのツール呼び出しにのみ発火するため、Workflow スクリプト内部のサブエージェントスポーンは防波堤層の対象外。この経路は env が (実測どおり) カバーする。
- **セッション途中の `/model` 切替は検知できない**: モデル切替に発火する hook イベントが存在しないため、誘導層は次の SessionStart (clear / compact / resume) まで旧判定のまま。env による強制はモデル非依存に効くため、コスト防御は破れない。
- **fork subagent は hook で deny しない**: fork は全会話コンテキストを継承し model 指定を無視する型で、Fable セッションでは入力コスト面から誘導層で「原則使用しない」と規定する。ただしコンテキスト継承が不可欠な委任をブロックしない柔軟さを優先し、物理 deny は行わない。
- **agent frontmatter の model は判定できない**: agent 定義 frontmatter の `model` は `tool_input` に現れないため、env 不在 + model 未指定 + frontmatter が fable を指す構成は hook では捕捉できない。この経路も env 側でカバーする。
- **不明な env 値は authoritative として信頼する**: `CLAUDE_CODE_SUBAGENT_MODEL` が非空・非 inherit で fable を含まない場合、その妥当性検証は Claude Code 本体と利用者の責務とし、hook は非 fable 値として信頼する (確信境界の外の値を deny 根拠にしない)。
- **fail-open ポリシー**: jq 不在・入力 JSON 不正・state file 不在などの判定不能ケースはすべて allow / 注入スキップ側に倒す。本プラグインは courtesy guard であり、誤 deny でセッションを止めるコストの方が高い。

## キーワード

`fable` `model-cost` `subagent` `delegation` `system-prompt` `hook` `guardrail` `workflow`
