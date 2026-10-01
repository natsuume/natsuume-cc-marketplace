"""agent-discipline の `rule:issue-claim` (連続 issue 解決時の排他制御) の契約テスト。

確保は claim comment の先着判定だけで確定し、確定後にラベルを付けてから作業 branch を
作る。step 1〜5 (早期判定・claim comment の投稿・3 秒待機・先着判定・ラベル付与) は claim
用のスクリプト (`skills/issue-start/scripts/claim-issue.sh`) が行い、その挙動は
``test_agent_discipline_claim_issue_script.py`` が検査する。本テストの検査対象は
always-3.md の `<!-- rule:issue-claim -->` から次の `<!-- rule:` マーカーの手前までの節と、
その手順を要約する issue-start skill・README・評価手順書である。always-3.md はスクリプトの
パスをプレースホルダ `{{CLAIM_ISSUE_SCRIPT_PATH}}` で書き、配送時に絶対パスへ置き換わる
(置き換えは ``test_agent_discipline_claim_script_injection.py`` が検査する)。

- 着手手順 (``IssueClaimStartProcedureTest``): issue-start skill の小節 1.1 で branch 名を
  決める → `'{{CLAIM_ISSUE_SCRIPT_PATH}}' <N> '<branch>'` を実行する → 作業 branch の作成、を
  この順に書き、スクリプトの exit code に従う (exit 0 で step 6 へ進む・exit 1 で撤退して
  1 行で報告する・exit 2 で停止して報告する・stdout に `label=failed` があればラベル付与の
  失敗を 1 行で報告する)。branch push で確保を確定する段階 (空 commit・即 push) と、
  排他基盤としての branch 名 uniqueness の説明が無い。
- 先着判定 (``IssueClaimArbitrationTest``): `session=` で自分の claim を識別すること、
  `ts=` ではなく GitHub が付ける順序 (`created_at`) で先着を決めることを説明として残す。
  セッション ID が未設定なら `uuidgen` の値を `--session-id` で渡し、同一セッション中は
  同じ値を使う。
- 後片付け (``IssueClaimCleanupTest``): 撤退はユーザーへの 1 行報告を必ず行う。先着判定で
  負けた (lost-race) 自分の claim はスクリプトが削除し、早期判定の撤退では削除するものが
  無い。exit 2 では自分の claim を残して停止する。着手中断は自分の claim comment の削除
  だけで、branch・draft PR・ラベルは残す。節のどこにも branch を削除するコマンドを置かない。
- 削除規律 (``IssueClaimDeletionDisciplineTest``): 他 session の claim comment / branch /
  ラベルを削除しない規律、`session=` による自他判別、撤退・着手中断のどちらでもラベルを
  削除しないこと、廃止した手順番号を参照しないこと、claim の反映を保証として書かないこと。
- 明示指示による再開 (``IssueClaimExplicitResumeTest``): ユーザのメッセージまたは handoff の
  文書が issue 番号か branch 名を挙げて継続を指示した場合 (明示指示) だけ step 6 から再開し、
  step 6 から再開する場合は、ラベルや他 session の claim comment があっても撤退も削除もしない。
  撤退しないこと・削除しないことを書く文は、すべて step 6 から再開する場合だけを条件にする。
  明示指示が無ければ、また明示指示があっても対応する branch が無ければ step 1 から実行する。
  issue 番号だけの明示指示では、issue-start skill の小節 1.1 の手順で branch を決め、同手順で
  新しい名前を決めた場合も step 1 から実行する。明示指示で step 1 から実行する場合は、step 1 の
  早期判定から issue-start skill の小節 1.2 に従い、明示指示が無い場合には小節 1.2 を使わせない。
- claim に埋め込む branch 名 (``IssueClaimExistingBranchNameTest``): 着手手順のうち
  issue-start skill の小節 1.1 を参照する項目 (スクリプトに渡す branch 名を決める項目) は、
  小節 1.1 の手順で見つけた既存の branch の名前を使い、無ければ同じ小節の手順 6 で命名規約に
  沿って決める (「無ければ」と「手順 6」の間に読点があってよい)。探索コマンドはその項目に
  書かず、branch 名のどこにでも一致する旧パターンを残さない。
- 作業 branch の用意 (``IssueClaimWorkBranchTest``): step 6 は `git fetch --prune origin` の後
  (失敗したら停止して報告)、prune 後の remote-tracking ref で同名 branch の有無を判定し、
  どちらにも無い・remote だけ・local だけ・両方の 4 通りで
  作成・`origin` からの switch・そのままの switch・fast-forward / 停止を分ける。draft PR は
  `rule:tdd-two-phase` に合わせ、Phase A の commit を push した後に作る。
- 関連文書 (``IssueClaimRelatedDocumentTest``): issue-start skill と README が branch push
  による確定・二段構成を説明しない。
- issue-start skill の pick-up 分岐 (``IssueStartPickUpTest``): 明示指示の有無で 2 つに分け、
  明示指示が無ければ既存の branch / PR があっても排他制御に進む。確認コマンドは local に
  だけある branch も確認し、明示指示で挙げられた branch 名は命名規約に依らずそのまま使う。
  明示指示が無い場合と issue 番号だけの明示指示は、小節 1.1 を参照する。明示指示で小節 1.1 が
  新しい名前を決めた場合は step 1 から実行し、step 1 から実行する条件を「どちらにも無い場合に
  限り」だけで書く文を残さない。
- issue-start skill の排他制御の参照 (``IssueStartClaimScriptReferenceTest``): セクション 2
  (`## 2.` の見出しから次の `## ` の手前まで) は claim 用のスクリプト (`claim-issue.sh`) に
  言及し、「本 skill 側では手順を複製しません」という文を含まない。
- 既存 branch の探し方 (``IssueStartBranchLookupTest``): issue-start skill の小節 1.1 は、
  `*/issue-<N>-*` で remote と local を探し (失敗したら投稿せず停止して報告)、同名を 1 つと
  数え、`-phase-b-wip` の補助 branch を除き、命名規約 (使える文字を英小文字・数字・ハイフンに
  限る) に合わない名前で停止し、その後で
  マージ済みの PR がある branch を除く。除くのは、branch が存在する側 (local / remote) の
  現在の commit (remote は `git ls-remote` の出力、local は `git rev-parse`) がすべて
  マージ済みの PR の head commit (`headRefOid`) のどれかと一致する場合だけで、どちらか一方でも
  一致しなければ候補に残す。候補が 1 つならその名前を使い、無ければ命名規約で
  決め (既存の branch と同名なら、どちらにも存在しない名前になるよう slug を変える)、複数
  なら投稿せず停止して確認する。見つけた名前は single quote で囲んで埋め込む。
- 明示指示で step 1 から実行するときの前の作業の残り (``IssueStartExplicitLeftoverTest``):
  issue-start skill の小節 1.2 は、明示指示があっても branch が無い場合と小節 1.1 の手順で
  新しい名前を決めた場合に適用する。スクリプトが exit 1 の `reason=label` /
  `reason=existing-claim` を返したら撤退せずに停止し、comment を REST GET
  (`gh api --paginate 'repos/{owner}/{repo}/issues/<N>/comments?per_page=100'`、`--slurp` を
  付けてもよい) の 1 回で取得してその結果から確認の対象と数値 comment id を決める (取得に
  失敗したらユーザに確認せず停止して報告する)。ラベルとすべての claim comment (`session=` の
  無いものと、自分のセッション ID と一致するものを含む) を、数値 comment id と本文を示して前の
  作業の残りかを `AskUserQuestion` で確認する。すべてが残りと確認された場合に限り、確認した
  数値 comment id を `--ignore-comment-id` で、確認したラベルがあれば `--confirmed-leftover` を
  付けてスクリプトを再実行し、1 件でも残りではないと答えたら、全体を残りではないとして投稿
  (再実行) せずに撤退する。確認の後に投稿された claim comment は確認の対象に入らないので除かない
  (`--ignore-comment-id` に渡さない)。確認の対象にしたラベルと claim comment は `session=` が
  自分と一致していても削除しない。明示指示が無い場合はこの手順を使わず、スクリプトの exit 1 に
  従って撤退する。確認していない claim や確認の後の claim を除く文 (`--ignore-comment-id` に
  渡す文を含む)、削除を許す文、明示指示が無い場合にこの手順を使わせる文を書かない。
- 緩い文の検出: 明示指示の段落と小節 1.2 の、書いてはならない文の検査は文書の文字列を受け取る
  関数にし、実ファイルと、緩い文を書き足した文書のコピーの両方に使う。コピーでは、書き足した
  文を検出することを検査する。
- 評価基準 (``IssueClaimEvaluationTest``): `docs/discipline-evaluation.md` の issue-claim の
  評価基準が、claim 用のスクリプトの実行と exit code に従うことを成功経路とし、grep anchor
  (観測コマンド) にスクリプトの実行 (`claim-issue.sh`) を含める。明示指示による再開と、既存
  branch の探し方で停止する経路を Pass として扱い、明示指示で step 1 から実行した場合の Pass
  定義が小節 1.2 の手順と一致する。

文章全体の一致は検査しない。手順を識別するコマンド・識別子の有無と出現順序を検査し、
言い回しは実装側で選べる。always-3.md・issue-start skill・評価手順書のパスは
``IssueClaimTestCase`` のクラス属性で差し替えられる (置換予定の本文を一時ファイルで検査するため)。
"""

from __future__ import annotations

import re
import unittest
from collections.abc import Callable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "agent-discipline"
ALWAYS_3 = PLUGIN_DIR / "hooks" / "prompts" / "always-3.md"
ISSUE_START_SKILL = PLUGIN_DIR / "skills" / "issue-start" / "SKILL.md"
PLUGIN_README = PLUGIN_DIR / "README.md"
REPO_README = ROOT / "README.md"
EVALUATION_DOC = ROOT / "docs" / "discipline-evaluation.md"

ISSUE_CLAIM_MARKER = "<!-- rule:issue-claim -->"
RULE_MARKER_PREFIX = "<!-- rule:"
START_PROCEDURE_HEADING = "### 着手手順"
REPO_README_PLUGIN_HEADING = "## agent-discipline"

# claim 用のスクリプトの呼び出し。パスは配送時にプレースホルダから絶対パスへ置き換わる。
CLAIM_SCRIPT_INVOCATION = "'{{CLAIM_ISSUE_SCRIPT_PATH}}' <N> '<branch>'"

# スクリプトの exit code と stdout に従うことを示す文の要素 (空白を除去した文に照合する)。
EXIT_CODE_REQUIREMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("exit 0 なら step 6 へ進む", ("exit 0", "step 6")),
    ("exit 1 なら撤退して 1 行で報告する", ("exit 1", "撤退", "1 行", "報告")),
    ("exit 2 なら停止してユーザーに報告する", ("exit 2", "停止", "報告")),
    (
        "stdout に label=failed があればラベル付与の失敗を 1 行で報告する",
        ("label=failed", "ラベル", "1 行", "報告"),
    ),
)

# branch push で確保を確定する段階と、それを排他基盤とする説明の語。
BRANCH_PUSH_CONFIRMATION_PHRASES = (
    "--allow-empty",
    "git push -u",
    "二段",
    "branch 名 uniqueness",
    "排他基盤 2",
)

# 撤退・着手中断の段落の先頭に置く太字ラベル。
WITHDRAWAL_LABEL = "**撤退**"
INTERRUPTION_LABEL = "**着手中断**"

# branch を削除するコマンド。撤退・着手中断のどちらの後片付けにも置かない。
BRANCH_DELETE_COMMANDS = (
    "git push origin --delete",
    "git push origin :",
    "git branch -D",
)

# 着手中断で残すもの。
INTERRUPTION_KEPT_ARTIFACTS = ("branch", "draft PR", "ラベル", "残す")

# 削除規律で branch も削除対象に含めていた語。
MERGED_CLEANUP_PHRASE = "自分の claim comment と branch のみ削除"

# 撤退・着手中断のどちらでもラベルを削除しないことを示す語。
LABEL_KEPT_PHRASE = "ラベルは削除しない"

# 「よくある誤操作と回避」が参照してはならない、確定段階を含んでいた手順番号。
STALE_STEP_REFERENCES = ("step 2-5", "step 1-3", "step 1, 4, 5")

# 他 session の claim が一覧に反映されることを保証として書く語。
VISIBILITY_GUARANTEE_PHRASE = "必ず観測できる"

OTHER_SESSION_TARGETS = "他 session の claim comment / branch / ラベル"
OWNERSHIP_CRITERION = "「自分の claim か」の判定基準"

# issue-start skill が排他制御の手順として書かない語。
SKILL_BRANCH_PUSH_PHRASE = "branch push による確定"

# README が排他系の仕組みとして書かない語。
README_BRANCH_PUSH_PHRASES = (
    "branch push (確定的排他)",
    "二段構成",
    "即 push で確定的排他",
    "branch push 排他",
    "push 成功時のみラベル付与",
)

# 着手手順の step 6 (作業 branch の用意) の項目の先頭。下位項目を含めて 1 項目とする。
WORK_BRANCH_STEP_MARKER = "6. **作業 branch"

