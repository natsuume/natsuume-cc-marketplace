#!/bin/bash
# codex-rate-limit.sh — codex (OpenAI) の rate limit 取得 + 閾値判定 (issue #245)
#
# /rate-limit:codex-status skill が foreground で 1 回実行する。codex app-server
# (stdio JSON-RPC) の `account/rateLimits/read` で rate limit を取得する。これは公式
# ドキュメント化済みの RPC であり、fetch-rate-limit.sh の経路② (undocumented OAuth
# endpoint) と異なり非公式依存が無い。codex 実装委任 (issue #247 の codex-implementer
# plugin) の「使用率 50% 超なら委任しない」fail-closed ガードの判定部品となる。
#
# ## 使い方
#
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/codex-rate-limit.sh" [--max-used-percent <N>]
#
# ## I/O 契約 (issue #245 で確定)
#
# - stdin: 使用しない
# - stdout: rate limit の JSON 1 ドキュメント (取得成功時は判定結果に依らず出力)。
#   `account/rateLimits/read` 応答の `.result` を**無加工**で出力する:
#     {
#       "rateLimits": {                  // limitId `codex` をミラーする単一バケットビュー
#         "planType": "pro",
#         "rateLimitReachedType": null,  // 非 null = 到達済み
#         "primary": {
#           "usedPercent": <0-100>,
#           "windowDurationMins": 10080, // 10080 = 週次
#           "resetsAt": <epoch 秒>        // 実測 2026-07-15 (codex CLI 0.144.1): epoch 秒。
#         }                              // 無加工出力のため ISO 変換は行わない
#       },
#       "rateLimitsByLimitId": { "codex": <RateLimitSnapshot>, "codex_bengalfox": ... },
#       "rateLimitResetCredits": { ... } // リセットクレジット一覧 (null のことがある)
#     }
# - stderr: エラー理由・進捗メッセージ (人間可読、fetch-rate-limit.sh と同じ設計)
# - exit code (公開契約。呼び出し側が「取得失敗」と「超過」を区別できること):
#     0 = 正常 (--max-used-percent 判定 OK を含む)
#     1 = 取得失敗・引数不正 (codex CLI 不在 / RPC エラー応答 / 30 秒 timeout /
#         応答が JSON 解釈不能 / rateLimits.primary.usedPercent 欠損 / secondary 窓が
#         存在するのに usedPercent 不正 / N の validation 違反)
#     2 = --max-used-percent 指定時のみ: primary / secondary (存在する場合) いずれかの
#         usedPercent が N 超、または rateLimitReachedType が非 null (到達済み)。
#         いずれの場合も JSON は出力する
#
# ## 判定仕様 (--max-used-percent <N>)
#
# - 主対象は `.rateLimits.primary.usedPercent` (limitId `codex` の primary、必須)。スキーマ上
#   `rateLimitsByLimitId` は nullable のため、必須フィールド検証・判定は backward-compatible
#   な `rateLimits` 側で行う
# - `.rateLimits.secondary` は tri-state で扱う (rescue 壁打ち 2026-07-15 で確定):
#   欠損 / null → 正常として無視。非 null で存在 → usedPercent (0-100 の数値) を必須検証し、
#   欠損・非数値・範囲外は応答不正として exit 1 (plan によっては primary=5h 窓 /
#   secondary=週次窓の構成がありえ、壊れた窓の黙殺は fail-open になるため)。存在して valid
#   なら primary と同じ閾値 N で追加判定する
# - `rateLimitReachedType` が非 null → exit 2 (usedPercent の値に依らず)
# - primary / secondary (存在する場合) のいずれかの usedPercent > N → exit 2 /
#   すべて N 以下 → exit 0 (usedPercent は小数でありうる)
# - N の validation: 10 進整数 (最大 3 桁。shell 整数範囲超の全数字文字列を構造排除) かつ
#   0 <= N <= 100。違反は usage を stderr に出して exit 1。フラグ指定の有無は値と独立に
#   追跡し、空文字の値 (`--max-used-percent ""`) も validation 違反として exit 1 にする
#   (「閾値指定なし」への silent fallback = fail-open を塞ぐ)。`--max-used-percent` 以外の
#   引数・値の欠落も usage + exit 1
#
# ## RPC シーケンス (実測確認済み 2026-07-15、codex CLI 0.144.1)
#
#   → {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"rate-limit-plugin","version":"<plugin version>"}}}
#   → {"jsonrpc":"2.0","method":"initialized"}
#   → {"jsonrpc":"2.0","id":2,"method":"account/rateLimits/read"}
#   ← JSONL で応答が返る。id:2 の応答行 (result | error) を待つ。id:1 の error 応答も
#     即失敗として扱う (initialize 失敗後に id:2 の応答は来ない)
#
# プロトコル全スキーマは `codex app-server generate-json-schema --out <DIR>` でローカル
# 生成して確認できる (InitializeParams は clientInfo.name / clientInfo.version が必須)。
#
# ## プロセスライフサイクル設計 (codex advisor 相談 2026-07-15 の指摘を反映)
#
# codex app-server は stdin EOF で終了するため、3 メッセージを送って即 stdin を閉じると
# id:2 応答が返る前にサーバが終了する (実測)。bash 3.2 (macOS) に coproc は無く GNU
# timeout も使えないため、mkfifo + writer fd 維持 + poll で実装する:
#
#   1. umask 077 + `mktemp -d` の専用一時ディレクトリに fifo / out / err を置く
#   2. EXIT/HUP/INT/TERM trap で「FD close → child TERM/KILL → reap → 一時 dir 削除」を
#      保証する (fetch-rate-limit.sh は正常経路のみ削除だが、本 script は background
#      プロセスを持つため異常経路の後始末が必須。SIGKILL 時の残骸のみ許容)
#   3. mkfifo 成功後に `codex app-server < fifo > out 2> err &` を起動し、`exec 3> fifo`
#      で writer fd を開く (双方の open が rendezvous する。`exec 3<>fifo` は O_RDWR の
#      移植性問題と、自己保有 read 端による EOF/SIGPIPE 隠蔽があるため使わない)
#   4. サーバ起動**後**に親プロセスのみ SIGPIPE を無視し (`trap '' PIPE`)、`printf >&3`
#      の失敗を明示的な exit 1 に変換する (起動前に無視すると codex 側に継承される)
#   5. poll ループ (0.5 秒 × 60 回 = 最大 30 秒):「id:2 応答 or RPC error の出現 →
#      `kill -0` でサーバ生存確認」の順で判定する。サーバ死亡検出時は out をもう一度
#      確認してから失敗分類する (死亡直前に応答を flush している可能性があるため)。
#      書きかけの行と malformed 行は「行の完成 (改行)」を境界に区別する: 改行で完結した
#      行が JSON として parse できない場合は JSONL プロトコル違反として即「JSON 解釈
#      不能」で失敗する (30 秒待たない)。改行の無い末尾断片は書きかけとして poll を
#      継続し、polling 終了時 (サーバ死亡・期限到達) の最終走査でのみ完成行と同じ基準で
#      検証する (codex review 指摘 + rescue 壁打ちで確定)
#   6. 取得後の shutdown も上限付きにする: FD close → grace → TERM → grace → KILL →
#      `wait` で reap (単純な `wait` は無期限停止点になるため)
#
#   注: kill はサーバ PID 単体に送る。codex が子孫プロセスを生成した場合は残存しうるが、
#   stdin EOF (FD close) による自律終了が通常経路のため実害は限定的 (advisor 指摘の残リスク)。
#
# ## 異常系と stderr
#
# 失敗時は人間可読の理由を stderr に必ず出す (どの段階で失敗したか区別できる文言)。
# app-server の stderr は err ファイルに保存し、異常終了時は末尾数行を診断情報として添える。
#
# ## 制約
#
# - Linux (WSL2) / macOS (bash 3.2 / BSD ツール) の両方で動作すること。bash 4+ 拡張
#   (coproc、連想配列、`${var//[[:space:]]/}` 等) と GNU 専用オプション (timeout、
#   sort -V 等) は使わない。sleep の小数秒指定 (0.5) は GNU/BSD 両対応のため使用可
# - 秘匿情報 (token 等) を読み取らない・出力しない (認証は codex app-server プロセス
#   自身が内部で処理する。本 script は auth.json 等に触れない)
# - jq 必須 (既存 plugin と同じ扱い)。codex CLI 不在は明示エラーで exit 1
#
# ---------------------------------------------------------------------------
# 実装本体 (Phase B, issue #245)
# ---------------------------------------------------------------------------

