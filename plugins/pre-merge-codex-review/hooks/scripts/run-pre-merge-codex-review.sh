#!/bin/bash
# run-pre-merge-codex-review.sh
# **codex review の定型実行 wrapper**。 `pre-merge-codex-review:codex-reviewer` subagent が
# 本 wrapper を foreground 起動する設計で、 block-bg-codex-wrapper.sh の agent_type gate に
# より、 同 subagent 以外 (メインセッションの直接 Bash 実行等) からの起動は deny される。
#
# ## 何をするか
#
# 1. 起動時に git-dir 直下の stale なレビュー記録 (final attestation / pending attestation /
#    投稿用本文) を削除する (前回の中断が残した記録で merge が通る経路を残さないため)
# 2. current branch の PR を gh で解決し、 head SHA・base branch・base commit SHA を取得する
#    (取得値は git コマンドへ渡す前に形式を検証する)
# 3. ローカル HEAD が PR の head SHA と一致し、 working tree が clean であることを確認する
#    (どちらかを欠くと、 head SHA を記録しながら別内容をレビューした記録を残すことになる)
# 4. PR が記録する base commit がローカルの `origin/<base>` から到達可能 (ancestor) である
#    ことを確認する (到達不能なら 1 度だけ fetch して再判定し、 それでも到達不能なら実行
#    しない)。 さらに merge-base..HEAD の差分が空でないことを確認する (空のままレビューすると、
#    何も見ていない 「レビュー済み」 記録を残すことになるため)
# 5. PR の実 base との merge-base..HEAD 全差分に対して codex review を foreground 実行する
#    (codex companion に `--base origin/<base>` を渡すと merge-base からの branch diff になる)
# 6. 記録を書く直前に HEAD と working tree の状態を再確認し、 レビュー中に変化していれば記録を
#    書かない (レビューした内容と記録する head SHA の乖離を残さないため)
# 7. 機械可読 header + review report を投稿用の本文ファイルへ、 PR 番号と head SHA を pending
#    attestation へ書く (いずれも git-dir 直下。 PR への投稿は行わない)
#
# 投稿用の本文ファイルの形式:
#
#   <!-- codex-review: head=<レビュー対象の full head SHA> status=pass|findings -->
#   # Codex Review
#   ...
#
# pending attestation の形式 (2 行):
#
#   pr=<PR 番号>
#   head=<レビュー対象の full head SHA>
#
# **本 wrapper は PR に投稿しない**。 レビュー完了の記録をローカルに書くところまでが責務で、
# subagent lifecycle hook (auto-mark.sh) が parent-safe report を検証して pending を final
# attestation へ昇格し、 merge gate (block-pre-merge.sh) が `gh pr merge` 時にその記録を検証して
# PR に投稿する。 記録は merge の approve でも findings 0 件の証明でもない (status=findings でも
# 「レビュー済み」 として成立し、 findings への対応判断は通常のレビューフローで行う)。
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

# 本 script が作るファイル (作業用 temp とレビュー記録) は他ユーザに読ませる必要がないため、
# 生成時の permission を所有者のみに絞る。
umask 077

_RUN_PRE_MERGE_CODEX_REVIEW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/codex-companion-resolver.sh
source "$_RUN_PRE_MERGE_CODEX_REVIEW_SCRIPT_DIR/lib/codex-companion-resolver.sh"
# shellcheck source=lib/review-status.sh
source "$_RUN_PRE_MERGE_CODEX_REVIEW_SCRIPT_DIR/lib/review-status.sh"
# shellcheck source=lib/markers.sh
source "$_RUN_PRE_MERGE_CODEX_REVIEW_SCRIPT_DIR/lib/markers.sh"

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

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || fail "現在の cwd は git repository ではありません。 codex review は PR の作業リポジトリ内で実行してください。"

# レビュー記録の置き場 (git-dir 直下)。 ファイル名の単一ソースは lib/markers.sh。
FINAL_ATTESTATION_PATH=$(pre_merge_final_marker_path "$GIT_DIR") || fail "レビュー記録の path を解決できませんでした。"
PENDING_ATTESTATION_PATH=$(pre_merge_pending_marker_path "$GIT_DIR") || fail "レビュー記録の path を解決できませんでした。"
COMMENT_BODY_PATH=$(pre_merge_comment_body_path "$GIT_DIR") || fail "レビュー記録の path を解決できませんでした。"

