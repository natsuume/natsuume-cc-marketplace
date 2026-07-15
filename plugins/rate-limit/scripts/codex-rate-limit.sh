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
#         応答が JSON 解釈不能 / rateLimits.primary.usedPercent 欠損 / N の validation 違反)
#     2 = --max-used-percent 指定時のみ: usedPercent が N 超、または
#         rateLimitReachedType が非 null (到達済み)。いずれの場合も JSON は出力する
#
# ## 判定仕様 (--max-used-percent <N>)
#
# - 対象は `.rateLimits.primary.usedPercent` (limitId `codex` の primary)。スキーマ上
#   `rateLimitsByLimitId` は nullable のため、必須フィールド検証・判定は backward-compatible
#   な `rateLimits` 側で行う
# - `rateLimitReachedType` が非 null → exit 2 (usedPercent の値に依らず)
# - usedPercent > N → exit 2 / usedPercent <= N → exit 0 (usedPercent は小数でありうる)
# - N の validation: 10 進整数かつ 0 <= N <= 100。違反は usage を stderr に出して exit 1。
#   `--max-used-percent` 以外の引数・値の欠落も usage + exit 1
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
#      書きかけの JSON 行を parse failure と誤認しないよう、poll 中の jq 失敗は再試行に
#      倒し、期限到達・サーバ死亡時のみ最終分類する
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
# Phase A (設計記述 commit): 上記が確定仕様。実装本体は Phase B で追加する。
# ---------------------------------------------------------------------------

echo "[rate-limit] codex-rate-limit.sh は未実装です (issue #245 Phase A)。" >&2
exit 1
