#!/bin/bash
# nudge-pr-review.sh
# `gh pr create` 成功直後に Claude へ `/code-review:code-review` 実行を促す
# PostToolUse フック。URL は tool_response から抽出して context に埋め込む。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# 大半の Bash 呼び出しは無関係。粗フィルタで早期離脱する。
case "$INPUT" in
  *"gh"*"pr"*"create"*) ;;
  *) exit 0 ;;
esac

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
if [ -z "$COMMAND" ]; then
  exit 0
fi

# コマンド先頭が `gh ... pr create` のときだけ拾う。`echo gh pr create` のような
# 文字列としての参照や、`# gh pr create` のコメント行で誤って発火しないため。
# `gh -R owner/repo pr create` のような global option を伴う形式は許容する。
if ! printf '%s' "$COMMAND" \
  | grep -qE '^[[:space:]]*gh([[:space:]]+[^[:space:];&|]+)*[[:space:]]+pr[[:space:]]+create([[:space:]]|$)'; then
  exit 0
fi

# 失敗した tool 実行 (例: PR が既に存在する旨を stderr に出すケース) では URL があっても
# 通知しない。is_error / interrupted を確認し、エラーなら skip する。
IS_ERROR=$(printf '%s' "$INPUT" | jq -r '.tool_response.is_error // .tool_response.isError // false')
INTERRUPTED=$(printf '%s' "$INPUT" | jq -r '.tool_response.interrupted // false')
if [ "$IS_ERROR" = "true" ] || [ "$INTERRUPTED" = "true" ]; then
  exit 0
fi

# 成功時の URL は stdout に出る (stderr には "already exists" 等の URL が混じり得るので除外)。
RESPONSE=$(printf '%s' "$INPUT" \
  | jq -r '[.tool_response.output, .tool_response.stdout] | map(select(. != null)) | join("\n")')
PR_URL=$(printf '%s' "$RESPONSE" | grep -oE 'https://github\.com/[^[:space:]]+/pull/[0-9]+' | head -n1)

if [ -z "$PR_URL" ]; then
  exit 0
fi

CONTEXT=$(cat <<EOF
PR を作成しました: $PR_URL

このリポジトリでは PR 作成直後に \`/code-review:code-review $PR_URL\` を実行してコードレビューする運用です。レビューで指摘があれば対応してから ready マーク (\`gh pr ready\`) に進めてください。

**注意**: \`/code-review:code-review\` skill のコメントテンプレートは英語でハードコードされています。本リポジトリは グローバル CLAUDE.md の「やり取りは日本語で行う」方針に従うため、PR へ投稿する直前に下記の対応訳に **すべて翻訳** してから \`gh pr comment\` してください。"🤖 Generated with [Claude Code]" の Trailer 行はそのまま残します。

  - \`### Code review\` → \`### コードレビュー\`
  - \`Found N issues:\` → \`N 件の指摘が見つかりました:\`
  - \`No issues found. Checked for bugs and CLAUDE.md compliance.\` → \`指摘なし。バグおよび CLAUDE.md 準拠を確認しました。\`
  - \`If this code review was useful, please react with 👍. Otherwise, react with 👎.\` → \`このコードレビューが役に立った場合は 👍、そうでなければ 👎 でリアクションしてください。\`
EOF
)

jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    additionalContext: $ctx
  }
}'
