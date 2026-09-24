# cross-model-advisor

Anthropic の [Advisor tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool) パターンを Claude Code に移植し、OpenAI Codex を助言役 (advisor) として利用するプラグインです。

Advisor パターンは「実行役 (executor) のモデルが、戦略的な岐路で別の高知能モデルに相談し、plan / course-correction の助言を受け取って続行する」構成です。本家は Anthropic API のサーバーサイド機能で advisor が Claude モデル限定のため、このプラグインは同パターンを Claude Code の hook + skill + wrapper script として再構成し、advisor に Codex を使えるようにしています。

## バージョン

v5.0.0

## 機構

| 構成要素 | 役割 |
|---|---|
| SessionStart hook (`inject-advisor-rules`) | メインセッション向けの利用規律 5 ルール (下記) を `additionalContext` として常時注入する |
| SubagentStart hook (`inject-advisor-rules-subagent`) | 通常 subagent に advisor の許可境界を注入する。通常 subagent は wrapper を直接起動せず、self-contained な相談 request を親へ返す |
| runner lifecycle hook (`manage-codex-runners.mjs`) | PreToolUse gate、PermissionDenied による拒否済み起動要求の解消、SubagentStart / SubagentStop の active・bounded retry (codex-advisor-runner の attestation footer 契約検証を含む)、Stop の reroute / 待機通知を管理する。auto mode では runner の最終 report が `SubagentHandback` tool 経由で届き SubagentStop の `last_assistant_message` には締めの文しか入らないため、PostToolUse (`SubagentHandback`) で report の footer / attestation を解析して state に記録し、SubagentStop がそれを採用する。runner state は UID + session ID で分離し、prompt / Codex 出力 (hand-back report の本文を含む) を保存しない |
| role 固有 runner agents | rescue / review / advisor の model 起動・job tracking・terminal output を subagent context に閉じ込める。起動 mode は Claude Code が決め、report は completion notification 経由で親へ届く |
| `/cross-model-advisor:consult` skill | self-contained な XML 相談 prompt を組み立て、Claude Code では `cross-model-advisor:codex-advisor-runner` を起動する。Codex host の source 契約は PTY stdin wrapper を維持する |
| `scripts/run-codex-job.sh` | official companion v1.0.6 の task / review / status / result / cancel を runner 向けの path-only command に限定して公開する。status wait は単発 status の短い poll で構成する |
| `scripts/run-codex-advisor.sh` | v0.3.0 の adapter 契約と Codex host source を維持する wrapper。Claude Code の通常 Skill は直接呼ばず advisor runner を使う。Codex host では PTY stdin から direct read-only / ephemeral `codex exec` を foreground 起動し、既定 10 分の watchdog で process group を回収する |

### 注入される規律 (hooks/prompts/advisor-rules.md)

| rule ID | 内容 |
|---|---|
| `rule:advisor-timing` | いつ相談するか: 実質的な作業前 (オリエンテーションは含まない) / 完了宣言前 (成果物を durable にしてから) / 行き詰まり / 方針転換の検討時。短い反応的タスクでは相談しない |
| `rule:advisor-weight` | 助言はフラットに扱う (独立した第二視点として自分の証拠・推論と同じ土俵で採否を判断し、採否と理由を明示する。黙って無視しない)。証拠と助言が衝突し自分で判断できないときは reconcile call (衝突を明示した再相談) で解消する |
| `rule:advisor-boundary` | 設計/仕様の決定はユーザ専権 (助言は AskUserQuestion の代替でない)。差分 finding は pre-push-review が担当し、review cadence の checkpoint (enforcement は pre-push-codex-review が担う) は根本方針の course-correction だけを相談する。advisor 不通時は相談なしで続行しユーザ報告 |
| `rule:rescue-thread` | `/codex:rescue` 起動時は `--resume` / `--fresh` を常に Claude が自律決定して付与し、thread 選択の AskUserQuestion を発行しない。`--resume` は「直前の rescue と同一論点の続き + 対象 rescue がセッション内で最新の再開可能 task (terminal 状態かつ threadId あり) と確実に分かる場合」のみで、それ以外・迷ったら `--fresh`。ユーザのフラグ明示指定が最優先 |
| `rule:codex-runner` | rescue / review / advisor は完全修飾 runner を `model: "sonnet"` で起動し、起動 mode は指定しない (Claude Code が決める)。runner の terminal report は completion notification 経由で後続ターンに届き、それを処理するまでタスクを完了扱いにしない。起動が classifier に拒否されたら同じ起動を繰り返さず `AskUserQuestion` で許可を得る |

