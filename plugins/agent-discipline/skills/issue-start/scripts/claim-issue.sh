#!/bin/bash
# claim-issue.sh
#
# issue への着手の排他制御 (rule:issue-claim の step 1〜5) を行う。claim comment を投稿し、
# GitHub が付ける順序で最も早い claim comment が自分のものなら確保とする。作業 branch の用意
# (step 6) と branch 名の決め方 (命名規約の検証を含む) は扱わない。
#
# ============================================================================
# 入出力契約
# ============================================================================
#
#   claim-issue.sh <issue 番号> <branch 名> [--session-id <id>] [--ignore-comment-id <id>]...
#                  [--confirmed-leftover]
#
# - <issue 番号>: 数字だけの文字列。それ以外は引数の不正とする
# - <branch 名>: claim comment の `branch=` に入れる。空文字列は引数の不正とする
# - --session-id <id>: 自分のセッション ID。無ければ環境変数 CLAUDE_CODE_SESSION_ID を使う
#   (どちらも空文字列は未設定と同じに扱う)。`-` で始まる値は、値の欠けたオプションとして
#   引数の不正とする
# - --ignore-comment-id <id>: 数値 comment id。複数回指定できる。指定した id の claim comment は
#   早期判定・先着判定の対象から除き、`session=` が自分のセッション ID と一致しても自分の claim
#   として扱わない。先頭の 0 は無視して数値として比べる。数字以外は引数の不正とする
# - --confirmed-leftover: 早期判定で `ai:in-progress` ラベルがあっても撤退しない (claim comment
#   による撤退には影響しない)
# - 上記以外のオプション、値の欠けたオプションは引数の不正とする
#
# stdout: 結果を必ず 1 行だけ書く。
#   claimed comment_id=<id>                 確保した (ラベルも付与した)
#   claimed comment_id=<id> label=failed    確保した (ラベルの付与だけ失敗した)
#   retreat reason=label                    早期判定でラベル `ai:in-progress` を見つけた
#   retreat reason=existing-claim           早期判定で claim comment を見つけた
#   retreat reason=lost-race                先着判定で負けた (自分の claim comment は削除済み)
#   error reason=<内容>                     停止した (<内容> は空白を含まない短い識別子)
#
# error reason の値:
#   invalid-arguments     引数の不正
#   missing-session-id    セッション ID が無い
#   missing-jq            jq が無い
#   label-fetch-failed    step 1 のラベルの取得の失敗
#   comment-fetch-failed  step 1 の comment の取得の失敗
#   post-failed           claim comment の投稿の失敗
#   refetch-failed        step 4 の comment の取得の失敗
#   own-claim-missing     step 4 の取得結果に自分の claim が無い
#   delete-failed         先着判定で負けた後の、自分の claim comment の削除の失敗
#
# exit code:
#   0 = 確保 / 1 = 撤退 / 2 = 停止 (fail-closed。「競合なし」とは扱わない)
#
# ============================================================================
# 手順
# ============================================================================
#
# 1. 早期判定: ラベル `ai:in-progress` (--confirmed-leftover のときは見ない) か、claim comment
#    (本文が `🔒 ai:claim ` で始まる comment。--ignore-comment-id の id を除く) があれば撤退する
# 2. claim comment を投稿する。本文は次の 1 行:
#      🔒 ai:claim branch=<branch 名> session=<セッション ID> ts=<UTC ISO 8601>
#    ts= は `date -u +%Y-%m-%dT%H:%M:%SZ` の形 (例: 2026-10-01T00:00:00Z)
# 3. `sleep 3` で、他 session の claim comment が一覧に反映されるまでの遅れを吸収する
# 4. comment を全ページ取得し直し、claim comment (--ignore-comment-id の id を除く) のうち
#    (created_at, 数値 id) の辞書順で最小のものを先着とする。created_at は文字列 (UTC の
#    ISO 8601) として、id は数値として比べる。ts= の値は判定に使わない。自分の claim は、
#    本文の `session=` の値がセッション ID と完全に一致する claim comment とする
#    - 先着が自分でなければ、POST の応答で得た id の comment だけを削除して撤退する
#      (`session=` で探し直して削除しない。--ignore-comment-id で除いた自分の session の
#      claim を消さないため)
# 5. 先着が自分なら、ラベル `ai:in-progress` を付ける
#
# ============================================================================
# 境界・異常系
# ============================================================================
#
# - 引数の不正 (issue 番号が数字でない・branch 名が空・--ignore-comment-id が数字でない・
#   未知のオプション・値の欠けたオプション) → 何も投稿せず exit 2
# - セッション ID が --session-id にも CLAUDE_CODE_SESSION_ID にも無い → 投稿せず exit 2
# - step 1 の取得 (ラベル・comment) の失敗 (非ゼロ終了・JSON として読めない応答) → 投稿せず
#   exit 2
# - claim comment の投稿の失敗 (非ゼロ終了・応答から id を読めない) → exit 2
# - step 4 の取得の失敗 (非ゼロ終了・ページ取得不能・JSON として読めない応答) → 確保を確定
#   せず exit 2。投稿済みの自分の claim は削除しない (ユーザーが状態を確認できるように残す)
# - step 4 の取得結果に自分の claim が無い → 確保を確定せず exit 2 (削除しない)
# - 先着判定で負けた後、自分の claim の削除が失敗 → exit 2 (撤退扱いにしない)
# - ラベル付与の失敗 → 確保として exit 0、stdout は `claimed comment_id=<id> label=failed`
#   (確保は claim comment の先着で決まり、ラベルは人間向けの目印のため)
#
# ============================================================================
# 使う外部コマンド
# ============================================================================
#
# `{owner}` / `{repo}` は gh が current repository から解決する。<N> は issue 番号。
#
# - ラベルの取得:
#     gh api 'repos/{owner}/{repo}/issues/<N>'
#   (応答の `.labels[].name`)
# - comment の全件取得 (早期判定と先着判定の両方):
#     gh api --paginate --slurp 'repos/{owner}/{repo}/issues/<N>/comments?per_page=100'
#   (出力はページの配列の配列。`jq 'add // []'` で 1 つの配列に結合する)
# - claim comment の投稿:
#     gh api -X POST 'repos/{owner}/{repo}/issues/<N>/comments' -f body=<本文>
#   (応答の `.id` と `.created_at` を使う)
# - 先着判定で負けたときの削除:
#     gh api -X DELETE 'repos/{owner}/{repo}/issues/comments/<id>'
# - ラベルの付与:
#     gh issue edit <N> --add-label ai:in-progress
# - 待機: sleep 3 (PATH 上の sleep)
# - JSON の処理: jq
#
# ============================================================================
# 移植性
# ============================================================================
#
# Linux (WSL2 を含む) の bash と macOS の bash 3.2 で動かす。bash 4 以降の機能 (mapfile /
# readarray、連想配列の declare -A、大文字小文字変換の ${var,,} / ${var^^}) と、GNU 拡張に
# 依存する date / sed / grep のオプションを使わない。

