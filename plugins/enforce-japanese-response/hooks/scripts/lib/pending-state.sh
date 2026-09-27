#!/bin/bash
# pending-state.sh — 書き直し指示待ちの印とメッセージのバッファーの管理
# (source して使う。直接実行しない)
#
# MessageDisplay hook (record-english-message.sh) が英語のメッセージを表示したことを
# 記録し、PostToolBatch hook (request-japanese-rewrite.sh) がそれを読んで書き直しを
# 指示し、Stop hook (enforce-japanese-response.sh) が turn の終わりに印を消す。
# 3 つの hook が同じ場所・同じ形式で状態を読み書きするため、状態の場所と形式を
# ここ以外に書かない。
#
# ## 状態ディレクトリー
#
#   ${TMPDIR:-/tmp}/enforce-japanese-response/<session_id>/
#     pending               英語のメッセージを表示したが、まだ書き直しを指示していない
#                           ことを表す印。内容は英語と判定したときの入力の `prompt_id`
#                           を末尾の改行なしでそのまま書いたもの (`prompt_id` が文字列で
#                           なければ空)
#     buffers/<message_id>  MessageDisplay の `delta` を届いた順に連結したバッファー
#
# - env `TMPDIR` が未設定または空文字列なら `/tmp` を使う
# - ディレクトリー・ファイルは所有者のみ読み書きできる権限 (umask 077) で作る
# - `session_id` と `message_id` はパスの 1 要素としてそのまま使うため、
#   STATE_ID_JQ_DEF の is_valid_state_id を満たす値だけを渡す。呼び出し側は満たさない
#   値を受け取ったら状態を読み書きせずに終える
# - id の検査は、入力 JSON の値に対して jq の中で行う。シェルのコマンド置換で取り出した
#   後の値は末尾の改行が落ちているため、取り出した後に検査すると `abc\n` のような値を
#   通してしまう
#
# bash と jq のみに依存し、Linux (WSL2) と macOS (bash 3.2 / BSD ツール) の両方で動く
# ように書く。
#
# ## 提供する定義と関数 (I/O 契約)
#
# - STATE_ID_JQ_DEF
#     呼び出し側の jq プログラムの先頭に連結して使う jq の関数定義。定義する
#     `is_valid_state_id` は、入力が文字列で、正規表現 `^[A-Za-z0-9_-]{1,128}$` に文字列
#     全体で一致すれば true、それ以外 (文字列でない値を含む) は false を返す。改行を
#     含む値は一致しない (`$` は末尾の改行の直前にも一致するため、文字列の先頭・末尾に
#     だけ一致する `\A` と `\z` で書く)
# - session_state_dir <session_id>
#     状態ディレクトリーのパスを 1 行で出力する。ディレクトリーは作らない
# - mark_pending <session_id> <prompt_id>
#     状態ディレクトリーを必要なら作り、`pending` の内容を <prompt_id> で置き換える
# - read_pending <session_id>
#     `pending` があればその内容を出力して終了ステータス 0、無ければ何も出力せず非 0
#     を返す
# - clear_pending <session_id>
#     `pending` があれば削除する。無くても失敗しない
# - reset_message_buffer <session_id> <message_id> <delta>
#     `buffers/<message_id>` の既存内容を捨て、<delta> だけを内容とする
# - append_message_buffer <session_id> <message_id> <delta>
#     `buffers/<message_id>` の末尾に <delta> を追記する。無ければ <delta> で作る
# - take_message_buffer <session_id> <message_id>
#     `buffers/<message_id>` の内容をそのまま stdout に出力し、バッファーを削除する。
#     バッファーが無ければ何も出力しない

STATE_ID_JQ_DEF=''

session_state_dir() {
  :
}

mark_pending() {
  :
}

read_pending() {
  return 1
}

clear_pending() {
  :
}

reset_message_buffer() {
  :
}

append_message_buffer() {
  :
}

take_message_buffer() {
  :
}
