"""agent-discipline 検知層 (PreToolUse type:agent hook) の Step 0 guard の契約テスト。

検知層は `gh issue create` / `gh issue edit` / `gh pr create` / `gh pr edit` の 4 entry
から成り、各 entry は `if: "Bash(gh <cmd>:*)"` filter と agent prompt を持つ。`if` filter は
best-effort であり、compound command の各 subcommand と env-prefix を剥がした command を
評価する一方、`$(...)` / バッククォート / `$VAR` を含む Bash では対象外でも hook を起動
しうる。そのため prompt の Step 0 は command 全体の先頭ではなく subcommand 単位で検証
対象を決め、静的に判定できない形は `ok: false` に倒す。

本ファイルが検査するのは次の点 (agent-discipline / experimental-agent-discipline の両方):

- (A) 各 entry の Step 0 節が正典文 (`STEP0_CANONICAL_TEMPLATE`) と空白除去後に完全一致する
- (B) 各 entry の Step 1 節が、hook 時点で存在しない `--body-file` を `ok: false` とする
  規則 (`STEP1_TOCTOU_RULE`) と、先行する `cd <dir>` を基準に相対 PATH を解決する規則
  (`STEP1_RELATIVE_PATH_RULE`) を含む
- (C) 各 entry の prompt が、command 全体の先頭 literal だけで判定する Step 0 の語と、
  `if` filter を fail-permissive と説明する冒頭段落の語を含まず、冒頭段落が
  `best-effort` を述べる
- (D) 両 plugin の agent prompt が `if` ごとに byte-identical で、model / timeout が固定値
- (E) 両 plugin の `scripts/lint-prompt-sync.sh` が exit 0 で終わる
- (F) agent-discipline README が、非対象 Bash では agent が起動しないという説明語を含まない
- (G) agent-discipline README の動作説明・既知の制約・SPOF 緩和の設計が、best-effort な
  `if` filter と Step 0 の subcommand 判定・静的判定不能の扱いを述べる
- (H) 両 plugin の version が 4 箇所 (plugin.json / marketplace.json / 直下 README の一覧
  テーブル / plugin README の `## バージョン`) で期待値に一致する
- (I) experimental-agent-discipline README の `## バージョン` 直下が期待値である

照合は needle と本文の両方から空白 (改行を含む) を全除去した文字列で行う。折り返しや
空白の入れ方の違いでは契約を回避できない。
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"
ROOT_README = ROOT / "README.md"

AGENT_DISCIPLINE = "agent-discipline"
EXPERIMENTAL_AGENT_DISCIPLINE = "experimental-agent-discipline"
PLUGIN_NAMES = (AGENT_DISCIPLINE, EXPERIMENTAL_AGENT_DISCIPLINE)

AGENT_DISCIPLINE_README = ROOT / "plugins" / AGENT_DISCIPLINE / "README.md"
EXPERIMENTAL_README = ROOT / "plugins" / EXPERIMENTAL_AGENT_DISCIPLINE / "README.md"

EXPECTED_VERSIONS: dict[str, str] = {
    AGENT_DISCIPLINE: "0.29.1",
    EXPERIMENTAL_AGENT_DISCIPLINE: "0.5.1",
}

# 検知層の 4 entry の `if` filter。
EXPECTED_IF_FILTERS = (
    "Bash(gh issue create:*)",
    "Bash(gh issue edit:*)",
    "Bash(gh pr create:*)",
    "Bash(gh pr edit:*)",
)
IF_FILTER_PATTERN = re.compile(r"\ABash\(gh (?P<cmd>[a-z]+ [a-z]+):\*\)\Z")

EXPECTED_AGENT_MODEL = "claude-sonnet-5"
EXPECTED_AGENT_TIMEOUT = 60

STEP0_HEADING = "## Step 0: defense-in-depth command guard"
STEP1_HEADING = "## Step 1: body content の抽出"
STEP2_HEADING_PREFIX = "## Step 2"

# Step 0 節の正典文 (見出し行を含む)。`<cmd>` は entry の `if` filter から導出した
# literal (`issue create` / `issue edit` / `pr create` / `pr edit`) に置換して照合する。
# 1. の `"gh" issue create` は全 entry 共通の固定例であり、置換しない。静的判定不能の
# 判定を先頭に置くのは、対象 subcommand が無い場合の早期 `ok: true` (5.) より前に
# 評価させ、`$(...)` 内にしか literal が無い command を通さないため。
STEP0_CMD_PLACEHOLDER = "<cmd>"
STEP0_CANONICAL_TEMPLATE = """\
## Step 0: defense-in-depth command guard

