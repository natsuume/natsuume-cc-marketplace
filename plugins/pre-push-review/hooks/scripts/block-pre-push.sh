#!/bin/bash
# block-pre-push.sh
# git push を未レビューの状態でブロックする PreToolUse フック。
#
# ## なぜ push 境界か
#
# pre-commit 境界だと:
#   - 1 commit ごとにレビューループが回り、N-commit PR では合計 N 回ループが走る
#   - /simplify edits や /codex:review 指摘修正が初期実装と同じ commit に混入し、
#     git log / blame / bisect の意味的解像度が失われる
#   - 中間 commit (WIP / 探索 / checkpoint) を残せない
#
# push 境界だと:
#   - PR 全差分に対して 1 周のループで済む (1-commit PR では同等、多 commit PR で削減)
#   - 中間 commit を自由に重ねられ、レビュー対応も独立 commit として記録できる
#   - **未レビューな commit を remote に到達させない** ため、PR 作成手段 (gh CLI / Web UI /
#     IDE / API) のいずれを使われても precondition (remote branch の存在) を破壊して
#     構造的に gate できる (「pre-PR matcher で gh pr create だけを止める」設計だと
#     人間の Web UI 操作で bypass される)。
#
# ## 動作
#
# 1. Bash command が `git push` を含むかを軽量フィルタで判定
# 2. cmd-parser で segment 分割 + tokenize
# 3. target-resolver で push 対象の実 cwd を決定的に解決 (cd / git -C / GIT_DIR= を考慮)
# 4. 解決不能 (subshell / pushd / wrapper 等) は保守的 deny
# 5. 解決した target cwd 上で:
#    - default branch (master/main) なら git-guardrails に委譲して skip
#    - branch 全差分 + 未コミット差分のハッシュを計算
#    - 2 マーカー (.claude-pre-push-simplified / .claude-pre-push-codex-reviewed) と一致
#      しなければ deny
# 6. push の引数解析: refspec が現在ブランチ以外 / `--all` / `--mirror` / `--tags` /
#    引用符付き引数 / `push.default=matching` 環境での bare push 等は deny
# 7. dirty-tree (target cwd の) は deny
#
# ## サポート外 / 限界
#
# - `bash -c "..."` のシェルラッパー経由 push はサポート外 (resolver が return 1)
# - `time git push ...` / `env git push ...` のような未対応 wrapper 経由は deny
# - subshell `(...)` / brace group `{...}` 経由は保守的 deny (cooperative 利用では稀)
# - pushd / popd 経由は保守的 deny (stack 保持していないため)
# - 別端末から実行された `git push` は Claude Code hook の原理的範囲外
# - default branch (master/main) 上での push は本フックで gate せず、git-guardrails の
#   block-default-branch-push.sh に委譲する (重複 deny メッセージを避けるため)

INPUT=$(cat)

# 大半の Bash 呼び出しは git push と無関係。jq を起動する前に粗フィルタで抜ける。
case "$INPUT" in
  *git*push*|*push*git*) ;;
  *) exit 0 ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
if [ -z "$COMMAND" ]; then
  exit 0
fi

# 行継続 `\<改行>` は実行時にバックスラッシュ+改行が消えて隣接トークンに連結される。
# 検出ロジックがこれを見落とさないよう、入力段階で空白に正規化する。
COMMAND="${COMMAND//$'\\\n'/ }"

deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
}

# `<<(` / `<<<(` / `>>(` の連続: bash 自体は syntax error で実行不能だが、 後段の sed strip
# が `>>` 等を redirection と誤認して `(...)` を食う経路を作るため、 sed の前に明示 deny して
# fail-closed に倒す (互換 shell や将来のシェル拡張で動く可能性に対する defense-in-depth)。
case "$COMMAND" in
  *'<<('*|*'<<<('*|*'>>('*)
    REASON=$(cat <<'EOF'
プッシュをブロックしました。 `<<(` / `<<<(` / `>>(` 形式のリダイレクト・パレン連続はサポート外です (bash 自体も syntax error として拒否する形式ですが、 hook 側で fail-closed に deny します)。

プロセス置換が必要なら `<(...)` / `>(...)` (前後に空白なし、または `> >(...)` のように間に空白を入れる) を使用してください。
EOF
)
    deny "$REASON"
    exit 0
    ;;
esac

