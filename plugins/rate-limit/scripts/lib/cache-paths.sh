#!/bin/bash
# cache-paths.sh — キャッシュファイルパスの単一定義 (source して使う。直接実行しない)
#
# fetch-rate-limit.sh (読み手) と statusline/cache-write-wrapper.sh (書き手) が
# 同じパスを参照するための共有定義。パスをここ以外に書かない。
#
# 提供する定義 (I/O 契約):
#   RATE_LIMIT_CACHE_DIR  = ${XDG_CACHE_HOME:-$HOME/.cache}/natsuume-rate-limit
#   RATE_LIMIT_CACHE_FILE = $RATE_LIMIT_CACHE_DIR/rate_limits.json
#
# キャッシュファイルの内容 (書き手が保証する形式):
#   { "written_at": "<ISO 8601 UTC (%Y-%m-%dT%H:%M:%SZ)>",
#     "rate_limits": <statusLine 入力 JSON の .rate_limits をそのまま> }
#
# rate limit はアカウント単位のグローバル値のため、セッション別・プロジェクト別に
# 分割しない。ディレクトリ・ファイルは所有者のみ読み書き可 (umask 077) で作成する。

echo "[rate-limit] not implemented (issue #225 Phase B)" >&2
return 1 2>/dev/null || exit 1
