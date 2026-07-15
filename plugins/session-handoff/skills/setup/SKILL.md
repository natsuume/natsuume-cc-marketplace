---
name: setup
description: session-handoff の producer を runtime 別に確認・構成する。Claude Code の context cache 連携、Codex の provider/privacy 明示承認付き PreCompact summary opt-in、handoff setup・compaction timing の確認で使う
---

# session-handoff setup — runtime 別 producer の構成

## Codex runtime

Codex では statusline cache を構成しない。PreCompact adapter は **privacy-safe default として無効**
であり、repository ごとの git-dir に安全な opt-in marker がある場合だけ次の経路を有効にする。

1. `PreCompact(auto|manual)` の `save-codex-handoff.sh` が transcript 末尾を read-only・ephemeral・
   hooks/user-config/rules 無効の別 Codex process に渡して Markdown handoff を atomic 保存する
2. 直後の `SessionStart(source=compact|resume)` は同じ session_id の Codex pending だけを、
   `clear|startup` は既存 pending 全般を `inject-pending-handoff.sh` で at-most-once 注入する

### 1. Plugin root と対象 repository を確定する

この `SKILL.md` の実パスから `skills/setup/` の 2 階層上を `<plugin-root>` とする。対象 repository は
現在の作業 repository とし、曖昧なら変更せず確認する。Claude 用の `setup-wrapper.sh`、
`~/.claude/settings.json`、statusline launcher は操作しない。

### 2. 必ず read-only inspect から始める

```bash
bash "<plugin-root>/scripts/setup-codex-summary.sh" inspect --repo "<repo>"
```

`target`、`state`、`protocol`、`exact-content`、`privacy-boundary`、action ごとの plan token を利用者へ
そのまま提示する。state は `disabled` / `enabled` / `different-*` / `unsafe-*` のいずれかである。
`unsafe-*` (symlink、非通常ファイル、非 owner directory/file) は自動修復せず停止する。inspect は
marker や directory を作成・変更しない。

### 3. Provider/privacy 境界を説明して明示承認を得る

nested process は再帰・project config/MCP の影響を避けるため `codex exec --ignore-user-config` を使う。
そのため親 session が Azure、Bedrock、local/custom provider、別 account/data-residency 設定で動いて
いても、transcript excerpt が nested Codex の **default provider/account** へ送信される可能性がある。
追加 usage、最大180秒の待ち時間、transcript tail、LLM summary の品質差も説明する。

enable を希望する場合は、inspect が示した repository 固有 target に exact v1 marker (mode 0600) を
作成してよいか、上記 provider 越境を含めて明示的な承認を求める。曖昧な返答、一般的な setup 依頼、
過去の別 repository への承認を同意として扱わない。承認前に marker を直接作ったり enable を実行
してはならない。

### 4. 承認した action token だけを適用する

enable の承認後:

```bash
bash "<plugin-root>/scripts/setup-codex-summary.sh" enable \
  --repo "<repo>" \
  --plan-token "<approved-enable-plan-token>"
```

disable の依頼時も inspect → target/state 提示 → marker 削除の明示承認を経て、次を実行する。

```bash
bash "<plugin-root>/scripts/setup-codex-summary.sh" disable \
  --repo "<repo>" \
  --plan-token "<approved-disable-plan-token>"
```

token mismatch は inspect 後に marker state が変わったことを示す。再 inspect、再提示、再承認を行い、
古い token を再利用しない。helper は symlink / 非通常 / 非 owner marker を変更せず、enable は同じ
directory の mode 0600 temp file から atomic rename する。marker を手作業で代替しない。

### 5. Hook trust と残る保証差を報告する

enable 後は `/hooks` で本 plugin の PreCompact / SessionStart hook が表示され current hash が trust
済みか確認するよう案内する。plugin hook は trust されるまで skip される。disable 後も既存 pending
の SessionStart 注入は残るが、新しい nested summary は生成されない。

Codex hook input から現在の context 使用率は取得できないため、Claude Code と同じ厳密な60% trigger
ではなく compaction 直前に発火する。`model_auto_compact_token_limit` は auto compaction を absolute
token 数で早める任意設定だが、次の理由から本 Skill は自動設定しない。

- handoff だけでなく session 全体の compaction 時期を変える
- 60% という割合ではなく absolute token 数であり、model の context window 切替へ自動追随しない
- user / project のどの `.codex/config.toml` layer を変更するかで影響範囲が変わる

利用者が scope、具体的 token 値、compaction への影響を理解したうえで明示的に変更を依頼した場合
だけ、対象 config をバックアップしてから設定する。値を推測したり「60%相当」と称して自動記入
してはならない。

## Claude Code runtime

session-handoff plugin の hook (`detect-context-threshold.sh`) は、natsuume-statusline (v0.6.0+)
が書き出す context 使用率キャッシュ (`${TMPDIR:-/tmp}/natsuume-context-cache-<uid>/<session_id>.json`)
を advisory 用途で読む consumer です。本 skill は、このキャッシュが書き出される状態を用意します。

**必須ではありません。** 未実行でも hook 自体はエラーにならず動作しますが、cache が無い/古い間は
閾値検知 (`SESSION_HANDOFF_THRESHOLD` 超過時の handoff 作成促進) が発火しません。

## 1. 現在の状態を確認する

