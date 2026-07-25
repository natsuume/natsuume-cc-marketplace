#!/bin/sh
# lint-prompt-sync.sh
#
# agent-discipline plugin のモデル別プロンプトファイル (hooks/prompts/always-fable.md /
# always-sonnet-1.md / always-sonnet-2.md / always-sonnet-3.md、issue #236 で always-sonnet.md
# から 3 part に分割) と hooks/hooks.json の 4 type:agent entries (gh issue create / gh issue
# edit / gh pr create / gh pr edit) が、意図せず同期ドリフトしていないかを検証する構造 lint。
# #178 (Phase B: 実装本体) で最初に実装された。 #185 (チェック 3 新設) / #186 (チェック 2
# 前提検証) / #187 (除去系実在性検証) は検出カバレッジの穴を埋める拡張 (v0.7.2 予定) であり、
# 本 commit はその Phase A (設計記述 commit) である。 本ファイルに書いた契約をそのまま
# Phase B で実装する。 親 issue #173、 モデル別 2 ファイル化元 #175、 検出層拡張の親 issue
# #184。 issue #236 で always-sonnet.md の 3 part 分割に伴いチェック 1/5 の母集合を
# 3 part の和集合へ変更し、 part 間 rule ID 重複検査を追加した。 その後の codex review P2
# 指摘を受け、 各 part ファイル単体での rule ID マーカー重複検査 (uniq -d) も追加した
# (part 間 pairwise 検査は自分自身と比較しないため単一ファイル内重複を検出できず、
# 和集合化 (sort -u) がそれを無音で吸収してしまう盲点への対処)。
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
# - exit code: 全チェック (1, 2, 3) が pass のとき 0。 いずれかのチェックが fail、
#   チェック 2 冒頭の前提検証 (#186) が不成立、 または前提ファイルが読めない等の実行時
#   エラーのときは 1。
# - 動作環境: CI (ubuntu-latest) 上での動作を必須要件とするが、 macOS 互換は要件外
#   (CI 専用ツールのため)。 ただし POSIX 準拠のシェル記法のみを用い、 bash 依存の記法
#   (配列, [[ ]], mapfile 等) を避けることで、 手元の sh / bash どちらでも実行できる
#   ようにする。 jq (JSON 抽出に使用) は ubuntu-latest に標準搭載されており、 ローカル
#   実行時は利用者が別途インストールする前提とする。
#
# ============================================================================
# チェック 1: ルール ID 一致 (always-fable.md <-> always-sonnet-{1,2,3}.md の和集合)
# ============================================================================
#
# 対象ファイル:
#   - plugins/agent-discipline/hooks/prompts/always-fable.md
#   - plugins/agent-discipline/hooks/prompts/always-sonnet-1.md
#   - plugins/agent-discipline/hooks/prompts/always-sonnet-2.md
#   - plugins/agent-discipline/hooks/prompts/always-sonnet-3.md
#
# 背景 (issue #236):
#   注入ペイロード分割により always-sonnet.md を rule 境界で 3 part に分割したため、
#   「sonnet 側の ID 集合」を単一ファイルではなく 3 part ファイルの和集合として扱う。
#   fable との完全一致検査 (契約 3) は従来どおり維持する。
#
# 契約:
#   1. always-fable.md と always-sonnet-{1,2,3}.md それぞれから `<!-- rule:<id> -->` 形式の
#      コメント行 (例: `<!-- rule:bash-decompose -->`) を抽出し、 ID 集合を作る (grep -o
#      '<!-- rule:[a-z0-9-]\+ -->' などで抽出し、 `rule:` プレフィクスと `-->` サフィックスを
#      取り除いた ID 文字列を要素とする)。 抽出結果は重複を保持したまま sort する (2. の
#      単一ファイル内重複検査が重複保持出力に依存するため、 ここでは sort -u しない)。
#   2. (#236 P2、 codex review 指摘) always-sonnet-{1,2,3}.md の各ファイル単体について、
#      同一ファイル内で rule ID マーカーが重複していないことを検証する (1. の重複保持出力に
#      `uniq -d` を掛けて重複 ID を抽出する)。 3. のペアワイズ検査は自分自身とは比較しない
#      ため単一ファイル内の重複を検出できず、 4. の和集合化 (sort -u) は重複を無音で吸収
#      してしまうため、 両方より前に検証する。 重複があれば fail してその ID を列挙する。
#   3. always-sonnet-{1,2,3}.md 3 ファイルの ID 集合は互いに素であること (part 間で rule ID が
#      重複しないこと) を検証する。 和集合化によって重複が隠れてしまうため、 和集合を作る前に
#      3 ファイルの全 2 組 (1-2, 1-3, 2-3) についてペアワイズに共通要素が無いことを確認し、
#      共通要素があれば fail してその ID を列挙する。
#   4. always-sonnet-{1,2,3}.md 3 ファイルの ID 集合の和集合を「sonnet 側の ID 集合」とする。
#   5. always-fable.md の ID 集合と 4. の和集合を、 順序に依らない集合として比較する
#      (ソート済み一覧の diff で可)。
#   6. 集合が完全一致すれば pass。 一致しない場合は fail し、 片方にのみ存在する ID
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
#       正規化 (norm_b) はこのブロックを除去する前に、 対象の gh pr create prompt 内に
#       `## Step 3: Closes 検証` 見出しから `## Step 4: 返り値` 見出しまでのブロックが
#       実在することを検証し、 存在しない場合は除去処理を silent no-op にせず fail する
#       (#187 の「除去対象の実在性未検証」パターンをこの除去にも適用する統一方針。
#       #185 のチェック 3 の対象ブロックと同一のブロックである)。
#
#   (c) PR 固有の判定原則 (gh pr create と gh pr edit の両方が持つ):
#       判定原則セクション 1 つ目の箇条書き末尾に付く以下の追加文言:
#       「。 PR body で commit/discussion 経由でユーザ承認が明示されている文脈
#       (= 「ユーザの decision により A を採用」 等の明示宣言) は禁止対象外」
#       は issue create / issue edit には存在せず、 pr create / pr edit にのみ存在する。
#       正規化 (norm_c) はこの文言を除去する前に、 PR 系 2 entries (`gh pr create` /
#       `gh pr edit`) それぞれの prompt 内に当該文言が実在することを検証し、 存在しない
#       場合は除去処理を silent no-op にせず fail する (#187)。
#
# 契約:
#   0. 前提検証 (#186、 チェック 2 の他の処理より前に実行する): `.hooks.PreToolUse[0].hooks[]`
#      のうち type == "agent" である entry の数が、 本ファイル内で定義する定数
#      `EXPECTED_AGENT_ENTRIES` (= 4) と一致することを検証する。 かつ、 既知の 4 つの `if`
#      値に対応する各 entry の `.prompt` フィールドが非空文字列であることを検証する。
#      いずれか 1 つでも不成立であれば、 以降の抽出・正規化・比較には進まず fail (exit 1)
#      とする (entry 数の増減や prompt 欠落という前提崩壊時に、 空同士の比較一致などで
#      pass 側へ倒れることを防ぐ)。
#   1. jq で `.hooks.PreToolUse[0].hooks[]` から、 既知の 4 つの `if` 値それぞれに対応する
#      `prompt` を個別に select して抽出する (`if` 値をキーに逆引きするため entries の配列順が
#      入れ替わっても追従できる)。
#   2. 各 entry について (a)(b)(c) の正規化を適用し、 entry 固有部分を共通ブロックから除去する。
#      (b)(c) はいずれも除去前に除去対象の文言が実在することを検証し、 実在しなければ fail
#      する (#187、 詳細は上記 (b)(c) の各定義を参照)。
#   3. 正規化後の 4 entries のテキストが byte-identical であれば pass。
#   4. 一致しない場合は fail し、 どの entry がどこで基準 (issue create を基準とする)
#      から乖離しているかを diff 形式で報告する。
#
# ============================================================================
# チェック 3 (新設, #185): gh pr create entry の Step 3 (Closes 検証) ブロック構造チェック
# ============================================================================
#
# 背景:
#   チェック 2 の正規化 (norm_b) は gh pr create 固有の Step 3 (Closes 検証) ブロックを、
#   共通ブロック比較の対象から除外するために丸ごと除去する。 そのため Step 3 ブロックの
#   中身自体 (判定手順の記述) がどのように破損しても、 除去後の共通ブロック比較 (チェック 2)
#   では検出できない (#185 の false pass 事例: 判定手順が丸ごと別の文言に置き換わっても、
#   開始・終了の見出しパターンさえ残っていれば norm_b はブロックを除去でき、 共通ブロック
#   比較は pass してしまう)。 チェック 3 はこの盲点を埋めるため、 除去される前の Step 3
#   ブロックの中身を独立に検証する。
#
# 対象:
#   - gh pr create entry (`if: "Bash(gh pr create:*)"`) の prompt から抽出した、
#     `## Step 3: Closes 検証 (branch 名からの issue 推定)` 見出しから
#     `## Step 4: 返り値` 見出しの直前までのブロック (チェック 2 の norm_b が正規化のために
#     除去する対象と同一のブロックを、 除去前の raw prompt から抽出して用いる)。
#
# 契約:
#   1. 期待構造のソース・オブ・トゥルースは本スクリプト内の定数とする (#185 の合意事項)。
#      README 等の外部文書は解析しない。 この定数は、 Step 3 の判定手順のうち以下の要素を
#      代表する文字列のリストとして持つ (具体的な文字列は Phase B で現物の Step 3 本文
#      から選定する):
#        - `.git` を Read tool で読む記述
#        - `gitdir:` 形式 (worktree) の解決に関する記述
#        - `ref: refs/heads/<branch>` 形式の判定に関する記述
#        - branch 名からの issue 番号 (`issue-<数字>`) 抽出に関する記述
#        - closing keyword 群 (Closes / Fixes / Resolves 等) に関する記述
#        - `#N` の境界一致判定 (`#12` が `#123` にマッチしないこと) に関する記述
#        - HEAD 取得不能等の場合に fail-open で通過させる規則に関する記述
#   2. 上記「対象」のブロック本文が、 1. の必須キーワードリストの要素をすべて含むかを
#      検証する (grep 等による文字列包含判定)。 いずれか 1 つでも欠落していれば fail する。
#   3. この検証は「Step 3 ブロックが判定手順の要素文字列を含むか」を確認する構造
#      スモークチェックであり、 判定手順の意味的な等価性 (ロジックが実際に正しく動作するか)
#      を検証するものではない。 キーワードをすべて含んだまま判定ロジックが破損するケース
#      (例: 条件式の反転、 keyword の意味が変わる形での書き換え) は本チェックのスコープ外
#      であり、 PR レビュー担当者が目視で確認する運用とする (チェック 1 のスコープ外事項と
#      同型の運用方針)。
#
# ============================================================================
# チェック 4 (新設, #195。PR2/agent-discipline 0.21.0 で 3 ファイル総当たりへ拡張):
# 分業規律 3 ファイルの rule ID セット一致
# ============================================================================
#
# 対象ファイル:
#   - plugins/agent-discipline/hooks/prompts/discipline-fable.md
#   - plugins/agent-discipline/hooks/prompts/discipline-sonnet.md
#   - plugins/agent-discipline/hooks/prompts/discipline-opus.md
#
# 背景:
#   #194 で分業規律がモデル別 2 ファイル化された (rule ID: role-split / delegation-rules /
#   delegation-instruction / escalation)。常時ルール 2 ファイルにはチェック 1 があるが
#   分業規律には同期検証が無く、PR #191 のレビューで「rule マーカー外・lint 対象外の
#   ドリフトは手動レビューでしか発見できない」ことが実証されている (Fable 版のみに存在した
#   進捗報告グラウンディング文の欠落を手動レビューで発見した事例) ため、同型の構造 lint を
#   分業規律ファイルにも掛ける。PR2 (agent-discipline 0.21.0) で discipline-opus.md
#   (Opus 系向け) が新設され分業規律が 3 ファイルになったため、対象を 3 ファイルへ拡張した。
#   モデル別バリアントは同一 rule ID セットを持つべき契約であり、常時ルールの part 分割検証
#   (チェック 1 の和集合方式) とは意味が異なるため、和集合方式は流用せず 3 ファイル総当たり
#   (fable↔sonnet と fable↔opus の 2 diff) の完全一致に拡張する (fable を hub にした 2 diff で
#   sonnet↔opus の一致も推移的に保証されるため、3 通りの組合せ全部ではなく 2 diff で足りる)。
#
# 契約:
#   1. チェック 1 と同じ抽出方式 (extract_rule_ids) で 3 ファイルの `<!-- rule:<id> -->`
#      ID 集合をそれぞれ抽出し、順序に依らない集合として比較する。
#   2. discipline-fable.md を基準に discipline-sonnet.md / discipline-opus.md それぞれと
#      diff を取る (2 diff)。両方が完全一致すれば pass。一致しない diff があれば fail し、
#      片方にのみ存在する ID (差集合) を両方向とも列挙してエラーメッセージに含める。
#   3. 対象 3 ファイルは pre-flight の存在チェック対象に加え、見つからなければ fail-closed
#      (exit 1) とする。マーカーが 1 件も抽出できない場合も fail (チェック 1 と同方針)。
#   4. 既存チェック 1〜3 の挙動には影響しない (共有するのは extract_rule_ids と WORKDIR のみ)。
#
# CI 発火 (ユーザ decision 2026-07-06、issue #195。PR2 で discipline-opus.md を追加):
#   .github/workflows/agent-discipline-prompt-lint.yml の paths filter に対象 3 ファイルを
#   追加し、discipline ファイルの変更でも本 lint が発火するようにする (paths 以外の workflow
#   構造は変更しない)。
#
# スコープ外 (チェック 1 と同じ方針): ID セットが一致した上でのルール本文の表現差分
# (意味的ドリフト) は自動検出せず、PR レビュー担当者が目視で確認する運用とする。
#
# ============================================================================
# チェック 5 (新設, #221): subagent-rules.md の rule ID サブセット検査
# ============================================================================
#
# 対象ファイル:
#   - plugins/agent-discipline/hooks/prompts/subagent-rules.md
#
# 背景:
#   #221 で subagent 向け常時適用ルール (SubagentStart 注入) が新設された。subagent-rules.md
#   は常時適用ルールの「サブセット + subagent 固有ブロック」で構成されるため、チェック 1/4 の
#   ような完全一致検査は適用できない。代わりに「rule: プレフィクスのマーカー ID が
#   always-sonnet-{1,2,3}.md の ID セットの和集合に含まれること」を検証し、always 側での
#   rule ID の改名・削除に subagent 版が追従し損ねるドリフト (存在しない rule への参照) を
#   CI で検知する (issue #236 で always-sonnet.md が 3 part に分割されたため母集合を和集合化)。
#
# 契約:
#   1. チェック 1 と同じ抽出方式 (extract_rule_ids) で subagent-rules.md の rule ID 集合を
#      抽出する。subagent 固有ブロックのマーカー (subagent-rule: プレフィクス) は
#      extract_rule_ids のパターンにマッチしないため、自然に検査対象外となる。
#   2. 抽出できた ID が 1 件も無い場合は fail (チェック 1/4 と同方針。マーカー形式の変更や
#      共有ルールの全削除という前提崩壊時に silent pass しない)。
#   3. 抽出した各 ID が always-sonnet-{1,2,3}.md の ID 集合の和集合 (チェック 1 で抽出・
#      重複検査済みの ids_sonnet.txt) に含まれていれば pass。含まれない ID があれば fail し、
#      その ID を列挙してエラーメッセージに含める (方向は subagent -> sonnet の片方向のみ。
#      sonnet 側にのみ存在する ID は「subagent に配送しない」という意図的な選択であり、
#      検査しない)。
#   4. 対象ファイルは pre-flight の存在チェック対象に加え、見つからなければ fail-closed
#      (exit 1) とする。
#   5. 既存チェック 1〜4 の挙動には影響しない (共有するのは extract_rule_ids と WORKDIR、
#      チェック 1 の ids_sonnet.txt のみ)。
#
# CI 発火 (#221): .github/workflows/agent-discipline-prompt-lint.yml の paths filter に
# subagent-rules.md を追加する (paths 以外の workflow 構造は変更しない)。
#
# スコープ外 (チェック 1 と同じ方針): ID が一致した上でのルール本文の表現差分 (意味的
# ドリフト) は自動検出せず、PR レビュー担当者が目視で確認する運用とする。
#
# ============================================================================
# 実装本体
# ============================================================================