本 prompt 末尾の `## Hook input` セクションに hook input JSON が `$ARGUMENTS` 経由で interpolate されている。 そこから `tool_input.command` フィールドを取り出し、 以下の手順で検証対象の subcommand を決める。 hook config の `if` filter は best-effort であり、 compound command の各 subcommand と env-prefix を剥がした command は正規に評価される一方、 `$(...)` / バッククォート / `$VAR` を含む Bash では対象外でも本 hook が起動しうる。

1. まず command 全体を見て、 shell が実際に実行する command 置換 (`$(...)` / バッククォート) の中で `gh <cmd>` literal が command として実行される場合、 または subcommand の command 語の位置で literal が引用符で分断されている (`"gh" issue create` のような形) 場合は、 body を静的に判定できないため `{"ok": false, "reason": "body を静的な文字列 (--body の直接指定、 または既存ファイルへの --body-file) で渡す形に書き直すこと"}` を返して終了する (以降の手順には進まない)。 single quote の内側と、 引用符付き delimiter の heredoc 本文 (`<<'EOF'` 等) の内側は shell が実行しないため、 そこに現れる `$(...)` / バッククォートや、 引数・body 本文の中で command 名に言及しているだけの文字列はこれに当たらない。
2. command を区切り (`&&` / `||` / `;` / `|` / 改行) で subcommand に分割する。 分割の前に、 行末の `\` による行継続 (`\` の直後の改行) を取り除いて前後の行を 1 行に連結する。 引用符 (`'...'` / `"..."`) の内側と heredoc 本文 (`<<EOF` から終端 `EOF` まで) の内側にある区切りでは分割しない。
3. 各 subcommand の先頭にある `VAR=value` 群 (env-prefix) と wrapper (`command` / `env` / `sudo`) を剥がす。
4. 剥がした後の subcommand が **`gh <cmd>` literal で始まる** ものを検証対象とする。 alias / 別 command / global option を subcommand の前に置く形式 (`gh -R owner/repo ...`) は対象にしない。
5. 対象 subcommand が 1 つも無い場合のみ、 一切の semantic 検証をせずに即座に `{"ok": true}` を返して終了する (= `if` filter が best-effort で通した非対象 Bash)。
6. 対象 subcommand が複数ある場合は、 そのすべてを Step 1 以降で検証し、 1 つでも違反があれば `{"ok": false, "reason": ...}` とする。

該当する対象 subcommand ごとに Step 1 以降へ進む。 すべての対象 subcommand が通過した、 または Step 1 で検証をスキップされた場合は `{"ok": true}` を返す。
"""

# Step 1 節が含む、hook 時点で存在しない body file の扱い (fail-closed)。
STEP1_TOCTOU_RULE = (
    "PATH のファイルが hook 時点で存在しない (同じ command 内で生成される等) 場合は、 "
    "body を静的に判定できないため "
    '{"ok": false, "reason": "body file を別の Bash 呼び出しで先に生成するか、 '
    '--body の静的文字列で渡すこと"} を返す'
)

# Step 1 節が含む、相対 PATH の解決基準。同じ command 内で先行する `cd <dir>` をすべて
# 順に適用した dir を基準に解決し、存在する body file を不在と誤判定しない。
STEP1_RELATIVE_PATH_RULE = (
    "PATH が相対パスの場合は、 hook input の `cwd` を起点に、 同じ command 内でその "
    "subcommand より前にある `cd <dir>` の subcommand を先頭から順にすべて適用した dir "
    "(各 `<dir>` が相対パスならその時点の dir を基準に解決する) を基準に PATH を解決して"
    "から Read する"
)

# Step 1 節が含む、body を取得できない subcommand (editor 起動経路 / stdin 経路) の扱い。
# Step 0 は全対象 subcommand の検証を求めるため、その subcommand だけを飛ばして残りを
# 検証する。hook 全体を即 `ok: true` で終える文は置かない。
STEP1_SKIP_RULE = (
    "その subcommand は判定不能として本 Step 以降の検証をスキップし、 "
    "残りの対象 subcommand の検証を続ける"
)
STEP1_FORBIDDEN_EARLY_EXIT_PHRASE = "を返して終了 (= hook visibility 外"

# gh pr create entry の Step 3 (Closes 検証) が branch を読む起点ディレクトリ。先行する
# `cd <dir>` をすべて順に適用し、`gh pr create` が実際に動く dir の branch を読む。
STEP3_HEADING_PREFIX = "## Step 3: Closes 検証"
STEP4_HEADING_PREFIX = "## Step 4"
PR_CREATE_IF_FILTER = "Bash(gh pr create:*)"
STEP3_EFFECTIVE_CWD_RULE = (
    "hook input の `cwd` を起点に、 同じ command 内で対象 subcommand より前にある "
    "`cd <dir>` の subcommand を先頭から順にすべて適用した dir (各 `<dir>` が相対パスなら"
    "その時点の dir を基準に解決する) を本 Step の `<cwd>` とする (`cd` が無い場合は "
    "hook input の `cwd` がそのまま `<cwd>` になる)"
)

# command 全体の先頭 literal だけで判定する Step 0 の語。prompt 全文に置かない。
PROMPT_FORBIDDEN_HEAD_ONLY_PHRASES = (
    "先頭 literal のみ judge",
    "literal command head 限定",
    "compound 経路は対象外として ok:true",
    "command の先頭が",
    "先頭が **",
)

# `if` filter を fail-permissive と説明する冒頭段落の語。prompt 全文に置かない。
PROMPT_FORBIDDEN_FAIL_PERMISSIVE_PHRASE = (
    "完全に parse できず if filter が fail-permissive で fall through した場合"
)

# prompt 冒頭段落 (最初の `## ` 見出しより前) が述べる `if` filter の性質。
PROMPT_INTRO_REQUIRED_PHRASE = "best-effort"

