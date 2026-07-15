# session-handoff プラグイン

Claude Code では context 使用率が閾値を超えたときに handoff ドキュメントの作成を促します。
Codex では repository ごとの provider/privacy 明示 opt-in 後に限り、context compaction の直前に
transcript から handoff を自動生成します。作成された handoff は次の session start で自動注入され、
context 圧縮や `/clear` を挟んでも直前までの作業を引き継げるようにします。

## バージョン

v0.2.0

### v0.1.0 → v0.2.0 の変更点

- Codex plugin manifest と Codex-only PreCompact adapter を追加した。`auto|manual` compaction の
  直前に transcript 末尾を read-only/ephemeral な nested Codex で要約し、pending handoff を
  atomic 保存して `SessionStart(source=compact|resume)` の同一 session 注入経路へ接続する
- Codex hook input では厳密な context 使用率を取得できないため、Claude Code と同じ 60% trigger
  ではなく「compaction 直前」を代替 trigger とした。保証差・fail-open 条件・fixture 検証範囲を
  本 README に明記した
- nested Codex が親 session と異なる default provider/account へ transcript を送る可能性があるため、
  producer を既定無効にした。git-dir 内の exact v1 owner-only marker を inspect → provider/privacy
  明示承認 → action-bound token の workflow で enable/disable する

## 機能概要

runtime 別の producer と、共有の consumer で構成されます。

1. **Claude Code producer (`detect-context-threshold.sh`, PostToolUse)**: context 使用率が閾値
   (既定 60%) を超えたことをツール実行のたびに検知し、超えていれば handoff ドキュメントの
   作成を Claude に促す指示を注入する
2. **Codex producer (`save-codex-handoff.sh`, PreCompact)**: 明示 opt-in 済み repository だけで
   `auto` / `manual` compaction の直前に transcript 末尾から Markdown handoff を別の Codex process
   で生成して atomic 保存する。既定は disabled
3. **共有 consumer (`inject-pending-handoff.sh`, SessionStart)**: 新しい context になる `clear` /
   `startup` と、Codex producer が作った同一 session の handoff に対する `resume` / `compact` で、
   直近 24 時間以内に作成された未消費の handoff があれば、その内容を前置き文とともに自動注入する

## hook の動作と境界

### Claude Code 検知 hook (PostToolUse, matcher `*`)

- 対象は cwd が git リポジトリのセッションのみ (非 git プロジェクトでは検知しない)
- context 使用率は natsuume-statusline (後述) が書き出すキャッシュファイルから読む
- **古いキャッシュでは検知しない**: キャッシュの `updated_at` (欠落時はファイル mtime) が
  600 秒より古い場合、producer 停止中とみなして検知しない (statusline が構成解除された後に
  残った古い値での誤通知・検知抑止を防ぐ)
- **1 セッション 1 回**: 一度通知したセッションでは再通知しない (marker による排他)
- **marker の意味は「通知を発行済み」であり「handoff が保存済み」ではない**: 通知後に
  Claude が実際に handoff ファイルを書く前にセッションが終了しても、次回以降のそのセッションで
  再通知はされない (割り切り。marker はあくまで「注入した」ことの記録であり、Claude が実際に
  指示に従ったかどうかまでは追跡しない)
- marker の claim は `mkdir` による atomic 操作で行うため、並列なツール実行があっても通知は
  1 回しか発行されない

### Codex 保存 hook (PreCompact, matcher `auto|manual`)

- Codex hook input schema で必須の extension `turn_id` があるときだけ動作する。Claude Code では
  同じ `hooks.json` を読んでも `turn_id` が無いため無音 no-op となり、既存の 60% producer を
  変更しない
- privacy-safe default は disabled。`<git-dir>/session-handoff/.codex-summary-opt-in` が実行 user
  所有の通常ファイル、mode 0600、内容が exact
  `session-handoff:nested-codex-summary-opt-in:v1` の全条件を満たす場合だけ transcript を読み、
  provider 境界を越える直前にも同じ条件を再検証する。symlink・非通常・非 owner・内容/version・
  mode 不一致は nested Codex を起動せず fail-open
- hook input の `transcript_path` は通常ファイル・非 symlink・実行 user 所有・readable の場合だけ
  採用する。transcript の形式は安定 API ではないため JSON として parse せず、既定で末尾
  524288 bytes だけを未信頼の opaque text として要約対象にする。byte tail で先頭の UTF-8 / record
  が途中になった場合は、上限の直前 1 byte を含めて取得して最初の改行までを除外する。これにより
  完全 record の境界を保ち、完全な record が残らなければ fail-open にする