set -u

FABLE_MD="plugins/agent-discipline/hooks/prompts/always-fable.md"
# issue #236 (注入ペイロード分割) で always-sonnet.md を 3 part に分割したため、
# チェック 1/5 の「sonnet 側」は 3 part ファイルの和集合として扱う (SONNET_MD_LIST)。
SONNET_MD_1="plugins/agent-discipline/hooks/prompts/always-sonnet-1.md"
SONNET_MD_2="plugins/agent-discipline/hooks/prompts/always-sonnet-2.md"
SONNET_MD_3="plugins/agent-discipline/hooks/prompts/always-sonnet-3.md"
HOOKS_JSON="plugins/agent-discipline/hooks/hooks.json"
DISCIPLINE_FABLE_MD="plugins/agent-discipline/hooks/prompts/discipline-fable.md"
DISCIPLINE_SONNET_MD="plugins/agent-discipline/hooks/prompts/discipline-sonnet.md"
# PR2 (agent-discipline 0.21.0) で discipline-opus.md (Opus 系向け) を新設し、チェック 4 の
# 対象を分業規律 3 ファイルに拡張した。
DISCIPLINE_OPUS_MD="plugins/agent-discipline/hooks/prompts/discipline-opus.md"
SUBAGENT_MD="plugins/agent-discipline/hooks/prompts/subagent-rules.md"

