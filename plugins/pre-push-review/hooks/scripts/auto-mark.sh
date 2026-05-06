#!/bin/bash
# auto-mark.sh
# /simplify と /codex:review --wait --scope branch の実行完了を PostToolUse で検知し、
# 対応するレビューマーカーとループカウンタを更新する。
#
# 検知対象:
#   - Skill tool で `simplify` skill が完了した瞬間 → simplified マーカー
#   - Bash tool で `codex-companion.mjs review --scope branch` が完了した瞬間
#     → codex-reviewed マーカー + ループカウンタ +1
#
# /codex:review は **--scope branch のみ受け付ける**。--scope working-tree は
# committed 部分を review しないため PR diff レビュー保証として不十分、 --scope auto は
# dirty 時に working-tree にフォールバックするため不確実なので、いずれもマーカー更新を
# skip する。
#
# 設計意図:
#   - マーカー: 「Claude が手動で mark-reviewed を呼ぶ」方式は修正後の状態を
#     レビュー済みと偽装できる経路 (= ループが強制されない) を残す。各ツールの
#     実走を hook が捕捉しハッシュを書き込むことで、`/simplify` と `/codex:review`
#     の双方が「現在のブランチ全差分」に対して直近で走ったことを保証する。
#   - ループカウンタ: 同一ブランチで `/codex:review --wait --scope branch` が何回
#     走ったかを数える。block-pre-push.sh が閾値超過時に `/codex:adversarial-review`
#     を促す案内文を deny メッセージに追加する。表層レビューだけで収束しないループに
#     気づかせるシグナル用途。

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
# 2 つの OR ブランチの意図:
#   - `"skill"[[:space:]]*:[[:space:]]*"simplify"`: Skill tool 完了の検出。
#     hook payload の JSON 整形 (`"skill":"simplify"` / `"skill": "simplify"` 等) に
#     左右されないよう whitespace を寛容に許容する。false negative (= 本来通すべき
#     payload を弾く) はマーカー未生成 → 永久 push ブロックの致命経路になるため、
#     フィルタは寛容に倒す (false positive は jq 後段の SKILL_NAME 一致判定で
#     正しく弾かれるので無害)。
#   - `codex-companion`: Bash tool での codex review companion 起動の粗検出。
#     Bash 分岐側に `^node` + companion path + `review` サブコマンドの厳密な
#     後段検証があるため、ここは単純 substring で十分 (false positive は後段で弾かれる)。
PRECHECK_RE='"skill"[[:space:]]*:[[:space:]]*"simplify"|codex-companion'
if ! [[ "$INPUT" =~ $PRECHECK_RE ]]; then
  exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty')

# ループカウンタを更新するか (Bash 分岐 = codex review --scope branch 完了時のみ true)。
INCREMENT_LOOP_COUNTER=0

case "$TOOL_NAME" in
  Skill)
    SKILL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_input.skill // empty')
    # `/simplify` のみ対象。namespace 付き skill (例: pr-review-toolkit:code-simplifier)
    # は別物として扱い、本プラグインのマーカーは更新しない。
    if [ "$SKILL_NAME" != "simplify" ]; then
      exit 0
    fi
    # Skill tool の PostToolUse は `Launching skill: simplify` を返した瞬間 (= skill body
    # 実行 **前**) に発火する。この timing でマーカーを書くことで、launch 時点の差分ハッシュ
    # (= skill body が見た state) を記録する。simplify が edits を起こせば current hash は
    # launch 時点と異なる値になり、block-pre-push.sh の比較で marker stale → DENY となる
    # ため、Claude は **修正後の state で再度 /simplify** を呼ぶ必要が生じる (loop 強制)。
    MARKER_FN=simplified_marker_path
    ;;
  Bash)
    COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
    [ -n "$COMMAND" ] || exit 0
    # codex プラグインの review companion 起動を検出する。
    # 公式 codex プラグインは `node "<root>/scripts/codex-companion.mjs" review ...`
    # 形式で起動するため、コマンド先頭が `node` であること **かつ** companion path に
    # `review` サブコマンドが続いていることを両方要求する。先頭 `node` 制約により、
    # `echo codex-companion.mjs review` / `grep ... codex-companion.mjs review` のような
    # 偶発的 substring 一致でマーカーが書かれる経路を防ぐ。
    #
    # `node` の前に `FOO=bar BAZ=qux node ...` のような env-prefix が付くケースもあり得る
    # ため、`NAME=value` 形式の代入トークンを 0 回以上許容する。
    if ! printf '%s' "$COMMAND" \
      | grep -qE '^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*node([[:space:]]|$)'; then
      exit 0
    fi
    if ! printf '%s' "$COMMAND" \
      | grep -qE 'codex-companion\.m[jt]s"?[[:space:]]+review([[:space:]]|$)'; then
      exit 0
    fi
    # **--scope branch の明示要求**: pre-push-review は PR diff (= 委ねた branch の commit
    # 列) のレビューを保証する目的なので、 --scope working-tree (= staged+unstaged のみ
    # review、committed 部分を見ない) や --scope auto (= dirty 時に working-tree にフォール
    # バックする) ではマーカーを更新しない。Claude には deny メッセージで明示的に
    # `--scope branch` を指示しているため、未指定なら hook 側で markers を黙って更新せず、
    # 次回 push 試行で再 deny にして loop を継続させる。
    # `--scope branch` / `--scope=branch` の両形式を許容。 Skill 経由の codex companion 呼び
    # 出しは args を `"--wait --scope branch"` のように quote で括られた単一文字列で渡すため、
    # branch の直後が `"` / `'` / `[[:space:]]` / 行末いずれでも match する必要がある。
    # `branch` の後ろに英数字 (= prefix bypass `branchX` 防止) でない文字または行末を要求する。
    if ! printf '%s' "$COMMAND" \
      | grep -qE -- '--scope[[:space:]=]+branch([^A-Za-z0-9]|$)'; then
      exit 0
    fi
    # `run_in_background: true` 起動は PostToolUse 発火時点で review が完了して
    # いないため auto-mark の対象外とする。pre-push-review コンテキストでは
    # `--wait` のみ許容する設計 (block-pre-push.sh 側で deny メッセージに記載)。
    RUN_IN_BG=$(printf '%s' "$INPUT" | jq -r '.tool_input.run_in_background // false')
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
    INCREMENT_LOOP_COUNTER=1
    ;;
  *)
    exit 0
    ;;
esac

# ツール実行が失敗 / 中断した場合はレビューが完遂していないためマーカーを更新しない
# (失敗した review / 失敗した simplify でマーカーを書くと、その後別の tool の成功と
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/diff-hash.sh
source "$SCRIPT_DIR/lib/diff-hash.sh"
# shellcheck source=lib/loop-counter.sh
source "$SCRIPT_DIR/lib/loop-counter.sh"
# shellcheck source=lib/markers.sh
source "$SCRIPT_DIR/lib/markers.sh"

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

# /codex:review --wait --scope branch の完了でループカウンタを +1。block-pre-push.sh が
# 閾値到達時に adversarial review の案内文を deny メッセージに追加する。カウンタは
# <git-dir> 配下に置かれるためリポジトリ単位で共有される (ブランチ単位ではない)。
# push 成功時にマーカーと一緒にリセットされるため、push を通せば 0 起算に戻る。
if [ "$INCREMENT_LOOP_COUNTER" -eq 1 ]; then
  CURRENT=$(read_loop_count "$GIT_DIR")
  write_loop_count "$GIT_DIR" "$((CURRENT + 1))"
fi
