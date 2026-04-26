#!/bin/bash
# diff-hash.sh
# pre-commit-review プラグインで使う「現在のステージング差分」のハッシュ計算を共通化する。
#
# auto-mark.sh が書き込むハッシュと block-pre-commit.sh が検証するハッシュが
# 1 文字でも乖離するとマーカーは永遠に一致せず commit が通らなくなる致命的なバグになる。
# 計算式の単一ソースとしてここに集約し、両スクリプトから source して呼び出す。
#
# `git commit -a` / `git commit <pathspec>` は unstaged tracked な変更も commit
# 対象にできるため、staged + unstaged を連結することで未レビュー差分の混入を検出可能にする。

compute_review_hash() {
  {
    git diff --cached 2>/dev/null
    git diff 2>/dev/null
  } | sha256sum | awk '{print $1}'
}
