# fable-discipline

Fable (Mythos 級モデル) をメインセッションで使う際の **分業規律** を配送するプラグインです。Fable は高性能ですが、サブエージェントまで Fable で実行されると (特に dynamic workflow で大量スポーンされる場面で) コストが膨張します。本プラグインは「Fable = 司令塔、実作業 = Sonnet/Opus」という分業を、誘導層 (additionalContext 注入) と防波堤層 (PreToolUse deny) の 2 層で支えます。

## バージョン

- **v0.3.2**: hooks.json の description を簡潔な概要に刷新 (変更履歴・設計経緯は本 README へ集約する方針に統一。hook 動作の変更なし)
- **v0.3.1**: 注入プロンプトを `hooks/prompts/*.md` (preamble-fable.md / preamble-self-gate.md / discipline-body.md) に分離し、inject-fable-role.sh は判定・組み立て・配送のみを担う構成に変更 (視認性・メンテナンス性のため。注入内容は変更なし — 編集前後の hook 出力 JSON の diff で byte-identical を検証済み。md が読めない場合は fail-open で注入スキップ)。あわせて plugin description (plugin.json / marketplace.json) を人間向けの簡潔な概要に刷新 (変更履歴・実装詳細は本 README が正)
- **v0.3.0**: 誘導層と deny メッセージを Fable 5 / Sonnet 5 の公式プロンプトガイドに整合。誘導層に追加: 並列・非同期委任 (完了を待たず作業継続、介入条件はセクション 4 の発動条件に統一)、fresh-context verifier による検収の一次検証、SendMessage による同一エージェント継続 (Workflow `agent()` は対象外)、effort の難度別明示 (機械的 = low / 検証・判定 = high 以上。Agent ツールに effort が無い場合の代替文言付き)、self-contained 要件への目的・背景 (判断を委ねる根拠にしない限定付き)、literal 解釈を踏まえた一括修正の適用範囲明示、調査・レビュー系委任の coverage-first (自己フィルタ禁止 + confidence/severity 付き全件報告)、エスカレーション返却への根拠添付と受領時の grounding (申告を鵜呑みにしない)。統一: Haiku 許容を誘導層・防波堤層で一致 (機械的作業限定)。deny メッセージ変更: B-1 (env=fable) を報告 + 修正依頼型に (ユーザのグローバル設定を独断で書き換えない)、B-3 (継承 deny) に env 未設定の明示と主防御提案を追加、自己ゲート文の適用スコープ (本注入メッセージ全文) を明確化
- **v0.2.0**: 誘導層に「委任指示の必須要素 (3 面 + 安全弁)」(セクション 3) と「エスカレーションフロー」(セクション 4) を追加 (#138)。`/model` 切替 + env 不在時に防波堤層が双方向に破れる既知の制約を README と script ヘッダコメントに明文化 (#157。公式 hooks リファレンスの調査により、SessionStart 以外の hook 入力に model フィールドは無く state 更新頻度の向上は公式手段では実現不能と確認したため文書化対応)
- **v0.1.0**: 初版 (誘導層 + 防波堤層の 2 レイヤ構成)

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
| **誘導層 (分業規律)** | `SessionStart` (`inject-fable-role.sh`) | stdin の `model` が fable = 無条件文 / `model` 欠落 (/clear 直後等) = 自己ゲート文 / 非 fable = 注入なし | Fable の担当 (曖昧さの分解・他モデル向け指示書作成・全体設計検討・検収) とサブエージェントへの委任対象 (明確化済み仕様の実装・具体調査・機械的作業) を規定。委任時は model 明示 + self-contained 指示 + 実作業者が意思決定不要な粒度。数行規模の自明修正のみ直接編集可。fork は原則禁止。v0.2.0 で委任指示の必須要素 (禁止スコープ / What / How の 3 面 + default-deny 安全弁 + 終了時自己点検) とエスカレーションフロー (第三の正規終了) を追加。v0.3.0 でプロンプトガイド整合 (並列・非同期委任 / verifier 検収 / SendMessage 継続 / effort 明示 / 目的・背景 / 適用範囲明示 / coverage-first / 受領時 grounding / Haiku 許容統一) |
| **防波堤層 (物理 deny)** | `PreToolUse` (matcher: `Agent\|Task`, `block-fable-subagent.sh`) | Agent / Task ツール呼び出し時 | 判定順序をモデル解決順序 (env > 明示指定 > 継承) と一致させる: (0) `CLAUDE_CODE_SUBAGENT_MODEL` が fable を指す場合は model の値に依らず無条件 deny (env は明示指定より優先のため)。(1) `tool_input.model` の fable 明示指定を deny。(2) 非 fable の具体指定は allow。(3) model 未指定 (`"inherit"` は trim + case-insensitive で未指定に正規化) は env が非 fable なら allow、env 不在時は SessionStart が記録した session model state が fable の場合のみ deny。判定不能はすべて fail-open |

## 委任指示の必須要素とエスカレーションフロー

v0.2.0 で誘導層に追加した委任プロトコル (注入テキストのセクション 3・4):

- **委任指示の必須要素 (3 面 + 安全弁)**: 禁止 (スコープ付き) / What (成果物・受入条件) / How (実行検証の可否と副作用操作の手順固定) の 3 面を毎回検討し、さらに (a) 指示に無い副作用操作を実行せずエスカレーションさせる default-deny 定型文、(b) 副作用を許可した場合の終了時自己点検、を必須とする。事前の網羅は原理的に漏れるため、列挙 (3 面) と網羅性に依存しない安全弁 (定型文 + 自己点検) を組にしている
- **エスカレーションフロー**: 「完遂 / 失敗」に次ぐ第三の正規終了。発動条件 4 種 (指示外の副作用が必要 / 前提と実態の矛盾 / 受入条件を満たせない / 一意に決まらない選択肢) と返却フォーマット 5 項目 (判断を仰ぐ事項 / 該当条件 / 完了済み作業の所在 / 選択肢と判断材料 / 再開に必要な入力) を委任指示に定型で含める。メインセッションはタスクレベルの判断を自ら決定して SendMessage で同一エージェントを再開し、設計 / 仕様レベルの判断 (ユーザ専権事項) は AskUserQuestion を経てから再開する

背景: How が未指定の委任では、実作業者が目的達成圧力の下で手順を即興し事故につながる (2026-07-04 の実リポジトリ汚染事故、#138 の委任指示監査参照)。禁止の列挙だけでは即興を防げないため、正規の出口 (エスカレーション) を規律として定義した。

## 機能

### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `inject-fable-role` | SessionStart | 分業規律をハイブリッド方式で `additionalContext` 注入。model 判定値を state file (`${TMPDIR:-/tmp}/fable-discipline-state/model-<session_id>`) に記録し、防波堤層と共有する。SessionStart は startup / resume / clear / compact の全 source で発火するため、compact 後も規律が再注入される |
| `block-fable-subagent` | PreToolUse (`Agent\|Task`) | env の fable 強制・fable 明示指定・「model 未指定 + fable セッション継承」の 3 経路を deterministic な文字列判定で deny。LLM 評価型 hook は使わない (モデル可用性 SPOF の回避)。deny メッセージが sonnet / opus (機械的作業なら haiku) の明示を促すため、Claude は自己修正して再実行できる。env 起因 (Step 0) の場合はセッションを超える設定のため独断修正させず、ユーザへの報告と修正依頼を促す (v0.3.0) |

## 設計判断と既知の制約

- **自己ゲート文が成立する理由**: SessionStart の `model` フィールドは公式に「保証なし」(欠落しうる) だが、受信側のモデルは自身の system prompt (Environment セクション) で自分が Fable かを確実に判別できるため、「Fable の場合のみ適用」という自己ゲート文は fail-safe として機能する。
- **Workflow 内部の `agent()` は hook で捕捉できない**: PreToolUse はメインループのツール呼び出しにのみ発火するため、Workflow スクリプト内部のサブエージェントスポーンは防波堤層の対象外。この経路は env が (実測どおり) カバーする。
- **セッション途中の `/model` 切替は検知できない**: モデル切替に発火する hook イベントが存在せず、SessionStart 以外の hook 入力に `model` フィールドは無い (`$CLAUDE_MODEL` 環境変数も存在しない。公式 hooks リファレンスで確認済み)。このため誘導層・state file とも次の SessionStart (clear / compact / resume) まで旧判定のままになる。帰結として **env 不在時は防波堤層も双方向に破れる**: 非 fable 開始 → fable 切替では model 未指定の委任が stale な state により allow され (fable 継承の素通り)、fable 開始 → 非 fable 切替では誤 deny が続く (deny メッセージの model 明示誘導で自己修復可能)。env (`CLAUDE_CODE_SUBAGENT_MODEL`) による強制はモデル非依存に効くため、env が設定されている限りコスト防御は破れない (#157)。
- **fork subagent は hook で deny しない**: fork は全会話コンテキストを継承し model 指定を無視する型で、Fable セッションでは入力コスト面から誘導層で「原則使用しない」と規定する。ただしコンテキスト継承が不可欠な委任をブロックしない柔軟さを優先し、物理 deny は行わない。
- **agent frontmatter の model は判定できない**: agent 定義 frontmatter の `model` は `tool_input` に現れないため、env 不在 + model 未指定 + frontmatter が fable を指す構成は hook では捕捉できない。この経路も env 側でカバーする。
- **不明な env 値は authoritative として信頼する**: `CLAUDE_CODE_SUBAGENT_MODEL` が非空・非 inherit で fable を含まない場合、その妥当性検証は Claude Code 本体と利用者の責務とし、hook は非 fable 値として信頼する (確信境界の外の値を deny 根拠にしない)。
- **fail-open ポリシー**: jq 不在・入力 JSON 不正・state file 不在などの判定不能ケースはすべて allow / 注入スキップ側に倒す。本プラグインは courtesy guard であり、誤 deny でセッションを止めるコストの方が高い。自己ゲート文も同方針で「Fable と確認できた場合のみ適用」とし、判定不能時は適用しない側に倒す (非 Fable セッションを汚さないことを優先)。
- **プロンプトガイド整合の棚卸し (v0.3.0)**: 誘導層・deny メッセージは 2026-07-05 に Fable 5 / Sonnet 5 の公式プロンプトガイドと突き合わせて監査済み (多段レビュー: 4 観点 finder + 指摘ごとの敵対的 2 lens 検証 + 書き直し後の 3 観点再検証)。セクション 3・4 の列挙的な規律はガイドの「旧モデル向けの prescriptive な指示は削除を検討せよ」と形式上緊張するが、2026-07-04 の実リポジトリ汚染事故の再発防止 (安全プロトコル) として意図的に維持している。モデル世代やガイドの更新時は本監査を再実施すること。

## キーワード

`fable` `model-cost` `subagent` `delegation` `system-prompt` `hook` `guardrail` `workflow`
