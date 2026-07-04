#!/bin/bash
# default-branch.sh
# git-guardrails の各 hook で共有する「カレントブランチがデフォルトブランチか」判定。
#
# 3 つの hook (push / commit / pr) で同一のブランチ名集合と判定ロジックを使うため、
# 1 か所に集約してドリフトを防ぐ。

# デフォルトブランチとして扱うブランチ名集合。GitHub のリポジトリでは master か main の
# どちらかが慣習的に使われる。両方を保護対象として扱う (個別リポジトリの実 default
# branch を `git symbolic-ref refs/remotes/origin/HEAD` で動的解決する手もあるが、
# `origin/HEAD` が未設定の clone も存在し信頼度が低いため、ハードコードで cooperative
# 利用に十分な範囲をカバーする)。
DEFAULT_BRANCH_NAMES=("master" "main")

# 引数: <branch-name>
# 戻り値: 0 = デフォルトブランチ、1 = それ以外
is_default_branch() {
  local target="$1"
  local b
  for b in "${DEFAULT_BRANCH_NAMES[@]}"; do
    [ "$target" = "$b" ] && return 0
  done
  return 1
}

# 出力: カレントブランチ名 (detached HEAD 等で取得できないなら空文字列)
current_branch() {
  git symbolic-ref --short HEAD 2>/dev/null
}