set +x

# stderr に "[rate-limit] " プレフィクス付きの人間可読メッセージを出して exit 1 する helper
# (fetch-rate-limit.sh / run-codex-advisor.sh と同じ設計)。
fail() {
  printf '[rate-limit] %s\n' "$1" >&2
  exit 1
}

usage() {
  printf '[rate-limit] 使い方: bash codex-rate-limit.sh [--max-used-percent <N>] (N は 0-100 の10進整数)\n' >&2
}

# ---- 引数解析 ----
#
# 受理するのは --max-used-percent <N> のみ。未知の引数・値欠落・N の validation 違反は
# すべて usage を出して exit 1 にする。
#
# フラグ指定の有無は値 (MAX_USED_PERCENT) と独立した MAX_USED_PERCENT_SET で追跡する。
# 値の空文字判定 ([ -n "$MAX_USED_PERCENT" ]) をゲートに使うと、`--max-used-percent ""`
# (呼び出し側の変数が空に展開されたケース) が「閾値指定なし」と同じ挙動に silent に
# 化けて fail-open になるため (codex review 指摘)。

MAX_USED_PERCENT=""
MAX_USED_PERCENT_SET=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --max-used-percent)
      if [ "$#" -lt 2 ]; then
        usage
        fail "--max-used-percent には値が必要です。"
      fi
      MAX_USED_PERCENT="$2"
      MAX_USED_PERCENT_SET=1
      shift 2
      ;;
    *)
      usage
      fail "不明な引数です: $1"
      ;;
  esac
