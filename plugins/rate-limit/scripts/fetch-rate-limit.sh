#!/bin/bash
# fetch-rate-limit.sh — サブスク usage limit の取得本体 (①→② フォールバック)
#
# /rate-limit:status skill が foreground で 1 回実行する。引数・stdin なし。
#
# 処理 (issue #225 の契約):
#   経路① statusline キャッシュ (lib/cache-paths.sh のパス) を読み、valid かつ 60 秒以内なら採用
#   経路② ①が使えないときのみ GET https://api.anthropic.com/api/oauth/usage
#   両方失敗 → exit 1 (stderr に経路ごとの失敗理由を必ず列挙)
#
# キャッシュ valid の定義 (すべて満たす。欠ければ stale として経路②へ):
#   - JSON parse 可能で written_at を持つ
#   - written_at が過去、かつ経過秒が 0〜60 (未来時刻は不正扱い)
#   - five_hour / seven_day の少なくとも一方の used_percentage が 0〜100 の数値
#
# 経路② の HTTP 契約:
#   - Authorization: Bearer <token> / anthropic-beta: oauth-2025-04-20 /
#     User-Agent: claude-code/<claude --version から抽出した X.Y.Z>
#   - version 抽出不能・claude CLI 不在 → 経路②を試行せず失敗扱い (429 バケット回避)
#   - curl --max-time 10、リトライなし (実行 1 回につき呼び出し最大 1 回)、-L 禁止、
#     接続先 https://api.anthropic.com 固定、token は --config (stdin) 渡し
#   - レスポンスの期待フィールド欠落・値域外 → 不正値を黙って返さず経路②失敗として扱う
#
# 出力 (stdout、公開契約。正規化マッピングの詳細は docs/issue-225-phase-a.md):
#   { "source": "statusline-cache" | "oauth-endpoint",
#     "fetched_at": "<ISO 8601 UTC>",
#     "cache_age_seconds": <①のみ>,
#     "five_hour": { "used_percentage": <0-100>, "resets_at": "<ISO 8601 UTC>" } | null,
#     "seven_day": 同上 | null,
#     "extras": <②のみ任意。seven_day_opus / seven_day_sonnet / extra_usage の非 null のみ> }
#
# 終了コード: 0 = いずれかの経路で取得成功 / 1 = 全経路失敗
# 依存: jq (必須)、curl・claude CLI (経路②のみ)。jq 不在は明示エラーで exit 1。

set +x

echo "[rate-limit] not implemented (issue #225 Phase B)" >&2
exit 1
