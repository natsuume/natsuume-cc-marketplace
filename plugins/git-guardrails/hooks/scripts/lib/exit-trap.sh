#!/bin/bash
# exit-trap.sh
# git-guardrails プラグインの 3 つの hook script (block-default-branch-commit.sh /
# block-default-branch-push.sh / block-default-branch-pr.sh) で共有する EXIT trap
# セットアップ関数を提供する。
#
# ## なぜ必要か
#
# 各 hook は通常パスで `exit 0` (allow / deny JSON 出力後の正常終了 / 想定済み silent
# skip) を返す。 しかし jq の引数バグ / 外部コマンドのクラッシュ / シェル展開の想定外
# 失敗 / signal などで script が **非ゼロで終了** すると、 PreToolUse hook の仕様
# 「その他の exit code: 続行」 により deny が出ないまま **続行 (= fail-open)** になる。
# つまり default branch 保護が無音で外れる silent failure 経路ができる。
#
# EXIT trap で `$?` を観測し、 非ゼロ終了をユーザの stderr に通知することで、
# 「hook が壊れた」 ことを能動的に可視化する。 trap は元の exit code を変更しない
# ため、 deny/allow の挙動はそのまま (= 既存挙動を変えない / fail-closed 化ではない)。
#
# ## 設計
#
# 構造 (exit code チェック → 非ゼロなら stderr に 2 行 printf) は sibling の
# pre-push-review/lib/exit-trap.sh と同型。 hook ごとに違うのは「タグ名」 と「壊れた
# 場合の影響説明」 だけなので、 各 hook では `install_exit_trap "<tag>" "<impact>"` の
# 1 行で済む。 caller の冒頭 (`INPUT=$(cat)` の前) で 1 度だけ呼ぶ。

# 共通の EXIT handler 本体。 install_exit_trap 経由で trap される。
_git_guardrails_exit_handler_dispatch() {
  local exit_code=$?
  local tag="$1"
  local impact="$2"
  if [ "$exit_code" -ne 0 ]; then
    printf '[git-guardrails/%s] 予期せぬエラーで hook が exit %s で終了しました。\n' \
      "$tag" "$exit_code" >&2
    printf '[git-guardrails/%s] %s marketplace https://github.com/natsuume/natsuume-cc-marketplace に hook 実装の bug として報告してください。\n' \
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
  trap "_git_guardrails_exit_handler_dispatch $tag_q $impact_q" EXIT
}