# issue 番号のパターンで既存の branch を探すコマンド (remote と local)。パターンは branch 名の
# 先頭の `<prefix>/issue-<N>-` に絞り、slug に issue 番号を含む別 issue の branch を除く。
EXISTING_BRANCH_SEARCH_COMMANDS = (
    "git ls-remote --heads origin '*/issue-<N>-*'",
    "git branch --list '*/issue-<N>-*'",
)

# branch 名のどこにでも一致していた旧パターン。
LOOSE_BRANCH_PATTERN = "'*issue-<N>-*'"

# issue-start skill の既存 branch の探し方の小節と、それを参照するときの節番号。
ISSUE_START_LOOKUP_HEADING = "### 1.1 既存 branch の探し方"
LOOKUP_SECTION_REFERENCE = "1.1"

# 着手手順の段階と、その段階を識別する語 (手順に書く順)。
START_ORDER = (
    ("issue-start skill の小節 1.1 で branch 名を決める", LOOKUP_SECTION_REFERENCE),
    ("claim 用のスクリプトの実行", CLAIM_SCRIPT_INVOCATION),
    ("作業 branch の作成", "git switch -c"),
)

# 既存の branch が無い場合に、小節 1.1 の手順 6 (命名規約と、local / remote のどちらにも存在
# しない名前にする規定) で名前を決めることを示す表記 (空白を除去した文に照合する)。「無ければ」
# と手順 6 が同じ文の中でこの順に並ぶことを求める (「無ければ、同手順 6 で」のように間に読点を
# 挟んでよい)。
NO_BRANCH_THEN_LOOKUP_STEP_6 = re.compile(r"無(?:け|い)[^。]*手順6")

# 読点の有無に依らず、branch 名を決める項目の命名の検査が満たされるべき正しい文。
STEP_2_NAMING_SENTENCES = (
    "issue-start skill セクション 1.1 の手順で既存の branch を探して見つかった名前を使い、"
    "無ければ同手順 6 で次の規約に沿って決める",
    "issue-start skill セクション 1.1 の手順で既存の branch を探して見つかった名前を使い、"
    "無ければ、同手順 6 で次の規約に沿って決める",
)

# issue-start skill の、明示指示で step 1 から実行するときの前の作業の残りを確かめる小節と、
# それを参照するときの節番号。
ISSUE_START_LEFTOVER_HEADING = "### 1.2 明示指示で step 1 から実行するときの前の作業の残り"
LEFTOVER_SECTION_REFERENCE = "1.2"

# 小節 1.2 で comment を取得する REST GET (空白を除去した文に照合する。`--slurp` を付けてもよい)。
COMMENTS_REST_GET = re.compile(
    r"ghapi--paginate(?:--slurp)?'repos/\{owner\}/\{repo\}/issues/<N>/comments\?per_page=100'"
)

# 小節 1.2 で、確認の後に claim 用のスクリプトをもう一度実行することを示す表記 (空白を除去した
# 文に照合する)。
RERUN = re.compile(r"再実行|実行し直|再度実行|もう一度実行|再び実行")

# claim comment を投稿しない (スクリプトを再実行しない) ことを示す表記 (空白を除去した文に照合する)。
NOT_POSTED = re.compile(
    r"投稿(?:せず|しない|しません)|再実行(?:せず|しない|しません)|実行し直さ(?:ず|ない)"
)

# 数値 comment id を `--ignore-comment-id` で渡す (除く) ことを肯定で書く表記 (空白を除去した
# 文に照合する)。「渡さず」「渡しません」「指定しない」は含めない。
PASSED_AS_IGNORED = re.compile(
    r"--ignore-comment-id`?(?:に|で|として)?"
    r"(?:渡(?:す|し(?!ません)|して)|指定(?:す|し(?!ない|ません))|付け(?:る|て|ます))"
)

# 除かないことを示す否定の表記 (空白を除去した文に照合する)。「通常どおり」だけでは満たさない。
NOT_EXCLUDED = re.compile(
    r"除(?:か(?:ず|ない|れない|れず|れません)|きません)"
    r"|除外(?:せず|しない|しません|されない|されません)"
)

# 確認の後に投稿された claim comment を、除かずに判定することを示す要素 (空白を除去した文に
# 照合する)。
LATER_CLAIM_REQUIREMENTS: tuple[Requirement, ...] = (
    re.compile(r"確認(?:の|した)後に投稿"),
    "claim comment",
    NOT_EXCLUDED,
)

# 「通常どおり」だけで、除かないことを書いていない文 (確認の後の claim の検査を満たさない)。
USUAL_JUDGEMENT_ONLY_SENTENCE = "確認の後に投稿された claim comment は通常どおり判定します"

# 確認していない claim comment、または確認の後に投稿された claim comment を指す表記 (空白を
# 除去した文に照合する)。
UNCONFIRMED_CLAIM = re.compile(
    r"確認(?:していない|されていない|しなかった|の(?:対象に)?(?:入らない|ない))"
    r"|確認(?:の|した)後に投稿"
)

# 除くことを肯定で書く表記 (空白を除去した文に照合する)。「除かず」「除かれません」「除きません」
# は含めない。
AFFIRMATIVE_EXCLUSION = re.compile(r"除(?:き(?!ません)|く|いて|外し(?!ない|ません)|外する)")

# 削除することを肯定で書く表記 (空白を除去した文に照合する)。「削除しない」「削除しません」
# 「削除せず」は含めない。
AFFIRMATIVE_DELETION = re.compile(r"削除(?:します|して|する|でき|可|を許)")

# 小節 1.2 の経路の撤退で削除してよい、この経路で自分が投稿した claim comment を指す表記
# (空白を除去した文に照合する)。
OWN_POSTED_CLAIM = re.compile(r"この経路で(?:自分が)?投稿したclaimcomment")

# 削除の対象を限ることを示す表記 (空白を除去した文に照合する)。「だけでなく」「のみならず」は
# 対象を広げる表記なので含めない。
ONLY = re.compile(r"(?:だけ|のみ)(?!でなく|ならず)")

# 手順を使わないことを示す表記 (空白を除去した文に照合する)。
NOT_USED = re.compile(r"使わ(?:ず|ない)|使いません")

# 小節 1.2 に書き足すと、確認していない claim comment や確認の後に投稿された claim comment を
# 除くことになる文。
LOOSE_UNCONFIRMED_EXCLUSION_SENTENCES = (
    "step 4 の先着判定では、確認していない claim comment も除きます。",
    "確認の後に投稿された claim comment も、先着判定の対象から除きます。",
    "確認の後に投稿された claim comment の数値 comment id も `--ignore-comment-id` に渡します。",
)

# 小節 1.2 に書き足すと、claim comment やラベルの削除を許すことになる文。
LOOSE_DELETION_SENTENCES = (
    "確認した残りのラベルと claim comment は削除します。",
    "撤退するときは、確認の対象にした claim comment のうち、`session=` の値が自分のセッション"
    " ID と一致するものも削除します。",
    "撤退するときは、この経路で自分が投稿した claim comment だけでなく、確認した claim comment"
    " も削除します。",
)

# 小節 1.2 に書き足すと、明示指示が無い場合にこの手順を使わせることになる文。
LOOSE_NO_INSTRUCTION_SKILL_SENTENCES = (
    "明示指示が無い場合も、ラベルや claim comment が見つかったらこの手順でユーザに確認します。",
)

# step 1 から実行することを示す表記 (空白を除去した文に照合する)。「step 1-5 を経ずに」の
# ような範囲の表記では満たされないよう、「step 1 から実行」の並びを求める。
STEP_1_START = re.compile(r"step1から実行")

# step 6 から再開することを示す表記 (空白を除去した文に照合する)。
STEP_6_RESUME = re.compile(r"step6から再開")

# step 6 から再開する場合に触れながら、条件を広げる表記 (空白を除去した文に照合する)。
STEP_6_WIDENED = re.compile(r"step6から再開(?:する場合に限らず|しない場合)")

# 撤退しないこと・削除しないことを示す表記 (空白を除去した文に照合する)。「撤退も削除もしない」
# の形と、撤退だけ・削除だけに触れる文も含める。
WITHDRAWAL_OR_DELETION_EXEMPTION = re.compile(
    r"(?:撤退|削除)(?:も|は)?(?:せず|しない|しません)"
)

# step 1 から実行する経路に触れる表記 (空白を除去した文に照合する)。「step 1-5 を経ずに」の
# ような範囲の表記は含めない。
STEP_1_MENTION = re.compile(r"step1(?![-0-9])")

# 明示指示の段落の先頭に置く太字ラベル。書き足す文をこの直後に置いて、段落のコピーを作る。
EXPLICIT_RESUME_LABEL = "**明示指示による再開**: "

# 明示指示の段落に書き足すと、撤退しないこと・削除しないことを step 6 から再開する場合に
# 限らずに書いたことになる文 (「撤退も削除もしない」の形、撤退だけに触れる文、step 1 の経路も
# 併記した文)。
LOOSE_EXEMPTION_SENTENCES = (
    "明示指示がある場合は、ラベルや他セッション ID の claim comment が残っていても撤退も削除もしない。",
    "明示指示がある場合は、ラベルや他セッション ID の claim comment が残っていても撤退しない。",
    "step 6 から再開する場合と step 1 から実行する場合は、ラベルや他セッション ID の claim"
    " comment が残っていても撤退せず、削除もしない。",
    "step 6 から再開する場合に限らず、ラベルや他セッション ID の claim comment が残っていても"
    "撤退も削除もしない。",
    "step 6 から再開しない場合も、ラベルや他セッション ID の claim comment が残っていても"
    "撤退しない。",
)

# 明示指示の段落に書き足すと、明示指示が無い場合に小節 1.2 を使わせることになる文。
LOOSE_NO_INSTRUCTION_REFERENCE_SENTENCES = (
    "明示指示が無い場合も、step 1 でラベルや claim comment が見つかったら issue-start skill"
    " セクション 1.2 の手順に従う。",
)

# step 1 から実行する条件を、branch が local / remote のどちらにも無い場合だけに限る表記
# (空白を除去した文に照合する)。
MISSING_BRANCH_ONLY = re.compile(r"(?:どちら|いずれ)にも(?:無|な)い(?:場合)?に限(?:り|って)")

# マージ済みの PR がある branch を確かめるコマンドの要素。
MERGED_PR_CHECK_PHRASES = ("gh pr list --head", "--state merged")

# マージ済みの PR の head commit を取り出す gh の引数。
MERGED_PR_HEAD_FIELD = "--json headRefOid"

# マージ済みの PR の head commit と比べる、branch の現在の commit を得るコマンド (remote は
# 手順 1 の `git ls-remote` の出力、local は `git rev-parse`)。空白を除去した文に照合し、
# 各側の語とコマンドが同じ読点区間の中でこの順に並ぶことを求める (`git ls-remote` の中の
# 「remote」だけで remote 側の要件を満たさないようにするため)。
CURRENT_COMMIT_SOURCE_REQUIREMENTS = (
    re.compile(r"remote[^、。]*手順1[^、。]*gitls-remote"),
    re.compile(r"local[^、。]*gitrev-parse"),
)

# 契約改訂手順が作る補助 branch の接尾辞。
WIP_BRANCH_SUFFIX = "-phase-b-wip"

# 命名規約に合わない名前で、何も変更せず停止してユーザに確認することを示す要素 (空白を
# 除去した文に照合する)。
CONVENTION_STOP_REQUIREMENTS: tuple[Requirement, ...] = (
    "命名規約",
    re.compile(r"合わな"),
    re.compile(r"変更(?:せず|しない)"),
    "停止",
    "AskUserQuestion",
)

# パターンに複数の branch が一致したときに、何も変更せず停止してユーザに確認することを
# 示す要素 (空白を除去した文に照合する)。
MULTIPLE_MATCH_REQUIREMENTS: tuple[Requirement, ...] = (
    "複数",
    re.compile(r"変更(?:せず|しない)"),
    "停止",
    "AskUserQuestion",
)

# local と remote の両方にある同名の branch を 1 つと数えることを示す要素 (空白を除去した文に
# 照合する)。
SAME_NAME_COUNTED_ONCE_REQUIREMENTS: tuple[Requirement, ...] = (
    "同名",
    re.compile(r"1つと数え"),
)

# 明示指示の定義を書く段落を識別する語 (両方を含む段落を定義の段落とする)。
EXPLICIT_INSTRUCTION = "明示指示"
EXPLICIT_DEFINITION_MARKERS = (EXPLICIT_INSTRUCTION, "handoff")

# 明示指示の定義に書く要素。
EXPLICIT_DEFINITION_PHRASES = (
    "ユーザ",
    "メッセージ",
    "handoff",
    "文書",
    "issue 番号",
    "branch 名",
    "継続",
)

# 明示指示が無いことを示す表記 (空白を除去した文に照合する)。
NO_EXPLICIT_INSTRUCTION = re.compile(r"明示指示が(?:無|な)(?:い|く|けれ)")

# 他 session の claim comment を指す表記 (空白を除去した文に照合する)。
OTHER_SESSION = re.compile(r"他(?:セッション|session)")

