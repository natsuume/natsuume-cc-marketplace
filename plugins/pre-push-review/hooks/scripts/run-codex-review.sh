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
# ## なぜ codex-review-customize の review.md patch 拡張ではなく wrapper か
#
# 本 marketplace には既に `codex-review-customize` プラグインがあり、 公式 codex プラグインの
# slash command 定義 (`review.md`) を sed で patch する抽象 (= disable-model-invocation を除去
# して Skill 経由起動を有効化) を持っている。 同じ抽象の延長で「review.md から AskUserQuestion
# ブロックを削除する patch を追加 → background 推奨を消す」 という方向もあり得たが、 v1.1.0 は
# wrapper 方式を選んだ。 理由:
#   - **PostToolUse detection が原理的に bg 起動を捕捉できない問題は patch では解けない**:
#     Claude が `/codex:review` Skill expand 後に `Bash({run_in_background: true})` を返す
#     経路は、 review.md の AskUserQuestion を消したとしても Claude が独自判断で取りうる
#     (review.md の Background flow 例の方が消えない限り)。 patch を増やせば増やすほど公式
#     プラグイン定義との drift が広がり、 codex プラグイン側の version 追従コストが増える
#   - **marker 直書きの構造的価値**: wrapper が自身で marker を書く設計は、 PostToolUse hook
#     が tool 完了タイミングを観測する仕組みそのものを bypass する。 「foreground/bg 区別」
#     「tool_response の is_error/interrupted 判定」 「dirty tree タイミングの hash 衝突
#     対策」 等を hook 層で複雑に重ねる必要がなく、 wrapper の exit code 1 つで成否を判断
#     できる。 これは patch では到達できない深さの解
# `codex-review-customize` プラグインは v1.1.0 以降 pre-push-review の観点では不要だが、
# Skill 経由 `/codex:review` を別用途で使いたいユーザにとっては引き続き有用 (README 参照)。
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
# ## cwd セマンティクス (multi-repo workflow との非対称)
#
# wrapper は dirty 判定 / base 検出 / branch / ハッシュ計算 / marker パスを **すべて起動時の
# cwd** で行う (= 「いま居る repo に対して codex review を実行する」 と記録する)。 auto-mark.sh
# と同じセマンティクスで、 block-pre-push.sh の target-resolver (`cd subrepo && git push` /
# `git -C subrepo push` などから push target cwd を解決する) とは非対称。
#
# 通常運用 (= session cwd が push 対象 repo と一致) では wrapper と block-pre-push.sh が
# 同じ cwd を見るため marker は正しく照合される。 一方、 ユーザが multi-repo workflow で
# session cwd を A、 push target を B (`cd B && git push` 等) にした場合、 Claude が
# session cwd A のまま wrapper を起動すると wrapper は A の marker を書き、 block-pre-push.sh
# は B の marker を要求するため **hash 不一致で deny** に倒れる (= fail-closed)。 これは
# auto-mark.sh と同じ安全性質 (= 「push する repo をその cwd で review し直す」 という正しい
# 挙動を強制するだけで、 未レビュー push を通す bypass にはならない) で、 セキュリティ上
# の問題ではない。 ただし運用前提として「**wrapper は push 対象 repo の cwd で実行する**」
# を守る必要がある (= multi-repo で push target を override する場合は、 wrapper も同じ
# target_cwd で実行する: 例えば `cd subrepo && bash <plugin>/hooks/scripts/run-codex-review.sh`)。
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

# ## wrapper では `install_exit_trap` (lib/exit-trap.sh) を **使わない** 理由
#
# block-pre-push.sh / auto-mark.sh は通常パスが全て exit 0 (deny は JSON 経由) で、
# `install_exit_trap` の trap は 「真に予期せぬ非ゼロ exit」 のみを diagnostic で
# 通知する暗黙の contract で動いている。 これに対し本 wrapper は **想定済みの error
# パス全てで `fail()` → exit 1** を返す設計 (detached HEAD / not a git repo / BASE
# 未検出 / dirty tree / companion 不在 / node 失敗 / marker 書き込み失敗)。 もし
# `install_exit_trap` を install すると、 fail() 経由の意図的 exit 1 でも trap が
# 発火し、「予期せぬエラーで hook が終了しました / marketplace に bug として報告
# してください」 という誤誘導メッセージが fail() の human-readable メッセージの
# 直後に出てしまう (= ユーザは実装 bug を踏んだと誤認する経路)。
#
# 代わりに、 wrapper では **MARKER_TMP の cleanup trap だけ** を install する。
# fail() / 正常完了 / 真の予期せぬ exit のいずれでも tmp ファイルを残さない。
# 真の予期せぬエラー (SIGINT / source 失敗等) の診断は wrapper 自身は行わず、 fail()
# が全 error pattern をカバーする設計に倒す (= wrapper の責務範囲を「codex review
# の foreground 実行と marker 書き込み」 に narrow する)。
MARKER_TMP=""
# trap の本文はシングルクォート (= 設定時ではなく発火時に評価) で、 trap 発火時点の
# `$MARKER_TMP` の値を見る。 wrapper 完了時 (mv 成功で MARKER_TMP は既に消費済) や
# fail() 経由の早期 exit (MARKER_TMP="" のまま or 部分書き込み) いずれでも、 rm が空文字 /
# 既消失 path に対して no-op (`|| true` で非ゼロ exit を抑止)。
trap 'rm -f "$MARKER_TMP" 2>/dev/null || true' EXIT

