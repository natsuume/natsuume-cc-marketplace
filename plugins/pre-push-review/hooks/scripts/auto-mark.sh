#!/bin/bash
# auto-mark.sh
# /code-review skill / pre-push-review:security-reviewer subagent /
# /security-review 標準 skill (主 session 直接呼び出しのみ) の実行完了を PostToolUse で
# 検知し、対応するレビューマーカーを更新する。
#
# policy: fail-open (PostToolUse / 正常完了後の marker 書き込み)
#   git / 環境の失敗は 2>/dev/null + exit 0 で silent skip する (正常完了後の処理を
#   阻害しない設計判断)。対照: PreToolUse 側の block-pre-push.sh は fail-closed。
#   同じ失敗が Pre=deny / Post=skip という非対称は意図的 (#90)。
#
# 検知対象 (3 マーカー構成 / v2.0.0):
#   - Skill `code-review` 完了 → code-reviewed marker (launch 時点ハッシュ)
#   - Skill `security-review` 完了 → security-reviewed marker (後方互換パス)
#   - Agent/Task で `pre-push-review:security-reviewer` 完了 → security-reviewed marker (推奨パス)
#
# codex review は wrapper script (run-codex-review.sh) が自身で marker を書く設計のため
# 本 hook の対象外。 v1.x 経緯は markers.sh / README 参照。
#
# **`/security-review` 標準 skill を残しつつ subagent も併用する理由**:
#   推奨は subagent 経由。 主 session から直接 `/security-review` を呼ぶと
#   「Your final reply must contain the markdown report and nothing else.」で
#   turn が終了して後続フロー (`git push`) が止まるため、 block-pre-push.sh の
#   deny メッセージは subagent 経由を案内している。 ただし「直接呼ばれた場合は
#   マーカーを書かない」 設計だと、 誤ってまたは旧運用で直接呼んだとき marker が
#   永久に更新されない user-hostile な経路が残るため、 直接呼び出しも検知して
#   marker を書く後方互換パスを維持する。
#
#   silent-pass のリスク (subagent が標準 skill を invoke → sub-task が動かず失敗
#   → でも marker は書かれる) は、 subagent の tools から **`Skill` を外す** ことで
#   構造的に塞いでいる。 subagent は標準 skill を invoke できないため、 直接
#   呼び出しの検知パスは subagent 経路と衝突しない。
#
# 設計意図:
#   - マーカー: 「Claude が手動で mark-reviewed を呼ぶ」方式は修正後の状態を
#     レビュー済みと偽装できる経路 (= ループが強制されない) を残す。各ツールの
#     実走を hook が捕捉しハッシュを書き込むことで、 すべてが「現在のブランチ全差分」
#     に対して直近で走ったことを保証する。 codex review だけは wrapper が直接書く
#     例外設計 (= Claude が wrapper の中身を編集しない限り偽装できない範囲)。
#   - 「security-reviewer subagent の完了」を Task 終了で検知するのは、 subagent が
#     実際にレビュー本体を完了させたタイミングを捉えるため。 launch 時点ではなく
#     完了時点でマーカーを書くことで、 subagent が途中で失敗した場合に marker が
#     書かれない (= push gate がそのまま deny) を担保する。
#
# レビュー対象 repo の前提 (block-pre-push.sh との非対称について):
#   本 hook は dirty 判定 / base 検出 / branch / ハッシュ計算 / marker パスを **すべて
#   PostToolUse 発火時の cwd** で行う (= 「いま居る repo を review した」と記録する)。
#   一方 block-pre-push.sh は `git push` コマンド引数から実 push target を解決し、 その
#   target cwd でハッシュ・marker パスを決める。 この非対称は意図的かつ安全:
#     - review は通常メイン session の cwd (= push 対象 repo) で実行されるため、
#       両者の cwd は一致し marker は正しく照合される。
#     - もし review を repo A の cwd で行い、 別の repo B を target-override
#       (`git -C B push` / `cd B && git push`) で push した場合、 marker は A に書かれ
#       push gate は B のハッシュを要求するため **hash 不一致で deny** に倒れる
#       (= fail-closed)。 「push する repo をその cwd で review し直す」という正しい挙動を
#       強制するだけで、 未レビュー push を通す bypass にはならない。
#   したがって運用前提は「**review は push 対象 repo の cwd で実行する**」。 target-override
#   push を多用する場合はこの前提に留意する (auto-mark を push 引数依存にしないのは、 本 hook
#   の発火契機が review 完了であって push コマンドではなく、 解決すべき push target が
#   存在しないため)。

