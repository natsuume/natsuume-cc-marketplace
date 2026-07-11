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

set +x

read_oauth_token() {
  local creds_file token newline_count keychain_payload

  creds_file="$HOME/.claude/.credentials.json"
  token=""

  # 経路1: ~/.claude/.credentials.json (jq が無いと読めないが、呼び出し元の
  # fetch-rate-limit.sh は jq 不在を先頭で弾く契約なのでここでは静かに次へフォールバックする)
  if [ -f "$creds_file" ] && command -v jq >/dev/null 2>&1; then
    token=$(jq -r '.claudeAiOauth.accessToken // empty' "$creds_file" 2>/dev/null)
  fi

  # 経路2: macOS Keychain (Darwin のみ。サービス名は実機未検証、README に明記)。
  # Keychain の password は credentials JSON オブジェクトそのもの (ファイル経路と同形式)。
  # JSON として parse できる場合はファイル経路と同じ schema (claudeAiOauth.accessToken が
  # string) を要求し、欠落・型不正は取得失敗とする ({} や true 等の「引用符も空白も
  # 含まない不正 JSON」を生 token として通さない)。parse 不能な場合のみ、生 token
  # 形式で保存されている可能性に備えて payload 自体を候補にする。
  if [ -z "$token" ] && [ "$(uname -s 2>/dev/null)" = "Darwin" ] && command -v security >/dev/null 2>&1; then
    keychain_payload=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null)
    if [ -n "$keychain_payload" ]; then
      if printf '%s' "$keychain_payload" | jq empty >/dev/null 2>&1; then
        token=$(printf '%s' "$keychain_payload" \
          | jq -r '(.claudeAiOauth.accessToken | select(type == "string")) // empty' 2>/dev/null)
      else
        token="$keychain_payload"
      fi
    fi
  fi

  if [ -z "$token" ]; then
    echo "[rate-limit] OAuth token を取得できません (~/.claude/.credentials.json が無いか token が空、macOS Keychain も未検出)。サブスクリプション認証環境でのみ経路②が利用可能です。" >&2
    return 1
  fi

  # token 形式検証 (curl --config への injection 防止): 改行を含まない 1 行、
  # かつ引用符・空白を含まないこと。
  newline_count=$(printf '%s' "$token" | wc -l | tr -d ' ')
  if [ "$newline_count" != "0" ] \
    || printf '%s' "$token" | grep -q "[[:space:]]" \
    || printf '%s' "$token" | grep -q "'" \
    || printf '%s' "$token" | grep -q '"'; then
    echo "[rate-limit] OAuth token の形式が不正です (改行・引用符・空白を含んでいます)。" >&2
    return 1
  fi

  printf '%s\n' "$token"
}
