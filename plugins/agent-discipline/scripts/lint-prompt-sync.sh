#!/bin/sh
# lint-prompt-sync.sh
#
# agent-discipline plugin のモデル別 2 プロンプトファイル (hooks/prompts/always-fable.md /
# always-sonnet.md) と hooks/hooks.json の 4 type:agent entries (gh issue create / gh issue
# edit / gh pr create / gh pr edit) が、意図せず同期ドリフトしていないかを検証する構造 lint。
# #178 (Phase A: 設計記述 commit)。 親 issue #173、 モデル別 2 ファイル化元 #175。
# .github/workflows/agent-discipline-prompt-lint.yml から呼ばれる。
#
# 現時点 (Phase A) は本ファイルに記載する契約の "設計記述" のみが目的で、 本体は no-op
# (exit 0) とする。 実装本体は Phase B (#178 の後続 commit) で追加する。
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
#   1. jq で `.hooks.PreToolUse[0].hooks[]` から `{if, prompt}` を 4 entries 分抽出する。
#   2. 各 entry について (a)(b)(c) の正規化を適用し、 entry 固有部分を共通ブロックから除去する。
#   3. 正規化後の 4 entries のテキストが byte-identical であれば pass。
#   4. 一致しない場合は fail し、 どの entry がどこで基準 (例: issue create を基準とする)
#      から乖離しているかを diff 形式で報告する。
#
# ============================================================================
# 実装本体 (Phase B で追加)
# ============================================================================

exit 0
