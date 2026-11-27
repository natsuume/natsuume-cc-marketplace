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
# 固定のパスを探さない。agy の stdin は /dev/null につなぐ:
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
# - 引数: `--max-used-percent <N>` だけを受け付ける。N は 10 進整数 (数字 0-9 のみ・最大
#   3 桁) かつ 0 <= N <= 100。空文字の値 (`--max-used-percent ""`) と、空白や改行を含む値も
#   違反とする。違反・値の欠落・未知の引数は、usage (`--max-used-percent` を含む) を stderr
#   に出して exit 1。引数の検査は、依存の検査と agy の起動より前に行う
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
#   グループと bucket の順序は agy の応答のまま保つ。`buckets` が空配列のグループは空配列の
#   まま出す。`description` と `name` は、agy の値が文字列ならそのまま、それ以外 (欠損を
#   含む) なら null にする。`resetTime` は、agy の値が `1970-01-01T00:00:00Z` 以外の文字列
#   ならそのまま、それ以外 (欠損を含む) なら null にする。usedPercent の丸めは四捨五入
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
#    先頭 5 行を出す。agy の stdout の内容は検証しない
# 4. 応答の検証違反: 次の条件を 1 つでも満たさなければ違反とする
#    - agy の stdout が JSON 値ちょうど 1 つとして解釈できる (空・空白だけ・JSON として
#      解釈できない・2 つ以上の JSON 値の連結は違反)
#    - `.status == "SUCCESS"`
#    - `.num_turns == 0` (数値の 0)。0 以外の数値なら agent turn が実行された、つまり
#      1.1.11 より古い agy で `/usage` が prompt として送られた可能性がある。この場合は
#      stderr に num_turns の値と、quota を消費したおそれがある旨 (`quota` の語を含める)
#      と、agy の更新を促す旨を書く
#    - `.command.name == "usage"`
#    - `.command.data.groups` が 1 件以上の配列で、各グループが object で `buckets` 配列を
#      持つ (`buckets` の空配列は許容する。そのグループは閾値判定の対象が無いだけ)
#    - すべてのグループのすべての bucket が object で、文字列の `id` と `window`、0 以上
#      1 以下の数値の `remaining_fraction` を持つ
#
# ## 判定 (--max-used-percent <N>)
#
# 出力する JSON の usedPercent (丸めた後の値) を、すべてのグループのすべての bucket に
# ついて N と比べる。N を超える bucket が 1 つでもあれば exit 2、すべて N 以下 (bucket が
# 1 つも無い場合を含む) なら exit 0。窓 (weekly / 5h) とグループを区別せず、同じ N で
# 判定する。`--max-used-percent` を指定しないときは判定せず、取得に成功すれば exit 0。
#
# ## 制約
#
# - Linux (WSL2) と macOS (bash 3.2 / BSD ツール) の両方で動くこと。bash 4 以降の拡張
#   (連想配列、mapfile、`${var,,}` 等) と GNU 専用のコマンドやオプション (timeout、
#   `date -d`、`sed -r` 等) を使わない。jq は 1.5 以降の組み込み関数だけを使う
# - jq 必須
# - 認証情報を読まない・出力しない (認証は agy 自身が行う)

set +x

LOG_PREFIX='[antigravity-rate-limit]'
UNUSED_RESET_TIME='1970-01-01T00:00:00Z'
AGY_STDERR_HEAD_LINES=5

# stderr に接頭辞付きのメッセージを 1 行出す。
log_error() {
  printf '%s %s\n' "$LOG_PREFIX" "$1" >&2
}

# stderr にメッセージを出して exit 1 で終わる。
fail() {
  log_error "$1"
  exit 1
}

# usage と理由を stderr に出して exit 1 で終わる。
fail_with_usage() {
  log_error '使い方: bash antigravity-rate-limit.sh [--max-used-percent <N>] (N は 0-100 の 10 進整数)'
  fail "$1"
}

# ---- 引数の検査 ----
#
# フラグ指定の有無は値と別の変数で追跡する。値の空文字判定で「閾値指定なし」と区別すると、
# 呼び出し側の変数が空に展開された `--max-used-percent ""` が判定なしの成功に化けるため。

