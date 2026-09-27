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
#   ${TMPDIR:-/tmp}/enforce-japanese-response-<uid>/<session_id>/
#     pending               英語のメッセージを表示したが、まだ書き直しを指示していない
#                           ことを表す印。内容は英語と判定したときの入力の `prompt_id`
#                           を末尾の改行なしでそのまま書いたもの (`prompt_id` が文字列で
#                           なければ空)
#     buffers/<message_id>  MessageDisplay の `delta` を届いた順に連結したバッファー
#
# - env `TMPDIR` が未設定または空文字列なら `/tmp` を使う
# - `<uid>` は `id -u` の値。数字だけでない (空を含む) 場合は状態を扱わない
# - 親 (`enforce-japanese-response-<uid>`)・セッションディレクトリー・`buffers` の
#   それぞれについて、symlink でない・ディレクトリーである・現在のユーザーが所有者で
#   ある、の 3 つを確かめてから使う。1 つでも満たさなければ、その下の状態を読み書き
#   しない。共有の一時ディレクトリーに他のユーザーが先回りして置いたディレクトリーや
#   symlink を通して、他の場所に書いたり他人の印を読んだりしないためである
# - 作るディレクトリー・ファイルは umask 077 で作り、作った (または既にある) ディレク
#   トリーは上の検査の後に `chmod 700` にする。親を検査してから子を作るため、親が
#   symlink なら子は作らない
# - `session_id` と `message_id` はパスの 1 要素としてそのまま使うため、
#   STATE_ID_JQ_DEF の is_valid_state_id を満たす値だけを渡す。呼び出し側は満たさない
#   値を受け取ったら状態を読み書きせずに終える
# - id の検査は、入力 JSON の値に対して jq の中で行う。シェルのコマンド置換で取り出した
#   後の値は末尾の改行が落ちているため、取り出した後に検査すると `abc\n` のような値を
#   通してしまう
#
# bash と jq と POSIX のコマンド (`id` `mkdir` `chmod` `rm` `cat`) のみに依存し、
# Linux (WSL2) と macOS (bash 3.2 / BSD ツール) の両方で動くように書く。所有者の検査は
# `stat` (GNU と BSD で書式が違う) ではなく `test -O` で行う。
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
#     状態ディレクトリーのパスを 1 行で出力する。ディレクトリーは作らず、検査もしない。
#     uid が数字だけでなければ何も出力せず非 0 を返す
# - mark_pending <session_id> <prompt_id>
#     状態ディレクトリーを必要なら作り、`pending` の内容を <prompt_id> で置き換える。
#     検査に通らなければ何も書かずに非 0 を返す
# - read_pending <session_id>
#     `pending` があればその内容を出力して終了ステータス 0、無い・検査に通らなければ
#     何も出力せず非 0 を返す
# - clear_pending <session_id>
#     `pending` があれば削除する。無い・検査に通らないときは何もしない (失敗しない)
# - reset_message_buffer <session_id> <message_id> <delta>
#     新しいメッセージの開始 (`index` 0) に使う。同じセッションの `buffers/` にある
#     <message_id> 以外のバッファーを削除してから、`buffers/<message_id>` の既存内容を
#     捨てて <delta> だけを内容とする。メッセージは 1 つずつ順に表示されるため、この
#     時点で残っている他のバッファーは `final` が届かなかったものである
# - append_message_buffer <session_id> <message_id> <delta>
#     `buffers/<message_id>` の末尾に <delta> を追記する。無ければ <delta> で作る
# - take_message_buffer <session_id> <message_id>
#     `buffers/<message_id>` の内容をそのまま stdout に出力し、バッファーを削除する。
#     バッファーが無い・検査に通らなければ何も出力しない

STATE_ID_JQ_DEF='def is_valid_state_id: type == "string" and test("\\A[A-Za-z0-9_-]{1,128}\\z");'

