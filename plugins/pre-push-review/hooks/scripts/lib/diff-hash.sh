#!/bin/bash
# diff-hash.sh
# pre-push-review プラグインで使う「現在のブランチ全差分」のハッシュ計算と
# 空 push 判定を共通化する。
#
# auto-mark.sh / run-codex-review.sh が書き込むハッシュと block-pre-push.sh が検証する
# ハッシュが 1 文字でも乖離するとマーカーは永遠に一致せず push が通らなくなる致命的な
# バグになる。計算式の単一ソースとしてここに集約し、各スクリプトから source して呼び出す。
#
# ## ハッシュ計算式 (v3.0.5 / issue #126)
#
# sha256 の入力は以下の連結 (順序固定。順序が変わると同一の作業状態でも
# ハッシュが変わるため必ず固定する):
#   1. HEAD 束縛行: `head <HEAD の commit OID>` + 改行 (`git rev-parse HEAD^{commit}`)
#   2. merge-base 束縛行: `mbase <merge-base の OID>` + 改行
#      (`git merge-base origin/<base> HEAD`)
#   3. ブランチ全差分 (`git diff --no-ext-diff --no-textconv <merge-base OID> HEAD`)
#   4. staged 差分 (`git diff --no-ext-diff --no-textconv --cached`)
#   5. unstaged 差分 (`git diff --no-ext-diff --no-textconv`)
#
# 各要素の意味:
#   - HEAD 束縛行 (issue #126 で追加): v3.0.4 までは diff 内容のみが入力だったため、
#     レビュー後に「commit A を積み、commit B で A を revert する」と net diff が
#     レビュー時点の値へ正確に戻り、失効したはずのマーカーが復活して未レビューの
#     commit A・B が push できた (A の内容は `git show <A>:file` で remote 履歴に
#     恒久的に残る)。HEAD の commit OID は親 commit 連鎖を再帰的に束縛するため、
#     add→revert / amend / rebase / squash など commit 列が変わるあらゆる操作で
#     マーカーが失効する
#   - merge-base 束縛行 (issue #126 の codex review 指摘で追加): HEAD 束縛だけでは
#     「origin/<base> の force-rewrite でレビュー範囲の境界だけが変わる」ケース
#     (HEAD と net diff は不変のまま origin/<base>..HEAD に未レビュー commit が
#     入り込む) で stale marker が生き残る。レビュー範囲の両端 (merge-base, HEAD)
#     を束縛することで範囲の同一性を保証する。merge-base は複数存在しうる
#     (criss-cross merge) ため、1 回だけ解決した OID を束縛行とブランチ全差分の
#     基点の両方に再利用し、選択の不定性がハッシュの乖離にならないようにする
#   - `head ` / `mbase ` プレフィクス行は diff 本文との入力空間の衝突を避ける
#     domain separation
#   - ブランチ全差分: push される commit 内容そのもの (PR diff と同じセマンティクス。
#     `origin/<base>...HEAD` の triple-dot と同義だが、上記のとおり merge-base OID を
#     明示して基点を固定する)
#   - 未コミット差分: /code-review などが残した未コミット edit を push 前に必ず
#     commit させるため。未コミット edit があるとハッシュが変わり markers 失効 →
#     「commit してから再 review → push」を強制できる
#   - `--no-ext-diff --no-textconv`: external diff driver / textconv はレンダリング
#     結果を差し替えられるため、ハッシュ入力は素の diff バイト列に固定する
#
# 途中の git 呼び出しが 1 つでも失敗した場合は関数全体を非ゼロで失敗させる
# (中途半端な入力でハッシュを返すと誤判定の元になるため、呼び出し側の fail-closed
# 処理に委ねる)。v3.0.4 までは staged / unstaged 差分の git 失敗が pipeline 内で
# 握り潰され部分入力のハッシュになり得たが、v3.0.5 で全要素を明示捕捉に変更した。
#
# 計算式変更の副作用: v3.0.4 以前に書かれた既存マーカーは plugin 更新後の最初の
# push で一度失効する (再レビュー 1 回で回復する、意図した一時コスト)。
#
# ## 空 push 判定 (is_empty_push / is_empty_push_in)
#
# v3.0.4 までは「ハッシュ == EMPTY_DIFF_HASH (空入力の sha256 定数)」で空 push を
# 判定していたが、HEAD 束縛行が入力に入ると HEAD が存在する限り一致しなくなるため、
# 専用の判定関数に分離した (EMPTY_DIFF_HASH 定数は廃止)。分離に合わせて判定も
# porcelain diff の空検査ではなく tree OID / plumbing ベースへ厳格化した。skip して
# よい (= レビュー対象が存在しない) と判定する条件は以下のすべて:
#
#   1. merge-base の tree OID と HEAD の tree OID が一致する (net 変更なし)
#   2. origin/<base>..HEAD に merge commit が存在しない
#   3. 範囲内の全 commit の tree OID が HEAD の tree OID と一致する
#      (= 全 commit が「親と同一 tree の empty commit」)
#   4. index が clean (`git diff-index --quiet --cached HEAD`)
#   5. working tree が clean (`git diff-files --quiet`)
#
# porcelain diff の空検査を使わない理由 (codex review 指摘): textconv / external
# diff driver は異なる blob を同一テキストにレンダリングでき、「diff 出力が空」は
# 「tree が同一」を含意しない。tree OID の直接比較と plumbing (diff-index /
# diff-files は textconv / external driver を適用しない) で判定する。
#
# 旧判定 (diff の空のみ) には issue #126 と同根の穴があった: fresh branch に
# 「commit A + A の revert」だけを積むと net diff が空になり、マーカー検証すら
# 経ずに未レビュー内容が remote 履歴に到達できた。厳格化後も、issue claim 手順の
# 「空 commit (--allow-empty) を新 branch に push」は tree 変更が無くレビュー対象が
# 存在しないため、従来どおりレビュー無しで通る。
#
# 判定の正当性: 条件 1 で tree(merge-base) == tree(HEAD)。条件 2 より範囲は HEAD
# から merge-base への単一の親子鎖であり、条件 3 と合わせると各 commit は親と同一
# tree (= empty commit) である。判定に使う git コマンドが失敗した場合は「空では
# ない」側に倒す (fail-closed: skip せずマーカー検証へ進み、ハッシュ計算失敗の
# 明示 deny に到達させる)。
#
# ## 利用側
#
#   - block-pre-push.sh: dirty-tree gate 通過後・ハッシュ計算前に is_empty_push_in で
#     空 push を skip し、それ以外は compute_review_hash_in で 3 マーカーを検証する
#   - run-codex-review.sh: is_empty_push で空 push を skip し、それ以外は
#     compute_review_hash のハッシュで Codex pending attestation を書く
#   - auto-mark.sh: compute_review_hash のハッシュで code / security marker を書き、
#     Codex pending attestation の一致を検証して final marker へ昇格する

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
# 出力: ハッシュ計算式 (ファイルヘッダ参照) による SHA-256 ハッシュ
# 失敗時 (HEAD / merge-base 解決不能、diff 失敗等) は非ゼロで exit する。
compute_review_hash() {
  local base="$1"
  compute_review_hash_in "" "$base"
}

