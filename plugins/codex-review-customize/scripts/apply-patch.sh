#!/bin/bash
# apply-patch.sh
# 公式 codex プラグインの commands/review.md をローカルでパッチする。
#   - frontmatter から `disable-model-invocation: true` を削除 (Skill tool から呼び出し可能に)
# 適用済みかはマーカー (<!-- codex-review-customize: patched -->) で判定し、二重適用を避ける。
# 復元はマーケットプレイス clone 上での `git checkout commands/review.md` か、
# codex プラグインの再インストールで行う (本スクリプト自身は backup を残さない)。

set -euo pipefail

PATCH_MARKER="<!-- codex-review-customize: patched -->"

# 候補が複数あると上書き対象を間違えるため、先頭採用ではなく明示的に件数で分岐する。
mapfile -t REVIEW_MD_CANDIDATES < <(find "$HOME/.claude/plugins/marketplaces" -path '*/plugins/codex/commands/review.md' 2>/dev/null)
case "${#REVIEW_MD_CANDIDATES[@]}" in
  0)
    echo "[codex-review-customize] codex プラグインの commands/review.md が見つかりません。先に codex プラグインをインストールしてください。" >&2
    exit 1 ;;
  1) REVIEW_MD="${REVIEW_MD_CANDIDATES[0]}" ;;
  *)
    echo "[codex-review-customize] codex の review.md が複数見つかりました。手動で 1 つに絞ってから再実行してください:" >&2
    printf '  - %s\n' "${REVIEW_MD_CANDIDATES[@]}" >&2
    exit 1 ;;
esac

if grep -qF "$PATCH_MARKER" "$REVIEW_MD"; then
  echo "[codex-review-customize] 既にパッチ済み: $REVIEW_MD (no-op)" >&2
  exit 0
fi

# `disable-model-invocation: true` を frontmatter から削除し、末尾に再適用検出用マーカーを追記。
# review.md がトークン情報を含む可能性は低いが、temp を world-readable にしないため defense-in-depth で umask を絞る。
umask 077
TMP=$(mktemp "$REVIEW_MD.XXXXXX")
# 途中で abort した場合に半端な temp を残さないよう EXIT trap で必ず削除。
trap 'rm -f "$TMP"' EXIT

sed '/^disable-model-invocation:[[:space:]]*true[[:space:]]*$/d' "$REVIEW_MD" > "$TMP"

# sed silent no-op を検出する: TMP の frontmatter (先頭 --- から次の --- まで) に
# `disable-model-invocation:` で始まる行が残っていれば、上流の表記が変わっており
# sed パターンが効いていない。マーカーだけ追記して「適用済み」と誤報告する事故を防ぐ。
if awk '/^---[[:space:]]*$/{c++; if (c==2) exit} c==1 && /^disable-model-invocation:/' "$TMP" | grep -q .; then
  echo "[codex-review-customize] frontmatter から disable-model-invocation 行を削除できませんでした (上流の表記変更の可能性)。手動でパッチを書き直してください。" >&2
  exit 1
fi

# 上流 review.md が trailing newline なしで終わる場合に、append したセクションが
# 直前行と連結されないよう補正する。
[ -z "$(tail -c1 "$TMP")" ] || printf '\n' >> "$TMP"

printf '\n%s\n' "$PATCH_MARKER" >> "$TMP"

# 簡易な健全性チェック: frontmatter の開始 \`---\` が 1 行目にあること。
if ! head -n1 "$TMP" | grep -qE '^---[[:space:]]*$'; then
  echo "[codex-review-customize] 生成された patch の先頭が frontmatter ではありません。原本を変更しません。" >&2
  exit 1
fi

# `umask 077` で作った TMP は 0600 だが、原本の mode を保持して mv する。
# 他ツールが review.md を読む可能性があるため、無闇に world-readable を外さない。
ORIGINAL_MODE=$(stat -c '%a' "$REVIEW_MD" 2>/dev/null || stat -f '%A' "$REVIEW_MD" 2>/dev/null || echo 644)
chmod "$ORIGINAL_MODE" "$TMP"

mv "$TMP" "$REVIEW_MD"
trap - EXIT

# /reload-plugins は cache を優先して読むため、対応する codex の cache を削除して
# 次回の /reload-plugins で marketplace clone から再 build させる。
# REVIEW_MD のパスから marketplace ID を抽出し、同じ marketplace の cache を狙い撃つ
# (find で `*/codex` を探すと別 marketplace の codex まで巻き込む可能性があるため)。
MARKETPLACE_ID=$(printf '%s' "$REVIEW_MD" | sed -n 's|.*/plugins/marketplaces/\([^/]*\)/plugins/codex/commands/review\.md$|\1|p')
if [ -z "$MARKETPLACE_ID" ]; then
  CACHE_MSG="codex の marketplace ID 抽出に失敗したため cache 削除はスキップしました。/reload-plugins 後に Skill tool から呼び出せない場合は ~/.claude/plugins/cache/*/codex を手動削除してください。"
else
  CACHE_DIR="$HOME/.claude/plugins/cache/$MARKETPLACE_ID/codex"
  if [ -d "$CACHE_DIR" ]; then
    rm -rf "$CACHE_DIR"
    CACHE_MSG="codex の cache ($CACHE_DIR) を削除しました。"
  else
    CACHE_MSG="codex の cache ($CACHE_DIR) は見つかりませんでした (未生成 or 既に削除済み)。"
  fi
fi

cat <<MSG
[codex-review-customize] パッチ適用完了。

  対象: $REVIEW_MD

変更内容:
  - frontmatter から \`disable-model-invocation: true\` を削除 (Skill tool から呼び出し可能に)
  - 本文末尾に再適用検出用マーカーを追記

$CACHE_MSG

次のステップ:
  1. Claude Code で \`/reload-plugins\` を実行し、codex プラグインを cache から再 build させる。
  2. これ以降、Claude が Skill tool 経由で \`/codex:review\` を呼び出せる (会話入力としての \`/codex:review\` は従前通り利用可能)。

復元 (パッチ削除) が必要な場合は次のいずれか:
  - codex プラグインを再インストール (marketplace から再 clone)
  - codex の marketplace clone で \`git checkout commands/review.md\`

codex プラグインが update されると本パッチは消えるため、その後 \`/codex-review-customize:setup\` を再実行してください。
MSG
