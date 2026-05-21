#!/bin/bash
# auto-mark.sh
# /code-review / /codex:review --wait --scope branch / pre-push-review:security-reviewer
# subagent / /security-review 標準 skill (主 session 直接呼び出しのみ) の実行完了を
# PostToolUse で検知し、対応するレビューマーカーを更新する。
#
# 検知対象:
#   - Skill tool で `code-review` skill (旧名 `simplify` も後方互換で受け付ける) が
#     完了した瞬間 → code-reviewed マーカー (launch 時点ハッシュ)。 Claude Code v2.1.146
#     で bundled skill `/simplify` が `/code-review` にリネームされたため両方を検知する。
#   - Skill tool で `security-review` skill が完了した瞬間 → security-reviewed マーカー
#     (launch 時点ハッシュ。 主 session が直接呼んだ場合のみ動く後方互換パス)
#   - Bash tool で `codex-companion.mjs review --scope branch` が完了した瞬間
#     → codex-reviewed マーカー
#   - Agent / Task tool で `pre-push-review:security-reviewer` subagent が完了した瞬間
#     → security-reviewed マーカー (subagent 完了時点ハッシュ。 推奨パス)
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
# /codex:review は **--scope branch のみ受け付ける**。--scope working-tree は
# committed 部分を review しないため PR diff レビュー保証として不十分、 --scope auto は
# dirty 時に working-tree にフォールバックするため不確実なので、いずれもマーカー更新を
# skip する。
#
# 設計意図:
#   - マーカー: 「Claude が手動で mark-reviewed を呼ぶ」方式は修正後の状態を
#     レビュー済みと偽装できる経路 (= ループが強制されない) を残す。各ツールの
#     実走を hook が捕捉しハッシュを書き込むことで、3 つすべてが「現在のブランチ全差分」
#     に対して直近で走ったことを保証する。
#   - 「security-reviewer subagent の完了」を Task 終了で検知するのは、 subagent が
#     実際にレビュー本体を完了させたタイミングを捉えるため。 launch 時点ではなく
#     完了時点でマーカーを書くことで、 subagent が途中で失敗した場合に marker が
#     書かれない (= push gate がそのまま deny) を担保する。

# 予期せぬエラー時の診断 handler (ノンブロッキング)。
#
# 本 hook の通常パスは「対象ツールでない → exit 0」「対象ツールだがエラー / 中断 → exit 0」
# 「対象ツール完了 → marker 書き込み → exit 0」 のいずれも exit 0 で抜ける silent skip 設計。
# 既存 silent skip では下記 trap は no-op になる (exit code 0)。
#
# 一方、 jq の引数バグ / 外部コマンドの突然死 / シェル展開の想定外失敗 / signal などで
# script が **非ゼロで終了** すると、 marker 書き込みが skip されて Claude / ユーザは hook
# 不調に気付かないまま loop が空回りする (= 後で `git push` 時に block-pre-push.sh が
# 「marker 未生成」 で deny し、 「review を完走したのに通らない」 混乱になる) 経路がある。
# EXIT trap で `$?` を観測し、 非ゼロ終了を stderr に出してユーザに気付かせる。 trap 自身は
# `exit 0` を返さず元の exit code を保つため、 push 動作はノンブロッキングのまま。
_pre_push_review_exit_handler() {
  local exit_code=$?
  if [ "$exit_code" -ne 0 ]; then
    printf '[pre-push-review/auto-mark] 予期せぬエラーで hook が exit %s で終了しました。\n' "$exit_code" >&2
    printf '[pre-push-review/auto-mark] レビュー marker の書き込みが skip された可能性があり、 次の `git push` 時に block-pre-push.sh が「marker 未生成」 で deny する経路があります。 marketplace https://github.com/natsuume/natsuume-cc-marketplace に hook 実装の bug として報告してください。\n' >&2
  fi
}
trap _pre_push_review_exit_handler EXIT

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
# 3 つの top-level OR ブランチの意図:
#   - `"skill"[[:space:]]*:[[:space:]]*"(code-review|simplify|security-review)"`: Skill tool で
#     `code-review` (v2.1.146 以降の新名) / `simplify` (v2.1.145 以下の旧名) / `security-review`
#     skill が完了したことを検出する粗フィルタ。 完全一致が必要なため末尾の `"` まで含めて
#     マッチさせ、 namespace 付き skill (`code-review:code-review` 等) は副次マッチしない。
#     **後段 case 分岐 (skill 名 → marker 関数のマッピング) と同期させること**:
#     新しい alias 追加時はここと case 文の両方を更新しないと、 PRECHECK_RE で通過するが
#     case の `*) exit 0 ;;` に落ちる silent skip が発生し marker が永久に書かれない。
#   - `"subagent_type"[[:space:]]*:[[:space:]]*"[^"]*security-reviewer"`: Agent / Task
#     tool で `pre-push-review:security-reviewer` subagent が完了したことを検出する粗
#     フィルタ。 namespace 付き形式 (`pre-push-review:security-reviewer`) と name-only
#     形式 (`security-reviewer`) の両方を許容するため `[^"]*security-reviewer` で末尾
#     match する。 後段の jq 検証で full match を確認する。
#   - `codex-companion`: Bash tool での codex review companion 起動の粗検出。
#     Bash 分岐側に `^node` + companion path + `review` サブコマンドの厳密な
#     後段検証があるため、ここは単純 substring で十分 (false positive は後段で弾かれる)。
#
# hook payload の JSON 整形 (`"skill":"code-review"` / `"skill": "code-review"` 等) に
# 左右されないよう whitespace を寛容に許容する。false negative (= 本来通すべき payload を
# 弾く) はマーカー未生成 → 永久 push ブロックの致命経路になるため、 フィルタは寛容に倒す
# (false positive は jq 後段の名前一致判定で正しく弾かれるので無害)。
PRECHECK_RE='"skill"[[:space:]]*:[[:space:]]*"(code-review|simplify|security-review)"|"subagent_type"[[:space:]]*:[[:space:]]*"[^"]*security-reviewer"|codex-companion'
if ! [[ "$INPUT" =~ $PRECHECK_RE ]]; then
  exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# lib 群を case 分岐前に source。 Bash 分岐内で `is_codex_review_invocation` /
