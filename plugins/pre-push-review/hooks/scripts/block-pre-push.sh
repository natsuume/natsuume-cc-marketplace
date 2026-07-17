#!/bin/bash
# block-pre-push.sh
# git push を未レビューの状態でブロックする PreToolUse フック。
#
# policy: fail-closed (PreToolUse / push gate)
#   未レビュー push を通さないため、判定不能・想定外状況は deny に倒す。対照:
#   PostToolUse 側の auto-mark.sh は fail-open。同じ失敗が Pre=deny / Post=skip という
#   非対称は意図的 (#90)。
#
# ## なぜ push 境界か
#
# pre-commit 境界だと:
#   - 1 commit ごとにレビューループが回り、 N-commit PR では合計 N 回ループが走る
#   - レビュー指摘の修正 commit が初期実装と同じ commit に混入し、
#     git log / blame / bisect の意味的解像度が失われる
#   - 中間 commit (WIP / 探索 / checkpoint) を残せない
#
# push 境界だと:
#   - PR 全差分に対して 1 周のループで済む (1-commit PR では同等、多 commit PR で削減)
#   - 中間 commit を自由に重ねられ、 レビュー対応も独立 commit として記録できる
#   - **未レビューな commit を remote に到達させない** ため、 PR 作成手段 (gh CLI / Web UI /
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
#    - 空 push (全 commit が empty commit の鎖) なら markers gate を skip
#    - commit 列 (HEAD / merge-base の OID) + branch 全差分 + 未コミット差分のハッシュを計算
#    - 3 マーカー (markers.sh の `*_MARKER_NAME` 定数で定義: code-reviewed / codex-reviewed /
#      security-reviewed) と一致しなければ deny。 3 マーカーは v2.0.0 から常に全て必須
#      (v1.x の simplified マーカーと CC version 依存の fail-open 緩和は廃止)。
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

# 予期せぬエラー時の診断 trap を install (実装は lib/exit-trap.sh)。
# 本 hook は fail-closed 設計のため、 markers 不一致 / 解析不能な push / dirty tree
# などは明示的に `deny "<reason>"` を返して `exit 0` で抜ける。 想定外の非ゼロ終了が
# 発生した場合のみ stderr に診断ログを出してユーザに知らせる (push 動作はノンブロッキング)。
_PRE_PUSH_REVIEW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/exit-trap.sh
source "$_PRE_PUSH_REVIEW_SCRIPT_DIR/lib/exit-trap.sh"
install_exit_trap "block-pre-push" "本 hook は fail-closed 設計のため、 通常は markers 不一致を deny JSON で返して exit 0 で抜けますが、 今回は途中で異常終了しています。 push gate が機能していない可能性があるため、"

INPUT=$(cat)

# 大半の Bash 呼び出しは git push と無関係。jq を起動する前に粗フィルタで抜ける。
case "$INPUT" in
  *git*push*|*push*git*) ;;
  *) exit 0 ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# Codex PreToolUse payload の非空 string turn_id だけを runtime discriminator にする。
# Claude Code も CLAUDE_PLUGIN_ROOT / CLAUDE_PLUGIN_DATA を持つため env 推測はしない。
MARKER_RUNTIME="claude"
if printf '%s' "$INPUT" | jq -e '
  (.turn_id | type == "string" and length > 0)
' >/dev/null 2>&1; then
  MARKER_RUNTIME="codex"
fi

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
if [ -z "$COMMAND" ]; then
  exit 0
fi

# bash `$(...)` の trailing-LF trim で消えた `\<LF>` を復元 (詳細は cmd-parser.sh の
# 「末尾 `\<LF>` 復元の caller 側 inline パターン」 セクション)。
case "$COMMAND" in *\\) COMMAND="${COMMAND}"$'\n' ;; esac

# 4 lib をまとめて source: cmd-parser.sh は直下の `normalize_line_continuations_to_space`
# で即使うため必須。 target-resolver / diff-hash / markers は本処理 (segment 解析以降) で
# 使うが、 SCRIPT_DIR を 1 度の計算で済ますためまとめて上に置く。 `${COMMAND//$'\\\n'/ }`
# を直接書かない理由は cmd-parser.sh の `_normalize_line_continuations_impl` を参照。
SCRIPT_DIR="$_PRE_PUSH_REVIEW_SCRIPT_DIR"
# shellcheck source=lib/cmd-parser.sh
source "$SCRIPT_DIR/lib/cmd-parser.sh"
# shellcheck source=lib/target-resolver.sh
source "$SCRIPT_DIR/lib/target-resolver.sh"
# shellcheck source=lib/diff-hash.sh
source "$SCRIPT_DIR/lib/diff-hash.sh"
# shellcheck source=lib/markers.sh
source "$SCRIPT_DIR/lib/markers.sh"

