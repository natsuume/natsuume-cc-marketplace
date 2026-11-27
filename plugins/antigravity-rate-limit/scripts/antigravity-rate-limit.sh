#!/bin/bash
# antigravity-rate-limit.sh — Antigravity CLI (agy) の利用枠の取得と閾値判定
#
# /antigravity-rate-limit:status skill が foreground で 1 回実行する。agy の print mode で
# `/usage` を実行し、応答 JSON を検証・正規化して stdout に出す。
#
# ## 使い方
#
#   bash "<plugin-root>/scripts/antigravity-rate-limit.sh" [--max-used-percent <N>]
#
# ## 取得方式
#
# PATH 上の agy を次の引数で 1 回だけ起動する (リトライしない)。agy は PATH から解決し、
# 固定のパスを探さない:
#
#   agy -p "/usage" --output-format json --print-timeout 60s
#
# - agy 同梱の changelog (1.1.11) に、print mode の `-p "/usage"` 等は agent turn を
#   始めず、quota を消費せず、conversation を残さないとある。1.1.11 より古い agy では
#   `/usage` が model への prompt として扱われ、quota を消費しうる
# - `--disable-slash-commands` を付けると `/usage` が通常の prompt として送られるため付けない
# - agy は実行ごとに `~/.gemini/antigravity-cli/log/` へのログ作成、期限切れの OAuth token
#   の refresh、前回の実行から 15 分以上空いていれば auto-update の起動を行う (agy 自身の
#   挙動。本 script からは制御しない)
# - `GEMINI_API_KEY` で接続している agy は終了コード 2 で終わる (agy 同梱の changelog の
#   記載。未実測)。本 script はこれも agy の非 0 終了として exit 1 にする
#
# agy の stdout は JSON 1 つ (2026-11-27 agy 1.5.18 / WSL2 で実測。説明文と一部の
# フィールドを省略し、グループは 1 つだけ示す):
#
#   {"conversation_id":"","status":"SUCCESS","response":"...","duration_seconds":0,
#    "num_turns":0,"usage":{"input_tokens":0,...},
#    "command":{"name":"usage","data":{
#      "description":"Within each group, models share a weekly limit and a 5-hour limit. ...",
#      "groups":[
#        {"name":"<グループ名>","description":"Models within this group: ...",
#         "buckets":[
#           {"id":"gemini-weekly","name":"Weekly Limit Remaining","window":"weekly",
#            "remaining_fraction":0.9880305528640747,"reset_time":"2026-12-04T09:00:59Z",
#            "description":"... fully refresh in 6 days, 23 hours."},
#           {"id":"gemini-5h","name":"Five Hour Limit Remaining","window":"5h",
#            "remaining_fraction":0.9821221232414246,"reset_time":"2026-11-27T14:00:59Z"}]}]}}}
#
# - 枠はモデル単位ではなくグループ単位 (同じグループのモデルが週次枠と 5 時間枠を共有する)
# - `remaining_fraction` は 0〜1 の数値 (満タンでは整数 1)
# - `reset_time` は RFC3339 の UTC。未使用の bucket では `1970-01-01T00:00:00Z`
# - bucket の `description` は使用済みの bucket にだけ付く
#
# ## I/O 契約
#
# - stdin: 使用しない
# - 引数: `--max-used-percent <N>` だけを受け付ける。N は 10 進整数 (数字のみ・最大 3 桁)
#   かつ 0 <= N <= 100。空文字の値 (`--max-used-percent ""`) も違反とする。違反・値の
#   欠落・未知の引数は、usage (`--max-used-percent` を含む) を stderr に出して exit 1。
#   引数の検査は、依存の検査と agy の起動より前に行う
# - stdout: 取得に成功したとき (exit 0 / 2) だけ、次の形の JSON を 1 つ出す。exit 1 では
#   何も出さない
#
#     {
#       "description": <command.data.description>,
#       "groups": [
#         {
#           "name": <groups[].name>,
#           "description": <groups[].description>,
#           "buckets": [
#             {
#               "id": <buckets[].id>,
#               "name": <buckets[].name>,
#               "window": <buckets[].window>,
#               "remainingFraction": <buckets[].remaining_fraction>,
#               "usedPercent": <(1 - remainingFraction) * 100 を小数第 1 位に丸めた数値>,
#               "resetTime": <buckets[].reset_time。1970-01-01T00:00:00Z なら null>,
#               "description": <buckets[].description。無ければ null>
#             }
#           ]
#         }
#       ]
#     }
#
#   グループと bucket の順序は agy の応答のまま保つ。`description` と `name` は、agy の値が
#   文字列ならそのまま、それ以外 (欠損を含む) なら null にする。`resetTime` は、agy の値が
#   `1970-01-01T00:00:00Z` 以外の文字列ならそのまま、それ以外 (欠損を含む) なら null にする
# - stderr: 進捗とエラーを日本語で出す (`[antigravity-rate-limit] ` を前置する)
# - exit code (呼び出し側が「取得失敗」と「超過」を区別できること):
#     0 = 取得成功 (`--max-used-percent` 指定時の判定 OK を含む)
#     1 = 取得失敗・引数不正 (次節の「失敗の分類」)
#     2 = `--max-used-percent` 指定時のみ: いずれかの bucket の usedPercent が N を超えた。
#         JSON は出す
#
# ## 失敗の分類 (exit 1)
#
# stderr の文言で、どの段階で失敗したかを区別できるようにする。
#
# 1. 引数不正: usage を出す。agy を起動しない
# 2. 依存の不在: jq が PATH に無い / agy が PATH に無い。stderr にどちらが無いかを書く。
#    依存の検査は agy の起動より前に行う (jq が無いときは agy を起動しない)
# 3. agy の非 0 終了 (終了コード 2 を含む): stderr に agy の終了コードと、agy の stderr の
#    先頭数行を出す。agy の stdout の内容は検証しない
# 4. 応答の検証違反: 次の条件を 1 つでも満たさなければ違反とする
#    - agy の stdout が JSON として解釈できる
#    - `.status == "SUCCESS"`
#    - `.num_turns == 0` (数値の 0)。0 以外の数値なら agent turn が実行された、つまり
#      1.1.11 より古い agy で `/usage` が prompt として送られた可能性がある。この場合は
#      stderr に num_turns の値と、quota を消費したおそれがある旨 (`quota` の語を含める)
#      と、agy の更新を促す旨を書く
#    - `.command.name == "usage"`
#    - `.command.data.groups` が 1 件以上の配列で、各グループが `buckets` 配列を持つ
#    - すべてのグループのすべての bucket が object で、文字列の `id` と `window`、0 以上
#      1 以下の数値の `remaining_fraction` を持つ
#
# ## 判定 (--max-used-percent <N>)
#
# 出力する JSON の usedPercent (丸めた後の値) を、すべてのグループのすべての bucket に
# ついて N と比べる。N を超える bucket が 1 つでもあれば exit 2、すべて N 以下なら exit 0。
# 窓 (weekly / 5h) とグループを区別せず、同じ N で判定する。`--max-used-percent` を
# 指定しないときは判定せず、取得に成功すれば exit 0。
#
# ## 制約
#
# - Linux (WSL2) と macOS (bash 3.2 / BSD ツール) の両方で動くこと。bash 4 以降の拡張
#   (連想配列、mapfile、`${var,,}` 等) と GNU 専用のコマンドやオプション (timeout、
#   `date -d`、`sed -r` 等) を使わない
# - jq 必須
# - 認証情報を読まない・出力しない (認証は agy 自身が行う)

# 暫定: 取得・検証・判定を実装しておらず、常に未実装である旨を stderr に出して exit 1 で
# 終わる。tests/test_antigravity_rate_limit.py の挙動テストが通る実装に置き換える。
printf '[antigravity-rate-limit] %s\n' '取得処理は未実装です。' >&2
exit 1
