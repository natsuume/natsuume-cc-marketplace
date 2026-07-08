# codex-advisor

Anthropic の [Advisor tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool) パターンを Claude Code に移植し、OpenAI Codex を助言役 (advisor) として利用するプラグインです。

Advisor パターンは「実行役 (executor) のモデルが、戦略的な岐路で別の高知能モデルに相談し、plan / course-correction の助言を受け取って続行する」構成です。本家は Anthropic API のサーバーサイド機能で advisor が Claude モデル限定のため、このプラグインは同パターンを Claude Code の hook + skill + wrapper script として再構成し、advisor に Codex を使えるようにしています。

## 機構

| 構成要素 | 役割 |
|---|---|
| SessionStart hook (`inject-advisor-rules`) | メインセッション向けの相談規律 3 ルール (下記) を `additionalContext` として常時注入する。公式ドキュメントは「tool 定義だけでは advisor は呼ばれない、システムプロンプト側の明示誘導が必須」と明言しており、この注入がそれに相当する |
| SubagentStart hook (`inject-advisor-rules-subagent`) | subagent 向けの簡約版規律 (許可・タイミング・実行方法・フラットな扱い・失敗時) を全 subagent 起動時に注入する。wrapper の絶対パスは注入時に解決して埋め込む (subagent の Bash 環境では `${CLAUDE_PLUGIN_ROOT}` が空になりうるため)。Claude Code 2.0.43 以降で有効 |
| `/codex-advisor:consult` skill | 相談プロンプトの組み立て方 (self-contained な XML ブロック構成) と wrapper の起動手順を定義する。ユーザによる明示起動も可能 |
| `scripts/run-codex-advisor.sh` | 公式 codex plugin の companion (`codex-companion.mjs task`) を foreground で 1 回起動する wrapper。`--effort xhigh` 固定・`--write` なし (read-only sandbox 固定)。stdout = Codex の助言、stderr = wrapper 状態 |

### 注入される相談規律 (hooks/prompts/advisor-rules.md)

| rule ID | 内容 |
|---|---|
| `rule:advisor-timing` | いつ相談するか: 実質的な作業前 (オリエンテーションは含まない) / 完了宣言前 (成果物を durable にしてから) / 行き詰まり / 方針転換の検討時。短い反応的タスクでは相談しない |
| `rule:advisor-weight` | 助言はフラットに扱う (独立した第二視点として自分の証拠・推論と同じ土俵で採否を判断し、採否と理由を明示する。黙って無視しない)。証拠と助言が衝突し自分で判断できないときは reconcile call (衝突を明示した再相談) で解消する |
| `rule:advisor-boundary` | 設計/仕様の決定はユーザ専権 (助言は AskUserQuestion の代替でない)。レビュー用途は pre-push-review が担当。advisor 不通時は相談なしで続行しユーザ報告 |

公式ドキュメントの推奨プロンプト (timing block / advice block) の移植ですが、次の 2 点は意図的に変えています: (1)「最初のファイル変更前に必ず advisor を呼ぶ」型の hard rule は採用していません (公式実測で、強い executor への hard rule 追加は過剰呼び出しを招き純効果がゼロ〜マイナスと報告されているため)。(2) advice block の「助言を重く扱う」も採用せず、フラットな扱いに変更しています (下記の差分参照)。

### subagent からの利用

subagent も同じ wrapper で相談できる。ただし **SubagentStart の注入は規律の配送であって許可の付与ではない**: 相談は課金を伴う外部サービス呼び出しであり、この環境の委任規律 (明記されていない外部サービス呼び出しは default-deny) と整合させるため、subagent が実際に相談できるのは **委任指示が codex-advisor の使用を明示的に許可している場合のみ**とした。subagent には AskUserQuestion が無いため、助言と証拠の衝突が自力で解消できない場合はエスカレーション (両論併記) に読み替える。

委任指示に含める許可の定型文の例:

> 方針にコミットする前または行き詰まったときは、codex-advisor の wrapper (SubagentStart 注入の指示に従う) で Codex に相談してよい。相談は read-only で foreground 実行し、助言の採否と理由を最終報告に含めること。

### 本家 API 版との意図的な差分

- API 版は会話全文が自動で advisor に渡ります。本プラグインでは Claude が self-contained な相談プロンプト (タスク要約 + 証拠 + 質問) を組み立てます
- API 版 advisor はツールなしで動きます。本プラグインの Codex は read-only sandbox でリポジトリを自分で読んで裏取りできます
- API 版のエラー設計 (advisor 失敗時も executor は続行) を踏襲し、Codex 不通時は相談なしで続行 + ユーザ報告します
- API 版の advice block は「executor より高知能な advisor」を前提に助言を重く扱わせますが、本プラグインの呼び出し元は advisor と同等以上のモデル (Fable 等) でもありうるため、助言はフラットに扱う規律に変更しています。advisor の価値は知能差ではなく、別モデル系統からの独立した第二視点です

## 依存

- [公式 codex plugin](https://github.com/openai/codex-plugin-cc) (`claude plugin install codex@openai-codex`) — companion script の提供元
- Codex CLI (`npm install -g @openai/codex`) と認証 (`codex login`)。状態診断は `/codex:setup`
- Node.js
- Linux (WSL2 含む) / macOS
- subagent への配送 (SubagentStart hook) は Claude Code 2.0.43 以降。それ未満ではメインセッション向け機能のみ有効

## トラブルシュート

| 症状 | 対処 |
|---|---|
| `codex companion が見つかりません` | `claude plugin install codex@openai-codex` を実行 |
| 認証エラー | `/codex:setup` で診断し、`codex login` で認証 |
| 相談が 10 分でタイムアウトする | 相談プロンプトの `<context>` を絞る (参照パスを減らす)。それでも超える場合は相談を分割する |

## キーワード

`codex` `advisor` `second-opinion` `system-prompt` `hook` `skill` `openai`