# `&` を含む shell redirection (`2>&1` / `&>file` / `<<EOF` 等) を空白に置換する。
# cmd-parser は `&` を一律 separator として扱うため、 redirection 内の `&` を parallel
# separator と誤認して false-positive deny を起こす経路を塞ぐ目的。
#
# 単独の `<` / `>` は strip しない: process substitution `<(...)` `>(...)` の `<` / `>` まで
# 食って内部の `git push` を parser から隠蔽する critical bypass になるため。 process 置換は
# 別途 segment shape check で deny する。 単独 `<file` / `>file` には `&` が含まれず、
# parallel-separator 検出にも影響しない。
#
# trailing 文字クラスは「filename-safe な文字」のみ許容する positive-list を取る。 redirection
# target に substitution shape (`$(...)` / `` `...` `` / `<(...)` / `(...)`) や quote / brace 等
# 通常 filename に現れない文字が混ざった時点で sed が触らず、 残存した特殊文字に対して segment
# shape check が deny に倒せる。 negative-list (= 除外文字を列挙) では新しい bypass shape
# (例: `cat <<<$(git push)` / `cat <<<\`git push\``) を見つけるたびに除外を増やす後手対応に
# なるため、 攻撃面を絞れる positive-list を採用。 想定する filename 文字は alnum + `.` `/`
# `_` `-` `=` `+` `@` `:` で日常的なログファイル名・パス・heredoc terminator を覆う。
COMMAND=$(printf '%s' "$COMMAND" \
  | sed -E 's/[0-9]?(&>>|&>|>>|>\&|<\&|<<<|<<|<>)[[:space:]]*[A-Za-z0-9_./=+@:-]*/ /g')

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/cmd-parser.sh
source "$SCRIPT_DIR/lib/cmd-parser.sh"
# shellcheck source=lib/target-resolver.sh
source "$SCRIPT_DIR/lib/target-resolver.sh"
# shellcheck source=lib/diff-hash.sh
source "$SCRIPT_DIR/lib/diff-hash.sh"
# shellcheck source=lib/loop-counter.sh
source "$SCRIPT_DIR/lib/loop-counter.sh"
# shellcheck source=lib/markers.sh
source "$SCRIPT_DIR/lib/markers.sh"

# segment に分割し、 push を含む segment 数を数える。 quote 内の `git push` 文字列参照
# (`grep "git push" README` 等) は tokenize で skip されるため、 ここでは「token level
# で `git push` 呼び出しを含む segment」のみカウントする。
#
# 1 push command per Bash invocation を前提にする (= 同一コマンド内に複数 push があると
# 1 マーカー = 1 push 保証が崩れるため deny)。
#
# 加えて、 push の **前** にある単独の `&` (background) や `|` (pipeline) は **並列実行**
# となり、 markers gate 検証完了後に index / working tree が並行変更される経路になる (例:
# `git commit X & git push` で push 開始後に新規 unreviewed commit が作られて push に
# 巻き込まれる)。 push の **後** に置く `&` / `|` (例: `git push 2>&1 | tee log`) は後続
# command が push 動作に影響しないため許容する (race の元にならない)。
# `&&` / `||` / `;` は逐次実行なので位置に関わらず許容。
SEGMENTS=()
SEPARATORS=()
SEP_INDEX=0
while IFS= read -r line; do
  if [[ "$line" == SEP:* ]]; then
    SEPARATORS+=("${line#SEP:}")
    continue
  fi
  SEGMENTS+=("$line")
done < <(split_command "$COMMAND")