# 非対象 Bash では agent が起動しない、Step 0 は先頭 literal だけで判定する、という
# 説明語。agent-discipline README 全文に置かない。
README_FORBIDDEN_PHRASES = (
    "起動さえしない",
    "LLM 呼び出しゼロ",
    "SPOF 露出なし",
    "影響ゼロ",
    "そもそも起動しない",
    "env-prefix 形式**",
    "compound command 経路",
    "先頭 literal 一致のみ判定",
    "literal で始まらなければ",
    "仮に検知層が compound を catch しても validate 不能",
    "fail-permissive で fall through",
)

# README の検知層「動作」bullet 群の範囲 (開始目印は終了目印の直前にある最後の出現)。
README_BEHAVIOR_MARKER = "**動作**:"
README_SPOF_MARKER = "**SPOF 緩和の設計**:"
README_DESIGN_HISTORY_MARKER = "**設計の変遷**"
README_KNOWN_LIMITATIONS_HEADING = "## 既知の制約"

# 動作 bullet 群の同一 bullet に共起させる語。
README_BEHAVIOR_KEYWORDS = ("best-effort", "Step 0", "$()")
# 既知の制約節の同一 bullet に共起させる語 (静的に判定できない command 置換の扱い)。
README_LIMITATION_SUBSTITUTION_KEYWORDS = ("$()", "バッククォート", "静的判定不能")
# 既知の制約節で TOCTOU を述べる bullet が含む語。
README_LIMITATION_TOCTOU_MARKER = "TOCTOU"
README_LIMITATION_TOCTOU_KEYWORD = "静的判定不能"
# SPOF 緩和の設計の範囲が述べる語 (非対象 Bash でも起動しうるが Step 0 で即終了する)。
README_SPOF_KEYWORDS = ("Step 0", "即終了")

LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+\.) ")
ROOT_README_VERSION_ROW = "| [{name}](#{name}) | {version} |"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def repo_relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def strip_whitespace(text: str) -> str:
    """空白文字 (改行を含む) をすべて除去する。"""
    return "".join(text.split())


def plugin_dir(plugin: str) -> Path:
    return ROOT / "plugins" / plugin


def hooks_path(plugin: str) -> Path:
    return plugin_dir(plugin) / "hooks" / "hooks.json"


def entry_label(plugin: str, if_filter: str) -> str:
    return f"{repo_relative(hooks_path(plugin))} の `{if_filter}` entry"