# step 6 で remote の状態を取り込むコマンド。remote で削除された branch の remote-tracking
# ref を残さず、同名 branch の有無を remote の現状で判定するため prune する。
WORK_BRANCH_FETCH_COMMAND = "git fetch --prune origin"

# step 6 の分岐時の停止で使ってはならない、local / remote の branch を変更するコマンド。
DIVERGENCE_FORBIDDEN_COMMANDS = (
    "git reset",
    "--force",
    "push -f",
    "git branch -D",
    "git push origin --delete",
)

# step 6 にあった、draft PR を実装より先に作る旧い順序の記述。
DRAFT_PR_FIRST_PHRASE = "draft PR 作成 → 実装"

ISSUE_START_PICK_UP_HEADING = "## 1. pick-up 分岐"
ISSUE_START_CLAIM_HEADING = "## 2. 排他制御の参照"
# issue-start skill のセクション 2 を、見出しの文言に依らず取り出すための見出しの先頭。
ISSUE_START_SECTION_2_PREFIX = "## 2."
# セクション 2 が書かない、手順を常時注入側に置いて skill 側に複製しないとしていた文。
SKILL_NO_DUPLICATION_PHRASE = "本 skill 側では手順を複製しません"

# pick-up 分岐で既存の作業状態を確認するコマンド。remote の branch と、local にだけある
# branch の両方を確認する。
PICK_UP_BRANCH_CHECK_COMMANDS = (
    "git ls-remote --heads origin",
    "git branch --list",
)

# 明示指示がある場合の分岐に書く要素 (空白を除去した文に照合する)。
EXPLICIT_BRANCH_REQUIREMENTS: tuple[tuple[str, tuple[Requirement, ...]], ...] = (
    (
        "挙げられた branch 名を命名規約に依らずそのまま使う",
        ("branch名", "命名規約", "そのまま"),
    ),
    (
        "local / remote のどちらにも無ければ step 1 から実行する",
        (re.compile(r"local.*remote.*(?:どちら|いずれ)にも(?:無|な)"), "step1"),
    ),
    (
        "issue 番号だけが挙げられた場合は、セクション 1.1 の手順で branch を決める",
        ("issue番号だけ", LOOKUP_SECTION_REFERENCE),
    ),
)

# 小節 1.1 の手順が新しい名前を決めた場合に step 1 から実行することを示す要素 (空白を除去
# した文に照合する)。手順で除外した branch は存在し続けるため、「branch が無い」場合とは
# 別に書く。
NEW_NAME_STARTS_FROM_STEP_1_REQUIREMENTS: tuple[Requirement, ...] = (
    LOOKUP_SECTION_REFERENCE,
    "新しい名前",
    STEP_1_START,
)

# issue-start skill の pick-up 分岐にあった、明示指示に触れない再開の分岐の語。
UNCONDITIONAL_RESUME_PHRASE = "branch / open PR が既に存在し"

# 排他制御の参照が、新規着手の場合だけに排他制御を限っていた語。
NEW_START_ONLY_PHRASE = "新規着手と判定した場合"

EVALUATION_ISSUE_CLAIM_ROW = "| issue-claim 手順の遵守 |"
EVALUATION_ISSUE_CLAIM_NOTE = "※ issue-claim 手順の遵守における経路別 Pass 定義"
# 評価基準の表の列 (指標名 | 対応 rule | 適用機会 | Pass | Violation | grep anchor) の Pass 列と
# grep anchor 列。
EVALUATION_PASS_COLUMN = 3
EVALUATION_GREP_ANCHOR_COLUMN = 5
# 経路別 Pass 定義のうち、成功経路の項目の先頭の太字ラベル。
EVALUATION_SUCCESS_ROUTE_LABEL = "**成功経路**"
# claim 用のスクリプトのファイル名 (評価の観測コマンドに含める)。
CLAIM_SCRIPT_NAME = "claim-issue.sh"

HEADING_PATTERN = re.compile(r"^#{1,6} ")
LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+\.) ")
TOP_LEVEL_LIST_ITEM_PATTERN = re.compile(r"^(?:[-*+]|\d+\.) ")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def display_path(path: Path) -> str:
    """失敗メッセージ用のパス (リポジトリ内ならリポジトリ直下からの相対パス)。"""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def strip_whitespace(text: str) -> str:
    """空白文字をすべて除去する (行の折り返しで分断された出現も照合するため)。"""
    return "".join(text.split())


def rule_block(text: str, marker: str) -> str:
    """`marker` から次の `<!-- rule:` マーカーの手前までを返す (マーカー行を除く)。"""
    start = text.find(marker)
    if start < 0:
        return ""
    body_start = start + len(marker)
    end = text.find(RULE_MARKER_PREFIX, body_start)
    return text[body_start:] if end < 0 else text[body_start:end]


def markdown_section(text: str, heading: str) -> str:
    """見出し行 `heading` の次の行から、同じか上位レベルの次の見出しの手前までを返す。"""
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines(keepends=True)
    start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if start is None:
            if stripped == heading or stripped.startswith(heading + " "):
                start = index
            continue
        if HEADING_PATTERN.match(stripped):
            next_level = len(stripped) - len(stripped.lstrip("#"))
            if next_level <= level:
                return "".join(lines[start + 1 : index])
    if start is None:
        return ""
    return "".join(lines[start + 1 :])


def text_before_subheading(text: str) -> str:
    """`text` のうち、最初の見出し行の手前までを返す (節の本文から小節を除くため)。"""
    lines = text.splitlines(keepends=True)
    end = next(
        (index for index, line in enumerate(lines) if HEADING_PATTERN.match(line)),
        len(lines),
    )
    return "".join(lines[:end])


def labeled_paragraph(text: str, label: str) -> str:
    """行頭が `label` の行から、次の行頭太字ラベル・見出しの手前までを返す。"""
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(label)), None
    )
    if start is None:
        return ""
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if HEADING_PATTERN.match(line) or line.startswith("**"):
            break
        end += 1
    return "\n".join(lines[start:end])


def list_item_containing(text: str, marker: str) -> str:
    """`marker` を含む箇条書き 1 項目分 (次の項目・空行の手前まで) を返す。"""
    lines = text.splitlines()
    target = next(
        (index for index, line in enumerate(lines) if marker in line), None
    )
    if target is None:
        return ""
    start = target
    while start > 0 and not LIST_ITEM_PATTERN.match(lines[start]):
        if not lines[start - 1].strip():
            break
        start -= 1
    end = target + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip() or LIST_ITEM_PATTERN.match(line):
            break
        end += 1
    return "\n".join(lines[start:end])


def top_level_list_item_containing(text: str, marker: str) -> str:
    """`marker` を含む行から、次のトップレベル箇条書き項目・空行の手前までを返す。

    インデントされた下位項目は同じ項目の一部として含める。
    """
    lines = text.splitlines()
    target = next(
        (index for index, line in enumerate(lines) if marker in line), None
    )
    if target is None:
        return ""
    end = target + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip() or TOP_LEVEL_LIST_ITEM_PATTERN.match(line):
            break
        end += 1
    return "\n".join(lines[target:end])


def top_level_list_items(text: str) -> list[str]:
    """トップレベルの箇条書き項目を、インデントされた下位項目・継続行を含めて返す。"""
    items: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if TOP_LEVEL_LIST_ITEM_PATTERN.match(line):
            if current is not None:
                items.append("\n".join(current))
            current = [line]
        elif current is not None and line.strip() and line[:1].isspace():
            current.append(line)
        elif current is not None:
            items.append("\n".join(current))
            current = None
    if current is not None:
        items.append("\n".join(current))
    return items


def paragraph_containing(text: str, markers: tuple[str, ...]) -> str:
    """空行で区切った段落のうち、`markers` をすべて含む最初の段落を返す。"""
    for paragraph in re.split(r"\n\s*\n", text):
        stripped = strip_whitespace(paragraph)
        if all(strip_whitespace(marker) in stripped for marker in markers):
            return paragraph
    return ""


def sentences(text: str) -> list[str]:
    """「。」・箇条書き項目の境目・空行で区切った文を返す。"""
    parts = re.split(r"。|\n(?=\s*(?:[-*+]|\d+\.)\s)|\n\s*\n", text)
    return [part for part in parts if part.strip()]


def list_items_following(text: str, lead: str) -> str:
    """行頭が `lead` の行の後に続く箇条書き (空行を挟んでよい) を、次の空行の手前まで返す。"""
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(lead)), None
    )
    if start is None:
        return ""
    index = start + 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    end = index
    while end < len(lines) and lines[end].strip():
        end += 1
    return "\n".join(lines[index:end])


def table_cells(row: str) -> list[str]:
    """Markdown の表の行を、前後の `|` を除いてセルに分ける。"""
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


Requirement = str | re.Pattern[str]


def satisfies(sentence: str, requirement: Requirement) -> bool:
    """空白を除去した `sentence` が、語を含むか正規表現に一致するか。"""
    stripped = strip_whitespace(sentence)
    if isinstance(requirement, str):
        return strip_whitespace(requirement) in stripped
    return requirement.search(stripped) is not None


def describe(requirement: Requirement) -> str:
    return requirement if isinstance(requirement, str) else requirement.pattern


def satisfies_all(sentence: str, requirements: tuple[Requirement, ...]) -> bool:
    """`sentence` が `requirements` (語または正規表現) をすべて満たすか。"""
    return all(satisfies(sentence, requirement) for requirement in requirements)


def explicit_resume_paragraph_of(always_3_text: str) -> str:
    """always-3.md の文字列から、rule:issue-claim 節の明示指示の定義を書く段落を返す。"""
    return paragraph_containing(
        rule_block(always_3_text, ISSUE_CLAIM_MARKER), EXPLICIT_DEFINITION_MARKERS
    )


def leftover_section_of(skill_text: str) -> str:
    """issue-start skill の文字列から、小節 1.2 の本文を返す。"""
    return markdown_section(skill_text, ISSUE_START_LEFTOVER_HEADING)


def unbounded_withdrawal_exemptions(always_3_text: str) -> list[str]:
    """明示指示の段落のうち、撤退しないこと・削除しないことを、step 6 から再開する場合に
    限らずに書いた文を返す (step 6 から再開する場合を条件にしない文、step 6 に触れながら
    条件を広げる文、step 1 の経路も併記した文)。"""
    return [
        sentence
        for sentence in sentences(explicit_resume_paragraph_of(always_3_text))
        if satisfies(sentence, WITHDRAWAL_OR_DELETION_EXEMPTION)
        and (
            not satisfies(sentence, STEP_6_RESUME)
            or satisfies(sentence, STEP_6_WIDENED)
            or satisfies(sentence, STEP_1_MENTION)
        )
    ]


def leftover_references_without_explicit_instruction(always_3_text: str) -> list[str]:
    """明示指示の段落のうち、明示指示が無い場合に小節 1.2 を使わせる文を返す。"""
    return [
        sentence
        for sentence in sentences(explicit_resume_paragraph_of(always_3_text))
        if satisfies(sentence, NO_EXPLICIT_INSTRUCTION)
        and satisfies(sentence, LEFTOVER_SECTION_REFERENCE)
    ]


def unconfirmed_claim_exclusions(skill_text: str) -> list[str]:
    """小節 1.2 のうち、確認していない claim comment や確認の後に投稿された claim comment を
    除く文 (`--ignore-comment-id` に渡す文を含む) を返す。"""
    return [
        sentence
        for sentence in sentences(leftover_section_of(skill_text))
        if satisfies(sentence, UNCONFIRMED_CLAIM)
        and (
            satisfies(sentence, AFFIRMATIVE_EXCLUSION)
            or satisfies(sentence, PASSED_AS_IGNORED)
        )
    ]


def leftover_deletion_permissions(skill_text: str) -> list[str]:
    """小節 1.2 のうち、claim comment やラベルの削除を許す文を返す。この経路で自分が投稿した
    claim comment だけを削除すると書く文 (ラベルに触れないもの) は除く。"""
    return [
        sentence
        for sentence in sentences(leftover_section_of(skill_text))
        if satisfies(sentence, AFFIRMATIVE_DELETION)
        and not (
            satisfies(sentence, OWN_POSTED_CLAIM)
            and satisfies(sentence, ONLY)
            and not satisfies(sentence, "ラベル")
        )
    ]


def leftover_procedure_without_explicit_instruction(skill_text: str) -> list[str]:
    """小節 1.2 のうち、明示指示が無い場合を書きながら、この手順を使わないと書いていない文を
    返す (明示指示が無い場合にこの手順を使わせる文)。"""
    return [
        sentence
        for sentence in sentences(leftover_section_of(skill_text))
        if satisfies(sentence, NO_EXPLICIT_INSTRUCTION)
        and not satisfies(sentence, NOT_USED)
    ]


def with_text_inserted(text: str, anchor: str, addition: str) -> str:
    """`text` の `anchor` の最初の出現の直後に `addition` を足したコピーを返す。"""
    index = text.index(anchor) + len(anchor)
    return text[:index] + addition + text[index:]