# 引数: <token>
# 出力: shell quote (`"..."` / `'...'`) を 1 段だけ剥がしたトークン
#
# Claude Code が tool_input.command に渡す文字列は shell 実行前なので、`gh pr create
# --head "master"` のようにユーザーが引用符を付けた場合、token には quote 文字が残る。
# `is_default_branch "master"` のように生のまま比較すると bypass されるため、判定前に
# 1 段剥がす。多重引用 (`"\"master\""`) は cooperative 利用では出ないので未対応。
strip_shell_quotes() {
  local s="$1"
  case "$s" in
    \"*\") s="${s%\"}"; s="${s#\"}" ;;
    \'*\') s="${s%\'}"; s="${s#\'}" ;;
  esac
  printf '%s' "$s"
}

# 引数: <refspec-part>
# 出力: refspec の片側 (左/右) を branch name として完全一致比較するための正規化形
#
# Git が受け付ける refspec 形式は:
#   - `master` / `main` (bare branch name)
#   - `+master` / `+main` (force-push prefix)
#   - `refs/heads/master` / `refs/heads/main` (full ref name)
#   - `HEAD` (シンボリック)
# このうち `+master` や `refs/heads/master` を完全一致比較で見落とすと、master 更新が
# 素通りする bypass になる。`+` と `refs/heads/` を順に剥がして branch 部分を取り出す。
# `HEAD` は対称解釈 (相手側が master/main なら別チェックで拾える) なのでそのまま。
normalize_refspec_part() {
  local s="$1"
  s="${s#+}"
  s="${s#refs/heads/}"
  printf '%s' "$s"
}

# 引数: <command-string>
# 出力: shell quote (`"..."` / `'...'`) で囲まれた範囲を空白に置換した command 文字列
#
# `has_target_mismatch_prefix` のような raw text grep は `git commit -m "docs: add cd
# instructions"` の引用符内の "cd" を誤検出する。検出前に quote 内を消すことで、
# テキスト参照と本物のコマンド prefix を区別する。
#
# **順序重要**: double quote を先に剥がす。逆順だと `git commit -m "don't" && cd /other
# && git commit -m "won't"` のような英文アポストロフィを含む chained コマンドで、最初の
# `'` から最後の `'` までを 1 つの single-quoted region と誤認し、間に挟まれた
# `&& cd /other &&` ごと食ってしまう。結果として `cd /other` が消えて
# `has_target_mismatch_prefix` の cd 検出を素通りさせる致命的バイパスになる。
# pre-push-review/block-pre-push.sh の同種 sed と同順で揃える。
#
# sed が行単位で動く都合上、複数行にまたがる double-quoted heredoc 等は対象外だが、
# has_target_mismatch_prefix の入力は単一行 command 想定なので問題なし。
strip_quoted_text() {
  printf '%s' "$1" | sed -E -e 's/"[^"]*"/ /g' -e "s/'[^']*'/ /g"
}

# 引数: <command-string>
# 出力: single quote (`'...'`) で囲まれた範囲のみを空白に置換した command 文字列
# (double quote 内の内容はそのまま残す)
#
# `strip_quoted_text` は single/double 両方の quote 領域を除去するため、dquote 内で
# 実行される command substitution `$(...)` / `<(...)` / `>(...)` を検出する第 2 パス
# (dquote 内 invocation の opener-anchored 保守的 deny。詳細は各 hook の置換 shape check
# セクション参照) には使えない (dquote 領域ごと消えて substitution の中身も消えてしまう
# ため)。本関数は single quote 領域のみを空白化し、dquote 内容は検出対象として残す。
#
# **quote 文脈を追跡する純 bash の文字 walk** (cmd-parser.sh の split_command と同じ
# 規律) で実装する: dquote 内では `'` を single quote の開始として扱わない (bash の実
# 挙動と一致させるため)。旧実装 (sed の `s/'[^']*'/ /g`) は dquote 文脈を知らない naive
# regex だったため、`echo "it's $(git push origin master) don't"` のように dquote 内に
# 英文アポストロフィが 2 個 (たまたま対になる) 現れる入力で、その間 (`$(git push ...)`
# を含む) を single-quote 領域と誤認して丸ごと空白化してしまい、第 2 パス (opener-
# anchored 保守的 deny) が bypass される実害があった (security review で実測確認、#F8)。
#
# **性能について**: 本関数は raw segment に `$(` / `<(` / `>(` のいずれかを含む場合の
# みホストから呼ばれる cold path (置換 shape check の第 2 パス専用) であり、大半の
# Bash 呼び出しは本関数を一度も呼ばない。cmd-parser.sh の
# `_normalize_line_continuations_impl` が警告する「純 bash の 1 文字ループは
# `result+=` の O(N) 再割当てで全体 O(N^2) になり hot path (全 Bash 呼び出しで発火) で
# は致命的」という問題は、呼び出し頻度が低くコマンド長も通常数百〜数千文字に収まる
# 本関数の cold path では実害が無いため、sed fallback を持たない単純な純 bash 実装で
# 十分とする。
strip_squoted_text() {
  local cmd="$1"
  local i=0 len=${#cmd}
  local in_squote=0 in_dquote=0
  local result=""

  while [ "$i" -lt "$len" ]; do
    local c="${cmd:$i:1}"

    if [ "$in_squote" -eq 1 ]; then
      # single-quote 領域全体 (開始 `'` から終了 `'` まで) を 1 個の空白に置換する
      # (strip_quoted_text の sed 版と同じ「マッチ全体を 1 空白に」という契約に揃える)。
      # 終了 `'` を見つけるまでは何も出力に追加しない。
      if [ "$c" = "'" ]; then
        in_squote=0
        result+=" "
      fi
      i=$((i+1)); continue
    fi

    if [ "$in_dquote" -eq 1 ]; then
      if [ "$c" = "\\" ]; then
        local nc="${cmd:$((i+1)):1}"
        case "$nc" in
          '$'|'`'|'"'|'\\')
            result+="$c$nc"; i=$((i+2)); continue ;;
        esac
      fi
      # dquote 内では `'` は特別扱いしない (bash の実挙動どおり)。dquote 内容はすべて
      # そのまま保持する (これが strip_quoted_text との決定的な違い)。
      [ "$c" = '"' ] && in_dquote=0
      result+="$c"
      i=$((i+1)); continue
    fi

    case "$c" in
      "'") in_squote=1; i=$((i+1)) ;;
      '"') in_dquote=1; result+="$c"; i=$((i+1)) ;;
      '\\')
        local nc="${cmd:$((i+1)):1}"
        result+="$c$nc"
        i=$((i+2))
        ;;
      *) result+="$c"; i=$((i+1)) ;;
    esac
  done

  printf '%s' "$result"
}