def agent_entries(plugin: str) -> dict[str, dict[str, object]]:
    """`matcher == "Bash"` group の type:agent entry を `if` ごとに返す。"""
    hooks = json.loads(read(hooks_path(plugin)))
    entries: dict[str, dict[str, object]] = {}
    for group in hooks["hooks"]["PreToolUse"]:
        if group.get("matcher") != "Bash":
            continue
        for handler in group["hooks"]:
            if handler.get("type") == "agent":
                entries[str(handler["if"])] = handler
    return entries


def command_literal(if_filter: str) -> str:
    """`Bash(gh issue create:*)` から `issue create` を取り出す。"""
    match = IF_FILTER_PATTERN.match(if_filter)
    if match is None:
        raise ValueError(f"想定外の if filter: {if_filter}")
    return match.group("cmd")


def expected_step0(if_filter: str) -> str:
    return STEP0_CANONICAL_TEMPLATE.replace(
        STEP0_CMD_PLACEHOLDER, command_literal(if_filter)
    )


def prompt_section(prompt: str, start_heading: str, end_prefix: str) -> str:
    """`start_heading` 行から、`end_prefix` で始まる次の行の直前までを返す。

    見出し行を含む。開始見出しが無ければ空文字を返す。
    """
    lines = prompt.splitlines(keepends=True)
    start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if start is None:
            if stripped == start_heading:
                start = index
            continue
        if stripped.startswith(end_prefix):
            return "".join(lines[start:index])
    if start is None:
        return ""
    return "".join(lines[start:])


def prompt_intro(prompt: str) -> str:
    """最初の `## ` 見出しより前の冒頭段落を返す。"""
    lines = prompt.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("## "):
            return "".join(lines[:index])
    return prompt


def text_between(text: str, start_marker: str, end_marker: str) -> str:
    """`end_marker` の直前にある最後の `start_marker` から `end_marker` の手前までを返す。"""
    end = text.find(end_marker)
    if end < 0:
        return ""
    start = text.rfind(start_marker, 0, end)
    if start < 0:
        return ""
    return text[start:end]


def markdown_h2_section(text: str, heading: str) -> str:
    """`heading` 行から次の `## ` 見出しまたは EOF までを返す (見出し行を含む)。"""
    lines = text.splitlines(keepends=True)
    start: int | None = None
    for index, line in enumerate(lines):
        if start is None:
            if line.rstrip("\n") == heading:
                start = index
            continue
        if line.startswith("## "):
            return "".join(lines[start:index])
    if start is None:
        return ""
    return "".join(lines[start:])


def list_items(text: str) -> list[str]:
    """箇条書き 1 項目 (入れ子の項目は別項目) を単位とする列を返す。

    空行と箇条書き項目の開始で区切る。箇条書きの記号で始まらない継続行は直前の
    項目に含める。
    """
    units: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            if current:
                units.append("\n".join(current))
                current = []
            continue
        if LIST_ITEM_PATTERN.match(line) and current:
            units.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        units.append("\n".join(current))
    return [unit for unit in units if LIST_ITEM_PATTERN.match(unit)]


def contains(haystack: str, needle: str) -> bool:
    return strip_whitespace(needle) in strip_whitespace(haystack)


def first_difference(actual: str, expected: str, width: int = 40) -> str:
    """空白除去後の 2 文字列の最初の差異を前後の文脈付きで示す。"""
    stripped_actual = strip_whitespace(actual)
    stripped_expected = strip_whitespace(expected)
    limit = min(len(stripped_actual), len(stripped_expected))
    position = next(
        (i for i in range(limit) if stripped_actual[i] != stripped_expected[i]),
        limit,
    )
    begin = max(0, position - width)
    return (
        f"空白除去後の {position} 文字目から不一致。"
        f" 実際: …{stripped_actual[begin : position + width]}…"
        f" / 期待: …{stripped_expected[begin : position + width]}…"
    )