done

if [ -n "$MAX_USED_PERCENT_SET" ]; then
  # 10 進整数のみ許可 (小数・負数・空文字・非数字はすべて regex で弾く)。桁数を最大 3 桁に
  # 制限するのは、shell の整数範囲を超える全数字文字列 (例: 24 桁の 9) が `[ ... -gt 100 ]`
  # の "integer expression expected" エラー (false 扱い) で validation を素通りする経路を
  # 塞ぐため (codex review 指摘)。0-999 に絞れば後続の -gt 100 は常に安全に動作する。
  if ! printf '%s' "$MAX_USED_PERCENT" | grep -Eq '^[0-9]{1,3}$'; then
    usage
    fail "--max-used-percent は 0-100 の10進整数で指定してください (指定値: '$MAX_USED_PERCENT')"
  fi
  if [ "$MAX_USED_PERCENT" -gt 100 ]; then
    usage
    fail "--max-used-percent は 0-100 の範囲で指定してください (指定値: $MAX_USED_PERCENT)"
  fi
fi

# ---- 依存チェック ----

command -v jq >/dev/null 2>&1 || fail "jq が見つかりません。jq をインストールしてから再実行してください。"
command -v codex >/dev/null 2>&1 || fail "codex CLI が見つかりません。codex CLI をインストールしてから再実行してください。"

# ---- プロセスライフサイクル用の helper 関数群 (workdir 作成・trap 設置より前に定義する) ----

# SERVER_PID (グローバル) が生存しているかを kill -0 で確認しつつ、最大 2 秒
# (0.2 秒 × 10 回) 待つ。途中で消えたら即 return 0、待ちきっても生きていれば return 1。
wait_for_server_exit() {
  local tries=0
  while [ "$tries" -lt 10 ]; do
    kill -0 "$SERVER_PID" 2>/dev/null || return 0
    sleep 0.2
    tries=$((tries + 1))
  done
  return 1
}

# codex app-server (background) と一時ディレクトリの後始末。EXIT/HUP/INT/TERM のいずれでも
# 呼ばれる。順序: writer fd close (stdin EOF → 自律終了の grace) → まだ生きていれば TERM →
# grace → まだ生きていれば KILL → wait で reap (無期限停止を避けるため) → 一時ディレクトリ削除。
cleanup() {
  exec 3>&- 2>/dev/null

  if [ -n "$SERVER_PID" ]; then
    if ! wait_for_server_exit; then
      kill -TERM "$SERVER_PID" 2>/dev/null
      if ! wait_for_server_exit; then
        kill -KILL "$SERVER_PID" 2>/dev/null
      fi
    fi
    wait "$SERVER_PID" 2>/dev/null
  fi

  [ -n "$WORKDIR" ] && rm -rf "$WORKDIR" 2>/dev/null
}

# ---- 一時ディレクトリ・trap 設置 ----

umask 077
WORKDIR=""
SERVER_PID=""
WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/codex-rate-limit.XXXXXX" 2>/dev/null)
if [ -z "$WORKDIR" ] || [ ! -d "$WORKDIR" ]; then
  fail "一時ディレクトリの作成に失敗しました。"
