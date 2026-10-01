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
#   (空文字列は未設定と同じに扱う)
# - --ignore-comment-id <id>: 数値 comment id。複数回指定できる。指定した id の claim comment は
#   早期判定・先着判定の対象から除き、`session=` が自分のセッション ID と一致しても自分の claim
#   として扱わない。数字以外は引数の不正とする
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

printf 'error reason=not-implemented\n'
exit 2