# 起動時に stale な記録を消す。 前回の実行が途中で落ちて残した記録 (別 head SHA のものを
# 含む) を放置すると、 merge gate がそれを投稿して 「レビューしていない状態を通す」 経路に
# なるため、 レビューを始める前に必ず片付ける。 消せない場合は記録の一貫性を保証できないので
# 中断する。
rm -f "$FINAL_ATTESTATION_PATH" "$PENDING_ATTESTATION_PATH" "$COMMENT_BODY_PATH" || fail "古いレビュー記録 (${GIT_DIR} 直下) を削除できませんでした。 権限を確認してから再実行してください。"

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

# gh の応答値はそのまま git コマンドの引数になるため、 形式を検証してから使う (`-` 始まりの
# 値が option として解釈される経路と、 想定外の文字を含む ref 名を排除する)。
printf '%s' "$BASE_OID" | grep -qE '^[0-9a-f]{40}$' || fail "PR の base commit SHA (${BASE_OID}) が 40 桁の 16 進数ではありません。 gh の応答が想定形式と異なるため中断します。"
# branch 名は先頭 `-` を拒否したうえで、 妥当性の判定を git 自身の ref 名規則に委ねる
# (独自の ASCII allowlist を置くと、 有効な Unicode branch 名の PR を恒久的に弾いてしまう)。
# 先頭 `-` の拒否を先に行うことで、 値が `git check-ref-format` のオプションとして解釈される
# 余地も塞ぐ。
case "$BASE_NAME" in
  -*) fail "PR の base branch 名 (${BASE_NAME}) が \`-\` で始まっています。 git コマンドのオプションとして解釈される値は受け付けません。" ;;
esac
git check-ref-format --branch "$BASE_NAME" >/dev/null 2>&1 || fail "PR の base branch 名 (${BASE_NAME}) が git の branch 名規則を満たしません。 gh の応答が想定形式と異なるため中断します。"
# ref 名として妥当なら追加の文字制限は課さない。 base の値は git へ argv で渡り、 codex
# companion へも argv (shell 非経由) で渡って JSON-RPC の値として codex CLI に届くため、
# shell が再解釈する経路が無い。 独自の文字 denylist を置くと、 `feat(api)/x` のような
# git 的に合法な branch 名を持つ PR を恒久的に弾いてしまう。

# ローカル HEAD と PR head の一致確認。 不一致のままレビューすると、 投稿する header の
# head SHA (= PR の head) と実際にレビューした内容が食い違うため実行しない。
LOCAL_HEAD=$(git rev-parse HEAD 2>/dev/null) || fail "ローカル HEAD の SHA を取得できませんでした。"
if [ "$LOCAL_HEAD" != "$HEAD_SHA" ]; then
  fail "ローカル HEAD (${LOCAL_HEAD}) が PR #${PR_NUMBER} の head (${HEAD_SHA}) と一致しません。 未 push の commit がある場合は push し、 remote が先行している場合は fetch / pull してから再実行してください。"
fi

# working tree が dirty なら実行しない。 codex の review は merge-base からの diff を
# working tree 込みで見るため、 dirty のままでは 「head SHA を記録しながら、 その head とは
# 異なる内容をレビューする」 乖離が生じ、 投稿するコメントが実態を表さなくなる。
WORKTREE_STATUS=$(git status --porcelain 2>/dev/null) || fail "git status の実行に失敗しました。 repo の状態を確認してから再実行してください。"
if [ -n "$WORKTREE_STATUS" ]; then
  fail "working tree に未コミットの変更があります (追跡対象の変更・未追跡ファイルのいずれも対象)。 codex review は working tree を含む差分を見るため、 dirty のままでは PR の head (${HEAD_SHA}) と異なる内容をレビューした記録を残すことになります。 追跡対象の変更は commit するか \`git stash\` で退避し、 未追跡ファイルは commit するか repo 外への移動・削除で片付けてから再実行してください (\`git status --porcelain\` の出力が空になる状態が条件です)。"
fi

