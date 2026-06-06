#!/bin/bash
# run-codex-review.sh
# pre-push-review v1.1.0 で導入された **codex review の定型実行 wrapper**。 deny メッセージ
# で Claude に `bash <plugin>/hooks/scripts/run-codex-review.sh` を案内し、 Skill (`/codex:review`)
# 経由ではなく本 wrapper 経由で codex review を実行させる。
#
# ## なぜ wrapper を介すか (v1.0.0 → v1.1.0 の設計変更背景)
#
# v1.0.0 以前は deny メッセージで `/codex:review --wait --scope branch` (slash command) を
# 案内していたが、 `/codex:review` の review.md (codex プラグイン公式定義) は AskUserQuestion
# 分岐で **「review が小さい場合のみ wait 推奨、 それ以外は background 推奨」** という方針を
# Claude に prompt する設計だった。 結果として:
#   - Claude が AskUserQuestion で 「Run in background」 を選択
#   - Bash tool の `run_in_background: true` で codex companion を起動
#   - block-bg-codex-review.sh が deny → review 1 サイクル無駄
# のループが頻発した。 さらに block-bg-codex-review.sh の検知漏れ経路 (parser bug 等)
# があると background 起動が完走して marker が永久に書かれない silent failure になる。
#
# wrapper 方式に切り替えると:
#   1. Claude は wrapper を Bash で呼ぶだけ (Skill expand を経由しない)
#   2. wrapper 内で `--wait --scope branch` を **hardcode** するため background 起動の余地がない
#   3. wrapper 自身が完了時に marker を書くため、 PostToolUse の検知ロジック (=
#      auto-mark.sh の Bash 経路) が不要になり、 codex-review-detect.sh / block-bg-codex-review.sh
#      も廃止できる
# = Claude の自由度を絞ることで「bg 起動による silent failure」 を構造的に排除する。
#
# ## marker 書き込みポリシー
#
# **codex review が exit 0 で完了したら verdict (approve / needs-attention) に関わらず marker を
# 書く**。 verdict ベースの判定 (= needs-attention のときは marker を書かず loop discipline を
# 強制する) も考えたが、 以下の理由で 「常に書く」 設計に倒した:
#   - codex review の output 形式 (markdown の `Verdict: approve` 行) は spec ではなく
#     companion の実装詳細で、 将来変わりうる。 文字列 grep ベースの verdict 判定は脆い
#   - 「review が指摘を出したら必ず修正してから push」 の判断は Claude の自律性に委ねる方
#     が運用上自然 (security-reviewer subagent も verdict 判定なしで完了時に marker を書く)
#   - Claude が指摘を無視して push した場合は、 修正に伴う差分変化で他 3 マーカー (simplify /
#     code-review / security) が失効し、 そちらで loop が回る (本 marker 単独では loop を
#     強制しないが、 4 マーカー全体としては修正を経由する設計に倒れる)
#
# exit 非 0 (codex review 失敗 / 中断) のときは marker を書かない。 失敗した review で marker
# を書くと未レビュー push が通る経路を作るため。
#
# ## working tree が dirty な場合の挙動
#
# auto-mark.sh の codex 検知と同じく、 dirty 時 (staged または unstaged 変更あり) は marker を
# 書かない。 `/codex:review --scope branch` は committed 部分のみを review するため、 dirty 状態
# で marker を書くと後の commit 状態と hash 衝突を起こし得る (詳細は auto-mark.sh の
# 該当箇所のコメント参照)。 wrapper はこの場合、 codex review 自体は実行せず early-exit して
# Claude に commit を促すエラーメッセージを返す。
#
# ## hooks/scripts/ 配下に置く理由 (hook ではないのに)
#
# 厳密には本 script は `hooks` event に bind されない通常の shell script だが、 既存の lib /
# helper と path を揃えて参照しやすくするため `hooks/scripts/` 配下に置く。 plugin.json の
# `hooks` 配列には登録しない。 deny メッセージで案内する起動 path は
# `${CLAUDE_PLUGIN_ROOT}/hooks/scripts/run-codex-review.sh`。

set -e

