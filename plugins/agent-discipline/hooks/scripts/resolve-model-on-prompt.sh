#!/bin/bash
# resolve-model-on-prompt.sh
# UserPromptSubmit で発火する one-shot 補正 (#175)。SessionStart 時点でモデル判定不能だった
# session (inject-always.sh が判定不能分岐で作成した pending マーカーが残っている session) に
# 対し、会話が進んで transcript に main-chain assistant 行が現れた最初のタイミングでモデルを
# 確定し、確定版プロンプトを 1 度だけ再送する。
#
# ## 発火条件
#
# pending マーカー `${TMPDIR:-/tmp}/agent-discipline-state/pending-model-<session_id>`
# (session_id は inject-always.sh と同じ sanitize 方式 `tr -cd 'A-Za-z0-9._-'`) が存在する
# session に限る。マーカーが無ければ即 exit 0 (通常時のオーバーヘッドをマーカー存在チェック
# 1 回に抑える)。
#
# ## transcript 解析
#
# pending マーカーが存在する場合のみ、hook input の `.transcript_path` に対し
#   jq -r 'select(.type=="assistant" and .isSidechain != true) | .message.model // empty' \
#     | tail -n 1
# を実行し、最後の main-chain assistant 行のモデル ID を取得する。
#
# ## 分岐
#
# - pending マーカーなし → 即 exit 0
# - pending マーカーあり + transcript にまだ main-chain assistant 行が無い (上記コマンドの
#   結果が空) → 何もしない (pending マーカーは残したまま exit 0。次回 UserPromptSubmit で再試行)
# - pending マーカーあり + assistant 行あり → モデルを確定し:
#   - モデル ID (小文字化) が `fable` を含む → 確定版 (hooks/prompts/always-fable.md) を
#     「以後この確定版を優先し、セッション冒頭の自己ゲート付き注入は破棄する」という前置きと
#     ともに additionalContext で 1 度だけ注入する
#   - それ以外 (sonnet / opus / haiku 等。自己ゲート時に always-sonnet.md を注入済みと同内容) →
#     再注入しない (出力なしで exit 0)
#   - いずれの場合も: state file
#     `${TMPDIR:-/tmp}/agent-discipline-state/model-<session_id>` に確定値を書き込んだ後で
#     pending マーカーを削除する (state file 書込 → pending マーカー削除の順で行い、TOCTOU の
#     隙間を作らない。#155 の教訓)
#
# ## 出力 JSON 形状 (再注入する場合のみ)
#
#   {
#     "hookSpecificOutput": {
#       "hookEventName": "<入力の hook_event_name をそのまま echo>",
#       "additionalContext": "<前置き + always-fable.md 本文>"
#     }
#   }
#
# 再注入しない分岐 (pending マーカーなし / assistant 行なし / 確定版が always-sonnet.md) では
# 何も出力せず exit 0 する。
#
# ## fail-open 条件
#
# - jq 不在
# - stdin が不正 JSON / hook_event_name が空
# - transcript_path が読めない
# - always-fable.md が読めない (空文字列を含む)
# - state file / pending マーカーの読み書き失敗
#
# 実装本体は #175 Phase A 時点では no-op (exit 0 のみ) とする。本コメントブロックは Phase B で
# 実装する one-shot 補正の設計を文書化したものであり、現時点では何もしない。

exit 0
