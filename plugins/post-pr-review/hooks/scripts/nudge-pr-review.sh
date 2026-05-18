#!/bin/bash
# nudge-pr-review.sh
# `gh pr create` 成功直後に Claude へ `/codex:adversarial-review` 実行を促す
# PostToolUse フック。URL は tool_response から抽出して context に文脈情報として埋め込む。
# `/codex:adversarial-review` 自体は git state を見るため PR URL を引数に取らない。

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
# 通知しない。is_error / interrupted を 1 回の jq invocation で同時取得する。
{ read -r IS_ERROR; read -r INTERRUPTED; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.tool_response.is_error // .tool_response.isError // false),
    (.tool_response.interrupted // false)
  '
)
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

このリポジトリでは PR 作成直後に \`/codex:adversarial-review --wait --scope branch\` を Skill tool で実行し、現在のブランチに対する **批判的レビュー** (実装方針・設計選択・トレードオフ・前提条件への challenge) を取得する運用です。read-only のコードレビューではなく、「採用しているアプローチ自体が妥当か」を問い直すレビューです。

レビュー結果に従って:
  - 設計方針や実装アプローチに対する根本的な指摘があれば、修正方針を検討して必要なら作業ブランチに反映する
  - 影響が大きい指摘 (アーキテクチャレベルの再考が必要等) は人間判断を仰ぐ
  - 表層的な実装細部の指摘は \`/codex:review\` で別途確認する

⚠ **\`/codex:adversarial-review\` からの指摘に対する修正は、 いきなり実装に入らず、 まず \`/codex:rescue\` で方針を壁打ちしてから実装を開始してください**:
  1. 指摘ごとに修正方針を言語化する (どの指摘をどう直すか / 代替案 / トレードオフ)
  2. \`/codex:rescue --wait\` を Skill tool で呼び出し、 その方針が以下の観点で妥当かを問う:
       - 指摘の根本原因に対する解として妥当か
       - 場当たり的な対処になっていないか (= 表層を塗りつぶすだけになっていないか)
       - 全体設計・既存の方針と一貫しているか
  3. \`/codex:rescue\` の応答が **approve (= 方針 OK)** になってから実装を開始する。
     rescue から異論・代替案・追加考慮事項が出た場合は方針を見直して再度 rescue に投げる

adversarial review は設計レベルの指摘になりやすく、 場当たり的な修正は新たな歪みを生むため、 事前の方針壁打ちで「根本原因への解として妥当か」「全体設計と一貫しているか」を確認してから実装する規律が特に重要です (本リポジトリの sibling プラグイン pre-push-review でも \`/codex:review\` 指摘修正時に同じ規律を要求しています)。

修正後は pre-push-review プラグインの通常ループ (\`/simplify\` → \`/codex:review --wait --scope branch\` → \`pre-push-review:security-reviewer\` subagent) を再度走らせて push に進んでください (差分が変わると pre-push-review のマーカーが自然失効するため、 手順省略はできません)。

\`/codex:adversarial-review\` は frontmatter で \`disable-model-invocation: true\` が指定されているため、Skill tool から呼び出すには姉妹プラグイン \`codex-review-customize\` の \`/codex-review-customize:setup\` でパッチを適用しておく必要があります。未適用の場合は会話入力としての \`/codex:adversarial-review --wait --scope branch\` を実行してください。 一方 \`/codex:rescue\` は \`disable-model-invocation\` が指定されていないため、 codex-review-customize パッチなしでも \`Skill(codex:rescue)\` で呼び出し可能です。
EOF
)

jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    additionalContext: $ctx
  }
}'