# 引数: <segment> (trim 済みで先頭が `(` または `{` である前提)
# stdout: 先頭の開き文字に対応する閉じ文字 (`)`/`}`) の index (0-based)。見つかれば
#         標準出力へ index を出力して exit 0、見つからなければ何も出力せず exit 1
#         (bash 3.2 には nameref が無いため、呼び出し側は
#         `_idx="$(find_group_close "$seg")"` で受け取り `[ -n "$_idx" ]` で判定する)。
#
# グループ unwrap (subshell `(...)` / brace group `{...}` の中身を取り出して再分割する
# 処理) で、「segment 内の**最後**の `)`/`}` で切る」という文字列ヒューリスティックは
# `(git push origin master) > $(mktemp)` のような入力で `$(mktemp)` 側の `)` を誤って
# 選んでしまい、外側 group の中身が `master) > $(mktemp` のように壊れて refspec 比較を
# 素通りする bypass になっていた (code-reviewer review で実測確認、#F9)。本関数は
# quote 文脈 (squote/dquote、dquote 内 escape) を追跡しつつ depth (先頭の開き文字を
# depth 1 として、quote 外の `(`/`{` で depth+1、quote 外の `)`/`}` で depth-1) を数え、
# depth が 0 に戻った位置 = **先頭の開き文字に対応する閉じ文字** を返す。`(`/`{` を
# 区別せず同一 depth に積むのは、bash の実際の入れ子でも type を跨いだ深さ管理で
# 「先頭に対応する閉じ」が一意に定まるため (type 不一致のネストは元より不正な bash
# 構文であり、cooperative 利用では想定しない単純化)。
find_group_close() {
  local seg="$1"
  local i=1 len=${#seg}
  local depth=1
  local in_squote=0 in_dquote=0

  while [ "$i" -lt "$len" ]; do
    local c="${seg:$i:1}"

    if [ "$in_squote" -eq 1 ]; then
      [ "$c" = "'" ] && in_squote=0
      i=$((i+1)); continue
    fi

    if [ "$in_dquote" -eq 1 ]; then
      if [ "$c" = "\\" ]; then
        local nc="${seg:$((i+1)):1}"
        case "$nc" in
          '$'|'`'|'"'|'\\') i=$((i+2)); continue ;;
        esac
      fi
      [ "$c" = '"' ] && in_dquote=0
      i=$((i+1)); continue
    fi

    case "$c" in
      "'") in_squote=1; i=$((i+1)) ;;
      '"') in_dquote=1; i=$((i+1)) ;;
      '\\') i=$((i+2)) ;;
      '('|'{') depth=$((depth+1)); i=$((i+1)) ;;
      ')'|'}')
        depth=$((depth-1))
        if [ "$depth" -eq 0 ]; then
          printf '%s' "$i"
          return 0
        fi
        i=$((i+1))
        ;;
      *) i=$((i+1)) ;;
    esac
  done

  # 見つからなかった (未終端 group: 不正な入力・記述途中等)。
  return 1
}

# 引数: <reason>
# stdout: PreToolUse deny の jq ペイロード
# 3 hook で同形だったペイロードを集約して、フィールド名やキー順のドリフトを防ぐ。
emit_deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
}