# fast-path: line continuation を含まない 99% の入力では `$(...)` subshell fork を回避。
# 関数本体 (`_normalize_line_continuations_impl`) にも fast-path がある (= 二重) が、
# caller 側の case で関数呼び出し自体を回避することで bash 3.2 で +637 us の fork コスト
# を消す (= hot-path 性能改善)。
case "$COMMAND" in
  *\\$'\n'*) COMMAND=$(normalize_line_continuations_to_space "$COMMAND") ;;
esac

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

# segment に分割し、 push を含む segment 数を数える。 quote 内の `git push` 文字列参照
# (`grep "git push" README` 等) は tokenize で skip されるため、 ここでは「token level
# で `git push` 呼び出しを含む segment」のみカウントする。
#
# 1 push command per Bash invocation を前提にする (= 同一コマンド内に複数 push があると
# 1 マーカー = 1 push 保証が崩れるため deny)。
#
# 加えて、 単独の `&` (background) や `|` (pipeline) は **並列実行** となり、 markers gate
# 検証完了後に index / working tree / refs が並行変更される経路になる (例:
# `git commit X & git push` で push 開始後に新規 unreviewed commit が作られて push に
# 巻き込まれる)。 これらは push の前後を **問わず** deny する (下記 SEPARATORS 判定ループ
# 参照)。 例えば `git push 2>&1 | tee log` も deny されるので、 logging は file
# redirection (`git push > log.txt 2>&1`) か push 完了後の別 Bash 呼び出しで行う。
# (downstream を `tee` 等の非 mutating に絞る allowlist は parser の複雑度に見合わないため
#  採らない。)
# `&&` / `||` / `;` は逐次実行なので位置に関わらず許容。
SEGMENTS=()
SEPARATORS=()
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
  skip_env_assignments _first_toks _fi
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
# caller の変数名は cmd-parser.sh の `skip_env_assignments` ローカル変数
# (`_toks_name` / `_idx_name` / `_n` / `_idx` / `_t` / `_t_raw`) と衝突しない prefix を
# 使う必要がある (bash 3.2 で macOS デフォルト)。 caller で `_idx` / `_n` を使うと
# `local _idx` / `local _n` 宣言で shadow され、 eval 経由の間接展開が空文字を読み
# `[: : integer expression expected` 警告を吐く (macOS で確認済)。 ここでは `_pidx`
# (push idx) / `_pn` (push n) を使う。
for line in "${SEGMENTS[@]}"; do
  # token level で `git ... push` を確認 (text reference を排除)
  declare -a _toks
  tokenize_segment "$line" _toks
  _pidx=0
  _pn=${#_toks[@]}
  skip_env_assignments _toks _pidx
  # `git` または path-qualified (`/usr/bin/git`, `./git` 等) を期待
  if [ "$_pidx" -lt "$_pn" ]; then
    _first="$(unquote_token "${_toks[$_pidx]}")"
    case "$_first" in
      git|*/git) _is_git=1 ;;
      *) _is_git=0 ;;
    esac
    if [ "$_is_git" -eq 1 ]; then
      _pidx=$((_pidx+1))
      # global option を walk して subcommand を探す
      while [ "$_pidx" -lt "$_pn" ]; do
        _opt="$(unquote_token "${_toks[$_pidx]}")"
        case "$_opt" in
          -C|--git-dir|--work-tree|-c|--config|--config-env)
            _pidx=$((_pidx+2)); continue ;;
          --git-dir=*|--work-tree=*) _pidx=$((_pidx+1)); continue ;;
          -*) _pidx=$((_pidx+1)); continue ;;
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

# `--help` / `-h` / `--dry-run` / `-n` は実 push を起こさず race 経路を作らないため、
# parallel separator deny より前に skip 判定する (`git push --help | less` のような
# pager 経由形式が parallel-sep で誤って deny されないように)。
declare -a PUSH_TOKENS
tokenize_segment "$PUSH_SEGMENT" PUSH_TOKENS
for tok in "${PUSH_TOKENS[@]}"; do
  t="$(unquote_token "$tok")"
  case "$t" in
    -h|--help|--dry-run|-n) exit 0 ;;
  esac