公式ドキュメントの推奨プロンプト (timing block / advice block) の移植ですが、次の 2 点は意図的に変えています: (1)「最初のファイル変更前に必ず advisor を呼ぶ」型の hard rule は採用していません (公式実測で、強い executor への hard rule 追加は過剰呼び出しを招き純効果がゼロ〜マイナスと報告されているため)。(2) advice block の「助言を重く扱う」も採用せず、フラットな扱いに変更しています (下記の差分参照)。

### subagent からの利用

通常 subagent が相談を必要とする場合も、wrapper / companion を直接実行しません。相談は課金を伴う外部サービス呼び出しなので、委任指示が cross-model-advisor の使用を明示的に許可している場合だけ self-contained な相談 request を親へ返します。親は `cross-model-advisor:codex-advisor-runner` を Agent tool で起動します。subagent には AskUserQuestion が無いため、助言と証拠の衝突が自力で解消できない場合は両論併記で親へエスカレーションします。

委任指示に含める許可の定型文の例:

> 方針にコミットする前または行き詰まったときは、cross-model-advisor 用の self-contained な相談 request を親へ返してよい。親が advisor runner で取得した助言の採否と理由を最終報告に含めること。

### 本家 API 版との意図的な差分

- API 版は会話全文が自動で advisor に渡ります。本プラグインでは Claude が self-contained な相談プロンプト (タスク要約 + 証拠 + 質問) を組み立てます
- API 版 advisor はツールなしで動きます。本プラグインの Codex は read-only sandbox でリポジトリを自分で読んで裏取りできます
- API 版のエラー設計 (advisor 失敗時も executor は続行) を踏襲し、Codex 不通時は相談なしで続行 + ユーザ報告します
- API 版の advice block は「executor より高知能な advisor」を前提に助言を重く扱わせますが、本プラグインの呼び出し元は advisor と同等以上のモデル (Fable 等) でもありうるため、助言はフラットに扱う規律に変更しています。advisor の価値は知能差ではなく、別モデル系統からの独立した第二視点です

本プラグイン自体は Claude Code 専用で、Codex marketplace では配布していません (OpenAI Codex は advisor として呼び出す外部 CLI であり、配布物ではありません)。

## auto mode での利用

auto mode (permission_mode = `auto`) では、Claude Code の classifier が各 tool call を審査します。classifier が読むのは「ユーザ発言・tool call・CLAUDE.md」で、tool result (PreToolUse gate の deny 文や Stop hook の指示を含む) は除去されます。そのため gate の deny 直後に runner を起動すると、classifier には「ユーザが依頼していない操作の一部」に見え、起動が `Blocked by classifier` で拒否されることがあります。

本 plugin はこれを次の 3 段で扱います:

1. **起動規律の注入 (SessionStart)**: `hooks/prompts/advisor-rules.md` の `rule:codex-runner` が、runner を `model: "sonnet"` で起動すること、起動 mode を指定しないこと、report を completion notification 経由で受け取ること、拒否されたら同じ起動を繰り返さず `AskUserQuestion` でユーザの許可を得てから再起動することを定めます
2. **拒否の記録 (PermissionDenied hook)**: 拒否された起動要求を runner state に反映し、Stop hook が同じ起動を要求し続ける loop を残しません
3. **classifier の allow 設定 (任意)**: 恒久的に解消するには、`~/.claude/settings.json` の `autoMode.allow` に次のルールを追加します。`"$defaults"` を残さないと組み込みルールが失われるので必ず併記してください

```json
{
  "autoMode": {
    "allow": [
      "$defaults",
      "Launching the cross-model-advisor:codex-rescue-runner, cross-model-advisor:codex-review-runner or cross-model-advisor:codex-advisor-runner subagent is allowed, including immediately after the cross-model-advisor PreToolUse gate denied a direct companion call: the runner only starts an OpenAI Codex job through the plugin's own helper and returns its output. It does not push, merge, post to GitHub, or delete anything."
    ]
  }
}
```