# codex companion には base を **branch ref** (`origin/<baseRefName>`) として渡す
# (companion 経由で codex に渡る base は branch 名として扱われるため)。 レビュー範囲の正しさは
# wrapper 側で担保する: PR が記録する base commit (`baseRefOid`) が、 ローカルの
# remote-tracking ref から到達可能 (ancestor) であることを確認する。 base branch は PR 作成後
# も進むため完全一致は要求しない (一致を要求すると、 base が 1 commit 進んだだけの PR で
# レビューが永久に実行できなくなる)。 到達不能なら 1 度だけ fetch して再判定し、 それでも
# 到達不能なら 「ローカルの base が PR の base branch を表していない」 状態なので、 誤った範囲を
# レビューして 「レビュー済み」 コメントを残さないよう実行しない。
#
# ref の指定は 2 系統を使い分ける: wrapper 内の git 操作はすべて完全修飾
# (`refs/remotes/origin/<base>`) で行い、 companion へ渡す `--base` だけは shorthand
# (`origin/<base>`) を使う (codex は branch 名を期待するため)。 shorthand の解決は
# `refs/<name>` → `refs/tags/<name>` → `refs/heads/<name>` → remote-tracking の順に候補を見る
# ため、 これら先行 namespace に `origin/<base>` という名前の ref があると remote-tracking ref
# が隠され、 「wrapper が検証する ref」 と 「codex がレビューする ref」 が乖離する。 該当する
# ref が 1 つでも存在する場合は実行しない。
BASE_REF="origin/${BASE_NAME}"
BASE_REF_FULL="refs/remotes/origin/${BASE_NAME}"

for _shadow_ref in "refs/${BASE_REF}" "refs/tags/${BASE_REF}" "refs/heads/${BASE_REF}"; do
  if git show-ref --verify --quiet "$_shadow_ref"; then
    fail "ローカルに \`${_shadow_ref}\` があります。 この ref は shorthand \`${BASE_REF}\` の解決で remote-tracking ref (\`${BASE_REF_FULL}\`) より先に選ばれ、 wrapper が検証する ref と codex がレビューする ref が食い違う原因になります。 当該 ref を改名または削除してから再実行してください。"
  fi
done

base_oid_is_in_origin_base() {
  git rev-parse --verify --quiet "$BASE_REF_FULL" >/dev/null 2>&1 || return 1
  git merge-base --is-ancestor "$BASE_OID" "$BASE_REF_FULL" 2>/dev/null
}

if ! base_oid_is_in_origin_base; then
  note "PR の base commit (${BASE_OID}) がローカルの ${BASE_REF_FULL} に含まれないため fetch します。"
  # 明示 refspec で fetch する。 refspec を省略すると single-branch clone 等では FETCH_HEAD
  # しか更新されず、 再判定が読む完全修飾 ref が更新されないまま恒久的に fail する。
  git fetch origin "+refs/heads/${BASE_NAME}:${BASE_REF_FULL}" >&2 || fail "\`git fetch origin +refs/heads/${BASE_NAME}:${BASE_REF_FULL}\` に失敗しました。 ネットワークと remote の状態を確認してから再実行してください。"
  if ! base_oid_is_in_origin_base; then
    fail "PR #${PR_NUMBER} の base commit (${BASE_OID}) が fetch 後もローカルの ${BASE_REF_FULL} に含まれません。 ローカルの base ref が PR の base branch (${BASE_NAME}) を表していない可能性があります (別 remote を指す PR / base branch の force-push 等)。 remote の設定と \`git fetch origin ${BASE_NAME}\` の結果を確認してから再実行してください。"
  fi
fi

# レビュー対象範囲 (merge-base..HEAD) に差分が無い状態でレビューすると、 何も見ていない
# 「レビュー済み」 記録を残すことになるため実行しない。
MERGE_BASE=$(git merge-base HEAD "$BASE_REF_FULL" 2>/dev/null) || fail "HEAD と ${BASE_REF_FULL} の merge-base を解決できませんでした (shallow clone / 履歴の不足が考えられます)。 \`git fetch --unshallow\` 等で履歴を補ってから再実行してください。"
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
# ファイルに落として記録の本文に使い、 stderr (進捗) はそのまま流す。
# `if !` で失敗を捕捉する (set -e 配下では node の非ゼロ exit で script が即終了し、
# fail() のメッセージを出す機会を失うため)。
if ! node "$COMPANION" review --wait --base "$BASE_REF" > "$REVIEW_OUT"; then
  fail "codex review が失敗しました。 レビュー記録は書きません。 上の output を確認して再実行してください。"
fi

# review report を stdout に流す (codex-reviewer subagent が parent-safe report に正規化する
# ための入力)。
cat "$REVIEW_OUT"

# findings の有無は codex review 出力の 「指摘なし」 表現の有無で判定する保守的な
# heuristic。 判定ロジックの詳細 (対象行数・finding 記述の優先判定・表現の正規化) は
# lib/review-status.sh の detect_review_status を参照。 status は merge gate の判定には
# 影響しない (pass / findings のどちらでも「レビュー済み」として成立する) ため、 誤判定は
# 記録の精度の問題に留まる。
STATUS=$(detect_review_status "$REVIEW_OUT")

