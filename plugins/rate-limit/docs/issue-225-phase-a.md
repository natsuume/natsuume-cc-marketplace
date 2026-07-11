# issue #225 Phase A: 設計契約 (Phase B で本ファイルは削除する)

issue #225 の契約を実装に落とすための設計記述。issue body が一次契約であり、本ファイルは
(1) Phase A で実施した実機検証の結果、(2) 検証結果から確定した正規化マッピング、
(3) ファイル間の責務分担、を記録する。

## 1. 実機検証の結果 (2026-07-11、Linux/WSL2、Claude Code v2.1.207)

### 1-1. credentials.json の token フィールド

`~/.claude/.credentials.json` の構造 (キーと型のみ確認、値は取得していない):

- OAuth access token: `.claudeAiOauth.accessToken` (string) — **確定**
- 参考: `.claudeAiOauth.subscriptionType` / `.claudeAiOauth.rateLimitTier` も存在する (本 plugin では未使用)

### 1-2. `GET https://api.anthropic.com/api/oauth/usage` の実レスポンス (redact 済み fixture)

HTTP 200。ヘッダ `Authorization: Bearer <token>` + `anthropic-beta: oauth-2025-04-20` +
`User-Agent: claude-code/<X.Y.Z>` で取得。構造 (値はダミー化):

```json
{
  "five_hour":  { "utilization": 39.0, "resets_at": "2026-07-11T11:10:00.148392+00:00",
                  "limit_dollars": null, "used_dollars": null, "remaining_dollars": null },
  "seven_day":  { "utilization": 8.0,  "resets_at": "2026-07-13T18:00:00.148413+00:00", "...": null },
  "seven_day_opus": null,
  "seven_day_sonnet": null,
  "extra_usage": { "is_enabled": false, "monthly_limit": null, "...": null },
  "limits": [ { "kind": "session", "percent": 39, "...": "..." } ],
  "spend": { "...": "..." },
  "（他にも未知フィールド多数。予告なく増減しうる）": "..."
}
```

確定事項:

- `utilization` は **0–100 スケールの数値** (実測 39.0 = statusline の used_percentage 39% と一致)
- `resets_at` は **ISO 8601 文字列 (+00:00 オフセット、マイクロ秒付き)**。epoch ではない
- 未知フィールドが多数あるため、issue の「extras は allowlist」方針で正規化する

### 1-3. statusline キャッシュ側の `resets_at` 形式

公式 docs は epoch 秒と記載するが、既存 natsuume-statusline の `lib.sh time_remaining()` は
epoch / ISO の両形式を防御的に受けている。本 plugin も同じ防御方針とする (下記 2-1)。

## 2. 正規化マッピング (fetch-rate-limit.sh)

出力 JSON (公開契約、issue #225 の I/O 契約と同一) への正規化:

| 出力フィールド | 経路① (statusline キャッシュ) | 経路② (oauth/usage) |
|---|---|---|
| `source` | `"statusline-cache"` | `"oauth-endpoint"` |
| `fetched_at` | キャッシュの `written_at` (ISO のまま) | 取得時刻 `date -u +%Y-%m-%dT%H:%M:%SZ` |
| `cache_age_seconds` | `now_epoch - written_at_epoch` (0–60 検証済みの値) | (出力しない) |
| `five_hour.used_percentage` | `rate_limits.five_hour.used_percentage` (0–100 数値検証) | `five_hour.utilization` (0–100 数値検証) |
| `five_hour.resets_at` | epoch なら ISO 8601 UTC (`...Z`) に変換、ISO ならそのまま | ISO のままパススルー |
| `seven_day.*` | 同上 | 同上 |
| `extras` | (出力しない) | `seven_day_opus` / `seven_day_sonnet` / `extra_usage` のうち **null でないもののみ**。全て null ならキー自体を省略 |

- 片 window が invalid (used_percentage が 0–100 の数値でない等) → その window は `null`。
  **両方 invalid なら経路失敗** (キャッシュなら経路②へ、経路②なら exit 1)
- `written_at` が parse 不能・未来時刻・60 秒超過 → キャッシュ stale として経路②へ

## 3. ファイル間の責務分担と依存関係

```
fetch-rate-limit.sh ──source──> lib/cache-paths.sh    (キャッシュパス定義)
                    ──source──> lib/portable-time.sh  (GNU/BSD 時刻変換)
                    ──source──> lib/read-oauth-token.sh (経路②のときのみ)
cache-write-wrapper.sh ──source──> lib/cache-paths.sh, lib/portable-time.sh
setup.sh            (launcher 生成。launcher は自己完結 sh で lib に依存しない)
```

- lib は「source される関数定義のみ」で、直接実行しない (実行ビット不要)
- launcher (`~/.claude/rate-limit-statusline-launcher.sh`、setup.sh が生成) は plugin cache が
  GC されても最低限壊れないよう自己完結にする。active version 解決ロジックは
  natsuume-statusline setup.sh (issue #51) の mtime + semver tie-break 方式を移植する
- portable-time.sh の ISO→epoch は **自前生成の固定フォーマット
  (`%Y-%m-%dT%H:%M:%SZ`) のみ対応**すればよい (cache written_at は wrapper が同フォーマットで
  書く)。BSD は `date -u -j -f`、GNU は `date -u -d` を使い、python3 依存は追加しない

## 4. Phase B で作成するファイル (設計は issue #225「各成果物の契約」に従う)

- `skills/status/SKILL.md` / `commands/setup.md` / `README.md` (配送対象 markdown は
  scaffolding 混入防止のため Phase B で新規作成する)
- `.claude-plugin/marketplace.json` エントリ追加 + リポジトリ README のテーブル行・詳細セクション
- 各 sh の実装本体 (Phase A では契約ヘッダ + not-implemented ガードのみ)
- 本ファイル (`docs/issue-225-phase-a.md`) の削除
