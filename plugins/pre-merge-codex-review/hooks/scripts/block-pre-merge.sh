#!/bin/bash
# block-pre-merge.sh
# codex review 済みでない PR の `gh pr merge` をブロックする PreToolUse フック。
#
# policy: fail-closed (関与したコマンドに限る)
#   関与条件 (下記 1.) を満たさない Bash 呼び出しには一切関与しない (無出力で exit 0)。
#   関与したコマンドについては、 判定不能・想定外状況 (gh / jq 不在、 PR 解決失敗、
#   取得・照合失敗) をすべて deny に倒す。
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
# 3. **レビューコメント照合**: merge 対象 PR の現在の head SHA と PR レビュー一覧を gh で
#    取得し、 レビュー本文に機械可読 header
#    `<!-- codex-review: head=<full head SHA> status=pass|findings -->` を持ち head SHA が
#    現在の head と完全一致するものがあるかを確認する。 在れば無出力で終了し (既定の
#    許可フローに委ねる)、 無ければ deny して `pre-merge-codex-review:codex-reviewer`
#    subagent の実行を案内する。 status は pass / findings のどちらでも「レビュー済み」
#    として成立する (merge の approve や findings 0 件の証明ではない)。
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

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
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

# 受理正規形との完全一致を要求する。 一致しない関与コマンド (前置コマンドの連結・複数
# merge・リダイレクト・シェル演算子・quote・`$` 展開・フラグより後ろの数字等) は、 本 script
# の解釈と shell の実挙動が乖離しうるため一律 deny する。
if ! printf '%s' "$NORMALIZED" \
  | grep -qE '^gh pr merge( [0-9]+)?( -[A-Za-z0-9=/._:@+-]+)*$'; then
  deny "マージをブロックしました。 本 gate が解釈できるのは \`gh pr merge [<number>] [flags]\` の単独正規形だけです (対象 PR の番号を置けるのは \`gh pr merge\` の直後のみ、 それ以降は \`-\` 始まりのフラグのみ)。

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
    gh pr view "$1" --json headRefOid,reviews 2>/dev/null
  else
    gh pr view --json headRefOid,reviews 2>/dev/null
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

MATCHED=$(printf '%s' "$PR_JSON" | jq -r --arg head "$HEAD_SHA" '
  [
    .reviews[]?
    | (.body // "")
    | select(
        contains("<!-- codex-review: head=" + $head + " status=pass -->")
        or contains("<!-- codex-review: head=" + $head + " status=findings -->")
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

if [ "$MATCHED" -gt 0 ]; then
  # 現 head SHA に一致する codex review コメントが在る = レビュー済み。 decision を出さず
  # 既定の許可フローに委ねる。
  exit 0
fi

deny "マージをブロックしました。 merge 前に codex review を実行してください。

対象 PR の head SHA: ${HEAD_SHA}
この head SHA に一致する codex review コメント (\`<!-- codex-review: head=<SHA> status=pass|findings -->\` 形式の header を持つ PR レビュー) が PR 上に見つかりませんでした。

Agent / Task tool で subagent_type=\"pre-merge-codex-review:codex-reviewer\", model=\"sonnet\" を起動してください。 subagent が codex review を実行し、 完了時に header 付きの PR レビューを投稿します。 投稿後に \`gh pr merge\` を再試行してください。

レビューは **current branch の PR** に対して実行・投稿されます。 別の PR を番号で指定して merge しようとしている場合は、 先にその PR のブランチへ \`git switch\` してから subagent を起動してください (別ブランチのまま起動すると、 レビューが current branch の PR に付いてしまい、 この merge は deny のままになります)。

model 未指定の Agent 起動は Fable セッションでは agent-discipline の hook に deny されるため、 上記の model を常に明示してください。

PR に commit を追加すると head SHA が変わり、 過去のレビューコメントは自動的に失効します (再レビューが必要になります)。"
