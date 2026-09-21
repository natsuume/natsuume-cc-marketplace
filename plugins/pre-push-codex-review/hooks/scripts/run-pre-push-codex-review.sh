#!/bin/bash
# run-pre-push-codex-review.sh
# **codex review の定型実行 wrapper**。 `pre-push-codex-review:codex-reviewer` subagent が
# 本 wrapper を foreground 起動する設計で、 block-bg-codex-wrapper.sh の agent_type gate に
# より、 同 subagent 以外 (メインセッションの直接 Bash 実行等) からの起動は deny される。
#
# ## なぜ wrapper を介すか
#
# 公式 codex プラグインの `/codex:review` slash command 定義 (review.md) は AskUserQuestion
# 分岐で **「review が小さい場合のみ wait 推奨、 それ以外は background 推奨」** という方針を
# Claude に prompt する。 slash command 経由で案内すると:
#   - Claude が AskUserQuestion で 「Run in background」 を選択
#   - Bash tool の `run_in_background: true` で codex companion を起動
#   - background 起動を検知する gate が deny → review 1 サイクル無駄
# のループが起きる。 さらに検知漏れ経路 (parser bug 等) があると background 起動が完走して
# codex-reviewer が review 出力を観察できない silent failure になる。
#
# wrapper 方式では:
#   1. Claude は wrapper を Bash で呼ぶだけ (Skill expand を経由しない)
#   2. wrapper 内で `--wait --scope branch` を **hardcode** するため background 起動の余地がない
#   3. wrapper が完了時に review 対象 hash の pending attestation を書き、
#      codex-reviewer の parent-safe report 完了後に auto-mark.sh が final marker へ昇格する
# = Claude の自由度を絞ることで「bg 起動による silent failure」 を構造的に排除する。
#
# 公式 slash command 定義を patch して Skill 経由起動を有効化する方式を採らないのは、
# (1) Claude が Skill expand 後に `Bash({run_in_background: true})` を返す経路は patch では
# 塞げない (2) patch を増やすほど公式プラグイン定義との drift が広がり version 追従コストが
# 増える、 の 2 点による。 wrapper が review 開始時点の hash を保持することで、 review 中に
# branch state が変わっても auto-mark.sh は current hash 不一致で final marker への昇格を
# 拒否できる。 wrapper exit 0 だけで final marker を書かず parent-safe report の正常完了も
# 要求することで、 review 実行と親への安全な結果配送の両方を完了条件にする。
#
# ## pending attestation / marker 昇格ポリシー
#
# **codex review が exit 0 で完了したら verdict (approve / needs-attention) に関わらず pending
# attestation を書く**。final codex-reviewed marker は codex-reviewer が `Status: pass` または
# `Status: findings` の正規 report を返した後、auto-mark.sh が attestation hash と current hash
# の一致を確認して atomic rename する。verdict ベースの判定 (= needs-attention のときは marker
# を書かず loop discipline を強制する) は以下の理由で採らない:
#   - codex review の output 形式 (markdown の `Verdict: approve` 行) は spec ではなく
#     companion の実装詳細で、 将来変わりうる。 文字列 grep ベースの verdict 判定は脆い
#   - 「review が指摘を出したら必ず修正してから push」 の判断は Claude の自律性に委ねる方
#     が運用上自然
#   - Claude が指摘を無視して push した場合は、 修正に伴う差分変化でマーカーが失効し、
#     次の push で再び review が要求される
#
# exit 非 0 (codex review 失敗 / 中断) のときは pending attestation を書かない。起動時に
# stale pending を削除するため、過去 run の attestation を後続 report が誤利用しない。
#
# ## working tree が dirty な場合の挙動
#
# dirty 時 (staged または unstaged 変更あり) は pending attestation を
# 書かない。 `/codex:review --scope branch` は committed 部分のみを review するため、 dirty 状態
# で attestation を書くと後の commit 状態と hash 衝突を起こし得る (詳細は auto-mark.sh の
# 該当箇所のコメント参照)。 wrapper はこの場合、 codex review 自体は実行せず early-exit して
# Claude に commit を促すエラーメッセージを返す。
#
# ## cwd セマンティクス (multi-repo workflow との非対称)
#
# wrapper は dirty 判定 / base 検出 / branch / ハッシュ計算 / pending path を **すべて起動時の
# cwd** で行う (= 「いま居る repo に対して codex review を実行する」 と記録する)。 auto-mark.sh
# と同じセマンティクスで、 block-pre-push-codex.sh の target-resolver (`cd subrepo && git push` /
# `git -C subrepo push` などから push target cwd を解決する) とは非対称。
#
# 通常運用 (= session cwd が push 対象 repo と一致) では wrapper と block-pre-push-codex.sh が
# 同じ cwd を見るため marker は正しく照合される。 一方、 ユーザが multi-repo workflow で
# session cwd を A、 push target を B (`cd B && git push` 等) にした場合、 Claude が
# session cwd A のまま wrapper を起動すると wrapper は A の pending attestation を書き、
# auto-mark.sh も A の marker を更新し、 block-pre-push-codex.sh
# は B の marker を要求するため **hash 不一致で deny** に倒れる (= fail-closed)。 これは
# auto-mark.sh と同じ安全性質 (= 「push する repo をその cwd で review し直す」 という正しい
# 挙動を強制するだけで、 未レビュー push を通す bypass にはならない) で、 セキュリティ上
# の問題ではない。 ただし運用前提として「**wrapper は push 対象 repo の cwd で実行する**」
# を守る必要がある (= multi-repo で push target を override する場合は、 wrapper も同じ
# target_cwd で実行する: 例えば
# `cd subrepo && bash <plugin>/hooks/scripts/run-pre-push-codex-review.sh`)。
#
# ## hooks/scripts/ 配下に置く理由 (hook ではないのに)
#
# 厳密には本 script は `hooks` event に bind されない通常の shell script だが、 既存の lib /
# helper と path を揃えて参照しやすくするため `hooks/scripts/` 配下に置く。 hooks.json の
# `hooks` 配列には登録しない。 deny メッセージで案内する起動 path は
# `${CLAUDE_PLUGIN_ROOT}/hooks/scripts/run-pre-push-codex-review.sh`。