# 予期せぬエラー時の診断 trap を install (実装は lib/exit-trap.sh)。
# 本 hook の通常パスは「対象ツールでない → exit 0」「対象ツールだがエラー / 中断 → exit 0」
# 「対象ツール完了 → marker 書き込み → exit 0」 のいずれも exit 0 で抜ける silent skip 設計。
# 想定外の非ゼロ終了が発生した場合のみ stderr に診断ログを出してユーザに知らせる。
_PRE_PUSH_REVIEW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/exit-trap.sh
source "$_PRE_PUSH_REVIEW_SCRIPT_DIR/lib/exit-trap.sh"
install_exit_trap "auto-mark" "レビュー marker の書き込みが skip された可能性があり、 次の \`git push\` 時に block-pre-push.sh が「marker 未生成」 で deny する経路があります。"

INPUT=$(cat)

# 本 hook は hooks.json で matcher: "*" (wildcard) を指定しており、すべての tool 完了で
# 発火する。tool 名に依存させない理由は、Claude Code 公式ドキュメントで Skill matcher の
# 挙動が tool 名リストに明示されておらず、特定の tool 名 (`Skill` など) を信頼できる
# matcher にできないため。代わりに本スクリプト側で bash 内蔵の正規表現マッチを唯一の
# ゲートにする。
#
# `grep` を呼ぶと hot path 上で毎回 fork が走るため、bash の `[[ =~ ]]` を使って
# subprocess を立てずに済ませる (Read/Edit/Write 等の対象外 tool 完了でも本 hook が
# 呼ばれるが、ここで即離脱できればフォーク無しで通り抜けられる)。
#
# **コスト注記 (#90)**: matcher "*" で全 tool 完了に発火するため、巨大な tool_response が
# INPUT に乗ると毎回 INPUT サイズに比例した ERE 評価が走る (fork は無いが in-process コスト)。
# 現状の規模では無視できるが、もし問題化したら ERE の前に `case "$INPUT" in *'"skill"'*|
# *'"subagent_type"'*) ;; *) exit 0 ;; esac` の substring pre-filter で大半を弾ける
# (この 2 substring は下記 PRECHECK_RE の全 match の superset なので false negative を
# 生まない)。早期離脱ロジックを変えるリスクを避け、現状はコスト注記に留める。
#
# PRECHECK_RE は 2 系統の粗フィルタを OR で繋ぐ:
#   - skill 名 (`code-review` / `security-review`) の完全一致
#   - subagent_type が `security-reviewer` 末尾一致 (namespace 付き / name-only 両方許容)
# **後段 case 文と必ず同期させること** (片方のみ更新だと silent skip 経路ができる)。
#
# hook payload の JSON 整形 (`"skill":"code-review"` / `"skill": "code-review"` 等) に
# 左右されないよう whitespace を寛容に許容する。false negative (= 本来通すべき payload を
# 弾く) はマーカー未生成 → 永久 push ブロックの致命経路になるため、 フィルタは寛容に倒す
# (false positive は jq 後段の名前一致判定で正しく弾かれるので無害)。
PRECHECK_RE='"skill"[[:space:]]*:[[:space:]]*"(code-review|security-review)"|"subagent_type"[[:space:]]*:[[:space:]]*"[^"]*security-reviewer"'
if ! [[ "$INPUT" =~ $PRECHECK_RE ]]; then
  exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# lib 群を case 分岐前に source。 marker 書き込みで diff-hash.sh / markers.sh のみ必要。