fi

FIFO="$WORKDIR/fifo"
OUT="$WORKDIR/out"
ERR="$WORKDIR/err"

trap cleanup EXIT
# シグナル受信時は明示的に exit する (bash はシグナル trap が普通の関数呼び出しで終わると
# 中断箇所から実行を再開してしまうため)。exit すれば上の EXIT trap が cleanup を実行する。
trap 'exit 1' HUP INT TERM

# ---- codex app-server 起動 ----
#
# `exec 3<>fifo` は使わない (O_RDWR の移植性問題と、自己保有 read 端による EOF/SIGPIPE
# 隠蔽があるため。docstring 「プロセスライフサイクル設計」参照)。

mkfifo "$FIFO" || fail "fifo の作成に失敗しました ($FIFO)。"

codex app-server < "$FIFO" > "$OUT" 2> "$ERR" &
SERVER_PID=$!

if ! exec 3> "$FIFO"; then
  fail "codex app-server への writer fd を開けませんでした。"
fi

# サーバ起動後のみ SIGPIPE を無視する (起動前に無視すると codex 側に継承されてしまうため)。
# 無視することで、読み手が消えた後の printf >&3 は SIGPIPE 即死ではなく EPIPE エラーとして
# 検知でき、fail() で明示的な exit 1 に変換できる。
trap '' PIPE

# ---- RPC 送信 (initialize → initialized 通知 → account/rateLimits/read) ----
#
# 応答を待たず 3 メッセージを送り切る (docstring の RPC シーケンス参照)。id:1 の error 応答は
# poll ループ側で検出して即失敗にする。

send_rpc() {
  printf '%s\n' "$1" >&3 2>/dev/null
}

INIT_MSG='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"rate-limit-plugin","version":"0.3.0"}}}'

send_rpc "$INIT_MSG" || fail "initialize リクエストの送信に失敗しました (writer fd への書き込みエラー)。"
send_rpc '{"jsonrpc":"2.0","method":"initialized"}' || fail "initialized 通知の送信に失敗しました (writer fd への書き込みエラー)。"
send_rpc '{"jsonrpc":"2.0","id":2,"method":"account/rateLimits/read"}' || fail "account/rateLimits/read リクエストの送信に失敗しました (writer fd への書き込みエラー)。"

# ---- 応答 poll (0.5 秒 × 60 回 = 最大 30 秒) ----
#
# out ファイルを毎回先頭から走査し、id:2 の応答行 (result | error) または id:1 / id:null の
# error 応答を探す。書きかけの行 (jq で parse できない) は再試行扱いにして無視する。

RESULT_JSON=""
ERROR_MSG=""

# 完成した JSONL 1 行を検証・分類する。戻り値: 0 = id:2 の result を検出 (RESULT_JSON に
# 格納) / 1 = この行は対象外 (通知・対象外 id 等、走査継続) / 2 = 即失敗すべき RPC error を
# 検出 (ERROR_MSG に格納) / 3 = JSON として parse できない (JSONL プロトコル違反)。
inspect_complete_line() {
  local line="$1" id err_msg result_json

  [ -n "$line" ] || return 1

  # 完成行の parse 判定には `jq empty` を使う (`jq -e '.'` は valid JSON の false / null
  # でも非ゼロになるため使わない)。app-server の stdout は JSONL 契約であり、完成した
  # 非 JSON 行はプロトコル違反 = 「応答が JSON として解釈できない」に分類する。
  if ! printf '%s' "$line" | jq empty >/dev/null 2>&1; then
    return 3
  fi

  id=$(printf '%s' "$line" | jq -r 'if type == "object" then (.id | if . == null then "null" else tostring end) else empty end' 2>/dev/null)
  [ -n "$id" ] || return 1

  if [ "$id" = "2" ]; then
    err_msg=$(printf '%s' "$line" | jq -r '.error.message // empty' 2>/dev/null)
    if [ -n "$err_msg" ]; then
      ERROR_MSG="account/rateLimits/read が RPC エラーを返しました: $err_msg"
      return 2
    fi
    result_json=$(printf '%s' "$line" | jq -c '.result // empty' 2>/dev/null)
    if [ -z "$result_json" ] || [ "$result_json" = "null" ]; then
      ERROR_MSG="account/rateLimits/read の応答に result も error も含まれていません。"
      return 2
    fi
    RESULT_JSON="$result_json"
    return 0
  fi

  if [ "$id" = "1" ] || [ "$id" = "null" ]; then
    err_msg=$(printf '%s' "$line" | jq -r '.error.message // empty' 2>/dev/null)
    if [ -n "$err_msg" ]; then
      if [ "$id" = "1" ]; then
        ERROR_MSG="initialize が RPC エラーを返しました: $err_msg"
      else
        ERROR_MSG="RPC エラー応答を受信しました (id: null): $err_msg"
      fi
      return 2
    fi
  fi

  return 1
}