class ContractTestCase(unittest.TestCase):
    def assert_phrase_absent(self, label: str, text: str, phrase: str) -> None:
        if not contains(text, phrase):
            return
        hits = [
            f"L{number}: {line.strip()[:120]}"
            for number, line in enumerate(text.splitlines(), start=1)
            if phrase in line
        ]
        joined = " / ".join(hits) if hits else "改行・空白をまたいで出現"
        self.fail(f"{label}: 「{phrase}」の言及が残っている: {joined}")

    def assert_scope_found(self, label: str, scope: str, hint: str) -> None:
        if not scope.strip():
            self.fail(f"{label}: 検査対象の箇所が見つからない ({hint})")

    def assert_keywords_cooccur_in_one_item(
        self, label: str, items: list[str], keywords: tuple[str, ...]
    ) -> None:
        counted = [
            (sum(contains(item, keyword) for keyword in keywords), item)
            for item in items
        ]
        if any(count == len(keywords) for count, _ in counted):
            return
        best_count, best_item = max(counted, default=(0, ""))
        missing = [keyword for keyword in keywords if not contains(best_item, keyword)]
        closest = " ".join(best_item.split())[:160] if best_count else "該当なし"
        self.fail(
            f"{label}: 語 ({', '.join(keywords)}) が同一の箇条書き項目にそろっていない。"
            f" 最も近い項目に欠けている語: {', '.join(missing) or 'なし'}"
            f" / その項目: {closest}"
        )

    def entries_or_fail(self, plugin: str) -> dict[str, dict[str, object]]:
        entries = agent_entries(plugin)
        self.assertEqual(
            set(EXPECTED_IF_FILTERS),
            set(entries),
            f"{repo_relative(hooks_path(plugin))}: type:agent entry の if filter 集合",
        )
        return entries


class Step0CanonicalTextTest(ContractTestCase):
    """(A) Step 0 節が正典文と一致する。"""

    def test_step0_section_matches_canonical_text(self) -> None:
        for plugin in PLUGIN_NAMES:
            entries = self.entries_or_fail(plugin)
            for if_filter in EXPECTED_IF_FILTERS:
                with self.subTest(plugin=plugin, entry=if_filter):
                    label = entry_label(plugin, if_filter)
                    section = prompt_section(
                        str(entries[if_filter]["prompt"]), STEP0_HEADING, STEP1_HEADING
                    )
                    self.assert_scope_found(
                        label,
                        section,
                        f"`{STEP0_HEADING}` から `{STEP1_HEADING}` までの節が無い",
                    )
                    expected = expected_step0(if_filter)
                    if strip_whitespace(section) != strip_whitespace(expected):
                        self.fail(
                            f"{label}: Step 0 節が正典文 (STEP0_CANONICAL_TEMPLATE の "
                            f"<cmd> = `{command_literal(if_filter)}`) と一致しない。 "
                            f"{first_difference(section, expected)}"
                        )


class Step1ToctouRuleTest(ContractTestCase):
    """(B) Step 1 節が、hook 時点で存在しない body file を fail-closed で扱う。"""

    def test_step1_section_states_missing_body_file_is_rejected(self) -> None:
        for plugin in PLUGIN_NAMES:
            entries = self.entries_or_fail(plugin)
            for if_filter in EXPECTED_IF_FILTERS:
                with self.subTest(plugin=plugin, entry=if_filter):
                    label = entry_label(plugin, if_filter)
                    section = prompt_section(
                        str(entries[if_filter]["prompt"]),
                        STEP1_HEADING,
                        STEP2_HEADING_PREFIX,
                    )
                    self.assert_scope_found(
                        label,
                        section,
                        f"`{STEP1_HEADING}` から `{STEP2_HEADING_PREFIX}` までの節が無い",
                    )
                    if not contains(section, STEP1_TOCTOU_RULE):
                        self.fail(
                            f"{label}: Step 1 節に TOCTOU 規則 (STEP1_TOCTOU_RULE) が無い: "
                            f"{STEP1_TOCTOU_RULE}"
                        )
                    if not contains(section, STEP1_RELATIVE_PATH_RULE):
                        self.fail(
                            f"{label}: Step 1 節に相対 PATH の解決規則 "
                            f"(STEP1_RELATIVE_PATH_RULE) が無い: "
                            f"{STEP1_RELATIVE_PATH_RULE}"
                        )

    def test_step1_section_skips_only_the_undecidable_subcommand(self) -> None:
        for plugin in PLUGIN_NAMES:
            entries = self.entries_or_fail(plugin)
            for if_filter in EXPECTED_IF_FILTERS:
                with self.subTest(plugin=plugin, entry=if_filter):
                    label = entry_label(plugin, if_filter)
                    section = prompt_section(
                        str(entries[if_filter]["prompt"]),
                        STEP1_HEADING,
                        STEP2_HEADING_PREFIX,
                    )
                    self.assert_scope_found(
                        label,
                        section,
                        f"`{STEP1_HEADING}` から `{STEP2_HEADING_PREFIX}` までの節が無い",
                    )
                    if not contains(section, STEP1_SKIP_RULE):
                        self.fail(
                            f"{label}: Step 1 節に、body を取得できない subcommand だけを "
                            f"飛ばす規則 (STEP1_SKIP_RULE) が無い: {STEP1_SKIP_RULE}"
                        )
                    self.assert_phrase_absent(
                        f"{label} の Step 1 節",
                        section,
                        STEP1_FORBIDDEN_EARLY_EXIT_PHRASE,
                    )