set -e

_RUN_PRE_PUSH_CODEX_REVIEW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/diff-hash.sh
source "$_RUN_PRE_PUSH_CODEX_REVIEW_SCRIPT_DIR/lib/diff-hash.sh"
# shellcheck source=lib/markers.sh
source "$_RUN_PRE_PUSH_CODEX_REVIEW_SCRIPT_DIR/lib/markers.sh"
# shellcheck source=lib/codex-companion-resolver.sh
source "$_RUN_PRE_PUSH_CODEX_REVIEW_SCRIPT_DIR/lib/codex-companion-resolver.sh"

# ## wrapper では `install_exit_trap` (lib/exit-trap.sh) を **使わない** 理由
#
# block-pre-push-codex.sh / auto-mark.sh は通常パスが全て exit 0 (deny は JSON 経由) で、
# `install_exit_trap` の trap は 「真に予期せぬ非ゼロ exit」 のみを diagnostic で
# 通知する暗黙の contract で動いている。 これに対し本 wrapper は **想定済みの error
# パス全てで `fail()` → exit 1** を返す設計 (detached HEAD / not a git repo / BASE
# 未検出 / dirty tree / companion 不在 / node 失敗 / marker 書き込み失敗)。 もし
# `install_exit_trap` を install すると、 fail() 経由の意図的 exit 1 でも trap が
# 発火し、「予期せぬエラーで hook が終了しました / marketplace に bug として報告
# してください」 という誤誘導メッセージが fail() の human-readable メッセージの
# 直後に出てしまう (= ユーザは実装 bug を踏んだと誤認する経路)。
#
# 代わりに、 wrapper では **ATTESTATION_TMP の cleanup と terminal sentinel の書き込みだけ**
# を EXIT trap で行う。 fail() / 正常完了 / 真の予期せぬ exit のいずれでも tmp ファイルを
# 残さない。 真の予期せぬエラー (SIGINT / source 失敗等) の診断は wrapper 自身は行わず、
# fail() が全 error pattern をカバーする設計に倒す (= wrapper の責務範囲を「codex review
# の foreground 実行と pending attestation 書き込み」 に narrow する)。
ATTESTATION_TMP=""
# この run を一意に識別する id と、 その run 専用の terminal sentinel の絶対パス。
# git-dir を解決するまでは空で、 空の間は sentinel を書かない (書き込み先が定まらないため)。
RUN_ID=""
TERMINAL_SENTINEL_PATH=""

