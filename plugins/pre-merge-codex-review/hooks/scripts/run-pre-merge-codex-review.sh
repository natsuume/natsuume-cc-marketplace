#!/bin/bash
# run-pre-merge-codex-review.sh
# **codex review の定型実行 wrapper**。 `pre-merge-codex-review:codex-reviewer` subagent が
# 本 wrapper を foreground 起動する設計で、 block-bg-codex-wrapper.sh の agent_type gate に
# より、 同 subagent 以外 (メインセッションの直接 Bash 実行等) からの起動は deny される。
#
# ## 何をするか
#
# 1. current branch の PR を gh で解決し、 head SHA・base branch・base commit SHA を取得する
# 2. ローカル HEAD が PR の head SHA と一致することを確認する (一致しなければ、 レビュー
#    対象と PR の内容が食い違うため実行しない)
# 3. ローカルの `origin/<base>` が PR の base commit と一致することを確認する (ずれていれば
#    1 度だけ fetch して再確認し、 それでも一致しなければ実行しない)。 さらに merge-base..HEAD
#    の差分が空でないことを確認する (空のままレビューすると、 何も見ていない 「レビュー済み」
#    コメントを残すことになるため)
# 4. PR の実 base との merge-base..HEAD 全差分に対して codex review を foreground 実行する
#    (codex companion に `--base origin/<base>` を渡すと merge-base からの branch diff になる)
# 5. 完了時に、 機械可読 header + review report を `gh pr review --comment` で PR レビューと
#    して投稿する
#
# 投稿する本文の形式:
#
#   <!-- codex-review: head=<レビュー対象の full head SHA> status=pass|findings -->
#   # Codex Review
#   ...
#
# **投稿がレビュー完了の記録である**。 ローカル marker / pending attestation の機構は持たず、
# merge gate (block-pre-merge.sh) は PR 上のこのコメントだけを照合する。 投稿は merge の
# approve でも findings 0 件の証明でもない (status=findings でも「レビュー済み」として
# 成立し、 findings への対応判断は通常のレビューフローで行う)。
#
# ## なぜ wrapper を介すか
#
# 公式 codex プラグインの `/codex:review` slash command 定義は 「review が小さい場合のみ
# wait 推奨、 それ以外は background 推奨」 という方針を Claude に prompt する。 slash command
# 経由で案内すると Claude が background 起動を選び、 gate に deny されて 1 サイクル無駄に
# なる (検知漏れがあれば、 codex-reviewer が review 出力を観察できない silent failure に
# なる)。 wrapper 方式では Claude は wrapper を Bash で呼ぶだけで、 wrapper 内で `--wait` を
# hardcode するため background 起動の余地がない。
#
# ## hooks/scripts/ 配下に置く理由 (hook ではないのに)
#
# 本 script は `hooks` event に bind されない通常の shell script だが、 lib と path を揃えて
# 参照しやすくするため `hooks/scripts/` 配下に置く。 hooks.json の `hooks` 配列には登録
# しない。 案内する起動 path は
# `${CLAUDE_PLUGIN_ROOT}/hooks/scripts/run-pre-merge-codex-review.sh`。

set -e

_RUN_PRE_MERGE_CODEX_REVIEW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/codex-companion-resolver.sh
source "$_RUN_PRE_MERGE_CODEX_REVIEW_SCRIPT_DIR/lib/codex-companion-resolver.sh"

# GitHub の PR コメント本文の上限 (65536 文字) に対する安全側の閾値。 これを超える report は
# 行単位で切り詰めて投稿する (byte 単位で切ると multibyte 文字が壊れるため行単位で切る)。
MAX_COMMENT_BODY_CHARS=60000

WORK_DIR=""
# trap 本文はシングルクォート (発火時評価) で、 WORK_DIR が未設定でも no-op になる。
trap 'if [ -n "$WORK_DIR" ]; then rm -rf "$WORK_DIR"; fi' EXIT

# stderr に人間可読のエラーを出して非ゼロ exit する helper。 set -e と組み合わせて使う。
fail() {
  printf '%s\n' "[run-pre-merge-codex-review] $1" >&2
  exit 1
}

note() {
  printf '%s\n' "[run-pre-merge-codex-review] $1" >&2
}