# 事前 shape チェック: subshell `(...)` / brace group `{...}` / shell wrapper (`bash -c` 等)
# / コマンド置換 `$(...)` / プロセス置換 `<(...)` `>(...)` / バッククォート `` `...` `` は
# 本 parser が安全に解析できない形式。 これら shape 内に `push` substring を含む segment
# を見つけたら、 push を hidden に持つ可能性があるため保守的 deny する。
# (実 push を持たない subshell / brace / wrapper / 置換は許容する。)
for seg in "${SEGMENTS[@]}"; do
  trimmed="${seg#"${seg%%[![:space:]]*}"}"
  trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
  case "$trimmed" in
    *push*) ;;
    *) continue ;;
  esac
  case "$trimmed" in
    \(*|\{*)
      REASON=$(cat <<'EOF'
プッシュをブロックしました。 サブシェル `(...)` や ブレースグループ `{...}` 内の `git push` はサポート外です (本 parser では cwd の伝播セマンティクスを正確に解析できないため、 保守的に deny します)。

直接 `git push` を実行するか、 `cd dir && git push` / `git -C dir push` 等の対応形式を使用してください。
EOF
)
      deny "$REASON"
      exit 0
      ;;
  esac
  # コマンド置換 `$(...)` / プロセス置換 `<(...)` `>(...)` / バッククォート `` `...` ``
  # は内部の cwd / push を parser から隠蔽する経路。 push substring を含むなら deny する。
  case "$trimmed" in
    *'$('*|*'<('*|*'>('*|*'`'*)
      REASON=$(cat <<'EOF'
プッシュをブロックしました。 コマンド置換 `$(...)` / プロセス置換 `<(...)` / `>(...)` / バッククォート `` `...` `` 内の `git push` はサポート外です。

これらは内部の cwd や `git push` を本 parser から隠蔽する経路で、 例えば `echo $(cd /other; git push)` のようなコマンドは markers gate を素通りする bypass になり得ます。

直接 `git push` を実行するか、 置換結果を変数 / ファイルに格納してから push してください。
EOF
)
      deny "$REASON"
      exit 0
      ;;
  esac
  declare -a _first_toks
  tokenize_segment "$trimmed" _first_toks
  _fi=0
  _fn=${#_first_toks[@]}
  while [ "$_fi" -lt "$_fn" ]; do
    _ft="$(unquote_token "${_first_toks[$_fi]}")"
    if [[ "$_ft" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      _fi=$((_fi+1)); continue
    fi
    break
  done
  if [ "$_fi" -lt "$_fn" ]; then
    _fc="$(unquote_token "${_first_toks[$_fi]}")"
    case "$_fc" in
      bash|sh|zsh|dash|ksh|eval|exec|builtin|command|time|env)
        REASON=$(cat <<'EOF'
プッシュをブロックしました。 シェルラッパー (`bash -c "..."` / `sh -c ...` / `eval ...` / `time git push ...` / `env git push ...` 等) 経由の git push はサポート外です。

直接 `git push` を実行してください (前段コマンドが必要な場合は `cd dir && git push` 形式で連結できます)。
EOF
)
        deny "$REASON"
        exit 0
        ;;
    esac
  fi
  unset _first_toks
done

PUSH_SEGMENT=""
PUSH_SEGMENT_COUNT=0
for line in "${SEGMENTS[@]}"; do
  # token level で `git ... push` を確認 (text reference を排除)
  declare -a _toks
  tokenize_segment "$line" _toks
  _idx=0
  _n=${#_toks[@]}
  # env-var prefix を skip
  while [ "$_idx" -lt "$_n" ]; do
    _t="$(unquote_token "${_toks[$_idx]}")"
    if [[ "$_t" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      _idx=$((_idx+1))
    else
      break
    fi
  done
  # `git` を期待
  if [ "$_idx" -lt "$_n" ]; then
    _first="$(unquote_token "${_toks[$_idx]}")"
    if [ "$_first" = "git" ]; then
      _idx=$((_idx+1))
      # global option を walk して subcommand を探す
      while [ "$_idx" -lt "$_n" ]; do
        _opt="$(unquote_token "${_toks[$_idx]}")"
        case "$_opt" in
          -C|--git-dir|--work-tree|-c|--config|--config-env)
            _idx=$((_idx+2)); continue ;;
          --git-dir=*|--work-tree=*) _idx=$((_idx+1)); continue ;;
          -*) _idx=$((_idx+1)); continue ;;
          push)
            PUSH_SEGMENT_COUNT=$((PUSH_SEGMENT_COUNT+1))
            if [ -z "$PUSH_SEGMENT" ]; then
              PUSH_SEGMENT="$line"
            fi
            break ;;
          *) break ;;
        esac
      done
    fi
  fi
  unset _toks
done

# `git push` を一つも含まないなら本 hook 対象外。
if [ "$PUSH_SEGMENT_COUNT" -eq 0 ]; then
  exit 0
fi

# 同一コマンド内に push が複数あると、 1 マーカー = 1 push 保証が崩れる。
if [ "$PUSH_SEGMENT_COUNT" -gt 1 ]; then
  REASON=$(cat <<'EOF'
プッシュをブロックしました。同一 Bash 呼び出し内に複数の `git push` 呼び出しが含まれています (1 マーカー = 1 push を保証するため)。

`git push` は単独の Bash コマンドとして実行し、後続の push は別の Bash 呼び出しに分けてください。
EOF
)
  deny "$REASON"
  exit 0
fi

