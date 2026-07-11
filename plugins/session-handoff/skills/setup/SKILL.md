---
name: setup
description: session-handoff plugin の context cache producer (natsuume-statusline または安定 launcher) を ~/.claude/settings.json に構成する
user-invocable: true
when_to_use: |
  ユーザーが以下のようなリクエストをした場合に使用:
  - 「session-handoff の setup をして」「/session-handoff:setup」
  - 「handoff の cache 連携を設定して」「context cache の producer を用意して」
---

# /session-handoff:setup — cache producer の構成

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