# stderr に人間可読のエラーを出して非ゼロ exit する helper。 set -e と組み合わせて使う。
# EXIT trap で MARKER_TMP の cleanup が走るため fail() 内では明示削除しない。
fail() {
  printf '%s\n' "[run-codex-review] $1" >&2
  exit 1
}

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || fail "現在の cwd は git repository ではありません。 codex review は repo 内で実行してください。"

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null) || fail "detached HEAD では codex review を実行できません。 ブランチを切ってから再実行してください。"

# default branch (master/main) では本プラグインは gate しない設計 (block-pre-push.sh が
# git-guardrails に委譲して skip する)。 wrapper も同じ前提に従い 「review 不要」 として
# **exit 0** で抜ける (= fail にしない)。 fail (exit 1) にすると Bash tool 呼び出しが
# `tool_response.is_error = true` で返るため、 Claude が 「失敗 → 再実行」 ループに乗る経路
# が生じる。 default branch では正常状態として informational message を stderr に出して
# 抜けるのが gate 全体の意図 (= 「review 不要」 を正常終了で伝える) と一致する。
case "$BRANCH" in
  master|main)
    printf '[run-codex-review] default branch (%s) では本プラグインは gate しません。 codex review は実行不要です。\n' "$BRANCH" >&2
    exit 0
    ;;
esac

BASE=$(detect_base_branch) || fail "default branch を検出できませんでした (origin/HEAD 未設定 / origin 不在 等)。 git remote set-head origin -a 等で base を解決してください。"

# dirty 検知。 auto-mark.sh の codex 経路と同じく、 dirty 状態で marker を書くと commit 後の
# 状態と hash 衝突を起こす経路があるため、 codex review 自体を実行せず early-exit する。
#
# `git diff --quiet` の exit code は 0 (差分なし) / 1 (差分あり = dirty) / 128 (git error:
# corrupt index、 GIT_DIR 不在、 権限不足 等) の 3 系統がある。 これらを区別せずに 「! git
# diff --quiet」 で truthy 判定すると、 128 (git error) も dirty 扱いされて 「working tree
# が dirty」 という誤メッセージで fail し、 真因 (= corrupt repo) の診断が困難になる。
# 対策: 各 git diff の exit code を変数に取り、 1 (dirty) と 128 (error) を別経路で fail
# させる。
#
# **set -e との相互作用に注意**: `set -e` 配下では `git diff --quiet; _diff_unstaged=$?` と
# 直書きすると、 dirty 時の exit 1 で `set -e` がトリガーされて script 全体が即 exit し、
# `_diff_unstaged=$?` の代入も後段の fail() も実行されない (EXIT trap の 「予期せぬエラー」
# 経路に倒れて diagnostic が壊れる)。 `|| _diff_x=$?` パターンで `||` 右辺に exit code を
# 取ることで `set -e` を回避しつつ exit code を捕捉する (codex review v1.1.0 fix で導入)。
_diff_unstaged=0
git diff --quiet 2>/dev/null || _diff_unstaged=$?
_diff_staged=0
git diff --quiet --cached 2>/dev/null || _diff_staged=$?
if [ "$_diff_unstaged" -ge 128 ] || [ "$_diff_staged" -ge 128 ]; then
  fail "git diff --quiet が git error (exit >= 128) で失敗しました。 repo が corrupt / GIT_DIR が壊れている / 権限不足の可能性があります。 git status の出力を確認してください。"
fi
if [ "$_diff_unstaged" -ne 0 ] || [ "$_diff_staged" -ne 0 ]; then
  fail "working tree が dirty です (staged または unstaged 変更あり)。 git status で確認 → commit してから再実行してください。 \`/codex:review --scope branch\` は committed 部分のみを review するため、 dirty 状態で marker を書くと commit 後の状態と hash 衝突を起こす経路があります。"
