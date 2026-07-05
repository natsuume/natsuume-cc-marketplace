#!/bin/sh
# lint-prompt-sync.sh
#
# agent-discipline plugin のモデル別 2 プロンプトファイル (hooks/prompts/always-fable.md /
# always-sonnet.md) と hooks/hooks.json の 4 type:agent entries (gh issue create / gh issue
# edit / gh pr create / gh pr edit) が、意図せず同期ドリフトしていないかを検証する構造 lint。
# #178 (Phase B: 実装本体)。 Phase A (設計記述 commit, #178) で本ファイルに書いた契約を
# そのまま実装する。 親 issue #173、 モデル別 2 ファイル化元 #175。
# .github/workflows/agent-discipline-prompt-lint.yml から呼ばれる。
#
# ============================================================================
# 入出力契約
# ============================================================================
#
# - 引数: なし (将来の flag 拡張余地は残すが、 現状は引数を取らない)
# - 実行位置: リポジトリルートから実行することを前提とする (plugins/agent-discipline/... や
#   .github/workflows/... への相対パス参照を用いるため)。 リポジトリルート以外から実行した
#   場合や、 参照先ファイルが見つからない場合は fail-closed (exit 1) とし、 チェックを
#   silent skip しない。
# - exit code: 両チェックとも pass のとき 0。 いずれかのチェックが fail、 または前提
#   ファイルが読めない等の実行時エラーのときは 1。
# - 動作環境: CI (ubuntu-latest) 上での動作を必須要件とするが、 macOS 互換は要件外
#   (CI 専用ツールのため)。 ただし POSIX 準拠のシェル記法のみを用い、 bash 依存の記法
#   (配列, [[ ]], mapfile 等) を避けることで、 手元の sh / bash どちらでも実行できる
#   ようにする。 jq (JSON 抽出に使用) は ubuntu-latest に標準搭載されており、 ローカル
#   実行時は利用者が別途インストールする前提とする。
#
# ============================================================================
# チェック 1: ルール ID 一致 (always-fable.md <-> always-sonnet.md)
# ============================================================================
#
# 対象ファイル:
#   - plugins/agent-discipline/hooks/prompts/always-fable.md
#   - plugins/agent-discipline/hooks/prompts/always-sonnet.md
#
# 契約:
#   1. 両ファイルから `<!-- rule:<id> -->` 形式のコメント行 (例: `<!-- rule:bash-decompose -->`)
#      を抽出し、 それぞれの ID 集合を作る (grep -o '<!-- rule:[a-z0-9-]\+ -->' などで抽出し、
#      `rule:` プレフィクスと `-->` サフィックスを取り除いた ID 文字列を要素とする)。
#   2. 2 つの ID 集合を順序に依らない集合として比較する (ソート済み一覧の diff で可)。
#   3. 集合が完全一致すれば pass。 一致しない場合は fail し、 片方にのみ存在する ID
#      (差集合) を両方向とも列挙してエラーメッセージに含める。
#
# スコープ外 (意図的に検出しない):
#   - ID セットが一致した上でのルール本文の表現差分 (意味的ドリフト)。 これは自動検出
#     せず、 PR レビュー担当者が目視で確認する運用とする (issue #178 の合意事項)。
#
# ============================================================================
# チェック 2: hooks.json 4 entries の共通ブロック一致
# ============================================================================
#
# 対象ファイル:
#   - plugins/agent-discipline/hooks/hooks.json
#
# 対象 entry (`.hooks.PreToolUse[0].hooks[]` 配下、 type == "agent" の 4 要素):
#   - if: "Bash(gh issue create:*)"
#   - if: "Bash(gh issue edit:*)"
#   - if: "Bash(gh pr create:*)"
#   - if: "Bash(gh pr edit:*)"
#
# 各 entry の `prompt` フィールドは概ね以下の構成を持つ:
#   - 冒頭段落 (「あなたは agent-discipline plugin の PreToolUse hook subagent です。...」)
#   - Step 0: defense-in-depth command guard
#   - Step 1: body content の抽出仕様
#   - Step 2: 禁止カテゴリの semantic 判定 (禁止カテゴリ列挙)
#   - Step 3: 返り値 (gh pr create のみ Step 3 が「Closes 検証」 になり、 返り値は Step 4 に
#     繰り下がる)
#   - 判定原則 (確定済み事項への rationale 記述除外 / 規範のソース・オブ・トゥルース / fail-closed)
#   - Hook input セクション ($ARGUMENTS 展開)
#
# entry 固有部分の定義 (以下を除いた残りが「共通ブロック」であり、 4 entries で一致すべき):
#
#   (a) 対象コマンド名の記載箇所:
#       - 冒頭段落中の `` `if: "Bash(gh <cmd>:*)"` `` という参照
#       - Step 0 本文中の 3 箇所: `` `gh <cmd>` literal で始まらない `` /
#         `` `cd repo && gh <cmd> ...` `` (compound 例) /
#         `` `cat > body.md && gh <cmd> -F body.md` `` (compound 例)
#       ここで `<cmd>` は entry の `if` フィールドから機械的に導出できる
#       (`Bash(gh ` プレフィクスと `:*)` サフィックスを取り除いた文字列。 例:
#       `Bash(gh pr create:*)` -> `pr create`)。 正規化時はこの `<cmd>` 文字列を含む
#       `gh <cmd>` という部分文字列をプレースホルダ (例: `gh __CMD__`) に置換して吸収する。
#
#   (b) gh pr create のみが持つ Closes 検証 Step:
#       `## Step 3: Closes 検証 (branch 名からの issue 推定)` 見出しから、 その次の
#       `## Step 4: 返り値` 見出しの直前までの全文は pr create 固有であり、 他 3 entries には
#       存在しない。 正規化時はこのブロックを丸ごと除去したうえで、 pr create 側の
#       `## Step 4: 返り値` 見出しを `## Step 3: 返り値` に読み替え、 本文中の
#       `Step 2 (禁止カテゴリ判定) に該当なし` / `Step 2 に該当あり` という参照を
#       他 3 entries と同じ `該当なし` / `該当あり` という表現に読み替える
#       (Closes 検証 Step の追加に伴う不可避な参照ズレであり、 意図的なドリフトではないため)。
#
#   (c) PR 固有の判定原則 (gh pr create と gh pr edit の両方が持つ):
#       判定原則セクション 1 つ目の箇条書き末尾に付く以下の追加文言:
#       「。 PR body で commit/discussion 経由でユーザ承認が明示されている文脈
#       (= 「ユーザの decision により A を採用」 等の明示宣言) は禁止対象外」
#       は issue create / issue edit には存在せず、 pr create / pr edit にのみ存在する。
#       正規化時はこの文言を除去する (存在しない場合は何もしない)。
#
# 契約:
#   1. jq で `.hooks.PreToolUse[0].hooks[]` から、 既知の 4 つの `if` 値それぞれに対応する
#      `prompt` を個別に select して抽出する (`if` 値をキーに逆引きするため entries の配列順が
#      入れ替わっても追従できる)。
#   2. 各 entry について (a)(b)(c) の正規化を適用し、 entry 固有部分を共通ブロックから除去する。
#   3. 正規化後の 4 entries のテキストが byte-identical であれば pass。
#   4. 一致しない場合は fail し、 どの entry がどこで基準 (issue create を基準とする)
#      から乖離しているかを diff 形式で報告する。
#
# ============================================================================
# 実装本体
# ============================================================================

