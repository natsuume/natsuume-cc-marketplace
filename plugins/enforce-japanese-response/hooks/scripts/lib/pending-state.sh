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
#     buffers/<message_id>/ メッセージごとのディレクトリー。中身は次のとおり
#       <index>             その batch の `delta`。<index> は 10 進の整数で、先頭に 0 を
#                           付けない
#       final               最後の batch の index (10 進の整数)
#       judged/             判定役を取った印。`mkdir` の成否を排他の手段に使う
#
# ## メッセージの断片と判定
#
# 対話モードでは、同じメッセージの MessageDisplay hook (最後の batch を含む) が並行して
# 走り、完了の順は index の順と限らない。そのため、batch ごとに断片を別のファイルに
# 書き、揃った時点で 1 つの hook だけが判定する:
# 1. 各 hook は自分の断片 `<index>` を書く。最後の batch なら続けて `final` を書く
# 2. 書いた後、`final` があり、その値 N について 0..N の断片がすべて揃っていれば
#    `mkdir judged` を試みる。成功した hook だけが判定役になる
# 3. 判定役は 0..N を index の順に連結して本文とし、判定と `pending` の作成を終えて
#    から、メッセージディレクトリーを丸ごと削除する
# 各 hook は断片を書いてから揃っているかを確かめるため、最後に断片を書いた hook が必ず
# 揃った状態を見る。判定の済んだメッセージディレクトリーは削除されているため、同じ
# message_id の batch が後から届くと、新しいメッセージの断片として扱う (0..N が揃わない
# 限り判定しない)。
#
# ファイルは同じディレクトリー内の一時名 (`.<名前>.tmp.<pid>`) に書いてから `mv` で
# 置き換える。書きかけのファイルを他の hook が読まないためである。
#
# ## 古いメッセージの掃除と待機
#
# - 最後の batch が届かなかったメッセージのディレクトリーは残る。`index` 0 の batch
#   (新しいメッセージの開始) を受けた hook が、同じセッションの `buffers/` 直下にあって
#   最終更新から 10 分を超えたメッセージディレクトリーを削除する。Stop hook は turn の
#   終わりに、そのセッションのメッセージディレクトリーをすべて削除する
# - PostToolBatch hook は `pending` を読む前に、同じセッションの `buffers/` 直下に最終
#   更新から 1 分以内のメッセージディレクトリーがある間は、0.1 秒ずつ最大 2 秒まで
#   待つ。未判定のもの (`judged/` が無い) に加えて、判定中のもの (`judged/` があり、
#   まだ削除されていない) も待つ。判定役は `pending` を書き終えてからディレクトリーを
#   削除するため、判定中のものを待たないと `pending` を書く前に読んでしまう
#
# - env `TMPDIR` が未設定または空文字列なら `/tmp` を使う
# - `<uid>` は `id -u` の値。数字だけでない (空を含む) 場合は状態を扱わない
# - 親 (`enforce-japanese-response-<uid>`)・セッションディレクトリー・`buffers`・
#   メッセージディレクトリーのそれぞれについて、symlink でない・ディレクトリーである・現在のユーザーが所有者で
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
# bash と jq と POSIX のコマンド (`id` `mkdir` `chmod` `mv` `rm` `rmdir` `cat` `find`
# `sleep`) のみに依存し、Linux (WSL2) と macOS (bash 3.2 / BSD ツール) の両方で動くように
# 書く。所有者の検査は `stat` (GNU と BSD で書式が違う) ではなく `test -O` で行う。
# `find` は GNU と BSD の両方にある `-mindepth` `-maxdepth` `-type d` `-mmin` だけを使い、
# symlink は辿らない。`sleep` の小数秒は GNU と macOS の両方で使える。
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
#     状態ディレクトリーを必要なら作り、`pending` の内容を <prompt_id> で置き換える
#     (一時名に書いてから `mv`)。検査に通らなければ何も書かずに非 0 を返す
# - read_pending <session_id>
#     `pending` があればその内容を出力して終了ステータス 0、無い・検査に通らなければ
#     何も出力せず非 0 を返す
# - clear_pending <session_id>
#     `pending` があれば削除する。無い・検査に通らないときは何もしない (失敗しない)
# - store_message_fragment <session_id> <message_id> <index> <delta> <is_final>
#     メッセージディレクトリーを必要なら作り、断片 `<index>` の内容を <delta> で置き
#     換える。<is_final> が `true` なら続けて `final` に <index> を書く。検査に通らない・
#     書けなければ非 0 を返す
# - claim_complete_message <session_id> <message_id>
#     `final` があり、その値 N について 0..N の断片がすべて揃っていれば `mkdir judged`
#     を試み、成功したら終了ステータス 0 を返す。揃っていない・mkdir に失敗した
#     (他の hook が判定役を取った)・検査に通らなければ非 0 を返す
# - read_message_text <session_id> <message_id>
#     断片 0..N (N は `final` の値) を index の順に連結して stdout に出力する
# - remove_message <session_id> <message_id>
#     メッセージディレクトリーを丸ごと削除する。検査に通らないときは何もしない
# - remove_stale_messages <session_id>
#     `buffers/` 直下にあって最終更新から 10 分を超えたメッセージディレクトリーを
#     削除する。`buffers` が無い・検査に通らないときは何もしない
# - wait_for_message_judgements <session_id>
#     `buffers/` 直下に最終更新から 1 分以内のメッセージディレクトリーがある間、
#     0.1 秒ずつ最大 20 回 (約 2 秒) 待つ。判定中のメッセージは直前に更新されている
#     ため 1 分以内に限れば足り、`final` が届かずに残ったメッセージで待ち続けない。
#     `buffers` が無い・検査に通らないときは待たない
# - remove_all_messages <session_id>
#     turn の終わりに使う。`buffers/` 直下のメッセージディレクトリーをすべて削除する。
#     turn 末尾のメッセージは Stop hook が自分で判定するため、判定前に消してよい。
#     `buffers` が無い・検査に通らないときは何もしない
# - remove_empty_session_state <session_id>
#     turn の終わりに使う。`buffers` とセッションディレクトリーを、空の場合に限り
#     `rmdir` で削除する。中身が残っていれば何もしない。検査に通らないときも何もしない
#     (失敗しない)

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
  write_state_file "$directory" pending "$prompt_id"
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

