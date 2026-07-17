---
name: status
description: Claude Code のサブスクリプション usage limit (5 時間セッション枠・週次枠の使用率と reset 時刻) を取得する。「rate limit」「残量」「usage limit」「5時間・週次枠の残りや使用率」を確認したいときに使う
---

# /rate-limit:status — サブスク usage limit の取得

`scripts/fetch-rate-limit.sh` を実行し、出力された JSON をユーザに報告する。

この `SKILL.md` を含む `skills/status/` の 2 階層上を `<plugin-root>` として解決する。通常の Skill 実行では hook 用の `${CLAUDE_PLUGIN_ROOT}` が設定される保証はないため、実パスを優先する。

## 1. 実行

Bash ツールで以下を **foreground で 1 回**実行する。リトライはしない (非公式 endpoint への呼び出しをスクリプト内部で 1 回に制限しているため)。

```bash
bash "<plugin-root>/scripts/fetch-rate-limit.sh"
```

## 2. 出力 JSON の読み方

exit 0 のとき、stdout に次の JSON が返る。

| フィールド | 意味 |
|---|---|
| `source` | データの取得経路。`"statusline-cache"` または `"oauth-endpoint"` (下記参照) |
| `fetched_at` | データ取得時刻 (ISO 8601 UTC)。`source` が `"statusline-cache"` のときはキャッシュ書き込み時刻 |
| `cache_age_seconds` | `source` が `"statusline-cache"` のときのみ出現。キャッシュ書き込みからの経過秒 |
| `five_hour` | 5 時間セッション枠。`{ "used_percentage": <0-100>, "resets_at": "<ISO 8601 UTC>" }`、取得できなければ `null` |
| `seven_day` | 週次枠。`five_hour` と同じ形式、または `null` |
| `extras` | `source` が `"oauth-endpoint"` のときのみ出現しうる任意フィールド (endpoint 固有の追加情報)。`limits` を含みうる (下記参照) |

`source` の値でデータの由来・信頼性が異なる:

- `"statusline-cache"`: Claude Code の statusLine に渡される公式データのスナップショット。`cache_age_seconds` がそのデータの鮮度 (何秒前の値か) を示す
- `"oauth-endpoint"`: 非公式 API を呼び出した瞬間の値。都度取得のため鮮度の概念はないが、公式にドキュメント化された経路ではない

`used_percentage` は 0〜100 の数値、`resets_at` は ISO 8601 UTC 形式の時刻。

### `extras.limits` — model-scoped limit を含む詳細枠

`extras.limits` は endpoint の `limits` 配列の raw passthrough (script は正規化・検証しない)。各 entry は次のフィールドを持ちうる:

| フィールド | 意味 |
|---|---|
| `kind` | 枠の種類 (実測例: `"session"` = 5 時間枠、`"weekly_all"` = 週次・全体、`"weekly_scoped"` = 週次・scope 限定) |
| `group` | 枠のグループ (実測例: `"session"` / `"weekly"`) |
| `percent` | 使用率 (0〜100) |
| `severity` | 逼迫度 (実測例: `"normal"` / `"warning"`) |
| `resets_at` | リセット時刻 (ISO 8601) |
| `scope` | 枠の適用範囲。`scope.model.display_name` が対象 model 名 (例: `"Fable"`)。`null` なら全体枠 |
| `is_active` | その枠が現在アクティブに効いているか |

## 3. ユーザへの報告

ユーザへは、取得できた `five_hour` / `seven_day` の `used_percentage` と `resets_at`、および `source` (データの由来) をそのまま報告する。

`extras.limits` が存在する場合は各 entry (`kind`、`scope.model.display_name`、`percent`、`severity`、`resets_at`、`is_active`) も報告に含め、`severity` が `normal` 以外、または `is_active` が true の entry を明示的に強調する。`extras.limits` が空配列の場合は「scoped limit なし」と報告する。entry に存在しないフィールドは推測で補わない。

## 4. 失敗時 (exit 1)

stderr に経路ごとの失敗理由が列挙される。その内容をそのままユーザに報告する。

キャッシュ経路 (`statusline-cache`) が一度も成功しない環境では、`/rate-limit:setup` の実行を案内する。