- nested process は `codex exec --sandbox read-only --ephemeral --disable hooks
  --ignore-user-config --ignore-rules` と専用の空作業ディレクトリを使用する。prompt でも transcript
  内の命令を実行しないよう制約する。空作業ディレクトリは git tree 外に置いて repository の
  `AGENTS.md` / `.codex/config.toml` 継承を避け、環境変数と hook disable の二重ガードで再帰を防ぐ。
  static prompt、trusted metadata、`<transcript>` framing 済み excerpt を単一 stdin stream にまとめ、
  positional prompt は使わず `codex exec ... -` へ渡す
- `--ignore-user-config` により、親 session が Azure / Bedrock / local/custom provider や別 account
  で動作していても transcript excerpt が nested Codex の default provider/account へ送られる可能性
  がある。marker はこの repository 固有の越境同意であり、provider が同じことを保証するものではない
- prompt と transcript excerpt をまとめた request は mode 0600 の一時ファイルから stdin fd を
  開いた直後に pathname を unlink する。nested process の実行中に signal/timeout が発生しても
  request file を一時領域へ残さない
- summary は `<git-dir>/session-handoff/.codex-handoff-summary.*` に mode 0600 で生成し、内容と
  サイズ、7 つの必須 Markdown 見出しを確認した後 `pending-codex-*.md` へ rename する。pending
  として見えるのは完成後だけ。非 git workspace は既存 producer と同様に無音で対象外とする
- transcript 欠落・symlink・codex CLI 不在・未認証・model/network error・空/過大 summary・
  保存先検証失敗では、可能なら `systemMessage` で警告して **exit 0** にする。handoff が無くても
  compaction は継続する (fail-open)。`jq` 不在時だけは安全な JSON 警告を組み立てられないため
  無音 fail-open とする
- nested Codex の起動は compaction を最大 180 秒待たせ、追加の Codex usage を消費する

### 注入 hook (SessionStart, matcher `clear|startup|resume|compact`)

- `clear` / `startup` は runtime を問わず既存 pending 全般を対象にする
- `SessionStart` input には Codex 固有の runtime field がないため、`resume` / `compact` では
  `pending-codex-<sanitized session_id>-*.md` のうち input の `session_id` と一致するものだけを
  timestamp / unique suffix を右端から分離して厳密に照合してから claim する。Claude Code の
  generic pending、session id が prefix だけ一致する別 Codex session の pending は消費しない。
  これにより Claude の `resume` で既に復元済みの context へ handoff を重複注入せず、generic
  pending を後続の `clear` / `startup` まで保持する
- 走査対象は `<git-dir>/session-handoff/` 配下の `pending-*.md` (mtime 24 時間以内のもの)。
  30 日を超えたファイルは best-effort で削除する
- **at-most-once**: 複数セッションが同時に起動しても、rename (`pending-` → `consumed-`)
  の atomic 性により同じ handoff が 2 重に注入されることはない。ただし、claim (rename) の
  直後・出力の直前にプロセスが異常終了した場合、その handoff は「消費済み」のまま失われる
  (再送しない割り切り)
- **staleness**: 24 時間を超えた pending は注入しない (作成から時間が経ちすぎた handoff を
  無条件に新セッションへ流し込まない)。24 時間以内に複数の pending がある場合、最新の 1 件のみを
  本文注入し、残りはパスの列挙に留める

## Claude Code producer 依存 (context 使用率キャッシュ)

検知 hook が読む context 使用率は、自プラグインでは取得できません。以下のいずれかで
`${TMPDIR:-/tmp}/natsuume-context-cache-<uid>/<session_id>.json` にキャッシュが書き出されている
ことが前提です。

- **natsuume-statusline (v0.6.0+)** を `statusLine.command` に登録している (通常の利用経路)
- 上記を使わない場合は、本プラグインの `/session-handoff:setup` で cache 専用の
  安定 launcher を登録する (下記)

キャッシュが無い/古い間、検知 hook は無音でスキップします (エラーにはなりません)。

## setup Skill

Codex の `$session-handoff:setup` は statusline や config を変更せず、次の opt-in workflow を実行します。

1. `scripts/setup-codex-summary.sh inspect --repo <repo>` を read-only で実行し、target、state、exact
   protocol content、provider boundary、enable/disable plan token を提示する
2. nested `--ignore-user-config` Codex が parent と異なる default provider/account へ transcript excerpt
   を送信し得ること、追加 usage・最大180秒・summary 品質差を説明し、repository 固有の明示承認を得る
3. 承認した action の token だけで `enable` または `disable` を実行する。state/token mismatch では
   再 inspect・再承認し、symlink / 非通常 / 非 owner marker は変更しない
4. enable 後は `/hooks` で PreCompact / SessionStart hook の current hash を確認・trust する