# チェック 2 前提検証 (#186) で使う、 期待される type:agent entry 数。
EXPECTED_AGENT_ENTRIES=4

overall_fail=0

# --- pre-flight: リポジトリルートから実行されているか / jq が使えるか ---
for f in "$FABLE_MD" "$SONNET_MD_1" "$SONNET_MD_2" "$SONNET_MD_3" "$HOOKS_JSON" "$DISCIPLINE_FABLE_MD" "$DISCIPLINE_SONNET_MD" "$DISCIPLINE_OPUS_MD" "$SUBAGENT_MD"; do
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

echo "== check 1: rule ID set (always-fable.md <-> always-sonnet-{1,2,3}.md union) =="

extract_rule_ids() {
  # $1 = file path。 `<!-- rule:<id> -->` から <id> だけを取り出しソートする。
  # 重複マーカーはここでは除去しない (sort -u ではなく sort のみ): 単一ファイル内の重複検査
  # (check_no_intra_file_dup_ids) が uniq -d でこの重複保持出力に依存するため。
  grep -Eo '<!-- rule:[a-zA-Z0-9_-]+ -->' "$1" 2>/dev/null \
    | sed -e 's/<!-- rule:\(.*\) -->/\1/' \
    | sort
}

# $1 = extract_rule_ids の出力ファイル (sort 済み・重複保持)、$2 = エラーメッセージ用の
# ファイル表示名。同一ファイル内で rule ID マーカーが重複していないかを検査する (#236 P2
# codex review 指摘: part 間 pairwise 検査は自分自身と比較しないため、単一 part ファイル内の
# 重複マーカーは検出できない。旧実装の単一ファイル検査は重複を保持したまま fable 側と diff
# していたため検出できていたが、3 part の和集合化 (sort -u) は重複を無音で吸収してしまう)。
# 重複が無ければ 0、あれば FAIL メッセージ (重複 ID 列挙付き) を出力して 1 を返す。
check_no_intra_file_dup_ids() {
  uniq -d "$1" > "$WORKDIR/dup_intra.txt" 2>/dev/null
  if [ -s "$WORKDIR/dup_intra.txt" ]; then
    echo "FAIL: $2 内で rule ID マーカーが重複しています (同一ファイル内に同じ <!-- rule:<id> --> が複数回書かれています):" >&2
    sed 's/^/  - /' "$WORKDIR/dup_intra.txt" >&2
    return 1
  fi
  return 0
}

