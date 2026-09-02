#!/bin/bash
# block-pre-merge.sh
# codex review 済みでない PR の `gh pr merge` をブロックし、 レビュー済みならローカルの
# レビュー記録を PR へ投稿してから merge を通す PreToolUse フック。
#
# policy: fail-closed (関与したコマンドに限る)
#   関与条件 (下記 1.) を満たさない Bash 呼び出しには一切関与しない (無出力で exit 0)。
#   関与したコマンドについては、 判定不能・想定外状況 (gh / jq 不在、 PR 解決失敗、
#   取得・照合失敗、 ローカル記録の解決・検証失敗、 投稿失敗) をすべて deny に倒す。
#
# ## 判定の流れ
#
# 1. **関与条件**: Bash コマンド文字列が `gh pr merge` の連続列を含む場合のみ関与する。
#    検出は粗い文字列判定であり、 フラグ文法の解析や invocation の厳密な分類は行わない。
#    誤爆した場合はコマンドの言い換えで回避できる (cooperative 利用前提)。
# 2. **`--auto` / `--admin`**: 関与したコマンドがこれらの文字列を含む場合は、 PR 上の
#    レビューコメントの有無に依らず deny する。 `--auto` は gate 確認と実 merge を
#    分離する遅延予約、 `--admin` は保護 bypass であり、 いずれも本 gate の観測範囲外の
#    merge を作るためサポート外とする。
# 3. **レビューコメント照合**: merge 対象 PR の番号・現在の head SHA・PR レビュー一覧を gh で
#    取得し、 レビュー本文の **先頭行** が機械可読 header
#    `<!-- codex-review: head=<full head SHA> status=pass|findings -->` で、 その head SHA が
#    現在の head と完全一致するものがあるかを確認する。 在れば無出力で終了する (既定の許可
#    フローに委ねる。 同じ PR・head のローカル記録が残っていれば best-effort で掃除する)。
#    status は pass / findings のどちらでも「レビュー済み」として成立する (merge の approve や
#    findings 0 件の証明ではない)。
# 4. **ローカル記録の検証と投稿**: 一致コメントが無い場合は、 merge 実行 repo (payload の
#    `cwd`) の git-dir 直下にある codex review の記録を検証する。 final attestation
#    (2 行 `pr=<全数字>` / `head=<40 hex 小文字>`) が symlink でない regular file として在り、
#    番号と head SHA が gh の値に一致し、 投稿用の本文ファイルの先頭行が同じ head SHA の
#    header であるときだけ、 `gh pr review <番号> --comment --body-file <本文>` で投稿する。
#    投稿に成功したら記録を掃除して無出力で終了し、 投稿に失敗したら記録を残したまま deny する
#    (再実行で投稿を再試行できる)。 記録が無い / git-dir を解決できない / 記録が不正 /
#    番号や head が不一致 / 本文を欠く場合はいずれも deny し、
#    `pre-merge-codex-review:codex-reviewer` subagent の実行を案内する。
#    レビュー記録の書き手は subagent が起動する wrapper と subagent lifecycle hook
#    (auto-mark.sh) であり、 本 gate は検証と投稿だけを行う。
#
# ## 受理正規形 (allow 判定の唯一の経路)
#
# 関与したコマンドのうち、 照合フロー (gh での SHA 照合) へ進めるのは **受理正規形に完全
# 一致** するものだけである。 それ以外の関与形はすべて deny する。 正規形は正規化後の
# コマンド文字列全体が次の構成に一致することを指す:
#
#   gh pr merge [<全数字 1 語>] [<`-` 始まりのフラグ>]...
#
# 数字は `gh pr merge` の直後の 1 語に限る (フラグより後ろの数字はフラグの値と区別できない)。
# リダイレクト・シェル演算子 (`&&` / `||` / `;` / `|` / `&`)・quote・`$` 展開・その他の
# トークンを含む形は、 本 script の解釈と shell の実挙動が乖離しうるため一律 deny する。
# 「除去して近似する」 のではなく 「解釈できる形だけを受理する」 ことで、 乖離による
# false-allow を構造的に消す設計であり、 正規形の外側を許可する拡張は行わない (利用者は
# 単独コマンドへの言い換えで対応する)。
#
# 加えて、 照合 repo を一本化するため次も deny する:
#   - `--repo` / `-R` の文字列を含む → 別 repo の PR を merge しうる
#   - `--auto` / `--admin` の文字列を含む → 遅延 merge 予約・保護 bypass
#
# 対象 PR の解決と head SHA の取得自体は gh に委ねる。 gh は **merge が実行される repo**
# (hook payload の `cwd`) で実行する。 `cwd` の欠落・空・非絶対パス・不在ディレクトリ・
# cd 不能はいずれも 「どの repo を照合すべきか決められない」 ため deny する。
#
# ## permissionDecision
#
# 本 gate が出す permissionDecision は deny のみである。 allow / updatedInput は出さず、
# 通過時は無出力で既定の許可フローを維持する。

