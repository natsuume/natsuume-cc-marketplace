#!/bin/bash
# auto-mark.sh
# /simplify と /codex:review --wait の実行完了を PostToolUse で検知し、
# 対応するレビューマーカーとループカウンタを更新する。
#
# 検知対象:
#   - Skill tool で `simplify` skill が完了した瞬間 → simplified マーカー
#   - Bash tool で `codex-companion.mjs review` が完了した瞬間 → codex-reviewed マーカー
#                                                                + ループカウンタ +1
#
# 設計意図:
#   - マーカー: 「Claude が手動で mark-reviewed を呼ぶ」方式は修正後の状態を
#     レビュー済みと偽装できる経路 (= ループが強制されない) を残す。各ツールの
#     実走を hook が捕捉しハッシュを書き込むことで、`/simplify` と `/codex:review`
#     の双方が「現在の staged+unstaged 差分」に対して直近で走ったことを保証する。
#   - ループカウンタ: 同一ブランチで `/codex:review --wait` が何回走ったかを数える。
#     block-pre-commit.sh が閾値超過時に `/codex:adversarial-review` (実装方針への
#     批判的レビュー) を促す案内文を deny メッセージに追加する。`/codex:review` の
#     表層レビューだけで収束しないループに気づかせるシグナル用途。
#     `/simplify` 側はカウントしない (Skill PostToolUse は launch 時点で発火するため
#     完了の signal としては不正確で、cooperative にカウントを膨らませる経路になる)。

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
#     payload を弾く) はマーカー未生成 → 永久 commit ブロックの致命経路になるため、
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

# ループカウンタを更新するか (Bash 分岐 = codex review 完了時のみ true)。
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
    # launch 時点と異なる値になり、block-pre-commit.sh の比較で marker stale → DENY となる
    # ため、Claude は **修正後の state で再度 /simplify** を呼ぶ必要が生じる (loop 強制)。
    # Stop event で finalize する設計も検討したが、その場合 simplify 後の codex review
    # 修正も「simplified 済み」と誤判定される (loop discipline が崩れる) ため採用しない。
    MARKER_NAME=".claude-pre-commit-simplified"
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
    # ため、`NAME=value` 形式の代入トークンを 0 回以上許容する。 false negative
    # (= 本来通すべき env-prefix 付き codex 起動を弾く) は永久 commit ブロックの致命経路に
    # なるため、env-prefix は寛容に許容する (false positive は後段の companion path
    # チェックで弾かれるので無害)。
    if ! printf '%s' "$COMMAND" \
      | grep -qE '^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*node([[:space:]]|$)'; then
      exit 0
    fi
    if ! printf '%s' "$COMMAND" \
      | grep -qE 'codex-companion\.m[jt]s"?[[:space:]]+review([[:space:]]|$)'; then
      exit 0
    fi
    # `run_in_background: true` 起動は PostToolUse 発火時点で review が完了して
    # いないため auto-mark の対象外とする。pre-commit-review コンテキストでは
    # `--wait` のみ許容する設計 (block-pre-commit.sh 側で deny メッセージに記載)。
    RUN_IN_BG=$(printf '%s' "$INPUT" | jq -r '.tool_input.run_in_background // false')
    if [ "$RUN_IN_BG" = "true" ]; then
      exit 0
    fi
    MARKER_NAME=".claude-pre-commit-codex-reviewed"
    INCREMENT_LOOP_COUNTER=1
    ;;
  *)
    exit 0
    ;;
esac

# ツール実行が失敗 / 中断した場合はレビューが完遂していないためマーカーを更新しない
# (失敗した review / 失敗した simplify でマーカーを書くと、その後別の tool の成功と
# 組み合わさって commit が通ってしまう抜け穴になる)。Skill / Bash 両分岐に共通。
# `is_error // .isError`: hook payload は snake_case が標準だが、Claude Code 側の
# 実装揺らぎに備えて camelCase も defensive にフォールバックする。
# 1 回の jq invocation で is_error / interrupted を同時取得する (jq 起動コスト節約)。
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

printf '%s' "$(compute_review_hash)" > "$GIT_DIR/$MARKER_NAME"

# /codex:review --wait の完了でループカウンタを +1。block-pre-commit.sh が閾値超過時に
# adversarial review の案内文を deny メッセージに追加する。commit 成功時にカウンタも
# まとめて削除されるため、ブランチをまたいだ持ち越しは起きない。
if [ "$INCREMENT_LOOP_COUNTER" -eq 1 ]; then
  CURRENT=$(read_loop_count "$GIT_DIR")
  write_loop_count "$GIT_DIR" "$((CURRENT + 1))"
fi