まず `${CLAUDE_PLUGIN_ROOT}/scripts/setup-wrapper.sh inspect` を実行し、出力 JSON を読む。**この
skill 内での書き換えは、すべてこの inspect 結果に基づいて行う。書き換えを実施する前に、現在の
`statusline_command` の値を必ずユーザに報告すること。**

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/setup-wrapper.sh" inspect
```

出力フィールドの意味:

| フィールド | 意味 |
|---|---|
| `statusline_configured` | `~/.claude/settings.json` の `statusLine.command` が設定済みか |
| `statusline_command` | 現在の command 文字列 (未設定なら空文字列) |
| `classification` | `none` / `self-launcher` / `natsuume-statusline` / `other` のいずれか |
| `cache_producer_active_guess` | 直近 120 秒以内に更新された cache entry が存在するかの近似判定。**正確な「現セッションの cache」一致ではない** (session_id は本 skill の実行環境からは取得できないため、producer が「何かしら最近稼働しているらしい」ことのベストエフォート推測に留まる) |
| `natsuume_statusline_cache_capable` | `classification` が `natsuume-statusline` のときのみ意味を持つ: `"true"` / `"false"` / `"unknown"` (0.6.0 で追加された `context-cache-dump.sh` の有無で判定) |
| `natsuume_statusline_detail` | 上記の判定根拠 (解決したパス、または判定不能の理由) |
| `chain_status` | `classification` が `other` のときのみ意味を持つ: `"clear"` / `"detected"` / `"ambiguous"` (1 段の連鎖検査の結果) |
| `chain_detail` | 上記の判定根拠 |

## 2. 分岐

`inspect` の結果に応じて次の優先順位で分岐する (該当した時点でそれ以降は評価しない)。

### 2.1 `cache_producer_active_guess` が `true`

producer は既に稼働していると推測されるので、構成済みとして報告して終了する (書き換え不要)。

### 2.2 `classification` が `natsuume-statusline`

- `natsuume_statusline_cache_capable` が `"true"`: 構成済みとして報告して終了する
- `"false"`: natsuume-statusline は導入済みだが 0.6.0 未満の可能性がある。`natsuume_statusline_detail`
  の内容とともに、`natsuume-statusline@natsuume-plugins` の update (`/plugin update` またはマーケット
  プレイス経由) を案内し、変更は行わず終了する
- `"unknown"`: 判定不能だった旨と `natsuume_statusline_detail` を報告する。必要なら Read/Bash で
  `statusline_command` が指すファイルを直接確認してよいが、`setup-wrapper.sh` による自動書き換えは
  行わず、現況をユーザに報告して終了する

### 2.3 `classification` が `self-launcher`

既に本 plugin の安定 launcher (`~/.claude/session-handoff-statusline-launcher.sh`) が登録されている。
**構成済みとして扱い、再ラップしない** (自己再帰・元 command 喪失の防止)。

launcher 本体の再生成が必要な場合 (dump 解決ロジックの更新を反映したい等) のみ、以下を実行する。

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/setup-wrapper.sh" regenerate-launcher
```

このコマンドは既存 launcher 内の `WRAPPED_COMMAND_B64` 代入行から内側コマンドを一意抽出・検証して
から launcher 本体だけを最新化する (settings.json は変更しない)。**抽出・検証に失敗した場合は非 0
終了し、launcher / settings.json のいずれも変更しない** — その場合は失敗メッセージ (stderr) をその
ままユーザに報告して終了する (自己判断でリカバリを試みない)。

### 2.4 `classification` が `other` (他の statusline が設定済み)

`chain_status` で分岐する。

| `chain_status` | 対応 |
|---|---|
| `"detected"` | 既に (別 plugin の launcher 経由等で) ラップ済みと判断し、**再ラップしない**。`chain_detail` をそのままユーザに報告して終了する |
| `"ambiguous"` | 解析・decode に失敗し、かつ自 launcher が既存のため循環リスクがある。**launcher / settings.json とも変更せず**、`chain_detail` を曖昧な連鎖としてユーザに報告して終了する |
| `"clear"` | 連鎖は検出されなかった。現在の `statusline_command` を提示したうえで、`AskUserQuestion` でラップ実行の確認を取る |

`"clear"` かつユーザが承認した場合のみ、以下を実行する。既存の `statusLine.command` を安定 launcher
で包み、表示は変えずに cache dump を追加する。

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/setup-wrapper.sh" install-wrap
```

出力 JSON の `backup` (settings.json のバックアップパス) と `launcher` (設置した launcher パス) を
ユーザに報告する。

### 2.5 `classification` が `none` (statusline 未設定)

`AskUserQuestion` で以下の 2 択を確認する。

- **natsuume-statusline を導入する**: 表示付きの高機能な statusline も併せて使いたい場合。
  `claude plugin install natsuume-statusline@natsuume-plugins` のインストールと、その後の
  `/natsuume-statusline:setup` の実行をユーザに案内する (natsuume-statusline 自体の導入は本 plugin
  のスコープ外のため、本 skill はこの経路を自動実行しない)
- **cache-only launcher を登録する**: 表示は増やさず、cache dump だけを行う launcher を登録したい
  場合。以下を実行する

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/setup-wrapper.sh" install-cache-only
```

出力 JSON の `backup` と `launcher` をユーザに報告する。

## 3. 失敗時

`setup-wrapper.sh` は jq 依存であり、jq 不在時は非 0 終了してその旨を stderr に出す。settings.json
が壊れている (jq parse 失敗) 場合も変更を行わず非 0 終了する。いずれもエラーメッセージをそのまま
ユーザに報告し、自己判断で修復を試みない。

## 4. 元に戻す

`install-wrap` / `install-cache-only` 実行時に作られたバックアップ
(`~/.claude/settings.session-handoff-backup.<timestamp>.json`) で `settings.json` を上書きすれば
元に戻る。

```bash
cp ~/.claude/settings.session-handoff-backup.<timestamp>.json ~/.claude/settings.json
```
