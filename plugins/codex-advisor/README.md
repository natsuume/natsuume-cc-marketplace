# codex-advisor

Anthropic の [Advisor tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool) パターンを Claude Code に移植し、OpenAI Codex を助言役 (advisor) として利用するプラグインです。

Advisor パターンは「実行役 (executor) のモデルが、戦略的な岐路で別の高知能モデルに相談し、plan / course-correction の助言を受け取って続行する」構成です。本家は Anthropic API のサーバーサイド機能で advisor が Claude モデル限定のため、このプラグインは同パターンを Claude Code の hook + skill + wrapper script として再構成し、advisor に Codex を使えるようにしています。

## バージョン

v0.3.0

### v0.2.0 → v0.3.0 の変更点

- Codex plugin manifest を追加した。wrapper は Claude の openai-codex companion を優先し、利用できない場合は hooks を無効化した独立 read-only / ephemeral `codex exec` process へ fallback する
- consult Skill の plugin root 解決を SKILL.md の実パス基準にし、hook 専用環境変数への依存を除いた
- Codex host では Claude Code 固有の Write tool の代わりに、PTY unified exec の分離された stdin channel から prompt を渡す。PTY は受信中だけ raw/noncanonical mode とし、明示 EOT-pair framing (`0x04 0x04`) で 8 KiB を超える単一行や CR も byte-preserving に転送する。本文を shell command、argv、永続 file に載せない
- direct `codex exec` は read-only / ephemeral / hooks 無効 / `xhigh` に加えて git repository 検査も明示的に skip し、既定 10 分の deadline 超過時は独立 process group 全体を TERM → KILL、leader を `wait` して descendant ごと回収する

