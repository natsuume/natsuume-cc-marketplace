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
# Claude Code や他の coding agent は、特定の場所・名前の Markdown を指示として読み込む
# (実質的にコードとして働く)。そのため、次に列挙した場所・名前の Markdown はコード扱いにし、
# テキスト系文書から除く:
#
#   - basename を除くディレクトリ要素のどれかが `agents` / `commands` / `skills` / `rules` /
#     `output-styles` / `.claude` / `.agents` / `.cursor` / `.gemini` / `.codex` / `.windsurf` /
#     `.clinerules` / `.roo` / `.kiro` / `.continue` / `.junie` / `.amazonq` に一致する (深さは
#     問わない)
#   - 連続する 2 つのディレクトリ要素が `hooks` → `prompts`、`.github` → `instructions`、
#     `.github` → `prompts`、`.github` → `chatmodes` のいずれかの順に並ぶ
#   - basename が `CLAUDE.md` / `CLAUDE.local.md` / `AGENTS.md` / `AGENTS.override.md` /
#     `GEMINI.md` / `copilot-instructions.md` に一致する
#
# 除外するのは列挙した場所・名前だけである。列挙に無い場所にある agent の指示ファイルや、
# 除外対象のファイルから間接的に読み込まれる文書 (`CLAUDE.md` から `@path` で import される
# 文書など) はテキスト系文書と判定される。
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
#   1. `<base_rev>..<head_rev>` の commit が 1 つ以上、100 以下である
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
# commit 数の上限 (100) は、gate を実行する Claude Code の command hook の既定の timeout
# (60 秒) 内に判定を終えるためのもの。3 は commit ごとに git を起動するため、範囲の commit 数に
# 比例して時間がかかる。hook が timeout すると gate の判定が働かないため、上限を超える範囲は
# 判定を行わずレビュー必須 (戻り値 1) として扱う。
#
# 判定は 1 → 2 → 4 → 3 の順に行う。git の呼び出しが 1 回で済む 4 (net diff) を、commit 数に
# 比例する 3 より先に行い、net diff にテキスト系文書以外の変更がある範囲では各 commit を走査
# せずに不成立を返す。
#
# 1-4 を満たした範囲は、最後に秘匿情報の候補を検査する (下記「秘匿情報の検査」)。
#
# exit code:
#
#   - 0: 範囲内の変更がテキスト系文書だけで、秘匿情報の検査も通った (skip してよい)
#   - 1: 1-4 のいずれかを満たさない (テキスト系文書だけではない、commit 数が上限を超える等)、
#        または 1-4 の判定に必要な git の呼び出しが失敗した
#   - 2: テキスト系文書だけだが、gitleaks が秘匿情報の候補を検出した、または gitleaks の実行が
#        失敗した
#
# 呼び出し側は 0 のときだけ skip し、1 と 2 では従来どおりレビューを要求する。2 を 1 と
# 分けるのは、deny の理由文で「テキスト系文書だけだが秘匿情報の検査で止めた」ことを示すため。
#
# ## 秘匿情報の検査 (gitleaks)
#
# テキスト系文書だけの skip ではレビュー (security review を含む) を行わないため、文書に
# 書き込まれた認証情報も検査されない。PATH に gitleaks がある (`command -v gitleaks` が成功
# する) 場合に限り、skip の前に範囲内の commit を次の形で検査する:
#
#   gitleaks git --no-banner --redact --ignore-gitleaks-allow --log-level error
#     --exit-code 1 --timeout 30 [--config <一時ファイル>] --gitleaks-ignore-path <一時ディレクトリ>
#     --log-opts="<base の OID>..<head の OID>" <repo の toplevel>
#
#   - 範囲の両端には、1-4 の判定で解決済みの commit OID を使う
#   - `--timeout 30` は、gate を実行する Claude Code の command hook の既定の timeout
#     (60 秒) より短くするための値。hook が timeout すると gate の判定が働かないため、
#     gitleaks 自身の timeout で先に終わらせ、レビュー必須 (戻り値 2) に倒す
#   - `--ignore-gitleaks-allow` は、文書中に `gitleaks:allow` コメントを書くだけで検出を
#     抑止できないようにするために付ける (テキスト系文書だけの変更は、レビュー無しで
#     そのコメントを書き込める)
#   - stdout / stderr はどちらも捨てる。検出した認証情報の内容を hook や wrapper の出力に
#     流さないため
#   - 終了コード 0 だけを「検出なし」とし、それ以外 (検出・timeout・実行エラー) はすべて
#     exit code 2 にする。repo の toplevel を解決できない場合も 2 にする
#
# gitleaks の設定 (`.gitleaks.toml`) と ignore (`.gitleaksignore`) は、gitleaks の既定の
# 読み方 (走査対象の toplevel の `.gitleaks.toml` と、cwd の `.gitleaksignore`) をさせず、
# 範囲の起点 (解決済みの base OID) の tree から読む。既定の読み方では working tree の
# 未追跡ファイルも読まれるが、push gate の clean 検査は未追跡ファイルを見ず、merge gate には
# clean 検査が無い。そのため未追跡の設定ファイルを置くだけで検出を抑止できてしまう。base の
# tree は、テキスト系文書だけの変更では書き換えられない (どちらのファイルもテキスト系文書では
# ない):
#
#   - 利用者の環境変数 `GITLEAKS_CONFIG` か `GITLEAKS_CONFIG_TOML` が空でない場合は、
#     利用者自身の設定として尊重し、`--config` を渡さない
#   - それ以外の場合、base の tree に `.gitleaks.toml` があればその内容を一時ファイルに
#     取り出して `--config` に渡す。無ければ `[extend]` と `useDefault = true` だけを書いた
#     一時ファイルを渡す (gitleaks の既定のルールだけを使う)
#   - `--gitleaks-ignore-path` には常に一時ディレクトリを渡す。base の tree に
#     `.gitleaksignore` があれば、その内容をこの一時ディレクトリの `.gitleaksignore` に
#     取り出す。無ければ空のディレクトリのまま渡す
#   - 一時ファイルと一時ディレクトリは `mktemp` で作り、関数を抜ける前に消す (source する
#     script の trap には手を加えない)。作成や取り出しに失敗した場合は exit code 2 にする
#
# gitleaks が PATH に無い環境では検査せず、1-4 を満たせば 0 を返す。
#
# ## push の予測 (is_text_only_push_in)
#
# push gate と push 前の codex review wrapper が使う。index と working tree が clean
# (`git diff-index --quiet --cached HEAD` と `git diff-files --quiet` がともに成功) で、
# かつ `origin/<base>..HEAD` が is_text_only_range_in を満たすときだけ成立する。exit code は
# is_text_only_range_in と同じ (index / working tree が clean でなければ 1。範囲の判定の
# 2 はそのまま返す)。
#
# ## 利用側
#
#   - push gate (block-pre-push.sh / block-pre-push-codex.sh): 空 push の skip の直後に
#     is_text_only_push_in で判定し、0 ならマーカーを要求せずに通す
#   - push 前の codex review wrapper: 空 push の判定の直後に is_text_only_push_in で判定し、
#     0 なら codex review を実行せず、pending attestation も書かずに終了する
#   - merge gate (block-pre-merge.sh): gh が返す PR の base commit (`baseRefOid`) と head SHA
#     を is_text_only_range_in に渡し、0 ならレビュー記録を照合せずに通す
#   - merge 前の codex review wrapper: PR の base commit (`baseRefOid`) と HEAD を
#     is_text_only_range_in に渡し、0 なら codex review を実行せず、pending attestation も
#     書かずに終了する
#
# gate は 2 のとき、deny の理由文に秘匿情報の検査でレビューを要求した旨の 1 文を加える
# (認証情報の内容や gitleaks の出力は含めない)。wrapper は 2 のとき通常どおり codex review を
# 実行する。

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
      [aA][gG][eE][nN][tT][sS].[oO][vV][eE][rR][rR][iI][dD][eE].[mM][dD] | \
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
        */.[cC][lL][iI][nN][eE][rR][uU][lL][eE][sS]/* | \
        */.[rR][oO][oO]/* | \
        */.[kK][iI][rR][oO]/* | \
        */.[cC][oO][nN][tT][iI][nN][uU][eE]/* | \
        */.[jJ][uU][nN][iI][eE]/* | \
        */.[aA][mM][aA][zZ][oO][nN][qQ]/* | \
        */[hH][oO][oO][kK][sS]/[pP][rR][oO][mM][pP][tT][sS]/* | \
        */.[gG][iI][tT][hH][uU][bB]/[iI][nN][sS][tT][rR][uU][cC][tT][iI][oO][nN][sS]/* | \
        */.[gG][iI][tT][hH][uU][bB]/[pP][rR][oO][mM][pP][tT][sS]/* | \
        */.[gG][iI][tT][hH][uU][bB]/[cC][hH][aA][tT][mM][oO][dD][eE][sS]/*)
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

# _text_paths_extract_base_file <target_cwd> <base_oid> <path> <destination>
# base の tree に <path> の blob があれば、その内容を <destination> に書き出す。
# exit code: 0 = 書き出した、1 = base の tree に無い、2 = 有るが書き出せなかった。
_text_paths_extract_base_file() {
  local target_cwd="$1"
  local base_oid="$2"
  local path="$3"
  local destination="$4"
  local -a git_prefix=()
  if [ -n "$target_cwd" ]; then
    git_prefix=(-C "$target_cwd")
  fi
  git "${git_prefix[@]}" cat-file -e "${base_oid}:${path}" 2>/dev/null || return 1
  git "${git_prefix[@]}" cat-file blob "${base_oid}:${path}" >"$destination" 2>/dev/null || return 2
  return 0
}

# _text_paths_run_gitleaks <target_cwd> <base_oid> <head_oid> <toplevel> <work_dir>
# <work_dir> (呼び出し側が作り、消す一時ディレクトリ) に gitleaks の設定と ignore を用意して
# gitleaks を起動する。gitleaks の終了コード 0 のときだけ 0 を返し、設定の用意に失敗した場合も
# 0 以外を返す。設定の決め方はファイルヘッダの「秘匿情報の検査」を参照。
_text_paths_run_gitleaks() {
  local target_cwd="$1"
  local base_oid="$2"
  local head_oid="$3"
  local toplevel="$4"
  local work_dir="$5"
  local extract_status

  local -a config_args=()
  if [ -z "${GITLEAKS_CONFIG:-}" ] && [ -z "${GITLEAKS_CONFIG_TOML:-}" ]; then
    local config_path="${work_dir}/gitleaks.toml"
    extract_status=0
    _text_paths_extract_base_file "$target_cwd" "$base_oid" .gitleaks.toml "$config_path" \
      || extract_status=$?
    case "$extract_status" in
      0) ;;
      1) printf '[extend]\nuseDefault = true\n' >"$config_path" 2>/dev/null || return 1 ;;
      *) return 1 ;;
    esac
    config_args=(--config "$config_path")
  fi

  local ignore_dir="${work_dir}/ignore"
  mkdir "$ignore_dir" 2>/dev/null || return 1
  extract_status=0
  _text_paths_extract_base_file "$target_cwd" "$base_oid" .gitleaksignore \
    "${ignore_dir}/.gitleaksignore" || extract_status=$?
  case "$extract_status" in
    0 | 1) ;;
    *) return 1 ;;
  esac

  gitleaks git --no-banner --redact --ignore-gitleaks-allow --log-level error \
    --exit-code 1 --timeout 30 "${config_args[@]}" --gitleaks-ignore-path "$ignore_dir" \
    --log-opts="${base_oid}..${head_oid}" "$toplevel" \
    </dev/null >/dev/null 2>&1
}

# _text_paths_secrets_scan_passes <target_cwd> <base_oid> <head_oid>
# `<base_oid>..<head_oid>` の commit を gitleaks で検査し、終了コード 0 (検出なし) のときだけ
# 0 を返す。gitleaks が PATH に無い場合は検査せずに 0 を返す。起動の形、設定と ignore の決め方、
# 出力を捨てる理由はファイルヘッダの「秘匿情報の検査」を参照。
_text_paths_secrets_scan_passes() {
  local target_cwd="$1"
  local base_oid="$2"
  local head_oid="$3"
  command -v gitleaks >/dev/null 2>&1 || return 0
  local -a git_prefix=()
  if [ -n "$target_cwd" ]; then
    git_prefix=(-C "$target_cwd")
  fi
  local toplevel
  toplevel=$(git "${git_prefix[@]}" rev-parse --show-toplevel 2>/dev/null) || return 1
  [ -n "$toplevel" ] || return 1

  # 一時ディレクトリはこの関数の中で作って消す (source する script の trap に手を加えない)。
  local work_dir
  work_dir=$(mktemp -d "${TMPDIR:-/tmp}/text-paths-gitleaks.XXXXXX" 2>/dev/null) || return 1
  if [ -z "$work_dir" ] || [ ! -d "$work_dir" ]; then
    return 1
  fi
  local scan_status=0
  _text_paths_run_gitleaks "$target_cwd" "$base_oid" "$head_oid" "$toplevel" "$work_dir" \
    || scan_status=$?
  rm -rf "$work_dir" 2>/dev/null
  return "$scan_status"
}

# is_text_only_range_in <target_cwd> <base_rev> <head_rev>
# exit code: 0 = 範囲内の変更がテキスト系文書だけで秘匿情報の検査も通った、1 = テキスト系
# 文書だけではない (git の失敗を含む)、2 = テキスト系文書だけだが秘匿情報の検査で止めた。
# 判定条件はファイルヘッダの「範囲の判定」と「秘匿情報の検査」を参照。target_cwd が空文字
# なら現在の cwd で、非空なら `git -C <target_cwd>` で git を実行する (diff-hash.sh の `*_in`
# 関数と同じ扱い)。
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

  # 判定は 1 → 2 → 4 → 3 の順に行う (理由はファイルヘッダの「範囲の判定」を参照)。

  # 条件 1: 範囲の commit 数が 1 以上、上限以下であること。
  local max_commits=100
  local commit_count
  commit_count=$(git "${git_prefix[@]}" rev-list --count "${base_oid}..${head_oid}" 2>/dev/null) || return 1
  case "$commit_count" in
    "" | *[!0123456789]*) return 1 ;;
  esac
  if [ "$commit_count" -eq 0 ] || [ "$commit_count" -gt "$max_commits" ]; then
    return 1
  fi

  # 条件 2: 範囲内に merge commit が無いこと。
  local merge_count
  merge_count=$(git "${git_prefix[@]}" rev-list --min-parents=2 --count "${base_oid}..${head_oid}" 2>/dev/null) || return 1
  [ "$merge_count" = "0" ] || return 1

  # 条件 4: merge-base から head までの差分 (net diff)。git の呼び出しが 1 回で済むため、
  # 各 commit の走査より先に行う。
  local merge_base_oid
  merge_base_oid=$(git "${git_prefix[@]}" merge-base "$base_oid" "$head_oid" 2>/dev/null) || return 1
  [ -n "$merge_base_oid" ] || return 1
  _text_paths_diff_is_text_only "$target_cwd" "$merge_base_oid" "$head_oid" || return 1

  # 条件 3: 範囲内の各 commit を親とともに列挙する (`<commit> <親>...` の 1 行ずつ)。親が
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

  # 条件 5: 秘匿情報の候補が無いこと (gitleaks がある場合のみ検査する)。
  _text_paths_secrets_scan_passes "$target_cwd" "$base_oid" "$head_oid" || return 2

  return 0
}

# is_text_only_push_in <target_cwd> <base>
# exit code: is_text_only_range_in と同じ (0 = skip してよい、1 = テキスト系文書だけではない
# または git の失敗、2 = テキスト系文書だけだが秘匿情報の検査で止めた)。判定条件はファイル
# ヘッダの「push の予測」を参照。target_cwd の扱いは is_text_only_range_in と同じ。
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
