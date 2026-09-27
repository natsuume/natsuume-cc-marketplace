#!/bin/bash
# record-english-message.sh
# 表示された assistant のメッセージが英語で書かれていたら、書き直し指示待ちの印
# (pending) を残す MessageDisplay hook。
#
# MessageDisplay は表示専用のイベントで、Claude に何も返せない。そのため、この hook は
# 英語のメッセージを検知したことを状態ディレクトリーに記録するだけにし、書き直しの
# 指示は次の PostToolBatch hook (request-japanese-rewrite.sh) が出す。tool 呼び出しの
# 合間に表示されるメッセージも turn 末尾の応答も、表示されるすべての assistant の
# テキストがこの hook に届く。
#
# 目標言語の解決と英語の判定は lib/language-judgement.sh、状態の場所と形式は
# lib/pending-state.sh の契約に従う。以下はこの hook の I/O 契約である。
#
# ## I/O 契約
#
# - 入力: MessageDisplay hook の stdin JSON。使うキーは次のとおり
#   - `session_id`: 状態ディレクトリーを決める
#   - `prompt_id` (任意): pending に書く。文字列でなければ空文字列として扱う
#   - `cwd`: env `CLAUDE_PROJECT_DIR` が空のとき、プロジェクトの settings を探す
#     基準ディレクトリ
#   - `message_id`: バッファーを決める。同じメッセージのすべての batch で同じ値になる
#   - `index`: メッセージ内の batch の番号 (0 始まりの整数)
#   - `final`: メッセージの最後の batch なら true
#   - `delta`: 前の batch 以降に確定したテキスト
#   - `agent_id`: subagent の中で発火したときだけ付く
# - 環境変数: `CLAUDE_PROJECT_DIR`、`HOME`、`TMPDIR`
# - 出力: stdout には何も出力しない (出力すると表示が置き換わるため)
# - exit code: どの場合も 0
#
# ## 処理
#
# 1. `index` が 0 なら、バッファーの既存内容を捨てて `delta` だけを内容とする。
#    それ以外なら、バッファーの末尾に `delta` を追記する
# 2. `final` が true なら、バッファー全体をメッセージ本文として取り出し、バッファーを
#    削除する。続けて目標言語を解決し、目標言語が日本語で、かつ本文が英語と判定されたら
#    pending を作る (内容は `prompt_id`)。目標言語の解決は `final` が true のときだけ行う
#
# 非対話の実行 (`claude -p`、Agent SDK) では、メッセージ全体が `index` 0・`final` true
# の 1 回の呼び出しで届く。この場合も 1 と 2 を同じ順に行う。
#
# ## 何もしない条件 (いずれも無出力で exit 0。状態を読み書きしない)
#
# 1. jq が無い (`command -v jq` が失敗する) → 判定できないため何もしない (fail-open)
# 2. stdin を JSON object として解析できない → 何もしない (fail-open)
# 3. `agent_id` が空でない文字列 → subagent のメッセージは対象外
# 4. `session_id` または `message_id` が無い・文字列でない・`^[A-Za-z0-9_-]{1,128}$` に
#    一致しない → パスに使えないため何もしない
# 5. `delta` が文字列でない、`index` が 0 以上の整数でない、`final` が bool でない
#    → 何もしない
# 6. 目標言語が日本語でない・未設定、または本文が英語でない → バッファーの削除だけを
#    行い、pending は作らない
# 7. 途中の jq 呼び出しやファイル操作が失敗した等、処理を完了できない → そこで終える
#    (fail-open)
#
# ## 実行環境
#
# bash と jq のみに依存し、Linux (WSL2) と macOS (bash 3.2 / BSD ツール) の両方で動く
# ように書く。lib はこの script の場所を基準に `"${BASH_SOURCE[0]%/*}/lib/..."` で
# source する。

exit 0