INPUT=$(cat)

# 大半の Bash 呼び出しは merge と無関係。 jq を起動する前に粗フィルタで抜ける。 ここでの
# 判定は raw payload に対する緩い superset (`gh` → `pr` → `merge` の出現順) で、 精密な
# 連続列判定は command 抽出後に行う。 jq 不在時はこのフィルタが唯一の関与条件になるため、
# 取りこぼしのない側 (superset) に倒す。
#
# 行継続 (`\` + 改行) は payload 上では JSON エスケープされて `\\` + `\n` (= backslash 3 つ
# + `n`) になる。 bash は行継続を削除して隣接トークンを連結するため、 ここでも同じ 4 文字列を
# 除去してから判定する (除去しないと `gh pr me\<改行>rge` を取りこぼす)。
INPUT_SCAN="${INPUT//\\\\\\n/}"
case "$INPUT_SCAN" in
  *gh*pr*merge*) ;;
  *) exit 0 ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"マージをブロックしました。 jq が見つからないため codex review 済みかを検証できません。 jq をインストールしてから、もう一度 gh pr merge を実行してください。"}}'
  exit 0
fi

deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
}

# jq が payload の解析に失敗した場合 (壊れた JSON 等) は、 抽出結果が空になるのを 「merge と
# 無関係」 と読み替えず deny する。 ここに到達している時点で raw payload は粗フィルタを通って
# おり、 merge コマンドを含む可能性があるため、 判定不能を通過させない (fail-closed)。
if ! COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty'); then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"マージをブロックしました。 hook payload を解析できず、 codex review 済みかを検証できません。 コマンドを言い換えて再実行しても解消しない場合は、 plugin の不具合として報告してください。"}}'
  exit 0
fi
[ -n "$COMMAND" ] || exit 0

# 正規化: 行継続 `\<改行>` は bash 実挙動と同じく **削除** して前後のトークンを連結する
# (`gh pr me\<改行>rge` は `gh pr merge` として実行されるため、 これを消さないと連続列検出を
# 素通りできる)。 残った生の改行はコマンド区切りなので `;` に置換し、 タブは空白として扱って
# 空白列を単一空白へ畳む。
COMMAND="${COMMAND//\\$'\n'/}"
NORMALIZED=$(printf '%s' "$COMMAND" | tr '\n\r\t' ';; ' | tr -s ' ')
NORMALIZED="${NORMALIZED# }"
NORMALIZED="${NORMALIZED% }"

# 関与条件の本判定 (粗い連続列検出)。
case "$NORMALIZED" in
  *"gh pr merge"*) ;;
  *) exit 0 ;;
esac

case "$NORMALIZED" in
  *--auto*|*--admin*)
    deny "マージをブロックしました。 \`--auto\` (遅延 merge 予約) と \`--admin\` (保護 bypass) は本プラグインのサポート外です。

