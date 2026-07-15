# rate-limit プラグイン

Claude (エージェント自身) が、セッション内でサブスクリプションの usage limit (5 時間セッション枠・週次枠の使用率と reset 時刻) をユーザ操作なしで取得できる `/rate-limit:status` Skill を提供するプラグインです。加えて、codex (OpenAI) の rate limit (週次枠使用率・reset 時刻・plan 種別) を `codex app-server` RPC 経由で取得する `/rate-limit:codex-status` Skill も提供します。こちらは公式ドキュメント化済みの RPC (`account/rateLimits/read`) を使うため、`/rate-limit:status` の経路② のような非公式依存はありません。

## バージョン

v0.2.0

## 取得経路

2 つの経路を①→②の順でフォールバックします。

| 経路 | 取得元 | 性質 |
|---|---|---|
| ① statusline キャッシュ | Claude Code の statusLine に渡される公式データ (`rate_limits` フィールド) を wrapper がキャッシュファイルへ保存したもの | 公式にドキュメント化されたデータのスナップショット。鮮度は `cache_age_seconds` で分かる |
| ② `GET https://api.anthropic.com/api/oauth/usage` | OAuth token 認証で `/usage` 画面と同じ数値を都度取得 | **非公式・undocumented**。呼んだ瞬間の値だが、Anthropic が動作を保証していない |

キャッシュが **60 秒以内** なら経路①を採用し、それより古い・存在しない・不正な場合のみ経路②を試します。両方失敗すると `/rate-limit:status` は exit 1 で失敗理由を報告します。

## setup 手順 (任意)

経路①を使うには、statusline の出力を横取りしてキャッシュに書き出す wrapper を `statusLine.command` に登録する必要があります。

```
/rate-limit:setup
```

このコマンドは安定 launcher `~/.claude/rate-limit-statusline-launcher.sh` を生成し、既存の `statusLine.command` (natsuume-statusline 等) をこの launcher で包みます。既存 statusline の表示は変化しません。

**setup は必須ではありません。** 未 setup の環境でも `/rate-limit:status` は経路② (非公式 OAuth endpoint) 単独で動作します。

## 非公式 API についての免責

経路② (`GET https://api.anthropic.com/api/oauth/usage`) は undocumented なエンドポイントです。Anthropic の公式ドキュメントには記載がなく、関連する issue (anthropics/claude-code#31021, #31637) は Anthropic 自身により invalid / not planned としてクローズされています。**予告なく仕様変更・廃止される可能性があります。** setup 済みで経路①が使える環境では、経路②が動かなくなっても経路①のみで動作は継続します。

## セキュリティ

経路② は `~/.claude/.credentials.json` (macOS では Keychain) に保存されている OAuth access token を読み取ります。

- token の送信先は `https://api.anthropic.com` のみです (リダイレクト追従は行わず、接続先を固定しています)
- token は stdout の応答本文以外 (stderr・ログ・プロセス一覧・一時ファイル) に一切出力しません。curl への受け渡しはコマンドライン引数ではなく `--config` (stdin 経由) で行い、`ps` などのプロセス一覧への露出を防いでいます
- token を扱うスクリプトは冒頭で `set +x` し、shell の xtrace 継承による意図しない出力を防いでいます

## 依存

- `jq` (必須)
- `curl`、`claude` CLI (経路② を使う場合のみ。UA に埋め込む version の抽出に `claude --version` を使用)
- `codex` CLI (`/rate-limit:codex-status` を使う場合のみ。認証は codex app-server 自身が内部処理する)

## macOS について

Keychain からの token 読み出し (`security find-generic-password`) はコミュニティ実装の報告値をもとにしており、**この開発環境 (Linux/WSL2) では実機検証していません。**

## トラブルシュート

取得不可 (`/rate-limit:status` が exit 1) になる代表的な理由:

| 症状 | 原因 |
|---|---|
| 経路①②とも失敗、stderr に credentials 不在の旨 | `/rate-limit:setup` 未実行 (経路① のキャッシュが無い) かつ `~/.claude/.credentials.json` も無い (API key 認証環境などサブスクリプションでない環境) |
| stderr に 429 の旨 | 経路② がレート制限に達した。リトライはしない設計のため、時間を置いて再実行する |
| stderr に認証切れの旨 (401/403) | OAuth token が失効している。再ログインが必要 |

### plugin アンインストール後の launcher の挙動

`/rate-limit:setup` 実行後に本 plugin をアンインストールしても、launcher
(`~/.claude/rate-limit-statusline-launcher.sh`) と `statusLine.command` の設定は残ります。
この状態でも launcher は元の statusline コマンドへ直接委譲し続けるため、既存の statusline
表示は壊れません (キャッシュの更新だけが止まります)。設定を完全に元へ戻すには、setup 時に
作成されたバックアップ (`~/.claude/settings.rate-limit-backup.*.json`) から `settings.json`
を復元するか、`statusLine.command` を手動で元のコマンドに書き戻して launcher を削除して
ください。

## 機能一覧

### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| status | `/rate-limit:status` | `scripts/fetch-rate-limit.sh` を実行し、5h/週次の使用率と reset 時刻を報告する |
| codex-status | `/rate-limit:codex-status` | `scripts/codex-rate-limit.sh` を実行し、codex app-server RPC で codex の rate limit (週次枠使用率・reset 時刻・plan 種別) を報告する。`--max-used-percent <N>` で閾値判定 (exit 0/1/2) |

### Commands

| コマンド | 説明 |
|---------|------|
| `/rate-limit:setup` | statusline キャッシュ連携 (安定 launcher) を `~/.claude/settings.json` に登録する |

### スクリプト

| ファイル | 用途 |
|---------|------|
| `scripts/fetch-rate-limit.sh` | 経路①→②のフォールバック取得本体 |
| `scripts/codex-rate-limit.sh` | codex app-server RPC (`account/rateLimits/read`) による codex の rate limit 取得本体 |
| `scripts/setup.sh` | 安定 launcher の設置と `statusLine` 設定の書き換え |
| `scripts/lib/cache-paths.sh` | キャッシュファイルパスの単一定義 |
| `scripts/lib/portable-time.sh` | epoch↔ISO 8601 UTC 変換・mtime 取得の GNU/BSD 両対応 helper |
| `scripts/lib/read-oauth-token.sh` | `.credentials.json` / macOS Keychain からの token 読み出し |
| `statusline/cache-write-wrapper.sh` | statusLine 入力 JSON をキャッシュに書き、内側 statusline へ委譲する wrapper (launcher から呼ばれる) |

## 関連情報

- [Claude Code Status Line ドキュメント](https://code.claude.com/docs/en/statusline)

## キーワード

`rate-limit` `usage-limit` `statusline` `oauth` `skill` `codex`