# 単独の `&` (background) や `|` (pipeline) で push 含むコマンドが連結されている場合は
# 位置を問わず deny する。 bash は `cmd1 | cmd2` の両側を同時起動し、 `cmd & cmd2` も
# `cmd` を background で走らせ `cmd2` を foreground で並走させるため、 hook の markers gate
# 検証完了後に並走 cmd が index / working tree / refs を変更し得る race 経路となる
# (例: `git push | git commit --allow-empty -m x` / `git commit X & git push`)。
# downstream を `tee` / `grep` 等の非 mutating に絞る allowlist は parser の複雑度に対して
# 価値が薄いため採らず、 logging / filtering は別の Bash 呼び出しか file redirection
# (`git push > log.txt 2>&1`) で代替してもらう設計。
# `&&` / `||` / `;` (sequential) は逐次実行で race にならず許容。
for sep in "${SEPARATORS[@]}"; do
  case "$sep" in
    "&"|"|")
      REASON=$(cat <<'EOF'
プッシュをブロックしました。 単独の `&` (background) や `|` (pipeline) で `git push` を含むコマンドを連結する形式はサポート外です。

これらの区切りは bash が両側を **同時** に起動するため、 hook の markers gate 検証完了後に並走 cmd が index / working tree / refs を変更すると、 未レビュー commit が push に巻き込まれる race 経路になります (例: `git push | git commit --allow-empty -m x` / `git commit X & git push`)。

連結が必要なら `&&` (success-and) / `||` (success-or) / `;` (sequential) のような **逐次** 実行区切りを使用してください。 push 出力を logging したい場合は file redirection (`git push > log.txt 2>&1`) か、 push 完了後に別の Bash 呼び出しでファイルを処理してください。
EOF
)
      deny "$REASON"
      exit 0
      ;;
  esac
done

# `git push --help` (もしくは `-h`) ならスキップ。
declare -a PUSH_TOKENS
tokenize_segment "$PUSH_SEGMENT" PUSH_TOKENS
PUSH_HAS_HELP=0
for tok in "${PUSH_TOKENS[@]}"; do
  t="$(unquote_token "$tok")"
  case "$t" in
    -h|--help) PUSH_HAS_HELP=1; break ;;
  esac
done
if [ "$PUSH_HAS_HELP" -eq 1 ]; then
  exit 0
fi

# `--dry-run` / `-n` push は remote ref を更新しない。 markers の状態に関わらず通す。
PUSH_HAS_DRY_RUN=0
for tok in "${PUSH_TOKENS[@]}"; do
  t="$(unquote_token "$tok")"
  case "$t" in
    --dry-run|-n) PUSH_HAS_DRY_RUN=1; break ;;
  esac
done
if [ "$PUSH_HAS_DRY_RUN" -eq 1 ]; then
  exit 0
fi

# `--all` / `--mirror` / `--tags` は複数参照の一括 push でマーカー検証対象外のコミットが
# 混入する。
for tok in "${PUSH_TOKENS[@]}"; do
  t="$(unquote_token "$tok")"
  case "$t" in
    --all|--mirror|--tags)
      REASON=$(cat <<'EOF'
プッシュをブロックしました。`--all` / `--mirror` / `--tags` (複数参照の一括 push) は本プラグインのレビュー gate 対象外です。

本プラグインは「現在ブランチの全差分 + 未コミット差分」のハッシュでマーカーを検証します。これらのオプションは現在ブランチ以外の参照 (他ローカルブランチ / tag) も remote に送るため、それらのコミットがレビュー gate を素通りします。

`--tags` を使いたい場合は、tag が指す commit を含むブランチを通常通りレビューして push し、別の Bash 呼び出しで `git push origin <tag-name>` のように個別 tag を push してください。
EOF
)
      deny "$REASON"
      exit 0
      ;;
  esac
done

# target cwd を resolve。 解析不能な形式 (subshell / pushd / wrapper 等) は保守的 deny。
TARGET_CWD=""
if ! TARGET_CWD=$(resolve_push_target "$COMMAND"); then
  REASON=$(cat <<'EOF'
プッシュをブロックしました。本フックの parser では target cwd を決定的に解析できない形式が含まれています。

サポート外の例:
  - `bash -c "..."` 等のシェルラッパー
  - `(cd dir && git push)` のサブシェル
  - `{ cd dir; git push; }` のブレースグループ
  - `pushd` / `popd` (本 parser は stack 保持していない)
  - `export GIT_DIR=...` / `declare GIT_DIR=...` 等の env-var 永続化
  - `--work-tree=...` (work tree override)
  - `time git push ...` / `env git push ...` 等の未対応 wrapper

`git push` を直接実行するか、対応している `cd dir && git push` / `git -C dir push` /
`GIT_DIR=path/.git git push` 形式で連結してください。
EOF
)
  deny "$REASON"
  exit 0
fi

