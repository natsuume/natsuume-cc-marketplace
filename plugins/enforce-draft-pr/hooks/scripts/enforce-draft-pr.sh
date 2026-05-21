#!/bin/bash
# enforce-draft-pr.sh
# PR を draft として作成することを強制するフック

# stdin から JSON を読み取る
INPUT=$(cat)

# tool_input.command を抽出
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# コマンドが空なら何もしない
if [ -z "$COMMAND" ]; then
  exit 0
fi

# bash の `$(...)` trailing-LF trim で消えた `\<LF>` を復元 (= line continuation を
# 含む `gh pr create\<LF>...` 入力で末尾 `\` が `--draft` 注入後にも残って updatedInput
# の command が壊れる cosmetic 影響を避ける)。 詳細は pre-push-review の cmd-parser.sh
# 「末尾 `\<LF>` 復元の caller 側 inline パターン」 セクション参照。
case "$COMMAND" in *\\) COMMAND="${COMMAND}"$'\n' ;; esac

# gh pr create コマンドかどうか判定（違えば即座に終了）
if ! echo "$COMMAND" | grep -qE '^\s*gh\s+pr\s+create'; then
  exit 0
fi

# 既に --draft フラグがあれば何もしない
if echo "$COMMAND" | grep -qE '\s--draft(\s|$)'; then
  exit 0
fi

# --draft フラグを gh pr create の直後に追加
UPDATED_COMMAND=$(echo "$COMMAND" | sed 's/gh pr create/gh pr create --draft/')

# updatedInput を含むレスポンスを返す
jq -n --arg cmd "$UPDATED_COMMAND" '{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "permissionDecisionReason": "PR は draft として作成されます",
    "updatedInput": {
      "command": $cmd
    }
  }
}'
