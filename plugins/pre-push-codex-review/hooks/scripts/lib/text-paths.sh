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
# md / markdown / rst / adoc のいずれかであればテキスト系文書とする。basename に `.` が無い
# (拡張子が無い) path はテキスト系文書ではない。txt と mdx を含めないのは、txt には依存
# 宣言 (requirements.txt 等) やビルド定義 (CMakeLists.txt) があり、mdx にはコードを埋め込める
# ため、どちらもレビュー無しで通すと挙動の変更を見逃すから。
#
# ただし次のいずれかに当たる path は、Claude Code や他の coding agent が指示として読み込む
# Markdown (実質的にコードとして働く) なので、テキスト系文書から除く:
#
#   - basename を除くディレクトリ要素のどれかが `agents` / `commands` / `skills` / `rules` /
#     `output-styles` / `.claude` / `.agents` / `.cursor` / `.gemini` / `.codex` / `.windsurf`
#     に一致する (深さは問わない)
#   - 連続する 2 つのディレクトリ要素が `hooks` → `prompts`、`.github` → `instructions`、
#     `.github` → `prompts` のいずれかの順に並ぶ
#   - basename が `CLAUDE.md` / `CLAUDE.local.md` / `AGENTS.md` / `GEMINI.md` /
#     `copilot-instructions.md` に一致する
#
# 除外条件の照合は ASCII の大文字小文字を区別しない。macOS 既定の filesystem は大文字小文字を
# 区別しないため、`Agents/` や `claude.md` も `agents/` や `CLAUDE.md` と同じファイルとして
# 解決され、agent に読み込まれる。
#
# 例: `README.md` / `docs/a.MD` / `plugins/x/README.md` / `docs/skills.md` (basename は
# ディレクトリ要素の除外条件の対象外) / `.github/README.md` はテキスト系文書。
# `plugins/x/agents/r.md` / `plugins/x/skills/s/SKILL.md` / `plugins/x/hooks/prompts/p.md` /
# `a/rules/r.md` / `.claude/CLAUDE.md` / `sub/CLAUDE.md` / `AGENTS.md` / `docs/agents.md` /
# `docs/Agents/x.md` / `.github/copilot-instructions.md` / `notes.txt` / `a.mdx` / `x.sh` /
# `Makefile` / `README` はテキスト系文書ではない。
#
# 分類は外部コマンドを使わず、case パターンと parameter 展開だけで行う。本 lib は hook
# 経由だけでなくセッション shell から起動される wrapper からも source されるため、呼び出し
# 元環境の alias / 関数 export / PATH 差し替えで分類結果が変わる余地を作らない。大文字小文字
# を区別しない照合も、範囲表記 (`[a-z]` 等) を使わず `[aA]` のように文字を列挙して書く
# (bracket 式の範囲が含む文字は locale によって変わるため)。
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
#   - merge gate (block-pre-merge.sh): gh が返す PR の base commit (`baseRefOid`) と head SHA
#     を is_text_only_range_in に渡し、成立すればレビュー記録を照合せずに通す
#   - merge 前の codex review wrapper: PR の base commit (`baseRefOid`) と HEAD を
#     is_text_only_range_in に渡し、成立すれば codex review を実行せず、pending attestation
#     も書かずに終了する