class Step3EffectiveCwdTest(ContractTestCase):
    """gh pr create の Step 3 が、先行する `cd <dir>` を branch の読み取り起点にする。"""

    def test_step3_reads_branch_from_effective_directory(self) -> None:
        for plugin in PLUGIN_NAMES:
            entries = self.entries_or_fail(plugin)
            with self.subTest(plugin=plugin):
                label = entry_label(plugin, PR_CREATE_IF_FILTER)
                prompt = str(entries[PR_CREATE_IF_FILTER]["prompt"])
                lines = prompt.splitlines(keepends=True)
                start = next(
                    (
                        index
                        for index, line in enumerate(lines)
                        if line.startswith(STEP3_HEADING_PREFIX)
                    ),
                    None,
                )
                section = ""
                if start is not None:
                    end = next(
                        (
                            index
                            for index in range(start + 1, len(lines))
                            if lines[index].startswith(STEP4_HEADING_PREFIX)
                        ),
                        len(lines),
                    )
                    section = "".join(lines[start:end])
                self.assert_scope_found(
                    label,
                    section,
                    f"`{STEP3_HEADING_PREFIX}` から `{STEP4_HEADING_PREFIX}` までの節が無い",
                )
                if not contains(section, STEP3_EFFECTIVE_CWD_RULE):
                    self.fail(
                        f"{label}: Step 3 節に起点ディレクトリの規則 "
                        f"(STEP3_EFFECTIVE_CWD_RULE) が無い: {STEP3_EFFECTIVE_CWD_RULE}"
                    )


class PromptHeadOnlyGuardAbsenceTest(ContractTestCase):
    """(C) 先頭 literal だけで判定する語と fail-permissive の説明が prompt に無い。"""

    def test_prompt_has_no_head_only_guard_phrases(self) -> None:
        for plugin in PLUGIN_NAMES:
            entries = self.entries_or_fail(plugin)
            for if_filter in EXPECTED_IF_FILTERS:
                prompt = str(entries[if_filter]["prompt"])
                label = entry_label(plugin, if_filter)
                for phrase in PROMPT_FORBIDDEN_HEAD_ONLY_PHRASES + (
                    PROMPT_FORBIDDEN_FAIL_PERMISSIVE_PHRASE,
                ):
                    with self.subTest(plugin=plugin, entry=if_filter, phrase=phrase):
                        self.assert_phrase_absent(f"{label} の prompt", prompt, phrase)

    def test_prompt_intro_describes_if_filter_as_best_effort(self) -> None:
        for plugin in PLUGIN_NAMES:
            entries = self.entries_or_fail(plugin)
            for if_filter in EXPECTED_IF_FILTERS:
                with self.subTest(plugin=plugin, entry=if_filter):
                    label = entry_label(plugin, if_filter)
                    intro = prompt_intro(str(entries[if_filter]["prompt"]))
                    self.assert_scope_found(
                        label, intro, "最初の `## ` 見出しより前の冒頭段落が無い"
                    )
                    if not contains(intro, PROMPT_INTRO_REQUIRED_PHRASE):
                        self.fail(
                            f"{label}: prompt 冒頭段落に「{PROMPT_INTRO_REQUIRED_PHRASE}」"
                            "が無い"
                        )