# ユーザーごとの親ディレクトリーのパスを出力する。uid が数字だけでなければ非 0。
state_root_dir() {
  local uid
  uid=$(id -u 2>/dev/null) || return 1
  case $uid in
    '' | *[!0-9]*) return 1 ;;
  esac
  printf '%s\n' "${TMPDIR:-/tmp}/enforce-japanese-response-$uid"
}

session_state_dir() {
  local session_id=$1
  local root
  root=$(state_root_dir) || return 1
  printf '%s\n' "$root/$session_id"
}

# symlink でなく、ディレクトリーで、現在のユーザーが所有者なら 0 を返す。
is_owned_directory() {
  local directory=$1
  [ ! -L "$directory" ] && [ -d "$directory" ] && [ -O "$directory" ]
}

# ディレクトリーを (無ければ) umask 077 で作り、検査に通れば 700 にする。
# 検査に通らなければ非 0 を返す。
prepare_owned_directory() {
  local directory=$1
  (umask 077 && mkdir -p "$directory") 2>/dev/null || return 1
  is_owned_directory "$directory" || return 1
  chmod 700 "$directory" 2>/dev/null
}

# 親とセッションディレクトリーを順に用意し、セッションディレクトリーのパスを出力する。
prepare_session_state_dir() {
  local session_id=$1
  local root
  root=$(state_root_dir) || return 1
  prepare_owned_directory "$root" || return 1
  prepare_owned_directory "$root/$session_id" || return 1
  printf '%s\n' "$root/$session_id"
}

# 既にある親とセッションディレクトリーが検査に通れば、セッションディレクトリーの
# パスを出力する。作らない。
existing_session_state_dir() {
  local session_id=$1
  local root
  root=$(state_root_dir) || return 1
  is_owned_directory "$root" || return 1
  is_owned_directory "$root/$session_id" || return 1
  printf '%s\n' "$root/$session_id"
}

# セッションディレクトリーと buffers を用意し、buffers のパスを出力する。
prepare_buffer_dir() {
  local session_id=$1
  local directory
  directory=$(prepare_session_state_dir "$session_id") || return 1
  prepare_owned_directory "$directory/buffers" || return 1
  printf '%s\n' "$directory/buffers"
}

mark_pending() {
  local session_id=$1 prompt_id=$2
  local directory
  directory=$(prepare_session_state_dir "$session_id") || return 1
  (umask 077 && printf '%s' "$prompt_id" >"$directory/pending") 2>/dev/null
}

read_pending() {
  local session_id=$1
  local directory
  directory=$(existing_session_state_dir "$session_id") || return 1
  [ -f "$directory/pending" ] || return 1
  cat "$directory/pending" 2>/dev/null
}

clear_pending() {
  local session_id=$1
  local directory
  directory=$(existing_session_state_dir "$session_id") || return 0
  rm -f "$directory/pending" 2>/dev/null
  return 0
}

reset_message_buffer() {
  local session_id=$1 message_id=$2 delta=$3
  local buffers buffer
  buffers=$(prepare_buffer_dir "$session_id") || return 1
  for buffer in "$buffers"/*; do
    [ -e "$buffer" ] || [ -L "$buffer" ] || continue
    [ "$buffer" = "$buffers/$message_id" ] && continue
    rm -f "$buffer" 2>/dev/null
  done
  (umask 077 && printf '%s' "$delta" >"$buffers/$message_id") 2>/dev/null
}

append_message_buffer() {
  local session_id=$1 message_id=$2 delta=$3
  local buffers
  buffers=$(prepare_buffer_dir "$session_id") || return 1
  (umask 077 && printf '%s' "$delta" >>"$buffers/$message_id") 2>/dev/null
}

take_message_buffer() {
  local session_id=$1 message_id=$2
  local directory buffer
  directory=$(existing_session_state_dir "$session_id") || return 0
  is_owned_directory "$directory/buffers" || return 0
  buffer="$directory/buffers/$message_id"
  [ -f "$buffer" ] || return 0
  cat "$buffer" 2>/dev/null
  rm -f "$buffer" 2>/dev/null
  return 0
}
