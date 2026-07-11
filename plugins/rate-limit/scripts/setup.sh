#!/bin/bash
# setup.sh — 安定 launcher の設置と settings.json への登録 (/rate-limit:setup から実行)
#
# natsuume-statusline の scripts/setup.sh (issue #51) の launcher パターンを踏襲する:
# statusLine.command には plugin update で消える version 固有 cache パスを焼き込めないため、
# ~/.claude/rate-limit-statusline-launcher.sh (安定パス) を生成して登録する。
#
# 処理手順 (順序が契約。launcher 設置が settings 更新より先):
#   1. ~/.claude/settings.json をタイムスタンプ付きでバックアップ (同秒衝突は連番回避)
#   2. 既存の statusLine.command 文字列を読み取る (statusLine 未設定なら空)
#   3. launcher を atomic 設置 (umask 077、同一ディレクトリ mktemp + mv)。launcher には
#      (a) version dir の親から active version の cache-write-wrapper.sh を解決するロジック
#          (natsuume-statusline wrapper の mtime + semver tie-break 方式を移植。自己完結 sh)
#      (b) 既存 statusLine.command 文字列を single-quote エスケープで verbatim に埋め込み、
#          wrapper の第 1 引数として渡す
#   4. statusLine を { "type": "command", "command": "bash '<launcher パス>'" } に atomic 更新
#      (更新前後で jq validate)
#
# 境界の挙動 (issue #225):
#   - statusLine.command が既に launcher 絶対パスを指す (exact 判定) → 二重 wrap しない
#     (launcher 本体の最新内容での再生成は行ってよい)
#   - statusLine 未設定 → 内側コマンド無しの launcher を設置
#   - settings.json 不在・空 → {} から開始 / parse 不能 → 変更せずエラー終了
#   - plugin が cache 配下でない安定パスにあっても launcher 方式で統一する
#     (内側コマンドの埋め込みが必要なため direct 分岐は持たない)
#
# 依存: jq (必須)。

set -euo pipefail

echo "[rate-limit] not implemented (issue #225 Phase B)" >&2
exit 1