command -v git >/dev/null 2>&1 || fail "git が見つかりません。"
command -v gh >/dev/null 2>&1 || fail "gh が見つかりません。 gh CLI をインストールし \`gh auth login\` を済ませてから再実行してください。"
command -v jq >/dev/null 2>&1 || fail "jq が見つかりません。 jq をインストールしてから再実行してください。"
command -v node >/dev/null 2>&1 || fail "node が見つかりません。 codex companion の実行に node が必要です。"

git rev-parse --git-dir >/dev/null 2>&1 || fail "現在の cwd は git repository ではありません。 codex review は PR の作業リポジトリ内で実行してください。"

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null) || fail "detached HEAD では PR を解決できません。 PR のブランチに切り替えてから再実行してください。"

# 対象 PR の解決は gh に委ねる (current branch に対応する PR)。
PR_JSON=$(gh pr view --json number,headRefOid,baseRefName,baseRefOid 2>/dev/null) || fail "current branch (${BRANCH}) の PR を gh で解決できませんでした。 PR を作成する / \`gh auth login\` を済ませる / ネットワークを確認する のいずれかが必要です。"

PR_NUMBER=$(printf '%s' "$PR_JSON" | jq -r '.number // empty')
HEAD_SHA=$(printf '%s' "$PR_JSON" | jq -r '.headRefOid // empty')
BASE_NAME=$(printf '%s' "$PR_JSON" | jq -r '.baseRefName // empty')
BASE_OID=$(printf '%s' "$PR_JSON" | jq -r '.baseRefOid // empty')
[ -n "$PR_NUMBER" ] || fail "PR 番号を取得できませんでした (gh の応答に number が含まれていません)。"
[ -n "$HEAD_SHA" ] || fail "PR の head SHA を取得できませんでした (gh の応答に headRefOid が含まれていません)。"
[ -n "$BASE_NAME" ] || fail "PR の base branch を取得できませんでした (gh の応答に baseRefName が含まれていません)。"
[ -n "$BASE_OID" ] || fail "PR の base commit SHA を取得できませんでした (gh の応答に baseRefOid が含まれていません)。"

# ローカル HEAD と PR head の一致確認。 不一致のままレビューすると、 投稿する header の
# head SHA (= PR の head) と実際にレビューした内容が食い違うため実行しない。
LOCAL_HEAD=$(git rev-parse HEAD 2>/dev/null) || fail "ローカル HEAD の SHA を取得できませんでした。"
if [ "$LOCAL_HEAD" != "$HEAD_SHA" ]; then
  fail "ローカル HEAD (${LOCAL_HEAD}) が PR #${PR_NUMBER} の head (${HEAD_SHA}) と一致しません。 未 push の commit がある場合は push し、 remote が先行している場合は fetch / pull してから再実行してください。"
fi

# codex companion には base を **branch ref** (`origin/<baseRefName>`) として渡す
# (companion 経由で codex に渡る base は branch 名として扱われるため)。 一方でレビュー範囲の
# 正しさは wrapper 側で担保する: ローカルの `origin/<baseRefName>` が PR の実 base commit
# (baseRefOid) を指していることを確認し、 ずれていれば 1 度だけ fetch して再確認する。 それでも
# 一致しなければ、 誤った範囲や空の差分をレビューして 「レビュー済み」 コメントを残すことに
# なるため実行しない。
BASE_REF="origin/${BASE_NAME}"

base_ref_oid_matches() {
  local local_oid
  local_oid=$(git rev-parse --verify --quiet "refs/remotes/origin/${BASE_NAME}") || return 1
  [ "$local_oid" = "$BASE_OID" ]
}

if ! base_ref_oid_matches; then
  note "ローカルの ${BASE_REF} が PR の base commit (${BASE_OID}) と一致しないため fetch します。"
  git fetch origin "$BASE_NAME" >&2 || fail "\`git fetch origin ${BASE_NAME}\` に失敗しました。 ネットワークと remote の状態を確認してから再実行してください。"
  if ! base_ref_oid_matches; then
    fail "ローカルの ${BASE_REF} が PR #${PR_NUMBER} の base commit (${BASE_OID}) と一致しません (fetch 後も不一致)。 base branch が更新中か、 PR の base が別 remote を指している可能性があります。 \`git fetch origin ${BASE_NAME}\` と PR の base 設定を確認してから再実行してください。"
  fi
fi