IN_PROGRESS_LABEL="ai:in-progress"
CLAIM_PREFIX="🔒 ai:claim "

# 結果を 1 行書いて終わる。$1 = stdout の行、$2 = exit code。
finish() {
  printf '%s\n' "$1"
  exit "$2"
}

stop() {
  finish "error reason=$1" 2
}

# 数字だけの文字列なら 0 を返す。
is_digits() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
  esac
  return 0
}

# ----------------------------------------------------------------------------
# 引数
# ----------------------------------------------------------------------------

ISSUE=""
BRANCH=""
POSITIONAL_COUNT=0
SESSION_ID=""
# 除外する数値 comment id の JSON 配列 (jq に --argjson で渡す)。
IGNORE_IDS_JSON=""
CONFIRMED_LEFTOVER=0

while [ $# -gt 0 ]; do
  case "$1" in
    --session-id)
      [ $# -ge 2 ] || stop invalid-arguments
      # 次のオプションを値として取り込まないよう、`-` で始まる値は値の欠落とみなす。
      case "$2" in
        -*) stop invalid-arguments ;;
      esac
      SESSION_ID=$2
      shift 2
      ;;
    --ignore-comment-id)
      [ $# -ge 2 ] || stop invalid-arguments
      is_digits "$2" || stop invalid-arguments
      # 先頭の 0 を落として JSON の数値にする (例: 007 → 7)。
      normalized=$(printf '%s' "$2" | sed 's/^0*\([0-9]\)/\1/')
      IGNORE_IDS_JSON="$IGNORE_IDS_JSON,$normalized"
      shift 2
      ;;
    --confirmed-leftover)
      CONFIRMED_LEFTOVER=1
      shift
      ;;
    -*)
      stop invalid-arguments
      ;;
    *)
      POSITIONAL_COUNT=$((POSITIONAL_COUNT + 1))
      case "$POSITIONAL_COUNT" in
        1) ISSUE=$1 ;;
        2) BRANCH=$1 ;;
        *) stop invalid-arguments ;;
      esac
      shift
      ;;
  esac
