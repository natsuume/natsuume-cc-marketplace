#!/bin/bash
# block-ignore-lint-comment.sh
#
# Edit/Write/MultiEdit/apply_patch が「新規挿入する」内容に linter/formatter の ignore
# コメントが含まれていたらツール実行を deny する。既に old_string や既存
# ファイルに含まれていた ignore コメントを保持するだけの編集は許可する
# (検出は detect-new-ignores.py で多重集合差分により実装)。
#
# 対象は eslint / prettier / ruff の代表的な ignore 構文。
#
# policy: fail-open (defense-in-depth)
# detect-new-ignores.py が「新規 ignore あり (exit 2)」を返したときのみ deny し、
# それ以外 (exit 0 = 検出なし / exit 1 = 入力不正等) は許可で抜ける。これは
# block-commit-lint.sh (fail-closed) と意図的に異なる (#67)。理由:
#   1. jq / python3 不在は下で先に gate するため、残る exit 1 は「stdin が有効 JSON
#      でない」場合に限られ、stdin は Claude Code 生成 payload なので現実の破損経路は乏しい。
#   2. python3 クラッシュで誤許可するのは「元々検出できなかった編集」であり新規 bypass を
#      生まない。
#   3. 本 hook は ignore バイパス対策の defense-in-depth の一枚で、commit 時に
#      block-commit-lint.sh が再 lint する二重防御がある。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi
if ! command -v python3 >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)
TOOL_NAME=$(extract_tool_name "$INPUT")

case "$TOOL_NAME" in
  Write|Edit|MultiEdit|apply_patch) ;;
  *) exit 0 ;;
esac

# 新規挿入された ignore コメントだけを Python ヘルパーで列挙する。
# exit code:
#   0  追加なし  → そのまま許可
#   2  追加あり  → DETECT_OUTPUT に検出箇所、deny する
#   その他      → 入力不正等。安全側で許可してスキップ
DETECT_OUTPUT=$(printf '%s' "$INPUT" | python3 "$AUTO_LINT_CHECK_LIB_DIR/detect-new-ignores.py")
DETECT_RC=$?

if [ "$DETECT_RC" = "2" ]; then
  REASON=$(printf '%s\n' \
    "lint/formatter の ignore コメント挿入は禁止されています。" \
    "lint 警告は本体のコードを修正することで解決してください。" \
    "" \
    "新規挿入された ignore コメント (最初の3件):" \
    "$DETECT_OUTPUT")
  emit_deny "$REASON"
fi

exit 0