done

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
  # env-var prefix / wrapper / git の global option を skip。 path-qualified git も許容
  # (例: `/usr/bin/git push`, `./git push`)。
  if [ "$SAW_GIT" -eq 0 ]; then
    if [[ "$t" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then continue; fi
    case "$t" in
      git|*/git) SAW_GIT=1; continue ;;
    esac
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
  # 値を次 token に取る push オプションは、 その値 token を remote / refspec と誤認しないよう
  # 引数ごと skip する。 `-o` / `--push-option` (push option), `--receive-pack` / `--exec`
  # (receive-pack path)。 これを欠くと `git push -o ci.skip origin <branch>` の `ci.skip` を
  # remote、 `origin` を refspec と誤読し、 現在ブランチへの正当な push を false-positive で
  # deny してしまう。 連結形 (`--push-option=*` / `--receive-pack=*` / `--exec=*` や短縮連結
  # `-o<val>`) は単一 token なので後段の `-*) continue` が吸収する (値消費は不要)。
  case "$t" in
    -o|--push-option|--receive-pack|--exec) _SKIP_NEXT=1; continue ;;
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
  # 明示 `dst=HEAD` も deny: remote の HEAD は symref で remote default branch (master/main)
  # を指すため、 `HEAD:HEAD` で reviewed feature を remote default に投影できる。 destination
  # 省略形 `git push origin HEAD` は dst が空 (= 本 check の対象外) なので別経路。
  if [ -n "$src" ] && [ -n "$dst" ] && [ "$dst_norm" != "$BRANCH" ]; then
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