MAX_USED_PERCENT=""
MAX_USED_PERCENT_SET=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --max-used-percent)
      if [ "$#" -lt 2 ]; then
        fail_with_usage "--max-used-percent には値が必要です。"
      fi
      MAX_USED_PERCENT="$2"
      MAX_USED_PERCENT_SET=1
      shift 2
      ;;
    *)
      fail_with_usage "不明な引数です: $1"
      ;;
  esac
done

if [ -n "$MAX_USED_PERCENT_SET" ]; then
  # 数字以外の文字を 1 つでも含む値 (空文字・符号・小数点・空白・改行を含む) を case の
  # パターンで弾く。文字集合を列挙するのは、範囲表記 [0-9] が locale によって数字以外にも
  # 一致しうるため。行単位で照合する grep は改行を含む値を通すため使わない。
  case "$MAX_USED_PERCENT" in
    '' | *[!0123456789]*)
      fail_with_usage "--max-used-percent は 0-100 の 10 進整数で指定してください (指定値: '$MAX_USED_PERCENT')。"
      ;;
  esac
  # 桁数を 3 桁までに絞ってから数値比較する (shell の整数範囲を超える値を比較に渡さない)。
  if [ "${#MAX_USED_PERCENT}" -gt 3 ] || [ "$MAX_USED_PERCENT" -gt 100 ]; then
    fail_with_usage "--max-used-percent は 0-100 の範囲で指定してください (指定値: $MAX_USED_PERCENT)。"
  fi
fi

# ---- 依存の検査 (agy の起動より前に行う) ----

command -v jq >/dev/null 2>&1 || fail "jq が見つかりません。jq をインストールしてから再実行してください。"
command -v agy >/dev/null 2>&1 || fail "agy (Antigravity CLI) が見つかりません。agy 1.1.11 以上をインストールし、PATH に追加してから再実行してください。"

# ---- 一時ディレクトリ ----

# mktemp -d は所有者だけが使える権限 (0700) でディレクトリを作るため、中のファイルは他の
# ユーザーから読めない。umask は変えない (変えると、起動する agy が作るログ等の権限にも
# 継承されるため)。
WORK_DIR=""
# 起動中の agy の pid。起動前と終了後は空。
AGY_PID=""

remove_work_dir() {
  if [ -n "$WORK_DIR" ]; then
    rm -rf "$WORK_DIR" 2>/dev/null
  fi
}

# シグナル受信時は、起動中の agy に TERM を転送し、終了を最大 5 秒待つ。終わらなければ
# KILL で止めてから明示的に exit し、EXIT trap で後始末する。
on_signal() {
  local waited=0
  if [ -n "$AGY_PID" ]; then
    kill -TERM "$AGY_PID" 2>/dev/null
    while kill -0 "$AGY_PID" 2>/dev/null && [ "$waited" -lt 50 ]; do
      sleep 0.1
      waited=$((waited + 1))
    done
    kill -KILL "$AGY_PID" 2>/dev/null
  fi
  exit 1
}

trap remove_work_dir EXIT
trap on_signal HUP INT TERM

WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/antigravity-rate-limit.XXXXXX" 2>/dev/null)
if [ -z "$WORK_DIR" ] || [ ! -d "$WORK_DIR" ]; then
  WORK_DIR=""
  fail "一時ディレクトリの作成に失敗しました。"
fi

AGY_STDOUT_FILE="$WORK_DIR/agy-stdout"
AGY_STDERR_FILE="$WORK_DIR/agy-stderr"

# ---- agy の起動 ----

# agy は background で起動して wait で待つ。foreground の子を待っている間の bash は、
# 受けたシグナルの trap を子の終了まで実行しないため、agy が応答待ちで止まると script も
# 後始末もそのまま止まる。wait はシグナルを受けるとすぐ戻り、trap が agy を止めて終了する。
agy -p "/usage" --output-format json --print-timeout 60s \
  < /dev/null > "$AGY_STDOUT_FILE" 2> "$AGY_STDERR_FILE" &
