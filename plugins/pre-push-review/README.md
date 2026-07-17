# pre-push-review プラグイン

`git push` を実行する前に **3 レビュー** を必ず実行させ、 未レビューな commit が remote に到達するのを構造的にブロックするプラグインです (`pre-commit-review` の後継)。 3 レビューはすべて subagent 経由で実行されます:

- **`pre-push-review:code-reviewer` subagent** (self-contained correctness バグ検出 / 詳細は [Agents](#agents))
- **`pre-push-review:codex-reviewer` subagent** (codex review wrapper `run-codex-review.sh` を foreground 起動し、結果を parent-safe report に抽象化 / 詳細は [Agents](#agents))
- **`pre-push-review:security-reviewer` subagent** (self-contained security review / 詳細は [Agents](#agents))

の 3 軸構成で、 **Anthropic と OpenAI の独立した 2 つのバグレビュー** に security review を重ねた defense-in-depth です。 修正や commit 列の変更 (add→revert / amend / rebase 含む) により hash が変わると 3 マーカーは自動失効し、 Claude は再走させる以外に push を通す手段がありません (= ループが構造的に強制されます)。

> **v3.0.0 で 3 レビューすべてを subagent 経由に統一** (互換破壊あり): v2.x の Skill `/code-review` と Bash 直接起動の codex review wrapper を、 それぞれ `pre-push-review:code-reviewer` / `pre-push-review:codex-reviewer` subagent に置換しました。 設計のメリット:
>
> - **context isolation**: reviewer は raw stdout / stderr、実行可能な command、具体的な再現手順を subagent context に留め、親 session には severity / location / impact / verification / fix direction / disposition を保持した parent-safe report だけを返します。これは agent prompt と contract test で固定する **instruction contract** であり、auto-mark が report 本文を機械検査して情報流出を遮断する **hard security boundary** ではありません。
> - **起動・marker 発行経路の単一化**: 親 session は 3 軸とも同じ `Agent` / `Task` tool で起動し、3 marker とも auto-mark.sh が SubagentStart の launch attestation と SubagentStop での parent-safe report 検証を経て発行します。Codex wrapper は review 開始時点の hash を pending attestation に束縛し、auto-mark が report 成功後に final marker へ昇格します。
> - **`/pre-push-review:review` slash command を 3 subagent 並列発出に書き換え**: deny メッセージとともに案内されます。 wall-clock は最遅レビュー 1 本の時間で完了します。

## バージョン

v4.1.0 (前身: `pre-commit-review` v0.4.0)

### v4.0.1 → v4.1.0 の変更点 (issue #285)

- レビュー完了検知を PostToolUse から subagent lifecycle hook (SubagentStart / SubagentStop) へ完全移行した。Claude Code v2.1.198 以降は Agent tool が既定で background 起動になり、PostToolUse は起動受理時 (`status: "async_launched"`) に 1 回発火するのみで完了時には発火しないため、v4.0.x の completion 検証は async 起動 harness で marker を永遠に書けず push gate が恒久 deny になっていた
- SubagentStart で「agent_id + 開始時 review hash」の launch attestation を one-shot 記録し、SubagentStop で (a) attestation の存在と一回限りの消費 (b) 開始時 hash と現在 hash の一致 (c) `last_assistant_message` 内の単一 `Status: pass|findings` 行 (d) `stop_hook_active == false` をすべて検証した場合のみ marker を書く。SendMessage resume 後の再 stop・レビュー開始後の差分変更・重複 stop は fail-closed に遮断する (codex-reviewer は従来どおり wrapper pending attestation の現在 hash 一致も要求)
- 旧 PostToolUse completion 経路と hooks.json の PostToolUse 配線を撤去した (PostToolUseFailure による codex pending 破棄は補助掃除経路として維持)

### v4.0.0 → v4.0.1 の変更点 (issue #281)

- 3 reviewer に共通の parent-safe report 契約を追加し、 finding ID、priority、confidence、location、cause class、violated invariant、impact、verification、fix direction、disposition を親 session へ返す形式に統一した
- `codex-reviewer` が wrapper の stdout / stderr を verbatim relay する挙動を廃止した。raw output は subagent context / transcript に留め、親へは各 finding を抽象化した summary、または正規化した execution failure だけを返す
- `code-reviewer` / `security-reviewer` も、内部では具体的な failure / attack scenario を検証しつつ、実行可能な command、再利用可能な payload、具体的な環境値、段階的な再現・回避手順を final report へ含めない
- repository-normalized `Severity: P1|P2|P3` と upstream の `Source severity` を分離した。source P0 は `Severity: P1` + `Source severity: P0` + `must-fix-before-push` に無損失写像し、ローカル label 体系に P0 が無くても criticality を落とさない
- exact detail を使った追加検証は、親へ raw detail を返す代わりに同一 reviewer subagent を resume して行い、結果だけを parent-safe report で返す
- `/pre-push-review:review` の 3 delegation prompt も parent-safe report を明示的に要求し、codex wrapper の stdout / stderr をまとめて返す旧指示を削除した。3 Agent call は `run_in_background: false` を明記し、Claude Code の background-default 時にも launch を review 完了と誤認しない
- 親の user-facing summary から agent ID、output file、transcript path、raw tool metadata を除外し、review の方針判断に不要な orchestration detail も context isolation の対象にした
- `auto-mark.sh` は Agent PostToolUse の `tool_response.status=completed` と final `content[].text` の単一 `Status: pass|findings` を両方確認した場合だけ 3 reviewer の marker を書くようにした。`async_launched`、`Status: execution-failed`、status 欠落・重複・未知値は marker を書かず、push gate を deny のまま維持する
- auto-mark の completion payload は Claude Code 2.1.211 で実機検証済み。`tool_response.status` がない場合は marker を書かず、旧 version / 未知 schema による恒久 deny と判別できる stderr 診断を出す
- Codex wrapper の final marker 直書きを廃止し、review 開始時点の hash を atomic な pending attestation に保存する方式へ変更した。auto-mark は attestation と current hash の一致を確認し、codex-reviewer の parent-safe report 成功後にのみ final marker へ atomic rename する。report 失敗・PostToolUseFailure・hash mismatch では pending を破棄する
- agent 定義・command・auto-mark の contract / integration test を追加し、必須 field、raw detail relay 禁止規律、foreground completion の fail-closed 判定を固定した

### v3.1.4 → v4.0.0 の変更点 (互換破壊あり / issue #267)

- **agent_type 検証 gate を追加 (fail-closed)**: `block-bg-codex-wrapper.sh` が hook payload トップレベルの `agent_type` を検証し、 `pre-push-review:codex-reviewer` (namespace 付き完全一致) 以外からの `run-codex-review.sh` wrapper 起動を deny するようにした。 **互換破壊**: hook payload に `agent_type` を含めない旧 Claude Code では、 正規フロー経由の wrapper 起動も deny される。 本 gate は Claude Code 2.1.211 で実機検証済み (= 動作要件の検証済み最低 version)
- **agent_type gate の発火対象を実行形コマンドのみに限定 (codex review P2 指摘への追加修正 / fail-closed 分類)**: substring `run-codex-review.sh` を含む segment を `split_command` / `tokenize_segment` / `skip_env_assignments` / `unquote_token` (cmd-parser.sh) で 3 規則に分類し、 wrapper を実行せず言及するだけの read-only コマンド (`cat` / `head` / `tail` / `grep` / `git diff` 等の言及 allowlist) は gate 対象外にした。 interpreter 起動・コマンド置換 (`$(...)` / バッククォート / `<(...)` / `>(...)`) を含む形・不明コマンドのみを実行形として gate し、 分類不能・想定外の形は fail-closed に実行形として扱う (regression: 従来は substring 一致のみで無害な言及コマンドまで deny していた)。 コマンド置換等の間接実行を検出した場合は、 `&` / `|` が wrapper 呼び出しに隣接していなくても位置を問わず deny するようにした (indirection 経由は substring を含む segment と実際に wrapper を実行する segment の対応関係を parser が追跡できないため)
- **indirection 判定を quote-aware に修正 (codex review High 指摘)**: 上記の規則 1 (indirection) が生 substring 一致 (`*'$('*` 等) だったため、 `grep -n '$(' run-codex-review.sh` のような wrapper 監査コマンドの引用符内 literal まで indirection と誤分類する regression があった。 `segment_has_indirection` 関数を追加し、 single quote 内・escape 済み (`\$(` 等) の literal は indirection から除外し、 double quote 内の実コマンド置換 (`"$(...)"` / バッククォート) は bash が実際に展開するため引き続き indirection として扱うようにした
- **mention 扱い segment の pipe 接続先を chain 全体で検査するよう修正 (codex review 指摘)**: `cat run-codex-review.sh | bash` のように、 substring を含む segment (`cat ...`) が規則 2 で言及扱いに分類されても、 pipe 接続先の `bash` が wrapper の内容を stdin 経由で実行できてしまう regression があった。 `mention_safe_segment` / `pipe_chain_all_mention_safe` 関数を追加し、 言及扱い segment が属する pipe chain (`|` のみで両方向に連続する極大区間、 `&&` / `||` / `;` / `&` で途切れる) 内の他の全 segment (substring を含まないものも含む) に mention-safe 判定 (indirection 不在 + allowlist / git 特例一致) を要求するようにした。 隣接 1 段でなく chain 全体を見るのは、 `cat wrapper | head -100 | bash` のように allowlist コマンドを 1 段挟むと隣接判定だけでは素通りするため (上流側 `bash gen.sh | grep -f - wrapper` も同様に検査する)。 `cat wrapper | grep "$(bash)"` のように allowlist head でもコマンド置換の内側が pipe の stdin を読んで実行できるため、 neighbor の indirection も同じ chain 走査で検査する
- **git 特例の subcommand 集合から `grep` を除外 (codex review 指摘)**: `git grep` は `--open-files-in-pager[=<cmd>]` / `-O<cmd>` option で外部プログラムを起動できるため、 言及扱いの git 特例 subcommand 集合 (`diff` / `log` / `show` / `status` / `ls-files` / `rev-parse` / `cat-file`) から `grep` を外した。 単体コマンドの `grep` は引き続き allowlist に残るため、 `git log -- file | grep pattern` のように単体 `grep` へ差し替えて使うことができる
- bg / pipeline deny の文言を、 gate 通過後にのみ到達する codex-reviewer subagent 向けに更新し、 直接実行の再実行を案内する文言 (「デフォルト false で再実行してください」等) を廃止した
- `agents/codex-reviewer.md` の frontmatter `description` から wrapper のパスと marker 実装詳細を削除し、 起動条件 (呼び出しタイミングと subagent_type) 中心の記述に縮小した (手順詳細は body のみに保持)
- `block-pre-push.sh` の deny メッセージで、 一部マーカーのみ失効している場合の該当 subagent 単独再起動を正規案内化した (`commands/review.md` も同じ案内で整合)
- `run-codex-review.sh` ヘッダの stale な記述 (v1.1.0 時代の「deny メッセージで wrapper 直接実行を案内する」という記述) を、 v3.0.0 以降の subagent 経由起動 + v4.0.0 の agent_type gate という現行実態に更新した (実行コードの変更なし)

### v3.1.3 → v3.1.4 の変更点

- 実機 E2E により、Codex 0.144.4 の `spawn_agent` は `agent_type` selector を公開せず、project custom agent の停止 event も `agent_type=default` になることを確認した。generic agent は role 固有の heading/footer を複製できるため、出力文字列だけで reviewer identity を認証することはできない
- `SubagentStop` hook の matcher と script を 3 named type の完全一致に戻し、`agent_type=default` は marker を書かない fail-closed 契約にした。`review-codex` Skill も `agent_type` selector が無い runtime で generic agent を起動せず、marker を生成せず停止する
- 現行 Codex runtime では安全な reviewer identity 契約を構築できないため、Codex marketplace の配布状態を `excluded` にした。Codex entry、manifest、Skill、hook は生成せず、Claude Code 版 v3.1.4 は従来どおり配布する。Codex 用 named profile template と adapter source は、selector を公開する将来 runtime の再検証用に保存する

### v3.1.2 → v3.1.3 の変更点

- Claude Code も `CLAUDE_PLUGIN_ROOT` / `CLAUDE_PLUGIN_DATA` を持つため、plugin env の存在で Codex を識別する方式を廃止し、hook payload の非空 `turn_id` で runtime を識別するよう修正した。Codex marker storage は `PLUGIN_DATA` を優先し、未設定時は `CLAUDE_PLUGIN_DATA` を互換 fallback として使う。選択した path が空・relative path なら `.git` へ fallback せず fail-closed を維持する

### v3.1.1 → v3.1.2 の変更点

- Codex の既定 workspace-write sandbox では `.git` が read-only のため、Codex marker を writable な `PLUGIN_DATA/pre-push-review/markers/<repo-key>/` へ移した。`repo-key` は physical git-dir の絶対 path を SHA-256 にして repository / linked worktree を分離し、`PLUGIN_DATA` が使えない場合は `.git` へ fallback せず push gate を fail-closed に deny する。Claude Code の `.git` storage、marker filename、review hash、gate 契約は変更していない

### v3.1.0 → v3.1.1 の変更点

- Codex v0.144.4 が custom agent の `name` に hyphen を受理しないため、Codex の 3 agent type を underscore 形式へ変更した。既存 setup との互換性のため TOML filename は hyphen 形式のまま維持し、marker filename と hash 契約も変更していない

### v3.0.5 → v3.1.0 の変更点

- Codex plugin manifest と `review-codex` Skill を追加した。初期実装では Claude agents の review contract を参照して Codex native subagent 3 本を並列実行した
- Codex の review 完了後に既存 diff hash / marker lib を使う `mark-review.sh` を追加し、Claude 側と同じ push gate を共有した。現行実装では project custom agent + SubagentStop hook による自動 marker を正規フローとし、この helper は通常フローから外れている
- push deny の復旧案内に Codex の `$pre-push-review:review-codex` を追加した。初期実装で Skill orchestration に依存していた完了検知は、現行実装では Codex runtime の lifecycle payload と role 固有 report footer の検証へ置換されている

### v3.0.4 → v3.0.5 の変更点 (#126)

- マーカー hash の入力に HEAD / merge-base の commit OID 束縛行を追加しました。add→revert で net diff がレビュー時点の値に戻っても、commit 列が変わっていればマーカーが失効します (それまでは未レビューの commit A + revert B が既存マーカーで push できました)。diff は `--no-ext-diff --no-textconv` 付きで取得し、staged / unstaged diff の取得失敗も hash 計算全体の失敗として fail-closed に伝播します
- 空 push の早期 skip 判定を「hash == 空入力の sha256」から tree OID / plumbing ベースの判定関数 `is_empty_push` / `is_empty_push_in` (lib/diff-hash.sh) に分離・厳格化しました。全 commit が empty commit (tree 変更なし、merge 非含有) の鎖のみ skip し、「commit A + A の revert」だけを積んだ fresh branch がマーカー検証なしで push できた同根の穴を塞ぎます。空 commit のみの push (issue claim 手順等) は従来どおりレビュー無しで通ります
- 計算式変更に伴い、既存マーカーは更新後最初の push で一度失効します (再レビュー 1 回で回復)

### v3.0.3 → v3.0.4 の変更点

plugin description (plugin.json / marketplace.json / リポジトリ README の一覧テーブル) を 1〜2 文に短縮しました。hook の動作変更はありません。

### v3.0.2 → v3.0.3 の変更点

hooks.json の description を簡潔な概要に刷新しました (変更履歴・設計経緯は本 README のバージョン節へ集約する方針に統一。hook の動作変更はありません)。

### v3.0.1 → v3.0.2 の変更点

plugin description (plugin.json / marketplace.json) を人間向けの簡潔な概要に刷新しました (変更履歴・実装詳細は本 README のバージョン節へ集約)。hook の動作変更はありません。

### v3.0.0 → v3.0.1 の変更点

共有 lib cmd-parser.sh の split_command のバグを修正しました: quote (`'...'`/`"..."`) 内の生改行がそのまま segment に残っていたため、呼び出し側の `while IFS= read -r line; do ... done < <(split_command ...)` が segment 内部の改行を次 segment との境界と誤認し、1 segment が複数行に分裂する潜在バグがありました (quote 内改行は空白 1 文字に正規化して 1 segment のまま保持するよう修正)。git-guardrails / enforce-draft-pr の cmd-parser.sh へも byte-identical で sync。挙動修正のみで本プラグインの hook の deny/allow 判定ロジックは不変です。

### v2.0.1 → v3.0.0 の変更点 (互換破壊あり)

- **`agents/code-reviewer.md` を新設**: v2.x の Skill `/code-review` (Anthropic bundled skill / read-only correctness バグ検出) に相当する self-contained subagent。 prompt は標準 skill と独立に管理 (security-reviewer と同じ理由: 主 session から直接 skill を呼ぶと turn が終了、 subagent 内から呼んでも nested subagent 制約で sub-task が動かないため)。 tools は `Bash, Read, Glob, Grep, LS` で `Skill` / `Task` を含まない (= 標準 skill を invoke できない構造的防御)。
- **`agents/codex-reviewer.md` を新設**: codex review wrapper (`run-codex-review.sh`) を foreground で 1 回起動するだけの最小 subagent。 tools は `Bash` のみで、 wrapper の output (codex review の verdict / findings) を markdown report として親 session に返す。v3.0.0 当時は wrapper が codex-reviewed marker を書いたが、v4.0.1 で pending attestation + auto-mark 昇格へ変更した。
- **`commands/review.md` を 3 subagent 並列発出に書き換え**: Skill (`code-review`) + Bash (codex wrapper) + Agent (security-reviewer) の 3 経路混在を、 Agent x 3 (code-reviewer + codex-reviewer + security-reviewer) に統一しました。
- **`auto-mark.sh` の検知ロジックを Skill → Agent に移行 + name-only 受理を廃止**: PRECHECK\_RE と case 文から Skill `code-review` / `security-review` の検知を全廃。v3.0.0 当時は namespace 付き code/security reviewer のみを検知し、codex marker は wrapper が書いた。v4.0.1 では正規 report Status を検証できるようになったため namespace 付き codex-reviewer も検知対象へ加え、pending attestation を final marker へ昇格する。name-only (`code-reviewer` / `security-reviewer` / `codex-reviewer`) は他 plugin の同名 agent との衝突を避けるため引き続き受理しない。
- **`block-pre-push.sh` の deny メッセージを 3 Agent 案内に書き換え**: Skill (`code-review`) と Bash (codex wrapper) の fallback 起動コマンドを Agent x 3 に置換。 wrapper の絶対パス埋め込み (CODEX_WRAPPER_PATH) も削除しました (subagent 経由で起動するため不要)。
- **後方互換 / 移行**: 既存の `.claude-pre-push-code-reviewed` / `.claude-pre-push-codex-reviewed` / `.claude-pre-push-security-reviewed` marker file 名と hash 計算式は不変です。 v2.x で実行済みの marker は v3.0.0 でも hash が一致する限り有効。 v2.x ユーザは v3.0.0 アップグレード後の最初の push で「`/pre-push-review:review` を実行してください」 と案内され、 そこから 3 subagent が走ります。
- **major bump にした理由**: ユーザフロー変更 (Skill / Bash 経路の廃止、 Agent 統一) と auto-mark の検知契約変更 (Skill 検知の全廃) を伴うため major。 marker file 名と hash 計算式は不変なので、 既存 marker は hash 一致時に引き続き有効。

### 過去の変更点

詳細な経緯と過去の version 履歴は git log を参照してください。 代表的なマイルストーン:

- **v2.0.1**: post-v2 cleanup。 README 見出しレベル / `lib/exit-trap.sh` docstring / keywords を整理。 `auto-mark.sh` に substring pre-filter を追加。
- **v2.0.0**: `/pre-push-review:review` slash command 新設 (3 レビュー並列発出の確定的フロー)。 `/simplify` (cleanup-only) マーカー削除し 3 軸 defense-in-depth に純化。 CC version 依存の fail-open 緩和 (`lib/first-party-review.sh`) も削除して 3 マーカー常時必須化。 `lib/codex-companion-resolver.sh` の sort -V fallback を POSIX numeric field sort に置換。
- **v1.1.0**: codex review を `/codex:review` slash command 経由から bash wrapper (`run-codex-review.sh`) 経由に切替え、 bg 起動による silent failure 経路を構造排除。 wrapper を `run_in_background: true` / shell-level `&` `|` で起動する経路は `block-bg-codex-wrapper.sh` が deny。
- **v1.0.0**: Claude Code の bundled skill 分岐 (v2.1.147 で `/code-review` が read-only バグ検出器に分離、 v2.1.154 で `/simplify` が cleanup-only として再導入) に追随し、 push gate を 3 → 4 マーカーに拡張。 第一者 (Anthropic) レビューを CC version 依存の fail-open 緩和で実装。
- **v0.x**: pre-commit 境界から push 境界への移行、 redirection / pipeline / wrapper / target-override などの parser 強化、 macOS bash 3.2 互換、 EXIT trap による silent failure 可視化、 tag reachability check 等。

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install pre-push-review@natsuume-plugins
```

公式 codex プラグインへの依存があるため、 codex review wrapper を動作させるには次も install してください:

```bash
claude plugin install codex@openai-codex
```

## Codex 配布状態

pre-push-review は現在 Codex marketplace の配布対象外です。Codex 0.144.4 の `spawn_agent` schema は `agent_type` selector を公開せず、project custom agent を起動しても `SubagentStop` には `agent_type=default` が届きます。generic agent が role 固有の heading/footer を生成できる以上、その文字列を marker 更新の権限根拠にすることはできません。

このため Codex version 4.0.0 で distribution を `excluded` にし、Codex marketplace entry、`.codex-plugin/plugin.json`、Skill、hook を生成しません。repository 直下の `.codex/agents/pre-push-*.toml` も配布対象 adapter と誤認させないため配置しません。この除外により当時の Claude Code 版 (v3.1.4) の command、agent、hook、marker 契約が変わることはありませんでした。

Codex 版 v3.1.4 以前をインストール済みの場合、marketplace entry の削除だけでは local config と cache は自動削除されません。次を実行して旧 plugin を削除し、新しい Codex thread を開始してください。旧 thread や残存 cache の `default` fallback は使用しないでください。

```bash
codex plugin remove pre-push-review@natsuume-plugins
```

Codex 用の named profile template、setup script、`review-codex` Skill source、SubagentStop adapter source は、`agent_type` selector を公開する将来 runtime の再監査用に plugin tree 内へ保存します。保存した hook は `pre_push_correctness_reviewer`、`pre_push_independent_reviewer`、`pre_push_security_reviewer` の 3 named type だけを受理し、generic type では marker を書きません。`review-codex` Skill も tool schema に **`agent_type` selector** が無ければ generic agent を spawn せず、marker を生成せず停止する契約です。

`tests.test_pre_push_codex_adapter` は Codex distribution からの除外、`agent_type=default` の marker 生成拒否、3 named type の exact report 契約、plugin data 欠落時の fail-closed、Claude Code の `.git` marker 維持を検証します。

## 機能一覧

### Commands

#### `/pre-push-review:review` (v2.0.0 で新設 / v3.0.0 で 3 subagent 並列発出に書き換え)

**ファイル**: `commands/review.md`

push 前 3 レビューを **同じアシスタントメッセージで並列に** 3 subagent として起動する確定的フローです。 deny メッセージから案内されたら、 Claude はこのコマンドを実行し、 3 subagent (`pre-push-review:code-reviewer` + `pre-push-review:codex-reviewer` + `pre-push-review:security-reviewer`) を 1 つの assistant message 内で並列 `Agent` / `Task` tool call として発出します。 順序や引数の自律判断は構造的に排除されています。

並列発出が技術的に成立しない / 一部のレビューが失敗した場合は、 3 subagent を順次起動しても push gate の構造的保証は同じ (3 マーカーの hash 一致が成立すれば push 可)。 wall-clock が伸びるだけのトレードオフです。

一部の marker のみ「未実行」 / 「失効」 の場合は、 該当 subagent だけを Agent / Task tool で `run_in_background: false` を明示して単独再起動するのが正規経路です (v4.0.0 で正規化。 block-pre-push.sh の deny メッセージも同じ案内をします)。 3 subagent 並列発出が既定であることは変わりません。

### Hooks

#### 1. block-pre-push (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-pre-push.sh`

`git push` を含むコマンドを検出した際、 commit 列 (HEAD / merge-base の OID) + ブランチ全差分 + 未コミット差分のハッシュと **3 つのレビューマーカー** (code-reviewer / codex-reviewer / security-reviewer subagent 起因) のハッシュを比較し、 3 マーカーがすべて一致しなければ `deny` を返します。 3 マーカーは v2.0.0 から常にすべて必須 (CC version 依存の fail-open 緩和は廃止)。

**動作**:

- `git push --dry-run` / `git push -n` (remote ref を更新しない診断 push) は markers の状態に関わらず通す (no-op なので gate 不要)
- 単独実行 (`git push`) と複合コマンド (`xxx && git push ...`, `cd dir && git push ...`) の双方を検出
- `git -C dir push` や `git --git-dir=... push`、 `GIT_DIR=... git push` のような target-override 形式も許容 (cooperative 利用前提)
- カレントブランチが default branch (master/main) の場合は本フックでは gate せず、 `git-guardrails` の `block-default-branch-push.sh` に委譲 (重複 deny メッセージを避けるため)
- merge-base と HEAD の tree が一致し、 merge commit を含まず、 範囲内の全 commit の tree が HEAD tree と一致し、 かつ index / worktree が clean な場合は gate しない (空 push は通す。 tree OID ベースの判定)
- **working tree が dirty (staged または unstaged 変更あり) の場合は markers の状態に関わらず deny**: push される committed 部分とレビューされた working tree の乖離を防ぐため、 push 前に commit 完了を要求する
- 3 マーカーがすべて一致した場合はそのまま push を許容する (markers は明示削除しない: PreToolUse は push 成功を確認できないため、 remote rejection / 認証失敗 / ネットワーク失敗時に同じ state での再 push がレビュー必須になる無駄ループを避ける。 markers は次の編集で hash が変わったときに自然に失効する)
- ハッシュは `head <HEAD の commit OID>` 行 + `mbase <merge-base の commit OID>` 行 + `git diff <merge-base> HEAD` + `git diff --cached` + `git diff` (diff 3 種はいずれも `--no-ext-diff --no-textconv` 付き) を連結した入力に対する sha256 として計算する。 HEAD の commit OID をハッシュ入力に束縛したことで、 レビュー後に commit A を積んでから revert して戻す (net diff は review 時と同一でも commit 列は変わっている) 操作でもマーカーが自動失効するようになった (issue #126 の修正: 従来は branch 全差分 + 未コミット差分のみで計算していたため、 net diff が戻ると失効しているべきマーカーが復活してしまっていた)。 未コミットの edit があると `git diff --cached` / `git diff` の内容が変わりハッシュも変わる点は従来どおりで、 markers が失効し commit + 再 review を強制できる
- `deny` 時の `permissionDecisionReason` には、 各マーカーの状態 (`未実行` / `失効` / `✓ 最新の差分でレビュー済み`) と Claude Code の `/pre-push-review:review` を記載する。Codex については、現行 runtime に `agent_type` selector が無いため marketplace 配布対象外であり、generic agent や marker helper で代行しないことを案内する

**残っている deny 制約 (loop discipline 維持に必要な最小防御)**:

- `bash -c "..."` 等の **シェルラッパー** 経由 push は引き続き deny (クォート内のコマンドを本フックの文字列パーサで解析できず、 postfix scan も成立しないため)
- 単独の `&` (background) と `|` (pipeline) は deny (並列実行になりマーカー検証完了後に状態が変更される経路になるため)
- `git push` の **後** にシェル区切り文字 (`;`, `&`, `&&`, `||`, `|`) を続ける複合コマンドは deny (1 マーカー = 1 push 保証のため)
- 引用符で囲まれた `git push` 文字列 (`grep "git push" README` など) はテキスト参照とみなしフックは介入しません
- **`git push` の引数に引用符 (`"` / `'`) が含まれる形** は deny (例: `git push origin "other-branch"`)。 本フックの parser は引用符付き引数を確実に解析できないため、 refspec/オプションチェックを素通りさせる経路を保守的に塞ぐ
- `time git push ...` / `env git push ...` のように本フックが認識していない wrapper を介して push する形式は deny
- **`--all` / `--mirror` / `--tags`** は deny (複数参照 / tag 一括 push でマーカー検証対象外のコミットが混入するため)
  - tag を push したい場合は、 tag が指す commit を含むブランチを通常通りレビューして push し、 別の Bash 呼び出しで `git push origin <tag-name>` のように個別 tag を push する運用
- **現在ブランチと一致しない refspec を明示する形 (`git push origin other-branch` 等)** は deny
  - `git push` / `git push origin` / `git push origin HEAD` / `git push -u origin <現在ブランチ名>` は引き続き許容
  - `git push origin :branch` (削除、 source 空) はローカルレビュー対象外なので許容
  - `git push --delete origin <branch>` / `git push -d origin <branch>` (削除フラグ) は新規 commit を送らないので許容
  - `git push origin <tag-name>` (個別 tag push) は 2 段階の reachability check で扱う
- **working tree が dirty のまま push** は deny
- **`git config push.default=matching` 環境での refspec 省略 push** は deny

**サポート外 (本プラグインの範囲外で別レイヤーが必要)**:

- 別端末・別 clone から行われる `git push` は Claude Code hook の原理的範囲外で gate できない (本気で塞ぐなら `.git/hooks/pre-push` real git hook を別レイヤーで併設)
- GitHub サーバ側で実施される操作 (Web UI のマージ / rebase 等) も Claude Code hook 範囲外
- **default branch (master/main) 上での push は本プラグイン単独では gate されない**: 本プラグインは `git-guardrails` の `block-default-branch-push.sh` が default branch push を deny する前提で gate を skip する。 `git-guardrails` を併用していない環境では default branch 上の push が review なしで通る経路が残る

> **target-mismatch の構造的解決**: 本プラグインは独自の bash command parser (`lib/cmd-parser.sh`) と target resolver (`lib/target-resolver.sh`) で `cd dir && git push` / `git -C dir push` / `GIT_DIR=path/.git git push` の **実 push target を決定的に解決** し、解決した target cwd の runtime 別 storage (Claude Code は `.git`、Codex は physical git-dir key で分離した `PLUGIN_DATA` / `CLAUDE_PLUGIN_DATA`) に対して markers / hash 比較を行います。解析不能な形式 (subshell `(...)`, brace group `{...}`, `bash -c "..."`, `pushd`/`popd`, `export GIT_DIR=...`, `--work-tree=...`, `time` / `env` 等の未対応 wrapper) は **保守的に deny** します。

#### 2. block-bg-codex-wrapper (PreToolUse, matcher: `Bash`)

**ファイル**: `hooks/scripts/block-bg-codex-wrapper.sh`

`run-codex-review.sh` wrapper の起動を検証する PreToolUse hook です。 **v4.0.0 で agent_type 検証 gate を追加** (issue #267): wrapper を含む Bash 実行の hook payload トップレベル `agent_type` が `pre-push-review:codex-reviewer` (namespace 付き完全一致) でなければ **fail-closed に deny** します。 `agent_type` 欠落はメインセッションからの直接実行、 または `agent_type` を hook payload に含めない旧 Claude Code を意味します。 本 gate は **Claude Code 2.1.211 で実機検証済み** (= 動作要件の検証済み最低 version) で、 それより古く `agent_type` を送らない Claude Code では、 正規フロー (`/pre-push-review:review` や codex-reviewer subagent 経由起動) からの wrapper 起動も deny されるため、 2.1.211 以上への更新が必要です。

**gate の発火対象は実行形コマンドのみ**: command が `run-codex-review.sh` の substring を含んでいても、 wrapper を実行せず言及するだけの read-only 検査コマンド (`cat` / `grep` / `git diff` 等) は agent_type gate を skip して許可します。 interpreter 起動 (`bash` / `sh` 等)、 コマンド置換 (`$(...)` / バッククォート / `<(...)` / `>(...)`) を含む形、 不明コマンドのみを実行形として gate します。 分類は fail-closed (不明・解析不能な形はすべて実行形扱い) です。 コマンド置換等の間接実行を含む場合は、 `&` / `|` が wrapper 呼び出しに隣接していなくても位置を問わず deny します (詳細は `block-bg-codex-wrapper.sh` のファイルヘッダ「検知ロジック」節を参照)。

agent_type gate を通過した後は、 従来どおり次の 2 経路の background 起動を検知します:

- Bash tool option `run_in_background: true` で wrapper を起動
- shell-level の `&` (background) や `|` (pipeline) で wrapper を連結

理由: 上記経路で起動すると **codex-reviewer subagent (ひいては親 session) は wrapper の stdout / stderr (= codex review の verdict / findings) を観察しない / 途中でしか観察しない** ため、正しい parent-safe report を組み立てられません。v4.0.1 以降は pending attestation があっても report 成功前に final marker へ昇格しないため push gate bypass にはなりませんが、無駄な review cycle と不正 report を防ぐため foreground を引き続き強制します。 jq 不在等の環境失敗時は本 hook 全体として fail-open に倒れますが、 agent_type gate 自体は fail-closed です。

#### 3. auto-mark (SubagentStart / SubagentStop, matcher: `^pre-push-review:(code|codex|security)-reviewer$`)

**ファイル**: `hooks/scripts/auto-mark.sh`

3 reviewer subagent の **実行完了** を subagent lifecycle hook (SubagentStart / SubagentStop) で自動検知し、対応するマーカーファイルに「commit 列 (HEAD / merge-base の OID) + branch 全差分 + 未コミット差分のハッシュ」 を書き込みます。v3.0.0 で Skill `/code-review` / `/security-review` の検知は全廃しました。v4.1.0 で completion 検知を PostToolUse から subagent lifecycle hook へ完全移行しました (Claude Code v2.1.198 以降、 Agent tool は既定で background 起動になり、 PostToolUse は起動受理時にしか発火しないため)。Codex は wrapper が review 時 hash の pending attestation を書き、本 hook が parent-safe report 成功後に final marker へ昇格します。PostToolUseFailure でも本 script を呼び、残った Codex pending を破棄します。

hooks.json の matcher は SubagentStart / SubagentStop とも `^pre-push-review:(code|codex|security)-reviewer$` で、 3 reviewer subagent 以外では本フックは発火しません。 スクリプト側でも agent_type の完全一致を再検証します (matcher の regex 解釈には依存しない)。

**検知ルール (v4.1.0)**:

| 検知対象                                                | event | 判定                                                                                                                                                                            | 書き込むマーカー                              |
| ------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| `pre-push-review:code-reviewer` subagent の完了 | `SubagentStart` + `SubagentStop` | launch attestation の存在・一回限りの消費 + 開始時 hash と現在 hash の一致 + `last_assistant_message` 内の単一 `Status: pass\|findings` 行 | `<git-dir>/.claude-pre-push-code-reviewed`    |
| `pre-push-review:codex-reviewer` subagent の完了 | `SubagentStart` + `SubagentStop` | 上記 completion 条件 + wrapper pending hash が current hash と一致 | `<git-dir>/.claude-pre-push-codex-reviewed` |
| `pre-push-review:security-reviewer` subagent の完了 | `SubagentStart` + `SubagentStop` | launch attestation の存在・一回限りの消費 + 開始時 hash と現在 hash の一致 + `last_assistant_message` 内の単一 `Status: pass\|findings` 行 | `<git-dir>/.claude-pre-push-security-reviewed` |

**subagent lifecycle hook (2 段階) で検知する理由**:

各 subagent は内部で標準 skill を呼ばずに self-contained でレビューを実行します。 Claude Code の Agent tool は既定で background 起動になり、 `async_launched` で正常 return した後は subagent 完了時に PostToolUse が発火しないため、 開始 (`SubagentStart`) と完了 (`SubagentStop`) の 2 イベントに分けて検知します。 `SubagentStart` は「レビューがこの差分に対して開始された」ことを launch attestation として one-shot 記録し、 `SubagentStop` は (a) attestation の一回限りの消費 (b) 開始時 hash と現在 hash の一致 (c) `last_assistant_message` 内の単一 `Status: pass\|findings` 行 (d) `stop_hook_active == false` をすべて検証した場合のみ marker を書きます。SendMessage resume 後の再 stop・レビュー開始後の差分変更・重複 stop・`execution-failed` は fail-closed に遮断され、push gate が deny のまま残るため silent-pass しない設計です。

**Codex pending attestation を挟む理由**:

wrapper exit 0 だけで final marker を書くと、その後の report 正規化が失敗・中断しても push gate が通り得ます。一方、report 完了時の current hash だけで marker を書くと review 中に branch state が変わった場合に「Codex が見ていない差分」をレビュー済みと誤認します。このため wrapper は review 対象 hash を pending に atomic write し、auto-mark は正規 report 成功と pending/current hash 一致の両方を確認して final marker へ atomic rename します。

**書き込みをスキップする条件**:

- `stop_hook_active` が boolean `false` でない (stop hook による継続中の中間 stop)
- launch attestation が無い、regular file でない (symlink 含む)、または開始時 hash と現在 hash が不一致
- `last_assistant_message` に単一の `Status: pass` / `Status: findings` 行が無い (`execution-failed`、欠落、重複、未知値、非 string)
- `agent_type` が namespace 付き 3 reviewer 以外、または `agent_id` が `^[A-Za-z0-9._-]{1,128}$` に不一致
- codex-reviewer では pending attestation が無い、regular file でない、または current hash と不一致
- カレントブランチが default branch (master/main)
- default branch (origin/HEAD) が検出できない (origin が無い等)

### マーカーファイル

すべて `<git-dir>` 配下に配置 (リポジトリ単位で共有、 ブランチ単位ではない):

| ファイル | 内容 | 寿命 |
|---|---|---|
| `.claude-pre-push-code-reviewed` | `pre-push-review:code-reviewer` subagent 完了時の commit 列 + branch 全差分のハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-codex-reviewed` | codex review + parent-safe report 完了時の commit 列 + branch 全差分のハッシュ。wrapper pending を auto-mark が昇格する | 次の編集で hash が変わると失効 (明示削除しない) |
| `.claude-pre-push-codex-reviewed.pending` | wrapper が束縛した review 対象 hash。final report 成功時だけ marker へ rename | report 失敗・hash mismatch・次回 wrapper 起動で削除 |
| `.claude-pre-push-security-reviewed` | `pre-push-review:security-reviewer` subagent 完了時の commit 列 + branch 全差分のハッシュ | 次の編集で hash が変わると失効 (明示削除しない) |

> **v2.x → v3.0.0 アップグレード時の注意**: v2.x で実行済みの 3 マーカー (code-reviewed / codex-reviewed / security-reviewed) は v3.0.0 でも hash が一致する限り有効です。 marker file 名と hash 計算式は不変なので追加の cleanup は不要です。

### Agents

#### `pre-push-review:code-reviewer` (subagent / v3.0.0 で追加)

**ファイル**: `agents/code-reviewer.md`

branch 全差分に対する correctness バグ検出を **self-contained に** 実行し、 結果のマークダウンレポートを親 session に返す subagent です。 v2.x までの Skill `/code-review` (Anthropic bundled skill / read-only correctness バグ検出) を置換するため v3.0.0 で追加されました。

**動作**:

- tools は `Bash, Read, Glob, Grep, LS` に制限 (Edit / Write / Skill / Task はすべて非許可)。 read-only でファイル改変を防ぎ、 `Skill` を外すことで標準 `/code-review` skill を invoke できないようにしている (理由は security-reviewer と同じ; 下記)。 `Task` を外すのは Claude Code が subagent からの nested subagent 起動を禁止しているため
- subagent body には logic errors / null/undefined / error handling / resource leaks / concurrency / API misuse / data corruption の各カテゴリと exclusion ルール (style / docs / perf / refactor / security / pre-existing bug 等) が prompt として含まれており、 単一 turn で review を完遂する
- 親 session は `Agent` / `Task` tool の result として parent-safe markdown report を受け取り、 後続フロー (`git push` 等) を継続できる。具体的な failure scenario は subagent context に留め、追加検証時は同じ subagent を resume する
- SubagentStop hook (auto-mark.sh) は launch attestation の開始時 hash と現在 hash の一致、および final report の単一 `Status: pass|findings` 行を確認して code-reviewed マーカーを更新する
- model は `inherit` で親 session と同じモデルを使用

#### `pre-push-review:codex-reviewer` (subagent / v3.0.0 で追加)

**ファイル**: `agents/codex-reviewer.md`

codex review wrapper (`hooks/scripts/run-codex-review.sh`) を foreground で 1 回起動し、 wrapper の stdout / stderr を subagent context 内で評価して parent-safe markdown report に抽象化する最小 subagent です。 v2.x までの Bash 直接起動 (commands/review.md と deny メッセージに wrapper 絶対パスを案内) を置換するため v3.0.0 で追加され、v4.0.1 で verbatim relay を廃止しました。

**動作**:

- tools は `Bash` のみ (Read / Edit / Write / Skill / Task はすべて非許可)。 wrapper-only な実行サーフェスを構造的に強制
- subagent body は wrapper を `run_in_background: false` で 1 回起動し、raw output を final reply へコピーせず parent-safe report に変換する
- 親 session は finding の priority / location / impact / verification / fix direction / disposition を受け取る。実行可能な command、payload、環境値、段階的な再現・回避手順、raw stdout / stderr は subagent context に閉じ込められる
- exact detail を使った追加確認が必要な場合は同一 codex-reviewer を resume し、検証結果だけを再度 parent-safe report で受け取る
- wrapper は exit 0 完了時に hash-bound pending attestation を atomic write し、auto-mark が subagent の正規 `pass/findings` report と current hash 一致を確認して codex-reviewed marker へ昇格する
- model は `inherit` で親 session と同じモデルを使用
- **v4.0.0 で frontmatter `description` を起動条件中心に縮小**: 呼び出しタイミング (deny メッセージがどのマーカーを指摘したときか) と `subagent_type="pre-push-review:codex-reviewer"` の呼び出し方だけを記載し、 wrapper path や marker/attestation 実装詳細はメインセッションへ直接開示しない (実行手順・report 形式は引き続き body に定義)

#### `pre-push-review:security-reviewer` (subagent)

**ファイル**: `agents/security-reviewer.md`

branch 全差分に対するセキュリティレビューを **self-contained に** 実行し、 結果のマークダウンレポートを親 session に返す subagent です。 v0.3.0 で追加されました。

**動作**:

- tools は `Bash, Read, Glob, Grep, LS` に制限 (Edit / Write / Skill / Task はすべて非許可)。 read-only でファイル改変を防ぎ、 `Skill` を外すことで標準 `/security-review` skill を invoke できないようにしている (理由は下記)。 `Task` を外すのは Claude Code が subagent からの nested subagent 起動を禁止しているため
- subagent body には input validation / authn-authz / crypto-secrets / injection / data-exposure の各カテゴリと exclusion ルール (DoS / 既存依存 CVE / テストファイル等) が prompt として含まれており、 単一 turn で review を完遂する
- 親 session は `Agent` / `Task` tool の result として parent-safe markdown report を受け取り、 後続フロー (`git push` 等) を継続できる。具体的な attack scenario は subagent context に留め、追加検証時は同じ subagent を resume する
- SubagentStop hook (auto-mark.sh) は launch attestation の開始時 hash と現在 hash の一致、および final report の単一 `Status: pass|findings` 行を確認して security マーカーを更新する (`execution-failed` / 欠落 / 重複 / 未知値では書かず、silent-pass を防ぐ)
- model は `inherit` で親 session と同じモデルを使用

#### code-reviewer / security-reviewer subagent が標準 skill を invoke しない理由 (共通)

(1) 主 session の Claude が直接 `/code-review` / `/security-review` を呼ぶと skill prompt 末尾「Your final reply must contain the markdown report and nothing else.」 によって turn が終了し、 後続フロー (`git push`) まで進まない。
(2) subagent 内から invoke しても、 標準 skill 本体は内部で sub-task (Task tool) を spawn する設計だが、 Claude Code は **subagent 内での nested subagent 起動を禁止** している (公式ドキュメント `subagents cannot spawn other subagents`)。 sub-task が動かないため degraded mode で実行される。 v2.x では PostToolUse が Skill launch 時点で発火するためマーカーは書かれてしまい silent-pass の経路があったが、 v3.0.0 で Skill 検知を全廃したのでこの経路は閉じている。
(3) このため subagent は **同等のレビュー内容を self-contained な prompt として持ち**、 標準 skill を invoke しない設計に倒している。 標準 skill の prompt とは別管理になるため、 Anthropic 側の今後の改善は手動で追随する必要がある (トレードオフ)。

**呼び出しタイミング (3 subagent 共通)**: `/pre-push-review:review` slash command の指示で `run_in_background: false` の 3 並列 `Agent` / `Task` tool calls として起動する。 deny メッセージにも個別起動のフォールバック手順を案内している。

## 関連プラグイン

- [git-guardrails](../git-guardrails/): default branch (master/main) への直接書き込みを deny。 本プラグインは default branch 上の push を git-guardrails に委譲します
- [decompose-bash](../decompose-bash/): Bash コマンドを最小粒度に分解する SessionStart 注入。 本プラグインの PreToolUse hook が `&&` / `||` 等の合成で取りこぼされないよう、 Claude に各コマンドを独立 Bash 呼び出しに分けさせる
