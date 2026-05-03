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
# 2. quote 内のテキスト参照や shell wrapper をハンドリング
# 3. カレントブランチが default (master/main) なら skip (git-guardrails が独立に gate)
# 4. branch 全差分 + 未コミット差分のハッシュを計算
# 5. 2 マーカー (.claude-pre-push-simplified / .claude-pre-push-codex-reviewed) が
#    現在のハッシュと一致すれば counter のみリセットして allow (markers は次の編集で
#    hash が変わるまで残す: PreToolUse は push 成功を確認できないため、明示削除すると
#    remote rejection / 認証失敗 / ネットワーク失敗時に同じ state での再 push が
#    レビュー必須になる無駄ループが発生する)
# 6. それ以外なら deny し、/simplify → /codex:review --wait --scope branch を促す
#
# ## サポート外 / 限界
#
# - `bash -c "..."` のシェルラッパー経由 push は引き続き deny (パーサが解析不能なため)
# - `time git push ...` / `env git push ...` のような未対応 wrapper 経由は deny
#   (postfix scan の起点が取れず未レビュー push を素通しさせるリスクを保守的に塞ぐ)
# - 別端末から `git push` した場合は Claude Code の hook 範囲外で gate できない
#   (本気で塞ぐなら `.git/hooks/pre-push` real git hook を別レイヤーで併設する)
# - master/main 上での push は本フックで gate せず、git-guardrails の
#   block-default-branch-push.sh に委譲する (重複 deny メッセージを避けるため)

INPUT=$(cat)

# 大半の Bash 呼び出しは git push と無関係。jq を起動する前に粗フィルタで抜ける。
# `git` と `push` の両方を含むことを要求し、`echo "let's push the button"` のような
# text-only の "push" 出現で重い後段パーサに進まないようにする。
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

# `git \<改行>push` のような Bash の行継続は実行時にバックスラッシュ+改行が消えて
# `git push` になる。検出ロジックがこれを見落とさないよう、入力段階で空白に正規化する。
COMMAND="${COMMAND//$'\\\n'/ }"