# out を走査する。引数: $1 = "final" なら末尾の改行なし断片も完成行として検証する
# (polling 終了時の最終走査。通常 poll では書きかけとして無視し、これ以上完成しない
# 局面でのみ分類する — codex review 指摘 + rescue 壁打ちで確定)。
# 戻り値: 0 = id:2 の result を検出 / 1 = 該当する応答なし (poll 継続) /
# 2 = 即失敗すべき RPC error を検出 / 3 = JSONL プロトコル違反 (非 JSON 行)。
check_out() {
  local final_scan="${1:-}" line rc

  line=""
  # 改行で完結した行のみ read が 0 を返す。完成行は即検証し、malformed (rc 3) も
  # ここで即座に呼び出し元へ返す (30 秒の無駄な timeout 待ちを避ける)。
  while IFS= read -r line; do
    inspect_complete_line "$line"
    rc=$?
    [ "$rc" -eq 1 ] || return "$rc"
    line=""
  done < "$OUT"

  # ループ後の $line には改行で終わっていない末尾断片が残る (無ければ空)。通常 poll では
  # 書きかけとして無視するが、最終走査 (サーバ死亡・期限到達後) ではこれ以上完成しない
  # ため、完成行と同じ基準で検証する。
  if [ -n "$final_scan" ] && [ -n "$line" ]; then
    inspect_complete_line "$line"
    rc=$?
    [ "$rc" -eq 1 ] || return "$rc"
  fi

  return 1
}

MAX_ATTEMPTS=60
STATUS=""
attempt=0

while [ "$attempt" -lt "$MAX_ATTEMPTS" ]; do
  check_out
  rc=$?
  if [ "$rc" -eq 0 ]; then
    STATUS="ok"
    break
  elif [ "$rc" -eq 2 ]; then
    STATUS="rpc-error"
    break
  elif [ "$rc" -eq 3 ]; then
    STATUS="malformed"
    break
  fi

  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    # サーバ死亡を検出。直前に応答を flush している可能性があるため out をもう一度
    # (最終走査として、末尾断片も含めて) 確認してから失敗として分類する。
    check_out final
    rc=$?
    if [ "$rc" -eq 0 ]; then
      STATUS="ok"
    elif [ "$rc" -eq 2 ]; then
      STATUS="rpc-error"
    elif [ "$rc" -eq 3 ]; then
      STATUS="malformed"
    else
      STATUS="server-died"
    fi
    break
  fi

  attempt=$((attempt + 1))
  sleep 0.5
done

if [ -z "$STATUS" ]; then
  # 最終 iteration の sleep 中に応答が到着した可能性があるため、timeout に分類する前に
  # もう一度 out を最終走査 (末尾断片も検証) する (codex review 指摘)。該当なしの場合は
  # サーバ生存状態で server-died / timeout を分類し、診断メッセージの一貫性を保つ。
  check_out final
  rc=$?
  if [ "$rc" -eq 0 ]; then
    STATUS="ok"
  elif [ "$rc" -eq 2 ]; then
    STATUS="rpc-error"
  elif [ "$rc" -eq 3 ]; then
    STATUS="malformed"
  elif ! kill -0 "$SERVER_PID" 2>/dev/null; then
    STATUS="server-died"
  else
    STATUS="timeout"
  fi
fi

case "$STATUS" in
  ok)
    : # 正常終了。後続の validation へ進む
    ;;
  rpc-error)
    fail "$ERROR_MSG"
    ;;
  server-died)
    if [ -s "$ERR" ]; then
      printf '[rate-limit] codex app-server の stderr (末尾):\n' >&2
      tail -n 20 "$ERR" >&2
    fi
    fail "codex app-server が応答前に終了しました。"
    ;;
  malformed)
    fail "codex app-server の応答が JSON として解釈できません (JSONL に非 JSON 行を検出)。"
    ;;
  timeout)
    fail "30 秒以内に account/rateLimits/read の応答がありませんでした (timeout)。"
    ;;
esac