set -u

FABLE_MD="plugins/agent-discipline/hooks/prompts/always-fable.md"
SONNET_MD="plugins/agent-discipline/hooks/prompts/always-sonnet.md"
HOOKS_JSON="plugins/agent-discipline/hooks/hooks.json"

overall_fail=0

# --- pre-flight: リポジトリルートから実行されているか / jq が使えるか ---
for f in "$FABLE_MD" "$SONNET_MD" "$HOOKS_JSON"; do
  if [ ! -f "$f" ]; then
    echo "ERROR: $f が見つかりません。リポジトリルートから実行してください。" >&2
    exit 1
  fi
done

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq が見つかりません。チェック 2 (hooks.json の解析) は jq に依存します。jq をインストールしてから再実行してください。" >&2
  exit 1
fi

WORKDIR=$(mktemp -d 2>/dev/null) || { echo "ERROR: 一時ディレクトリの作成 (mktemp -d) に失敗しました。" >&2; exit 1; }
trap 'rm -rf "$WORKDIR"' EXIT INT TERM HUP

# ============================================================================
# チェック 1: ルール ID 一致
# ============================================================================

echo "== check 1: rule ID set (always-fable.md <-> always-sonnet.md) =="

extract_rule_ids() {
  # $1 = file path。 `<!-- rule:<id> -->` から <id> だけを取り出しソートする。
  grep -Eo '<!-- rule:[a-zA-Z0-9_-]+ -->' "$1" 2>/dev/null \
    | sed -e 's/<!-- rule:\(.*\) -->/\1/' \
    | sort
}

extract_rule_ids "$FABLE_MD" > "$WORKDIR/ids_fable.txt"
extract_rule_ids "$SONNET_MD" > "$WORKDIR/ids_sonnet.txt"

if [ ! -s "$WORKDIR/ids_fable.txt" ] || [ ! -s "$WORKDIR/ids_sonnet.txt" ]; then
  echo "ERROR: <!-- rule:<id> --> 形式のコメントが 1 件も抽出できませんでした ($FABLE_MD / $SONNET_MD)。ファイル欠如またはコメント形式の変更の可能性があります。" >&2
  exit 1
fi

if diff -u "$WORKDIR/ids_fable.txt" "$WORKDIR/ids_sonnet.txt" > "$WORKDIR/ids_diff.txt" 2>&1; then
  id_count=$(wc -l < "$WORKDIR/ids_fable.txt" | tr -d ' ')
  echo "OK: rule ID sets match (${id_count} IDs)"
