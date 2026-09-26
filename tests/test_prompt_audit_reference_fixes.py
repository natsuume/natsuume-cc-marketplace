"""plugin 内の壊れた参照と指示ファイル間の矛盾を解消した状態を固定する契約テスト。

対象は update-default-branch / pre-push-review / natsuume-writing / agent-discipline /
cross-model-advisor / pre-push-codex-review の skill・command・hook 注入文である。
各テストの docstring にある A1〜A17 は、この修正の受入基準の項目 ID を指す。

検査は 2 種類を組み合わせる:

- 修正後に残ってはいけない旧文言 (存在しない plugin・節への参照、hook に deny される
  委任先、実装と一致しない説明) の不在
- 修正後に必要な識別子 (agent 名・rule ID・tool 名・節名・コマンド) の存在

存在検査は識別子・固有名に限り、文章全体の一致は検査しない。言い回しは実装側で
選べる。version bump・CI・lint の整合は scripts/check_plugin_versions.py と
各 lint が検査するため、本ファイルでは扱わない。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

# update-default-branch
UPDATE_DEFAULT_BRANCH = PLUGINS / "update-default-branch"
UPDATE_DEFAULT_BRANCH_SKILL = (
    UPDATE_DEFAULT_BRANCH / "skills" / "update-default-branch" / "SKILL.md"
)
UPDATE_DEFAULT_BRANCH_README = UPDATE_DEFAULT_BRANCH / "README.md"

# pre-push-review
PRE_PUSH_REVIEW_README = PLUGINS / "pre-push-review" / "README.md"

# natsuume-writing
NATSUUME_WRITING = PLUGINS / "natsuume-writing"
WRITING_REVIEW_SKILL = NATSUUME_WRITING / "skills" / "review" / "SKILL.md"

# agent-discipline
AGENT_DISCIPLINE = PLUGINS / "agent-discipline"
ISSUE_PLAN_SKILL = AGENT_DISCIPLINE / "skills" / "issue-plan" / "SKILL.md"
ISSUE_START_SKILL = AGENT_DISCIPLINE / "skills" / "issue-start" / "SKILL.md"
DISCIPLINE_PROMPTS = AGENT_DISCIPLINE / "hooks" / "prompts"
ALWAYS_1 = DISCIPLINE_PROMPTS / "always-1.md"
ALWAYS_2 = DISCIPLINE_PROMPTS / "always-2.md"
ALWAYS_3 = DISCIPLINE_PROMPTS / "always-3.md"
SUBAGENT_RULES = DISCIPLINE_PROMPTS / "subagent-rules.md"
UNCOMMITTED_CHECK = DISCIPLINE_PROMPTS / "uncommitted-check.md"
AUTO_MODE = DISCIPLINE_PROMPTS / "auto-mode.md"

# cross-model-advisor
ADVISOR_PROMPTS = PLUGINS / "cross-model-advisor" / "hooks" / "prompts"
ADVISOR_RULES = ADVISOR_PROMPTS / "advisor-rules.md"
ADVISOR_RULES_SUBAGENT = ADVISOR_PROMPTS / "advisor-rules-subagent.md"

# pre-push-codex-review
PRE_PUSH_CODEX_REVIEW = PLUGINS / "pre-push-codex-review"
CODEX_REVIEW_COMMAND = PRE_PUSH_CODEX_REVIEW / "commands" / "review.md"
CODEX_AUTO_MARK = PRE_PUSH_CODEX_REVIEW / "hooks" / "scripts" / "auto-mark.sh"

# 委任先・参照先として名指しする識別子。
RESCUE_RUNNER = "cross-model-advisor:codex-rescue-runner"
BASH_DECOMPOSE_RULE = "rule:bash-decompose"

# 存在しない plugin 名。plugins/ 配下のどこにも残さない。
REMOVED_PLUGIN_NAME = "decompose-bash"

# hook に deny される委任先の agent 名。
DENIED_RESCUE_AGENT = "codex:codex-rescue"

# bash-decompose の「なぜ」にある、hook が先頭パターンしか見ないという旧説明。
# 実際の hook は `&&` / `||` / `;` / `|` / `&` で segment に分割して判定する。
OUTDATED_BASH_DECOMPOSE_REASONS = {
    ALWAYS_1: (
        "先頭以外の部分が hook 検知から外れる",
        "パターンしか見ずに通過させてしまう",
    ),
    SUBAGENT_RULES: ("先頭以外が hook 検知から外れ",),
}

# pre-push-codex-review が標準 skill を直接呼ばない理由として成立しない説明語。
OUTDATED_STANDARD_SKILL_REASONS = (
    "nested subagent 制約",
    "nested 制約",
    "turn が終了",
    "degraded mode",
)

# 標準 skill を直接呼ばない現行の理由 3 点を示す語。
# (1) parent-safe report 契約 (2) lifecycle hook による marker 検知
# (3) `tools` から `Agent` を除外して read-only に保つ
CURRENT_STANDARD_SKILL_REASON_KEYWORDS = (
    "parent-safe",
    "SubagentStart",
    "Agent",
    "read-only",
)

CODEX_REVIEW_RATIONALE_MARKER = "標準 skill を直接呼ばない理由"
CODEX_REVIEW_FIX_FLOW_HEADING = "## レビュー指摘の修正フロー"
ISSUE_CLAIM_MARKER = "<!-- rule:issue-claim -->"
RESCUE_THREAD_MARKER = "<!-- rule:rescue-thread -->"
RULE_MARKER_PREFIX = "<!-- rule:"

LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+\.) ")
TOP_LEVEL_LIST_ITEM_PATTERN = re.compile(r"^(?:[-*+]|\d+\.) ")
HEADING_PATTERN = re.compile(r"^#{1,6} ")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def repo_relative(path: Path) -> str:
    """リポジトリ直下からの相対パスを返す (失敗メッセージと subTest の区別に使う)。"""
    return str(path.relative_to(ROOT))


def strip_whitespace(text: str) -> str:
    """空白文字をすべて除去する (行の折り返しで分断された出現も照合するため)。"""
    return "".join(text.split())


def files_under(directory: Path) -> list[Path]:
    """`directory` 配下の通常ファイルを列挙する。"""
    return sorted(path for path in directory.rglob("*") if path.is_file())


def files_containing(directory: Path, phrase: str) -> list[str]:
    """`directory` 配下で `phrase` を含むファイルの相対パス一覧を返す。"""
    needle = phrase.encode("utf-8")
    return [
        repo_relative(path)
        for path in files_under(directory)
        if needle in path.read_bytes()
    ]


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


def rule_block(text: str, marker: str) -> str:
    """`marker` から次の `<!-- rule:` マーカーの手前までを返す (マーカー行を除く)。"""
    start = text.find(marker)
    if start < 0:
        return ""
    body_start = start + len(marker)
    end = text.find(RULE_MARKER_PREFIX, body_start)
    return text[body_start:] if end < 0 else text[body_start:end]


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

    インデントされた下位項目は同じ項目の一部として含める (列挙を下位項目に
    分けて書いた場合も 1 項目として扱うため)。
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


class ReferenceFixTestCase(unittest.TestCase):
    """失敗時に該当行だけを示す helper。"""

    def assert_phrase_absent(self, path: Path, phrase: str) -> None:
        """空白を除去した全文で `phrase` の不在を確認する。"""
        body = read(path)
        if strip_whitespace(phrase) not in strip_whitespace(body):
            return
        hits = [
            f"L{number}: {line.strip()[:120]}"
            for number, line in enumerate(body.splitlines(), start=1)
            if phrase in line
        ]
        joined = " / ".join(hits) if hits else "改行をまたいで出現"
        self.fail(f"{repo_relative(path)}: 「{phrase}」が残っている: {joined}")

    def assert_phrase_present(self, label: str, scope: str, phrase: str) -> None:
        if phrase not in scope:
            self.fail(f"{label}: 「{phrase}」が無い")

    def assert_scope_found(self, label: str, scope: str, hint: str) -> None:
        if not scope.strip():
            self.fail(f"{label}: 検査対象の箇所が見つからない ({hint})")


class UpdateDefaultBranchReferenceTest(ReferenceFixTestCase):
    """update-default-branch の参照先と復元コマンド。"""

    def test_no_plugin_file_mentions_the_removed_decompose_bash_plugin(self) -> None:
        """A1 / A3: plugins/ 配下に存在しない `decompose-bash` plugin への言及が無い。"""
        offenders = files_containing(PLUGINS, REMOVED_PLUGIN_NAME)
        self.assertEqual(
            [], offenders, f"「{REMOVED_PLUGIN_NAME}」が残るファイル: {offenders}"
        )

    def test_skill_refers_to_the_bash_decompose_rule(self) -> None:
        """A1: SKILL.md が agent-discipline の `rule:bash-decompose` を参照する。"""
        self.assert_phrase_present(
            repo_relative(UPDATE_DEFAULT_BRANCH_SKILL),
            read(UPDATE_DEFAULT_BRANCH_SKILL),
            BASH_DECOMPOSE_RULE,
        )

    def test_no_file_uses_git_checkout(self) -> None:
        """A2: update-default-branch 配下に `git checkout` が残らない。"""
        offenders = files_containing(UPDATE_DEFAULT_BRANCH, "git checkout")
        self.assertEqual([], offenders, f"`git checkout` が残るファイル: {offenders}")

    def test_restore_command_uses_git_switch_create(self) -> None:
        """A2: SKILL.md と README.md の復元手順が `git switch -c '<name>' <sha>` を使う。

        SKILL.md は作業ブランチ作成にも `git switch -c '<name>'` を使うため、
        SHA 指定を含む復元コマンドの形で検査する。
        """
        for path in (UPDATE_DEFAULT_BRANCH_SKILL, UPDATE_DEFAULT_BRANCH_README):
            with self.subTest(file=repo_relative(path)):
                self.assert_phrase_present(
                    repo_relative(path), read(path), "git switch -c '<name>' <sha>"
                )


class PrePushReviewReadmeReferenceTest(ReferenceFixTestCase):
    """pre-push-review README の関連 plugin 参照。"""

    def test_readme_has_no_link_to_the_removed_plugin_directory(self) -> None:
        """A3: README に存在しない `../decompose-bash/` へのリンクが無い。"""
        self.assert_phrase_absent(PRE_PUSH_REVIEW_README, "../decompose-bash/")

    def test_readme_refers_to_the_bash_decompose_rule(self) -> None:
        """A3: README が agent-discipline の `rule:bash-decompose` を参照する。"""
        self.assert_phrase_present(
            repo_relative(PRE_PUSH_REVIEW_README),
            read(PRE_PUSH_REVIEW_README),
            BASH_DECOMPOSE_RULE,
        )


class NatsuumeWritingReviewDelegationTest(ReferenceFixTestCase):
    """natsuume-writing review skill の観点 3 の委任先。"""

    def test_no_file_delegates_to_the_denied_rescue_agent(self) -> None:
        """A4: natsuume-writing 配下に `codex:codex-rescue` が残らない。"""
        offenders = files_containing(NATSUUME_WRITING, DENIED_RESCUE_AGENT)
        self.assertEqual(
            [], offenders, f"「{DENIED_RESCUE_AGENT}」が残るファイル: {offenders}"
        )

    def test_review_skill_delegates_to_the_rescue_runner(self) -> None:
        """A4: 委任先が `cross-model-advisor:codex-rescue-runner` (model sonnet) になる。"""
        text = read(WRITING_REVIEW_SKILL)
        label = repo_relative(WRITING_REVIEW_SKILL)
        self.assert_phrase_present(label, text, RESCUE_RUNNER)
        self.assert_phrase_present(label, text, 'model: "sonnet"')

    def test_review_skill_fallback_names_the_cross_model_advisor_plugin(self) -> None:
        """A4: fallback の条件が cross-model-advisor plugin の未導入になる。"""
        text = read(WRITING_REVIEW_SKILL)
        self.assert_phrase_present(
            repo_relative(WRITING_REVIEW_SKILL),
            text,
            "cross-model-advisor plugin が未導入",
        )
        self.assert_phrase_absent(WRITING_REVIEW_SKILL, "codex plugin が未導入")


class AgentDisciplineSkillReferenceTest(ReferenceFixTestCase):
    """agent-discipline の skill 内の手順と参照。"""

    def test_issue_plan_fallback_has_no_command_substitution(self) -> None:
        """A5: sub-issue fallback にコマンド置換とシェル変数が無い。"""
        for phrase in ("SUB_ID=$(", "$SUB_ID"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(ISSUE_PLAN_SKILL, phrase)

    def test_issue_plan_fallback_keeps_the_two_api_calls_and_id_warning(self) -> None:
        """A5: 2 段の gh api 呼び出しと「内部数値 ID」の注意書きが残る。"""
        text = read(ISSUE_PLAN_SKILL)
        label = repo_relative(ISSUE_PLAN_SKILL)
        for phrase in ("--jq .id", "sub_issue_id=", "内部数値 ID"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, text, phrase)

    def test_issue_start_points_to_the_untestable_artifact_paragraph(self) -> None:
        """A6: `rule:tdd-two-phase` の参照先が「テスト不能な成果物の扱い」になる。"""
        text = read(ISSUE_START_SKILL)
        self.assert_phrase_absent(ISSUE_START_SKILL, "境界節")
        self.assert_phrase_present(
            repo_relative(ISSUE_START_SKILL), text, "テスト不能な成果物の扱い"
        )

    def test_referenced_paragraph_exists_in_always_rules(self) -> None:
        """A6: 参照先の「テスト不能な成果物の扱い」が always-3.md に実在する。"""
        self.assert_phrase_present(
            repo_relative(ALWAYS_3), read(ALWAYS_3), "テスト不能な成果物の扱い"
        )


class AgentDisciplinePromptTest(ReferenceFixTestCase):
    """agent-discipline の hook 注入文。"""

    def test_sub_issue_link_does_not_name_the_gh_extension(self) -> None:
        """A7: sub-issue 親子リンクの項目が `gh sub-issue` 拡張を名指ししない。"""
        self.assert_phrase_absent(ALWAYS_2, "gh sub-issue")

    def test_sub_issue_link_points_to_the_issue_plan_skill(self) -> None:
        """A7: sub-issue 親子リンクの項目が `issue-plan` skill を参照する。"""
        item = list_item_containing(read(ALWAYS_2), "sub-issue 親子リンク")
        label = f"{repo_relative(ALWAYS_2)} の sub-issue 親子リンク項目"
        self.assert_scope_found(label, item, "「sub-issue 親子リンク」を含む項目が無い")
        self.assert_phrase_present(label, item, "issue-plan")

    def test_bash_decompose_reason_drops_the_head_pattern_claim(self) -> None:
        """A8: bash-decompose の「なぜ」に、hook が先頭パターンしか見ず後続が
        素通りされるという旧説明が無い。"""
        for path, phrases in OUTDATED_BASH_DECOMPOSE_REASONS.items():
            for phrase in phrases:
                with self.subTest(file=repo_relative(path), phrase=phrase):
                    self.assert_phrase_absent(path, phrase)

    def test_bash_decompose_rule_marker_is_kept(self) -> None:
        """A8: ルール本体の書き換えで `rule:bash-decompose` マーカーが消えない。"""
        for path in (ALWAYS_1, SUBAGENT_RULES):
            with self.subTest(file=repo_relative(path)):
                self.assert_phrase_present(
                    repo_relative(path), read(path), "<!-- rule:bash-decompose -->"
                )

    def test_uncommitted_check_confirms_with_ask_user_question(self) -> None:
        """A9: 未コミット変更の確認が `AskUserQuestion` を使い、自由文の問いかけ例が無い。"""
        self.assert_phrase_present(
            repo_relative(UNCOMMITTED_CHECK),
            read(UNCOMMITTED_CHECK),
            "AskUserQuestion",
        )
        self.assert_phrase_absent(UNCOMMITTED_CHECK, "進めてよろしいですか")

    def test_uncommitted_check_keeps_template_variable_order(self) -> None:
        """A9: `{{CWD}}` と `{{DIRTY}}` がそれぞれ 1 回だけ現れ、`{{CWD}}` が先に来る。"""
        text = read(UNCOMMITTED_CHECK)
        self.assertEqual(1, text.count("{{CWD}}"), "{{CWD}} の出現回数")
        self.assertEqual(1, text.count("{{DIRTY}}"), "{{DIRTY}} の出現回数")
        self.assertLess(
            text.index("{{CWD}}"),
            text.index("{{DIRTY}}"),
            "{{CWD}} が {{DIRTY}} より前に無い",
        )

    def test_cleanup_does_not_delete_branches(self) -> None:
        """A10: rule:issue-claim の後片付けが branch を削除せず、`master` 固定と `&&`
        連結も使わない (default branch 上の push を deny する git-guardrails と、作業
        branch 上の push を検査する pre-push-review のどちらにも止められない)。"""
        section = rule_block(read(ALWAYS_3), ISSUE_CLAIM_MARKER)
        label = f"{repo_relative(ALWAYS_3)} の rule:issue-claim 節"
        self.assert_scope_found(label, section, f"`{ISSUE_CLAIM_MARKER}` 節が無い")
        for phrase in (
            "git switch master",
            "<branch> &&",
            "git push origin --delete",
            "git push origin :",
            "git branch -D",
        ):
            with self.subTest(absent=phrase):
                if phrase in section:
                    self.fail(f"{label}: 「{phrase}」が残っている")

    def test_auto_mode_allows_stopping_for_design_approval(self) -> None:
        """A11: 「止まってよいのは」の列挙が rule:design-approval の
        `AskUserQuestion` を含む。"""
        item = top_level_list_item_containing(read(AUTO_MODE), "止まってよいのは")
        label = f"{repo_relative(AUTO_MODE)} の「止まってよいのは」項目"
        self.assert_scope_found(label, item, "「止まってよいのは」を含む項目が無い")
        for phrase in ("design-approval", "AskUserQuestion"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, item, phrase)


class CrossModelAdvisorPromptTest(ReferenceFixTestCase):
    """cross-model-advisor の hook 注入文。"""

    def rescue_thread_block(self) -> str:
        block = rule_block(read(ADVISOR_RULES), RESCUE_THREAD_MARKER)
        self.assert_scope_found(
            repo_relative(ADVISOR_RULES), block, f"`{RESCUE_THREAD_MARKER}` 節が無い"
        )
        return block

    def test_rescue_thread_rule_assumes_the_rescue_runner(self) -> None:
        """A12: §4 rescue の thread 選択が `codex-rescue-runner` への依頼を前提にする。"""
        self.assert_phrase_present(
            f"{repo_relative(ADVISOR_RULES)} の rule:rescue-thread 節",
            self.rescue_thread_block(),
            "codex-rescue-runner",
        )

    def test_rescue_thread_rule_drops_outdated_launch_paths(self) -> None:
        """A12: §4 に deny される起動経路の列挙と過去形の実測記述が無い。"""
        block = strip_whitespace(self.rescue_thread_block())
        label = f"{repo_relative(ADVISOR_RULES)} の rule:rescue-thread 節"
        for phrase in ("Skill / command / subagent 経由のいずれも", "実測でほぼ常に新規"):
            with self.subTest(phrase=phrase):
                if strip_whitespace(phrase) in block:
                    self.fail(f"{label}: 「{phrase}」が残っている")

    def test_rescue_thread_rule_keeps_the_flag_decision(self) -> None:
        """A12: `--resume` / `--fresh` を自分で決める判定規則が残る。"""
        block = self.rescue_thread_block()
        label = f"{repo_relative(ADVISOR_RULES)} の rule:rescue-thread 節"
        for phrase in ("--resume", "--fresh"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, block, phrase)

    def test_subagent_rules_delegate_to_the_parent_consult_procedure(self) -> None:
        """A13: subagent の相談 request を親の consult 手順 (Codex / Fable 並列相談) に委ねる。"""
        text = read(ADVISOR_RULES_SUBAGENT)
        if "Fable" not in text and "consult" not in text:
            self.fail(
                f"{repo_relative(ADVISOR_RULES_SUBAGENT)}: 親の consult 手順 "
                "(Fable を含む並列相談) への委任を示す「Fable」「consult」のいずれも無い"
            )

    def test_subagent_rules_do_not_request_the_codex_runner_only(self) -> None:
        """A13: Codex の advisor runner だけを名指しして起動を親に依頼する文が無い。"""
        self.assert_phrase_absent(
            ADVISOR_RULES_SUBAGENT,
            '`cross-model-advisor:codex-advisor-runner` の Agent (`model: "sonnet"` '
            "を明示し、起動 mode は指定しない) として実行するよう依頼する",
        )


class PrePushCodexReviewCommandTest(ReferenceFixTestCase):
    """pre-push-codex-review の review command と auto-mark.sh のコメント。"""

    def fix_flow_section(self) -> str:
        section = markdown_section(
            read(CODEX_REVIEW_COMMAND), CODEX_REVIEW_FIX_FLOW_HEADING
        )
        self.assert_scope_found(
            repo_relative(CODEX_REVIEW_COMMAND),
            section,
            f"`{CODEX_REVIEW_FIX_FLOW_HEADING}` 節が無い",
        )
        return section

    def test_rationale_heading_says_subagent(self) -> None:
        """A14: 見出しが「Skill / Bash ではなく subagent」になる。"""
        self.assert_phrase_absent(
            CODEX_REVIEW_COMMAND, "Skill / Bash ではなく foreground subagent"
        )
        self.assert_phrase_present(
            repo_relative(CODEX_REVIEW_COMMAND),
            read(CODEX_REVIEW_COMMAND),
            "Skill / Bash ではなく subagent",
        )

    def test_standard_skill_rationale_drops_outdated_reasons(self) -> None:
        """A15: command と auto-mark.sh に nested 制約・turn 終了・degraded mode の
        説明が無い。"""
        for path in (CODEX_REVIEW_COMMAND, CODEX_AUTO_MARK):
            for phrase in OUTDATED_STANDARD_SKILL_REASONS:
                with self.subTest(file=repo_relative(path), phrase=phrase):
                    self.assert_phrase_absent(path, phrase)

    def test_standard_skill_rationale_states_the_current_reasons(self) -> None:
        """A15: 「標準 skill を直接呼ばない理由」の項目が現行の理由 3 点の語を含む。"""
        item = list_item_containing(
            read(CODEX_REVIEW_COMMAND), CODEX_REVIEW_RATIONALE_MARKER
        )
        label = f"{repo_relative(CODEX_REVIEW_COMMAND)} の理由節"
        self.assert_scope_found(
            label, item, f"「{CODEX_REVIEW_RATIONALE_MARKER}」を含む項目が無い"
        )
        for keyword in CURRENT_STANDARD_SKILL_REASON_KEYWORDS:
            with self.subTest(keyword=keyword):
                self.assert_phrase_present(label, item, keyword)

    def test_fix_flow_rescue_branches_on_cross_model_advisor(self) -> None:
        """A16: 修正フローの壁打ちが、併用時の `cross-model-advisor:codex-rescue-runner`
        と非併用時の `/codex:rescue --wait` の両経路を持つ。"""
        section = self.fix_flow_section()
        label = f"{repo_relative(CODEX_REVIEW_COMMAND)} の修正フロー"
        for phrase in (RESCUE_RUNNER, "/codex:rescue --wait"):
            with self.subTest(phrase=phrase):
                self.assert_phrase_present(label, section, phrase)

    def test_fix_flow_removes_leaked_secrets_from_history(self) -> None:
        """A17: 修正フローが、中間 commit の秘匿情報を push 前に履歴から除去する
        規則を持つ。"""
        self.assert_phrase_present(
            f"{repo_relative(CODEX_REVIEW_COMMAND)} の修正フロー",
            self.fix_flow_section(),
            "履歴から除去",
        )


if __name__ == "__main__":
    unittest.main()