# ---- usedPercent の validation ----
#
# rateLimitsByLimitId は nullable なので、必須フィールド検証は backward-compatible な
# rateLimits 側 (limitId `codex` の primary) で行う (docstring 判定仕様参照)。

USED_PERCENT=$(printf '%s' "$RESULT_JSON" | jq -r '(.rateLimits.primary.usedPercent | select(type == "number")) // empty' 2>/dev/null)
if [ -z "$USED_PERCENT" ]; then
  fail "応答に rateLimits.primary.usedPercent (0-100 の数値) が含まれていません。"
fi
if ! awk -v v="$USED_PERCENT" 'BEGIN { exit !(v >= 0 && v <= 100) }'; then
  fail "rateLimits.primary.usedPercent が 0-100 の範囲外です (値: $USED_PERCENT)。"
fi

# secondary は window 全体が nullable (欠損 / null は正常)。ただし非 null で存在する場合、
# RateLimitWindow.usedPercent はプロトコル上の必須フィールドのため、欠損・非数値・範囲外は
# 応答不正として exit 1 にする。plan によっては primary が 5h 窓・secondary が週次窓の構成が
# ありえ、壊れた secondary を黙って無視すると週次超過を見逃す fail-open になる (codex review
# 指摘 + rescue 壁打ちで確定した tri-state 検証)。
# 存在判定に jq の `//` を使わない: `//` は false と null を両方 falsy として扱うため、
# malformed 応答で secondary が boolean false 等になった場合に「欠損」扱いで検証を
# スキップし fail-open になる (codex review 指摘)。null / 欠損のみを「無し」とし、
# falsy を含む非 null 値はすべて後続の usedPercent 必須検証 (fail-closed の受け皿) に流す。
SECONDARY_USED_PERCENT=""
if [ "$(printf '%s' "$RESULT_JSON" | jq -r '.rateLimits.secondary | if . == null then "no" else "yes" end' 2>/dev/null)" = "yes" ]; then
  SECONDARY_USED_PERCENT=$(printf '%s' "$RESULT_JSON" | jq -r '(.rateLimits.secondary.usedPercent | select(type == "number")) // empty' 2>/dev/null)
  if [ -z "$SECONDARY_USED_PERCENT" ]; then
    fail "応答の rateLimits.secondary に usedPercent (0-100 の数値) が含まれていません (secondary 窓が存在する場合は必須)。"
  fi
  if ! awk -v v="$SECONDARY_USED_PERCENT" 'BEGIN { exit !(v >= 0 && v <= 100) }'; then
    fail "rateLimits.secondary.usedPercent が 0-100 の範囲外です (値: $SECONDARY_USED_PERCENT)。"
  fi
fi

# ---- --max-used-percent 判定 (指定時のみ。usedPercent は小数がありうるため awk で数値比較) ----
#
# primary に加え、secondary 窓が存在する場合はそれも同じ閾値で判定する (いずれかが N 超で
# exit 2)。判定ゲートは MAX_USED_PERCENT_SET (フラグ指定の有無) であり、値の空文字判定に
# 依存しない。

EXIT_CODE=0

if [ -n "$MAX_USED_PERCENT_SET" ]; then
  # nullness を固定 sentinel ("null" / "non-null") で判定する。`// empty` + `[ -n ]` は
  # false や空文字の rateLimitReachedType を「未到達」として隠すため使わない (codex review
  # 指摘)。契約は「null = 未到達、非 null = 到達済み」であり、falsy を含む非 null 値は
  # すべて到達済み (exit 2) 側に倒す。
  REACHED_STATE=$(printf '%s' "$RESULT_JSON" | jq -r '.rateLimits.rateLimitReachedType | if . == null then "null" else "non-null" end' 2>/dev/null)
  if [ "$REACHED_STATE" = "non-null" ]; then
    EXIT_CODE=2
  elif awk -v v="$USED_PERCENT" -v n="$MAX_USED_PERCENT" 'BEGIN { exit !(v > n) }'; then
    EXIT_CODE=2
  elif [ -n "$SECONDARY_USED_PERCENT" ] && awk -v v="$SECONDARY_USED_PERCENT" -v n="$MAX_USED_PERCENT" 'BEGIN { exit !(v > n) }'; then
    EXIT_CODE=2
  fi
fi

# 取得成功時は判定結果 (exit 0/2) に依らず result を無加工で出力する。
printf '%s\n' "$RESULT_JSON"
exit "$EXIT_CODE"