\`--auto\` は gate 確認と実 merge が分離されるため、 確認時点の codex review 済み状態が実 merge 時点でも成立している保証がありません。 \`--admin\` は branch protection を bypass します。

codex review 済みであることを確認したうえで、 \`--auto\` / \`--admin\` を外した \`gh pr merge\` を実行してください。"
    exit 0
    ;;
esac

# repo selector (`--repo` / `-R`) 付きの merge は、 本 gate が照合に使う repo (hook プロセスの
# cwd) と実際に merge される repo が食い違いうる。 別 repo の同番号 PR のレビューコメントで
# 未レビュー merge を通す経路になるため deny する (`--auto` / `--admin` と同じ粗い文字列検出)。
case "$NORMALIZED" in
  *--repo*|*-R*)
    deny "マージをブロックしました。 repo 指定付きの merge (\`--repo\` / \`-R\`) はサポート外です。

本 gate はコマンドを実行する repo の文脈でレビューコメントを照合するため、 repo を切り替える指定が入ると別 repo の PR を照合してしまう可能性があります。

対象 repo のディレクトリに移動し、 repo 指定を外した \`gh pr merge\` を単独のコマンドとして実行してください。"
    exit 0
    ;;
esac

# 先頭・末尾の separator (末尾改行や前後の空行が正規化で `;` になったもの) と空白を落とす。
# コマンドの前後に付くだけの区切りは実行内容に影響しないため、 正規形照合の前に取り除く
# (**内部** の separator は複数コマンドの連結を意味するので、 落とさず deny の材料に残す)。
while :; do
  case "$NORMALIZED" in
    " "*) NORMALIZED="${NORMALIZED# }" ;;
    ";"*) NORMALIZED="${NORMALIZED#;}" ;;
    *) break ;;
  esac
done
while :; do
  case "$NORMALIZED" in
    *" ") NORMALIZED="${NORMALIZED% }" ;;
    *";") NORMALIZED="${NORMALIZED%;}" ;;
    *) break ;;
  esac
done

# 受理正規形との完全一致を要求する。 一致しない関与コマンド (前置コマンドの連結・複数
# merge・リダイレクト・シェル演算子・quote・`$` 展開・フラグより後ろの数字等) は、 本 script
# の解釈と shell の実挙動が乖離しうるため一律 deny する。
#
# フラグとして受理するのは長フラグ (`--name` / `--name=value`) と **単文字** 短フラグ
# (`-d` 等) のみ。 短フラグの束ね形 (`-dR` 等) を受理しないのは、 束の中に repo selector
# (`-R`) を隠すと `--repo` / `-R` の文字列検出をすり抜けて別 repo の PR を merge できて
# しまうため。
if ! printf '%s' "$NORMALIZED" \
  | grep -qE '^gh pr merge( [0-9]+)?(( --[A-Za-z0-9][A-Za-z0-9=/._:@+-]*)|( -[A-Za-z0-9]))*$'; then
  deny "マージをブロックしました。 本 gate が解釈できるのは \`gh pr merge [<number>] [flags]\` の単独正規形だけです (対象 PR の番号を置けるのは \`gh pr merge\` の直後のみ、 それ以降は長フラグ \`--name\` / \`--name=value\` か単文字の短フラグ \`-d\` のみ。 \`-dR\` のような短フラグの束ね形は受理しません)。

リダイレクト (\`> file\` / \`2>&1\` 等)・他コマンドとの連結 (\`&&\` / \`||\` / \`;\` / \`|\` / \`&\`)・quote・変数展開・複数の merge を含む形は、 gate の解釈と実際の shell の挙動が食い違いうるため受理しません。

リダイレクトや連結を外した単独コマンドに言い換えて再実行してください (例: \`gh pr merge 123 --squash\` / \`gh pr merge --squash\`)。"
  exit 0
fi

# 正規形に一致した時点で、 対象指定は 「`gh pr merge` の直後の全数字 1 語」 か 「無し」 の
# どちらかに確定している。 前者ならその番号を gh に渡し、 後者は gh の current branch 解決に
# 委ねる。
TARGET=""
REST="${NORMALIZED#gh pr merge}"
REST="${REST# }"
FIRST_TOKEN="${REST%% *}"
case "$FIRST_TOKEN" in
  ""|-*) ;;
  *) TARGET="$FIRST_TOKEN" ;;
esac

if ! command -v gh >/dev/null 2>&1; then
  deny "マージをブロックしました。 gh が見つからないため、 対象 PR の head SHA と codex review コメントを取得できません。 gh CLI をインストール (および \`gh auth login\`) してから、もう一度 gh pr merge を実行してください。"
  exit 0
fi

# merge が実行される repo は Bash tool の cwd (hook payload の `cwd`) であり、 hook プロセス
# 自身の cwd とは限らない (Bash tool の cwd は tool 呼び出しをまたいで持続するため、 前の
# 呼び出しで別 repo に移動していることがある)。 payload の `cwd` を照合 repo の正本とし、
# 欠落・空・非絶対パス・不在ディレクトリ・cd 不能はいずれも deny する (自プロセスの cwd への
# fallback は持たない。 別 repo の PR を照合して未レビュー merge を通す経路を作らないため)。
PAYLOAD_CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null) || PAYLOAD_CWD=""
CWD_PROBLEM=""
case "$PAYLOAD_CWD" in
  "") CWD_PROBLEM="hook payload に cwd がありません" ;;
  /*)
    if [ ! -d "$PAYLOAD_CWD" ]; then
      CWD_PROBLEM="hook payload の cwd (\`${PAYLOAD_CWD}\`) がディレクトリとして存在しません"
    elif ! (cd "$PAYLOAD_CWD") 2>/dev/null; then
      CWD_PROBLEM="hook payload の cwd (\`${PAYLOAD_CWD}\`) に移動できません (権限を確認してください)"
    fi
    ;;
  *) CWD_PROBLEM="hook payload の cwd (\`${PAYLOAD_CWD}\`) が絶対パスではありません" ;;
esac
if [ -n "$CWD_PROBLEM" ]; then
  deny "マージをブロックしました。 ${CWD_PROBLEM}。

本 gate は merge が実行されるディレクトリの repo でレビューコメントを照合します。 そのディレクトリを特定できないと、 別 repo の PR を照合して未レビューの merge を通してしまう可能性があるため、 判定せず deny します。

merge 対象 repo のディレクトリが存在しアクセスできる状態にしてから、もう一度 gh pr merge を実行してください。"
  exit 0
fi

# gh を merge 対象 repo の位置で実行する。 コマンド置換のサブシェル内で `cd` するため、
# 本プロセスの cwd は変わらない。
run_gh_pr_view() {
  cd "$PAYLOAD_CWD" || return 1
  if [ -n "$1" ]; then
    gh pr view "$1" --json number,headRefOid,reviews 2>/dev/null
  else
    gh pr view --json number,headRefOid,reviews 2>/dev/null
  fi
}

PR_JSON=$(run_gh_pr_view "$TARGET") || PR_JSON=""

if [ -z "$PR_JSON" ]; then
  deny "マージをブロックしました。 merge 対象 PR の情報を gh から取得できませんでした (PR を解決できない / 認証が切れている / ネットワーク不通 等)。

\`gh pr view --json headRefOid,reviews\` が成功する状態にしてから、もう一度 gh pr merge を実行してください。"
  exit 0
fi

HEAD_SHA=$(printf '%s' "$PR_JSON" | jq -r '.headRefOid // empty' 2>/dev/null) || HEAD_SHA=""
case "$HEAD_SHA" in
  ""|*[!0-9a-fA-F]*)
    deny "マージをブロックしました。 merge 対象 PR の head SHA を取得できませんでした (gh の応答に headRefOid が含まれていません)。

gh の認証・バージョンと PR の状態を確認してから、もう一度 gh pr merge を実行してください。"
    exit 0
    ;;
esac

# PR 番号はローカルのレビュー記録との照合と、 記録を投稿する際の対象指定に使う。 正規形の
# `gh pr merge` は番号を省略できる (current branch 解決) ため、 番号は常に gh の応答から取る。
# 一致コメントが在る場合は番号を使わずに通すため、 ここでは取得だけ行い、 番号が要る経路
# (記録の照合・投稿) の直前で検証する。
PR_NUMBER=$(printf '%s' "$PR_JSON" | jq -r '.number // empty' 2>/dev/null) || PR_NUMBER=""

# header は本文の **先頭行** にあるものだけを attestation として扱う。 本文の途中に現れる
# header 形の文字列 (レビュー report がレビュー対象の差分から引用したもの等) を受理すると、
# 別 SHA の attestation として機能してしまうため。
MATCHED=$(printf '%s' "$PR_JSON" | jq -r --arg head "$HEAD_SHA" '
  [
    .reviews[]?
    | (.body // "")
    | (split("\n")[0] // "")
    | rtrimstr("\r")
    | select(
        startswith("<!-- codex-review: head=" + $head + " status=pass -->")
        or startswith("<!-- codex-review: head=" + $head + " status=findings -->")
      )
  ] | length
' 2>/dev/null) || MATCHED=""

case "$MATCHED" in
  ""|*[!0-9]*)
    deny "マージをブロックしました。 PR レビューコメントと head SHA の照合に失敗しました (gh の応答を解析できませんでした)。

\`gh pr view --json headRefOid,reviews\` の出力を確認してから、もう一度 gh pr merge を実行してください。"
    exit 0
    ;;
esac

# レビュー記録のファイル名は lib/markers.sh が単一ソース。 読み込めない場合は記録を検証でき
# ないため deny に倒す (fail-closed)。
_BLOCK_PRE_MERGE_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/markers.sh
if ! source "$_BLOCK_PRE_MERGE_SCRIPT_DIR/lib/markers.sh" 2>/dev/null; then
  deny "マージをブロックしました。 plugin の lib (\`hooks/scripts/lib/markers.sh\`) を読み込めず、 codex review のレビュー記録を検証できません。

plugin を再インストール (\`claude plugin install pre-merge-codex-review@natsuume-plugins\`) してから、もう一度 gh pr merge を実行してください。"
  exit 0
fi

# レビュー記録は merge が実行される repo の git-dir 直下に置かれる。 gh と同じく payload の
# `cwd` を正本として解決する (自プロセスの cwd への fallback は持たない)。
resolve_absolute_git_dir() {
  cd "$PAYLOAD_CWD" || return 1
  git rev-parse --absolute-git-dir 2>/dev/null
}
GIT_DIR=$(resolve_absolute_git_dir) || GIT_DIR=""

FINAL_PATH=""
PENDING_PATH=""
BODY_PATH=""
if [ -n "$GIT_DIR" ]; then
  FINAL_PATH=$(pre_merge_final_marker_path "$GIT_DIR" 2>/dev/null) || FINAL_PATH=""
  PENDING_PATH=$(pre_merge_pending_marker_path "$GIT_DIR" 2>/dev/null) || PENDING_PATH=""
  BODY_PATH=$(pre_merge_comment_body_path "$GIT_DIR" 2>/dev/null) || BODY_PATH=""
fi

# attestation の 1 行目 / 2 行目を読む (行末 CR は除去する)。
read_record_line() {
  sed -n "$1p" "$2" 2>/dev/null | tr -d '\r'
}

# 記録一式 (final attestation / pending attestation / 投稿用本文) を削除する。
local_record_is_resolvable() {
  [ -n "$FINAL_PATH" ] && [ -n "$PENDING_PATH" ] && [ -n "$BODY_PATH" ]
}
discard_local_record() {
  local_record_is_resolvable || return 0
  rm -f "$FINAL_PATH" "$PENDING_PATH" "$BODY_PATH" 2>/dev/null
}

if [ "$MATCHED" -gt 0 ]; then
  # 現 head SHA に一致する codex review コメントが在る = レビュー済み。 decision を出さず
  # 既定の許可フローに委ねる。 同じ PR・head のローカル記録が残っている場合は、 同じ内容を
  # 二重投稿する材料にならないよう best-effort で掃除する (掃除の失敗では deny しない)。
  if local_record_is_resolvable && [ ! -L "$FINAL_PATH" ] && [ -f "$FINAL_PATH" ]; then
    if [ "$(read_record_line 1 "$FINAL_PATH")" = "pr=${PR_NUMBER}" ] \
      && [ "$(read_record_line 2 "$FINAL_PATH")" = "head=${HEAD_SHA}" ]; then
      discard_local_record || true
    fi
  fi
  exit 0
fi

# ここから先は 「現 head SHA に一致する codex review コメントが PR に無い」 場合の処理。
# ローカルのレビュー記録を検証し、 成立していれば PR に投稿してから merge を通す。

# 記録が無い / 解決できない場合に共通で案内する本文。
REVIEW_GUIDANCE="Agent / Task tool で subagent_type=\"pre-merge-codex-review:codex-reviewer\", model=\"sonnet\" を起動してください。 subagent が codex review を実行し、 完了時にレビュー記録を repo の git-dir 直下 (ローカル) に保存します。 その後 \`gh pr merge\` を再実行すると、 この gate が記録を検証して PR に投稿してから merge に進みます。

レビューは **current branch の PR** に対して実行され、 記録も current branch の PR に紐づきます。 別の PR を番号で指定して merge しようとしている場合は、 先にその PR のブランチへ \`git switch\` してから subagent を起動してください (別ブランチのまま起動すると、 記録が current branch の PR のものになり、 この merge は deny のままになります)。

model 未指定の Agent 起動は Fable セッションでは agent-discipline の hook に deny されるため、 上記の model を常に明示してください。

PR に commit を追加すると head SHA が変わり、 過去のレビュー記録と投稿済みコメントは自動的に失効します (再レビューが必要になります)。"

# ここから先は PR 番号を使う (記録の照合・投稿対象の指定)。 取得できていない場合は判定不能な
# ため deny する。
case "$PR_NUMBER" in
  ""|*[!0-9]*)
    deny "マージをブロックしました。 merge 前に codex review を実行してください。

対象 PR の head SHA: ${HEAD_SHA}
この head SHA に一致する codex review コメントが PR に無く、 gh の応答から PR 番号 (number) を取得できなかったため、 ローカルのレビュー記録とも照合できませんでした。

gh の認証・バージョンと PR の状態も併せて確認してください。

${REVIEW_GUIDANCE}"
    exit 0
    ;;
esac

if ! local_record_is_resolvable; then
  deny "マージをブロックしました。 merge 前に codex review を実行してください。

対象 PR: #${PR_NUMBER} (head SHA: ${HEAD_SHA})
この head SHA に一致する codex review コメントが PR に無く、 merge を実行するディレクトリ (\`${PAYLOAD_CWD}\`) から git-dir を解決できないため、 ローカルのレビュー記録も参照できませんでした (git repository の外で merge しようとしている可能性があります)。

${REVIEW_GUIDANCE}"
  exit 0
fi

if [ ! -e "$FINAL_PATH" ]; then
  deny "マージをブロックしました。 merge 前に codex review を実行してください。

対象 PR: #${PR_NUMBER} (head SHA: ${HEAD_SHA})
この head SHA に一致する codex review コメントが PR に無く、 ローカルのレビュー記録 (\`${FINAL_PATH}\`) もありません。

${REVIEW_GUIDANCE}"
  exit 0
fi

if [ -L "$FINAL_PATH" ] || [ ! -f "$FINAL_PATH" ]; then
  deny "マージをブロックしました。 ローカルのレビュー記録 (\`${FINAL_PATH}\`) が通常のファイルではありません (symlink 等)。

記録は plugin の hook が書いた通常のファイルであることを要求します。 当該ファイルを取り除き、 subagent_type=\"pre-merge-codex-review:codex-reviewer\", model=\"sonnet\" で codex review をやり直してから、もう一度 gh pr merge を実行してください。"
  exit 0
fi

RECORD_PR_LINE=$(read_record_line 1 "$FINAL_PATH")
RECORD_HEAD_LINE=$(read_record_line 2 "$FINAL_PATH")
if ! printf '%s' "$RECORD_PR_LINE" | grep -qE '^pr=[0-9]+$' \
  || ! printf '%s' "$RECORD_HEAD_LINE" | grep -qE '^head=[0-9a-f]{40}$'; then
  deny "マージをブロックしました。 ローカルのレビュー記録 (\`${FINAL_PATH}\`) の形式が不正です。

記録は 1 行目が \`pr=<PR 番号>\`、 2 行目が \`head=<40 桁の head SHA>\` の 2 行である必要があります。

当該ファイルを取り除き、 subagent_type=\"pre-merge-codex-review:codex-reviewer\", model=\"sonnet\" で codex review をやり直してから、もう一度 gh pr merge を実行してください。"
  exit 0
fi
RECORD_PR="${RECORD_PR_LINE#pr=}"
RECORD_HEAD="${RECORD_HEAD_LINE#head=}"

if [ "$RECORD_PR" != "$PR_NUMBER" ]; then
  deny "マージをブロックしました。 ローカルのレビュー記録は別の PR (#${RECORD_PR}) のものです (merge 対象は #${PR_NUMBER})。

レビューは current branch の PR に対して実行され、 記録もその PR に紐づきます。 merge 対象 PR のブランチへ \`git switch\` してから subagent_type=\"pre-merge-codex-review:codex-reviewer\", model=\"sonnet\" で codex review を実行し、 そのうえで gh pr merge を実行してください。"
  exit 0
fi

if [ "$RECORD_HEAD" != "$HEAD_SHA" ]; then
  deny "マージをブロックしました。 ローカルのレビュー記録が対象 PR の現在の head と一致しません。

記録の head SHA: ${RECORD_HEAD}
対象 PR の現在の head SHA: ${HEAD_SHA}

PR に commit が追加されるなどして head が変わっているため、 記録は失効しています。 subagent_type=\"pre-merge-codex-review:codex-reviewer\", model=\"sonnet\" で再レビューを実行してから、もう一度 gh pr merge を実行してください。"
  exit 0
fi

if [ -L "$BODY_PATH" ] || [ ! -f "$BODY_PATH" ]; then
  deny "マージをブロックしました。 ローカルのレビュー記録が不完全です (投稿用の本文 \`${BODY_PATH}\` が通常のファイルとして見つかりません)。

subagent_type=\"pre-merge-codex-review:codex-reviewer\", model=\"sonnet\" で codex review をやり直してから、もう一度 gh pr merge を実行してください。"
  exit 0
fi

BODY_FIRST_LINE=$(sed -n '1p' "$BODY_PATH" 2>/dev/null | tr -d '\r')
case "$BODY_FIRST_LINE" in
  "<!-- codex-review: head=${HEAD_SHA} status=pass -->") ;;
  "<!-- codex-review: head=${HEAD_SHA} status=findings -->") ;;
  *)
    deny "マージをブロックしました。 ローカルのレビュー記録が不完全です (投稿用の本文 \`${BODY_PATH}\` の先頭行が対象 head SHA の header になっていません)。

本文の先頭行は \`<!-- codex-review: head=${HEAD_SHA} status=pass|findings -->\` である必要があります。

subagent_type=\"pre-merge-codex-review:codex-reviewer\", model=\"sonnet\" で codex review をやり直してから、もう一度 gh pr merge を実行してください。"
    exit 0
    ;;
esac

# 検証を通過した記録を PR レビューとして投稿する。 gh の確認出力は stderr へ回し、 hook の
# stdout は decision JSON 専用に保つ (通過時は無出力)。
post_review_comment() {
  cd "$PAYLOAD_CWD" || return 1
  gh pr review "$PR_NUMBER" --comment --body-file "$BODY_PATH" >&2
}

if ! post_review_comment; then
  deny "マージをブロックしました。 codex review は完了していますが、 レビュー記録の PR への投稿に失敗しました。

対象 PR: #${PR_NUMBER} (head SHA: ${HEAD_SHA})
レビュー記録はローカルに保持しています。 gh の認証・権限・ネットワークを確認してから gh pr merge を再実行すると、 投稿を再試行します。"
  exit 0
fi

# 投稿済みの記録は不要になる。 残すと次の merge で同じ内容を二重投稿する材料になるため掃除
# するが、 掃除の失敗で merge を止めはしない (投稿は成功しており、 PR 上のコメントが正本)。
if ! discard_local_record; then
  printf '[pre-merge-codex-review] レビュー記録を PR #%s に投稿しましたが、 ローカルの記録 (%s 直下) を削除できませんでした。 次の merge で二重投稿にならないよう、 手動で削除してください。\n' \
    "$PR_NUMBER" "$GIT_DIR" >&2
fi

exit 0