class IssueClaimTestCase(unittest.TestCase):
    """`rule:issue-claim` の節を取り出す helper と、失敗時に該当箇所を示す assert。"""

    always_3_path: Path = ALWAYS_3
    issue_start_skill_path: Path = ISSUE_START_SKILL
    evaluation_doc_path: Path = EVALUATION_DOC

    def label(self, scope: str = "") -> str:
        base = f"{display_path(self.always_3_path)} の rule:issue-claim 節"
        return f"{base} の {scope}" if scope else base

    def issue_claim_block(self) -> str:
        block = rule_block(read(self.always_3_path), ISSUE_CLAIM_MARKER)
        self.assert_scope_found(self.label(), block, f"`{ISSUE_CLAIM_MARKER}` が無い")
        return block

    def assert_scope_found(self, label: str, scope: str, hint: str) -> None:
        if not scope.strip():
            self.fail(f"{label}: 検査対象の箇所が見つからない ({hint})")

    def assert_phrase_present(self, label: str, scope: str, phrase: str) -> None:
        if strip_whitespace(phrase) not in strip_whitespace(scope):
            self.fail(f"{label}: 「{phrase}」が無い")

    def assert_phrase_absent(self, label: str, scope: str, phrase: str) -> None:
        """空白を除去した全文で `phrase` の不在を確認し、残っていれば該当行を示す。"""
        if strip_whitespace(phrase) not in strip_whitespace(scope):
            return
        hits = [
            line.strip()[:120] for line in scope.splitlines() if phrase in line
        ]
        joined = " / ".join(hits) if hits else "改行をまたいで出現"
        self.fail(f"{label}: 「{phrase}」が残っている: {joined}")

    def assert_some_sentence(
        self, label: str, scope: str, requirements: tuple[Requirement, ...]
    ) -> None:
        """`requirements` (語または正規表現) をすべて満たす文が `scope` にあることを確認する。"""
        if any(
            all(satisfies(sentence, requirement) for requirement in requirements)
            for sentence in sentences(scope)
        ):
            return
        wanted = "」「".join(describe(requirement) for requirement in requirements)
        self.fail(f"{label}: 「{wanted}」をすべて満たす文が無い")

    def assert_no_violations(self, label: str, violations: list[str], what: str) -> None:
        """検査関数が返した文 (書いてはならない文) が無いことを確認する。"""
        if violations:
            joined = " / ".join(sentence.strip()[:120] for sentence in violations)
            self.fail(f"{label}: {what}がある: {joined}")

    def assert_loose_sentences_detected(
        self,
        label: str,
        check: Callable[[str], list[str]],
        text: str,
        anchor: str,
        addition_format: str,
        loose_sentences: tuple[str, ...],
    ) -> None:
        """`text` の `anchor` の直後に緩い文を 1 つずつ足したコピーを作り、`check` がその文を
        検出することを確認する (`addition_format` の `{}` に緩い文を入れて足す)。"""
        if anchor not in text:
            self.fail(f"{label}: 緩い文を足す位置の「{anchor.strip()}」が無い")
        for loose in loose_sentences:
            with self.subTest(loose=loose[:60]):
                copy = with_text_inserted(text, anchor, addition_format.format(loose))
                wanted = strip_whitespace(loose.rstrip("。"))
                if not any(wanted in strip_whitespace(found) for found in check(copy)):
                    self.fail(f"{label}: 緩い文を足したコピーを検査が拒否しない: {loose}")

    def start_procedure(self) -> str:
        section = markdown_section(self.issue_claim_block(), START_PROCEDURE_HEADING)
        self.assert_scope_found(
            self.label(START_PROCEDURE_HEADING),
            section,
            f"`{START_PROCEDURE_HEADING}` 節が無い",
        )
        return section

    def procedure_step(self, marker: str, scope: str) -> str:
        """着手手順のうち `marker` で始まる項目を、下位項目を含めて返す。"""
        section = markdown_section(self.issue_claim_block(), START_PROCEDURE_HEADING)
        item = top_level_list_item_containing(section, marker)
        self.assert_scope_found(
            self.label(scope),
            item,
            f"`{START_PROCEDURE_HEADING}` 節に「{marker}」で始まる項目が無い",
        )
        return item

    def branch_name_step(self) -> str:
        """着手手順のうち、issue-start skill の小節 1.1 を参照する項目 (claim 用のスクリプトに
        渡す branch 名を決める項目) を、下位項目を含めて返す。"""
        section = markdown_section(self.issue_claim_block(), START_PROCEDURE_HEADING)
        item = next(
            (
                item
                for item in top_level_list_items(section)
                if satisfies(item, "issue-start") and satisfies(item, LOOKUP_SECTION_REFERENCE)
            ),
            "",
        )
        self.assert_scope_found(
            self.label("branch 名を決める項目"),
            item,
            f"`{START_PROCEDURE_HEADING}` 節に issue-start skill の"
            f" {LOOKUP_SECTION_REFERENCE} を参照する項目が無い",
        )
        return item

    def work_branch_step(self) -> str:
        """着手手順の step 6 (作業 branch の用意) の項目を、下位項目を含めて返す。"""
        return self.procedure_step(WORK_BRANCH_STEP_MARKER, "step 6 の項目")


class IssueClaimStartProcedureTest(IssueClaimTestCase):
    """着手手順の段階と順序、claim 用のスクリプトの exit code に従うこと。"""

    def test_marker_appears_exactly_once(self) -> None:
        """`<!-- rule:issue-claim -->` マーカーが always-3.md に 1 つだけある。"""
        self.assertEqual(
            1,
            read(self.always_3_path).count(ISSUE_CLAIM_MARKER),
            f"{display_path(self.always_3_path)}: {ISSUE_CLAIM_MARKER} の出現回数",
        )

    def test_steps_appear_in_order(self) -> None:
        """issue-start skill の小節 1.1 で branch 名を決める → claim 用のスクリプトを
        `'{{CLAIM_ISSUE_SCRIPT_PATH}}' <N> '<branch>'` で実行する → 作業 branch の作成、の順に
        書く。

        各段階を識別する語の、着手手順節での最初の出現位置で順序を比べる。
        """
        section = self.start_procedure()
        label = self.label(START_PROCEDURE_HEADING)
        positions = []
        for step, phrase in START_ORDER:
            with self.subTest(step=step):
                self.assert_phrase_present(label, section, phrase)
            positions.append((step, section.find(phrase)))
        for (earlier, earlier_at), (later, later_at) in zip(positions, positions[1:]):
            with self.subTest(earlier=earlier, later=later):
                # 語が改行で分かれていると存在の検査は通り、位置は取れない。順序を判定
                # できないまま skip すると誤った順序が見逃されるため、失敗させる。
                missing = [step for step, at in ((earlier, earlier_at), (later, later_at)) if at < 0]
                self.assertEqual(
                    [],
                    missing,
                    f"{label}: 段階を識別する語がそのままの形で無く、順序を判定できない",
                )
                self.assertLess(
                    earlier_at,
                    later_at,
                    f"{label}: 「{earlier}」が「{later}」より前に無い",
                )

    def test_script_is_invoked_in_the_start_procedure(self) -> None:
        """着手手順は claim 用のスクリプトを `'{{CLAIM_ISSUE_SCRIPT_PATH}}' <N> '<branch>'`
        の形で実行する (パスと branch 名を single quote で囲む)。"""
        self.assert_phrase_present(
            self.label(START_PROCEDURE_HEADING), self.start_procedure(), CLAIM_SCRIPT_INVOCATION
        )

    def test_exit_codes_are_followed(self) -> None:
        """スクリプトの結果に従う: exit 0 なら step 6 へ進み、exit 1 なら撤退して 1 行で報告し、
        exit 2 なら停止してユーザーに報告し、stdout に `label=failed` があればラベル付与の失敗を
        1 行で報告する。"""
        section = self.start_procedure()
        for name, requirements in EXIT_CODE_REQUIREMENTS:
            with self.subTest(requirement=name):
                self.assert_some_sentence(
                    self.label(START_PROCEDURE_HEADING), section, requirements
                )

    def test_work_branch_starts_from_the_latest_default_branch(self) -> None:
        """作業 branch は最新の default branch を起点に作る。中断した別 issue の branch に
        残る commit を、次の issue の branch が引き継がないようにするため。

        step 6 の項目は `6. **作業 branch` の行から下位項目までとする。
        """
        item = self.work_branch_step()
        label = self.label("step 6 の項目")
        for phrase in (WORK_BRANCH_FETCH_COMMAND, "git switch -c", "origin/<default-branch>"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, item, phrase)

    def test_no_branch_push_confirmation_stage(self) -> None:
        """branch push で確保を確定する段階 (空 commit・即 push) と、branch 名
        uniqueness を排他基盤とする説明が無い。"""
        block = self.issue_claim_block()
        for phrase in BRANCH_PUSH_CONFIRMATION_PHRASES:
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(self.label(), block, phrase)


class IssueClaimArbitrationTest(IssueClaimTestCase):
    """先着判定の説明 (判定そのものは claim 用のスクリプトが行う) とセッション ID。"""

    def test_own_claim_is_identified_by_session(self) -> None:
        """自分の claim を claim comment の `session=` の値で識別することを説明する。"""
        self.assert_some_sentence(
            self.label(),
            self.issue_claim_block(),
            ("session=", "自分の claim", re.compile(r"識別|判定|一致")),
        )

    def test_first_claim_follows_github_order_not_ts(self) -> None:
        """先着は `ts=` (自己申告) ではなく GitHub が付ける順序 (`created_at`) で決まることを
        説明する。"""
        self.assert_some_sentence(
            self.label(),
            self.issue_claim_block(),
            (
                "ts=",
                re.compile(r"created_at|GitHub"),
                re.compile(r"先着|判定"),
                re.compile(r"使わ(?:ない|ず)|ではなく|用いない|(?:依|よ)らず"),
            ),
        )

    def test_missing_session_id_is_generated_and_passed(self) -> None:
        """環境変数のセッション ID が未設定なら `uuidgen` で生成した値を `--session-id` で
        スクリプトに渡し、同一セッション中は同じ値を使う。"""
        self.assert_some_sentence(
            self.label(),
            self.issue_claim_block(),
            (
                re.compile(r"未設定|(?:無|な)い場合|(?:無|な)ければ"),
                "uuidgen",
                "--session-id",
                re.compile(r"同じ値|同一の値"),
            ),
        )


class IssueClaimCleanupTest(IssueClaimTestCase):
    """撤退と着手中断の後片付け。"""

    def paragraph(self, label_text: str) -> str:
        paragraph = labeled_paragraph(self.issue_claim_block(), label_text)
        self.assert_scope_found(
            self.label(f"{label_text} の段落"),
            paragraph,
            f"行頭が `{label_text}` の段落が無い",
        )
        return paragraph

    def test_withdrawal_reports_in_one_line(self) -> None:
        """撤退したらユーザーに撤退理由を 1 行で報告する。"""
        paragraph = self.paragraph(WITHDRAWAL_LABEL)
        label = self.label(f"{WITHDRAWAL_LABEL} の段落")
        for phrase in ("1 行", "報告"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, paragraph, phrase)

    def test_lost_race_claim_is_deleted_by_the_script(self) -> None:
        """先着判定で負けた (lost-race) 場合の自分の claim comment は、claim 用のスクリプトが
        削除する。"""
        self.assert_some_sentence(
            self.label(f"{WITHDRAWAL_LABEL} の段落"),
            self.paragraph(WITHDRAWAL_LABEL),
            (
                "スクリプト",
                re.compile(r"lost-race|先着"),
                re.compile(r"削除(?:する|します|して|済|される|されて)"),
            ),
        )

    def test_early_withdrawal_has_nothing_to_delete(self) -> None:
        """早期判定の撤退ではまだ claim comment を投稿していないので、削除するものが無い。"""
        self.assert_some_sentence(
            self.label(f"{WITHDRAWAL_LABEL} の段落"),
            self.paragraph(WITHDRAWAL_LABEL),
            ("早期判定", "削除", re.compile(r"(?:無|な)(?:い|く|し)|不要")),
        )

    def test_stop_keeps_the_own_claim(self) -> None:
        """スクリプトが exit 2 を返したら、自分の claim を残して停止する (ユーザーが状態を
        確認できるように削除しない)。"""
        self.assert_some_sentence(
            self.label(),
            self.issue_claim_block(),
            (
                "exit 2",
                "自分の claim",
                re.compile(r"残(?:し|す|る|った)|削除(?:せず|しない)"),
                "停止",
            ),
        )

    def test_block_has_no_branch_deletion(self) -> None:
        """撤退・着手中断のどちらの後片付けでも branch を削除しない。確保の判定に
        branch を使わないため、削除する理由が無い。"""
        block = self.issue_claim_block()
        for phrase in BRANCH_DELETE_COMMANDS:
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(self.label(), block, phrase)

    def test_interruption_deletes_only_the_claim_comment(self) -> None:
        """着手中断の後片付けは自分の claim comment の削除だけで、branch・draft PR・
        ラベルは残す。"""
        paragraph = self.paragraph(INTERRUPTION_LABEL)
        label = self.label(f"{INTERRUPTION_LABEL} の段落")
        for phrase in ("claim comment", "削除", *INTERRUPTION_KEPT_ARTIFACTS):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, paragraph, phrase)