\`git -C "${TARGET_CWD}" status\` で変更を確認して commit し、Claude Code の \`/pre-push-review:review\` で 3 review を再走させてから push してください。現行 Codex runtime は \`spawn_agent\` に \`agent_type\` selector を公開しないため、Codex 版は marketplace 配布対象外です。
EOF
)
  deny "$REASON"
  exit 0
fi

if CODE_REVIEWED_MARKER=$(code_reviewed_marker_path "$GIT_DIR" "$MARKER_RUNTIME") &&
  CODEX_MARKER=$(codex_marker_path "$GIT_DIR" "$MARKER_RUNTIME") &&
  SECURITY_MARKER=$(security_marker_path "$GIT_DIR" "$MARKER_RUNTIME"); then
  CODE_REVIEWED_HASH=$([ -f "$CODE_REVIEWED_MARKER" ] && cat "$CODE_REVIEWED_MARKER" 2>/dev/null)
  CODEX_HASH=$([ -f "$CODEX_MARKER" ] && cat "$CODEX_MARKER" 2>/dev/null)
  SECURITY_HASH=$([ -f "$SECURITY_MARKER" ] && cat "$SECURITY_MARKER" 2>/dev/null)
else
  # Codex で PLUGIN_DATA / CLAUDE_PLUGIN_DATA が使えない場合を含め、
  # storage 解決不能時は .git へ fallback せず全 marker を missing として
  # 後段の gate を fail-closed にする。
  CODE_REVIEWED_HASH=""
  CODEX_HASH=""
  SECURITY_HASH=""
fi

# 真の commit push (real push) に到達した時点で BASE が解決できないと branch 全差分が
# 計算できないため fail-closed deny。 ここに到達するのは deletion / tag-only / matching skip
# のいずれにも該当しなかった real push。 silent exit 0 すると markers gate が黙って無効化
# される silent install 失敗の経路になるため、 setup を促す明示 deny に倒す。
if [ -z "$BASE" ]; then
  REASON=$(cat <<EOF
プッシュをブロックしました。 target (\`${TARGET_CWD}\`) の default branch が解決できません。

本プラグインは branch 全差分のレビュー検証に default branch (origin/HEAD or origin/master / origin/main) を必要とします。 以下のいずれかを設定してください:

  - \`git -C "${TARGET_CWD}" remote set-head origin --auto\` で origin/HEAD を自動設定
  - \`git -C "${TARGET_CWD}" remote set-head origin <branch-name>\` で明示設定 (例: develop)
  - origin remote が無い場合は \`git -C "${TARGET_CWD}" remote add origin <url>\` で追加

設定後に再度 \`git push\` を試してください。
EOF
)
  deny "$REASON"
  exit 0
fi

# 空 push (レビュー対象となる変更が remote に載らない push) は markers gate を skip。
# 判定は tree OID / plumbing ベース (条件と正当性は lib/diff-hash.sh ヘッダ参照)。
# 全 commit が empty commit の鎖 (issue claim 手順の --allow-empty push 等) のみ skip
# し、「commit A + A の revert」のような net diff だけが空の鎖はマーカー検証へ進める
# (#126 と同根の穴を塞ぐ)。dirty-tree gate は通過済みだが、呼び出し順への依存を
# 作らないよう is_empty_push_in は index / worktree の clean も自前で再検査する。
if is_empty_push_in "$TARGET_CWD" "$BASE"; then
  exit 0
fi

# branch diff hash 計算。 失敗時 (orphan branch / shallow clone 等) は明示 deny。
if ! CURRENT_HASH=$(compute_review_hash_in "$TARGET_CWD" "$BASE"); then
  REASON=$(cat <<EOF
プッシュをブロックしました。ブランチ全差分のハッシュ計算 (origin/${BASE} と HEAD の merge-base 解決 + \`git -C "${TARGET_CWD}" diff\`) が失敗しました。

考えられる原因:
  - 孤児ブランチ (origin/${BASE} と共通祖先を持たない unrelated history)
  - shallow clone で merge-base が欠落している
  - origin/${BASE} ref が破損している

対応:
  - 通常の branch (master/main から派生) で作業しているか確認する
  - shallow clone の場合は \`git -C "${TARGET_CWD}" fetch --unshallow\` で履歴を完全に取得する
  - origin/${BASE} を更新する: \`git -C "${TARGET_CWD}" fetch origin ${BASE}\`
EOF
)
  deny "$REASON"
  exit 0
fi

# マーカーが現在の差分と一致 = 「現状の commit 列 + branch 全差分 + 未コミットに対して該当レビューが
# 直近で実走済み」を意味する。 markers は明示削除しない: PreToolUse は push 成功確認
# できないため、 remote rejection / 認証失敗 / ネットワーク失敗時に同じ state での再 push が
# レビュー必須になる無駄ループを避ける。 markers は次の編集で hash が変わったときに自然に失効する。
is_fresh() {
  [ -n "$1" ] && [ "$1" = "$CURRENT_HASH" ]
}

# 3 マーカーを全必須化する単純 gate (v2.0.0)。 v1.x の simplify / fail-open 緩和の経緯は
# markers.sh / README 参照。
if is_fresh "$CODE_REVIEWED_HASH" && is_fresh "$CODEX_HASH" && is_fresh "$SECURITY_HASH"; then
  exit 0
fi

format_status() {
  local stored="$1"
  if [ -z "$stored" ]; then
    printf '未実行'
  elif [ "$stored" = "$CURRENT_HASH" ]; then
    printf '✓ 最新の差分でレビュー済み'
  else
    printf '⚠ 失効 (差分または commit 列が変わったため再実行が必要)'
  fi
}
CODE_REVIEWED_STATUS=$(format_status "$CODE_REVIEWED_HASH")
CODEX_STATUS=$(format_status "$CODEX_HASH")
SECURITY_STATUS=$(format_status "$SECURITY_HASH")

REASON=$(cat <<EOF
プッシュをブロックしました。 push 前に 3 レビューを実行してください。

target: ${TARGET_CWD}
ブランチ: ${BRANCH} (基準: origin/${BASE})

レビュー状態 (下記 3 つすべてが「✓ 最新の差分でレビュー済み」 になると push が許可されます):
  correctness review : $CODE_REVIEWED_STATUS
  independent review : $CODEX_STATUS
  security review    : $SECURITY_STATUS

実行 surface に応じて、次の正規フローを使ってください:
  - Claude Code: **\`/pre-push-review:review\`** (3 namespaced custom agent を並列起動)
  - Codex: 現行 runtime の \`spawn_agent\` に \`agent_type\` selector が無く、generic agent の reviewer identity を認証できないため marketplace 配布対象外。Codex から generic agent で代行したり marker helper を直接実行したりしない

一部のマーカーのみ「未実行」 / 「失効」 の場合は、 該当レビューの subagent だけを Agent / Task tool で単独再起動してもかまいません (全 3 subagent の再走も可)。 マーカーと subagent_type の対応:
  - correctness review (code-reviewed)  → subagent_type="pre-push-review:code-reviewer"
  - independent review (codex-reviewed) → subagent_type="pre-push-review:codex-reviewer"
  - security review (security-reviewed) → subagent_type="pre-push-review:security-reviewer"

codex review を \`run-codex-review.sh\` wrapper の直接実行で代行することはできません (block-bg-codex-wrapper.sh の agent_type 検証 gate が \`pre-push-review:codex-reviewer\` subagent 以外からの起動を deny します)。

修正後に branch 差分が変わるとマーカーは自動失効します。同じ正規フローで再走させ、
全マーカーが ✓ になったら \`git push\` を再試行してください。

Codex 用に保存している adapter は、runtime が \`agent_type\` selector を公開し、named reviewer identity を SubagentStop event へ引き継げる将来の実行面だけを想定しています。現行 runtime では marker を生成せずfail-closed に停止してください。
EOF
)

deny "$REASON"