# レビュー対象範囲 (merge-base..HEAD) に差分が無い状態でレビューすると、 何も見ていない
# 「レビュー済み」 コメントを PR に残すことになるため実行しない。
MERGE_BASE=$(git merge-base HEAD "$BASE_REF" 2>/dev/null) || fail "HEAD と ${BASE_REF} の merge-base を解決できませんでした (shallow clone / 履歴の不足が考えられます)。 \`git fetch --unshallow\` 等で履歴を補ってから再実行してください。"
if git diff --quiet "$MERGE_BASE" HEAD 2>/dev/null; then
  fail "レビュー対象の差分が空です (merge-base ${MERGE_BASE} と HEAD が同一内容)。 PR に差分がある状態で再実行してください。"
fi

COMPANION=$(resolve_codex_companion) || fail "codex プラグインが見つかりません。 \`claude plugin install codex@openai-codex\` で導入してください (versioned cache / unversioned cache / marketplace clone のいずれにも codex-companion.mjs が見つかりませんでした。 詳細な探索 path は \`lib/codex-companion-resolver.sh\` のヘッダを参照)。"

WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pre-merge-codex-review.XXXXXX") || fail "一時ディレクトリを作成できませんでした。"
REVIEW_OUT="$WORK_DIR/review.md"
COMMENT_BODY="$WORK_DIR/comment.md"

note "PR #${PR_NUMBER} (head ${HEAD_SHA}) を base ${BASE_REF} との merge-base からレビューします。"
note "codex companion: ${COMPANION}"
note "running: node ${COMPANION} review --wait --base ${BASE_REF}"

# codex review を foreground 実行する。 `--wait` を hardcode することで、 呼び出し側からの
# argument injection で background 起動になる余地を排除する。 stdout (review report) は
# ファイルに落として投稿本文に使い、 stderr (進捗) はそのまま流す。
# `if !` で失敗を捕捉する (set -e 配下では node の非ゼロ exit で script が即終了し、
# fail() のメッセージを出す機会を失うため)。
if ! node "$COMPANION" review --wait --base "$BASE_REF" > "$REVIEW_OUT"; then
  fail "codex review が失敗しました。 PR へのレビュー投稿は行いません。 上の output を確認して再実行してください。"
fi

# review report を stdout に流す (codex-reviewer subagent が parent-safe report に正規化する
# ための入力)。
cat "$REVIEW_OUT"

# findings の有無は codex review 出力の 「指摘なし」 表現の有無で判定する保守的な heuristic。
# 判定できない場合は findings 側に倒す (「findings 0 件」 を誤って主張しないため)。
# status は merge gate の判定には影響しない (pass / findings のどちらでも「レビュー済み」
# として成立する) ため、 誤判定は記録の精度の問題に留まる。
STATUS="findings"
if grep -qiE '^[[:space:]]*(no material findings|no findings|no issues found)[[:space:].!]*$' "$REVIEW_OUT"; then
  STATUS="pass"
fi

{
  printf '<!-- codex-review: head=%s status=%s -->\n' "$HEAD_SHA" "$STATUS"
  cat "$REVIEW_OUT"
} > "$COMMENT_BODY"

# 長すぎる本文は PR コメントの上限で投稿が失敗するため、 行単位で切り詰める (header 行は
# 先頭にあるため常に残る)。
awk -v budget="$MAX_COMMENT_BODY_CHARS" '
  { used += length($0) + 1 }
  used > budget { print ""; print "(report が長いため以降を省略しました。 完全な出力は codex review の実行ログを参照してください。)"; exit }
  { print }
' "$COMMENT_BODY" > "$COMMENT_BODY.capped" || fail "レビュー本文の整形に失敗しました。"
mv "$COMMENT_BODY.capped" "$COMMENT_BODY" || fail "レビュー本文の差し替えに失敗しました。"

# レビュー完了の記録を PR に投稿する。 gh の確認出力は stderr へ回して stdout を review
# report だけに保つ。
if ! gh pr review "$PR_NUMBER" --comment --body-file "$COMMENT_BODY" >&2; then
  fail "PR #${PR_NUMBER} へのレビュー投稿に失敗しました。 codex review 自体は完了していますが、 レビュー済みの記録が PR に残っていないため merge gate は deny を続けます。 gh の認証・権限を確認して再実行してください。"
fi

note "PR #${PR_NUMBER} に codex review コメントを投稿しました (head=${HEAD_SHA} status=${STATUS})。"

exit 0