# ## terminal sentinel (run ごとの終了信号)
#
# Bash tool は timeout 時に wrapper を kill せず background へ移行させる。 移行後の
# codex-reviewer subagent は wrapper の終了を観測できないため、 wrapper 自身が exit 時に
# 「この run は終わった」 という信号を残す。 信号を出力ストリームのテキストではなく
# **ファイルの出現** に置くのは、 review 本文が進捗行と同じ語を含んでも誤判定せず、 かつ
# 別 run の残骸と取り違えないため (ファイル名と内容の両方に run id を入れる)。
#
# stdout には終了行 (`terminal sentinel end run=<id>`) を出してから sentinel を書く。
# 回収側は sentinel の出現で終了を知り、 output file の最終行が終了行であることで本文が
# 最後まで届いていることを確認する。

# run ごとに一意な id (`<pid>-<epoch 秒>-<8 桁 16 進>`) を作る。 文字集合を [0-9a-f-] に
# 限るのは、 回収側の待機ループが `grep -qE " run=<id>$"` で照合するため (正規表現の
# メタ文字が混ざると照合が壊れる)。 /dev/urandom が読めない環境では bash の $RANDOM で
# 代替する (pid と epoch 秒が同じ run が同一ホストで衝突する確率を下げるための補助)。
new_run_id() {
  local random_suffix
  random_suffix=$(od -An -N4 -tx1 /dev/urandom 2>/dev/null | tr -dc '0-9a-f')
  case "$random_suffix" in
    [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
    *) random_suffix=$(printf '%04x%04x' "$((RANDOM % 65536))" "$((RANDOM % 65536))") ;;
  esac
  printf '%s-%s-%s' "$$" "$(date +%s)" "$random_suffix"
}

# 引数: <sentinel を置くディレクトリ>
# 24 時間より古い自 plugin の sentinel だけを削除する。 実行中の別 run の sentinel を消すと
# その run の回収が終了を検知できなくなるため、 mtime での足切りを必ず挟む。
# `-mtime +0` は 「経過時間を 24 時間単位に切り捨てた値が 0 より大きい」 = 24 時間以上前の
# 意味で、 GNU / BSD (macOS) の find が共通で解釈する。
prune_stale_terminal_sentinels() {
  local directory="$1"

  [ -n "$directory" ] || return 0
  find "$directory" -maxdepth 1 -type f -name "${TERMINAL_SENTINEL_PREFIX}*" \
    -mtime +0 -exec rm -f {} + 2>/dev/null || true
}

# 引数: <exit status>
# 自 run の sentinel に終了状態を 1 行だけ書く (tmp + mv で atomic に置く)。 読み手が
# 中途半端な内容を観測しないようにするため、 直接の追記ではなく rename で公開する。
write_terminal_sentinel() {
  local exit_status="$1"
  local state="failed"
  local sentinel_tmp

  [ -n "$TERMINAL_SENTINEL_PATH" ] || return 0
  if [ "$exit_status" = "0" ]; then
    state="ok"
  fi
  # 終了行の前に改行を 1 つ置く。 直前に stdout へ流れた review 本文が改行で終わって
  # いない場合でも、 終了行が独立した最終行になることを保証するため (回収側は output
  # file の最終行が終了行であることで本文の完結を判定する)。 本文が改行で終わる通常
  # ケースでは空行が 1 つ挟まるだけで、 最終行が終了行であることは変わらない。
  # stdout への書き込み失敗 (閉じられた pipe 等) で trap が `set -e` により途中終了し、
  # sentinel が書かれないまま exit status も置き換わる経路を塞ぐため、 失敗を無視する。
  printf '\nterminal sentinel end run=%s\n' "$RUN_ID" || true
  sentinel_tmp="${TERMINAL_SENTINEL_PATH}.tmp.$$"
  if printf 'status=%s run=%s\n' "$state" "$RUN_ID" > "$sentinel_tmp" 2>/dev/null; then
    mv "$sentinel_tmp" "$TERMINAL_SENTINEL_PATH" 2>/dev/null \
      || rm -f "$sentinel_tmp" 2>/dev/null || true
  else
    rm -f "$sentinel_tmp" 2>/dev/null || true
  fi
}