_RUN_CODEX_REVIEW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/diff-hash.sh
source "$_RUN_CODEX_REVIEW_SCRIPT_DIR/lib/diff-hash.sh"
# shellcheck source=lib/markers.sh
source "$_RUN_CODEX_REVIEW_SCRIPT_DIR/lib/markers.sh"
# shellcheck source=lib/codex-companion-resolver.sh
source "$_RUN_CODEX_REVIEW_SCRIPT_DIR/lib/codex-companion-resolver.sh"

# stderr に人間可読のエラーを出して非ゼロ exit する helper。 set -e と組み合わせて使う。
fail() {
  printf '%s\n' "[run-codex-review] $1" >&2
  exit 1
}

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || fail "現在の cwd は git repository ではありません。 codex review は repo 内で実行してください。"

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null) || fail "detached HEAD では codex review を実行できません。 ブランチを切ってから再実行してください。"

case "$BRANCH" in
  master|main)
    fail "default branch (master/main) では本プラグインは gate しません。 codex review も実行不要です。"
    ;;
esac

BASE=$(detect_base_branch) || fail "default branch を検出できませんでした (origin/HEAD 未設定 / origin 不在 等)。 git remote set-head origin -a 等で base を解決してください。"

# dirty 検知。 auto-mark.sh の codex 経路と同じく、 dirty 状態で marker を書くと commit 後の
# 状態と hash 衝突を起こす経路があるため、 codex review 自体を実行せず early-exit する。
if ! git diff --quiet 2>/dev/null || ! git diff --quiet --cached 2>/dev/null; then
  fail "working tree が dirty です (staged または unstaged 変更あり)。 git status で確認 → commit してから再実行してください。 \`/codex:review --scope branch\` は committed 部分のみを review するため、 dirty 状態で marker を書くと commit 後の状態と hash 衝突を起こす経路があります。"
fi

# branch 全差分が空 (= base と同一) なら review 対象がなく実行不要。 これは block-pre-push.sh
# も空 push を通す挙動と整合する。
HASH=$(compute_review_hash "$BASE") || fail "branch diff hash の計算に失敗しました。"
if [ "$HASH" = "$EMPTY_DIFF_HASH" ]; then
  printf '[run-codex-review] branch 全差分が空のため codex review は実行不要です。\n'
  # marker は書かない (空差分時は block-pre-push.sh が gate を skip するため不要)。
  exit 0
fi

# codex companion path 解決
COMPANION=$(resolve_codex_companion) || fail "codex プラグインが見つかりません。 \`claude plugin install codex@openai-codex\` で導入してください (cache 配下に codex-companion.mjs が無い)。"

# codex review を foreground 実行。 引数は `--wait --scope branch` を hardcode することで、
# Claude / 呼び出し側からの argument injection で background 起動になる余地を排除する。
# stdout は標準出力にそのまま流す (Claude が Bash tool の tool_response として受け取る形)。
#
# `set -e` は ON のまま node が非ゼロ exit すると本 script も非ゼロ exit する。 trap は使わず、
# 「正常完了 (exit 0) のときだけ marker を書く」 を `exit 0 直前で書く` 設計で表現する。
printf '[run-codex-review] codex companion: %s\n' "$COMPANION" >&2
printf '[run-codex-review] running: node %s review --wait --scope branch\n' "$COMPANION" >&2

# `set -e` を一時的に外し、 node の exit code を捕捉する。 失敗時もエラーメッセージを出して
# marker を書かずに非ゼロ exit する。
set +e
node "$COMPANION" review --wait --scope branch
NODE_EXIT=$?
set -e

if [ "$NODE_EXIT" -ne 0 ]; then
  fail "codex review が失敗しました (exit code: $NODE_EXIT)。 marker は書きません。 上の output を確認して再実行してください。"
fi

# marker を書く: 「codex review が exit 0 で完了した」 という事実だけを根拠に、 verdict
# (approve / needs-attention) に関わらず書く (ヘッダの 「marker 書き込みポリシー」 参照)。
printf '%s' "$HASH" > "$(codex_marker_path "$GIT_DIR")"
printf '[run-codex-review] codex marker を更新しました: %s\n' "$(codex_marker_path "$GIT_DIR")" >&2

exit 0
