#!/bin/bash
# apply-patch.sh
# 公式 codex プラグインの commands/*.md をローカルでパッチする。
#   - frontmatter から `disable-model-invocation: true` を削除 (Skill tool から呼び出し可能に)
# 対象:
#   - commands/review.md          (`/codex:review`)
# 適用済みかはマーカー (<!-- codex-review-customize: patched -->) で判定し、二重適用を避ける。
# 復元はマーケットプレイス clone 上での `git checkout commands/<file>.md` か、
# codex プラグインの再インストールで行う (本スクリプト自身は backup を残さない)。

set -euo pipefail

PATCH_MARKER="<!-- codex-review-customize: patched -->"

# パッチ対象。codex プラグインで「Skill tool から起動したい read-only review コマンド」を
# 列挙する。新たな codex review 系コマンドが増えたらここに追記する。
TARGET_FILENAMES=(
  "review.md"
)

# 1 ファイルにパッチを当てる。既適用なら no-op。前提を満たさなければ非 0 を返す。
# 戻り値: 0 = 適用 or 既適用 / 1 = エラー
# 副作用: caller が定義している配列 `PATCHED_PATHS` に解決済みパスを append する
#         (cache 削除時に marketplace ID 抽出の参照を 1 つ拾うために使う)。
#         caller 側で `PATCHED_PATHS=()` を本関数の最初の呼び出しよりも前に初期化すること。
patch_one() {
  local target_filename="$1"

  # 候補が複数あると上書き対象を間違えるため、先頭採用ではなく明示的に件数で分岐する。
  # macOS の default /bin/bash 3.2.57 は mapfile (bash 4.0+) 非対応のため while read で代用。
  local CANDIDATES=()
  local line
  while IFS= read -r line; do
    CANDIDATES+=("$line")
  done < <(find "$HOME/.claude/plugins/marketplaces" -path "*/plugins/codex/commands/$target_filename" 2>/dev/null)
  case "${#CANDIDATES[@]}" in
    0)
      echo "[codex-review-customize] codex プラグインの commands/$target_filename が見つかりません。先に codex プラグインをインストールしてください。" >&2
      return 1 ;;
    1) ;;
    *)
      echo "[codex-review-customize] codex の $target_filename が複数見つかりました。手動で 1 つに絞ってから再実行してください:" >&2
      printf '  - %s\n' "${CANDIDATES[@]}" >&2
      return 1 ;;
  esac

  local TARGET="${CANDIDATES[0]}"

  if grep -qF "$PATCH_MARKER" "$TARGET"; then
    echo "[codex-review-customize] 既にパッチ済み: $TARGET (no-op)" >&2
    PATCHED_PATHS+=("$TARGET")
    return 0
  fi

  # `disable-model-invocation: true` を frontmatter から削除し、末尾に再適用検出用マーカーを追記。
  # 対象ファイルがトークン情報を含む可能性は低いが、temp を world-readable にしないため defense-in-depth で umask を絞る。
  umask 077
  local TMP
  TMP=$(mktemp "$TARGET.XXXXXX")
  # 途中で abort した場合に半端な temp を残さないよう EXIT trap で必ず削除。
  trap 'rm -f "$TMP"' EXIT

  sed '/^disable-model-invocation:[[:space:]]*true[[:space:]]*$/d' "$TARGET" > "$TMP"

  # sed silent no-op を検出する: TMP の frontmatter (先頭 --- から次の --- まで) に
  # `disable-model-invocation:` で始まる行が残っていれば、上流の表記が変わっており
  # sed パターンが効いていない。マーカーだけ追記して「適用済み」と誤報告する事故を防ぐ。
  if awk '/^---[[:space:]]*$/{c++; if (c==2) exit} c==1 && /^disable-model-invocation:/' "$TMP" | grep -q .; then
    echo "[codex-review-customize] $target_filename の frontmatter から disable-model-invocation 行を削除できませんでした (上流の表記変更の可能性)。手動でパッチを書き直してください。" >&2
    return 1
  fi

  # 上流が trailing newline なしで終わる場合に、append したセクションが
  # 直前行と連結されないよう補正する。
  [ -z "$(tail -c1 "$TMP")" ] || printf '\n' >> "$TMP"

  printf '\n%s\n' "$PATCH_MARKER" >> "$TMP"

  # 簡易な健全性チェック: frontmatter の開始 \`---\` が 1 行目にあること。
  if ! head -n1 "$TMP" | grep -qE '^---[[:space:]]*$'; then
    echo "[codex-review-customize] $target_filename: 生成された patch の先頭が frontmatter ではありません。原本を変更しません。" >&2
    return 1
  fi

  # `umask 077` で作った TMP は 0600 だが、原本の mode を保持して mv する。
  # 他ツールが当該ファイルを読む可能性があるため、無闇に world-readable を外さない。
  local ORIGINAL_MODE
  ORIGINAL_MODE=$(stat -c '%a' "$TARGET" 2>/dev/null || stat -f '%A' "$TARGET" 2>/dev/null || echo 644)
  chmod "$ORIGINAL_MODE" "$TMP"

  mv "$TMP" "$TARGET"
  trap - EXIT

  echo "[codex-review-customize] パッチ適用: $TARGET" >&2
  PATCHED_PATHS+=("$TARGET")
}

# パッチ完了したファイルを記録 (cache 削除時の marketplace ID 抽出に 1 つ拾うため)。
PATCHED_PATHS=()

for FNAME in "${TARGET_FILENAMES[@]}"; do
  if ! patch_one "$FNAME"; then
    echo "[codex-review-customize] $FNAME のパッチ適用に失敗しました。中断します。" >&2
    exit 1
  fi
done

# /reload-plugins は cache を優先して読むため、対応する codex の cache を削除して
# 次回の /reload-plugins で marketplace clone から再 build させる。
# 任意の patched パスから marketplace ID を抽出すれば、同じ marketplace の cache を
# 狙い撃てる (find で `*/codex` を探すと別 marketplace の codex まで巻き込む可能性があるため)。
CACHE_MSG=""
if [ "${#PATCHED_PATHS[@]}" -gt 0 ]; then
  REF_PATH="${PATCHED_PATHS[0]}"
  MARKETPLACE_ID=$(printf '%s' "$REF_PATH" | sed -n 's|.*/plugins/marketplaces/\([^/]*\)/plugins/codex/commands/[^/]*\.md$|\1|p')
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
fi

cat <<MSG
[codex-review-customize] パッチ処理完了。

  対象:
$(printf '    - %s\n' "${PATCHED_PATHS[@]}")

変更内容 (各ファイルに対し):
  - frontmatter から \`disable-model-invocation: true\` を削除 (Skill tool から呼び出し可能に)
  - 本文末尾に再適用検出用マーカーを追記

$CACHE_MSG

次のステップ:
  1. Claude Code で \`/reload-plugins\` を実行し、codex プラグインを cache から再 build させる。
  2. これ以降、Claude が Skill tool 経由で \`/codex:review\` を呼び出せる (会話入力としてのコマンドは従前通り利用可能)。

復元 (パッチ削除) が必要な場合は次のいずれか:
  - codex プラグインを再インストール (marketplace から再 clone)
  - codex の marketplace clone で \`git checkout commands/review.md\`

codex プラグインが update されると本パッチは消えるため、その後 \`/codex-review-customize:setup\` を再実行してください。
MSG