# target cwd で git 操作を行う。 すべて `git -C "$TARGET_CWD" ...` 経由で実行することで、
# 「hook 検証の target」と「実 push の target」が完全に一致する。
if ! GIT_DIR=$(git -C "$TARGET_CWD" rev-parse --git-dir 2>/dev/null); then
  REASON=$(cat <<EOF
プッシュをブロックしました。解決された target cwd \`${TARGET_CWD}\` が git リポジトリではありません。

\`cd <dir> && git push\` / \`git -C <dir> push\` 等で指定したパスが正しい git リポジトリを指しているか確認してください。
EOF
)
  deny "$REASON"
  exit 0
fi

# GIT_DIR が相対パスの場合、 TARGET_CWD と組み合わせて絶対パスに解決
if [[ "$GIT_DIR" != /* ]]; then
  GIT_DIR="$TARGET_CWD/$GIT_DIR"
fi

# detached HEAD 等で現在ブランチが取れない場合は skip (cooperative)。
BRANCH=$(git -C "$TARGET_CWD" symbolic-ref --short HEAD 2>/dev/null) || exit 0

# default branch (master/main) では gate しない。git-guardrails の
# block-default-branch-push.sh が独立に deny するため、 こちらでも deny すると
# メッセージが重複して混乱する。
case "$BRANCH" in
  master|main) exit 0 ;;
esac

# default branch を target cwd で解決。 deletion-only / tag-only push は BASE 不要のため、
# ここでは取得のみして empty 許容。 fail-closed deny は real push の hash 計算直前で行う。
BASE=$(detect_base_branch "$TARGET_CWD") || BASE=""

# refspec / オプションを再走査して deny 判定を行う。
HAS_DELETE_FLAG=0
for tok in "${PUSH_TOKENS[@]}"; do
  t="$(unquote_token "$tok")"
  case "$t" in
    --delete|-d) HAS_DELETE_FLAG=1; break ;;
  esac
done

SAW_REMOTE=0
REFSPEC_COUNT=0
HAS_REAL_PUSH=0  # 1 if any refspec sends commits (= HEAD or current branch)
SAW_GIT=0
SAW_PUSH=0
_SKIP_NEXT=0  # `-C dir` / `--git-dir dir` / `-c key=val` のような separate-arg option の
              # 引数を次イテレーションで skip するためのフラグ。 ループ先頭で必ず消費する
              # ことで SAW_PUSH 遷移を跨いでも leak しない (各 phase で消費漏れすると後段
              # の refspec 検証が誤動作する)。
for tok in "${PUSH_TOKENS[@]}"; do
  t="$(unquote_token "$tok")"
  # 直前 iter で separate-arg option の存在を検出した場合、 現 token はその引数なので
  # 無条件 skip する (state machine のどのフェーズでも統一的に動く)。
  if [ "$_SKIP_NEXT" -eq 1 ]; then
    _SKIP_NEXT=0
    continue
  fi
  # env-var prefix / wrapper / git の global option を skip
  if [ "$SAW_GIT" -eq 0 ]; then
    if [[ "$t" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then continue; fi
    if [ "$t" = "git" ]; then SAW_GIT=1; continue; fi
    continue
  fi
  if [ "$SAW_PUSH" -eq 0 ]; then
    case "$t" in
      -C|--git-dir|--work-tree|-c|--config|--config-env)
        _SKIP_NEXT=1; continue ;;
      --git-dir=*|--work-tree=*|-*) continue ;;
      push) SAW_PUSH=1; continue ;;
      *) continue ;;
    esac
  fi
  # push の `--repo=<repo>` / `--repo <repo>` は remote を option 経由で指定する形式。
  # 後続の非オプション token は **refspec** として扱う必要があるため、 SAW_REMOTE=1 に
  # 倒して以降を refspec としてパースする (こうしないと `git push --repo=origin HEAD:main`
  # が refspec を remote 名として食って destination-override check を素通りする)。
  case "$t" in
    --repo=*) SAW_REMOTE=1; continue ;;
    --repo) _SKIP_NEXT=1; SAW_REMOTE=1; continue ;;
  esac
  # その他 push のオプションを skip (`-u`, `--force`, `--delete` 等)。
  case "$t" in
    -*) continue ;;
  esac
  # 最初の非オプションは remote 名と仮定
  if [ "$SAW_REMOTE" -eq 0 ]; then
    SAW_REMOTE=1
    continue
  fi
  REFSPEC_COUNT=$((REFSPEC_COUNT+1))
  # `+` / `refs/heads/` を剥がし、 source / destination を `:` で分離する。
  raw="${t#+}"
  case "$raw" in
    *:*)
      src="${raw%%:*}"
      dst="${raw#*:}"
      ;;
    *)
      src="$raw"
      dst=""
      ;;
  esac
  src="${src#refs/heads/}"
  dst_norm="${dst#refs/heads/}"
  # destination が指定されており、 かつ現在ブランチと一致しない場合は deny。
  # `git push origin HEAD:main` のような destination override で reviewed branch の commit を
  # 別 ref (例: default branch) に投影する経路を防ぐ。 destination 省略 (= source と同名で
  # push) や `:branch` (source 空 = remote 削除、 後続で許容) は対象外。
  if [ -n "$src" ] && [ -n "$dst" ] && [ "$dst_norm" != "$BRANCH" ] && [ "$dst_norm" != "HEAD" ]; then
    REASON=$(cat <<EOF
プッシュをブロックしました。 push 引数の refspec \`${t}\` で source (\`${src:-空}\`) と destination (\`${dst_norm}\`) が異なる形式 (= destination override) はサポート外です。

このプラグインは現在ブランチ (\`${BRANCH}\`) の差分でレビューマーカーを検証するため、 別 ref (例: default branch / 別ブランチ) を destination に指定する形は、 現在ブランチのマーカーで未レビューな別 ref に commit を投影してしまう経路になります (例: \`git push origin HEAD:main\` で reviewed feature を default branch に直接送り込む形)。

destination を現在ブランチと同名に揃えるか、 destination を省略してください:
  - \`git push\` / \`git push origin\` / \`git push origin HEAD\` / \`git push origin ${BRANCH}\`
EOF
)
    deny "$REASON"
    exit 0
  fi
  # `:dest` (source 空) は remote ブランチの削除なのでローカルレビュー対象外、許容。
  [ -z "$src" ] && continue
  if [ "$src" = "HEAD" ] || [ "$src" = "$BRANCH" ]; then
    HAS_REAL_PUSH=1
    continue
  fi
  # `--delete` / `-d` フラグ付き push は remote ref の削除であり、ローカルから新規 commit
  # を送らない。
  if [ "$HAS_DELETE_FLAG" -eq 1 ]; then
    continue
  fi
  # 個別 tag push: tag を peel して 2 段階の reachability check を行う。
  # 1. HEAD から reachable でなければ別ブランチ / 任意 commit を指す tag = deny
  # 2. HEAD から reachable でも、 origin/<base> から既に reachable (= remote に到達済 = 過去
  #    の review を経ている) でなければ「現在ブランチの未レビュー commit に tag を打って
  #    push」 経路になるため、 通常の real push と同じ扱いにして markers gate を要求する
  #    (HAS_REAL_PUSH=1)。 origin/<base> から既に reachable な tag は remote 上の commit を
  #    指すだけなので skip 安全。
  if TAG_COMMIT=$(git -C "$TARGET_CWD" rev-parse --verify --quiet "refs/tags/$src^{commit}" 2>/dev/null); then
    if ! git -C "$TARGET_CWD" merge-base --is-ancestor "$TAG_COMMIT" HEAD 2>/dev/null; then
      REASON=$(cat <<EOF
プッシュをブロックしました。 tag \`${src}\` が指す commit (\`${TAG_COMMIT}\`) が現在ブランチ (\`${BRANCH}\`) の HEAD から reachable ではありません。

tag を push すると git は tag が指す commit object を remote に転送するため、 別ブランチ / 未レビュー commit を指す tag は markers gate を素通りして未レビュー commit を remote に到達させる経路になります。

対応:
  - tag が指す commit を含むブランチに \`git switch\` で切り替えてから tag を push
  - もしくは現在ブランチの HEAD を含む形で tag を再作成: \`git tag -f ${src} HEAD\` (force tag)
EOF
)
      deny "$REASON"
      exit 0
    fi
    # tag が origin/<base> 側にも到達済なら markers skip 安全 (= 既に review 経由で remote
    # に到達した commit を指している)。 そうでなければ現在ブランチの未レビュー commit を
    # 指す可能性があるため real push 扱いにして markers gate を回す。
    if [ -n "$BASE" ] && git -C "$TARGET_CWD" merge-base --is-ancestor "$TAG_COMMIT" "origin/$BASE" 2>/dev/null; then
      continue
    fi
    HAS_REAL_PUSH=1
    continue
  fi
  REASON=$(cat <<EOF
プッシュをブロックしました。push 引数の refspec \`${t}\` が現在ブランチ (\`${BRANCH}\`) と一致していません。

本プラグインは現在ブランチの差分でレビューマーカーを検証するため、別ブランチを refspec で明示する形 (例: \`git push origin other-branch\`) は、現在ブランチのマーカーで未レビューな別ブランチ commit を通してしまう経路になります。

push したいブランチに \`git switch ${src}\` で切り替えてから \`git push\` してください (引数省略形 \`git push\` / \`git push origin\` / \`git push origin HEAD\` も同等で、現在ブランチを push します)。

許容される例外:
  - \`git push --delete origin <branch>\`: remote branch 削除 (新規 commit を送らない)
  - \`git push origin <tag-name>\`: 個別 tag push (cooperative 前提で許容)
EOF
)
  deny "$REASON"
  exit 0
done

# deletion / tag-only push (HEAD と一致する refspec が無く、 削除 / reachable tag のみ) は
# markers gate を skip。 新規 commit は送らないため。
if [ "$REFSPEC_COUNT" -gt 0 ] && [ "$HAS_REAL_PUSH" -eq 0 ]; then
  exit 0
fi

# `push.default=matching` 環境では bare な `git push` (refspec 省略) が複数のローカル
# ブランチを一括 push する。 現在ブランチ以外の commit が gate を素通りする経路になるため
# deny する。
PUSH_DEFAULT=$(git -C "$TARGET_CWD" config --get push.default 2>/dev/null || true)
if [ "$PUSH_DEFAULT" = "matching" ] && [ "$REFSPEC_COUNT" -eq 0 ]; then
  REASON=$(cat <<EOF
プッシュをブロックしました。git config \`push.default=matching\` 環境で refspec 省略形 (\`git push\` / \`git push origin\`) を実行しています。

\`matching\` モードでは local の全マッチブランチが remote に送られ、現在ブランチ以外の未レビュー commit がレビュー gate を素通りします。

対応 (どちらか):
  - 現在ブランチを明示: \`git push origin HEAD\` または \`git push origin ${BRANCH}\`
  - もしくは config 変更: \`git config push.default simple\` (現代の git デフォルト、現在ブランチのみ push)
EOF
)
  deny "$REASON"
  exit 0
fi

# dirty-tree gate: target の working tree が dirty なら deny。 push される committed 部分と
# レビューされた working tree の乖離を防ぐ。
if ! git -C "$TARGET_CWD" diff --quiet 2>/dev/null || ! git -C "$TARGET_CWD" diff --quiet --cached 2>/dev/null; then
  REASON=$(cat <<EOF
プッシュをブロックしました。target (\`${TARGET_CWD}\`) の working tree に未コミット変更が存在します (staged または unstaged)。

本プラグインは「push される committed 部分」が確実にレビュー済みであることを保証するため、push 前に working tree が clean であることを要求します。

\`git -C ${TARGET_CWD} status\` で変更を確認し、commit してから \`/simplify\` → \`/codex:review --wait --scope branch\` を再走させて push してください。
EOF
)
  deny "$REASON"
  exit 0
fi

SIMPLIFIED_MARKER=$(simplified_marker_path "$GIT_DIR")
CODEX_MARKER=$(codex_marker_path "$GIT_DIR")
SIMPLIFIED_HASH=$([ -f "$SIMPLIFIED_MARKER" ] && cat "$SIMPLIFIED_MARKER" 2>/dev/null)
CODEX_HASH=$([ -f "$CODEX_MARKER" ] && cat "$CODEX_MARKER" 2>/dev/null)

# 真の commit push (real push) に到達した時点で BASE が解決できないと branch 全差分が
# 計算できないため fail-closed deny。 ここに到達するのは deletion / tag-only / matching skip
# のいずれにも該当しなかった real push。 silent exit 0 すると markers gate が黙って無効化
# される silent install 失敗の経路になるため、 setup を促す明示 deny に倒す。
if [ -z "$BASE" ]; then
  REASON=$(cat <<EOF
プッシュをブロックしました。 target (\`${TARGET_CWD}\`) の default branch が解決できません。

本プラグインは branch 全差分のレビュー検証に default branch (origin/HEAD or origin/master / origin/main) を必要とします。 以下のいずれかを設定してください:

  - \`git -C ${TARGET_CWD} remote set-head origin --auto\` で origin/HEAD を自動設定
  - \`git -C ${TARGET_CWD} remote set-head origin <branch-name>\` で明示設定 (例: develop)
  - origin remote が無い場合は \`git -C ${TARGET_CWD} remote add origin <url>\` で追加

設定後に再度 \`git push\` を試してください。
EOF
)
  deny "$REASON"
  exit 0
fi

# branch diff hash 計算。 失敗時 (orphan branch / shallow clone 等) は明示 deny。
if ! CURRENT_HASH=$(compute_review_hash_in "$TARGET_CWD" "$BASE"); then
  REASON=$(cat <<EOF
プッシュをブロックしました。ブランチ全差分の計算 (\`git -C ${TARGET_CWD} diff origin/${BASE}...HEAD\`) が失敗しました。

考えられる原因:
  - 孤児ブランチ (origin/${BASE} と共通祖先を持たない unrelated history)
  - shallow clone で merge-base が欠落している
  - origin/${BASE} ref が破損している

対応:
  - 通常の branch (master/main から派生) で作業しているか確認する
  - shallow clone の場合は \`git -C ${TARGET_CWD} fetch --unshallow\` で履歴を完全に取得する
  - origin/${BASE} を更新する: \`git -C ${TARGET_CWD} fetch origin ${BASE}\`
EOF
)
  deny "$REASON"
  exit 0
fi

# branch 全差分 + 未コミット差分が空なら push しても remote に新規変更は載らない (空 push)。
# 通す。
if [ "$CURRENT_HASH" = "$EMPTY_DIFF_HASH" ]; then
  exit 0
fi

LOOP_THRESHOLD=3
LOOP_COUNT=$(read_loop_count "$GIT_DIR")

# 双方のマーカーが現在の差分と一致 = 「現状の branch 全差分 + 未コミットに対して
# /simplify と /codex:review --wait --scope branch が直近で実走済み」を意味する。
# markers は明示削除しない: PreToolUse は push 成功確認できないため、 remote rejection /
# 認証失敗 / ネットワーク失敗時に同じ state での再 push がレビュー必須になる無駄ループを
# 避ける。 markers は次の編集で hash が変わったときに自然に失効する。
if [ -n "$SIMPLIFIED_HASH" ] && [ -n "$CODEX_HASH" ] \
   && [ "$SIMPLIFIED_HASH" = "$CURRENT_HASH" ] \
   && [ "$CODEX_HASH" = "$CURRENT_HASH" ]; then
  reset_loop_count "$GIT_DIR"
  exit 0
fi

format_status() {
  local stored="$1"
  if [ -z "$stored" ]; then
    printf '未実行'
  elif [ "$stored" = "$CURRENT_HASH" ]; then
    printf '✓ 最新の差分でレビュー済み'
  else
    printf '⚠ 失効 (差分が変わったため再実行が必要)'
  fi
}
SIMPLIFIED_STATUS=$(format_status "$SIMPLIFIED_HASH")
CODEX_STATUS=$(format_status "$CODEX_HASH")

ADVERSARIAL_NOTE=""
if [ "$LOOP_COUNT" -ge "$LOOP_THRESHOLD" ]; then
  ADVERSARIAL_NOTE=$(cat <<EOF

⚠ レビューループが ${LOOP_COUNT} 回に達しています (閾値 ${LOOP_THRESHOLD} 回)。
\`/codex:review\` の指摘修正だけで収束しない場合、根本的な実装方針・アーキテクチャ設計に
ミスマッチがある可能性があります。次のいずれかの対応を検討してください:

  - **\`/codex:adversarial-review --wait --scope branch\`** を Skill tool で呼び出し、
    現在のブランチに対する **批判的レビュー** (採用しているアプローチ自体が妥当か、
    設計選択のトレードオフ、暗黙の前提が壊れていないか) を取得する。
  - 大きな方針転換が必要そうなら、ユーザーに状況をエスカレートして判断を仰ぐ。

\`/codex:adversarial-review\` は本ループのマーカー対象外です。 実行後は通常通り
\`/simplify\` → \`/codex:review --wait --scope branch\` を走らせて push へ進んでください。
EOF
)
fi

REASON=$(cat <<EOF
プッシュをブロックしました。push 前に下記のレビューを実行してください。

target: ${TARGET_CWD}
ブランチ: ${BRANCH} (基準: origin/${BASE})

レビュー状態 (双方が「✓ 最新の差分でレビュー済み」になると push が許可されます):
  /simplify                        : $SIMPLIFIED_STATUS
  /codex:review --scope branch     : $CODEX_STATUS
  ループ回数                       : ${LOOP_COUNT} 回 (閾値 ${LOOP_THRESHOLD} 回でアーキテクチャレビュー誘導)

実行手順 (修正が落ち着くまでループ):
  1. /simplify を Skill tool で呼び出す (コード変更を伴うため先に実行)
  2. /codex:review --wait --scope branch を Skill tool で呼び出す
     (--scope branch 必須: branch 全差分 = PR diff のレビューを保証するため)
  3. レビュー結果に指摘があれば修正し、必要に応じて新規 commit を作成する
  4. branch 全差分 + 未コミット差分が変わるとマーカーは自動的に失効する。
     その場合は手順 1〜2 を最初から再実行する
  5. 双方のマーカーが「✓ 最新の差分でレビュー済み」になったら \`git push\` を再試行する

マーカーは PostToolUse hook (auto-mark.sh) が \`/simplify\` と
\`/codex:review --wait --scope branch\` の実行完了を検知して自動的に記録します。マーカーは
push 通過時に明示削除されません (次の編集でハッシュが変わると自動的に失効するため)。

\`/codex:review\` の実行方式 (Claude が自律判断し、ユーザーには確認しないこと):
  - **\`--wait\` (フォアグラウンド) のみサポート**
  - **\`--scope branch\` 必須**

⚠ 重要: \`/codex:review\` であって \`/codex:rescue\` ではありません。両者は別コマンドです。

(注: PR 作成後の adversarial レビューは post-pr-review プラグイン経由で
 \`/codex:adversarial-review\` が起動されます。)$ADVERSARIAL_NOTE
EOF
)

deny "$REASON"
