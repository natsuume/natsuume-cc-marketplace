#!/bin/bash
# exit-trap.sh
# pre-push-review プラグインの 3 つの hook script (block-pre-push.sh / auto-mark.sh /
# block-bg-codex-review.sh) で共有する EXIT trap セットアップ関数を提供する。
#
# ## なぜ必要か
#
# 各 hook は通常パスで `exit 0` (allow / deny JSON 出力後の正常終了 / 想定済み silent
# skip) を返す。 しかし jq の引数バグ / 外部コマンドのクラッシュ / シェル展開の想定外
# 失敗 / signal などで script が **非ゼロで終了** すると、
#   - block-pre-push: fail-closed 設計の deny JSON を返せていない可能性
#   - auto-mark: marker 書き込みが skip された可能性
#   - block-bg-codex-review: background 起動の deny を返せていない可能性
# という silent failure 経路ができる。 ユーザ / Claude は hook の異常終了を認知できず、
# 後で push が通らない / 未レビュー commit が混入する / `--background` review が
# silent skip する、 といった不可解な状況に遭遇する。
#
# EXIT trap で `$?` を観測し、 非ゼロ終了をユーザの stderr に通知することで、
# 「hook が壊れた」 ことを能動的に可視化する。 trap は元の exit code を変更しない
# ため、 push 動作はノンブロッキングのまま (= 既存挙動を変えない)。
#
# ## なぜ 3 hook で共通化するか
#
# 構造 (exit code チェック → 非ゼロなら stderr に 2 行 printf) は完全に同型で、
# hook ごとに違うのは「タグ名 (hook ファイル名)」 と「壊れた場合の影響説明」 だけ。
# 共通化することで:
#   - 関数名衝突 (`_pre_push_review_exit_handler` が 3 ファイルで同名) を回避
#   - 将来 trap 仕様変更 (例: structured logging への切り替え) を 1 箇所で完結
#   - 各 hook では `install_exit_trap "<tag>" "<impact>"` の 1 行で済む
#
# ## 呼び出し規約
#
# install_exit_trap <tag> <impact>
#   <tag>    : hook を特定する短いラベル (例: "block-pre-push", "auto-mark")
#              stderr ログの `[pre-push-review/<tag>]` プレフィクスに使われる
#   <impact> : 非ゼロ終了が起きた場合に何が壊れるかの説明 (1 文)
#              「~ の可能性があります。」 形式で終わる文字列を想定
#
# caller の冒頭 (`INPUT=$(cat)` の前) で 1 度だけ呼ぶ。 source 順序の制約はないが、
# 早期に呼ぶほど多くの異常終了経路を捕捉できる。

# 共通の EXIT handler 本体。 install_exit_trap 経由で trap される。
# 関数内 local `_etag` / `_eimpact` は install_exit_trap が `eval` 経由で trap に
# 焼き込んで参照可能にする (= EXIT 発火時には install 時の引数が見える)。
_pre_push_review_exit_handler_dispatch() {
  local exit_code=$?
  local tag="$1"
  local impact="$2"
  if [ "$exit_code" -ne 0 ]; then
    printf '[pre-push-review/%s] 予期せぬエラーで hook が exit %s で終了しました。\n' \
      "$tag" "$exit_code" >&2
    printf '[pre-push-review/%s] %s marketplace https://github.com/natsuume/natsuume-cc-marketplace に hook 実装の bug として報告してください。\n' \
      "$tag" "$impact" >&2
  fi
}

# install_exit_trap <tag> <impact>
# tag / impact を closure 的に保持した EXIT trap を設定する。
#
# 実装: `trap` の command 引数は trap 発火時に **新規 shell 解釈** される文字列。
# install 時の引数 (`$1` / `$2`) を関数 local として保存するだけでは trap 発火時に
# 見えない (trap が走るのは install_exit_trap の return 後の EXIT) ため、 引数を
# `printf '%q'` で安全に quote してから trap command に焼き込む。
install_exit_trap() {
  local tag_q impact_q
  tag_q=$(printf '%q' "$1")
  impact_q=$(printf '%q' "$2")
  # shellcheck disable=SC2064
  trap "_pre_push_review_exit_handler_dispatch $tag_q $impact_q" EXIT
}