AGY_PID=$!
wait "$AGY_PID"
AGY_EXIT_CODE=$?
AGY_PID=""

if [ "$AGY_EXIT_CODE" -ne 0 ]; then
  log_error "agy が終了コード $AGY_EXIT_CODE で終了しました。"
  if [ "$AGY_EXIT_CODE" -eq 2 ]; then
    log_error "GEMINI_API_KEY で接続している可能性があります。この接続では利用枠を取得できません (agy 同梱の changelog の記載)。GEMINI_API_KEY を設定しない状態で agy を起動し、ログインしてください。"
  fi
  if [ -s "$AGY_STDERR_FILE" ]; then
    log_error "agy の stderr (先頭 ${AGY_STDERR_HEAD_LINES} 行):"
    head -n "$AGY_STDERR_HEAD_LINES" "$AGY_STDERR_FILE" >&2
  fi
  exit 1
fi

# ---- 応答の検証 ----

# stdout が JSON 値ちょうど 1 つであること。slurp で全 JSON 値を配列に集め、件数を数える。
DOCUMENT_COUNT=$(jq -s 'length' < "$AGY_STDOUT_FILE" 2>/dev/null)
if [ -z "$DOCUMENT_COUNT" ]; then
  fail "agy の応答が JSON として解釈できません。"
fi
if [ "$DOCUMENT_COUNT" != "1" ]; then
  fail "agy の応答が JSON 1 つではありません (JSON の個数: $DOCUMENT_COUNT)。"
fi

# 応答の構造を検証し、最初に見つかった違反の種類 (違反が無ければ ok) を返す。型を先に
# 確かめてからフィールドを読むことで、想定外の型でも jq をエラーで止めない。num_turns が
# 0 以外の数値である違反は status の検査より先に判定する。agent turn が実行された応答は
# status にかかわらず quota を消費したおそれがあり、その警告を他の違反の報告で隠さないため。
VALIDATION_FILTER='
def is_fraction:
  if type != "number" then false else (. >= 0 and . <= 1) end;
def is_valid_bucket:
  if type != "object" then false
  elif (.id | type) != "string" then false
  elif (.window | type) != "string" then false
  else (.remaining_fraction | is_fraction) end;
def is_valid_group:
  if type != "object" then false else ((.buckets | type) == "array") end;
if type != "object" then "not-object"
elif ((.num_turns | type) == "number" and .num_turns != 0) then "num-turns"
elif .status != "SUCCESS" then "status"
elif (.num_turns | type) != "number" then "num-turns-type"
elif (.command | type) != "object" then "command"
elif .command.name != "usage" then "command"
elif (.command.data | type) != "object" then "groups"
elif (.command.data.groups | type) != "array" then "groups"
elif (.command.data.groups | length) == 0 then "groups"
elif ([.command.data.groups[] | is_valid_group] | all | not) then "group"
elif ([.command.data.groups[].buckets[] | is_valid_bucket] | all | not) then "bucket"
else "ok" end
'

VALIDATION_RESULT=$(jq -r "$VALIDATION_FILTER" < "$AGY_STDOUT_FILE" 2>/dev/null)

case "$VALIDATION_RESULT" in
  ok)
    :
    ;;
  not-object)
    fail "agy の応答が JSON object ではありません。"
    ;;
  status)
    fail "agy の応答の status が SUCCESS ではありません (値: $(jq -c '.status' < "$AGY_STDOUT_FILE" 2>/dev/null))。"
    ;;
  num-turns-type)
    fail "agy の応答の num_turns が数値ではありません (値: $(jq -c '.num_turns' < "$AGY_STDOUT_FILE" 2>/dev/null))。"
    ;;
  num-turns)
    fail "agy の応答の num_turns が 0 ではありません (値: $(jq -c '.num_turns' < "$AGY_STDOUT_FILE" 2>/dev/null))。agent turn が実行されました。agy が 1.1.11 より古く /usage が prompt として送られた可能性があり、quota を消費したおそれがあります。agy を 1.1.11 以上に更新してください。"
    ;;
  command)
    fail "agy の応答の command.name が usage ではありません。agy の更新で出力の形式が変わった可能性があります。"
    ;;
  groups)
    fail "agy の応答に command.data.groups (1 件以上の配列) がありません。agy の更新で出力の形式が変わった可能性があります。"
    ;;
  group)
    fail "agy の応答に、buckets 配列を持たないグループがあります。agy の更新で出力の形式が変わった可能性があります。"
    ;;
  bucket)
    fail "agy の応答に、文字列の id・window と 0〜1 の数値の remaining_fraction を持たない bucket があります。agy の更新で出力の形式が変わった可能性があります。"
    ;;
  *)
    fail "agy の応答を検証できませんでした。"
    ;;