# $1 $2 = 比較する 2 つのソート済み ID ファイル、$3 $4 = エラーメッセージ用のファイル表示名。
# 共通要素が無ければ 0、あれば FAIL メッセージを出力して 1 を返す。
check_no_dup_ids() {
  comm -12 "$1" "$2" > "$WORKDIR/dup_pair.txt" 2>/dev/null
  if [ -s "$WORKDIR/dup_pair.txt" ]; then
    echo "FAIL: $3 と $4 に重複する rule ID があります (part 分割は rule 境界で行う契約に反する):" >&2
    sed 's/^/  - /' "$WORKDIR/dup_pair.txt" >&2
    return 1
  fi
  return 0
}

extract_rule_ids "$FABLE_MD" > "$WORKDIR/ids_fable.txt"
extract_rule_ids "$SONNET_MD_1" > "$WORKDIR/ids_sonnet_1.txt"
extract_rule_ids "$SONNET_MD_2" > "$WORKDIR/ids_sonnet_2.txt"
extract_rule_ids "$SONNET_MD_3" > "$WORKDIR/ids_sonnet_3.txt"

if [ ! -s "$WORKDIR/ids_fable.txt" ] || [ ! -s "$WORKDIR/ids_sonnet_1.txt" ] || [ ! -s "$WORKDIR/ids_sonnet_2.txt" ] || [ ! -s "$WORKDIR/ids_sonnet_3.txt" ]; then
  echo "ERROR: <!-- rule:<id> --> 形式のコメントが 1 件も抽出できませんでした ($FABLE_MD / $SONNET_MD_1 / $SONNET_MD_2 / $SONNET_MD_3)。ファイル欠如またはコメント形式の変更の可能性があります。" >&2
  exit 1