# EXIT trap 本体。 trap の引数で受け取った exit status から終了状態を決める
# (fail() 経由の非ゼロ exit は `status=failed`、 exit 0 経路は `status=ok`)。
# ATTESTATION_TMP の rm は、 wrapper 完了時 (mv 成功で消費済) や fail() 経由の早期 exit
# (空文字 or 部分書き込み) いずれでも no-op になる (`|| true` で非ゼロ exit を抑止)。
on_exit() {
  rm -f "$ATTESTATION_TMP" 2>/dev/null || true
  write_terminal_sentinel "$1"
}
trap 'on_exit $?' EXIT
# 信号による終了を EXIT trap に非ゼロの exit status として伝える。 信号の既定動作のまま
# 終了すると EXIT trap の `$?` が直前のコマンドの 0 を引き継ぐことがあり、 review を
# 完了していない run の sentinel が `status=ok` になる経路が生じるため、 128 + 信号番号で
# 明示的に exit する。
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# stderr に人間可読のエラーを出して非ゼロ exit する helper。 set -e と組み合わせて使う。
# EXIT trap で ATTESTATION_TMP の cleanup が走るため fail() 内では明示削除しない。
fail() {
  printf '%s\n' "[run-pre-push-codex-review] $1" >&2
  exit 1
}

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || fail "現在の cwd は git repository ではありません。 codex review は repo 内で実行してください。"

# sentinel の path は回収側が shell 変数へ補間するため、 cwd に依存しない絶対形で案内する
# (`git rev-parse --git-dir` は cwd 次第で相対 path を返す)。
ABSOLUTE_GIT_DIR=$(git rev-parse --absolute-git-dir 2>/dev/null) || fail "git directory の絶対 path を解決できませんでした。"
prune_stale_terminal_sentinels "$ABSOLUTE_GIT_DIR"
RUN_ID=$(new_run_id) || fail "run id の生成に失敗しました。"
TERMINAL_SENTINEL_PATH=$(codex_terminal_sentinel_path "$ABSOLUTE_GIT_DIR" "$RUN_ID") || fail "terminal sentinel path の計算に失敗しました。"
# 案内行は stdout と stderr の両方に出す。 stdout の先頭行にすることで、 background へ
# 移行した run の output file の先頭行から回収側が run id と sentinel path を取れる。
printf 'terminal sentinel: %s run=%s\n' "$TERMINAL_SENTINEL_PATH" "$RUN_ID"
printf 'terminal sentinel: %s run=%s\n' "$TERMINAL_SENTINEL_PATH" "$RUN_ID" >&2

PENDING_PATH=$(codex_pending_marker_path "$GIT_DIR") || fail "codex pending attestation path の計算に失敗しました。"
# 前回 run が report 配送前に異常終了した場合の stale attestation を先に破棄する。
rm -f "$PENDING_PATH" || fail "stale codex pending attestation を削除できませんでした。"

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null) || fail "detached HEAD では codex review を実行できません。 ブランチを切ってから再実行してください。"

# default branch (master/main) では本プラグインは gate しない設計 (block-pre-push-codex.sh が
# git-guardrails に委譲して skip する)。 wrapper も同じ前提に従い 「review 不要」 として
# **exit 0** で抜ける (= fail にしない)。 fail (exit 1) にすると Bash tool 呼び出しが
# `tool_response.is_error = true` で返るため、 Claude が 「失敗 → 再実行」 ループに乗る経路
# が生じる。 default branch では正常状態として informational message を stderr に出して
# 抜けるのが gate 全体の意図 (= 「review 不要」 を正常終了で伝える) と一致する。
case "$BRANCH" in
  master|main)
    printf '[run-pre-push-codex-review] default branch (%s) では本プラグインは gate しません。 codex review は実行不要です。\n' "$BRANCH" >&2
    exit 0
    ;;
esac

BASE=$(detect_base_branch) || fail "default branch を検出できませんでした (origin/HEAD 未設定 / origin 不在 等)。 git remote set-head origin -a 等で base を解決してください。"