v0.2.0 でプラグインのスコープを「advisor 相談規律」から「codex 利用規律全般」へ拡張し、`/codex:rescue` の thread 選択自律化 (`rule:rescue-thread`、issue #241) を含むようになりました。rescue はレビューループ外の設計相談でも多用されるため、相談規律と同じ SessionStart 注入で配送します。

## 機構

| 構成要素 | 役割 |
|---|---|
| SessionStart hook (`inject-advisor-rules`) | メインセッション向けの利用規律 4 ルール (下記) を `additionalContext` として常時注入する。公式ドキュメントは「tool 定義だけでは advisor は呼ばれない、システムプロンプト側の明示誘導が必須」と明言しており、この注入がそれに相当する |
| SubagentStart hook (`inject-advisor-rules-subagent`) | subagent 向けの簡約版規律 (許可・タイミング・実行方法・フラットな扱い・失敗時) を全 subagent 起動時に注入する。wrapper の絶対パスは注入時に解決し、jq の `@sh` で shell-quote して埋め込む (subagent の Bash 環境では `${CLAUDE_PLUGIN_ROOT}` が空になりうるため。quote はパスにメタ文字を含む install 環境への防御)。Claude Code 2.0.43 以降で有効 |
| `/codex-advisor:consult` skill | 相談プロンプトの組み立て方 (self-contained な XML ブロック構成) と wrapper の起動手順を定義する。ユーザによる明示起動も可能 |
| `scripts/run-codex-advisor.sh` | Claude Code host では公式 codex plugin の companion を優先し、Codex host では明示 EOT pair で framing した PTY stdin を受けて direct `codex exec` を foreground で起動する wrapper。read-only・ephemeral・hooks 無効・git 検査 skip・`xhigh` を固定し、独立 process group を監視する既定 10 分の watchdog を持つ。stdout = 助言、stderr = wrapper 状態 |

### 注入される規律 (hooks/prompts/advisor-rules.md)

| rule ID | 内容 |
|---|---|
| `rule:advisor-timing` | いつ相談するか: 実質的な作業前 (オリエンテーションは含まない) / 完了宣言前 (成果物を durable にしてから) / 行き詰まり / 方針転換の検討時。短い反応的タスクでは相談しない |
| `rule:advisor-weight` | 助言はフラットに扱う (独立した第二視点として自分の証拠・推論と同じ土俵で採否を判断し、採否と理由を明示する。黙って無視しない)。証拠と助言が衝突し自分で判断できないときは reconcile call (衝突を明示した再相談) で解消する |
| `rule:advisor-boundary` | 設計/仕様の決定はユーザ専権 (助言は AskUserQuestion の代替でない)。レビュー用途は pre-push-review が担当。advisor 不通時は相談なしで続行しユーザ報告 |
| `rule:rescue-thread` | `/codex:rescue` 起動時は `--resume` / `--fresh` を常に Claude が自律決定して付与し、thread 選択の AskUserQuestion を発行しない。`--resume` は「直前の rescue と同一論点の続き + 対象 rescue がセッション内で最新の再開可能 task (terminal 状態かつ threadId あり) と確実に分かる場合」のみで、それ以外・迷ったら `--fresh`。ユーザのフラグ明示指定が最優先 |

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

- [公式 codex plugin](https://github.com/openai/codex-plugin-cc) (`claude plugin install codex@openai-codex`) — Claude Code host の companion script 提供元。Codex host の direct 経路には不要
- Codex CLI (`npm install -g @openai/codex`) と認証 (`codex login`)。状態診断は `/codex:setup`
- Node.js
- jq (hook の注入 JSON 生成に使用。不在時は注入をスキップする fail-open)
- POSIX awk / `stty` (Codex host の PTY framing。Linux / macOS の標準ツール)
- Linux (WSL2 含む) / macOS
- subagent への配送 (SubagentStart hook) は Claude Code 2.0.43 以降。それ未満ではメインセッション向け機能のみ有効

## 既知の制約

- `rule:rescue-thread` は openai-codex plugin (v1.0.6 で確認) の rescue.md の「`--resume` / `--fresh` 指定時は thread 選択を質問しない」挙動を前提とします。外部 plugin の将来更新でこの前提が壊れた場合は規律の見直しが必要です
- ユーザが `/codex:rescue` の本文を直接指定し、かつ対象の rescue がセッション内で最新の再開可能 task でなくなっている場合 (間に consult 等の Codex task が terminal 状態になった場合)、規律は安全側の degraded mode (`--fresh` + 本文無改変転送、thread 文脈の連続性なし) に倒れます。誤 thread 再開の防止と rescue.md の verbatim 転送契約を文脈の連続性より優先するためで、継続文脈が必要な場合は再依頼時に本文へ含めてください

## Codex 代替の保証差と検証テスト

Codex host の `$codex-advisor:consult` は、別 context・read-only sandbox・ephemeral・hooks 無効の独立 process と foreground 観察を維持します。一方、実行役と advisor が同じ model family / provider になる可能性があるため、Claude Code から Codex を呼ぶ場合と同じ異種 model の独立性は保証しません。Claude Code の scratchpad file transport は、Codex では PTY session の stdin transport に置き換わります。

Codex transport は受信中の PTY を echo 無効・raw/noncanonical mode にし、連続する 2 byte の EOT (`0x04 0x04`) を EOF 操作ではなく明示 frame terminator として扱います。2 byte により正常な delimiter と delimiter 前の切断を区別します。このため canonical PTY の行長上限と CR 変換を避けられますが、prompt 本文自体に `0x04` は含められません。direct process は `--sandbox read-only --ephemeral --disable hooks --skip-git-repo-check --color never -c 'model_reasoning_effort="xhigh"' -` で固定し、git repository 外でも相談できます。既定 600 秒を超えた独立 process group は TERM、grace period 後の KILL、leader の `wait` の順で descendant ごと終了・回収します。descendant が stdout / stderr の pipe FD を保持して foreground session を残す経路も同じ group signal で閉じます。

`tests/test_codex_advisor_adapter.py` は host 分岐、PTY 必須条件と echo 抑止、8 KiB 超の単一行 + CR の byte-exact round-trip、上記の全安全引数、git repository 外からの起動、deadline 超過時に TERM を無視して pipe FD を保持する長寿命 descendant を含む process-group KILL / leader `wait` cleanup、Claude Code の既存 file-stdin companion 経路を検証します。これは adapter のローカル契約を検証するもので、model family の独立性、外部 service・認証・rate limit の可用性、Codex が返す助言の品質までは保証しません。`scripts/smoke_codex_marketplace.sh` は生成 manifest からの install surface を検証します。保証差の集約表は root の `docs/codex-compatibility.md` にあります。

## トラブルシュート

| 症状 | 対処 |
|---|---|
| `companion unavailable; running direct codex exec` | Codex CLI への fallback を示す進捗メッセージなので、相談が成功すれば対処不要。Claude companion を優先したい場合だけ `claude plugin install codex@openai-codex` を実行 |
| `codex companion と codex CLI のどちらも見つかりません` | Codex CLI を導入するか、Claude Code host では公式 codex plugin も導入する |
| 認証エラー | `/codex:setup` で診断し、`codex login` で認証 |
| 相談が 10 分でタイムアウトする | 相談プロンプトの `<context>` を絞る (参照パスを減らす)。それでも超える場合は相談を分割する |

## キーワード

`codex` `advisor` `second-opinion` `system-prompt` `hook` `skill` `openai`