done

[ "$POSITIONAL_COUNT" -eq 2 ] || stop invalid-arguments
is_digits "$ISSUE" || stop invalid-arguments
[ -n "$BRANCH" ] || stop invalid-arguments
IGNORE_IDS_JSON="[${IGNORE_IDS_JSON#,}]"

if [ -z "$SESSION_ID" ]; then
  SESSION_ID=${CLAUDE_CODE_SESSION_ID:-}
fi
[ -n "$SESSION_ID" ] || stop missing-session-id

command -v jq >/dev/null 2>&1 || stop missing-jq

# ----------------------------------------------------------------------------
# GitHub からの取得と JSON の処理
# ----------------------------------------------------------------------------

# ラベル `ai:in-progress` が付いていれば true、無ければ false を書く。取得の失敗・読めない応答
# では 1 を返す。
fetch_has_in_progress_label() {
  local response
  response=$(gh api "repos/{owner}/{repo}/issues/$ISSUE") || return 1
  printf '%s' "$response" | jq -r --arg label "$IN_PROGRESS_LABEL" '
    if type == "object" and (.labels | type) == "array"
    then any(.labels[]; .name? == $label)
    else error("labels が読めない")
    end
  ' 2>/dev/null
}

# comment の全件を 1 つの JSON 配列として書く。取得の失敗 (非ゼロ終了) と読めない応答では 1 を
# 返す。gh が途中までの出力で非ゼロ終了した場合も失敗として扱う。
fetch_all_comments() {
  local response
  response=$(gh api --paginate --slurp "repos/{owner}/{repo}/issues/$ISSUE/comments?per_page=100") \
    || return 1
  printf '%s' "$response" | jq -c '
    if type == "array" and all(.[]; type == "array")
    then add // []
    else error("ページの配列ではない")
    end
  ' 2>/dev/null
}