この allow ルールが緩めるのは subagent の**起動**の審査だけです。runner が実行中に行う各 tool call は引き続き classifier が親 session と同じ規則で審査します。

classifier は project settings (`.claude/settings.json` / `.claude/settings.local.json`) の `autoMode` を読まないため、ユーザ設定 (`~/.claude/settings.json`) に書く必要があります。classifier は CLAUDE.md も読むため、プロジェクトの CLAUDE.md に同趣旨の 1 文を書く方法でも代替できます。設定なしで拒否された場合は、ユーザが `AskUserQuestion` の確認で許可すれば次の起動は通ります (classifier は明示的なユーザ意図で soft block を解除します)。

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
- 3 runner は model: sonnet を frontmatter で固定しているが、model 制限環境で sonnet が利用できない場合は runner の起動自体が失敗し、review cadence の `unavailable` 記録に到達できない。この場合は呼び出し側の Agent tool で利用可能な非 Fable モデルを `model` に明示して runner を再実行する (呼び出し側指定は frontmatter より優先される)
- runner 以外からの直接起動を deny する PreToolUse gate は matcher が `Bash` であるため、PowerShell tool (`CLAUDE_CODE_USE_POWERSHELL_TOOL=1` で Linux / macOS でも有効化できる) および Monitor tool 経由で発行された companion / wrapper の起動を gate は観測しない。これらの tool を有効にした環境はサポート外

## Codex 代替の保証差と検証テスト

Codex host の `$cross-model-advisor:consult` は、別 context・read-only sandbox・ephemeral・hooks 無効の独立 process と foreground 観察を維持します。一方、実行役と advisor が同じ model family / provider になる可能性があるため、Claude Code から Codex を呼ぶ場合と同じ異種 model の独立性は保証しません。Claude Code の scratchpad file transport は、Codex では PTY session の stdin transport に置き換わります。

Codex transport は受信中の PTY を echo 無効・raw/noncanonical mode にし、連続する 2 byte の EOT (`0x04 0x04`) を EOF 操作ではなく明示 frame terminator として扱います。2 byte により正常な delimiter と delimiter 前の切断を区別します。このため canonical PTY の行長上限と CR 変換を避けられますが、prompt 本文自体に `0x04` は含められません。direct process は `--sandbox read-only --ephemeral --disable hooks --skip-git-repo-check --color never -c 'model_reasoning_effort="xhigh"' -` で固定し、git repository 外でも相談できます。既定 600 秒を超えた独立 process group は TERM、grace period 後の KILL、leader の `wait` の順で descendant ごと終了・回収します。descendant が stdout / stderr の pipe FD を保持して foreground session を残す経路も同じ group signal で閉じます。

`tests/test_cross_model_advisor_subagent_runner.py` は direct gate の agent type matrix、実行形 / audit 言及の分類、session state、retry 上限、codex-advisor-runner の review cadence attestation footer 契約検証、stale cleanup、3 runner / Skill / hook artifact を検証します。`tests/test_cross_model_advisor_adapter.py` は v0.3.0 から維持する PTY / file-stdin adapter と process-group cleanup を検証します。いずれも外部 service・認証・rate limit の可用性や Codex 出力品質までは保証しません。

## トラブルシュート

| 症状 | 対処 |
|---|---|
| `companion unavailable; running direct codex exec` | Codex CLI への fallback を示す進捗メッセージなので、相談が成功すれば対処不要。Claude companion を優先したい場合だけ `claude plugin install codex@openai-codex` を実行 |
| runner が `codex companion が見つかりません` を返す | Claude Code で公式 codex plugin の install を確認し、`/codex:setup` で診断する。main session の direct wrapper へ退避しない |
| Codex host の wrapper が `codex companion と codex CLI のどちらも見つかりません` を返す | Codex CLI を導入する |
| 認証エラー | `/codex:setup` で診断し、`codex login` で認証 |
| 相談が 10 分でタイムアウトする | 相談プロンプトの `<context>` を絞る (参照パスを減らす)。それでも超える場合は相談を分割する |

## キーワード

`codex` `advisor` `second-opinion` `system-prompt` `hook` `skill` `openai`