else
  echo "FAIL: rule ID sets differ between $FABLE_MD and $SONNET_MD" >&2
  cat "$WORKDIR/ids_diff.txt" >&2
  overall_fail=1
fi

# ============================================================================
# チェック 2: hooks.json 4 entries の共通ブロック一致
# ============================================================================

echo ""
echo "== check 2: hooks.json 4 type:agent entries common block =="

extract_prompt() {
  # $1 = if フィールドの値 (例: "Bash(gh issue create:*)")
  jq -r --arg iff "$1" '
    .hooks.PreToolUse[0].hooks[]
    | select(.type == "agent" and .if == $iff)
    | .prompt
  ' "$HOOKS_JSON"
}

if_issue_create='Bash(gh issue create:*)'
if_issue_edit='Bash(gh issue edit:*)'
if_pr_create='Bash(gh pr create:*)'
if_pr_edit='Bash(gh pr edit:*)'

extract_prompt "$if_issue_create" > "$WORKDIR/raw_issue_create.txt"
extract_prompt "$if_issue_edit" > "$WORKDIR/raw_issue_edit.txt"
extract_prompt "$if_pr_create" > "$WORKDIR/raw_pr_create.txt"
extract_prompt "$if_pr_edit" > "$WORKDIR/raw_pr_edit.txt"

for name in raw_issue_create raw_issue_edit raw_pr_create raw_pr_edit; do
  if [ ! -s "$WORKDIR/$name.txt" ]; then
    echo "ERROR: hooks.json から type:agent entry ($name) の prompt が抽出できませんでした。if フィールドの変更、または entry の欠落の可能性があります。" >&2
    exit 1
  fi
done

# (a) 対象コマンド名の記載箇所の除去: "gh <cmd>" というリテラルをプレースホルダに置換する。
#     冒頭段落の `if: "Bash(gh <cmd>:*)"` 参照、 Step 0 内の 3 箇所 (literal 判定 /
#     cd 複合例 / cat 複合例) をまとめて吸収できる (いずれも文字列 "gh <cmd>" を含むため)。
norm_a() {
  # $1 = cmd literal (例: "issue create")。 stdin = raw prompt、 stdout = 正規化後。
  sed "s/gh $1/gh __CMD__/g"
}

# (b) gh pr create のみが持つ Closes 検証 Step (Step 3) の除去。
#     除去後に Step 4 (返り値) を Step 3 に読み替え、 Step 番号参照の文言も他 3 entries と
#     揃える (Closes Step 追加に伴う不可避な繰り下がりであり、 意図的なドリフトではないため)。
norm_b_pr_create_only() {
  sed -e '/^## Step 3: Closes 検証/,/^## Step 4: 返り値/{/^## Step 4: 返り値/!d}' \
    -e 's/^## Step 4: 返り値/## Step 3: 返り値/' \
    -e 's/Step 2 (禁止カテゴリ判定) に該当なし/該当なし/' \
    -e 's/Step 2 に該当あり/該当あり/'
}

# (c) gh pr create / gh pr edit の両方が持つ PR 固有の判定原則追加文の除去。
norm_c_pr_only() {
  sed 's#。 PR body で commit/discussion 経由でユーザ承認が明示されている文脈 (= 「ユーザの decision により A を採用」 等の明示宣言) は禁止対象外##'
}

norm_a "issue create" < "$WORKDIR/raw_issue_create.txt" > "$WORKDIR/norm_issue_create.txt"
norm_a "issue edit" < "$WORKDIR/raw_issue_edit.txt" > "$WORKDIR/norm_issue_edit.txt"
norm_a "pr create" < "$WORKDIR/raw_pr_create.txt" | norm_b_pr_create_only | norm_c_pr_only > "$WORKDIR/norm_pr_create.txt"
norm_a "pr edit" < "$WORKDIR/raw_pr_edit.txt" | norm_c_pr_only > "$WORKDIR/norm_pr_edit.txt"

baseline="$WORKDIR/norm_issue_create.txt"
check2_fail=0
for entry in norm_issue_edit norm_pr_create norm_pr_edit; do
  if ! diff -u "$baseline" "$WORKDIR/$entry.txt" > "$WORKDIR/diff_$entry.txt" 2>&1; then
    echo "FAIL: common block mismatch between issue-create (baseline) and ${entry#norm_}" >&2
    cat "$WORKDIR/diff_$entry.txt" >&2
    check2_fail=1
  fi
done

if [ "$check2_fail" -eq 0 ]; then
  echo "OK: hooks.json 4 entries の共通ブロックが一致しました"
else
  overall_fail=1
fi

# ============================================================================
# 結果
# ============================================================================

echo ""
if [ "$overall_fail" -ne 0 ]; then
  echo "lint-prompt-sync.sh: FAIL" >&2
  exit 1
fi

echo "lint-prompt-sync.sh: OK (all checks passed)"
exit 0