class AgentPromptSyncTest(ContractTestCase):
    """(D) 両 plugin の agent prompt と model / timeout が一致する。"""

    def test_agent_prompts_are_identical_across_plugins(self) -> None:
        base = self.entries_or_fail(AGENT_DISCIPLINE)
        fork = self.entries_or_fail(EXPERIMENTAL_AGENT_DISCIPLINE)
        for if_filter in EXPECTED_IF_FILTERS:
            with self.subTest(entry=if_filter):
                self.assertEqual(
                    base[if_filter]["prompt"],
                    fork[if_filter]["prompt"],
                    f"`{if_filter}` entry の prompt が "
                    f"{repo_relative(hooks_path(AGENT_DISCIPLINE))} と "
                    f"{repo_relative(hooks_path(EXPERIMENTAL_AGENT_DISCIPLINE))} "
                    "で一致しない",
                )

    def test_agent_model_and_timeout_are_pinned(self) -> None:
        for plugin in PLUGIN_NAMES:
            entries = self.entries_or_fail(plugin)
            for if_filter in EXPECTED_IF_FILTERS:
                with self.subTest(plugin=plugin, entry=if_filter):
                    label = entry_label(plugin, if_filter)
                    self.assertEqual(
                        EXPECTED_AGENT_MODEL, entries[if_filter].get("model"), label
                    )
                    self.assertEqual(
                        EXPECTED_AGENT_TIMEOUT, entries[if_filter].get("timeout"), label
                    )