# `normalize_line_continuations` を呼ぶため codex-review-detect.sh と cmd-parser.sh は
# case 分岐より上で読み込む必要がある。 diff-hash.sh / markers.sh は後段の marker 書き
# 込みでのみ使うが、 SCRIPT_DIR を 1 回で済ますためまとめて上に置く。
#
# cmd-parser.sh は v0.7.1 で `normalize_line_continuations` の bash 3.2 互換実装を提供する
# ようになったため、 codex-review-detect.sh から取り込んだ場合と同じ関数名で使える。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/cmd-parser.sh
source "$SCRIPT_DIR/lib/cmd-parser.sh"
# shellcheck source=lib/codex-review-detect.sh
source "$SCRIPT_DIR/lib/codex-review-detect.sh"
# shellcheck source=lib/diff-hash.sh
source "$SCRIPT_DIR/lib/diff-hash.sh"
# shellcheck source=lib/markers.sh
source "$SCRIPT_DIR/lib/markers.sh"

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty')

case "$TOOL_NAME" in
  Skill)
    SKILL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_input.skill // empty')
    # 対応する skill 名 → マーカー関数のマッピング。namespace 付き skill (例:
    # pr-review-toolkit:code-simplifier) は別物として扱い、本プラグインのマーカーは更新しない。
    #
    # Skill tool の PostToolUse は `Launching skill: <name>` を返した瞬間 (= skill body 実行
    # **前**) に発火する。この timing でマーカーを書くことで、launch 時点の差分ハッシュ
    # (= skill body が見た state) を記録する。skill body が edits を起こせば current hash は
    # launch 時点と異なる値になり、block-pre-push.sh の比較で marker stale → DENY となる
    # ため、Claude は **修正後の state で再度 skill** を呼ぶ必要が生じる (loop 強制)。
    case "$SKILL_NAME" in
      # v2.1.146 で `/simplify` が `/code-review` にリネームされたため両方を受け付ける。
      # **PRECHECK_RE (上の skill alternation) と同期させること**: 片方だけ更新すると、
      # PRECHECK_RE で通過するが case で `*) exit 0 ;;` に落ちる silent skip を作りうる。
      # マーカーファイル名は `.claude-pre-push-code-reviewed` (v0.7.0 で旧 `.claude-pre-push-simplified` から rename)。
      code-review|simplify) MARKER_FN=code_reviewed_marker_path ;;
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
  Bash)
    # command と run_in_background は jq を 2 回呼んで取得する。 `@tsv` で 1 回 jq に
    # まとめると TSV エンコードで LF が `\n` literal に変換され、 後段の line continuation
    # 正規化対象 (`\<改行>`) が失われるため、 2 回呼びのまま保つ。
    COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
    [ -n "$COMMAND" ] || exit 0
    # 行継続 `\<改行>` を **削除** して隣接 token を連結する (block-bg-codex-review.sh と
    # 同じ理由 — `\<newline>` を含む形式で `--scope branch` や codex review 検知の `review`
    # token を bypass される経路を塞ぐ)。
    COMMAND=$(normalize_line_continuations "$COMMAND")
    RUN_IN_BG=$(printf '%s' "$INPUT" | jq -r '.tool_input.run_in_background // false')
    # codex プラグインの review companion 起動を検出する。 検知ロジックは
    # lib/codex-review-detect.sh に集約しており、 block-bg-codex-review.sh と
    # 同じ関数を共有することで「marker を書く対象」 と 「block する対象」 の
    # マッチが drift しない。
    is_codex_review_invocation "$COMMAND" || exit 0
    # **--scope branch の明示要求**: pre-push-review は PR diff (= 委ねた branch の commit
    # 列) のレビューを保証する目的なので、 --scope working-tree (= staged+unstaged のみ
    # review、committed 部分を見ない) や --scope auto (= dirty 時に working-tree にフォール
    # バックする) ではマーカーを更新しない。Claude には deny メッセージで明示的に
    # `--scope branch` を指示しているため、未指定なら hook 側で markers を黙って更新せず、
    # 次回 push 試行で再 deny にして loop を継続させる。
    # `--scope branch` / `--scope=branch` を match させつつ、 `branchX` 等の prefix bypass を
    # 拒否する (英数字で続く場合は不一致)。 trailing 文字を「英数字以外 / 行末」に取る形だと
    # 引用符・空白・記号いずれの区切りも自然に許容される。
    if ! printf '%s' "$COMMAND" \
      | grep -qE -- '--scope[[:space:]=]+branch([^A-Za-z0-9]|$)'; then
      exit 0
    fi
    # `run_in_background: true` 起動は PostToolUse 発火時点で review が完了して
    # いないため auto-mark の対象外とする。 こちらは silent skip するだけだが、
    # PreToolUse の block-bg-codex-review.sh が起動自体を deny するため、 通常は
    # ここに到達しない (defense-in-depth として残す)。
    if [ "$RUN_IN_BG" = "true" ]; then
      exit 0
    fi
    # **dirty 状態での codex マーカー書き込みを禁止**: /codex:review --scope branch は
    # committed 部分のみを review する。 working tree が dirty (staged または unstaged 変更
    # あり) のときに marker を書くと、ハッシュ式が「committed + uncommitted」を連結する
    # 都合上、 後で uncommitted を commit した状態のハッシュと一致してしまうケース
    # (例: 新規ブランチで `git diff --cached` の内容を commit すると `git diff origin/master...HEAD`
    # と byte-for-byte 同じになる) がある。 つまり「review 時に committed=空、uncommitted=D」
    # と「commit 後に committed=D、uncommitted=空」が同じハッシュ値になり、未レビューな commit
    # が markers の整合性チェックを素通りする経路ができる。
    # 対策: dirty な状態では codex marker を書かない。Claude は commit してから再 review する
    # 必要があり、その時の marker は committed 部分のみのハッシュになるため上記混同が起きない。
    if ! git diff --quiet 2>/dev/null || ! git diff --quiet --cached 2>/dev/null; then
      exit 0
    fi
    MARKER_FN=codex_marker_path
    ;;
  *)
    exit 0
    ;;
esac

# ツール実行が失敗 / 中断した場合はレビューが完遂していないためマーカーを更新しない
# (失敗した review / 失敗した code-review でマーカーを書くと、その後別の tool の成功と
# 組み合わさって push が通ってしまう抜け穴になる)。Skill / Bash 両分岐に共通。
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