# 検査済みの buffers のパスを出力する。作らない。
existing_buffer_dir() {
  local session_id=$1
  local directory
  directory=$(existing_session_state_dir "$session_id") || return 1
  is_owned_directory "$directory/buffers" || return 1
  printf '%s\n' "$directory/buffers"
}

# 検査済みのメッセージディレクトリーのパスを出力する。作らない。
existing_message_dir() {
  local session_id=$1 message_id=$2
  local buffers
  buffers=$(existing_buffer_dir "$session_id") || return 1
  is_owned_directory "$buffers/$message_id" || return 1
  printf '%s\n' "$buffers/$message_id"
}

# <directory>/<name> の内容を <content> で置き換える。一時名に書いてから mv する。
write_state_file() {
  local directory=$1 name=$2 content=$3
  local temporary="$directory/.$name.tmp.$$"
  if (umask 077 && printf '%s' "$content" >"$temporary") 2>/dev/null &&
    mv -f "$temporary" "$directory/$name" 2>/dev/null; then
    return 0
  fi
  rm -f "$temporary" 2>/dev/null
  return 1
}

# `final` の値 (10 進の整数) を出力する。無い・整数でなければ非 0 を返す。
read_final_index() {
  local message_dir=$1
  local final_index
  [ -f "$message_dir/final" ] || return 1
  final_index=$(cat "$message_dir/final" 2>/dev/null) || return 1
  case $final_index in
    '' | *[!0-9]*) return 1 ;;
  esac
  printf '%s\n' "$final_index"
}

store_message_fragment() {
  local session_id=$1 message_id=$2 index=$3 delta=$4 is_final=$5
  local buffers message_dir
  buffers=$(prepare_buffer_dir "$session_id") || return 1
  message_dir="$buffers/$message_id"
  prepare_owned_directory "$message_dir" || return 1
  write_state_file "$message_dir" "$index" "$delta" || return 1
  if [ "$is_final" = "true" ]; then
    write_state_file "$message_dir" final "$index" || return 1
  fi
  return 0
}

claim_complete_message() {
  local session_id=$1 message_id=$2
  local message_dir final_index index
  message_dir=$(existing_message_dir "$session_id" "$message_id") || return 1
  final_index=$(read_final_index "$message_dir") || return 1
  index=0
  while [ "$index" -le "$final_index" ]; do
    [ -f "$message_dir/$index" ] || return 1
    index=$((index + 1))
  done
  (umask 077 && mkdir "$message_dir/judged") 2>/dev/null
}

read_message_text() {
  local session_id=$1 message_id=$2
  local message_dir final_index index
  message_dir=$(existing_message_dir "$session_id" "$message_id") || return 1
  final_index=$(read_final_index "$message_dir") || return 1
  index=0
  while [ "$index" -le "$final_index" ]; do
    cat "$message_dir/$index" 2>/dev/null || return 1
    index=$((index + 1))
  done
}

remove_message() {
  local session_id=$1 message_id=$2
  local message_dir
  message_dir=$(existing_message_dir "$session_id" "$message_id") || return 0
  rm -rf "$message_dir" 2>/dev/null
  return 0
}

remove_stale_messages() {
  local session_id=$1
  local buffers
  buffers=$(existing_buffer_dir "$session_id") || return 0
  find "$buffers" -mindepth 1 -maxdepth 1 -type d -mmin +10 \
    -exec rm -rf {} + 2>/dev/null
  return 0
}

# 最終更新から 1 分以内のメッセージディレクトリーがあれば 0 を返す。
has_recent_message() {
  local buffers=$1
  local recent
  recent=$(find "$buffers" -mindepth 1 -maxdepth 1 -type d ! -mmin +1 2>/dev/null)
  [ -n "$recent" ]
}

wait_for_message_judgements() {
  local session_id=$1
  local buffers attempts=0
  buffers=$(existing_buffer_dir "$session_id") || return 0
  while [ "$attempts" -lt 20 ] && has_recent_message "$buffers"; do
    sleep 0.1
    attempts=$((attempts + 1))
  done
  return 0
}

remove_all_messages() {
  local session_id=$1
  local buffers
  buffers=$(existing_buffer_dir "$session_id") || return 0
  find "$buffers" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} + 2>/dev/null
  return 0
}

remove_empty_session_state() {
  local session_id=$1
  local directory
  directory=$(existing_session_state_dir "$session_id") || return 0
  if is_owned_directory "$directory/buffers"; then
    rmdir "$directory/buffers" 2>/dev/null
  fi
  rmdir "$directory" 2>/dev/null
  return 0
}