class IssueClaimDeletionDisciplineTest(IssueClaimTestCase):
    """他 session の成果物を消さない規律と自他判別。"""

    def test_other_sessions_artifacts_are_never_deleted(self) -> None:
        """他 session の claim comment / branch / ラベルを削除しない。"""
        item = list_item_containing(self.issue_claim_block(), OTHER_SESSION_TARGETS)
        label = self.label("他 session の削除禁止の項目")
        self.assert_scope_found(label, item, f"「{OTHER_SESSION_TARGETS}」を含む項目が無い")
        self.assert_phrase_present(label, item, "削除しない")

    def test_ownership_is_judged_by_session_id(self) -> None:
        """「自分の claim か」は claim comment の `session=` 値と自分のセッション ID の
        一致で判別し、一致しない・`session=` が無い claim は削除しない。"""
        item = top_level_list_item_containing(
            self.issue_claim_block(), OWNERSHIP_CRITERION
        )
        label = self.label("自他判別の項目")
        self.assert_scope_found(label, item, f"「{OWNERSHIP_CRITERION}」を含む項目が無い")
        for phrase in ("session=", "セッション ID と一致", "削除禁止"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, item, phrase)

    def test_cleanup_discipline_keeps_branches_and_labels(self) -> None:
        """削除規律は branch を削除対象に含めず、撤退・着手中断のどちらでもラベルを
        削除しない。"""
        block = self.issue_claim_block()
        self.assert_phrase_absent(self.label(), block, MERGED_CLEANUP_PHRASE)
        self.assert_phrase_present(self.label(), block, LABEL_KEPT_PHRASE)

    def test_pitfalls_do_not_reference_removed_steps(self) -> None:
        """「よくある誤操作と回避」などが、確定段階を含んでいた手順番号を参照しない。"""
        block = self.issue_claim_block()
        for phrase in STALE_STEP_REFERENCES:
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(self.label(), block, phrase)

    def test_visibility_is_written_as_a_premise(self) -> None:
        """他 session の claim が一覧に反映されることを保証として書かない。"""
        self.assert_phrase_absent(
            self.label(), self.issue_claim_block(), VISIBILITY_GUARANTEE_PHRASE
        )


class IssueClaimExplicitResumeTest(IssueClaimTestCase):
    """明示指示による再開の規定。"""

    def explicit_resume_paragraph(self) -> str:
        paragraph = paragraph_containing(
            self.issue_claim_block(), EXPLICIT_DEFINITION_MARKERS
        )
        self.assert_scope_found(
            self.label("明示指示の段落"),
            paragraph,
            "「明示指示」と「handoff」を含む段落が無い",
        )
        return paragraph

    def test_explicit_instruction_is_defined(self) -> None:
        """明示指示を、ユーザのメッセージまたは handoff の文書が issue 番号か branch 名を
        挙げて継続を指示していること、と定義する。"""
        self.assert_some_sentence(
            self.label("明示指示の段落"),
            self.explicit_resume_paragraph(),
            (EXPLICIT_INSTRUCTION, *EXPLICIT_DEFINITION_PHRASES),
        )

    def test_explicit_instruction_resumes_from_step_6(self) -> None:
        """明示指示がある場合は、claim の手順を経ずに step 6 から再開する。"""
        self.assert_some_sentence(
            self.label("明示指示の段落"),
            self.explicit_resume_paragraph(),
            ("step 6", "再開"),
        )

    def test_explicit_instruction_neither_withdraws_nor_deletes(self) -> None:
        """明示指示による再開では、ラベルや他 session の claim comment が残っていても撤退
        せず、それらを削除もしない。"""
        self.assert_some_sentence(
            self.label("明示指示の段落"),
            self.explicit_resume_paragraph(),
            (
                "ラベル",
                "claim comment",
                OTHER_SESSION,
                re.compile(r"撤退(?:せず|しない)"),
                re.compile(r"削除(?:も)?(?:せず|しない)"),
            ),
        )

    def test_without_explicit_instruction_starts_from_step_1(self) -> None:
        """明示指示が無ければ、既存の branch / draft PR があっても step 1 から実行する。"""
        self.assert_some_sentence(
            self.label("明示指示の段落"),
            self.explicit_resume_paragraph(),
            (NO_EXPLICIT_INSTRUCTION, "branch", "step 1"),
        )

    def test_explicit_instruction_without_branch_starts_from_step_1(self) -> None:
        """明示指示があっても対応する branch が無ければ、step 1 から実行する。branch を
        新しく作るには claim を要するため。"""
        self.assert_some_sentence(
            self.label("明示指示の段落"),
            self.explicit_resume_paragraph(),
            (
                EXPLICIT_INSTRUCTION,
                re.compile(r"branchが(?:無|な|存在しな)"),
                "step 1",
            ),
        )


    def test_issue_number_only_refers_to_the_lookup_section(self) -> None:
        """issue 番号だけの明示指示で branch を決める手順として、issue-start skill の
        既存 branch の探し方 (セクション 1.1) を参照する。"""
        self.assert_some_sentence(
            self.label("明示指示の段落"),
            self.explicit_resume_paragraph(),
            ("issue 番号だけ", "issue-start", LOOKUP_SECTION_REFERENCE),
        )

    def test_new_name_from_the_lookup_starts_from_step_1(self) -> None:
        """issue 番号だけの明示指示で、セクション 1.1 の手順が新しい名前を決めた場合も
        step 1 から実行する。手順で除外した branch は存在し続けるため、「branch が無い」
        場合の規定だけでは claim を経ずに新しい branch を作りうる。"""
        self.assert_some_sentence(
            self.label("明示指示の段落"),
            self.explicit_resume_paragraph(),
            NEW_NAME_STARTS_FROM_STEP_1_REQUIREMENTS,
        )

    def test_withdrawal_exemption_is_for_the_step_6_resume(self) -> None:
        """撤退しないこと・削除しないことを書く文は、すべて step 6 から再開する場合だけを条件に
        する (「撤退も削除もしない」の形や撤退だけに触れる文も含め、step 6 から再開する場合を
        条件にしない文と、step 1 の経路も併記した文を置かない。明示指示で step 1 から実行する
        場合にも及ぶと読めないようにするため)。"""
        self.assert_no_violations(
            self.label("明示指示の段落"),
            unbounded_withdrawal_exemptions(read(self.always_3_path)),
            "step 6 から再開する場合に限らずに撤退・削除をしないと書く文",
        )

    def test_loose_exemptions_are_detected(self) -> None:
        """撤退しないこと・削除しないことを step 6 から再開する場合に限らずに書いた文
        (「撤退も削除もしない」の形、撤退だけに触れる文、step 1 の経路も併記した文) を明示
        指示の段落に足したコピーを、同じ検査が拒否する。"""
        self.assert_loose_sentences_detected(
            self.label("明示指示の段落のコピー"),
            unbounded_withdrawal_exemptions,
            read(self.always_3_path),
            EXPLICIT_RESUME_LABEL,
            "{}",
            LOOSE_EXEMPTION_SENTENCES,
        )

    def test_step_1_route_follows_the_skill_from_the_early_check(self) -> None:
        """明示指示で step 1 から実行する場合は、step 1 の早期判定から issue-start skill の
        小節 1.2 の手順に従う。小節 1.2 を参照する文は、他 session の claim comment や、何かが
        見つかった場合を条件にしない (自分と同じ session ID の claim comment だけが残っている
        場合も小節 1.2 で確認し、早期判定の comment の取得も小節 1.2 に従うため)。"""
        label = self.label("明示指示の段落")
        paragraph = self.explicit_resume_paragraph()
        self.assert_some_sentence(
            label,
            paragraph,
            (
                EXPLICIT_INSTRUCTION,
                STEP_1_START,
                "早期判定",
                "issue-start",
                LEFTOVER_SECTION_REFERENCE,
            ),
        )
        for sentence in sentences(paragraph):
            if not satisfies(sentence, LEFTOVER_SECTION_REFERENCE):
                continue
            with self.subTest(sentence=sentence.strip()[:60]):
                for condition in (OTHER_SESSION, re.compile(r"見つか")):
                    if satisfies(sentence, condition):
                        self.fail(
                            f"{label}: 小節 1.2 を参照する文が「{describe(condition)}」を"
                            f"条件にしている: {sentence.strip()[:120]}"
                        )

    def test_leftover_section_is_not_used_without_explicit_instruction(self) -> None:
        """明示指示の段落に、明示指示が無い場合に小節 1.2 を使わせる文を置かない。"""
        self.assert_no_violations(
            self.label("明示指示の段落"),
            leftover_references_without_explicit_instruction(read(self.always_3_path)),
            "明示指示が無い場合に小節 1.2 を使わせる文",
        )

    def test_loose_no_instruction_references_are_detected(self) -> None:
        """明示指示が無い場合に小節 1.2 を使わせる文を明示指示の段落に足したコピーを、同じ
        検査が拒否する。"""
        self.assert_loose_sentences_detected(
            self.label("明示指示の段落のコピー"),
            leftover_references_without_explicit_instruction,
            read(self.always_3_path),
            EXPLICIT_RESUME_LABEL,
            "{}",
            LOOSE_NO_INSTRUCTION_REFERENCE_SENTENCES,
        )