inspect は filesystem を変更しません。enable は mode 0600 temp file から同一 directory 内で atomic
rename し、disable は安全な owner regular marker だけを削除します。marker を直接手作業で作らず、
setup Skill/helper を使います。disable 後も既存 pending の注入は残ります。

Codex の `model_auto_compact_token_limit` を明示すると auto compaction の時期を早められますが、
これは「handoff の trigger」だけでなく **その repository/session 全体の compaction 時期を変える
侵襲的な absolute-token 設定**です。モデルの context window から 60% を自動算出・追随する設定では
ありません。利用者が対象 scope・token 値・影響を明示的に選んだ場合に限って別途設定し、本 Skill
は既定で書き換えません。

### Claude Code の launcher setup

`/session-handoff:setup` は、context 使用率キャッシュの producer を用意するための任意の
セットアップです。現在の `~/.claude/settings.json` の `statusLine.command` を分類し、以下の
いずれかを行います。

- natsuume-statusline (0.6.0+) が既に構成済み、または cache が既に稼働中と判断できる場合は
  何もしない (構成済みとして報告する)
- 他の statusline が設定済みの場合は、既存の表示を変えずに包む安定 launcher
  (`~/.claude/session-handoff-statusline-launcher.sh`) を設置する。設置前に承認を得る
- statusline が未設定の場合は、natsuume-statusline の導入または表示無しの
  cache-only launcher 登録のいずれかを選ばせる

### 連鎖検査

他の statusline を包む前に、**1 段の連鎖検査**を行います。既存の statusline が指すスクリプト
ファイルの中に、(a) 自 launcher ファイル名への平文参照、または (b) 既知形式の base64 代入行
(例: `rate-limit` の `INNER_COMMAND_B64=...`) を decode した中身への参照、のいずれかが見つかった
場合は「既にラップ済み」と判断して再ラップせず、連鎖の存在を報告するに留めます。代入行はあるが
decode に失敗し、かつ自 launcher が既に存在する場合は、循環リスクありとして変更せず曖昧な連鎖を
報告します。

### 実行時の再帰ガード

連鎖検査は setup 時点の静的な検査であり、検査をすり抜けた循環構成 (例えば手動編集や他 plugin の
将来変更) が万一残っていた場合の安全網として、launcher は実行時にも固有名の環境変数
(`NATSUUME_SESSION_HANDOFF_LAUNCHER_ACTIVE`) で自己再帰を検知します。既に設定済みなら何も出力
せず正常終了するため、無限再帰・ハングにはなりません (ただし循環構成が残っている間、表示は
空になります)。

### launcher の設計

- settings.json には plugin cache の version 固有パスを直接焼き込みません。安定パス
  (`~/.claude/session-handoff-statusline-launcher.sh`) を生成して登録し、launcher が実行時に
  plugin cache から session-handoff 最新版の `scripts/context-cache-dump.sh` を解決します
  (`/plugin update` 後も再 setup 不要)
- 元の statusline command は base64 で launcher 内に損失なく埋め込みます (single quote・改行を
  含むコマンドでも壊れません)
- dump の解決・書き込みに失敗しても、元の statusline への委譲は継続します (fail-open)

## 環境変数

| 変数 | 意味 | 既定値 |
|---|---|---|
| `SESSION_HANDOFF_THRESHOLD` | 検知 hook が使う context 使用率の閾値 (1〜99 の整数)。不正値・未設定は既定値にフォールバック | `60` |
| `SESSION_HANDOFF_CODEX_TRANSCRIPT_MAX_BYTES` | Codex PreCompact が transcript 末尾から渡す最大 byte 数 (65536〜4194304。不正値は既定値) | `524288` |

## 保証差

| 観点 | Claude Code | Codex 代替実装 | 保証差 |
|---|---|---|---|
| trigger | statusline cache が示す使用率 60% 以上を PostToolUse で検知 | `PreCompact(auto|manual)` | Codex は厳密な60%ではなく実際の compaction 直前。tool を使わない turn でも発火する一方、早期 handoff は保証しない |
| handoff 作成 | main model に保存を指示する advisory | 明示 opt-in 後に別の Codex が transcript excerpt を Markdown 要約して保存 | 既定では生成しない。enable 後も追加 usage・最大180秒の遅延があり、model/network/auth に依存する |
| provider/privacy | main session の provider 境界内 | `--ignore-user-config` の nested Codex | parent provider/account を継承・照合できず default provider/account へ越境し得るため、repository ごとの exact marker 明示同意を必須にする |
| 入力完全性 | main session が保持する context | transcript の末尾最大524288 bytes | transcript 形式は不安定で、tail truncation により初期の判断を欠落し得る |
| 意味品質 | main model の規律遵守に依存 | summary model の正確性と prompt-injection 耐性に依存 | fixture は LLM の意味品質・非幻覚・秘密除去を証明しない |
| 配送 | pending を rename-first claim | 同じ consumer を共有 | at-most-once は同等。claim 後・出力前 crash では再送せず失われ得る |
| 失敗時 | cache 不在等は no-op | action可能な失敗は警告し no-op | compaction 継続を優先するため fail-closed な handoff 保証ではない |