# compute_review_hash_in <target_cwd> <base>
# target_cwd が空文字なら現在の cwd で git を実行 (= compute_review_hash と等価)。
# 非空なら `git -C <target_cwd> ...` 経由で target repo に対して計算する。
# block-pre-push.sh が `cd dir && git push` / `git -C dir push` の target repo に対して
# 直接 hash 比較を行うために使う。
#
# 副作用: detached HEAD では symbolic-ref が失敗するため、呼び出し側で BRANCH の存在を
# 検証する。
compute_review_hash_in() {
  local target_cwd="$1"
  local base="$2"
  local -a git_prefix=()
  if [ -n "$target_cwd" ]; then
    git_prefix=(-C "$target_cwd")
  fi
  local head_oid
  head_oid=$(git "${git_prefix[@]}" rev-parse 'HEAD^{commit}' 2>/dev/null) || return 1
  local mbase_oid
  mbase_oid=$(git "${git_prefix[@]}" merge-base "origin/${base}" HEAD 2>/dev/null) || return 1
  local branch_diff
  branch_diff=$(git "${git_prefix[@]}" diff --no-ext-diff --no-textconv "$mbase_oid" HEAD 2>/dev/null) || return 1
  local staged_diff
  staged_diff=$(git "${git_prefix[@]}" diff --no-ext-diff --no-textconv --cached 2>/dev/null) || return 1
  local unstaged_diff
  unstaged_diff=$(git "${git_prefix[@]}" diff --no-ext-diff --no-textconv 2>/dev/null) || return 1
  # GNU coreutils は `sha256sum`、BSD/macOS は `shasum -a 256` を使う。
  # どちらも `<hex>  -` 形式で出力するため後段の awk はそのまま動作する。
  local -a sha_cmd
  if command -v sha256sum >/dev/null 2>&1; then
    sha_cmd=(sha256sum)
  else
    sha_cmd=(shasum -a 256)
  fi
  {
    printf 'head %s\n' "$head_oid"
    printf 'mbase %s\n' "$mbase_oid"
    printf '%s' "$branch_diff"
    printf '%s' "$staged_diff"
    printf '%s' "$unstaged_diff"
  } | "${sha_cmd[@]}" | awk '{print $1}'
}

