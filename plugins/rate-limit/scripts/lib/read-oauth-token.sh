#!/bin/bash
# read-oauth-token.sh — OAuth access token の読み出し (source して使う。直接実行しない)
#
# 提供する関数 (I/O 契約):
#   read_oauth_token : token を stdout に 1 行で返す。取得不能なら非ゼロ exit +
#                      stderr に理由 (token そのものは stderr・ログ・一時ファイルに書かない)
#
# 探索順 (issue #225 の契約):
#   1. ~/.claude/.credentials.json が存在すれば .claudeAiOauth.accessToken を読む
#      (フィールド名は 2026-07-11 に実機検証済み。docs/issue-225-phase-a.md 参照)
#   2. 無ければ macOS (uname -s = Darwin) に限り Keychain を試す:
#      security find-generic-password -s "Claude Code-credentials" -w
#      (サービス名はコミュニティ実装の報告値。実機未検証である旨を README に明記)
#   3. いずれも不可なら非ゼロ exit
#
# token 検証 (curl config injection 防止): 非空かつ改行・引用符・空白を含まない 1 行で
# あることを検証し、不一致なら失敗させる。
#
# セキュリティ契約: 冒頭で set +x (xtrace 継承対策)。token を echo デバッグしない。
# 呼び出し側は token を curl 引数に直接置かず --config (stdin) で渡す (ps 露出防止)。

echo "[rate-limit] not implemented (issue #225 Phase B)" >&2
return 1 2>/dev/null || exit 1