専用 app-server host なら token usage event を使った独自 trigger を構築できますが、通常の marketplace
plugin 単体の保証範囲には含めません。

## 検証テスト

repository root で次を実行します。

```bash
python3 -m unittest tests.test_session_handoff_codex_adapter
bash -n plugins/session-handoff/hooks/scripts/*.sh plugins/session-handoff/scripts/*.sh \
  plugins/session-handoff/scripts/lib/*.sh
jq empty plugins/session-handoff/hooks/hooks.json
```

fixture は、既定無効で transcript/nested Codex に触れないこと、inspect の read-only 性、明示 token
付き enable/disable、exact content/mode、symlink/nonregular 拒否、stale/action token 拒否、Codex runtime
guard、`auto|manual` matcher、transcript tail 上限、static prompt / trusted metadata / transcript framing の
単一 stdin と最終引数 `-`、nested Codex の read-only/ephemeral/hooks-disabled/
user-config-and-rules-ignored flags、必須 Markdown 見出し、hidden temp から pending への atomic 保存、
`SessionStart(source=compact)` の same-session rename-first claim、wrong-event / 別 session / 再実行時
no-op、missing transcript と nested Codex failure の exit 0 警告を検証します。実サービスの model
応答品質・認証・network、Codex 本体が
将来も同じ不安定 transcript 形式を出すことは fixture の保証対象外です。

## スコープ外

- **Stop hook による handoff 作成の強制**: 閾値超過時の指示注入のみを行い、Claude が実際に
  ファイルを書いたかどうかまでは検証・強制しません
- **transcript からの使用率算出**: Claude Code の context 使用率はキャッシュ経由だけで取得し、
  Codex 側も transcript から使用率を逆算しません。Codex transcript は handoff 要約の opaque input
  にだけ使います
- **同一セッション内の再警告**: 1 セッション 1 回の通知のみで、閾値を超え続けても再通知は
  行いません

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install session-handoff@natsuume-plugins
```

context 使用率キャッシュの producer が未構成の場合は、続けて setup skill を実行してください。

```
/session-handoff:setup
```

## 機能一覧

### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `detect-context-threshold` | PostToolUse (`*`) | Claude Code の context 使用率が閾値を超えたことを検知し、handoff 作成指示を注入する (1 セッション 1 回) |
| `save-codex-handoff` | PreCompact (`auto\|manual`) | 明示 opt-in 済み Codex repository で transcript 末尾を nested Codex に要約させ、pending handoff を atomic 保存する |
| `inject-pending-handoff` | SessionStart (`clear\|startup\|resume\|compact`) | 直近 24 時間以内の未消費 handoff を runtime 別 source policy で自動注入する (at-most-once) |

### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| setup | Claude: `/session-handoff:setup` / Codex: `$session-handoff:setup` | Claude では context cache producer を構成。Codex では provider/privacy 境界を提示し、明示承認 token 付きで repository opt-in を enable/disable する |

### スクリプト

| ファイル | 用途 |
|---------|------|
| `hooks/scripts/save-codex-handoff.sh` | Codex PreCompact transcript 要約・pending atomic 保存 adapter |
| `scripts/setup-codex-summary.sh` | Codex nested summary opt-in の inspect / token-bound enable / disable helper |
| `scripts/lib/codex-summary-opt-in.sh` | marker 名・exact v1 content・owner/mode/type 判定の共有契約 |
| `scripts/setup-wrapper.sh` | setup skill から呼ばれる設置スクリプト本体 (inspect / install-wrap / install-cache-only / regenerate-launcher) |
| `scripts/context-cache-dump.sh` | natsuume-statusline v0.6.0 の同名ファイルの同梱コピー (`dump_context_cache` 関数)。launcher の dump 処理から解決・source される |

## 関連情報

- [natsuume-statusline プラグイン](../natsuume-statusline/README.md) — context 使用率キャッシュの標準 producer
- [rate-limit プラグイン](../rate-limit/README.md) — 同種の launcher パターン (`INNER_COMMAND_B64`) を採用する姉妹プラグイン

## キーワード

`session-handoff` `context-window` `handoff` `session-start` `statusline` `cache` `hook` `skill`