# 引数: <command-string>
# 戻り値: 0 = target-mismatch を起こす可能性ありの prefix を含む / 1 = 含まない
#
# hook は cwd の `git symbolic-ref` でカレントブランチ・対象 repo を判定するため、
# コマンドの前段が以下いずれかで cwd / カレントブランチを書き換える形だと「hook 検証時の
# target」と「実コマンドの target」が乖離する (target-mismatch):
#   - 対象 repo の切替: `cd /other && git push` / `git -C /other push` / `GIT_DIR=...`
#   - 対象 branch の切替: `git switch master && git commit` / `git checkout main && ...`
# 別 repo の master を更新する経路、別 worktree の default branch を巻き込む経路、もしくは
# default branch に切り替えてから commit/push/PR 作成する経路は、いずれも default branch
# 保護の趣旨を直接破壊するため、cwd / カレントブランチ解析よりも保守側に倒して deny する。
#
# 対象 prefix:
#   - `cd <dir>` / `pushd <dir>` / `popd`
#   - `git -C <dir> ...` / `git --git-dir=...` / `git --work-tree=...`
#   - `GIT_DIR=` / `GIT_WORK_TREE=` / `GIT_INDEX_FILE=` 環境変数 (bare assignment)
#   - 上記環境変数を `export` / `declare` / `typeset` / `readonly` でセットする形
#   - `git switch master` / `git switch main` / `git checkout master` / `git checkout main`
#     (`-c master` / `-b main` で master/main を新規作成する形も同列)
has_target_mismatch_prefix() {
  # 引用符内の文字列 (commit message やオプション値) を空白に置換してから検出する。
  # `git commit -m "docs: add cd instructions"` のような quoted text に "cd" を含む
  # ケースで raw grep が誤検出するのを防ぐため。
  local cmd
  cmd=$(strip_quoted_text "$1")
  # subshell `(cd ...; git commit ...)` / brace group `{ cd ...; git commit ...; }` /
  # command substitution `$(cd ...; git ...)` のように `()` / `{}` で囲まれたグループ越しの
  # target-mismatch を捕捉できるよう、これらを空白に正規化する。pre-push-review
  # block-pre-push.sh で同じパターンを採用している (PR #20 の `2d4f2b0` で旧 pre-commit-review に
  # 導入され、pre-push-review への移行時に継承された)。
  # `[(){}]` を class にすると `}` がパラメータ展開と衝突するため、4 回に分けて置換する。
  cmd="${cmd//\(/ }"
  cmd="${cmd//\)/ }"
  cmd="${cmd//\{/ }"
  cmd="${cmd//\}/ }"
  # cd / pushd / popd
  # 右境界に `<>` を含めるのは、`cd>/dev/null /other && ...` のように cd 直後にスペース
  # 無しで redirection 演算子が来ても cd 本体は実行されるため。pre-push-review でも
  # 同種の意図 (= command 名直後の redirection を境界と認識) を `CHAIN_PREFIX_REGEX` の
  # `[[:space:]<>]` 境界 (block-pre-push.sh) で別経路として実装している。
  if printf '%s' "$cmd" \
    | grep -qE '(^|[;&|[:space:]])(cd|pushd|popd)([[:space:]<>;&|]|$)'; then
    return 0
  fi
  # bare assignment: GIT_DIR=... / GIT_WORK_TREE=... / GIT_INDEX_FILE=...
  if printf '%s' "$cmd" \
    | grep -qE '(^|[;&|[:space:]])(GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE)='; then
    return 0
  fi
  # export GIT_DIR=... / declare -x GIT_DIR=... / typeset -x ... / readonly ...
  if printf '%s' "$cmd" \
    | grep -qE '(^|[;&|[:space:]])(export|declare|typeset|readonly)[[:space:]]+(-[^[:space:];&|]+[[:space:]]+)*(GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE)([[:space:]=;&|]|$)'; then
    return 0
  fi
  # git -C <dir> / git --git-dir=... / git --work-tree=...
  if printf '%s' "$cmd" \
    | grep -qE '(^|[;&|[:space:]])git[[:space:]]+([^[:space:];&|]+[[:space:]]+)*(-C|--git-dir|--work-tree)([[:space:]=;&|]|$)'; then
    return 0
  fi
  # git switch master / git checkout master / git switch -c master / git checkout -b master 等。
  # 実コマンド前に default branch (master/main) へ切り替える chained 形式は、後続の
  # commit/push/pr が `current_branch` でなく master/main 上で実行される経路になる。
  # `git switch <flags>* <branch>` / `git checkout <flags>* <branch>` の <branch> 部分が
  # master/main の場合に deny する。`-c new-branch` / `-b new-branch` で master/main を
  # 新規作成する形も同列に扱う。
  # 終端境界は `[[:space:]]` と `;`/`&`/`|` の両方を含める (改行を `;` に正規化したコマンド
  # 列で `git switch master;git commit` の `master` の直後 `;` を境界として認識するため)。
  if printf '%s' "$cmd" \
    | grep -qE '(^|[;&|[:space:]])git[[:space:]]+(switch|checkout)([[:space:]]+-[^[:space:];&|]+)*[[:space:]]+(master|main)([[:space:]]|[;&|]|$)'; then
    return 0
  fi
  return 1
}

# 共通の deny 文言: target-mismatch prefix が原因で deny する場合のメッセージ。
# 3 hook で内容が揃うように 1 か所に readonly で置く。
readonly TARGET_MISMATCH_DENY_REASON='デフォルトブランチ保護フックは hook 実行時の cwd / カレントブランチを基に判定するため、対象 repo / branch を切り替える前段を含むコマンドは保護を素通りさせる経路になります。具体的には:
  - 対象 repo を切り替える: `cd <dir> && ...` / `git -C <dir> ...` / `GIT_DIR=...`
  - 対象 branch を切り替える: `git switch master && ...` / `git checkout main && ...`
これらは保守的に deny します。対象 repo / branch へ切り替えた後、別の Bash 呼び出しとして実コマンドを実行してください (master/main 上の commit / push / PR 作成は他の guardrail でも引き続き deny されます)。'
