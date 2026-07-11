#!/bin/bash
# weekly-scoped-limits.sh — per-model 週次レートリミット (例: Fable) のデータ供給
#
# Claude Code が statusline stdin に渡す rate_limits には five_hour / seven_day しか
# 無く (v2.1.207 の実 stdin dump で確認)、Fable 等のモデル別週次枠は含まれない。
# 一方 OAuth usage API (https://api.anthropic.com/api/oauth/usage) の limits[] には
# {kind:"weekly_scoped", percent, resets_at, scope.model.display_name} が返ってくる
# (2026-07-12 実測)。本ファイルはこの API を TTL 付き file cache + background fetch で
# 取得し、3 行目のレンダラへ表示データを供給する。
#
# ============================================================================
# Phase A 設計契約 (Phase B で実装する)
# ============================================================================
#
# ■ データ優先順位 (main.sh 側の配線契約)
#   1. stdin の rate_limits.model_scoped[] — Claude Code バイナリに schema が存在する
#      公式経路 (現行未配線)。emit され始めたらこちらを優先し、本ファイルの cache
#      経路は読まず background fetch も起動しない。
#   2. 本ファイルの cache (OAuth usage API 由来)。
#
# ■ cache ファイル
#   パス: ${XDG_CACHE_HOME:-$HOME/.cache}/natsuume-statusline/weekly-scoped.json
#   権限: ディレクトリ・ファイルとも所有者のみ (umask 077)。
#   書き込み: 同一ディレクトリの mktemp + mv による atomic write のみ。
#   schema (書き手 = 本ファイルの fetch worker):
#     {
#       "fetched_at": <最後に成功した fetch の epoch 秒。成功前は 0>,
#       "consecutive_failures": <連続失敗回数。成功で 0 にリセット>,
#       "next_attempt_at": <この epoch 秒より前は再 fetch しない>,
#       "weekly_scoped": [
#         {"display_name": "<モデル名 (例: Fable)>",
#          "percent": <使用率 0-100 の数値>,
#          "resets_at": "<ISO 8601 文字列 (API の値をそのまま保存)>"}
#       ]
#     }
#   失敗時も weekly_scoped は前回成功値を保持したまま counters のみ更新する
#   (レートリミットは変化が緩やかで、古い値でも非表示より情報価値がある)。
#
# ■ 提供する関数 (main.sh から source して使う)
#
# read_weekly_scoped_entries
#   cache を読み、表示可能な entry を 1 行 1 entry の TSV で stdout に返す:
#     <display_name>\t<percent>\t<resets_at>
#   fail-open: cache 不在 / jq parse 不能 / weekly_scoped 空・欠落 / percent が
#   数値でない entry はスキップし、出力可能なものが無ければ空出力 (exit 0)。
#
# kick_weekly_scoped_refresh
#   全表示出力の後に main.sh が呼ぶ (表示への不干渉は context-cache-dump.sh と
#   同じ配置契約)。以下すべてを満たすときのみ background worker を起動する:
#     - curl と jq が存在する
#     - now >= next_attempt_at (cache 不在時は常に満たす)
#     - now - fetched_at > TTL (300 秒)
#     - mkdir lock を取得できた
#   worker は `bash <本ファイル> --fetch-worker` を `</dev/null >/dev/null 2>&1 &`
#   で切り離して起動し、statusline のレンダリングを一切ブロックしない。
#
# ■ fetch worker (直接実行モード: `bash weekly-scoped-limits.sh --fetch-worker`)
#   1. lock 下で TTL / next_attempt_at を再確認する (kick 判定との TOCTOU 対策)
#   2. OAuth token を読む:
#      - ~/.claude/.credentials.json の .claudeAiOauth.accessToken
#      - 無ければ macOS (Darwin) に限り Keychain
#        (security find-generic-password -s "Claude Code-credentials" -w)
#      - token は argv / ログ / stderr / 一時ファイルに書かない。冒頭 set +x。
#        形式検証 (改行・引用符・空白を含まない 1 行) を通らなければ失敗扱い。
#   3. curl --disable --config - (stdin 経由で Authorization header を渡す。
#      ps 露出防止) --max-time 10 で GET https://api.anthropic.com/api/oauth/usage
#      header: anthropic-beta: oauth-2025-04-20 / User-Agent: claude-code/<version>
#      (<version> は `claude --version` から抽出。取得不能なら固定文字列にせず失敗扱い)
#   4. 応答の limits[] から kind == "weekly_scoped" かつ scope.model.display_name が
#      非空文字列かつ percent が数値の entry のみ抽出して cache に atomic write。
#      抽出結果 0 件は「成功・空配列」として保存する (常に空なら 3 行目は 7d のみ)。
#   5. 失敗時 (token 不能 / curl 失敗 / 非 200 / JSON 不能):
#      consecutive_failures += 1、
#      next_attempt_at = now + min(60 * 2^(consecutive_failures - 1), 1800)
#      成功時: consecutive_failures = 0、next_attempt_at = now + TTL。
#   6. 終了時に必ず lock を解放する (trap)。
#
# ■ lock
#   パス: <cache_dir>/.fetch.lock (mkdir による排他)。
#   stale 判定: lock dir の mtime が 120 秒より古ければ奪取してよい
#   (curl の 10 秒 timeout に対して十分長い)。
#
# ■ 依存と縮退
#   jq: 必須 (プラグイン全体の必須依存)。curl: optional — 無ければ fetch せず
#   cache 供給のみ (fail-open)。python3: 不要 (ISO 8601 の解釈は表示側の
#   lib.sh time_remaining が担う)。
#   いかなる失敗でも stdout / stderr に出力しない (statusline を汚さない)。

# 実装は Phase B で行う。
