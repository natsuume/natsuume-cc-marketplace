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
# ## 対象 PR の解決 (repo スコープの一本化)
#
# 照合は **hook プロセスの cwd の repo** の文脈で行うため (`gh pr view` を cwd で実行する)、
# 実際に merge される repo がそこから動きうる形はすべて deny する。 対象 PR の解決と head SHA
# の取得自体は gh に委ね、 本 script はフラグ文法を解析しない:
#   - `--repo` / `-R` の文字列を含む → 別 repo の PR を merge しうるため deny する
#   - 正規化済みコマンドが `gh pr merge` で始まらない (前置コマンド連結・`cd` ・環境変数の
#     前置き等) → 実際の merge 対象 repo が cwd と食い違いうるため deny する。 連続列より
#     後ろの連結 (`gh pr merge 123 && ...`) は照合対象に影響しないので許容する
#   - `gh pr merge` の連続列が 2 回以上ある → どの PR を照合すべきか一意に決まらないため
#     deny する
#   - 連続列の後ろのトークンを、 リダイレクト (演算子と書き込み先) を除いたうえで、 シェル
#     演算子 (`&&` / `||` / `;` / `|` / `&`) の手前まで順に見る:
#       - 走査範囲のトークンがすべて `-` 始まり (フラグ) → 対象指定なしとして `gh pr view`
#         の current branch 解決に委ねる
#       - 最初の非フラグトークンが連続列の**直後**にある → 全数字 (PR 番号) のときだけ
#         その値を `gh pr view` に渡す。 URL や branch 名は別 repo を指しうるため deny する
#       - 最初の非フラグトークンが直後以外の位置にある (例: `gh pr merge --squash 123`)
#         → フラグの値なのか対象指定なのかを本 script は判別できないため deny する
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

# 同一コマンド内に `gh pr merge` の連続列が複数あると、 どの PR を照合すれば merge 全体を
# 検証したことになるかが決まらない。 1 つでも未レビューの merge を通さないため deny する。
MERGE_SEQUENCE_COUNT=0
SEQUENCE_SCAN="$NORMALIZED"
while :; do
  case "$SEQUENCE_SCAN" in
    *"gh pr merge"*)
      SEQUENCE_SCAN="${SEQUENCE_SCAN#*gh pr merge}"
      MERGE_SEQUENCE_COUNT=$((MERGE_SEQUENCE_COUNT+1))
      ;;
    *) break ;;
  esac
done
if [ "$MERGE_SEQUENCE_COUNT" -gt 1 ]; then
  deny "マージをブロックしました。 同一の Bash 呼び出しに \`gh pr merge\` が複数含まれています。

本 gate は 1 回の呼び出しにつき 1 つの対象 PR を照合するため、 複数 merge が混在するとどの PR を検証したのか一意に決まりません。

merge は 1 回の Bash 呼び出しにつき 1 つにして、 それぞれ個別に実行してください。"
  exit 0
fi

# merge は単独コマンドとして先頭に置かれていることを要求する。 前置コマンドの連結
# (`cd other-repo; gh pr merge 123` / `FOO=bar gh pr merge 123` 等) は、 実際に merge が
# 行われる repo と本 gate が照合に使う repo (hook プロセスの cwd) が食い違いうるため deny する。
# 連続列より **後ろ** の連結 (`gh pr merge 123 && ...`) は照合対象に影響しないので許容する。
TRIMMED="${NORMALIZED# }"
case "$TRIMMED" in
  "gh pr merge") ;;
  "gh pr merge "*) ;;
  *)
    deny "マージをブロックしました。 \`gh pr merge\` は単独のコマンドとして実行してください (前置コマンドとの連結・\`cd\` の後置き・環境変数の前置き等は、 実際に merge される repo と本 gate が照合する repo が食い違う経路になります)。