fi

# 各 part ファイル単体で rule ID マーカーが重複していないことを検査する (#236 P2)。
# part 間 pairwise 検査 (このすぐ後) は自分自身とは比較しないため単一ファイル内の重複を
# 検出できず、後続の和集合化 (sort -u) は重複を無音で吸収してしまう。両方より前に検査する。
check1_intra_dup_fail=0
check_no_intra_file_dup_ids "$WORKDIR/ids_sonnet_1.txt" "$SONNET_MD_1" || check1_intra_dup_fail=1
check_no_intra_file_dup_ids "$WORKDIR/ids_sonnet_2.txt" "$SONNET_MD_2" || check1_intra_dup_fail=1
check_no_intra_file_dup_ids "$WORKDIR/ids_sonnet_3.txt" "$SONNET_MD_3" || check1_intra_dup_fail=1

if [ "$check1_intra_dup_fail" -eq 0 ]; then
  echo "OK: no intra-file duplicate rule IDs within always-sonnet-{1,2,3}.md"
else
  overall_fail=1
fi

# part 間で rule ID が重複しないことを検査する (和集合化によって重複が隠れるため、
# 和集合を作る前に全 2 組をペアワイズに検査する)。
check1_dup_fail=0
check_no_dup_ids "$WORKDIR/ids_sonnet_1.txt" "$WORKDIR/ids_sonnet_2.txt" "$SONNET_MD_1" "$SONNET_MD_2" || check1_dup_fail=1
check_no_dup_ids "$WORKDIR/ids_sonnet_1.txt" "$WORKDIR/ids_sonnet_3.txt" "$SONNET_MD_1" "$SONNET_MD_3" || check1_dup_fail=1
check_no_dup_ids "$WORKDIR/ids_sonnet_2.txt" "$WORKDIR/ids_sonnet_3.txt" "$SONNET_MD_2" "$SONNET_MD_3" || check1_dup_fail=1

