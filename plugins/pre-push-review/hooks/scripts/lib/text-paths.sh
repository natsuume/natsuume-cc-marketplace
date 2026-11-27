#!/bin/bash
# text-paths.sh
# 変更が「テキスト系文書」だけで構成されるかを判定する。push gate / merge gate と codex
# review wrapper は、この判定が成立した変更に対してレビュー記録を要求せずに通す。判定に
# 必要な情報が得られない場合や git の呼び出しが 1 つでも失敗した場合は、すべて
# 「テキスト系文書のみではない」(= レビュー必須) 側に倒す (fail-closed)。
#
# 本 lib は pre-push-review (`plugins/pre-push-review/hooks/scripts/lib/`) が canonical で、
# pre-push-codex-review と pre-merge-cross-review の `hooks/scripts/lib/` に byte-identical な
# コピーを置く。同一性は tests/test_shared_lib_copies.py と tests/test_pre_merge_lib_copies.py
# が検査する。コピー先の plugin が持たない lib もあるため、本 lib は他の lib に依存せず
# 単独で source できる状態を保つ。
#
# bash 3.2 (macOS の /bin/bash) で動くこと。大文字小文字変換の展開 (`${var,,}` 等)・連想
# 配列・mapfile / readarray・GNU 拡張のコマンドオプションを使わない。
#
# ## テキスト系文書の定義 (is_text_document_path)
#
# repo 相対 path の basename の最後の `.` より後ろ (拡張子) が、大文字小文字を区別せずに
# md / mdx / markdown / txt / rst / adoc のいずれかであればテキスト系文書とする。basename に
# `.` が無い (拡張子が無い) path はテキスト系文書ではない。
#
# ただし次のいずれかに当たる path は、Claude Code や他の coding agent の挙動を定義する
# Markdown (実質的にコードとして働く) なので、テキスト系文書から除く:
#
#   - basename を除くディレクトリ要素のどれかが `agents` / `commands` / `skills` / `rules` /
#     `.claude` / `.agents` に一致する (深さは問わない。大文字小文字を区別する)
#   - 連続する 2 つのディレクトリ要素が `hooks`、`prompts` の順に並ぶ
#   - basename が `CLAUDE.md` / `CLAUDE.local.md` / `AGENTS.md` / `GEMINI.md` に一致する
#     (大文字小文字を区別する)
#
# 例: `README.md` / `docs/a.MD` / `plugins/x/README.md` / `notes.txt` / `docs/agents.md`
# (basename は除外条件の対象外) はテキスト系文書。`plugins/x/agents/r.md` /
# `plugins/x/skills/s/SKILL.md` / `plugins/x/hooks/prompts/p.md` / `a/rules/r.md` /
# `.claude/CLAUDE.md` / `sub/CLAUDE.md` / `AGENTS.md` / `x.sh` / `Makefile` / `README` は
# テキスト系文書ではない。
#
# 分類は外部コマンドを使わず、case パターンと parameter 展開だけで行う。本 lib は hook
# 経由だけでなくセッション shell から起動される wrapper からも source されるため、呼び出し
# 元環境の alias / 関数 export / PATH 差し替えで分類結果が変わる余地を作らない。
#
# ## 範囲の判定 (is_text_only_range_in)
#
# `<base_rev>..<head_rev>` の変更がテキスト系文書だけと判定するのは、次をすべて満たす
# ときに限る:
#
#   1. `<base_rev>..<head_rev>` に commit が 1 つ以上ある
#   2. 範囲内に merge commit が無い
#   3. 範囲内の各 commit について、親 commit との raw 差分の全エントリで、変更前・変更後の
#      ファイル mode がそれぞれ `000000` (不在) か `100644` (通常ファイル) のいずれかで、
#      かつ path が is_text_document_path を満たす。親を持たない commit が範囲内にあれば
#      不成立
#   4. merge-base(`<base_rev>`, `<head_rev>`) と `<head_rev>` の raw 差分も、3 と同じ条件を
#      満たす
#
# raw 差分は rename / copy の検出を行わずに取る (rename は削除と追加の 2 エントリになり、
# 変更前と変更後の両方の path が検査対象になる)。出力は NUL 区切りで読み、空白・改行・
# 非 ASCII 文字を含む path も quote されないまま扱う。mode の条件により、symlink
# (`120000`)・submodule (`160000`)・実行ビット付きファイル (`100755`) はテキスト系文書から
# 除かれる。
#
# net diff (merge-base..head) だけでなく範囲内の各 commit を検査するのは、コードを追加する
# commit とそれを revert する commit を重ねた範囲を skip させないため。push や merge で
# remote に届くのは net diff ではなく範囲内の全 commit であり、中間 commit のコードも履歴に
# 残る。
#
# ## push の予測 (is_text_only_push_in)
#
# push gate と push 前の codex review wrapper が使う。index と working tree が clean
# (`git diff-index --quiet --cached HEAD` と `git diff-files --quiet` がともに成功) で、
# かつ `origin/<base>..HEAD` が is_text_only_range_in を満たすときだけ成立する。
#
# ## 利用側
#
#   - push gate (block-pre-push.sh / block-pre-push-codex.sh): 空 push の skip の直後に
#     is_text_only_push_in で判定し、成立すればマーカーを要求せずに通す
#   - push 前の codex review wrapper: 空 push の判定の直後に is_text_only_push_in で判定し、
#     成立すれば codex review を実行せず、pending attestation も書かずに終了する
#   - merge gate (block-pre-merge.sh): PR の base branch の remote-tracking ref と PR の head
#     SHA を is_text_only_range_in に渡し、成立すればレビュー記録を照合せずに通す
#   - merge 前の codex review wrapper: `refs/remotes/origin/<base>..HEAD` を
#     is_text_only_range_in に渡し、成立すれば codex review を実行せず、pending attestation
#     も書かずに終了する

# is_text_document_path <repo 相対 path>
# exit code: 0 = テキスト系文書、1 = それ以外。定義はファイルヘッダの「テキスト系文書の
# 定義」を参照。
is_text_document_path() {
  # 暫定: 判定を実装しておらず、常に 1 (テキスト系文書ではない = レビュー必須) を返す。
  # tests/test_text_only_review_skip.py の skip を期待するテストが通る実装に置き換える。
  return 1
}

# is_text_only_range_in <target_cwd> <base_rev> <head_rev>
# exit code: 0 = 範囲内の変更がテキスト系文書だけ、1 = それ以外 (git の失敗を含む)。
# 判定条件はファイルヘッダの「範囲の判定」を参照。target_cwd が空文字なら現在の cwd で、
# 非空なら `git -C <target_cwd>` で git を実行する (diff-hash.sh の `*_in` 関数と同じ扱い)。
is_text_only_range_in() {
  # 暫定: 判定を実装しておらず、常に 1 (レビュー必須) を返す。
  # tests/test_text_only_review_skip.py の skip を期待するテストが通る実装に置き換える。
  return 1
}

# is_text_only_push_in <target_cwd> <base>
# exit code: 0 = push される変更がテキスト系文書だけ、1 = それ以外 (git の失敗を含む)。
# 判定条件はファイルヘッダの「push の予測」を参照。target_cwd の扱いは
# is_text_only_range_in と同じ。
is_text_only_push_in() {
  # 暫定: 判定を実装しておらず、常に 1 (レビュー必須) を返す。
  # tests/test_text_only_review_skip.py の skip を期待するテストが通る実装に置き換える。
  return 1
}