class LintPromptSyncTest(ContractTestCase):
    """(E) 両 plugin の lint-prompt-sync.sh が通る。"""

    def test_lint_prompt_sync_passes(self) -> None:
        for plugin in PLUGIN_NAMES:
            script = plugin_dir(plugin) / "scripts" / "lint-prompt-sync.sh"
            with self.subTest(plugin=plugin):
                result = subprocess.run(
                    ["/bin/bash", str(script)],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                self.assertEqual(
                    0,
                    result.returncode,
                    f"{repo_relative(script)} が失敗: {result.stdout}{result.stderr}",
                )


class ReadmeForbiddenPhraseTest(ContractTestCase):
    """(F) agent-discipline README が、非対象 Bash で agent が起動しないと説明しない。"""

    def test_readme_has_no_never_invoked_claims(self) -> None:
        body = read(AGENT_DISCIPLINE_README)
        for phrase in README_FORBIDDEN_PHRASES:
            with self.subTest(phrase=phrase):
                self.assert_phrase_absent(
                    repo_relative(AGENT_DISCIPLINE_README), body, phrase
                )


class ReadmeRequiredPhraseTest(ContractTestCase):
    """(G) agent-discipline README が best-effort な if filter と Step 0 の扱いを述べる。"""

    def test_behavior_bullet_states_best_effort_filter_and_step0(self) -> None:
        label = (
            f"{repo_relative(AGENT_DISCIPLINE_README)} の検知層 "
            f"`{README_BEHAVIOR_MARKER}` 節"
        )
        scope = text_between(
            read(AGENT_DISCIPLINE_README), README_BEHAVIOR_MARKER, README_SPOF_MARKER
        )
        self.assert_scope_found(
            label,
            scope,
            f"`{README_SPOF_MARKER}` の手前に `{README_BEHAVIOR_MARKER}` が無い",
        )
        self.assert_keywords_cooccur_in_one_item(
            label, list_items(scope), README_BEHAVIOR_KEYWORDS
        )

    def test_known_limitations_state_substitution_is_not_statically_decidable(
        self,
    ) -> None:
        label = (
            f"{repo_relative(AGENT_DISCIPLINE_README)} の "
            f"`{README_KNOWN_LIMITATIONS_HEADING}` 節"
        )
        section = markdown_h2_section(
            read(AGENT_DISCIPLINE_README), README_KNOWN_LIMITATIONS_HEADING
        )
        self.assert_scope_found(
            label, section, f"`{README_KNOWN_LIMITATIONS_HEADING}` 見出しが無い"
        )
        self.assert_keywords_cooccur_in_one_item(
            label, list_items(section), README_LIMITATION_SUBSTITUTION_KEYWORDS
        )

    def test_known_limitations_toctou_items_state_not_statically_decidable(
        self,
    ) -> None:
        label = (
            f"{repo_relative(AGENT_DISCIPLINE_README)} の "
            f"`{README_KNOWN_LIMITATIONS_HEADING}` 節"
        )
        section = markdown_h2_section(
            read(AGENT_DISCIPLINE_README), README_KNOWN_LIMITATIONS_HEADING
        )
        self.assert_scope_found(
            label, section, f"`{README_KNOWN_LIMITATIONS_HEADING}` 見出しが無い"
        )
        toctou_items = [
            item
            for item in list_items(section)
            if contains(item, README_LIMITATION_TOCTOU_MARKER)
        ]
        if not toctou_items:
            self.fail(
                f"{label}: 「{README_LIMITATION_TOCTOU_MARKER}」を含む箇条書き項目が無い"
            )
        for item in toctou_items:
            if not contains(item, README_LIMITATION_TOCTOU_KEYWORD):
                self.fail(
                    f"{label}: 「{README_LIMITATION_TOCTOU_MARKER}」を含む項目に"
                    f"「{README_LIMITATION_TOCTOU_KEYWORD}」が無い: "
                    f"{' '.join(item.split())[:160]}"
                )

    def test_spof_design_states_step0_exits_immediately(self) -> None:
        label = (
            f"{repo_relative(AGENT_DISCIPLINE_README)} の `{README_SPOF_MARKER}` "
            f"から `{README_DESIGN_HISTORY_MARKER}` まで"
        )
        body = read(AGENT_DISCIPLINE_README)
        end = body.find(README_DESIGN_HISTORY_MARKER)
        start = body.rfind(README_SPOF_MARKER, 0, end) if end >= 0 else -1
        scope = body[start:end] if start >= 0 else ""
        self.assert_scope_found(
            label,
            scope,
            f"`{README_DESIGN_HISTORY_MARKER}` の手前に `{README_SPOF_MARKER}` が無い",
        )
        missing = [
            keyword for keyword in README_SPOF_KEYWORDS if not contains(scope, keyword)
        ]
        if missing:
            self.fail(f"{label}: 語が無い: {', '.join(missing)}")


def readme_version_line(path: Path) -> str:
    """`## バージョン` 見出しの直後にある最初の空でない行を返す。"""
    lines = read(path).splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "## バージョン":
            for following in lines[index + 1 :]:
                if following.strip():
                    return following.strip()
            return ""
    return ""


class VersionSyncTest(ContractTestCase):
    """(H) 両 plugin の version が 4 箇所で期待値に一致する。"""

    def test_versions_match_expected_in_all_four_places(self) -> None:
        marketplace = json.loads(read(MARKETPLACE_JSON))
        marketplace_versions = {
            str(entry["name"]): str(entry.get("version"))
            for entry in marketplace["plugins"]
        }
        root_readme = read(ROOT_README)
        for plugin, version in EXPECTED_VERSIONS.items():
            plugin_json = plugin_dir(plugin) / ".claude-plugin" / "plugin.json"
            plugin_readme = plugin_dir(plugin) / "README.md"
            with self.subTest(plugin=plugin, place="plugin.json"):
                self.assertEqual(
                    version,
                    json.loads(read(plugin_json)).get("version"),
                    repo_relative(plugin_json),
                )
            with self.subTest(plugin=plugin, place="marketplace.json"):
                self.assertEqual(
                    version,
                    marketplace_versions.get(plugin),
                    f"{repo_relative(MARKETPLACE_JSON)} の {plugin}",
                )
            with self.subTest(plugin=plugin, place="README.md table"):
                row = ROOT_README_VERSION_ROW.format(name=plugin, version=version)
                if row not in root_readme:
                    self.fail(
                        f"{repo_relative(ROOT_README)}: plugin 一覧テーブルに "
                        f"`{row}` が無い"
                    )
            with self.subTest(plugin=plugin, place="plugin README"):
                self.assertEqual(
                    f"v{version}",
                    readme_version_line(plugin_readme),
                    f"{repo_relative(plugin_readme)} の `## バージョン` 直下",
                )


class ExperimentalReadmeVersionTest(ContractTestCase):
    """(I) experimental-agent-discipline README の `## バージョン` 直下。"""

    def test_experimental_readme_version_line(self) -> None:
        self.assertEqual(
            f"v{EXPECTED_VERSIONS[EXPERIMENTAL_AGENT_DISCIPLINE]}",
            readme_version_line(EXPERIMENTAL_README),
            f"{repo_relative(EXPERIMENTAL_README)} の `## バージョン` 直下",
        )


if __name__ == "__main__":
    unittest.main()
