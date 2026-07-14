#!/bin/bash
# diff-hash.sh
# pre-push-review プラグインで使う「現在のブランチ全差分」のハッシュ計算と
# 空 push 判定を共通化する。
#
# auto-mark.sh / run-codex-review.sh が書き込むハッシュと block-pre-push.sh が検証する
# ハッシュが 1 文字でも乖離するとマーカーは永遠に一致せず push が通らなくなる致命的な
# バグになる。計算式の単一ソースとしてここに集約し、各スクリプトから source して呼び出す。
#
# ==========================================================================
# 設計契約 (issue #126 対応、v3.0.5)。実装本体はこの PR の Phase B commit で行う。
# ==========================================================================
#
# ## ハッシュ計算式
#
# sha256 の入力は以下の連結 (順序固定。順序が変わると同一の作業状態でも
# ハッシュが変わるため必ず固定する):
#   1. HEAD 束縛行: `head <HEAD の commit OID>` + 改行 (`git rev-parse HEAD^{commit}`)
#   2. ブランチ全差分 (git diff origin/<base>...HEAD)
#   3. staged 差分 (git diff --cached)
#   4. unstaged 差分 (git diff)
#
# 各要素の意味:
#   - HEAD 束縛行 (issue #126 で追加): v3.0.4 までは diff 内容のみが入力だったため、
#     レビュー後に「commit A を積み、commit B で A を revert する」と net diff が
#     レビュー時点の値へ正確に戻り、失効したはずのマーカーが復活して未レビューの
#     commit A・B が push できた (A の内容は `git show <A>:file` で remote 履歴に
#     恒久的に残る)。HEAD の commit OID は親 commit 連鎖を再帰的に束縛するため、
#     add→revert / amend / rebase / squash など commit 列が変わるあらゆる操作で
#     マーカーが失効する。`head ` プレフィクス行は diff 本文との入力空間の衝突を
#     避ける domain separation。OID の取得に失敗した場合は関数全体を非ゼロで
#     失敗させる (中途半端な入力でハッシュを返すと誤判定の元になるため、
#     呼び出し側の fail-closed 処理に委ねる)
#   - ブランチ全差分: push される commit 内容そのもの (PR diff と同じセマンティクス)
#   - 未コミット差分: /code-review などが残した未コミット edit を push 前に必ず
#     commit させるため。未コミット edit があるとハッシュが変わり markers 失効 →
#     「commit してから再 review → push」を強制できる
#
# 計算式変更の副作用: v3.0.4 以前に書かれた既存マーカーは plugin 更新後の最初の
# push で一度失効する (再レビュー 1 回で回復する、意図した一時コスト)。
#
# ## 空 push 判定 (is_empty_push / is_empty_push_in)
#
# v3.0.4 までは「ハッシュ == EMPTY_DIFF_HASH (空入力の sha256 定数)」で空 push を
# 判定していたが、HEAD 束縛行が入力に入ると HEAD が存在する限り一致しなくなるため、
# 専用の判定関数に分離する (EMPTY_DIFF_HASH 定数は廃止)。分離に合わせて skip 条件も
# 厳格化する:
#
#   - diff 3 種 (branch / staged / unstaged) がすべて空、かつ
#   - origin/<base>..HEAD に merge commit が存在せず、かつ
#   - 範囲内の全 commit の tree が HEAD の tree と一致する (= 全 commit が
#     「親と同一 tree の empty commit」)
#
# 旧判定 (diff の空のみ) には issue #126 と同根の穴があった: fresh branch に
# 「commit A + A の revert」だけを積むと net diff が空になり、マーカー検証すら
# 経ずに未レビュー内容が remote 履歴に到達できた。厳格化後も、issue claim 手順の
# 「空 commit (--allow-empty) を新 branch に push」は tree 変更が無くレビュー対象が
# 存在しないため、従来どおりレビュー無しで通る。
#
# 判定の正当性: branch diff (triple-dot) が空 ⇔ tree(merge-base) == tree(HEAD)。
# 範囲内に merge commit が無ければ範囲は HEAD から merge-base への単一の親子鎖で
# あり、全 commit の tree が tree(HEAD) と一致するなら各 commit は親と同一 tree
# (= empty commit) である。判定に使う git コマンドが失敗した場合は「空ではない」
# 側に倒す (fail-closed: skip せずマーカー検証へ進み、ハッシュ計算失敗の明示 deny
# に到達させる)。
#
# ## 利用側の契約 (Phase B で同時に変更)
#
#   - block-pre-push.sh: EMPTY_DIFF_HASH 比較による早期 skip を is_empty_push_in に
#     置換する (dirty-tree gate 通過後・ハッシュ計算前)。マーカー失効メッセージの
#     文言を「差分または commit 列が変わったため再実行が必要」に更新する
#   - run-codex-review.sh: EMPTY_DIFF_HASH 比較による review skip を is_empty_push に
#     置換する
#   - auto-mark.sh: 変更なし (compute_review_hash の新計算式が自動で波及する)

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