esac

# ---- 正規化 ----

# usedPercent は (1 - remaining_fraction) * 100 を小数第 1 位に四捨五入する。値は 0 以上
# なので「1000 倍に 0.5 を足して floor し 10 で割る」で四捨五入になる (round は jq の
# build によって無いことがあるため使わない)。floor の前に 1e-9 を足すのは、2 進浮動小数点の
# 誤差で 0.5 ちょうどの値が 0.4999... になり、切り捨て側 (閾値判定では fail-open 側) に
# ずれるのを防ぐため (例: remaining_fraction 0.9995 の 1000 倍は 0.49999999999994 になる)。
NORMALIZE_FILTER='
def string_or_null:
  if type == "string" then . else null end;
def used_percent:
  (((1 - .) * 1000 + 0.5 + 1e-9) | floor) / 10;
{
  description: (.command.data.description | string_or_null),
  groups: [
    .command.data.groups[] | {
      name: (.name | string_or_null),
      description: (.description | string_or_null),
      buckets: [
        .buckets[] | {
          id: .id,
          name: (.name | string_or_null),
          window: .window,
          remainingFraction: .remaining_fraction,
          usedPercent: (.remaining_fraction | used_percent),
          resetTime: (
            if (.reset_time | type) == "string" and .reset_time != $unused_reset_time
            then .reset_time
            else null
            end
          ),
          description: (.description | string_or_null)
        }
      ]
    }
  ]
}
'

NORMALIZED_JSON=$(jq -c --arg unused_reset_time "$UNUSED_RESET_TIME" "$NORMALIZE_FILTER" < "$AGY_STDOUT_FILE" 2>/dev/null)
if [ -z "$NORMALIZED_JSON" ]; then
  fail "agy の応答の正規化に失敗しました。"
fi

# ---- 閾値判定 (--max-used-percent 指定時のみ) ----

EXIT_CODE=0

# usedPercent が N を超えた bucket を 1 行ずつ「グループ名 / id (window): usedPercent%」で
# 列挙する。N は先頭の 0 を含みうる (例: 007) ため、JSON の数値としてではなく文字列で渡して
# 変換する。
JUDGE_FILTER='
($max | tonumber) as $limit
| .groups[] as $group
| $group.buckets[]
| select(.usedPercent > $limit)
| "\($group.name // "(名前なし)") / \(.id) (\(.window)): \(.usedPercent)%"
'

if [ -n "$MAX_USED_PERCENT_SET" ]; then
  EXCEEDED_BUCKETS=$(printf '%s' "$NORMALIZED_JSON" | jq -r --arg max "$MAX_USED_PERCENT" "$JUDGE_FILTER" 2>/dev/null)
  JUDGE_EXIT_CODE=$?
  if [ "$JUDGE_EXIT_CODE" -ne 0 ]; then
    fail "--max-used-percent の判定に失敗しました。"
  fi
  if [ -n "$EXCEEDED_BUCKETS" ]; then
    EXIT_CODE=2
    log_error "使用率が ${MAX_USED_PERCENT}% を超えた枠があります:"
    printf '%s\n' "$EXCEEDED_BUCKETS" >&2
  fi
fi

printf '%s\n' "$NORMALIZED_JSON"
exit "$EXIT_CODE"
