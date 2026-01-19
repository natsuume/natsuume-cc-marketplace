#!/bin/bash
# block-default-branch-push.sh
# デフォルトブランチ（master/main）への直接 push をブロックするフック

# stdin から JSON を読み取る
INPUT=$(cat)

# tool_input.command を抽出
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# コマンドが空なら何もしない
if [ -z "$COMMAND" ]; then
  exit 0
fi

# git push コマンドかどうか判定（違えば即座に終了）
if ! echo "$COMMAND" | grep -qE '^\s*git\s+push'; then
  exit 0
fi

# master または main へのプッシュかどうか判定
# パターン: git push origin master, git push origin main, git push --force origin master など
if echo "$COMMAND" | grep -qE 'git\s+push\s+.*\s+(master|main)(\s|$)'; then
  # デフォルトブランチへの push をブロック
  jq -n '{
    "hookSpecificOutput": {
      "hookEventName": "PreToolUse",
      "permissionDecision": "deny",
      "permissionDecisionReason": "デフォルトブランチ（master/main）への直接 push は禁止されています。working branch を作成し、そこから PR を作成してマージしてください。"
    }
  }'
  exit 0
fi

# デフォルトブランチ以外への push は許可
exit 0
