---
name: codex-status
description: Codex (OpenAI) の rate limit (週次枠等の使用率・reset 時刻・plan 種別) を取得する。「Codex の rate limit・残量・使用率・週次枠」確認や、Codex への委任前の使用率ガード判定で使う
---

# /rate-limit:codex-status — codex の rate limit 取得

`scripts/codex-rate-limit.sh` を実行し、出力された JSON をユーザに報告する。データは `codex app-server` (stdio JSON-RPC) の `account/rateLimits/read` から取得する (公式ドキュメント化済みの RPC。`/rate-limit:status` の経路② のような非公式依存は無い)。

この `SKILL.md` を含む `skills/codex-status/` の 2 階層上を `<plugin-root>` として解決する。通常の Skill 実行では hook 用の `${CLAUDE_PLUGIN_ROOT}` が設定される保証はないため、実パスを優先する。

## 1. 実行

Bash ツールで以下を **foreground で 1 回**実行する (script 内部に 30 秒の応答 timeout があるため、Bash の timeout は既定で足りる)。

```bash
bash "<plugin-root>/scripts/codex-rate-limit.sh"
```

呼び出し側で閾値判定が必要な場合 (委任可否ガード等) は `--max-used-percent <N>` (N は 0〜100 の整数) を付ける。

## 2. 出力 JSON の読み方

exit 0 (および exit 2) のとき、stdout に `account/rateLimits/read` 応答の `result` がそのまま返る。

| フィールド | 意味 |
|---|---|
| `rateLimits` | 本体枠 (limitId `codex`) をミラーする単一バケットビュー |
| `rateLimits.planType` | ChatGPT plan 種別 (`pro` / `plus` / `team` 等) |
| `rateLimits.rateLimitReachedType` | 到達済みなら非 null (`rate_limit_reached` 等)、未到達なら null |
| `rateLimits.primary.usedPercent` | 使用率 (0〜100 の数値) |
| `rateLimits.primary.windowDurationMins` | 枠の窓幅 (分)。10080 = 週次 |
| `rateLimits.primary.resetsAt` | reset 時刻 (**epoch 秒**。ISO 変換はしていない) |
| `rateLimits.secondary` | 第 2 の枠 (plan によっては存在。無ければ欠損 / null)。構造は `primary` と同じ |
| `rateLimitsByLimitId` | limitId 別の全枠。本体枠 `codex` のほか、独立枠 (例: `codex_bengalfox` = GPT-5.3-Codex-Spark) を含む |
| `rateLimitResetCredits` | リセットクレジット一覧 (無ければ null) |

ユーザへ報告するときは、`rateLimits.primary` (および存在すれば `secondary`) の `usedPercent` と reset 時刻 (`resetsAt` を人間可読に直す)、`planType`、および `rateLimitsByLimitId` にある独立枠の使用率を報告する。

## 3. exit code 契約

| exit | 意味 | stdout |
|---|---|---|
| 0 | 正常 (`--max-used-percent` の判定 OK を含む) | JSON あり |
| 1 | 取得失敗・引数不正 (codex CLI 不在 / 未認証等の RPC エラー / 30 秒 timeout / 応答不正 (secondary 窓が存在するのに usedPercent 不正を含む) / N の validation 違反) | 保証なし |
| 2 | `--max-used-percent` 指定時のみ: `primary` / `secondary` (存在する場合) いずれかの `usedPercent` が N 超、または `rateLimitReachedType` が非 null (到達済み) | JSON あり |

呼び出し側は 1 (取得失敗 → fail-closed 判断) と 2 (超過) を区別できる。

## 4. 失敗時 (exit 1)

stderr に失敗理由が人間可読で出力される。その内容をそのままユーザに報告する。代表的な原因:

| 症状 | 原因と案内 |
|---|---|
| codex CLI が見つからない旨 | codex CLI 未インストール。インストールを案内する |
| RPC がエラー応答を返した旨 | codex CLI 未認証の可能性。`codex login` の実行を案内する |
| 30 秒以内に応答しない旨 | ネットワーク不調等。時間を置いて再実行する |
