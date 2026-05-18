#!/bin/bash
# diff-hash.sh
# pre-push-review プラグインで使う「現在のブランチ全差分」のハッシュ計算を共通化する。
#
# auto-mark.sh が書き込むハッシュと block-pre-push.sh が検証するハッシュが
# 1 文字でも乖離するとマーカーは永遠に一致せず push が通らなくなる致命的なバグになる。
# 計算式の単一ソースとしてここに集約し、両スクリプトから source して呼び出す。
#
# 計算対象は **ブランチ全差分** (`git diff <base>...HEAD`) + 未コミット差分
# (`git diff --cached`, `git diff`) の連結:
#   - ブランチ全差分: push される commit 内容そのもの (PR diff と同じセマンティクス)
#   - 未コミット差分: /simplify などが残した未コミット edit を push 前に必ず commit させるため。
#     未コミット edit があるとハッシュが変わり markers 失効 → 「commit してから再 review → push」
#     を強制できる。未コミットのまま push しても push 自体は committed のみ反映するが、
#     ローカルの未コミット edit は次回 commit 時にレビューを再走させるためにも検出が必要。
#
# 統合 diff の生成順は固定:
#   1. ブランチ全差分 (origin/<base>...HEAD)
#   2. staged 差分 (git diff --cached)
#   3. unstaged 差分 (git diff)
# 順序が変わると同一の作業状態でもハッシュが変わるため必ず固定する。

# 空入力 (`printf '' | sha256sum`) のハッシュ値。SHA-256 アルゴリズムで定数なので
# hot path で毎回 fork して計算するのを避けるため事前計算値をハードコードする。
# 本値は branch 全差分 + 未コミット差分が空のとき (= base と同一でレビュー不要)
# の早期 skip 判定に使う。
readonly EMPTY_DIFF_HASH="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# detect_base_branch [<target_cwd>]
# 出力: default branch 名 (master/main 等)、検出失敗時は空文字列を返し非ゼロで exit
# target_cwd を指定すると `git -C <target_cwd>` 経由で resolve する (block-pre-push.sh が
# `cd dir && git push` の target repo に対して使う)。 省略 / 空文字なら現在の cwd を使う。
detect_base_branch() {
  local target_cwd="${1:-}"
  local -a git_prefix=()
  if [ -n "$target_cwd" ]; then
    git_prefix=(-C "$target_cwd")
  fi
  # 最優先: origin/HEAD のシンボリックリンク (`git clone` 時に自動設定される)
  local ref
  ref=$(git "${git_prefix[@]}" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null)
  if [ -n "$ref" ]; then
    printf '%s' "${ref#refs/remotes/origin/}"
    return 0
  fi
  # フォールバック: master / main の存在を順に確認
  local b
  for b in master main; do
    if git "${git_prefix[@]}" rev-parse --verify "origin/$b" >/dev/null 2>&1; then
      printf '%s' "$b"
      return 0
    fi
  done
  return 1
}

# 引数: <base-branch>
# 出力: ブランチ全差分 + 未コミット差分の SHA-256 ハッシュ
#
# `git diff <base>...HEAD` (triple-dot) は merge-base(base, HEAD) から HEAD までの差分で、
# PR の "Files changed" タブと同じ意味論。`base..HEAD` (double-dot) は base の最新と HEAD の
# 比較になり、base が動くと値がブレるため triple-dot を使う。
#
# `origin/<base>` を起点にすることで、ローカル base が古くても remote base の最新を基準にできる。
# 副作用: detached HEAD では symbolic-ref が失敗するため、呼び出し側で BRANCH の存在を検証する。
compute_review_hash() {
  local base="$1"
  compute_review_hash_in "" "$base"
}

# compute_review_hash_in <target_cwd> <base>
# target_cwd が空文字なら現在の cwd で git を実行 (= compute_review_hash と等価)。
# 非空なら `git -C <target_cwd> ...` 経由で target repo の diff を計算する。
# block-pre-push.sh が `cd dir && git push` / `git -C dir push` の target repo に対して
# 直接 hash 比較を行うために使う。
compute_review_hash_in() {
  local target_cwd="$1"
  local base="$2"
  local -a git_prefix=()
  if [ -n "$target_cwd" ]; then
    git_prefix=(-C "$target_cwd")
  fi
  local branch_diff
  branch_diff=$(git "${git_prefix[@]}" diff "origin/${base}...HEAD" 2>/dev/null) || return 1
  # GNU coreutils は `sha256sum`、BSD/macOS は `shasum -a 256` を使う。
  # どちらも `<hex>  -` 形式で出力するため後段の awk はそのまま動作する。
  local -a sha_cmd
  if command -v sha256sum >/dev/null 2>&1; then
    sha_cmd=(sha256sum)
  else
    sha_cmd=(shasum -a 256)
  fi
  {
    printf '%s' "$branch_diff"
    git "${git_prefix[@]}" diff --cached 2>/dev/null
    git "${git_prefix[@]}" diff 2>/dev/null
  } | "${sha_cmd[@]}" | awk '{print $1}'
}