# 判定の対象にする claim comment (本文が CLAIM_PREFIX で始まり、除外 id に入っていないもの) を
# 取り出し、各要素に `session=` の値 (無ければ空文字列) を付ける jq の定義。`$` は jq の変数
# なので、シェルに展開させないよう single quote で囲む。
# shellcheck disable=SC2016
CLAIMS_JQ='
  def session_of:
    [(.body // "") | splits("\\s+") | select(startswith("session="))]
    | (.[0] // "") | ltrimstr("session=");
  def claims($prefix; $ignore):
    [ .[]
      | select(type == "object")
      | select((.body | type) == "string" and (.body | startswith($prefix)))
      | select(.id as $id | any($ignore[]; . == $id) | not)
      | . + {session: session_of}
    ];
'

# 除外していない claim comment の数を書く。
count_claims() {
  printf '%s' "$1" | jq -r --arg prefix "$CLAIM_PREFIX" --argjson ignore "$IGNORE_IDS_JSON" \
    "$CLAIMS_JQ"' claims($prefix; $ignore) | length' 2>/dev/null
}

# 先着判定の結果 (win / lose / missing) を書く。先着は (created_at, 数値 id) の辞書順で最小の
# claim comment。自分の claim は `session=` の値がセッション ID と完全に一致するもの。
judge_first_claim() {
  printf '%s' "$1" | jq -r --arg prefix "$CLAIM_PREFIX" --argjson ignore "$IGNORE_IDS_JSON" \
    --arg session "$SESSION_ID" "$CLAIMS_JQ"'
    claims($prefix; $ignore) as $claims
    | if ($claims | map(select(.session == $session)) | length) == 0 then "missing"
      elif ($claims | sort_by([.created_at, .id]) | .[0].session) == $session then "win"
      else "lose"
      end
  ' 2>/dev/null
}

# ----------------------------------------------------------------------------
# step 1: 早期判定
# ----------------------------------------------------------------------------

HAS_LABEL=$(fetch_has_in_progress_label) || stop label-fetch-failed
case "$HAS_LABEL" in
  true|false) ;;
  *) stop label-fetch-failed ;;
esac
COMMENTS=$(fetch_all_comments) || stop comment-fetch-failed
CLAIM_COUNT=$(count_claims "$COMMENTS") || stop comment-fetch-failed
is_digits "$CLAIM_COUNT" || stop comment-fetch-failed

if [ "$HAS_LABEL" = "true" ] && [ "$CONFIRMED_LEFTOVER" -eq 0 ]; then
  finish "retreat reason=label" 1
fi
if [ "$CLAIM_COUNT" -gt 0 ]; then
  finish "retreat reason=existing-claim" 1
fi

# ----------------------------------------------------------------------------
# step 2: claim comment の投稿
# ----------------------------------------------------------------------------

TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
BODY="${CLAIM_PREFIX}branch=$BRANCH session=$SESSION_ID ts=$TIMESTAMP"
POSTED=$(gh api -X POST "repos/{owner}/{repo}/issues/$ISSUE/comments" -f "body=$BODY") \
  || stop post-failed
POSTED_ID=$(printf '%s' "$POSTED" | jq -r '.id | numbers | tostring' 2>/dev/null)
is_digits "$POSTED_ID" || stop post-failed

# ----------------------------------------------------------------------------
# step 3: 3 秒待機
# ----------------------------------------------------------------------------

sleep 3

# ----------------------------------------------------------------------------
# step 4: 全件の取得し直しと先着判定
# ----------------------------------------------------------------------------

# 取得の失敗と自分の claim が無い場合は、投稿済みの自分の claim を削除せずに停止する。
COMMENTS=$(fetch_all_comments) || stop refetch-failed
JUDGEMENT=$(judge_first_claim "$COMMENTS") || stop refetch-failed
case "$JUDGEMENT" in
  win) ;;
  lose)
    gh api -X DELETE "repos/{owner}/{repo}/issues/comments/$POSTED_ID" >/dev/null \
      || stop delete-failed
    finish "retreat reason=lost-race" 1
    ;;
  missing) stop own-claim-missing ;;
  *) stop refetch-failed ;;
esac

# ----------------------------------------------------------------------------
# step 5: ラベル付与
# ----------------------------------------------------------------------------

if gh issue edit "$ISSUE" --add-label "$IN_PROGRESS_LABEL" >/dev/null; then
  finish "claimed comment_id=$POSTED_ID" 0
fi
finish "claimed comment_id=$POSTED_ID label=failed" 0