対象 repo のディレクトリで作業している状態にしてから、 \`gh pr merge\` を先頭に置いた単独のコマンドとして実行してください (\`gh pr merge 123 --squash\` の後ろに別コマンドを連結する形は許容されます)。"
    exit 0
    ;;
esac

# 連続列の後ろのトークンを走査して対象指定を取り出す (フラグ文法は解析しない)。
TARGET=""
REST="${TRIMMED#gh pr merge}"
case "$REST" in
  " "*) REST="${REST# }" ;;
  *) REST="" ;;
esac
# リダイレクト演算子とその書き込み先は引数ではないため走査対象から外す (除去しないと
# `gh pr merge --squash > /tmp/log` の `/tmp/log` を対象指定と誤認する)。 除去は演算子から
# 後ろに続く filename-safe な文字列までに限り、 想定外の shape はそのまま残して後段の分類で
# deny 側に倒す。
REST=$(printf '%s' "$REST" \
  | sed -E 's/[0-9]?(&>>|&>|>>|>\&|<\&|<<<|<<|<>|>|<)[[:space:]]*[A-Za-z0-9_./=+@:-]*/ /g')
# シェル演算子 (`&&` / `||` / `;` / `|` / `&`) から後ろは別コマンドなので走査対象から外す。
REST="${REST%%[&|;]*}"
# リダイレクト除去で生じた空白を畳み直し、 先頭の空白を落とす。
REST=$(printf '%s' "$REST" | tr -s ' ')
REST="${REST# }"

TOKEN_POSITION=0
while [ -n "$REST" ]; do
  TOKEN="${REST%% *}"
  case "$REST" in
    *" "*) REST="${REST#* }" ;;
    *) REST="" ;;
  esac
  [ -n "$TOKEN" ] || continue
  case "$TOKEN" in
    -*)
      TOKEN_POSITION=$((TOKEN_POSITION+1))
      continue
      ;;
  esac
  # `-` で始まらない最初のトークン。 連続列の直後にある場合だけ対象指定として受理する。
  if [ "$TOKEN_POSITION" -ne 0 ]; then
    deny "マージをブロックしました。 merge 対象の PR を一意に特定できません (\`${TOKEN}\` がフラグの値なのか対象 PR の指定なのかを本 gate は判別できません)。

対象 PR を指定する場合は \`gh pr merge <number>\` のように \`gh pr merge\` の直後に置いてください (フラグはその後ろに並べます。 例: \`gh pr merge 123 --squash\`)。 対象 PR のブランチに切り替えたうえで対象指定を省略する形 (\`gh pr merge --squash\`) も使えます。"
    exit 0
  fi
  case "$TOKEN" in
    *[!0-9]*)
      deny "マージをブロックしました。 merge 対象の PR を一意に特定できません (\`gh pr merge\` の直後の語 \`${TOKEN}\` は PR 番号ではありません)。

本 gate は対象 PR の解決を gh に委ねますが、 照合はコマンドを実行する repo の文脈で行うため、 別 repo を指しうる URL 指定や branch 名指定は受理しません。 対象 PR は同じ repo 内の番号 (\`gh pr merge 123\`) で指定するか、 対象 PR のブランチに切り替えて対象指定を省略する形 (\`gh pr merge --squash\`) を使ってください。"
      exit 0
      ;;
    *) TARGET="$TOKEN" ;;
  esac
  break
done

if ! command -v gh >/dev/null 2>&1; then
  deny "マージをブロックしました。 gh が見つからないため、 対象 PR の head SHA と codex review コメントを取得できません。 gh CLI をインストール (および \`gh auth login\`) してから、もう一度 gh pr merge を実行してください。"
  exit 0
fi

PR_JSON=""
if [ -n "$TARGET" ]; then
  PR_JSON=$(gh pr view "$TARGET" --json headRefOid,reviews 2>/dev/null) || PR_JSON=""
else
  PR_JSON=$(gh pr view --json headRefOid,reviews 2>/dev/null) || PR_JSON=""
fi

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