# is_text_document_path <repo 相対 path>
# exit code: 0 = テキスト系文書、1 = それ以外。定義はファイルヘッダの「テキスト系文書の
# 定義」を参照。
is_text_document_path() {
  local path="$1"
  local basename="${path##*/}"
  local directories=""
  case "$path" in
    */*) directories="${path%/*}" ;;
  esac

  # 拡張子 (basename の最後の `.` より後ろ) が対象の 4 つのいずれかであること。
  case "$basename" in
    *.*) ;;
    *) return 1 ;;
  esac
  case "${basename##*.}" in
    [mM][dD] | \
      [mM][aA][rR][kK][dD][oO][wW][nN] | \
      [rR][sS][tT] | \
      [aA][dD][oO][cC]) ;;
    *) return 1 ;;
  esac

  # agent が指示として読み込む basename。
  case "$basename" in
    [cC][lL][aA][uU][dD][eE].[mM][dD] | \
      [cC][lL][aA][uU][dD][eE].[lL][oO][cC][aA][lL].[mM][dD] | \
      [aA][gG][eE][nN][tT][sS].[mM][dD] | \
      [gG][eE][mM][iI][nN][iI].[mM][dD] | \
      [cC][oO][pP][iI][lL][oO][tT]-[iI][nN][sS][tT][rR][uU][cC][tT][iI][oO][nN][sS].[mM][dD])
      return 1
      ;;
  esac

  # ディレクトリ要素。前後に `/` を付けた文字列に対して照合し、要素の一部だけが一致する
  # 名前 (`agentsx/` 等) を除外しない。
  if [ -n "$directories" ]; then
    case "/${directories}/" in
      */[aA][gG][eE][nN][tT][sS]/* | \
        */[cC][oO][mM][mM][aA][nN][dD][sS]/* | \
        */[sS][kK][iI][lL][lL][sS]/* | \
        */[rR][uU][lL][eE][sS]/* | \
        */[oO][uU][tT][pP][uU][tT]-[sS][tT][yY][lL][eE][sS]/* | \
        */.[cC][lL][aA][uU][dD][eE]/* | \
        */.[aA][gG][eE][nN][tT][sS]/* | \
        */.[cC][uU][rR][sS][oO][rR]/* | \
        */.[gG][eE][mM][iI][nN][iI]/* | \
        */.[cC][oO][dD][eE][xX]/* | \
        */.[wW][iI][nN][dD][sS][uU][rR][fF]/* | \
        */[hH][oO][oO][kK][sS]/[pP][rR][oO][mM][pP][tT][sS]/* | \
        */.[gG][iI][tT][hH][uU][bB]/[iI][nN][sS][tT][rR][uU][cC][tT][iI][oO][nN][sS]/* | \
        */.[gG][iI][tT][hH][uU][bB]/[pP][rR][oO][mM][pP][tT][sS]/*)
        return 1
        ;;
    esac
  fi

  return 0
}

# _text_paths_raw_entries_are_text_only
# 標準入力の `git diff-tree -r -z --no-renames --no-commit-id` の出力 (エントリごとに
# `:<変更前 mode> <変更後 mode> <変更前 blob> <変更後 blob> <status>` と path の 2 フィールドを
# NUL で区切る) を読み、全エントリが範囲の判定の条件 3 を満たすとき 0 を返す。想定外の
# 形式 (先頭が `:` でないフィールド、path の欠けたエントリ、NUL で終わらない末尾) は 1 に倒す。
_text_paths_raw_entries_are_text_only() {
  local metadata=""
  local path old_mode new_mode
  while IFS= read -r -d '' metadata; do
    IFS= read -r -d '' path || return 1
    case "$metadata" in
      :*) ;;
      *) return 1 ;;
    esac
    metadata="${metadata#:}"
    old_mode="${metadata%% *}"
    metadata="${metadata#* }"
    new_mode="${metadata%% *}"
    case "$old_mode" in
      000000 | 100644) ;;
      *) return 1 ;;
    esac
    case "$new_mode" in
      000000 | 100644) ;;
      *) return 1 ;;
    esac
    is_text_document_path "$path" || return 1
    metadata=""
  done
  # 最後の read が NUL で終わらないフィールドを読んで止まった場合は、出力が途中で切れている。
  [ -z "$metadata" ] || return 1
  return 0
}

# _text_paths_diff_is_text_only <target_cwd> <from_oid> <to_oid>
# from と to の raw 差分の全エントリが範囲の判定の条件 3 を満たすとき 0 を返す。git の失敗も
# 解析の失敗も 1 に倒す (pipeline の両側の終了状態を PIPESTATUS で確かめる)。
_text_paths_diff_is_text_only() {
  local target_cwd="$1"
  local from_oid="$2"
  local to_oid="$3"
  local -a git_prefix=()
  if [ -n "$target_cwd" ]; then
    git_prefix=(-C "$target_cwd")
  fi
  local -a statuses=()
  git "${git_prefix[@]}" diff-tree -r -z --no-renames --no-commit-id \
    --ignore-submodules=none "$from_oid" "$to_oid" </dev/null 2>/dev/null \
    | _text_paths_raw_entries_are_text_only
  statuses=("${PIPESTATUS[@]}")
  [ "${statuses[0]}" = "0" ] && [ "${statuses[1]}" = "0" ]
}

# is_text_only_range_in <target_cwd> <base_rev> <head_rev>
# exit code: 0 = 範囲内の変更がテキスト系文書だけ、1 = それ以外 (git の失敗を含む)。
# 判定条件はファイルヘッダの「範囲の判定」を参照。target_cwd が空文字なら現在の cwd で、
# 非空なら `git -C <target_cwd>` で git を実行する (diff-hash.sh の `*_in` 関数と同じ扱い)。
is_text_only_range_in() {
  local target_cwd="$1"
  local base_rev="$2"
  local head_rev="$3"
  local -a git_prefix=()
  if [ -n "$target_cwd" ]; then
    git_prefix=(-C "$target_cwd")
  fi

  # revision は git の引数になるため、空や `-` 始まり (option として解釈される値) は拒否する。
  case "$base_rev" in
    "" | -*) return 1 ;;
  esac
  case "$head_rev" in
    "" | -*) return 1 ;;
  esac

  # 以降の git 呼び出しには解決済みの commit OID (16 進数のみ) だけを渡す。
  local base_oid head_oid
  base_oid=$(git "${git_prefix[@]}" rev-parse --verify --quiet "${base_rev}^{commit}" 2>/dev/null) || return 1
  head_oid=$(git "${git_prefix[@]}" rev-parse --verify --quiet "${head_rev}^{commit}" 2>/dev/null) || return 1
  if [ -z "$base_oid" ] || [ -z "$head_oid" ]; then
    return 1
  fi

  # 条件 1-3: 範囲内の各 commit を親とともに列挙する (`<commit> <親>...` の 1 行ずつ)。親が
  # ちょうど 1 つでない行 (親を持たない commit と merge commit) があれば不成立。
  local listing
  listing=$(git "${git_prefix[@]}" rev-list --parents "${base_oid}..${head_oid}" 2>/dev/null) || return 1
  [ -n "$listing" ] || return 1
  local commit parent extra
  # here-string の redirect が失敗する (一時ファイルを作れない等) とループ本体が 1 回も
  # 実行されないまま終わるため、その失敗も不成立として扱う。
  while IFS=' ' read -r commit parent extra; do
    if [ -z "$commit" ] || [ -z "$parent" ] || [ -n "$extra" ]; then
      return 1
    fi
    _text_paths_diff_is_text_only "$target_cwd" "$parent" "$commit" || return 1
  done <<<"$listing" || return 1

  # 条件 4: merge-base から head までの差分。
  local merge_base_oid
  merge_base_oid=$(git "${git_prefix[@]}" merge-base "$base_oid" "$head_oid" 2>/dev/null) || return 1
  [ -n "$merge_base_oid" ] || return 1
  _text_paths_diff_is_text_only "$target_cwd" "$merge_base_oid" "$head_oid" || return 1

  return 0
}

# is_text_only_push_in <target_cwd> <base>
# exit code: 0 = push される変更がテキスト系文書だけ、1 = それ以外 (git の失敗を含む)。
# 判定条件はファイルヘッダの「push の予測」を参照。target_cwd の扱いは
# is_text_only_range_in と同じ。
is_text_only_push_in() {
  local target_cwd="$1"
  local base="$2"
  local -a git_prefix=()
  if [ -n "$target_cwd" ]; then
    git_prefix=(-C "$target_cwd")
  fi
  [ -n "$base" ] || return 1

  # index / working tree が clean であること。plumbing (textconv / external driver 非適用) で
  # 判定する。
  git "${git_prefix[@]}" diff-index --quiet --cached HEAD 2>/dev/null || return 1
  git "${git_prefix[@]}" diff-files --quiet 2>/dev/null || return 1

  is_text_only_range_in "$target_cwd" "origin/${base}" HEAD
}