if [ "$check1_dup_fail" -eq 0 ]; then
  echo "OK: no duplicate rule IDs across always-sonnet-{1,2,3}.md"
else
  overall_fail=1
fi

cat "$WORKDIR/ids_sonnet_1.txt" "$WORKDIR/ids_sonnet_2.txt" "$WORKDIR/ids_sonnet_3.txt" | sort -u > "$WORKDIR/ids_sonnet.txt"

if [ ! -s "$WORKDIR/ids_sonnet.txt" ]; then
  echo "ERROR: always-sonnet-{1,2,3}.md の rule ID 和集合が空です。" >&2
  exit 1
fi

if diff -u "$WORKDIR/ids_fable.txt" "$WORKDIR/ids_sonnet.txt" > "$WORKDIR/ids_diff.txt" 2>&1; then
  id_count=$(wc -l < "$WORKDIR/ids_fable.txt" | tr -d ' ')
  echo "OK: rule ID sets match (${id_count} IDs)"
else
  echo "FAIL: rule ID sets differ between $FABLE_MD and always-sonnet-{1,2,3}.md union" >&2
  cat "$WORKDIR/ids_diff.txt" >&2
  overall_fail=1
fi

# ============================================================================
# チェック 2: hooks.json 4 entries の共通ブロック一致
# ============================================================================

echo ""
echo "== check 2: hooks.json 4 type:agent entries common block =="

# --- 前提検証 (#186): type:agent entry 数と prompt 非空を、抽出・正規化・比較より前に検証する ---
#     jq が非 0 で終了した場合 (.hooks.PreToolUse 欠落など)、 $(...) はその exit status を
#     引き継ぐ (代入のみの simple command の $? は最後に実行した command substitution の
#     exit status になる、 POSIX 規定) ため、 ここで明示的に検査する。 検査を怠ると jq 失敗時に
#     変数が空文字列のまま後続の `-ne` / `-n` 比較に渡り、 `[ "" -ne 4 ]` が
#     "integer expression expected" で失敗して if 自体が偽扱いになり、 前提検証が
#     silent に skip されて偽の "OK" が出力される (fail-closed 契約違反)。
check_jq_status() {
  # $1 = 直前の jq 呼び出しの exit status、 $2 = エラーメッセージに含める処理名
  if [ "$1" -ne 0 ]; then
    echo "ERROR: hooks.json の構造解析に失敗しました (${2}、jq exit status: $1)。.hooks.PreToolUse の構造が想定と異なる可能性があります。" >&2
    exit 1
  fi
}

agent_entry_count=$(jq '[.hooks.PreToolUse[0].hooks[] | select(.type == "agent")] | length' "$HOOKS_JSON")
check_jq_status "$?" "type:agent entry 数の取得"

case "$agent_entry_count" in
  ''|*[!0-9]*)
    echo "ERROR: jq の出力 (${agent_entry_count}) が type:agent entry 数として数値ではありません。hooks.json の構造解析に失敗した可能性があります。" >&2
    exit 1
    ;;
esac

if [ "$agent_entry_count" -ne "$EXPECTED_AGENT_ENTRIES" ]; then
  echo "ERROR: hooks.json の type:agent entry 数が ${EXPECTED_AGENT_ENTRIES} と一致しません (実際: ${agent_entry_count})。entry の追加・削除が無いか確認してください。" >&2
  exit 1
fi