# dirty 検知。dirty 状態で attestation を書くと commit 後の
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
# 取ることで `set -e` を回避しつつ exit code を捕捉する。
_diff_unstaged=0
git diff --quiet 2>/dev/null || _diff_unstaged=$?
_diff_staged=0
git diff --quiet --cached 2>/dev/null || _diff_staged=$?
if [ "$_diff_unstaged" -ge 128 ] || [ "$_diff_staged" -ge 128 ]; then
  fail "git diff --quiet が git error (exit >= 128) で失敗しました。 repo が corrupt / GIT_DIR が壊れている / 権限不足の可能性があります。 git status の出力を確認してください。"
fi
if [ "$_diff_unstaged" -ne 0 ] || [ "$_diff_staged" -ne 0 ]; then
  fail "working tree が dirty です (staged または unstaged 変更あり)。 git status で確認 → commit してから再実行してください。 \`/codex:review --scope branch\` は committed 部分のみを review するため、 dirty 状態で attestation を書くと commit 後の状態と hash 衝突を起こす経路があります。"
fi

# 空 push (レビュー対象となる変更が無い) なら review 実行不要。 これは
# block-pre-push-codex.sh も同条件で markers gate を skip する挙動と整合する (判定条件・
# fail-closed 方針・正当性は lib/diff-hash.sh ヘッダの「空 push 判定」セクションを参照)。
if is_empty_push "$BASE"; then
  # 進捗 / 完了メッセージは全て stderr に統一する (caller である Bash tool は stdout を
  # tool_response.stdout として受け取るため、 codex review の出力 vs wrapper の status を
  # 分離して扱える設計に倒す)。
  printf '[run-pre-push-codex-review] レビュー対象となる変更が無い (空 push) ため codex review は実行不要です。\n' >&2
  # pending attestation は書かない (空 push 時は block-pre-push-codex.sh が gate を skip するため不要)。
  exit 0
fi
HASH=$(compute_review_hash "$BASE") || fail "branch diff hash の計算に失敗しました。"

# codex companion path 解決
COMPANION=$(resolve_codex_companion) || fail "codex プラグインが見つかりません。 \`claude plugin install codex@openai-codex\` で導入してください (versioned cache / unversioned cache / marketplace clone のいずれにも codex-companion.mjs が見つかりませんでした。 詳細な探索 path は \`lib/codex-companion-resolver.sh\` のヘッダを参照)。"

# codex review を foreground 実行。 引数は `--wait --scope branch` を hardcode することで、
# Claude / 呼び出し側からの argument injection で background 起動になる余地を排除する。
# stdout は標準出力にそのまま流す (Claude が Bash tool の tool_response として受け取る形)。
#
# 正常完了 (exit 0) のときだけ pending attestation を書く設計。失敗時はエラーメッセージを
# 出して attestation を書かずに非ゼロ exit する (fail() が exit 1 する)。
printf '[run-pre-push-codex-review] codex companion: %s\n' "$COMPANION" >&2
printf '[run-pre-push-codex-review] running: node %s review --wait --scope branch\n' "$COMPANION" >&2

# `if !` で node の成否を直接捕捉する。 set +e / set -e の dance や exit code 変数を使わない:
# - set -e 配下では `node ...` が非ゼロ exit すると script 全体が即終了するため、 失敗時に
#   fail() でエラーメッセージを出す機会を失う
# - `if !` は `set -e` の影響を受けず、 失敗ブランチでカスタムメッセージを出せる
# - exit code の数値そのものは error 表示で使わない (失敗したという事実だけが本質) ため
#   $? を変数に保存する必要もない
if ! node "$COMPANION" review --wait --scope branch; then
  fail "codex review が失敗しました。 pending attestation は書きません。 上の output を確認して再実行してください。"
fi

# review 対象 hash を pending attestation として atomic write する。final marker への昇格は
# codex-reviewer の parent-safe report 完了後に auto-mark.sh が行う。
ATTESTATION_TMP="${PENDING_PATH}.tmp.$$"
umask 077
printf '%s' "$HASH" > "$ATTESTATION_TMP" || fail "codex pending attestation の一時ファイル書き込みに失敗しました。"
mv "$ATTESTATION_TMP" "$PENDING_PATH" || fail "codex pending attestation の atomic rename に失敗しました。"
ATTESTATION_TMP=""
printf '[run-pre-push-codex-review] codex pending attestation を更新しました: %s\n' "$PENDING_PATH" >&2

exit 0
