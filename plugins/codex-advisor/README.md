# codex-advisor

Anthropic の [Advisor tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool) パターンを Claude Code に移植し、OpenAI Codex を助言役 (advisor) として利用するプラグインです。

Advisor パターンは「実行役 (executor) のモデルが、戦略的な岐路で別の高知能モデルに相談し、plan / course-correction の助言を受け取って続行する」構成です。本家は Anthropic API のサーバーサイド機能で advisor が Claude モデル限定のため、このプラグインは同パターンを Claude Code の hook + skill + wrapper script として再構成し、advisor に Codex を使えるようにしています。

## バージョン

v2.0.3

### v2.0.2 → v2.0.3 の変更点

注入対象 md (`advisor-rules.md` / `advisor-rules-subagent.md`) のヘッダコメントから issue 番号による経緯記述を除去し、現行の役割・構成・制約のみの説明に書き換えた。ルール本文は無変更 (#363)。

### v2.0.1 → v2.0.2 の変更点

lifecycle hook の runner footer / attestation 解析を、コードフェンス行・空白行を無視した実質末尾行方式へ頑健化し、成功 runner の retry-required 誤判定を解消した (#348)。3 runner md に footer をフェンスで囲まない出力契約を明記した。

### v2.0.0 → v2.0.1 の変更点

`advisor-rules.md` の checkpoint 必須 4 項目と async 起動時の結果回収手順を、`/codex-advisor:consult` skill を正本とする参照に置換した (残余義務と起動安全項目は維持しつつ注入サイズを削減した)。

### v1.1.0 → v2.0.0 の変更点

- **互換破壊**: 3 runner が model: sonnet を要求するようになった。Sonnet を利用できない model 制限環境では runner 起動が失敗する (回避は既知の制約を参照)
- 3 runner (`advisor-runner` / `rescue-runner` / `review-runner`) の frontmatter を `model: sonnet` に固定した (従来の `model: inherit` を廃止)
- `/codex-advisor:consult` skill、`hooks/prompts/advisor-rules.md`、`hooks/prompts/advisor-rules-subagent.md`、`manage-codex-runners.mjs` の deny メッセージ、3 runner 本文の親向け起動契約に、起動すべき model (`model: "sonnet"`) を明示した
- 理由: Claude 5 世代の運用制約 (Fable はサブエージェント禁止、Sonnet 系はサブエージェント推奨) と、`CLAUDE_CODE_SUBAGENT_MODEL` 環境固定の廃止により、Fable セッションでは model 未指定の Agent 起動が agent-discipline の hook に deny されるため

### v1.0.0 → v1.1.0 の変更点

- `pre-push-review:codex-reviewer` の `Status: pass|findings` と、成功した `codex-advisor:review-runner` を同じ session cadence へ加算し、前回の根本方針 checkpoint から 5 サイクル完了すると main session の Stop と次の一般 / pre-push Codex review 起動を block する gate を追加した
- cadence 用 advisor request に、元の Goal / 受入基準・制約、直近 5 サイクルの findings / 修正 / 反復傾向、現在の仮説・アプローチ、根本方針を変えるべきかという 1 問を必須化した
- `advisor-runner` の予約 metadata (`Codex-Advisor-Review-Cadence`) で qualifying consultation の成功だけを証明し、通常の advisor 相談ではカウンターを reset しない。外部 service が利用不能な qualifying attempt は既存の fail-open 方針に従って block を解除し、報告を必須とする

後方互換のある機能追加なので Claude Code version を minor bump しました。本 plugin は Codex marketplace では excluded のため、Codex version は変更していません。

### v0.3.0 → v1.0.0 の変更点

- rescue / review / advisor の role 固有 runner (`codex-advisor:rescue-runner` / `review-runner` / `advisor-runner`) を追加し、Codex model 起動を main session から隔離した
- PreToolUse gate が `codex-companion.mjs task|review|adversarial-review` と `run-codex-advisor.sh` の直接実行を deny し、対応 runner へ reroute する。公式 legacy rescue agent も例外にしない
- runner は起動前の companion job 集合を記録する。rescue / advisor は detached task の job ID、review は tracking 喪失時の job 集合差分を使い、`status` / `result` で terminal result を回収する
- SubagentStart / SubagentStop / Stop の session-scoped state machine を追加した。tracking failure は 1 回だけ retry し、2 回目の失敗、cancel、未 install / 未認証、入力不正は terminal にする。SessionStart / SessionEnd で同じ session の stale state を掃除する
- `/codex-advisor:consult` の Claude Code 経路を main session の wrapper Bash から `codex-advisor:advisor-runner` Agent へ変更した。この直接実行契約の削除が major bump の理由である

v0.3.0 で追加した Codex host の PTY stdin / direct read-only process は source tree 内に維持しています。ただし本 plugin は Codex marketplace では excluded であり、v1.0.0 の install surface 変更は Claude Code 固有です。

v0.2.0 でプラグインのスコープを「advisor 相談規律」から「codex 利用規律全般」へ拡張し、`/codex:rescue` の thread 選択自律化 (`rule:rescue-thread`、issue #241) を含むようになりました。rescue はレビューループ外の設計相談でも多用されるため、相談規律と同じ SessionStart 注入で配送します。

## 機構

| 構成要素 | 役割 |
|---|---|
| SessionStart hook (`inject-advisor-rules`) | メインセッション向けの利用規律 6 ルール (下記) を `additionalContext` として常時注入する |
| SubagentStart hook (`inject-advisor-rules-subagent`) | 通常 subagent に advisor の許可境界を注入する。通常 subagent は wrapper を直接起動せず、self-contained な相談 request を親へ返す |
| runner lifecycle hook (`manage-codex-runners.mjs`) | PreToolUse gate、SubagentStart / SubagentStop の active・bounded retry、Stop の reroute / completion 回収要求、一般 review と pre-push Codex review を合算する 5 review ごとの cadence checkpoint を管理する。runner state と cadence state は UID + session ID で分離し、prompt / Codex 出力を保存しない |
| role 固有 runner agents | rescue / review / advisor の model 起動・job tracking・terminal output を subagent context に閉じ込める。全 runner は foreground Agent として起動する |
| `/codex-advisor:consult` skill | self-contained な XML 相談 prompt を組み立て、Claude Code では `codex-advisor:advisor-runner` を起動する。Codex host の source 契約は PTY stdin wrapper を維持する |
| `scripts/run-codex-job.sh` | official companion v1.0.6 の task / review / status / result / cancel を runner 向けの path-only command に限定して公開する。status wait は単発 status の短い poll で構成する |
| `scripts/run-codex-advisor.sh` | v0.3.0 の adapter 契約と Codex host source を維持する wrapper。Claude Code の通常 Skill は直接呼ばず advisor runner を使う。Codex host では PTY stdin から direct read-only / ephemeral `codex exec` を foreground 起動し、既定 10 分の watchdog で process group を回収する |

### 注入される規律 (hooks/prompts/advisor-rules.md)

| rule ID | 内容 |
|---|---|
| `rule:advisor-timing` | いつ相談するか: 実質的な作業前 (オリエンテーションは含まない) / 完了宣言前 (成果物を durable にしてから) / 行き詰まり / 方針転換の検討時。短い反応的タスクでは相談しない |
| `rule:review-cadence` | `pre-push-review:codex-reviewer` の正常終了と成功した一般 Codex review を session ごとに合算し、5 サイクル完了後は次の review / 完了宣言より先に advisor へ根本方針・問題設定・設計境界・検証戦略を相談する。Stop と両 review model gate で強制し、qualifying attestation だけが reset する |
| `rule:advisor-weight` | 助言はフラットに扱う (独立した第二視点として自分の証拠・推論と同じ土俵で採否を判断し、採否と理由を明示する。黙って無視しない)。証拠と助言が衝突し自分で判断できないときは reconcile call (衝突を明示した再相談) で解消する |
| `rule:advisor-boundary` | 設計/仕様の決定はユーザ専権 (助言は AskUserQuestion の代替でない)。差分 finding は pre-push-review が担当し、review cadence は根本方針の course-correction だけを相談する。advisor 不通時は相談なしで続行しユーザ報告 |
| `rule:rescue-thread` | `/codex:rescue` 起動時は `--resume` / `--fresh` を常に Claude が自律決定して付与し、thread 選択の AskUserQuestion を発行しない。`--resume` は「直前の rescue と同一論点の続き + 対象 rescue がセッション内で最新の再開可能 task (terminal 状態かつ threadId あり) と確実に分かる場合」のみで、それ以外・迷ったら `--fresh`。ユーザのフラグ明示指定が最優先 |
| `rule:codex-runner` | rescue / review / advisor は完全修飾 runner を `model: "sonnet"`、`run_in_background: false` で起動する。Agent が async 受理されても completion notification / TaskOutput と terminal report を回収するまで turn を終了しない |

公式ドキュメントの推奨プロンプト (timing block / advice block) の移植ですが、次の 2 点は意図的に変えています: (1)「最初のファイル変更前に必ず advisor を呼ぶ」型の hard rule は採用していません (公式実測で、強い executor への hard rule 追加は過剰呼び出しを招き純効果がゼロ〜マイナスと報告されているため)。(2) advice block の「助言を重く扱う」も採用せず、フラットな扱いに変更しています (下記の差分参照)。

### subagent からの利用

通常 subagent が相談を必要とする場合も、wrapper / companion を直接実行しません。相談は課金を伴う外部サービス呼び出しなので、委任指示が codex-advisor の使用を明示的に許可している場合だけ self-contained な相談 request を親へ返します。親は `codex-advisor:advisor-runner` を foreground Agent として起動します。subagent には AskUserQuestion が無いため、助言と証拠の衝突が自力で解消できない場合は両論併記で親へエスカレーションします。

委任指示に含める許可の定型文の例:

> 方針にコミットする前または行き詰まったときは、codex-advisor 用の self-contained な相談 request を親へ返してよい。親が advisor runner で取得した助言の採否と理由を最終報告に含めること。

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

- review cadence は同一 Claude Code session 内の `pre-push-review:codex-reviewer` (`Status: pass|findings`) と、成功した `codex-advisor:review-runner` を数える。失敗・cancel・不正 report、別 session、pre-push の code-reviewer / security-reviewer は対象外である。advisor が利用不能な場合は qualifying attempt で gate を解除するため、助言取得そのものではなく「5 サイクル以内に根本方針相談を試行すること」が外部障害時の保証上限になる
- `rule:rescue-thread` は openai-codex plugin (v1.0.6 で確認) の rescue.md の「`--resume` / `--fresh` 指定時は thread 選択を質問しない」挙動を前提とします。外部 plugin の将来更新でこの前提が壊れた場合は規律の見直しが必要です
- ユーザが `/codex:rescue` の本文を直接指定し、かつ対象の rescue がセッション内で最新の再開可能 task でなくなっている場合 (間に consult 等の Codex task が terminal 状態になった場合)、規律は安全側の degraded mode (`--fresh` + 本文無改変転送、thread 文脈の連続性なし) に倒れます。誤 thread 再開の防止と rescue.md の verbatim 転送契約を文脈の連続性より優先するためで、継続文脈が必要な場合は再依頼時に本文へ含めてください
- 3 runner は model: sonnet を frontmatter で固定しているが、model 制限環境で sonnet が利用できない場合は runner の起動自体が失敗し、review cadence の `unavailable` 記録に到達できない。この場合は呼び出し側の Agent tool で利用可能な非 Fable モデルを `model` に明示して runner を再実行する (呼び出し側指定は frontmatter より優先される)

## Codex 代替の保証差と検証テスト

Codex host の `$codex-advisor:consult` は、別 context・read-only sandbox・ephemeral・hooks 無効の独立 process と foreground 観察を維持します。一方、実行役と advisor が同じ model family / provider になる可能性があるため、Claude Code から Codex を呼ぶ場合と同じ異種 model の独立性は保証しません。Claude Code の scratchpad file transport は、Codex では PTY session の stdin transport に置き換わります。

Codex transport は受信中の PTY を echo 無効・raw/noncanonical mode にし、連続する 2 byte の EOT (`0x04 0x04`) を EOF 操作ではなく明示 frame terminator として扱います。2 byte により正常な delimiter と delimiter 前の切断を区別します。このため canonical PTY の行長上限と CR 変換を避けられますが、prompt 本文自体に `0x04` は含められません。direct process は `--sandbox read-only --ephemeral --disable hooks --skip-git-repo-check --color never -c 'model_reasoning_effort="xhigh"' -` で固定し、git repository 外でも相談できます。既定 600 秒を超えた独立 process group は TERM、grace period 後の KILL、leader の `wait` の順で descendant ごと終了・回収します。descendant が stdout / stderr の pipe FD を保持して foreground session を残す経路も同じ group signal で閉じます。

`tests/test_codex_advisor_subagent_runner.py` は direct gate の agent type matrix、実行形 / audit 言及の分類、session state、retry 上限、一般 / pre-push 共通 5 review cadence の Stop / next-review block、失敗 report 非加算、attestation reset、stale cleanup、3 runner / Skill / hook artifact を検証します。`tests/test_codex_advisor_adapter.py` は v0.3.0 から維持する PTY / file-stdin adapter と process-group cleanup を検証します。いずれも外部 service・認証・rate limit の可用性や Codex 出力品質までは保証しません。

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