# report 本文が header 形の文字列を含む場合 (レビュー対象の差分に header 形のコードが含まれ、
# report がそれを引用した場合等) は無害化してから埋め込む。 merge gate は本文 **先頭行** の
# header だけを attestation として扱うが、 引用された header 形をそのまま残すと読み手にとって
# 「別 SHA の attestation」 に見える紛らわしい本文になるため。
{
  printf '<!-- codex-review: head=%s status=%s -->\n' "$HEAD_SHA" "$STATUS"
  sed 's/<!-- codex-review:/<!-- codex-review (quoted):/g' "$REVIEW_OUT"
} > "$COMMENT_BODY"

# 長すぎる本文は PR コメントの上限で投稿が失敗するため、 行単位で切り詰める (header 行は
# 先頭にあるため常に残る)。
awk -v budget="$MAX_COMMENT_BODY_CHARS" '
  { used += length($0) + 1 }
  used > budget { print ""; print "(report が長いため以降を省略しました。 完全な出力は codex review の実行ログを参照してください。)"; exit }
  { print }
' "$COMMENT_BODY" > "$COMMENT_BODY.capped" || fail "レビュー本文の整形に失敗しました。"
mv "$COMMENT_BODY.capped" "$COMMENT_BODY" || fail "レビュー本文の差し替えに失敗しました。"

# 記録を書く直前の再検証。 レビュー実行中に commit / checkout / 編集が入ると、 記録する
# head SHA と実際にレビューした内容が乖離する。 記録は 「この head SHA の内容をレビューした」
# という主張なので、 状態が変わっていたら記録を書かずに中断する (再実行すれば新しい状態で
# レビューし直す)。
POST_REVIEW_HEAD=$(git rev-parse HEAD 2>/dev/null) || fail "HEAD の再確認に失敗しました。 レビュー記録は書いていません。"
if [ "$POST_REVIEW_HEAD" != "$HEAD_SHA" ]; then
  fail "codex review の実行中に HEAD が変わりました (${HEAD_SHA} → ${POST_REVIEW_HEAD})。 レビューした内容と記録する head SHA が食い違うため記録を書きません。 HEAD が PR の head と一致する状態で再実行してください。"
fi
POST_REVIEW_STATUS=$(git status --porcelain 2>/dev/null) || fail "working tree の再確認に失敗しました。 レビュー記録は書いていません。"
if [ -n "$POST_REVIEW_STATUS" ]; then
  fail "codex review の実行中に working tree が変更されました。 レビューした内容と記録する head SHA が食い違うため記録を書きません。 working tree が clean な状態で再実行してください。"
fi

# 本文ファイルの書き込み以降で失敗した場合の後始末。 本文だけが残ると 「pending の無い
# 本文」 という中途半端な記録になるため、 失敗時は本文も消してから中断する。
fail_after_body() {
  rm -f "$COMMENT_BODY_PATH" 2>/dev/null || true
  fail "$1"
}

# レビュー記録を git-dir 直下に書く。 WORK_DIR (TMPDIR 配下) と git-dir は別 filesystem に
# なりうるため、 本文は mv で移す (失敗したら記録を残さず中断する)。 pending は git-dir 内の
# temp file から mv して atomic に置く (読み手が中途半端な内容を観測しないため)。
mv "$COMMENT_BODY" "$COMMENT_BODY_PATH" || fail "投稿用の本文を ${COMMENT_BODY_PATH} へ書き込めませんでした。 レビュー記録は残っていません。"

PENDING_TMP=$(mktemp "${PENDING_ATTESTATION_PATH}.XXXXXX" 2>/dev/null) || fail_after_body "pending attestation の一時ファイルを作成できませんでした。 レビュー記録は残っていません。"
if ! printf 'pr=%s\nhead=%s\n' "$PR_NUMBER" "$HEAD_SHA" > "$PENDING_TMP" 2>/dev/null; then
  rm -f "$PENDING_TMP" 2>/dev/null || true
  fail_after_body "pending attestation を書き込めませんでした。 レビュー記録は残っていません。"
fi
if ! mv "$PENDING_TMP" "$PENDING_ATTESTATION_PATH" 2>/dev/null; then
  rm -f "$PENDING_TMP" 2>/dev/null || true
  fail_after_body "pending attestation を ${PENDING_ATTESTATION_PATH} へ配置できませんでした。 レビュー記録は残っていません。"
fi

note "レビュー記録をローカルに保存しました (pr=${PR_NUMBER} head=${HEAD_SHA} status=${STATUS})。 \`gh pr merge\` を実行すると merge gate が記録を PR に投稿してから merge に進みます。"

exit 0
