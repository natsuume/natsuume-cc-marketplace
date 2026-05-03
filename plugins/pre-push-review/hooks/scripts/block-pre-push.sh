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

# PreToolUse の deny payload を出力する共通ヘルパ。
deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
}

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
# 加えて、 単独の `&` (background) と `|` (pipeline) は **並列実行** であり、 markers gate
# 検証完了後に index / working tree が並行変更される経路になる (例:
# `git commit X & git push` で push 確認後に新規 unreviewed commit が作られて push に
# 巻き込まれる、 もしくは `cmd | git push` で stdin 経由で push に状態が流れ込む)。
# `&&` / `||` / `;` のみ許容し、 単独の `&` / `|` を分離区切りに含むコマンドは deny する。
SEGMENTS=()
HAS_PARALLEL_SEPARATOR=0
while IFS= read -r line; do
  if [[ "$line" == SEP:* ]]; then
    case "$line" in
      "SEP:&"|"SEP:|") HAS_PARALLEL_SEPARATOR=1 ;;
    esac
    continue
  fi
  SEGMENTS+=("$line")
done < <(split_command "$COMMAND")

# 単独 `&` / `|` を含み、かつ どこかの segment が push を含む可能性がある場合のみ deny。
# (parallel separator 自体は無関係なコマンドでも合法なので、 push の文脈に限定する。)
if [ "$HAS_PARALLEL_SEPARATOR" -eq 1 ]; then
  for seg in "${SEGMENTS[@]}"; do
    case "$seg" in
      *push*)
        REASON=$(cat <<'EOF'
プッシュをブロックしました。 単独の `&` (background) や `|` (pipeline) で `git push` を含むコマンドを連結する形式はサポート外です。

これらの区切りはコマンドを並列実行するため、 レビューマーカー検証完了後に index / working tree が並行変更され、 未レビュー commit が push に巻き込まれる経路になります (例: `git commit X & git push` / `cmd | git push`)。

連結が必要なら `&&` (success-and) / `||` (success-or) / `;` (sequential) を使用してください。 並列実行や pager 接続が必要な場合は別の Bash 呼び出しに分けてください。
EOF
)
        deny "$REASON"
        exit 0
        ;;
    esac
  done
fi

# 事前 shape チェック: subshell `(...)` / brace group `{...}` / shell wrapper (`bash -c` 等)
# は本 parser が安全に解析できない形式。 これら shape 内に `push` substring を含む segment
# を見つけたら、 push を hidden に持つ可能性があるため保守的 deny する。
# (実 push を持たない subshell / brace / wrapper は許容する。)
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
PUSH_SEGMENT_INDEX=-1
i=0
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
            if [ "$PUSH_SEGMENT_INDEX" -lt 0 ]; then
              PUSH_SEGMENT_INDEX=$i
              PUSH_SEGMENT="$line"
            fi
            break ;;
          *) break ;;
        esac
      done
    fi
  fi
  unset _toks
  i=$((i+1))
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

# default branch を target cwd で解決
BASE=$(git -C "$TARGET_CWD" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|^refs/remotes/origin/||')
if [ -z "$BASE" ]; then
  for b in master main; do
    if git -C "$TARGET_CWD" rev-parse --verify "origin/$b" >/dev/null 2>&1; then
      BASE="$b"; break
    fi
  done
fi
[ -z "$BASE" ] && exit 0

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
              # 引数を次イテレーションで skip するためのフラグ。 ループ先頭で消費するので
              # SAW_PUSH 遷移を跨いでも誤起動しない (旧版は SAW_PUSH 遷移後に消費判定が
              # 走る設計で、 separate-arg option の引数が wildcard `*) continue` で
              # silently 食われた後に _SKIP_NEXT が leak し、 `git -C /t push origin master`
              # の `origin` を skip 対象に誤認、 `master` を remote 名扱いして refspec
              # 検証を素通りさせていた)。
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
  # push のオプションを skip (`-u`, `--force`, `--delete` 等)。
  case "$t" in
    -*) continue ;;
  esac
  # 最初の非オプションは remote 名と仮定
  if [ "$SAW_REMOTE" -eq 0 ]; then
    SAW_REMOTE=1
    continue
  fi
  REFSPEC_COUNT=$((REFSPEC_COUNT+1))
  # source 部分 (`:`の左側) を取り出して `+` / `refs/heads/` を剥がし current branch と比較。
  src="${t#+}"
  src="${src#refs/heads/}"
  case "$src" in
    *:*) src="${src%%:*}" ;;
  esac
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
  # 個別 tag push は cooperative 前提で許容 (HAS_REAL_PUSH を立てない = gate skip 対象)。
  if git -C "$TARGET_CWD" rev-parse --verify --quiet "refs/tags/$src" >/dev/null 2>&1; then
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

# deletion / tag-only push は markers gate を skip (codex review P2 指摘に対応)。
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