empty_prompt_ifs=$(jq -r '
  [
    .hooks.PreToolUse[0].hooks[]
    | select(.type == "agent")
    | select((.prompt | type) != "string" or (.prompt | length) == 0)
    | .if
  ] | join(", ")
' "$HOOKS_JSON")
check_jq_status "$?" "prompt 非空チェック"

if [ -n "$empty_prompt_ifs" ]; then
  echo "ERROR: hooks.json の type:agent entry のうち prompt が空または文字列以外の entry があります: ${empty_prompt_ifs}" >&2
  exit 1
fi

echo "OK: 前提検証 (type:agent entry 数 = ${EXPECTED_AGENT_ENTRIES}、全 entry の prompt が非空文字列)"

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

# --- 除去系の実在検証 (#187、 #185 のチェック 3 と対象ブロックを共有) ---
#     norm_b (Closes 検証 Step の除去) が対象とするブロックそのものを、 除去 (norm_b_pr_create_only
#     の呼び出し) より前に抽出し、 実在を確認する。 このブロックはチェック 3 の入力としても
#     再利用する (#185 のチェック 3 の対象ブロックと同一であるため)。
sed -n '/^## Step 3: Closes 検証/,/^## Step 4: 返り値/{/^## Step 4: 返り値/!p}' "$WORKDIR/raw_pr_create.txt" > "$WORKDIR/step3_block.txt"
if [ ! -s "$WORKDIR/step3_block.txt" ]; then
  echo "ERROR: gh pr create entry の prompt から '## Step 3: Closes 検証' ブロックが抽出できませんでした (norm_b の除去対象が実在しません)。見出しの変更または削除の可能性があります。" >&2
  exit 1
fi

#     norm_c (PR 固有の判定原則追加文の除去) が対象とする文言も、 除去 (norm_c_pr_only の
#     呼び出し) より前に PR 系 2 entries それぞれへの実在を確認する。
PR_JUDGMENT_ADDITION='。 PR body で commit/discussion 経由でユーザ承認が明示されている文脈 (= 「ユーザの decision により A を採用」 等の明示宣言) は禁止対象外'
for name in raw_pr_create raw_pr_edit; do
  if ! grep -qF -- "$PR_JUDGMENT_ADDITION" "$WORKDIR/$name.txt"; then
    echo "ERROR: $name の prompt に PR 固有の判定原則追加文 (norm_c の除去対象) が見つかりません。文言の変更または削除の可能性があります。" >&2
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
#     除去対象の実在検証は上記 (raw 抽出直後の step3_block.txt 抽出 + 非空チェック) で
#     完了済みのため、 ここでは除去のみを行う (#187)。
norm_b_pr_create_only() {
  sed -e '/^## Step 3: Closes 検証/,/^## Step 4: 返り値/{/^## Step 4: 返り値/!d}' \
    -e 's/^## Step 4: 返り値/## Step 3: 返り値/' \
    -e 's/Step 2 (禁止カテゴリ判定) に該当なし/該当なし/' \
    -e 's/Step 2 に該当あり/該当あり/'
}

# (c) gh pr create / gh pr edit の両方が持つ PR 固有の判定原則追加文の除去。
#     除去対象の実在検証は上記 (raw 抽出直後の PR_JUDGMENT_ADDITION チェック) で完了済みのため、
#     ここでは同じ変数を使って除去のみを行う (実在検証と除去対象の文言をここで乖離させない)。
norm_c_pr_only() {
  sed "s#${PR_JUDGMENT_ADDITION}##"
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
# チェック 3: gh pr create entry の Step 3 (Closes 検証) ブロック構造チェック
# ============================================================================

echo ""
echo "== check 3: gh pr create Step 3 (Closes 検証) block structure =="

# 期待構造のソース・オブ・トゥルース (#185): Step 3 の判定手順のうち 7 要素を代表する
# 文字列のリスト。 README 等の外部文書は参照しない。 このスモークチェックは Step 3 ブロックが
# これらの文字列を含むかどうかのみを見る (判定ロジックの意味的な等価性は検証しない)。
cat > "$WORKDIR/step3_required_keywords.txt" <<'KEYWORDS_EOF'
<cwd>/.git
gitdir:
ref: refs/heads/
issue-<数字>
closing keyword
境界一致
fail-open で誘導層の
KEYWORDS_EOF

check3_fail=0
missing_keywords=""
while IFS= read -r kw; do
  [ -z "$kw" ] && continue
  if ! grep -qF -- "$kw" "$WORKDIR/step3_block.txt"; then
    missing_keywords="${missing_keywords}  - ${kw}
"
    check3_fail=1
  fi
done < "$WORKDIR/step3_required_keywords.txt"

if [ "$check3_fail" -ne 0 ]; then
  echo "FAIL: gh pr create の Step 3 ブロックに以下の必須キーワードが含まれていません (構造スモークチェックであり意味的等価性の検証ではない):" >&2
  printf '%s' "$missing_keywords" >&2
  overall_fail=1
else
  echo "OK: gh pr create の Step 3 ブロックが必須キーワードをすべて含んでいます"
fi

# ============================================================================
# チェック 4: 分業規律 3 ファイルの rule ID セット一致
# (discipline-fable.md <-> discipline-sonnet.md、discipline-fable.md <-> discipline-opus.md)
# ============================================================================

echo ""
echo "== check 4: discipline rule ID set (discipline-fable.md <-> discipline-sonnet.md / discipline-opus.md) =="

extract_rule_ids "$DISCIPLINE_FABLE_MD" > "$WORKDIR/ids_discipline_fable.txt"
extract_rule_ids "$DISCIPLINE_SONNET_MD" > "$WORKDIR/ids_discipline_sonnet.txt"
extract_rule_ids "$DISCIPLINE_OPUS_MD" > "$WORKDIR/ids_discipline_opus.txt"

if [ ! -s "$WORKDIR/ids_discipline_fable.txt" ] || [ ! -s "$WORKDIR/ids_discipline_sonnet.txt" ] || [ ! -s "$WORKDIR/ids_discipline_opus.txt" ]; then
  echo "ERROR: <!-- rule:<id> --> 形式のコメントが 1 件も抽出できませんでした ($DISCIPLINE_FABLE_MD / $DISCIPLINE_SONNET_MD / $DISCIPLINE_OPUS_MD)。ファイル欠如またはコメント形式の変更の可能性があります。" >&2
  exit 1
fi

check4_fail=0

if diff -u "$WORKDIR/ids_discipline_fable.txt" "$WORKDIR/ids_discipline_sonnet.txt" > "$WORKDIR/ids_discipline_diff_sonnet.txt" 2>&1; then
  discipline_id_count=$(wc -l < "$WORKDIR/ids_discipline_fable.txt" | tr -d ' ')
  echo "OK: discipline rule ID sets match (${discipline_id_count} IDs, fable <-> sonnet)"
else
  echo "FAIL: discipline rule ID sets differ between $DISCIPLINE_FABLE_MD and $DISCIPLINE_SONNET_MD" >&2
  cat "$WORKDIR/ids_discipline_diff_sonnet.txt" >&2
  check4_fail=1
fi

if diff -u "$WORKDIR/ids_discipline_fable.txt" "$WORKDIR/ids_discipline_opus.txt" > "$WORKDIR/ids_discipline_diff_opus.txt" 2>&1; then
  discipline_id_count=$(wc -l < "$WORKDIR/ids_discipline_fable.txt" | tr -d ' ')
  echo "OK: discipline rule ID sets match (${discipline_id_count} IDs, fable <-> opus)"
else
  echo "FAIL: discipline rule ID sets differ between $DISCIPLINE_FABLE_MD and $DISCIPLINE_OPUS_MD" >&2
  cat "$WORKDIR/ids_discipline_diff_opus.txt" >&2
  check4_fail=1
fi

if [ "$check4_fail" -ne 0 ]; then
  overall_fail=1
fi

# ============================================================================
# チェック 5: subagent-rules.md の rule ID サブセット検査
# (subagent-rules.md ⊆ always-sonnet-{1,2,3}.md の和集合)
# ============================================================================

echo ""
echo "== check 5: subagent rule ID subset (subagent-rules.md ⊆ always-sonnet-{1,2,3}.md union) =="

extract_rule_ids "$SUBAGENT_MD" > "$WORKDIR/ids_subagent.txt"

if [ ! -s "$WORKDIR/ids_subagent.txt" ]; then
  echo "ERROR: <!-- rule:<id> --> 形式のコメントが 1 件も抽出できませんでした ($SUBAGENT_MD)。マーカー形式の変更、または共有ルールの全削除の可能性があります。" >&2
  exit 1
fi

# always-sonnet-{1,2,3}.md の ID 集合の和集合 (チェック 1 で抽出・重複検査済みの
# ids_sonnet.txt) に対する片方向の包含検査。comm -23 (sorted 前提) で
# 「subagent 側にのみ存在する ID」を取り出す。extract_rule_ids は sort 済みの出力を
# 返すため、そのまま comm に渡せる。
comm -23 "$WORKDIR/ids_subagent.txt" "$WORKDIR/ids_sonnet.txt" > "$WORKDIR/ids_subagent_orphan.txt"

if [ -s "$WORKDIR/ids_subagent_orphan.txt" ]; then
  echo "FAIL: $SUBAGENT_MD に、always-sonnet-{1,2,3}.md の和集合に存在しない rule ID が含まれています (always 側での改名・削除への追従漏れ、または typo):" >&2
  sed 's/^/  - /' "$WORKDIR/ids_subagent_orphan.txt" >&2
  overall_fail=1
else
  subagent_id_count=$(wc -l < "$WORKDIR/ids_subagent.txt" | tr -d ' ')
  echo "OK: subagent rule IDs (${subagent_id_count} IDs) はすべて always-sonnet-{1,2,3}.md の和集合に存在します"
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