# 改行は **context-aware** に変換する:
#   - quote 内 (`"..."` または `'...'`) の改行 → スペース
#   - quote 外の改行 → `;` (コマンド区切り)
# 素の改行で連結した `git push origin a\n git push origin b` のような形式を後段の
# `[;&|]` postfix scan で deny に倒せる (1 マーカー = 1 push 保証)。
COMMAND=$(printf '%s' "$COMMAND" | awk '
BEGIN { in_squote = 0; in_dquote = 0 }
{
  if (NR > 1) {
    if (in_dquote || in_squote) printf " "; else printf ";"
  }
  printf "%s", $0
  L = length($0); bs = 0
  for (i = 1; i <= L; i++) {
    c = substr($0, i, 1)
    if (in_squote) {
      if (c == "\047") in_squote = 0
      continue
    }
    if (bs) { bs = 0; continue }
    if (c == "\\") { bs = 1; continue }
    if (c == "\"") { in_dquote = !in_dquote; continue }
    if (c == "\047" && !in_dquote) in_squote = 1
  }
}
END { if (NR > 0) print "" }
')

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

# `git push` サブコマンドを検出する。
# `git` と `push` の間に許すのは「`-` で始まる global option と任意の引数値」のみ。
# これにより `git push-status` 等の存在しない / 別サブコマンドや、`git log --grep push`
# 等の push を引数として持つ別サブコマンドを誤検知しない。
OPT='-[^[:space:];&|]+'
OPT_ARG='([[:space:]]+[^-[:space:];&|][^[:space:];&|]*)?'
# 検出用 (どこかに `git ... push` を含むか): 軽量フィルタ
PUSH_DETECT_REGEX="(^|[^[:alnum:]_-])git[[:space:]]+(${OPT}${OPT_ARG}[[:space:]]+)*push"
# 連結プレフィックス共通形 (CHAIN_PREFIX_REGEX) と、それを土台に定義する各種検出 regex。
#   セグメント境界 `(^|[;&|])` + 空白 + 任意の env-var assignment + 任意の wrapper
#   (`builtin`/`command`/`eval`、`command -p` 等の flag 列も許容)。
CHAIN_PREFIX_REGEX='(^|[;&|])[[:space:]]*([A-Za-z_][A-Za-z0-9_]*=[^[:space:];&|]*[[:space:]<>]+)*(builtin[[:space:]<>]+|command([[:space:]<>]+-[^[:space:];&|]*)*[[:space:]<>]+|eval[[:space:]<>]+)?'
PUSH_INVOCATION_REGEX="${CHAIN_PREFIX_REGEX}git[[:space:]]+(${OPT}${OPT_ARG}[[:space:]]+)*push([[:space:]]|\$)"

if ! printf '%s' "$COMMAND" | grep -qE "${PUSH_DETECT_REGEX}([[:space:]]|$)"; then
  exit 0
fi

# 後段のセグメント分割・トークン化のために single quote と double quote の両方を剥がす。
# **順序重要**: double quote を先に剥がす。逆順だと `git push origin "don't-ship"` のような
# 英語アポストロフィを含む引数で、最初の `'` から最後の `'` までを 1 つの single-quoted
# region と誤認し、間に挟まれた `&& cd /other &&` 等を巻き込んで食ってしまう経路がある
# (cd 越しの target-mismatch を deny ロジックがすり抜ける致命的バイパス)。double quote を
# 先に剥がせば内側の `'` は double quote 削除と同時に消えるため誤誘発しない。
COMMAND_DEQUOTED=$(printf '%s' "$COMMAND" | sed -E -e 's/"[^"]*"//g' -e "s/'[^']*'//g")

# `git push` がクォート内にしか現れない場合は、テキスト参照かラッパー経由のいずれか。
SHELL_WRAPPER_REGEX='^[[:space:]]*(bash|sh|zsh|dash|ksh|eval)([[:space:]]|$)'
IS_SHELL_WRAPPER=0
if printf '%s' "$COMMAND" | grep -qE "$SHELL_WRAPPER_REGEX"; then
  IS_SHELL_WRAPPER=1
fi
if ! printf '%s' "$COMMAND_DEQUOTED" | grep -qE "${PUSH_DETECT_REGEX}([[:space:]]|$)"; then
  if [ "$IS_SHELL_WRAPPER" -eq 0 ]; then
    exit 0
  fi
fi

# `()` (サブシェル) と `{}` (group) はコマンド境界として `;`/`&`/`|` と等価なため、
# スペースに正規化しておくと下流のセグメント分割・トークン化が均一に動く。`[(){}]` を
# class として一括置換すると `}` がパラメータ展開の終端と衝突して動かないため、
# 4 回に分けて置換する (各々フォーク無しの parameter expansion)。
COMMAND_DEQUOTED="${COMMAND_DEQUOTED//\(/ }"
COMMAND_DEQUOTED="${COMMAND_DEQUOTED//\)/ }"
COMMAND_DEQUOTED="${COMMAND_DEQUOTED//\{/ }"
COMMAND_DEQUOTED="${COMMAND_DEQUOTED//\}/ }"
# bash の redirection を本体検査から除外する。
COMMAND_DEQUOTED=$(printf '%s' "$COMMAND_DEQUOTED" \
  | sed -E 's/[0-9]?(>>|<<<|<<|<>|>&|<&|>|<)[[:space:]]*[^[:space:];&|]*/ /g')

# 最初の `git ... push` 呼び出しの後ろ (= 「実 push の引数部分以降」) を抽出する。
# COMMAND_DEQUOTED から POSTFIX を取り、引用符付き引数で parser を bypass されるリスクを
# 後段の quote-detect で別途防ぐ (= `git push origin "other-branch"` のような quoted refspec
# は dequote 後に消えてしまい refspec チェックを素通りするため、原文 COMMAND の POSTFIX に
# 引用符が残っていれば deny する)。
PUSH_POSTFIX=""
if [[ "$COMMAND_DEQUOTED" =~ $PUSH_INVOCATION_REGEX ]]; then
  PUSH_POSTFIX="${COMMAND_DEQUOTED#*"${BASH_REMATCH[0]}"}"
elif [ "$IS_SHELL_WRAPPER" -eq 0 ]; then
  REASON=$(cat <<'EOF'
プッシュをブロックしました。本フックが解析できない形式の `git push` 呼び出しが含まれています (例: `time git push ...`, `env git push ...` のように未対応の wrapper 経由など)。

`git push` を直接実行するか、対応している `xxx && git push ...` 形式 (cd や heredoc 埋め込みは許容) で連結してください。
EOF
)
  deny "$REASON"
  exit 0
fi

# **quoted 引数の保守的 deny**: COMMAND_DEQUOTED は quoted region を空白に置換するため、
# `git push origin "other-branch"` のような quoted refspec は POSTFIX から消えてしまう。
# 結果として refspec チェックが素通りし、現在ブランチのマーカーで未レビューな別ブランチ
# が push される経路ができる (codex review P1 指摘)。同様に quoted な `--all` / `--tags` も
# bulk-push deny を回避する。
# 原文 COMMAND の最初の `git ... push` 呼び出しの POSTFIX に引用符が残っていれば deny する。
# cooperative 利用では引用符なしで `git push origin feat/x` のような形で十分なため、
# 保守的に deny する判断。
RAW_PUSH_POSTFIX=""
if [[ "$COMMAND" =~ $PUSH_INVOCATION_REGEX ]]; then
  RAW_PUSH_POSTFIX="${COMMAND#*"${BASH_REMATCH[0]}"}"
fi
if [[ "$RAW_PUSH_POSTFIX" == *\"* ]] || [[ "$RAW_PUSH_POSTFIX" == *\'* ]]; then
  REASON=$(cat <<'EOF'
プッシュをブロックしました。`git push` の引数に引用符 (`"` または `'`) が含まれています。

本フックの文字列ベースな parser では引用符付き引数を確実に解析できず、`git push origin "other-branch"` のような形で refspec チェックを素通りさせる経路 (= 現在ブランチのマーカーで未レビューな別ブランチが push される) を防ぐため、保守的に deny します。

引用符なしで実行してください (例: `git push origin feat/x` のように、ブランチ名 / remote 名はそのまま渡す)。ブランチ名に shell special char が含まれない通常運用では、引用符は不要です。
EOF
)
  deny "$REASON"
  exit 0
fi

# `git push --help` (もしくは `-h`) ならスキップ。
if [[ "$PUSH_POSTFIX" =~ ^[[:space:]]*(-h|--help)[[:space:]]*$ ]]; then
  exit 0
fi

# シェルラッパー (`bash -c "git push ..."` 等) はクォート内のコマンドを本フックの
# 文字列ベースなパーサで解析できず、postfix scan も成立しない。
if [ "$IS_SHELL_WRAPPER" -eq 1 ]; then
  REASON=$(cat <<'EOF'
プッシュをブロックしました。`bash -c "..."` のようなシェルラッパー経由の `git push` はサポート外です。

`git push` を直接実行してください (前段コマンドが必要な場合は `xxx && git push ...` 形式で連結できます)。
EOF
)
  deny "$REASON"
  exit 0
fi

# 単独の `&` (background) と `|` (pipeline) は連結というより並列実行になるため、
# `git fetch & git push ...` や `cmd | git push ...` の形式はマーカー検証完了後に状態が
# 並行変更される経路になる。chain prefix では `&&` / `||` / `;` のみ許容する。
if [[ "$COMMAND_DEQUOTED" =~ (^|[^&])\&([^&]|$) ]] \
   || [[ "$COMMAND_DEQUOTED" =~ (^|[^\|])\|([^\|]|$) ]]; then
  REASON=$(cat <<'EOF'
プッシュをブロックしました。`&` (background) や `|` (pipeline) で `git push` と他コマンドを連結する形式はサポート外です。

これらの区切りはコマンドを並列実行するため、レビューマーカー検証後に状態が変更される経路になります。

連結が必要なら `&&` (success-and) や `;` (sequential) を使用してください。並列実行や pager 接続が必要な場合は別の Bash 呼び出しに分けてください。
EOF
)
  deny "$REASON"
  exit 0
fi

# `git push origin a && ... && git push origin b` のように、push の後にシェル区切り文字を
# 伴う追加コマンドが続くと、マーカー消費後に未レビューな push が走り得る。
# 1 マーカー = 1 push を保証するため、PUSH_POSTFIX に区切り文字を含むコマンドは deny する。
if printf '%s' "$PUSH_POSTFIX" | grep -qE '[;&|]'; then
  REASON=$(cat <<'EOF'
プッシュをブロックしました。`git push` の後に `;`, `&`, `&&`, `||`, `|` などのシェル区切り文字が続く複合コマンドはサポート外です (1 マーカー = 1 push を保証するため)。

`git push` は単独の Bash コマンドとして実行し、後続コマンドは別の Bash 呼び出しに分けてください。
EOF
)
  deny "$REASON"
  exit 0
fi

# `--dry-run` / `-n` push は remote ref を更新しない (git の仕様)。レビュー gate の目的は
# 未レビュー commit を remote に到達させないことなので、no-op 診断 push は markers の
# 状態に関わらず通す。危険な連結形式 (上の `[;&|]` チェック) を抜けた後にこの skip を
# 行うことで、`git push --dry-run; rm -rf` のような後段不正は引き続き block 済み。
if printf '%s' "$PUSH_POSTFIX" | grep -qE '(^|[[:space:]])(--dry-run|-n)([[:space:]=]|$)'; then
  exit 0
fi

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/diff-hash.sh
source "$SCRIPT_DIR/lib/diff-hash.sh"
# shellcheck source=lib/loop-counter.sh
source "$SCRIPT_DIR/lib/loop-counter.sh"
# shellcheck source=lib/markers.sh
source "$SCRIPT_DIR/lib/markers.sh"

# detached HEAD 等で現在ブランチが取れない場合は skip (cooperative)。
BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null) || exit 0

# default branch (master/main) では gate しない。git-guardrails の
# block-default-branch-push.sh が独立に deny するため、こちらでも deny すると
# メッセージが重複して混乱する。
case "$BRANCH" in
  master|main) exit 0 ;;
esac

# default branch が検出できない場合 (origin が無い等) は skip。
BASE=$(detect_base_branch) || exit 0

# **refspec/ブランチ整合性チェック**: 本フックは「現在ブランチの diff」のハッシュで
# markers を検証するため、`git push origin other-branch` のように別ブランチを明示
# 指定する形は、現在ブランチのマーカーが正規でも未レビューな別ブランチの commit を
# remote に到達させてしまう経路になる。同様に `--all` / `--mirror` / `--tags` は複数
# 参照を一括 push するためマーカー検証の対象外コミットが混入する。
#
# v0.1.0 では「現在ブランチのみ」を gate 対象とし、それ以外の形式は保守的 deny にする。
# 別ブランチを push したい場合は switch してから push し直す運用を要求する。
# `--tags` も `--all` と同じ扱い: tags は他ブランチの commit を指す可能性があるため、
# 現在ブランチのマーカーで通してしまうと未レビュー commit が tag 経由で remote に到達する。
if printf '%s' "$PUSH_POSTFIX" | grep -qE '(^|[[:space:]])(--all|--mirror|--tags)([[:space:]=]|$)'; then
  REASON=$(cat <<'EOF'
プッシュをブロックしました。`--all` / `--mirror` / `--tags` (複数参照の一括 push) は本プラグインのレビュー gate 対象外です。

本プラグインは「現在ブランチの全差分 + 未コミット差分」のハッシュでマーカーを検証します。これらのオプションは現在ブランチ以外の参照 (他ローカルブランチ / tag) も remote に送るため、それらのコミットがレビュー gate を素通りします。

`--tags` を使いたい場合は、tag が指す commit を含むブランチを通常通りレビューして push し、別の Bash 呼び出しで `git push origin <tag-name>` のように個別 tag を push してください。
EOF
)
  deny "$REASON"
  exit 0
fi

# PUSH_POSTFIX のトークンを走査し、refspec が現在ブランチ以外の **ブランチ** を指す場合に deny。
# パース簡略化のため、最初の非オプショントークンは remote 名 (`origin` など)、
# それ以降の非オプショントークンを refspec として扱う。
# 既知の制限: `-o option-arg` のように option が separate arg を取る場合、arg を refspec
# と誤分類して誤検知する可能性があるが、cooperative 利用では稀なので許容する
# (`--option=val` 形式や no-arg option はこの罠にかからない)。
#
# 以下は安全と判断して許容する:
#   - `--delete` / `-d` フラグ付き (= remote ref の削除、新規 commit を送らない)
#   - refspec が tag (`refs/tags/<name>`) を指す形 (= 個別 tag push、README で推奨経路)
HAS_DELETE_FLAG=0
if printf '%s' "$PUSH_POSTFIX" | grep -qE '(^|[[:space:]])(--delete|-d)([[:space:]=]|$)'; then
  HAS_DELETE_FLAG=1
fi
read -ra PUSH_TOKENS <<< "$PUSH_POSTFIX"
SAW_REMOTE=0
REFSPEC_COUNT=0
HAS_REAL_PUSH=0  # 1 if any refspec sends commits (= HEAD or current branch refspec).
                 # 0 にとどまれば全 refspec が deletion / `:dest` 形式 = ローカルから新規
                 # commit を送らない経路なので、後段の dirty-tree / marker check を skip して
                 # 良い (codex review P2 指摘)。
for tok in "${PUSH_TOKENS[@]}"; do
  case "$tok" in
    -*) continue ;;
  esac
  if [ "$SAW_REMOTE" -eq 0 ]; then
    # 最初の非オプションは remote 名と仮定
    SAW_REMOTE=1
    continue
  fi
  REFSPEC_COUNT=$((REFSPEC_COUNT + 1))
  # ここに来るのは refspec (例: `branch`, `+branch`, `branch:dest`,
  # `refs/heads/branch:refs/heads/dest`, `:dest`, `HEAD`, `HEAD:dest`)
  # source 部分 (`:`の左側) を取り出して `+` / `refs/heads/` を剥がし current branch と比較。
  src="${tok#+}"
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
  # を送らない。refspec が現在ブランチと一致しなくても許容する。
  if [ "$HAS_DELETE_FLAG" -eq 1 ]; then
    continue
  fi
  # 個別 tag push (`git push origin <tag-name>`) は README で「tag を push したい場合の
  # 推奨経路」として明記している。tag が指す commit は通常 push 済みのブランチ上にある
  # (= 既にレビュー済) ことを cooperative 前提で信頼し、tag 名と一致する場合は許容する。
  # tag は新規 commit を送らない前提なので HAS_REAL_PUSH を立てない (= 後段の dirty-tree /
  # marker check は skip)。
  if git rev-parse --verify --quiet "refs/tags/$src" >/dev/null 2>&1; then
    continue
  fi
  REASON=$(cat <<EOF
プッシュをブロックしました。push 引数の refspec \`${tok}\` が現在ブランチ (\`${BRANCH}\`) と一致していません。

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

# **deletion / tag-only push の gate skip**: refspec が全て deletion (`--delete` flag、
# `:dest` 形式) や tag 個別 push の場合、ローカル commit を新規送信しないため markers gate を
# skip する (codex review P2 指摘: README は deletion を「許容」と謳うが、現状の実装は dirty-tree
# や markers check に引っかかって誤 deny される)。
# REFSPEC_COUNT > 0 を要求するのは、 bare `git push` (refspec 省略) は現在ブランチを push する
# ため通常通り gate が必要なため (`git push` で REFSPEC_COUNT=0、 HAS_REAL_PUSH=0 だが skip して
# はいけない)。
if [ "$REFSPEC_COUNT" -gt 0 ] && [ "$HAS_REAL_PUSH" -eq 0 ]; then
  exit 0
fi

# **`push.default=matching` 検出**: この config 下では bare な `git push` (refspec 省略) が
# 複数のローカルブランチを一括 push する。現在ブランチ以外の commit が gate を素通りする
# 経路になるため、refspec 省略形を deny して明示形 (`git push origin HEAD`) を要求する。
# 現代の git デフォルト (`simple`, 2014 年以降) では bare push は現在ブランチのみ送るため
# 安全。 user が明示的に `matching` を設定している環境のみ deny する。
PUSH_DEFAULT=$(git config --get push.default 2>/dev/null || true)
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

# **dirty-tree gate**: working tree が dirty (staged または unstaged 変更が存在する)
# 状態で /simplify / /codex:review が走るとマーカーは「committed + 未コミット」のハッシュで
# 書かれる。その後 push すると markers は dirty hash と一致するが、push が remote に送るのは
# committed 部分のみ。 codex --scope branch は committed 部分のみを review するため、
# 「reviewer が見た state (= committed + dirty)」と「push される state (= committed)」が
# 乖離する経路ができてしまう (codex review P2 指摘)。
#
# 対策: working tree が dirty なら markers の状態に関わらず deny し、「commit してから
# 再 review」を強制する。dirty 状態が解消されると markers のハッシュ (dirty 込み) は
# 自動的に失効するため、Claude は新しい committed 状態に対して /simplify と /codex:review を
# 再実行する必要がある。
if ! git diff --quiet 2>/dev/null || ! git diff --quiet --cached 2>/dev/null; then
  REASON=$(cat <<'EOF'
プッシュをブロックしました。working tree に未コミット変更が存在します (staged または unstaged)。

本プラグインは「push される committed 部分」が確実にレビュー済みであることを保証するため、push 前に working tree が clean であることを要求します。未コミット変更があるまま push しても committed 部分のみが remote に送られるため、レビュー対象 (working tree 全体) と push 内容 (committed のみ) が乖離します。

`git status` で変更を確認し、`git add` / `git commit` で確定してから `/simplify` → `/codex:review --wait --scope branch` を再走させて push してください。
EOF
)
  deny "$REASON"
  exit 0
fi

SIMPLIFIED_MARKER=$(simplified_marker_path "$GIT_DIR")
CODEX_MARKER=$(codex_marker_path "$GIT_DIR")
SIMPLIFIED_HASH=$([ -f "$SIMPLIFIED_MARKER" ] && cat "$SIMPLIFIED_MARKER" 2>/dev/null)
CODEX_HASH=$([ -f "$CODEX_MARKER" ] && cat "$CODEX_MARKER" 2>/dev/null)

# compute_review_hash は branch diff 計算失敗時 (orphan branch / shallow clone で merge-base
# 欠落 等) に非ゼロを返す。失敗を素通りさせると空文字 → empty-diff fast-path で gate を
# bypass される (codex review P2)。失敗時は明示的に deny する。
if ! CURRENT_HASH=$(compute_review_hash "$BASE"); then
  REASON=$(cat <<EOF
プッシュをブロックしました。ブランチ全差分の計算 (\`git diff origin/${BASE}...HEAD\`) が失敗しました。

考えられる原因:
  - 孤児ブランチ (origin/${BASE} と共通祖先を持たない unrelated history)
  - shallow clone で merge-base が欠落している
  - origin/${BASE} ref が破損している

対応:
  - 通常の branch (master/main から派生) で作業しているか確認する
  - shallow clone の場合は \`git fetch --unshallow\` で履歴を完全に取得する
  - origin/${BASE} を更新する: \`git fetch origin ${BASE}\`
EOF
)
  deny "$REASON"
  exit 0
fi

# branch 全差分 + 未コミット差分が空なら push しても remote に新規変更は載らない
# (空 push / branch がすでに base と一致しているケース)。 マーカーの有無に依らず gate
# 不要で pass する (markers 不在でも空 diff push は許容しないと、初期状態のブランチに
# 対する no-op push が誤って deny される)。
if [ "$CURRENT_HASH" = "$EMPTY_DIFF_HASH" ]; then
  exit 0
fi

# `/codex:review --wait --scope branch` の完了が連続して何回走ったか。
LOOP_THRESHOLD=3
LOOP_COUNT=$(read_loop_count "$GIT_DIR")

# 双方のマーカーが現在の差分と一致 = 「現状の branch 全差分 + 未コミットに対して
# /simplify と /codex:review --wait --scope branch が直近で実走済み」を意味する。
#
# ここで markers を削除しない: 本フックは PreToolUse なので、後続の `git push` 自体が
# remote rejection / 認証失敗 / ネットワーク失敗で落ちる可能性がある。markers を消すと
# 「同じ state なのに再 push でレビュー必須」の無駄ループが発生する。markers は次の
# 編集で hash が変わったときに自然に失効するため、明示削除は不要。 ループカウンタは
# advisory 用途なので push 試行成功時にリセットしておく (失敗時に counter が 0 でも、
# 次回 review 時に +1 されるだけで実害なし)。
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

# loop counter が閾値以上なら「設計レベルの再考」を促す追加文を組み立てる。
ADVERSARIAL_NOTE=""
if [ "$LOOP_COUNT" -ge "$LOOP_THRESHOLD" ]; then
  ADVERSARIAL_NOTE=$(cat <<EOF

⚠ レビューループが ${LOOP_COUNT} 回に達しています (閾値 ${LOOP_THRESHOLD} 回)。
\`/codex:review\` の指摘修正だけで収束しない場合、根本的な実装方針・アーキテクチャ設計に
ミスマッチがある可能性があります。次のいずれかの対応を検討してください:

  - **\`/codex:adversarial-review --wait --scope branch\`** を Skill tool で呼び出し、
    現在のブランチに対する **批判的レビュー** (採用しているアプローチ自体が妥当か、
    設計選択のトレードオフ、暗黙の前提が壊れていないか) を取得する。
    実装表層を見直す \`/codex:review\` とは別観点なので、ループの停滞を打開する手がかりに
    なる場合があります。
  - 大きな方針転換が必要そうなら、ユーザーに状況をエスカレートして判断を仰ぐ。

\`/codex:adversarial-review\` は本ループのマーカー対象外です (= 実行してもマーカーは更新
されません)。実行後は通常通り \`/simplify\` → \`/codex:review --wait --scope branch\` を
走らせて push へ進んでください。

\`/codex:adversarial-review\` を Skill tool から呼ぶには姉妹プラグイン
\`codex-review-customize\` の \`/codex-review-customize:setup\` でパッチを適用しておく必要が
あります。未適用の場合は会話入力としての
\`/codex:adversarial-review --wait --scope branch\` を使用してください。
EOF
)
fi

REASON=$(cat <<EOF
プッシュをブロックしました。push 前に下記のレビューを実行してください。

ブランチ: ${BRANCH} (基準: origin/${BASE})

レビュー状態 (双方が「✓ 最新の差分でレビュー済み」になると push が許可されます):
  /simplify                        : $SIMPLIFIED_STATUS
  /codex:review --scope branch     : $CODEX_STATUS
  ループ回数                       : ${LOOP_COUNT} 回 (閾値 ${LOOP_THRESHOLD} 回でアーキテクチャレビュー誘導)

実行手順 (修正が落ち着くまでループ):
  1. /simplify を Skill tool で呼び出す (コード変更を伴うため先に実行)
  2. /codex:review --wait --scope branch を Skill tool で呼び出す
     (--scope branch 必須: branch 全差分 = PR diff のレビューを保証するため。
      --scope working-tree や --scope auto ではマーカーは更新されません)
  3. レビュー結果に指摘があれば修正し、必要に応じて新規 commit を作成する
     (commit を分けて記録できるのが本プラグインの主目的の一つ)
  4. branch 全差分 + 未コミット差分が変わるとマーカーは自動的に失効する。
     その場合は手順 1〜2 を最初から再実行する
     (/codex:review の指摘修正で /simplify 対象が新たに発生する可能性も、
      /simplify の修正で /codex:review の新規指摘が出る可能性もあるため、
      両方を再走させる)
  5. 双方のマーカーが「✓ 最新の差分でレビュー済み」になったら \`git push\` を再試行する

マーカーは PostToolUse hook (auto-mark.sh) が \`/simplify\` と
\`/codex:review --wait --scope branch\` の実行完了を検知して自動的に記録します。手動で
スクリプトを呼び出す必要はありません。マーカーは push 通過時に明示削除されません
(次の編集でハッシュが変わると自動的に失効するため)。新しい変更を加えるとマーカーは
失効し、再度レビューが必要になります。

ループ回数の上限は設けません (実装表層レビューの強制ブロックはマーカーのハッシュ比較
のみで行います)。Claude が自身の判断で「修正不要」または「人間判断を仰ぐべき」と
判断したタイミングで進行/エスカレートしてください。

\`/codex:review\` の実行方式 (Claude が自律判断し、ユーザーには確認しないこと):
  - **\`--wait\` (フォアグラウンド) のみサポート**。本コンテキストでは
    \`--background\` は使用しないこと。auto-mark hook は Bash tool 完了時に
    発火するため、background 起動だとレビュー完了前に PostToolUse が走り、
    マーカーが更新されない (= ループが永遠に閉じない)
  - **\`--scope branch\` 必須**。working-tree や auto では markers が更新されません。
  - 単体の \`/codex:review\` は通常 AskUserQuestion で実行方式を尋ねるが、
    pre-push-review の文脈では明示的に \`--wait --scope branch\` を渡して質問をスキップする

⚠ 重要: \`/codex:review\` であって \`/codex:rescue\` ではありません。両者は別コマンドで、
  - \`/codex:review\`: read-only のコードレビュー (本プラグインが要求する用途)
  - \`/codex:rescue\`: 修正・調査を delegate する subagent (本プラグインの用途には不適)

Claude は名前の似た \`/codex:rescue\` を誤って選ぶ傾向が報告されています。
コマンド名を必ず確認してください。\`/codex:review\` は frontmatter で
\`disable-model-invocation: true\` が指定されている場合 Skill tool から
呼び出せません。その場合は姉妹プラグイン
\`codex-review-customize\` の \`/codex-review-customize:setup\` を実行して
パッチを適用してください。

(注: PR 作成後の adversarial レビューは post-pr-review プラグイン経由で
 \`/codex:adversarial-review\` が起動されます。)$ADVERSARIAL_NOTE
EOF
)

deny "$REASON"