SCRIPT_DIR="$_PRE_PUSH_REVIEW_SCRIPT_DIR"
# shellcheck source=lib/diff-hash.sh
source "$SCRIPT_DIR/lib/diff-hash.sh"
# shellcheck source=lib/markers.sh
source "$SCRIPT_DIR/lib/markers.sh"

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty')

case "$TOOL_NAME" in
  Skill)
    SKILL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_input.skill // empty')
    # 対応する skill 名 → マーカー関数のマッピング。 namespace 付き skill (例:
    # pr-review-toolkit:code-reviewer) は別物として扱い、 本プラグインのマーカーは更新しない。
    #
    # Skill tool の PostToolUse は `Launching skill: <name>` を返した瞬間 (= skill body 実行
    # **前**) に発火する。 この timing でマーカーを書くことで、 launch 時点の差分ハッシュ
    # (= skill body が見た state) を記録する。 修正が後で起こる場合は次の編集で current hash
    # が launch 時点と異なる値になり、 block-pre-push.sh の比較で marker stale → DENY と
    # なって再走を強制できる。
    case "$SKILL_NAME" in
      # **PRECHECK_RE (上の skill alternation) と同期させること**: 片方だけ更新すると、
      # PRECHECK_RE で通過するが case で `*) exit 0 ;;` に落ちる silent skip を作りうる。
      code-review) MARKER_FN=code_reviewed_marker_path ;;
      security-review) MARKER_FN=security_marker_path ;;
      *) exit 0 ;;
    esac
    ;;
  Agent|Task)
    # Claude Code の Agent tool は内部的に "Agent" / "Task" 2 つの名前で公開されている。
    # PostToolUse の tool_name はそのどちらかが入りうるので両方 match させる。
    SUBAGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.tool_input.subagent_type // empty')
    # subagent 名は namespace 付き (`pre-push-review:security-reviewer`) と name-only
    # (`security-reviewer`) のどちらでも受け付ける。 同名 subagent が別 plugin に存在
    # して衝突する可能性は低いが、 後者を許容することで運用ミスへの寛容度を上げる。
    case "$SUBAGENT_TYPE" in
      pre-push-review:security-reviewer|security-reviewer)
        MARKER_FN=security_marker_path ;;
      *)
        exit 0 ;;
    esac
    ;;
  *)
    exit 0
    ;;
esac

# ツール実行が失敗 / 中断した場合はレビューが完遂していないためマーカーを更新しない
# (失敗した review / 失敗した code-review でマーカーを書くと、その後別の tool の成功と
# 組み合わさって push が通ってしまう抜け穴になる)。Skill / Agent / Task 全分岐に共通。
{ read -r IS_ERROR; read -r INTERRUPTED; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.tool_response.is_error // .tool_response.isError // false),
    (.tool_response.interrupted // false)
  '
)
if [ "$IS_ERROR" = "true" ] || [ "$INTERRUPTED" = "true" ]; then
  exit 0
fi

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0

# default branch が検出できない場合はマーカー更新を skip (block-pre-push.sh も同条件で
# pass-through するため、整合性が保たれる)。
BASE=$(detect_base_branch) || exit 0

# detached HEAD などで現在ブランチが取れない場合も skip。
BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null) || exit 0

# default branch (master/main) では gate しない (= block-pre-push.sh も skip するため、
# こちらでも markers を書く必要がない)。
case "$BRANCH" in
  master|main) exit 0 ;;
esac

# branch diff 計算失敗時は markers を書かない (block-pre-push.sh は失敗を deny に倒すため、
# こちらでも書かないことで整合性を保つ。中途半端なハッシュ値で marker 書き込みを許すと、
# 後続 push で誤判定の元になる)。
if ! HASH=$(compute_review_hash "$BASE"); then
  exit 0
fi
printf '%s' "$HASH" > "$("$MARKER_FN" "$GIT_DIR")"