fi

# branch 全差分が空 (= base と同一) なら review 対象がなく実行不要。 これは block-pre-push.sh
# も空 push を通す挙動と整合する。
HASH=$(compute_review_hash "$BASE") || fail "branch diff hash の計算に失敗しました。"
if [ "$HASH" = "$EMPTY_DIFF_HASH" ]; then
  # 進捗 / 完了メッセージは全て stderr に統一する (caller である Bash tool は stdout を
  # tool_response.stdout として受け取るため、 codex review の出力 vs wrapper の status を
  # 分離して扱える設計に倒す)。 v1.1.0 の初版では 1 行のみ stdout に出していたが、
  # E16 の指摘で他の status (L131, L132, L153) と一致させた。
  printf '[run-codex-review] branch 全差分が空のため codex review は実行不要です。\n' >&2
  # marker は書かない (空差分時は block-pre-push.sh が gate を skip するため不要)。
  exit 0
fi

# codex companion path 解決
COMPANION=$(resolve_codex_companion) || fail "codex プラグインが見つかりません。 \`claude plugin install codex@openai-codex\` で導入してください (versioned cache / unversioned cache / marketplace clone のいずれにも codex-companion.mjs が見つかりませんでした。 詳細な探索 path は \`lib/codex-companion-resolver.sh\` のヘッダを参照)。"

# codex review を foreground 実行。 引数は `--wait --scope branch` を hardcode することで、
# Claude / 呼び出し側からの argument injection で background 起動になる余地を排除する。
# stdout は標準出力にそのまま流す (Claude が Bash tool の tool_response として受け取る形)。
#
# 正常完了 (exit 0) のときだけ marker を書く設計。 失敗時はエラーメッセージを出して
# marker を書かずに非ゼロ exit する (fail() が exit 1 する)。
printf '[run-codex-review] codex companion: %s\n' "$COMPANION" >&2
printf '[run-codex-review] running: node %s review --wait --scope branch\n' "$COMPANION" >&2

# `if !` で node の成否を直接捕捉する。 set +e / set -e の dance や exit code 変数を使わない:
# - set -e 配下では `node ...` が非ゼロ exit すると script 全体が即終了するため、 失敗時に
#   fail() でエラーメッセージを出す機会を失う
# - `if !` は `set -e` の影響を受けず、 失敗ブランチでカスタムメッセージを出せる
# - exit code の数値そのものは error 表示で使わない (失敗したという事実だけが本質) ため
#   $? を変数に保存する必要もない
if ! node "$COMPANION" review --wait --scope branch; then
  fail "codex review が失敗しました。 marker は書きません。 上の output を確認して再実行してください。"
fi

# marker を書く: 「codex review が exit 0 で完了した」 という事実だけを根拠に、 verdict
# (approve / needs-attention) に関わらず書く (ヘッダの 「marker 書き込みポリシー」 参照)。
# marker path は書き込み先と表示で共有するため変数に保持 (2 回計算回避)。
# 前段の GIT_DIR / BRANCH / BASE / HASH / COMPANION と同じく `|| fail` 経由で人間可読
# エラーを返す (set -e 単独だと marker path 計算失敗で silent exit する経路を埋める)。
MARKER_PATH=$(codex_marker_path "$GIT_DIR") || fail "codex marker path の計算に失敗しました。"

# `printf > marker` は truncate+write の 2 段階で atomic ではないため、 SIGKILL や disk
# full で write 途中で死ぬと marker が空 / 部分書き込み状態で残る。 block-pre-push.sh の
# hash 比較は fail-closed (= 不一致なら deny) なので security 上の影響は無いが、 「codex
# review が exit 0 で完了したのに marker が空」 という silent な乖離を防ぐため、 tmp +
# atomic rename パターンで書き込む。 tmp の prefix は marker と同じディレクトリ (= 通常
# `<git-dir>` 配下) に置くことで、 mv が同 filesystem 内 rename = atomic になることを
# 保証する (異 filesystem 跨ぎ rename は cross-device で fallback copy になり atomic
# 性が崩れる)。
MARKER_TMP="${MARKER_PATH}.tmp.$$"
printf '%s' "$HASH" > "$MARKER_TMP" || fail "codex marker の一時ファイル書き込みに失敗しました ($MARKER_TMP)。 disk full / 権限不足等を確認してください。"
mv "$MARKER_TMP" "$MARKER_PATH" || fail "codex marker の atomic rename に失敗しました ($MARKER_TMP → $MARKER_PATH)。"
printf '[run-codex-review] codex marker を更新しました: %s\n' "$MARKER_PATH" >&2

exit 0
