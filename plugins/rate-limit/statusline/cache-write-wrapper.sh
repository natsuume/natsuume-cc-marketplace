#!/bin/bash
# cache-write-wrapper.sh — statusLine 入力 JSON のキャッシュ書き出し + 内側 statusline への委譲
#
# setup.sh が生成する launcher (~/.claude/rate-limit-statusline-launcher.sh) から呼ばれる。
# 直接 statusLine.command に登録しない (plugin update で消える version 固有パスのため。
# issue #51 / Claude Code bug #52079)。
#
# I/O 契約 (issue #225):
#   stdin  : Claude Code が statusLine に渡す JSON (全体を一度メモリに読み込む)
#   引数   : $1 = 内側 statusline コマンド文字列 1 引数 (省略可)。bash -c "$1" で実行し、
#            pipeline・引用符・環境変数参照を含む shell 文字列でも元の意味を保つ
#   stdout : 内側コマンドの出力を素通し (内側未指定なら空出力)
#   exit   : 内側コマンドの exit code をそのまま返す (内側未指定なら 0)
#
# キャッシュ書き込み:
#   - stdin JSON に .rate_limits があるときのみ、lib/cache-paths.sh のパスへ
#     { written_at, rate_limits } を atomic write (umask 077、同一ディレクトリ temp + mv)
#   - .rate_limits が無い tick (セッション最初の API 応答前、API key 認証環境等) は
#     キャッシュを上書きしない (既存キャッシュ保持)
#   - 書き込み失敗 (ディレクトリ作成不可等) は無音でスキップし、内側への委譲は続行する
#     (statusline 表示を壊さないことを最優先)

set +x

echo "[rate-limit] not implemented (issue #225 Phase B)" >&2
exit 1
