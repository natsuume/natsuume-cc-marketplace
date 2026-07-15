#!/bin/bash
# run-codex-implementer.sh
# codex-implementer plugin の **実装委任実行 wrapper** (issue #247、親 issue #246 Phase 1)。
# `codex-implementer` subagent が Bash tool 経由で本 wrapper を foreground 起動し、
# 委任する実装タスクのプロンプトを stdin から渡す。wrapper は委任実行の**前に**
# rate-limit ガード (fail-closed) を行い、通過した場合のみ codex companion の
# `task --write --model <model> --effort xhigh` を foreground 実行する。
#
# ## I/O 契約 (issue #247 で確定)
#
# - stdin: 委任する実装タスクのプロンプト全文をファイルからの stdin リダイレクト
#   (`< "/path/to/prompt.md"`) で渡す。引数は一切受け取らない (プロンプトを argv に
#   乗せない設計。shell quoting 事故・history 露出・guardrail 誤 deny を避けるため
#   stdin 経由に統一する。run-codex-advisor.sh と同じ設計)
# - stdout: codex companion の stdout (codex の最終メッセージ・変更ファイルの情報) を
#   そのまま流す。wrapper はこれを一切加工しない
# - stderr: wrapper 自身の状態 (ガード判定・使用率・実行コマンド・完了/失敗理由)
# - exit code: 0 = 委任完了 / 1 = 失敗。失敗クラスは stderr のメッセージで区別する:
#     (a) ガード超過: codex の週次枠使用率が閾値超過、または rateLimitReachedType 到達済み
#         (使用率と reset 時刻を含む理由を出力)
#     (b) fail-closed: 使用率が確認できない (codex-status が exit 1) — 「使用率が確認
#         できないため委任を中止した (Claude 本体での実装に切替を推奨)」の旨。(a) と
#         区別できる文言にする
#     (c) rate-limit plugin 未 install: codex-status script が解決できない — fail-closed +
#         install 案内 (`claude plugin install rate-limit@natsuume-plugins`)
#     (d) 実行環境不備: Node.js 不在 / codex companion 未検出 / stdin 未指定 / 空プロンプト
#     (e) companion 失敗: codex CLI 未インストール・未認証等 (`/codex:setup` を案内)
#     (f) 時間超過: work cutoff までに job が完了しなかった。中断の確認状態で文言を
#         区別する — turn/interrupt RPC の成功確認済み、または安定識別語
#         `turn interruption unconfirmed` を含む未確認警告 (処理フロー 5-d 参照)
#
# ## 処理フロー
#
#   0. 時間予算の設定 (すべて wrapper 起動時刻起点の絶対時刻。rescue 壁打ち 2026-07-15 で確定):
#      - **hard deadline = 570 秒**: wrapper が必ず終了する上限。Bash tool の timeout
#        (600 秒、到達時はコマンドを kill せず background 移行する) より確実に短くする
#      - **work cutoff = 540 秒**: job の status poll を打ち切り cancel を開始する時刻。
#        hard deadline との差 30 秒は「cancel RPC 完了待ち 20 秒 + cancel プロセスの
#        TERM/KILL/reap 最大 4 秒 + JSON 検査・診断出力 6 秒」の固定予算として予約する
#        (poll を hard deadline まで続けると cancel の実行予算が残らないため、二段階の
#        時刻が必須)。前段のガード (最大 30 秒) 等もすべて同じ絶対時計で消費される
#   1. 引数チェック ($# != 0 → usage + exit 1) / Node.js チェック / stdin TTY チェック →
#      プロンプト読み込み → 空 (空白のみ) チェック
#   2. 設定読み込み: カレントディレクトリの `.claude/codex-implementer.local.md` の
#      YAML frontmatter (後述) から model / ガード閾値を読む
#   3. rate-limit ガード: lib/rate-limit-status-resolver.sh で rate-limit plugin の
#      `scripts/codex-rate-limit.sh` を解決し、`--max-used-percent <閾値>` で foreground
#      実行する。codex-status の exit code 契約 (0 = 通過 / 1 = 取得失敗 / 2 = 超過・
#      到達済み) に従い分岐する:
#        - exit 0 → ガード通過。stdout (JSON) から usedPercent を stderr の状態表示に使う
#        - exit 2 → (a) 委任拒否。拒否の判断材料をすべて stderr に出して exit 1:
#          設定閾値 (max_used_percent)、rateLimits.primary の usedPercent / resetsAt
#          (epoch 秒。人間可読へ変換して添える)、rateLimits.secondary が存在する場合は
#          その usedPercent / resetsAt も、rateLimitReachedType が非 null の場合はその値も
#          表示する (空文字等の falsy 値でも「到達済み」であることが読み取れる表現を使う)。
#          secondary 窓の超過でも exit 2 になるため、primary のみの表示では「閾値未満の
#          数値を出しながら拒否する」誤解を招く — 判断材料の全報告で要因を自明にする。
#          どの窓が要因かの特定ロジックは wrapper に持たせない (判定は codex-status 側の
#          責務。wrapper 側での判定再実装は二重実装になる)
#        - exit 1 → (b) fail-closed。委任せず exit 1
#        - その他の exit code → 契約外だが安全側 (fail-closed) に倒して exit 1
#      script が解決できない場合は (c)。ガードは wrapper 内で完結し、通過しない限り
#      companion 起動経路に到達しない (構造的 fail-closed)
#   4. companion 解決: lib/codex-companion-resolver.sh (pre-push-review / codex-advisor と
#      同一内容のコピー。plugin 間でファイル共有ができないため各 plugin が自前で持つ) で
#      codex-companion.mjs を解決する
#   5. 実行 (job 監督モデル): Bash tool から見ると wrapper は foreground 1 回のままだが、
#      wrapper 内部では companion の job 管理インタフェースで委任を監督する。wrapper は
#      起動時に一意の相関 ID を生成して `CODEX_COMPANION_SESSION_ID` 環境変数に設定し、
#      **task / status / result / cancel の全 companion 呼び出しに同じ値を渡す** (companion
#      は job 作成時にこれを sessionId として記録し status もこれで filter するため、
#      後述の recovery の本人性確立に使える):
#        a. `printf '%s' "$PROMPT" | node "$COMPANION" task --background --json --write
#           --model "$MODEL" --effort xhigh` で job を起動し、**JSON payload の `.jobId`**
#           を厳密に取得する (--json は companion 1.0.6 の usage には非表示だが handleTask
#           が受理し jobId を含む payload を返すことを実装確認済み。人間可読テキストの
#           parse はしない)。jobId を取得できない場合は recovery を試みる:
#           `node "$COMPANION" status --all --json` の running 一覧から
#           「sessionId == 自分の相関 ID かつ kind == "task" かつ write == true かつ
#           workspaceRoot 一致かつ createdAt >= wrapper の job 起動時刻」の候補を検索し、
#           **候補が厳密に 1 件の場合のみ** その job ID を採用して以降の監督に回す。
#           0 件・複数件・status 呼び出し失敗・JSON 不正はすべて「ID 不明の enqueue 済み
#           job が存在しうる」として、安定識別語 `turn interruption unconfirmed` を含む
#           警告 (「write job が起動済みの可能性があるが job ID を特定できなかった。
#           worktree の状態と active な codex job (status --all) を確認するまで代替実装を
#           開始しないこと」) を stderr に出して exit 1 (複数候補から最新 1 件を選ぶ等の
#           近似はしない — 別 job の誤 cancel を防ぐ)。recovery 自体も hard deadline 内で
#           行う。時刻比較は epoch 秒に正規化して行う (companion の createdAt は ms 付き
#           ISO、BSD date は秒精度のため、ISO 文字列の単純比較はしない)
#        b. `node "$COMPANION" status <job-id> --json` を数秒間隔で poll し、job の完了を
#           **work cutoff** (工程 0) まで待つ。待機中は経過を stderr に間欠出力する
#        c. job 完了 → `node "$COMPANION" result <job-id>` の stdout (codex の最終
#           メッセージ・変更ファイル情報) をそのまま流し、job の成否に応じて exit 0/1
#        d. work cutoff 到達 → **上限付き bounded cancel** を実行する:
#           `node "$COMPANION" cancel <job-id> --json` を background 起動し、cancel RPC
#           完了待ち予算 (20 秒) 内の完了を poll する。予算超過時は cancel プロセスを
#           TERM → grace → KILL → wait reap する (プロセス管理は codex-rate-limit.sh の
#           cleanup パターン。companion の RPC には request timeout が無く、broker が
#           応答しないと cancel 自体が無期限ブロックするため、cancel も監督対象とする)。
#           中断結果は cancel の --json 出力の `turnInterrupted` フィールドで判定する
#           (JSON boolean の true を厳密に要求する。truthy 判定・文字列 "true" は不可):
#           - cancel が予算内に完了し turnInterrupted == true → 「委任を時間超過で中断した
#             (turn/interrupt RPC の成功を確認済み)。10 分以内に完了しない実装タスクは
#             委任に不適 — タスクを分割するか Claude 本体で実装する」を stderr に出して
#             exit 1
#           - turnInterrupted が true 以外 (false / 欠損 / null) / JSON 解釈不能 / cancel
#             非ゼロ終了 / cancel 予算超過・強制終了 → すべて「未確認」に集約し、安定
#             識別語 **`turn interruption unconfirmed`** を含む警告を stderr に出して
#             exit 1:「委任を時間超過で中断したが、write turn の停止を確認できなかった。
#             worktree が並行変更され続けている可能性があるため、worktree の状態
#             (git status / 変更ファイル) と active turn を確認するまで代替実装を
#             開始しないこと」。中断確認済みの場合と明確に区別できる文言にする
#           (job status の "cancelled" は turn 停止の証拠にしない — companion 1.0.6 は
#            turn/interrupt を発行できない場合でも job を cancelled にするため)
#        e. HUP/INT/TERM trap でも同じ bounded cancel + 判定を実行してから終了する
#           (wrapper の異常終了経路でも write turn を放置しない)。trap は再入防止フラグで
#           二重実行を防ぎ、cleanup は idempotent に書く (cancel subprocess の PID が
#           設定済みの場合のみ TERM/KILL/reap する)
#
# ## 設定 (.claude/codex-implementer.local.md の YAML frontmatter)
#
#   ---
#   model: gpt-5.6-luna
#   max_used_percent: 50
#   ---
#
# - `model` (既定: `gpt-5.6-luna`): companion の task --model に渡す値。空文字・未設定は既定値
# - `max_used_percent` (既定: `50`): ガード閾値。整数でない・0〜100 の範囲外の値は
#   **既定値 50 を使い stderr に警告** (設定不備で委任が止まらないよう、拒否ではなく
#   既定値 fallback。ガード自体は必ず実行されるため fail-open にはならない)
# - settings ファイル不在 → すべて既定値で動作 (警告なし)
# - frontmatter の parse は sed/grep による単純な行抽出 (plugin-settings パターン)。
#   値の引用符 (`"..."`) は除去する
#
# ## 設計判断
#
# - **effort は `xhigh` 固定**: companion が現在受理する上限。max は Phase 2 (#248)、
#   ultra は対象外 (luna 非対応)。未サポート model×effort ペア送出によるハング
#   (openai/codex#31552) を避けるため、effort は設定可能にしない
# - **`--write` を付ける**: 本 wrapper は実装委任が目的であり、codex がファイル変更を
#   行うことが前提。read-only の相談は codex-advisor が担う (役割分離)
# - **companion を PID kill ではなく job cancel で止める**: companion (1.0.6) は Codex turn
#   を detached broker で実行するため、foreground の Node プロセスを TERM/KILL しても
#   write turn が停止する保証がない。時間超過時に PID kill のみで戻ると、親 session が
#   fallback 実装を始めた worktree を残存 turn が並行変更する競合が生じる (P1)。companion
#   の job 管理 (task --background / status / result / cancel) を使い、中断をアプリケーション
#   レベルで発行・確認する (rescue 壁打ち 2026-07-15 で確定)。`task --background` の起動
#   プロセスは起動確認だけで終了する短命プロセスのため、Bash tool の timeout 挙動 (kill
#   せず background 移行) に依存する経路も構造的に消える。ただし cancel は中断の**試行**
#   であり保証ではない — turn/interrupt が確認できない経路は隠さず
#   `turn interruption unconfirmed` として親に警告する (不確実性を silent にしない、
#   fail-closed の情報版)
# - **git 状態 (branch / dirty tree) を検査しない**: git-guardrails 等の既存 hook に委ねる
#   (issue #247 で確定)
# - **ガードの閾値判定は codex-status 側に委譲**: wrapper は exit code (0/1/2) だけで
#   分岐し、JSON の解釈は表示用に限る。判定ロジックの二重実装を避ける
#
# ## 制約
#
# - Linux (WSL2) / macOS (bash 3.2 / BSD ツール) の両方で動作すること。bash 4+ 拡張・
#   GNU 専用オプションは使わない
# - jq 必須 (拒否メッセージの usedPercent / resetsAt 抽出に使用。不在なら値の抽出を
#   省略して拒否理由のみ出す — ガード分岐自体は exit code 依存のため jq 不在でも機能する)
#
# ---------------------------------------------------------------------------
# Phase A (設計記述 commit): 上記が確定仕様。実装本体は Phase B で追加する。
# ---------------------------------------------------------------------------

echo "[codex-implementer] run-codex-implementer.sh は未実装です (issue #247 Phase A)。" >&2
exit 1