class IssueClaimExistingBranchNameTest(IssueClaimTestCase):
    """claim 用のスクリプトに渡して claim に埋め込む branch 名を、issue-start skill の手順で
    見つけた既存の branch から決める。"""

    def assert_step_sentence(self, requirements: tuple[Requirement, ...]) -> None:
        self.assert_some_sentence(
            self.label("branch 名を決める項目"), self.branch_name_step(), requirements
        )

    def test_existing_branch_refers_to_the_lookup_section(self) -> None:
        """既存の branch は issue-start skill のセクション 1.1 の手順で探し、見つかった
        名前を使う。"""
        self.assert_step_sentence(("issue-start", LOOKUP_SECTION_REFERENCE, "名前"))

    def test_no_match_follows_the_naming_convention(self) -> None:
        """既存の branch が無ければ、命名規約で branch 名を決める。"""
        self.assert_step_sentence((re.compile(r"無(?:け|い)"), "規約"))

    def test_no_match_follows_step_6_of_the_lookup(self) -> None:
        """既存の branch が無ければ、issue-start skill のセクション 1.1 の手順 6 で名前を
        決める (命名規約だけで決めると、手順で除外したマージ済みの branch と同じ名前になり、
        step 6 がその branch に switch しうるため。手順 6 は local / remote のどちらにも
        存在しない名前になるよう slug を変える)。"""
        self.assert_step_sentence((LOOKUP_SECTION_REFERENCE, NO_BRANCH_THEN_LOOKUP_STEP_6))

    def test_step_6_naming_allows_a_comma(self) -> None:
        """手順 6 で名前を決める検査は、「無ければ」と「手順 6」の間に読点がある正しい文でも
        満たされる。"""
        for sentence in STEP_2_NAMING_SENTENCES:
            with self.subTest(sentence=sentence):
                self.assertTrue(
                    satisfies_all(
                        sentence, (LOOKUP_SECTION_REFERENCE, NO_BRANCH_THEN_LOOKUP_STEP_6)
                    ),
                    f"branch 名の命名の検査が正しい文を拒否する: {sentence}",
                )

    def test_search_commands_are_not_duplicated(self) -> None:
        """探索コマンドは issue-start skill の 1 か所に置き、branch 名を決める項目には
        書かない。"""
        item = self.branch_name_step()
        for phrase in ("git ls-remote", "git branch --list"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(self.label("branch 名を決める項目"), item, phrase)

    def test_loose_pattern_is_absent(self) -> None:
        """branch 名のどこにでも一致する旧パターンを rule:issue-claim の節に残さない。"""
        self.assert_phrase_absent(self.label(), self.issue_claim_block(), LOOSE_BRANCH_PATTERN)


class IssueClaimWorkBranchTest(IssueClaimTestCase):
    """step 6 の作業 branch の用意と draft PR の順序。"""

    def assert_step_sentence(self, requirements: tuple[Requirement, ...]) -> None:
        self.assert_some_sentence(
            self.label("step 6 の項目"), self.work_branch_step(), requirements
        )

    def test_fetch_failure_stops_and_reports(self) -> None:
        """`git fetch --prune origin` が失敗したら、remote の状態を判定できないので停止して
        ユーザに報告する。"""
        self.assert_step_sentence((WORK_BRANCH_FETCH_COMMAND, "失敗", "停止", "報告"))

    def test_branch_presence_is_judged_by_pruned_tracking_refs(self) -> None:
        """同名の branch の有無は、prune した後の remote-tracking ref (`origin/<branch>`)
        で判定する。remote で削除された branch の古い ref を「remote だけにある」と
        判定し、マージ済みの branch を追跡しないため。"""
        self.assert_step_sentence(
            (
                re.compile(r"prune(?:した)?後"),
                "remote-tracking ref",
                "origin/<branch>",
                "判定",
            )
        )

    def test_missing_branch_is_created_from_the_default_branch(self) -> None:
        """同名の branch がどこにも無ければ、最新の default branch を起点に作る。"""
        self.assert_step_sentence(
            (
                re.compile(r"無い|無け|ない:|存在しない"),
                "git switch -c",
                "--no-track",
                "origin/<default-branch>",
            )
        )

    def test_remote_only_branch_is_switched_from_origin(self) -> None:
        """同名の branch が remote だけにあれば、`origin` の branch を起点に switch して
        再開する (remote の推測に依存しない)。"""
        self.assert_step_sentence(
            ("remote だけ", "git switch -c <branch> --track origin/<branch>")
        )

    def test_local_only_branch_is_switched_as_is(self) -> None:
        """同名の branch が local だけにあれば (一度も push していない)、remote と比べずに
        switch してそのまま再開する。"""
        self.assert_step_sentence(("local だけ", "git switch <branch>", "そのまま"))

    def test_local_branch_case_is_split_by_remote_presence(self) -> None:
        """remote の有無を問わない「local にある」場合を置かない (remote が無いと比較先が
        無いため、local だけ・両方の 2 つに分ける)。"""
        self.assert_phrase_absent(
            self.label("step 6 の項目"), self.work_branch_step(), "local にある"
        )

    def test_branch_on_both_sides_is_switched(self) -> None:
        """同名の branch が local と remote の両方にあれば、`git switch <branch>` の後に
        remote と比べる。"""
        self.assert_step_sentence(("両方", "git switch <branch>"))

    def test_older_local_branch_is_fast_forwarded(self) -> None:
        """両方にある場合に local の branch が remote より古ければ、fast-forward で追いつく。"""
        self.assert_step_sentence(("両方", "local が古", "git merge --ff-only"))

    def test_newer_local_branch_is_kept(self) -> None:
        """両方にある場合に local の branch が remote より新しければ、未 push の commit を
        保ったまま再開する。"""
        self.assert_step_sentence(("両方", "新し"))

    def test_diverged_branch_stops_without_changes(self) -> None:
        """両方にある場合に local と remote が分岐していれば、どちらも変更せずに停止して
        ユーザに報告する。"""
        self.assert_step_sentence(
            (
                "両方",
                "分岐",
                "local",
                "remote",
                re.compile(r"変更(?:せず|しない)"),
                "停止",
                "報告",
            )
        )

    def test_step_has_no_branch_rewriting_commands(self) -> None:
        """step 6 に local / remote の branch を書き換える・消すコマンドを置かない。"""
        item = self.work_branch_step()
        for phrase in DIVERGENCE_FORBIDDEN_COMMANDS:
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(self.label("step 6 の項目"), item, phrase)

    def test_draft_pr_follows_tdd_two_phase(self) -> None:
        """draft PR は `rule:tdd-two-phase` に合わせ、Phase A の commit を push した後に
        作る。既存の draft PR があればそれを使う。"""
        item = self.work_branch_step()
        self.assert_phrase_present(self.label("step 6 の項目"), item, "rule:tdd-two-phase")
        self.assert_step_sentence((re.compile(r"PhaseA.*push.*後.*draftPR"),))
        self.assert_step_sentence(("既存", "draft PR"))

    def test_draft_pr_first_order_is_absent(self) -> None:
        """draft PR を実装より先に作る旧い順序 (「draft PR 作成 → 実装」) を書かない。"""
        self.assert_phrase_absent(
            self.label(), self.issue_claim_block(), DRAFT_PR_FIRST_PHRASE
        )


class IssueClaimRelatedDocumentTest(IssueClaimTestCase):
    """rule:issue-claim の手順を要約する skill・README。"""

    def test_issue_start_skill_has_no_branch_push_confirmation(self) -> None:
        """issue-start skill の排他制御の説明に「branch push による確定」が無い。"""
        text = read(self.issue_start_skill_path)
        label = display_path(self.issue_start_skill_path)
        self.assert_phrase_present(label, text, "rule:issue-claim")
        self.assert_phrase_absent(label, text, SKILL_BRANCH_PUSH_PHRASE)

    def test_readmes_do_not_describe_branch_push_exclusion(self) -> None:
        """plugin README と、ルート README の agent-discipline 節が、branch push を
        排他の仕組みとして説明しない。"""
        repo_section = markdown_section(read(REPO_README), REPO_README_PLUGIN_HEADING)
        repo_label = f"{display_path(REPO_README)} の {REPO_README_PLUGIN_HEADING} 節"
        self.assert_scope_found(
            repo_label, repo_section, f"`{REPO_README_PLUGIN_HEADING}` 節が無い"
        )
        scopes = (
            (display_path(PLUGIN_README), read(PLUGIN_README)),
            (repo_label, repo_section),
        )
        for label, scope in scopes:
            for phrase in README_BRANCH_PUSH_PHRASES:
                with self.subTest(file=label, phrase=phrase):
                    self.assert_phrase_absent(label, scope, phrase)


class IssueStartPickUpTest(IssueClaimTestCase):
    """issue-start skill の pick-up 分岐と排他制御の参照。"""

    def skill_label(self, heading: str) -> str:
        return f"{display_path(self.issue_start_skill_path)} の {heading} 節"

    def skill_section(self, heading: str) -> str:
        section = markdown_section(read(self.issue_start_skill_path), heading)
        self.assert_scope_found(
            self.skill_label(heading), section, f"`{heading}` 節が無い"
        )
        return section

    def pick_up_items(self) -> list[str]:
        """pick-up 分岐の箇条書き項目 (小節 1.1 の手順の箇条書きは含めない)。"""
        items = top_level_list_items(
            text_before_subheading(self.skill_section(ISSUE_START_PICK_UP_HEADING))
        )
        if not items:
            self.fail(f"{self.skill_label(ISSUE_START_PICK_UP_HEADING)}: 分岐の箇条書きが無い")
        return items

    def test_explicit_instruction_branch_resumes_from_the_unfinished_phase(self) -> None:
        """明示指示がある場合の分岐は、Phase A が完了済みなら Phase B から、未完了なら
        Phase A から再開する。"""
        label = self.skill_label(ISSUE_START_PICK_UP_HEADING)
        explicit_items = [
            item
            for item in self.pick_up_items()
            if satisfies(item, EXPLICIT_INSTRUCTION)
            and not satisfies(item, NO_EXPLICIT_INSTRUCTION)
        ]
        requirements: tuple[Requirement, ...] = (
            re.compile(r"完了済み.*PhaseB"),
            re.compile(r"未完了.*PhaseAから"),
        )
        if not any(
            all(satisfies(item, requirement) for requirement in requirements)
            for item in explicit_items
        ):
            self.fail(
                f"{label}: 明示指示がある場合の分岐に、完了済みなら Phase B から・未完了なら"
                f" Phase A から再開する記述が無い (明示指示がある場合の項目: {len(explicit_items)} 件)"
            )

    def test_no_explicit_instruction_branch_goes_to_the_claim(self) -> None:
        """明示指示が無い場合の分岐は、既存の branch / PR の有無に依らずセクション 2 の
        排他制御に進む。"""
        label = self.skill_label(ISSUE_START_PICK_UP_HEADING)
        requirements: tuple[Requirement, ...] = (
            NO_EXPLICIT_INSTRUCTION,
            "branch",
            re.compile(r"有無に(?:依|よ)らず|あっても"),
            "セクション 2",
            "排他制御",
        )
        if not any(
            all(satisfies(item, requirement) for requirement in requirements)
            for item in self.pick_up_items()
        ):
            wanted = "」「".join(describe(requirement) for requirement in requirements)
            self.fail(f"{label}: 「{wanted}」をすべて満たす分岐が無い")

    def test_state_check_covers_local_and_remote_branches(self) -> None:
        """既存の作業状態の確認コマンドが、remote の branch に加えて local にだけある
        branch も確認する。"""
        label = self.skill_label(ISSUE_START_PICK_UP_HEADING)
        section = self.skill_section(ISSUE_START_PICK_UP_HEADING)
        for command in PICK_UP_BRANCH_CHECK_COMMANDS:
            with self.subTest(command=command):
                self.assert_phrase_present(label, section, command)

    def test_explicit_instruction_uses_the_named_branch_as_is(self) -> None:
        """明示指示で branch 名が挙げられた場合は、命名規約を検証せずその branch を使い、
        local / remote のどちらにも無い場合は step 1 から実行する (step 1 から実行するのは、
        この場合と、issue 番号だけの明示指示でセクション 1.1 の手順が新しい名前を決めた場合)。"""
        label = self.skill_label(ISSUE_START_PICK_UP_HEADING)
        explicit_items = [
            item
            for item in self.pick_up_items()
            if satisfies(item, EXPLICIT_INSTRUCTION)
            and not satisfies(item, NO_EXPLICIT_INSTRUCTION)
        ]
        for name, requirements in EXPLICIT_BRANCH_REQUIREMENTS:
            with self.subTest(requirement=name):
                if not any(
                    all(satisfies(sentence, requirement) for requirement in requirements)
                    for item in explicit_items
                    for sentence in sentences(item)
                ):
                    wanted = "」「".join(describe(requirement) for requirement in requirements)
                    self.fail(
                        f"{label}: 明示指示がある場合の分岐に「{wanted}」をすべて含む文が無い"
                        f" (明示指示がある場合の項目: {len(explicit_items)} 件)"
                    )

    def test_new_name_from_the_lookup_starts_from_step_1(self) -> None:
        """明示指示がある場合の分岐で、セクション 1.1 の手順が新しい名前を決めたときは、
        新規着手として step 1 から実行する (手順で除外した branch は存在し続けるため、
        「branch が無い」場合とは別に書く)。"""
        label = self.skill_label(ISSUE_START_PICK_UP_HEADING)
        explicit_items = [
            item
            for item in self.pick_up_items()
            if satisfies(item, EXPLICIT_INSTRUCTION)
            and not satisfies(item, NO_EXPLICIT_INSTRUCTION)
        ]
        if not any(
            all(
                satisfies(sentence, requirement)
                for requirement in NEW_NAME_STARTS_FROM_STEP_1_REQUIREMENTS
            )
            for item in explicit_items
            for sentence in sentences(item)
        ):
            wanted = "」「".join(
                describe(requirement) for requirement in NEW_NAME_STARTS_FROM_STEP_1_REQUIREMENTS
            )
            self.fail(
                f"{label}: 明示指示がある場合の分岐に「{wanted}」をすべて含む文が無い"
                f" (明示指示がある場合の項目: {len(explicit_items)} 件)"
            )

    def test_missing_branch_is_not_the_only_step_1_condition(self) -> None:
        """step 1 から実行する条件を「local / remote のどちらにも無い場合に限り」だけで書く
        文を、新しい名前の規定と別に残さない (セクション 1.1 の手順が新しい名前を決めた場合も
        step 1 から実行するため)。"""
        label = self.skill_label(ISSUE_START_PICK_UP_HEADING)
        for sentence in sentences(self.skill_section(ISSUE_START_PICK_UP_HEADING)):
            with self.subTest(sentence=sentence.strip()[:60]):
                if satisfies(sentence, MISSING_BRANCH_ONLY) and not satisfies(
                    sentence, "新しい名前"
                ):
                    self.fail(
                        f"{label}: step 1 から実行する条件を branch が無い場合だけに限る文が"
                        f"ある: {sentence.strip()[:120]}"
                    )

    def test_every_branch_depends_on_explicit_instruction(self) -> None:
        """pick-up 分岐のどの項目も明示指示の有無を条件にし、既存の branch / PR だけを
        条件に Phase B から再開する分岐が無い。"""
        label = self.skill_label(ISSUE_START_PICK_UP_HEADING)
        self.assert_phrase_absent(
            label,
            self.skill_section(ISSUE_START_PICK_UP_HEADING),
            UNCONDITIONAL_RESUME_PHRASE,
        )
        for item in self.pick_up_items():
            with self.subTest(item=item.splitlines()[0][:60]):
                self.assert_phrase_present(label, item, EXPLICIT_INSTRUCTION)

    def test_no_explicit_instruction_branch_refers_to_the_lookup_section(self) -> None:
        """明示指示が無い場合の分岐は、既存の branch を小節 1.1 の手順で探す。"""
        label = self.skill_label(ISSUE_START_PICK_UP_HEADING)
        if not any(
            satisfies(item, NO_EXPLICIT_INSTRUCTION)
            and satisfies(item, LOOKUP_SECTION_REFERENCE)
            for item in self.pick_up_items()
        ):
            self.fail(f"{label}: 明示指示が無い場合の分岐が、小節 1.1 を参照していない")

    def test_claim_reference_is_not_limited_to_new_starts(self) -> None:
        """排他制御の参照が、排他制御を新規着手の場合だけに限らない (既存の branch / PR が
        あっても、明示指示が無ければ排他制御を実行するため)。"""
        self.assert_phrase_absent(
            self.skill_label(ISSUE_START_CLAIM_HEADING),
            self.skill_section(ISSUE_START_CLAIM_HEADING),
            NEW_START_ONLY_PHRASE,
        )

    def test_loose_pattern_is_absent(self) -> None:
        """branch 名のどこにでも一致する旧パターンを issue-start skill に残さない。"""
        self.assert_phrase_absent(
            display_path(self.issue_start_skill_path),
            read(self.issue_start_skill_path),
            LOOSE_BRANCH_PATTERN,
        )


class IssueStartClaimScriptReferenceTest(IssueClaimTestCase):
    """issue-start skill のセクション 2 (排他制御の参照) が claim 用のスクリプトを示す。"""

    def section_2(self) -> tuple[str, str]:
        label = f"{display_path(self.issue_start_skill_path)} のセクション 2"
        section = markdown_section(
            read(self.issue_start_skill_path), ISSUE_START_SECTION_2_PREFIX
        )
        self.assert_scope_found(
            label, section, f"`{ISSUE_START_SECTION_2_PREFIX}` で始まる見出しが無い"
        )
        return label, section

    def test_section_2_refers_to_the_claim_script(self) -> None:
        """セクション 2 は claim 用のスクリプト (`claim-issue.sh`) に言及する。"""
        label, section = self.section_2()
        self.assert_phrase_present(label, section, CLAIM_SCRIPT_NAME)

    def test_section_2_drops_the_no_duplication_sentence(self) -> None:
        """セクション 2 は「本 skill 側では手順を複製しません」という文を含まない (手順の本体は
        claim 用のスクリプトにあるため)。"""
        label, section = self.section_2()
        self.assert_phrase_absent(label, section, SKILL_NO_DUPLICATION_PHRASE)


class IssueStartBranchLookupTest(IssueClaimTestCase):
    """issue-start skill の既存 branch の探し方 (小節 1.1)。"""

    def lookup_label(self) -> str:
        return f"{display_path(self.issue_start_skill_path)} の {ISSUE_START_LOOKUP_HEADING} 節"

    def lookup_section(self) -> str:
        section = markdown_section(
            read(self.issue_start_skill_path), ISSUE_START_LOOKUP_HEADING
        )
        self.assert_scope_found(
            self.lookup_label(), section, f"`{ISSUE_START_LOOKUP_HEADING}` 節が無い"
        )
        return section

    def assert_lookup_sentence(self, requirements: tuple[Requirement, ...]) -> None:
        self.assert_some_sentence(self.lookup_label(), self.lookup_section(), requirements)

    def test_search_commands_use_the_leading_issue_pattern(self) -> None:
        """remote と local を、branch 名の先頭の `<prefix>/issue-<N>-` に絞ったパターンで
        探す。"""
        section = self.lookup_section()
        for command in EXISTING_BRANCH_SEARCH_COMMANDS:
            with self.subTest(command=command):
                self.assert_phrase_present(self.lookup_label(), section, command)

    def test_search_failure_stops_before_posting(self) -> None:
        """探索コマンドが失敗したら「一致 0 件」と扱わず、claim comment を投稿せず停止して
        報告する。"""
        self.assert_lookup_sentence(
            (
                "失敗",
                re.compile(r"0件"),
                re.compile(r"投稿(?:せず|する前|の前)"),
                "停止",
                "報告",
            )
        )

    def test_matches_are_counted_by_branch_name(self) -> None:
        """local と remote の両方にある同名の branch は 1 つと数える。"""
        self.assert_lookup_sentence(SAME_NAME_COUNTED_ONCE_REQUIREMENTS)

    def test_wip_branches_are_excluded(self) -> None:
        """契約改訂手順の補助 branch (`-phase-b-wip`) は候補から除く。"""
        self.assert_lookup_sentence((WIP_BRANCH_SUFFIX, re.compile(r"除|数えない")))

    def test_names_off_the_convention_stop(self) -> None:
        """命名規約に合わない名前が残ったら、何も変更せず停止してユーザに確認する。"""
        self.assert_lookup_sentence(CONVENTION_STOP_REQUIREMENTS)

    def test_convention_defines_the_allowed_characters(self) -> None:
        """検証に使う命名規約が、使える文字 (英小文字・数字・ハイフン) を定める (シェルの
        メタ文字を含む名前を候補から外すため)。"""
        self.assert_lookup_sentence(("命名規約", "英小文字", "数字", "ハイフン"))

    def test_merged_branches_are_excluded(self) -> None:
        """マージ済みの PR がある branch は候補から除く。"""
        self.assert_lookup_sentence((*MERGED_PR_CHECK_PHRASES, re.compile(r"除")))

    def test_merged_check_reads_the_head_commits(self) -> None:
        """マージ済みの確認は、マージ済みの PR の head commit を `--json headRefOid` で
        取り出す (branch 名だけで判定すると、同じ名前で作り直した branch も除くため)。"""
        self.assert_lookup_sentence((*MERGED_PR_CHECK_PHRASES, MERGED_PR_HEAD_FIELD))

    def test_current_commits_come_from_the_search_and_rev_parse(self) -> None:
        """head commit と比べる branch の現在の commit は、remote は手順 1 の `git ls-remote`
        の出力、local は `git rev-parse` で得る (側とコマンドの対応を、同じ読点区間の中の
        並び順で検査する)。"""
        self.assert_lookup_sentence(CURRENT_COMMIT_SOURCE_REQUIREMENTS)

    def test_merged_branch_is_excluded_only_when_every_commit_matches(self) -> None:
        """branch が存在する側 (local / remote) の commit がすべて、マージ済みの PR の
        head commit のどれかと一致する場合だけ候補から除く。"""
        self.assert_lookup_sentence(
            (
                "存在する",
                "すべて",
                "head commit",
                "一致",
                re.compile(r"だけ|限り"),
                re.compile(r"除"),
            )
        )

    def test_unmatched_commit_keeps_the_candidate(self) -> None:
        """local / remote のどちらか一方でも head commit と一致しなければ、マージ済みと
        確認できないので候補に残す (local が head より古いだけの場合も不一致になるため、
        未マージの commit があるとは限らない)。"""
        self.assert_lookup_sentence(
            (
                re.compile(r"(?:どちらか|いずれか)一方でも"),
                "一致しなけれ",
                re.compile(r"マージ済みと確認できな"),
                "候補に残",
            )
        )

    def test_merged_check_failure_stops(self) -> None:
        """マージ済みの確認 (gh) が失敗したら停止して報告する。"""
        self.assert_lookup_sentence(("gh", "失敗", "停止", "報告"))

    def test_names_are_validated_before_the_merged_check(self) -> None:
        """見つけた名前を gh のコマンドに埋め込む前に命名規約で検証する (命名規約に合わない
        名前で停止する文が、マージ済みの確認の文より前にある)。"""
        items = sentences(self.lookup_section())
        convention = next(
            (
                index
                for index, sentence in enumerate(items)
                if all(satisfies(sentence, r) for r in CONVENTION_STOP_REQUIREMENTS)
            ),
            None,
        )
        merged = next(
            (
                index
                for index, sentence in enumerate(items)
                if all(satisfies(sentence, phrase) for phrase in MERGED_PR_CHECK_PHRASES)
            ),
            None,
        )
        if convention is None or merged is None or convention > merged:
            self.fail(
                f"{self.lookup_label()}: 命名規約に合わない名前で停止する文が、マージ済みの確認"
                f" (`{' '.join(MERGED_PR_CHECK_PHRASES)}`) の文より前に無い"
            )

    def test_single_match_name_is_used(self) -> None:
        """候補が 1 つならその名前を使う。"""
        self.assert_lookup_sentence((re.compile(r"1つ(?:だけ)?(?:な|あれ|見つか)"), "その名前"))

    def test_no_match_follows_the_naming_convention(self) -> None:
        """候補が無ければ、命名規約で新しい名前を決める。"""
        self.assert_lookup_sentence(
            (re.compile(r"候補が(?:無|な)(?:け|い)"), "命名規約", "新し")
        )

    def test_new_name_avoids_existing_branches(self) -> None:
        """候補が無くなって決めた新しい名前の branch が既にある場合 (除外したマージ済みの
        branch 等) は、local / remote のどちらにも存在しない名前になるよう slug を変える
        (step 6 の同名判定で、除外した branch に switch しないため)。"""
        self.assert_lookup_sentence(
            ("slug", re.compile(r"(?:どちら|いずれ)にも存在しない"))
        )

    def test_multiple_matches_stop_before_posting(self) -> None:
        """候補が複数なら、claim comment を投稿せず、何も変更せず停止して確認する。"""
        self.assert_lookup_sentence(
            MULTIPLE_MATCH_REQUIREMENTS + (re.compile(r"投稿(?:せず|する前|の前)"),)
        )

    def test_found_names_are_single_quoted(self) -> None:
        """見つけた branch 名をコマンドに埋め込むときは single quote で囲む。"""
        self.assert_lookup_sentence(("コマンド", "single quote"))


class IssueStartExplicitLeftoverTest(IssueClaimTestCase):
    """issue-start skill の、明示指示で step 1 から実行するときの前の作業の残り (小節 1.2)。"""

    def leftover_label(self) -> str:
        return (
            f"{display_path(self.issue_start_skill_path)} の {ISSUE_START_LEFTOVER_HEADING} 節"
        )

    def leftover_section(self) -> str:
        section = markdown_section(
            read(self.issue_start_skill_path), ISSUE_START_LEFTOVER_HEADING
        )
        self.assert_scope_found(
            self.leftover_label(), section, f"`{ISSUE_START_LEFTOVER_HEADING}` 節が無い"
        )
        return section

    def assert_leftover_sentence(self, requirements: tuple[Requirement, ...]) -> None:
        self.assert_some_sentence(
            self.leftover_label(), self.leftover_section(), requirements
        )

    def test_section_is_under_the_pick_up_section(self) -> None:
        """小節 1.2 は pick-up 分岐の節 (セクション 1) の配下に置く。"""
        pick_up = markdown_section(
            read(self.issue_start_skill_path), ISSUE_START_PICK_UP_HEADING
        )
        if ISSUE_START_LEFTOVER_HEADING not in pick_up.splitlines():
            self.fail(
                f"{self.leftover_label()}: `{ISSUE_START_PICK_UP_HEADING}` 節の配下に"
                " 見出しが無い"
            )

    def test_applies_to_both_step_1_routes(self) -> None:
        """明示指示があっても step 1 から実行する 2 つの場合 (対象の branch が無い場合と、
        セクション 1.1 の手順で新しい名前を決めた場合) を対象にする。"""
        self.assert_leftover_sentence(
            (
                EXPLICIT_INSTRUCTION,
                re.compile(r"branchが[^、。]*(?:無|な)い"),
                LOOKUP_SECTION_REFERENCE,
                "新しい名前",
                STEP_1_START,
            )
        )

    def test_label_or_claim_exit_stops_without_withdrawing(self) -> None:
        """claim 用のスクリプトが exit 1 の `reason=label` か `reason=existing-claim` を返したら、
        撤退せずに停止する。"""
        self.assert_leftover_sentence(
            (
                "exit 1",
                "reason=label",
                "reason=existing-claim",
                re.compile(r"撤退(?:せず|しない)"),
                "停止",
            )
        )

    def test_comments_are_fetched_with_one_rest_get(self) -> None:
        """comment は REST GET の 1 回で取得し、その結果から確認の対象と数値 comment id を
        決める (取得を 2 回に分けると、その間に稼働中の別 session が投稿した claim comment も
        確認の対象に加わりうるため)。"""
        self.assert_leftover_sentence(
            (
                COMMENTS_REST_GET,
                re.compile(r"1回"),
                "確認の対象",
                "数値 comment id",
            )
        )

    def test_rest_get_failure_stops_without_asking(self) -> None:
        """早期判定の REST GET が失敗したら、ユーザに確認せずに停止して報告する (step 4 と
        同じ fail-closed)。"""
        self.assert_leftover_sentence(
            (
                "REST GET",
                "失敗",
                re.compile(r"確認(?:せず|しない|しません)"),
                "停止",
                "報告",
            )
        )

    def test_every_found_claim_is_a_target(self) -> None:
        """確認の対象は、step 1 で見つかったラベルとすべての claim comment で、`session=` の
        無い claim comment と、`session=` が自分のセッション ID と一致する claim comment も
        含める。"""
        self.assert_leftover_sentence(
            (
                "確認の対象",
                "ラベル",
                re.compile(r"すべてのclaimcomment"),
                re.compile(r"session=`?の(?:無|な)いclaimcomment"),
                re.compile(r"自分のセッションIDと一致するclaimcomment"),
            )
        )

    def test_targets_are_not_limited_to_other_sessions(self) -> None:
        """確認の対象を、`session=` が自分のセッション ID と一致しない claim comment に限る文を
        置かない。"""
        self.assert_no_violations(
            self.leftover_label(),
            [
                sentence
                for sentence in sentences(self.leftover_section())
                if satisfies(sentence, re.compile(r"一致しないclaimcomment"))
            ],
            "確認の対象を自分と一致しない claim comment に限る文",
        )

    def test_leftovers_are_shown_and_confirmed(self) -> None:
        """見つかったラベルと claim comment (数値 comment id と本文) を示し、前の作業の
        残りかどうかを `AskUserQuestion` でユーザに確認する (claim comment と、示す数値
        comment id・本文の対応を、同じ読点区間の中の並び順で検査する)。"""
        self.assert_leftover_sentence(
            (
                "ラベル",
                re.compile(r"claimcomment[^、。]*数値commentid[^、。]*本文"),
                "残り",
                "AskUserQuestion",
            )
        )

    def test_only_all_confirmed_leftovers_rerun_the_script(self) -> None:
        """claim 用のスクリプトを `--ignore-comment-id` を付けて再実行するのは、確認の対象の
        すべてが残りだと確認された場合に限る。"""
        self.assert_leftover_sentence(
            (
                re.compile(r"すべて[^、。]*残り(?:だ|である)?と確認"),
                re.compile(r"場合に限り|場合だけ"),
                "--ignore-comment-id",
                RERUN,
            )
        )

    def test_confirmed_claims_are_passed_by_numeric_id(self) -> None:
        """確認した claim comment は、数値 comment id を `--ignore-comment-id` で渡して特定する
        (本文や `session=` の値では照合しない)。"""
        self.assert_leftover_sentence(
            (re.compile(r"確認した"), "数値 comment id", "--ignore-comment-id")
        )

    def test_confirmed_label_is_passed_as_confirmed_leftover(self) -> None:
        """確認したラベルがあれば `--confirmed-leftover` を付けて再実行する。"""
        self.assert_leftover_sentence(("確認", "ラベル", "--confirmed-leftover"))

    def test_any_non_leftover_withdraws_without_posting(self) -> None:
        """確認の対象のうち 1 件でも残りではないと答えられたら、全体を残りではないとして扱い、
        claim comment を投稿せず (スクリプトを再実行せず) に撤退する。"""
        self.assert_leftover_sentence(
            (
                re.compile(r"1件でも[^。]*残りでは(?:ない|なく)"),
                "全体",
                NOT_POSTED,
                "撤退",
            )
        )

    def test_later_claims_are_judged_as_usual(self) -> None:
        """確認の後に投稿された claim comment は、除かずに判定する (「通常どおり」だけでは
        満たさず、「除かず」などの否定の語を求める)。"""
        self.assert_leftover_sentence(LATER_CLAIM_REQUIREMENTS)

    def test_usual_judgement_alone_does_not_satisfy_later_claims(self) -> None:
        """確認の後の claim の検査は、「通常どおり」だけの文では満たされない。"""
        self.assertFalse(
            satisfies_all(USUAL_JUDGEMENT_ONLY_SENTENCE, LATER_CLAIM_REQUIREMENTS),
            f"確認の後の claim の検査が「通常どおり」だけの文で満たされる: "
            f"{USUAL_JUDGEMENT_ONLY_SENTENCE}",
        )

    def test_later_claims_are_not_targets(self) -> None:
        """確認の後に投稿された claim comment は確認の対象に入らないので、除かれない。"""
        self.assert_leftover_sentence(
            (
                re.compile(r"確認(?:の|した)後に投稿"),
                "claim comment",
                re.compile(r"確認の対象に(?:入らない|ならない|含まれない)"),
                NOT_EXCLUDED,
            )
        )

    def test_unconfirmed_claims_are_not_excluded(self) -> None:
        """確認していない claim comment や、確認の後に投稿された claim comment を除く文を
        置かない。"""
        self.assert_no_violations(
            self.leftover_label(),
            unconfirmed_claim_exclusions(read(self.issue_start_skill_path)),
            "確認していない claim comment や確認の後の claim comment を除く文",
        )

    def test_loose_unconfirmed_exclusions_are_detected(self) -> None:
        """確認していない claim comment や確認の後に投稿された claim comment を除く文を
        小節 1.2 に足したコピーを、同じ検査が拒否する。"""
        self.assert_loose_sentences_detected(
            f"{self.leftover_label()} のコピー",
            unconfirmed_claim_exclusions,
            read(self.issue_start_skill_path),
            ISSUE_START_LEFTOVER_HEADING + "\n",
            "\n{}\n",
            LOOSE_UNCONFIRMED_EXCLUSION_SENTENCES,
        )

    def test_not_a_leftover_withdraws(self) -> None:
        """ユーザが残りではない (稼働中の別 session のもの) と答えた場合は撤退する。"""
        self.assert_leftover_sentence((re.compile(r"残りでは(?:ない|なく)"), "撤退"))

    def test_confirmed_leftovers_are_not_deleted(self) -> None:
        """確認の対象にしたラベルと claim comment は、`session=` が自分のセッション ID と一致
        していても削除しない (他 session の claim を削除しない規律に従う)。"""
        self.assert_leftover_sentence(
            (
                "確認の対象にした",
                "ラベル",
                "claim comment",
                "session=",
                "一致していても",
                re.compile(r"削除し(?:ない|ません)|削除せず"),
            )
        )

    def test_section_does_not_permit_deletion(self) -> None:
        """小節 1.2 に、claim comment やラベルの削除を許す文を置かない (この経路で自分が
        投稿した claim comment だけを削除すると書く文を除く)。"""
        self.assert_no_violations(
            self.leftover_label(),
            leftover_deletion_permissions(read(self.issue_start_skill_path)),
            "claim comment やラベルの削除を許す文",
        )

    def test_loose_deletion_permissions_are_detected(self) -> None:
        """claim comment やラベルの削除を許す文を小節 1.2 に足したコピーを、同じ検査が
        拒否する。"""
        self.assert_loose_sentences_detected(
            f"{self.leftover_label()} のコピー",
            leftover_deletion_permissions,
            read(self.issue_start_skill_path),
            ISSUE_START_LEFTOVER_HEADING + "\n",
            "\n{}\n",
            LOOSE_DELETION_SENTENCES,
        )

    def test_without_explicit_instruction_withdraws(self) -> None:
        """明示指示が無い場合は、この手順を使わず、スクリプトの exit 1 (早期判定の撤退) に
        従って撤退する。"""
        self.assert_leftover_sentence(
            (NO_EXPLICIT_INSTRUCTION, re.compile(r"exit1|早期判定"), "撤退")
        )

    def test_section_is_not_used_without_explicit_instruction(self) -> None:
        """明示指示が無い場合を書く文は、この手順を使わないと書く (明示指示が無い場合に
        この手順を使わせる文を置かない)。"""
        self.assert_no_violations(
            self.leftover_label(),
            leftover_procedure_without_explicit_instruction(
                read(self.issue_start_skill_path)
            ),
            "明示指示が無い場合にこの手順を使わせる文",
        )

    def test_loose_no_instruction_sentences_are_detected(self) -> None:
        """明示指示が無い場合にこの手順を使わせる文を小節 1.2 に足したコピーを、同じ検査が
        拒否する。"""
        self.assert_loose_sentences_detected(
            f"{self.leftover_label()} のコピー",
            leftover_procedure_without_explicit_instruction,
            read(self.issue_start_skill_path),
            ISSUE_START_LEFTOVER_HEADING + "\n",
            "\n{}\n",
            LOOSE_NO_INSTRUCTION_SKILL_SENTENCES,
        )


class IssueClaimEvaluationTest(IssueClaimTestCase):
    """`docs/discipline-evaluation.md` の issue-claim の評価基準。"""

    def test_explicit_resume_is_a_pass(self) -> None:
        """明示指示による再開で claim を経ないことを、表の Pass 列か表下の経路別 Pass
        定義のどちらかで Pass として扱う。"""
        text = read(self.evaluation_doc_path)
        label = f"{display_path(self.evaluation_doc_path)} の issue-claim 手順の遵守"
        row = next(
            (line for line in text.splitlines() if line.startswith(EVALUATION_ISSUE_CLAIM_ROW)),
            "",
        )
        self.assert_scope_found(label, row, f"「{EVALUATION_ISSUE_CLAIM_ROW}」の行が無い")
        cells = table_cells(row)
        pass_cell = cells[EVALUATION_PASS_COLUMN] if len(cells) > EVALUATION_PASS_COLUMN else ""
        notes = list_items_following(text, EVALUATION_ISSUE_CLAIM_NOTE)
        in_pass_cell = satisfies(pass_cell, EXPLICIT_INSTRUCTION)
        in_notes = any(
            satisfies(item, EXPLICIT_INSTRUCTION) and satisfies(item, "再開")
            for item in top_level_list_items(notes)
        )
        if not (in_pass_cell or in_notes):
            self.fail(
                f"{label}: 明示指示による再開が、表の Pass 列にも「{EVALUATION_ISSUE_CLAIM_NOTE}」"
                "の箇条書きにも無い"
            )

    def test_branch_lookup_stop_is_a_pass(self) -> None:
        """既存 branch の探し方で停止して確認・報告する経路 (複数一致・命名規約違反・
        探索失敗) を、経路別 Pass 定義で Pass として扱う。"""
        text = read(self.evaluation_doc_path)
        label = f"{display_path(self.evaluation_doc_path)} の「{EVALUATION_ISSUE_CLAIM_NOTE}」"
        notes = list_items_following(text, EVALUATION_ISSUE_CLAIM_NOTE)
        self.assert_scope_found(label, notes, "経路別 Pass 定義の箇条書きが無い")
        requirements: tuple[Requirement, ...] = (
            "既存",
            "branch",
            "複数",
            "命名規約",
            "失敗",
            "停止",
        )
        if not any(
            all(satisfies(item, requirement) for requirement in requirements)
            for item in top_level_list_items(notes)
        ):
            wanted = "」「".join(describe(requirement) for requirement in requirements)
            self.fail(f"{label}: 「{wanted}」をすべて含む項目が無い")

    def issue_claim_row(self) -> list[str]:
        text = read(self.evaluation_doc_path)
        label = f"{display_path(self.evaluation_doc_path)} の issue-claim 手順の遵守"
        row = next(
            (line for line in text.splitlines() if line.startswith(EVALUATION_ISSUE_CLAIM_ROW)),
            "",
        )
        self.assert_scope_found(label, row, f"「{EVALUATION_ISSUE_CLAIM_ROW}」の行が無い")
        return table_cells(row)

    def test_success_route_follows_the_script(self) -> None:
        """経路別 Pass 定義の成功経路は、claim 用のスクリプトを実行して exit code に従うこと
        とする。"""
        text = read(self.evaluation_doc_path)
        label = f"{display_path(self.evaluation_doc_path)} の「{EVALUATION_ISSUE_CLAIM_NOTE}」"
        notes = list_items_following(text, EVALUATION_ISSUE_CLAIM_NOTE)
        item = next(
            (
                item
                for item in top_level_list_items(notes)
                if TOP_LEVEL_LIST_ITEM_PATTERN.sub("", item, count=1).startswith(
                    EVALUATION_SUCCESS_ROUTE_LABEL
                )
            ),
            "",
        )
        self.assert_scope_found(
            label, item, f"「{EVALUATION_SUCCESS_ROUTE_LABEL}」で始まる項目が無い"
        )
        for requirement in (
            re.compile(r"claim-issue\.sh|スクリプト"),
            re.compile(r"exitcode|exit0"),
        ):
            with self.subTest(requirement=describe(requirement)):
                if not satisfies(item, requirement):
                    self.fail(f"{label}: 成功経路の項目に「{describe(requirement)}」が無い")

    def test_grep_anchor_includes_the_script(self) -> None:
        """issue-claim の行の grep anchor (観測コマンド) に、claim 用のスクリプトの実行
        (`claim-issue.sh`) を含める。"""
        cells = self.issue_claim_row()
        anchor_cell = (
            cells[EVALUATION_GREP_ANCHOR_COLUMN] if len(cells) > EVALUATION_GREP_ANCHOR_COLUMN else ""
        )
        if CLAIM_SCRIPT_NAME not in anchor_cell:
            self.fail(
                f"{display_path(self.evaluation_doc_path)} の issue-claim 手順の遵守:"
                f" grep anchor の列に「{CLAIM_SCRIPT_NAME}」が無い"
            )

    def test_explicit_step_1_route_matches_the_leftover_section(self) -> None:
        """明示指示で step 1 から実行した場合の Pass 定義が、issue-start skill の小節 1.2 の
        手順と一致する: comment を REST GET の 1 回で取得し (失敗したらユーザに確認せず停止
        する)、すべての claim comment を確認の対象にし、すべてが残りなら確認した id を
        `--ignore-comment-id` で、確認したラベルを `--confirmed-leftover` で渡してスクリプトを
        再実行し、1 件でも残りではないと答えられたら投稿せずに撤退する。"""
        text = read(self.evaluation_doc_path)
        label = f"{display_path(self.evaluation_doc_path)} の「{EVALUATION_ISSUE_CLAIM_NOTE}」"
        notes = list_items_following(text, EVALUATION_ISSUE_CLAIM_NOTE)
        item = next(
            (
                item
                for item in top_level_list_items(notes)
                if satisfies(item, EXPLICIT_INSTRUCTION)
                and satisfies(item, LEFTOVER_SECTION_REFERENCE)
            ),
            "",
        )
        self.assert_scope_found(label, item, "明示指示と小節 1.2 を含む項目が無い")
        requirements: tuple[Requirement, ...] = (
            "REST GET",
            re.compile(r"1回"),
            re.compile(r"確認(?:せず|しない)"),
            re.compile(r"すべてのclaimcomment"),
            re.compile(r"1件でも"),
            NOT_POSTED,
            "--ignore-comment-id",
            "--confirmed-leftover",
        )
        for requirement in requirements:
            with self.subTest(requirement=describe(requirement)):
                if not satisfies(item, requirement):
                    self.fail(
                        f"{label}: 明示指示で step 1 から実行した場合の項目に"
                        f"「{describe(requirement)}」が無い"
                    )


if __name__ == "__main__":
    unittest.main()