# is_empty_push <base>
# is_empty_push_in "" <base> と等価。run-codex-review.sh が現在の cwd に対して使う。
is_empty_push() {
  local base="$1"
  is_empty_push_in "" "$base"
}

# is_empty_push_in <target_cwd> <base>
# 「push してもレビュー対象となる変更が remote に載らない」と判定できる場合のみ 0 を
# 返す。判定条件・fail-closed 方針・正当性の論証はファイルヘッダの「空 push 判定」
# セクションを参照。target_cwd の扱いは compute_review_hash_in と同じ。
is_empty_push_in() {
  local target_cwd="$1"
  local base="$2"
  local -a git_prefix=()
  if [ -n "$target_cwd" ]; then
    git_prefix=(-C "$target_cwd")
  fi

  # 条件 1: merge-base と HEAD の tree OID が一致 (net 変更なし)。
  # porcelain diff の空検査ではなく OID 比較で判定する (ヘッダ参照)。
  local mbase_oid
  mbase_oid=$(git "${git_prefix[@]}" merge-base "origin/${base}" HEAD 2>/dev/null) || return 1
  local head_tree
  head_tree=$(git "${git_prefix[@]}" rev-parse 'HEAD^{tree}' 2>/dev/null) || return 1
  local mbase_tree
  mbase_tree=$(git "${git_prefix[@]}" rev-parse "${mbase_oid}^{tree}" 2>/dev/null) || return 1
  if [ "$mbase_tree" != "$head_tree" ]; then
    return 1
  fi

  # 条件 2: 範囲内に merge commit が無い (merge を含む鎖は「全 commit empty」の
  # 論証が成立しないため、保守的にレビュー必須へ倒す)。
  local merge_count
  merge_count=$(git "${git_prefix[@]}" rev-list --min-parents=2 --count "origin/${base}..HEAD" 2>/dev/null) || return 1
  if [ "$merge_count" != "0" ]; then
    return 1
  fi

  # 条件 3: 範囲内の全 commit の tree OID が HEAD の tree OID と一致する。
  # 範囲が空 (= push すべき新規 commit が無い) 場合も skip 条件を満たす (ループが
  # 0 回で素通りする)。
  # 一致検査は外部コマンド (grep 等) に依存せず pure bash で行う: この lib は
  # hook 経由だけでなくセッション shell から実行される run-codex-review.sh からも
  # source されるため、呼び出し元環境の alias / 関数 export / PATH 差し替えで
  # gate 判定が変わる余地を作らない。OID は hex のみで空白・glob 文字を含まない
  # ため、素の word splitting で安全に列挙できる。
  local range_trees
  range_trees=$(git "${git_prefix[@]}" log --format=%T "origin/${base}..HEAD" 2>/dev/null) || return 1
  local range_tree
  for range_tree in $range_trees; do
    if [ "$range_tree" != "$head_tree" ]; then
      return 1
    fi
  done

  # 条件 4-5: index / working tree が clean。plumbing (textconv / external driver
  # 非適用) で判定する。diff-files は stat cache が古いと false dirty を返しうるが、
  # その場合も「空ではない」側 (= マーカー検証へ進む) に倒れるだけで安全。
  git "${git_prefix[@]}" diff-index --quiet --cached HEAD 2>/dev/null || return 1
  git "${git_prefix[@]}" diff-files --quiet 2>/dev/null || return 1

  return 0
}
