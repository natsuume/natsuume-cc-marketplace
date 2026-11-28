"""テキスト系文書だけの変更でレビュー gate を通す機能の契約テスト。

対象は次のとおり:

- 判定 lib `hooks/scripts/lib/text-paths.sh` (canonical は pre-push-review) の 2 関数
  - `is_text_document_path <path>`: repo 相対 path がテキスト系文書かを分類する
  - `is_text_only_range_in <target_cwd> <base_ref> <head>`: `<base_ref>` と `<head>` の
    merge-base から `<head>` までの net diff で変わった path が、すべてテキスト系文書かを
    判定する
- push gate 2 つ (`block-pre-push.sh` / `block-pre-push-codex.sh`) と merge gate
  (`block-pre-merge.sh`): テキスト系文書だけの変更では、pre-push-review の gate は code
  review の記録を要求せず (security review の記録は要求する)、他の 2 つは記録を要求せずに
  通す
- codex review wrapper 2 つ (`run-pre-push-codex-review.sh` / `run-pre-merge-codex-review.sh`):
  テキスト系文書だけの変更では codex review を起動せず、stdout に
  `Review skipped: text-only changes` の 1 行だけを出して exit 0 で終わり、記録を書かない

コードを含む変更・net diff が空の変更・判定に使う値が得られない場合は、従来どおり記録を
要求する側に倒れることも固定する。

git repository はすべて一時ディレクトリに作り、実行環境の git の system / global 設定を
読まないようにする。fixture を作った直後に、変わった path を Python 側で確かめてから判定や
gate を実行する (fixture の取り違えで期待どおりの結果に見えることを防ぐ)。
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
CORE_SCRIPTS = PLUGINS / "pre-push-review" / "hooks" / "scripts"
CODEX_SCRIPTS = PLUGINS / "pre-push-codex-review" / "hooks" / "scripts"
MERGE_SCRIPTS = PLUGINS / "pre-merge-cross-review" / "hooks" / "scripts"

TEXT_PATHS_LIB = CORE_SCRIPTS / "lib" / "text-paths.sh"
DIFF_HASH_LIB = CORE_SCRIPTS / "lib" / "diff-hash.sh"
CORE_PUSH_GATE = CORE_SCRIPTS / "block-pre-push.sh"
CODEX_PUSH_GATE = CODEX_SCRIPTS / "block-pre-push-codex.sh"
MERGE_GATE = MERGE_SCRIPTS / "block-pre-merge.sh"
PUSH_WRAPPER = CODEX_SCRIPTS / "run-pre-push-codex-review.sh"
MERGE_WRAPPER = MERGE_SCRIPTS / "run-pre-merge-codex-review.sh"

SKIP_LINE = "Review skipped: text-only changes"

# 判定に渡す base (完全な ref 名)。
MASTER_REF = "refs/heads/master"
ORIGIN_MASTER_REF = "refs/remotes/origin/master"

# gate が照合する記録 (git-dir 直下)。
CODE_REVIEWED_MARKER = ".claude-pre-push-code-reviewed"
SECURITY_REVIEWED_MARKER = ".claude-pre-push-security-reviewed"
PUSH_CODEX_PENDING = ".claude-pre-push-codex-reviewed.pending"
MERGE_FINAL_RECORD = ".claude-pre-merge-codex-reviewed"
MERGE_PENDING_RECORD = ".claude-pre-merge-codex-reviewed.pending"

# deny の理由文が案内する subagent_type。pre-push-review の gate がどのレビューを要求して
# いるかを、理由文に現れる subagent_type で確かめる。
CODE_REVIEWER = "pre-push-review:code-reviewer"
SECURITY_REVIEWER = "pre-push-review:security-reviewer"
PUSH_CODEX_REVIEWER = "pre-push-codex-review:codex-reviewer"
MERGE_CODEX_REVIEWER = "pre-merge-cross-review:codex-reviewer"

PR_NUMBER = 123

BASH = shutil.which("bash")
GIT = shutil.which("git")
JQ = shutil.which("jq")
NODE = shutil.which("node")

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


def _load_merge_wrapper_fixture():
    """merge wrapper の fake gh を持つ既存テスト module を読み込む。

    merge wrapper は gh から PR の番号・head SHA・base branch 名と base の commit を取る。
    その応答を返す fake gh は tests/test_pre_merge_auto_mark.py の wrapper テストが持つため、
    同じ fake を使う (同じ応答の組み立てを 2 か所に置かない)。`import` 文で書くと
    「sys.path 操作より前に import 文が来る」という lint 制約 (E402) に抵触するため、既存
    テストと同じ importlib 経由の明示 import にする。
    """
    return importlib.import_module("test_pre_merge_auto_mark")


_merge_wrapper_fixture = _load_merge_wrapper_fixture()

CALL_FUNCTION_SCRIPT = 'source "$1" || exit 99; shift; "$@"'
CLASSIFY_SCRIPT = (
    'source "$1" || exit 99; shift; '
    'for candidate in "$@"; do '
    'is_text_document_path "$candidate"; printf "%s\\n" "$?"; '
    "done"
)
REVIEW_HASH_SCRIPT = 'source "$1" || exit 99; compute_review_hash_in "$2" "$3"'

# テキスト系文書に分類される path (観点ごと。空白区切りで列挙する)。
TEXT_DOCUMENT_PATHS = {
    "拡張子 (大文字小文字を区別しない)": """
        README.md docs/guide.md README.MD docs/a.Md a.markdown docs/Guide.MarkDown
        a.rst a.RST a.adoc docs/manual.ADOC x.sh.md
    """.split(),
    "除外名を一部に含むだけの要素・basename・片方だけの組": """
        agentsx/r.md my-skills/r.md docs/skills.md output-styles.md commands.rst
        hooks/x.md prompts/x.md prompts/hooks/x.md a/hooks/b/prompts/x.md
        .github/README.md github/prompts/x.md docs/instructions/x.md chatmodes/x.md
        docs/claude.md.rst CLAUDE-notes.md
    """.split(),
    "日本語の path": "docs/日本語.md 日本語/説明.md ドキュメント/手順.ADOC".split(),
}

# テキスト系文書に分類されない path (観点ごと。空白区切りで列挙する)。
NON_TEXT_PATHS = {
    "対象外の拡張子・拡張子なし": """
        notes.txt README.TXT requirements.txt a.mdx docs/page.MDX tool.py x.md.sh
        Makefile README README. dir.md/file docs/日本語.txt
    """.split(),
    "除外するディレクトリ要素 (深さを問わない)": """
        agents/r.md plugins/x/agents/r.md a/b/c/skills/d/SKILL.md commands/c.md
        rules/r.md output-styles/o.md .claude/notes.md .agents/x.md .cursor/x.md
        .gemini/x.md .codex/x.md .windsurf/x.md .clinerules/x.md .roo/x.md .kiro/x.md
        .continue/x.md .junie/x.md .amazonq/x.md sub/dir/.roo/rules-code/x.md
    """.split(),
    "除外するディレクトリ要素の大文字小文字違い": """
        Agents/x.md docs/COMMANDS/c.md Skills/s/SKILL.md Rules/r.md
        Output-Styles/x.md .Claude/x.md .CURSOR/x.md .AmazonQ/x.md .ClineRules/x.md
    """.split(),
    "連続する 2 つのディレクトリ要素": """
        hooks/prompts/p.md plugins/x/hooks/prompts/p.md .github/instructions/x.md
        .github/prompts/x.md .github/chatmodes/x.chatmode.md sub/.github/prompts/x.md
        HOOKS/Prompts/x.md .GitHub/Instructions/x.md .Github/CHATMODES/x.md
    """.split(),
    "除外する basename": """
        CLAUDE.md docs/CLAUDE.md CLAUDE.local.md AGENTS.md sub/AGENTS.override.md
        GEMINI.md .github/copilot-instructions.md docs/copilot-instructions.md
        claude.md Agents.md gemini.MD Claude.Local.md Copilot-Instructions.MD
        agents.Override.md
    """.split(),
    "日本語の path を含む除外": "日本語/agents/説明.md .claude/日本語.md 資料/CLAUDE.md".split(),
}

# bash 3.2 (macOS) に無い構文。lib に含まれないことを静的に確かめる。
BASH4_ONLY_CONSTRUCTS = (",,}", "^^}", "declare -A", "local -A", "mapfile", "readarray")

# merge gate 用の fake gh。`pr view` に gh-config.json の PR 情報を返す。
FAKE_GH_TEMPLATE = """#!{python}
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "gh-config.json"), encoding="utf-8") as fp:
    config = json.load(fp)

if sys.argv[1:3] == ["pr", "view"]:
    print(json.dumps(config["pr"]))
sys.exit(0)
"""

# fake codex companion は、起動されたことを記録してから review 本文を出す。
COMPANION_LOG_ENV = "TEXT_ONLY_SKIP_TEST_COMPANION_LOG"
COMPANION_RELATIVE_PATH = (
    ".claude/plugins/cache/openai-codex/codex/1.0.0/scripts/codex-companion.mjs"
)
FAKE_COMPANION_SCRIPT = (
    'import { appendFileSync } from "node:fs";\n'
    f"appendFileSync(process.env.{COMPANION_LOG_ENV}, "
    'process.argv.slice(2).join(" ") + "\\n");\n'
    'process.stdout.write("# Codex Review\\n\\nNo findings.\\n");\n'
)


def wrapper_body_lines(stdout: str) -> list[str]:
    """wrapper の stdout から、terminal sentinel の案内行・終了行と空行を除いた行。"""
    return [
        line
        for line in stdout.splitlines()
        if line.strip()
        and not line.startswith("terminal sentinel: ")
        and not line.startswith("terminal sentinel end run=")
    ]


def run_command(
    command: list[str], cwd: Path, env: dict[str, str], stdin: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        input=stdin,
        capture_output=True,
        check=False,
        timeout=180,
    )


class Repository:
    """一時ディレクトリ上の git repository を操作する fixture helper。"""

    def __init__(self, path: Path, env: dict[str, str]) -> None:
        self.path = path
        self.env = env

    def git(self, *args: str, cwd: Path | None = None) -> str:
        assert GIT is not None
        result = run_command([GIT, *args], cwd or self.path, self.env)
        if result.returncode != 0:
            raise AssertionError(
                f"git {' '.join(args)} が失敗しました: "
                f"{result.stderr.decode('utf-8', errors='surrogateescape')}"
            )
        return result.stdout.decode("utf-8", errors="surrogateescape")

    def write(self, relative: str, content: str = "content\n") -> None:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def remove(self, relative: str) -> None:
        (self.path / relative).unlink()

    def commit_all(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def git_dir(self) -> Path:
        return Path(self.git("rev-parse", "--absolute-git-dir").strip())

    def changed_paths(self, base: str, head: str = "HEAD") -> list[str]:
        """merge-base から head までの net diff で変わった path (rename は削除と追加に分ける)。"""
        merge_base = self.git("merge-base", base, head).strip()
        output = self.git(
            *("-c", "core.quotePath=false", "diff", "--name-only", "-z"),
            *("--no-renames", merge_base, head),
        )
        return sorted(path for path in output.split("\0") if path)


@unittest.skipUnless(BASH and GIT, "requires bash and git")
class WorkspaceTestCase(unittest.TestCase):
    """隔離した一時ディレクトリと git の環境を用意する共通 base。"""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # macOS の一時ディレクトリは symlink なので、解決済みの path を使う。
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        tmp = self.root / "tmp"
        tmp.mkdir()
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_") and key != "XDG_CONFIG_HOME"
        }
        env["HOME"] = str(self.home)
        env["TMPDIR"] = str(tmp)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CEILING_DIRECTORIES"] = str(self.root)
        for role in ("AUTHOR", "COMMITTER"):
            env[f"GIT_{role}_NAME"] = "Text Only Skip Test"
            env[f"GIT_{role}_EMAIL"] = "text-only-skip@example.invalid"
        self.env = env

    def create_repository(self, name: str = "repo") -> Repository:
        """master に base commit (文書 2 つとコード 1 つ) を持ち、feature branch に
        切り替えた repository を作る。"""
        path = self.root / name
        path.mkdir()
        repository = Repository(path, self.env)
        repository.git("-c", "init.defaultBranch=master", "init", "-q")
        repository.write("README.md", "# base\n")
        repository.write("docs/guide.md", "guide\n")
        repository.write("tool.py", "print('base')\n")
        repository.commit_all("base")
        repository.git("switch", "-q", "-c", "feature")
        return repository

    def create_repository_with_origin(self) -> Repository:
        """create_repository に bare な origin を付け、master を push して origin/HEAD を
        master に向ける。"""
        repository = self.create_repository()
        origin = self.root / "origin.git"
        repository.git("init", "-q", "--bare", str(origin))
        repository.git("remote", "add", "origin", str(origin))
        repository.git("push", "-q", "origin", "master")
        repository.git("remote", "set-head", "origin", "master")
        return repository

    def commit_documents(self, repository: Repository) -> None:
        """文書だけを変える commit を 2 つ積む。"""
        repository.write("README.md", "# updated\n")
        repository.commit_all("update readme")
        repository.write("docs/new.md", "new\n")
        repository.commit_all("add a document")
        self.assertEqual(
            repository.changed_paths(MASTER_REF), ["README.md", "docs/new.md"]
        )

    def commit_mixed_change(self, repository: Repository) -> None:
        """文書とコードを一緒に変える commit を積む。"""
        repository.write("README.md", "# updated\n")
        repository.write("tool.py", "print('changed')\n")
        repository.commit_all("mixed change")
        self.assertEqual(repository.changed_paths(MASTER_REF), ["README.md", "tool.py"])

    def add_and_remove_a_document(self, repository: Repository) -> None:
        """文書を足す commit と消す commit を積む (net diff は空)。"""
        repository.write("docs/temporary.md", "temporary\n")
        repository.commit_all("add a temporary document")
        repository.remove("docs/temporary.md")
        repository.commit_all("remove the temporary document")
        self.assertEqual(repository.changed_paths(MASTER_REF), [])

    def call_function(
        self, function: str, *arguments: str, cwd: Path | None = None
    ) -> int:
        """判定 lib を source して関数を呼び、終了コードを返す。"""
        assert BASH is not None
        command = [BASH, "-c", CALL_FUNCTION_SCRIPT, "text-paths-test"]
        command += [str(TEXT_PATHS_LIB), function, *arguments]
        return run_command(command, cwd or self.root, self.env).returncode

    def range_status(
        self, repository: Repository, base: str = MASTER_REF, head: str = "HEAD"
    ) -> int:
        return self.call_function(
            "is_text_only_range_in", str(repository.path), base, head
        )

    def run_hook(
        self, hook: Path, command: str, repository: Repository
    ) -> subprocess.CompletedProcess[bytes]:
        """PreToolUse (Bash) の payload で gate を実行する (gate 自体は exit 0 で終わる)。"""
        assert BASH is not None
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(repository.path),
        }
        stdin = json.dumps(payload).encode("utf-8")
        result = run_command([BASH, str(hook)], repository.path, self.env, stdin)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        return result

    def assert_passed(self, result: subprocess.CompletedProcess[bytes]) -> None:
        """gate が何も出さずに通したこと。"""
        self.assertEqual(result.stdout, b"", result.stdout.decode("utf-8"))

    def deny_reason(self, result: subprocess.CompletedProcess[bytes]) -> str:
        """gate の deny の理由文 (deny でなければ失敗にする)。"""
        self.assertTrue(result.stdout.strip(), "gate が deny を返していない")
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        return output["permissionDecisionReason"]


class TextDocumentPathTest(WorkspaceTestCase):
    """is_text_document_path の分類。"""

    def classify(self, paths: list[str]) -> dict[str, int]:
        assert BASH is not None
        command = [BASH, "-c", CLASSIFY_SCRIPT, "text-paths-test"]
        result = run_command(
            [*command, str(TEXT_PATHS_LIB), *paths], self.root, self.env
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        statuses = result.stdout.decode("utf-8").splitlines()
        self.assertEqual(len(statuses), len(paths), result.stdout.decode("utf-8"))
        return {path: int(status) for path, status in zip(paths, statuses)}

    def assert_classified_as(self, groups: dict[str, list[str]], expected: int) -> None:
        for group, paths in groups.items():
            with self.subTest(group=group):
                statuses = self.classify(paths)
                mismatched = [path for path in paths if statuses[path] != expected]
                self.assertEqual(mismatched, [], f"{expected} にならない path")

    def test_text_document_paths_return_0(self) -> None:
        self.assert_classified_as(TEXT_DOCUMENT_PATHS, 0)

    def test_other_paths_return_1(self) -> None:
        self.assert_classified_as(NON_TEXT_PATHS, 1)


class TextPathsLibSourceTest(unittest.TestCase):
    """判定 lib が bash 3.2 (macOS) で動く書き方であること。"""

    def test_lib_avoids_constructs_missing_from_bash_3_2(self) -> None:
        # コメント行は構文の名前を説明に含みうるため対象外にする。
        code = "\n".join(
            line
            for line in TEXT_PATHS_LIB.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        for construct in BASH4_ONLY_CONSTRUCTS:
            with self.subTest(construct=construct):
                self.assertNotIn(construct, code)


class TextOnlyRangeTest(WorkspaceTestCase):
    """is_text_only_range_in の判定。"""

    def test_document_only_changes_return_0(self) -> None:
        repository = self.create_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("update readme")
        repository.write("docs/new.md", "new\n")
        repository.write("docs/notes.rst", "notes\n")
        repository.write("docs/manual.ADOC", "manual\n")
        repository.remove("docs/guide.md")
        head = repository.commit_all("add and remove documents")
        self.assertEqual(
            repository.changed_paths(MASTER_REF),
            "README.md docs/guide.md docs/manual.ADOC docs/new.md docs/notes.rst".split(),
        )
        for head_argument in ("HEAD", head):
            with self.subTest(head=head_argument):
                self.assertEqual(self.range_status(repository, head=head_argument), 0)

    def test_japanese_document_names_return_0(self) -> None:
        repository = self.create_repository()
        repository.write("docs/日本語の手順.md", "手順\n")
        repository.write("設計/概要.rst", "概要\n")
        repository.commit_all("日本語の文書を足す")
        self.assertEqual(
            repository.changed_paths(MASTER_REF),
            sorted(["docs/日本語の手順.md", "設計/概要.rst"]),
        )
        self.assertEqual(self.range_status(repository), 0)

    def test_empty_target_cwd_uses_current_directory(self) -> None:
        repository = self.create_repository()
        self.commit_documents(repository)
        status = self.call_function(
            "is_text_only_range_in", "", MASTER_REF, "HEAD", cwd=repository.path
        )
        self.assertEqual(status, 0)

    def enable_diff_relative(self, repository: Repository) -> Path:
        """利用者の設定に `diff.relative=true` を置き、cwd に使う subdirectory を返す。"""
        repository.git("config", "diff.relative", "true")
        return repository.path / "docs"

    def test_subdirectory_call_sees_documents_outside_it(self) -> None:
        """`diff.relative=true` でも、subdirectory の外の文書の変更を見て 0 を返す。"""
        repository = self.create_repository()
        subdirectory = self.enable_diff_relative(repository)
        repository.write("README.md", "# updated\n")
        repository.commit_all("update readme")
        # 設定が効く diff では、subdirectory の外の変更が消える (fixture の確認)。
        relative = repository.git(
            "diff", "--name-only", MASTER_REF, "HEAD", cwd=subdirectory
        )
        self.assertEqual(relative, "")
        cases = {
            "target_cwd が空で cwd が subdirectory": ("", subdirectory),
            "target_cwd が subdirectory": (str(subdirectory), self.root),
        }
        for label, (target_cwd, cwd) in cases.items():
            with self.subTest(case=label):
                status = self.call_function(
                    "is_text_only_range_in", target_cwd, MASTER_REF, "HEAD", cwd=cwd
                )
                self.assertEqual(status, 0)

    def test_subdirectory_call_sees_code_outside_it(self) -> None:
        """`diff.relative=true` でも、subdirectory の外のコードの変更を見て 1 を返す。"""
        repository = self.create_repository()
        subdirectory = self.enable_diff_relative(repository)
        repository.write("docs/new.md", "new\n")
        repository.write("tool.py", "print('changed')\n")
        repository.commit_all("docs and code")
        relative = repository.git(
            "diff", "--name-only", MASTER_REF, "HEAD", cwd=subdirectory
        )
        self.assertEqual(relative.split(), ["new.md"])
        status = self.call_function(
            "is_text_only_range_in", "", MASTER_REF, "HEAD", cwd=subdirectory
        )
        self.assertEqual(status, 1)

    def test_code_change_returns_1(self) -> None:
        repository = self.create_repository()
        self.commit_mixed_change(repository)
        self.assertEqual(self.range_status(repository), 1)

    def test_code_renamed_to_markdown_returns_1(self) -> None:
        repository = self.create_repository()
        repository.git("mv", "tool.py", "tool.md")
        repository.git("commit", "-q", "-m", "rename code to markdown")
        # rename を検出する diff では、変わった path が tool.md だけに見える。
        renamed = repository.git("diff", "--name-only", "-M", MASTER_REF, "HEAD")
        self.assertEqual(renamed.split(), ["tool.md"])
        self.assertEqual(repository.changed_paths(MASTER_REF), ["tool.md", "tool.py"])
        self.assertEqual(self.range_status(repository), 1)

    def test_empty_net_diff_returns_1(self) -> None:
        repository = self.create_repository()
        with self.subTest(case="commit が無い"):
            self.assertEqual(self.range_status(repository), 1)
        self.add_and_remove_a_document(repository)
        with self.subTest(case="文書を足して消した"):
            self.assertEqual(self.range_status(repository), 1)

    def test_missing_base_ref_returns_1(self) -> None:
        repository = self.create_repository()
        self.commit_documents(repository)
        missing_base = "refs/remotes/origin/missing"
        self.assertEqual(self.range_status(repository, base=missing_base), 1)

    def test_unrelated_history_returns_1(self) -> None:
        repository = self.create_repository()
        repository.git("switch", "-q", "--orphan", "unrelated")
        repository.write("README.md", "# unrelated\n")
        repository.commit_all("unrelated documents")
        assert GIT is not None
        merge_base = run_command(
            [GIT, "merge-base", MASTER_REF, "HEAD"], repository.path, self.env
        )
        self.assertNotEqual(merge_base.returncode, 0, "merge-base が見つかった")
        self.assertEqual(self.range_status(repository), 1)


@unittest.skipUnless(JQ, "push gate integration requires jq")
class PushGateTest(WorkspaceTestCase):
    """2 つの push gate の、テキスト系文書だけの push とコードを含む push の扱い。"""

    def run_gate(
        self, gate: Path, repository: Repository
    ) -> subprocess.CompletedProcess[bytes]:
        return self.run_hook(gate, "git push origin HEAD", repository)

    def write_core_records(
        self, repository: Repository, *, code: bool, security: bool
    ) -> None:
        """今の状態のハッシュ (diff-hash.sh の compute_review_hash_in) で pre-push-review の
        記録を書く。"""
        assert BASH is not None
        command = [BASH, "-c", REVIEW_HASH_SCRIPT, "review-hash", str(DIFF_HASH_LIB)]
        result = run_command(
            [*command, str(repository.path), "master"], repository.path, self.env
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        review_hash = result.stdout.decode("utf-8").strip()
        self.assertRegex(review_hash, r"^[0-9a-f]{64}$")
        git_dir = repository.git_dir()
        for enabled, name in (
            (code, CODE_REVIEWED_MARKER),
            (security, SECURITY_REVIEWED_MARKER),
        ):
            if enabled:
                (git_dir / name).write_text(review_hash, encoding="utf-8")

    # -- pre-push-review ---------------------------------------------------

    def test_core_gate_passes_document_only_push_with_security_record_only(
        self,
    ) -> None:
        repository = self.create_repository_with_origin()
        self.commit_documents(repository)
        self.write_core_records(repository, code=False, security=True)
        self.assert_passed(self.run_gate(CORE_PUSH_GATE, repository))

    def test_core_gate_asks_only_for_security_review_on_document_only_push(
        self,
    ) -> None:
        repository = self.create_repository_with_origin()
        self.commit_documents(repository)
        reason = self.deny_reason(self.run_gate(CORE_PUSH_GATE, repository))
        self.assertIn(SECURITY_REVIEWER, reason)
        self.assertNotIn(CODE_REVIEWER, reason)

    def test_core_gate_denies_document_only_push_with_code_record_only(self) -> None:
        repository = self.create_repository_with_origin()
        self.commit_documents(repository)
        self.write_core_records(repository, code=True, security=False)
        reason = self.deny_reason(self.run_gate(CORE_PUSH_GATE, repository))
        self.assertIn(SECURITY_REVIEWER, reason)

    def test_core_gate_asks_for_both_reviews_on_code_push(self) -> None:
        repository = self.create_repository_with_origin()
        self.commit_mixed_change(repository)
        with self.subTest(records="なし"):
            reason = self.deny_reason(self.run_gate(CORE_PUSH_GATE, repository))
            self.assertIn(CODE_REVIEWER, reason)
            self.assertIn(SECURITY_REVIEWER, reason)
        self.write_core_records(repository, code=False, security=True)
        with self.subTest(records="security review だけ"):
            reason = self.deny_reason(self.run_gate(CORE_PUSH_GATE, repository))
            self.assertIn(CODE_REVIEWER, reason)

    def test_core_gate_passes_code_push_with_both_records(self) -> None:
        repository = self.create_repository_with_origin()
        self.commit_mixed_change(repository)
        self.write_core_records(repository, code=True, security=True)
        self.assert_passed(self.run_gate(CORE_PUSH_GATE, repository))

    # -- pre-push-codex-review ---------------------------------------------

    def test_codex_gate_passes_document_only_push_without_record(self) -> None:
        repository = self.create_repository_with_origin()
        self.commit_documents(repository)
        self.assert_passed(self.run_gate(CODEX_PUSH_GATE, repository))

    def test_codex_gate_denies_code_push_without_record(self) -> None:
        repository = self.create_repository_with_origin()
        self.commit_mixed_change(repository)
        reason = self.deny_reason(self.run_gate(CODEX_PUSH_GATE, repository))
        self.assertIn(PUSH_CODEX_REVIEWER, reason)

    # -- 両方の gate --------------------------------------------------------

    def test_gates_compare_with_the_remote_base_branch(self) -> None:
        """ローカルの master だけが進んだコードの commit は、文書だけの branch の上に
        あっても push の差分に含める (base は remote-tracking ref)。"""
        repository = self.create_repository_with_origin()
        repository.git("switch", "-q", "master")
        repository.write("tool.py", "print('unpushed')\n")
        repository.commit_all("unpushed code on local master")
        repository.git("switch", "-q", "-c", "docs-on-local-master")
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        self.assertEqual(repository.changed_paths(MASTER_REF), ["README.md"])
        self.assertEqual(
            repository.changed_paths(ORIGIN_MASTER_REF), ["README.md", "tool.py"]
        )
        with self.subTest(gate="pre-push-review"):
            reason = self.deny_reason(self.run_gate(CORE_PUSH_GATE, repository))
            self.assertIn(CODE_REVIEWER, reason)
        with self.subTest(gate="pre-push-codex-review"):
            reason = self.deny_reason(self.run_gate(CODEX_PUSH_GATE, repository))
            self.assertIn(PUSH_CODEX_REVIEWER, reason)


@unittest.skipUnless(JQ, "merge gate integration requires jq")
class MergeGateTest(WorkspaceTestCase):
    """merge gate の、テキスト系文書だけの PR とそれ以外の PR の扱い。"""

    def setUp(self) -> None:
        super().setUp()
        self.fake_bin_dir = self.root / "fake-bin"
        self.fake_bin_dir.mkdir()
        gh_path = self.fake_bin_dir / "gh"
        gh_path.write_text(
            FAKE_GH_TEMPLATE.format(python=sys.executable), encoding="utf-8"
        )
        gh_path.chmod(gh_path.stat().st_mode | stat.S_IXUSR)
        self.env["PATH"] = f"{self.fake_bin_dir}{os.pathsep}{self.env['PATH']}"

    def configure_pr(self, head: str, base_name: str | None = "master") -> None:
        """fake gh が返す PR 情報を設定する (base_name が None なら baseRefName を含めない)。"""
        pr: dict[str, object] = {"number": PR_NUMBER, "headRefOid": head}
        if base_name is not None:
            pr["baseRefName"] = base_name
        (self.fake_bin_dir / "gh-config.json").write_text(
            json.dumps({"pr": pr}, ensure_ascii=False), encoding="utf-8"
        )

    def record_path(self, repository: Repository) -> Path:
        return repository.git_dir() / MERGE_FINAL_RECORD

    def run_gate(self, repository: Repository) -> subprocess.CompletedProcess[bytes]:
        command = f"gh pr merge {PR_NUMBER} --squash"
        return self.run_hook(MERGE_GATE, command, repository)

    def assert_record_deny(self, result: subprocess.CompletedProcess[bytes]) -> None:
        """記録を要求する deny (codex-reviewer の起動を案内する) であること。"""
        self.assertIn(MERGE_CODEX_REVIEWER, self.deny_reason(result))

    def assert_record_is_checked(self, repository: Repository) -> None:
        """判定せずに記録の照合へ進むこと (記録が無ければ deny、あれば通す)。"""
        record = self.record_path(repository)
        record.unlink(missing_ok=True)
        self.assert_record_deny(self.run_gate(repository))
        record.write_text(
            f"pr={PR_NUMBER}\nhead={repository.head()}\n", encoding="utf-8"
        )
        self.assert_passed(self.run_gate(repository))
        record.unlink()

    def test_merge_gate_passes_document_only_pr_without_record(self) -> None:
        repository = self.create_repository_with_origin()
        self.commit_documents(repository)
        self.configure_pr(repository.head())
        self.assertFalse(self.record_path(repository).exists())
        self.assert_passed(self.run_gate(repository))

    def test_merge_gate_checks_record_for_code_pr(self) -> None:
        repository = self.create_repository_with_origin()
        self.commit_mixed_change(repository)
        self.configure_pr(repository.head())
        self.assert_record_is_checked(repository)

    def test_merge_gate_checks_record_when_base_branch_name_is_unusable(self) -> None:
        repository = self.create_repository_with_origin()
        self.commit_documents(repository)
        cases = {
            "baseRefName が無い": None,
            "空": "",
            "- 始まり": "-master",
            "branch 名として不正": "bad..name",
            "remote に無い branch": "develop",
        }
        for label, base_name in cases.items():
            with self.subTest(case=label):
                self.configure_pr(repository.head(), base_name)
                self.assert_record_is_checked(repository)

    def test_merge_gate_checks_record_when_head_sha_is_not_full_lowercase(
        self,
    ) -> None:
        repository = self.create_repository_with_origin()
        self.commit_documents(repository)
        head = repository.head()
        for label, head_value in {"大文字": head.upper(), "短縮": head[:12]}.items():
            with self.subTest(case=label):
                self.configure_pr(head_value)
                self.assertFalse(self.record_path(repository).exists())
                self.assert_record_deny(self.run_gate(repository))

    def test_merge_gate_checks_record_when_net_diff_is_empty(self) -> None:
        repository = self.create_repository_with_origin()
        self.add_and_remove_a_document(repository)
        self.configure_pr(repository.head())
        self.assert_record_is_checked(repository)


@unittest.skipUnless(NODE, "wrapper integration requires node")
class WrapperTestCase(WorkspaceTestCase):
    """fake codex companion を HOME の plugin cache に置き、wrapper を実行する共通 base。

    fake companion は起動されると COMPANION_LOG_ENV のファイルに追記するため、その有無で
    codex review が起動されたかを確かめる。
    """

    def setUp(self) -> None:
        super().setUp()
        assert NODE is not None
        # HOME を差し替えても node を解決できるよう、実 node のディレクトリを PATH に通す。
        real_node = subprocess.check_output(
            [NODE, "-e", "process.stdout.write(process.execPath)"]
        ).decode("utf-8")
        self.env["PATH"] = f"{Path(real_node).parent}{os.pathsep}{self.env['PATH']}"
        self.companion_log = self.root / "companion-invocations.log"
        self.env[COMPANION_LOG_ENV] = str(self.companion_log)
        companion = self.home / COMPANION_RELATIVE_PATH
        companion.parent.mkdir(parents=True)
        companion.write_text(FAKE_COMPANION_SCRIPT, encoding="utf-8")

    def run_wrapper(
        self, wrapper: Path, repository: Repository
    ) -> subprocess.CompletedProcess[str]:
        assert BASH is not None
        result = run_command([BASH, str(wrapper)], repository.path, self.env)
        return subprocess.CompletedProcess(
            result.args,
            result.returncode,
            result.stdout.decode("utf-8"),
            result.stderr.decode("utf-8"),
        )

    def assert_skipped(self, result: subprocess.CompletedProcess[str]) -> None:
        """codex review を起動せず、stdout の本文が skip の 1 行だけで exit 0 であること。"""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(wrapper_body_lines(result.stdout), [SKIP_LINE], result.stdout)
        self.assertFalse(self.companion_log.exists(), "codex companion が起動された")

    def assert_reviewed(self, result: subprocess.CompletedProcess[str]) -> None:
        """skip せずに codex review を起動して exit 0 で終わったこと。"""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(SKIP_LINE, result.stdout)
        self.assertTrue(self.companion_log.exists(), result.stderr)


class PushWrapperTest(WrapperTestCase):
    """pre-push-codex-review の wrapper。"""

    def test_push_wrapper_skips_document_only_branch(self) -> None:
        repository = self.create_repository_with_origin()
        self.commit_documents(repository)
        pending = repository.git_dir() / PUSH_CODEX_PENDING
        pending.write_text("stale\n", encoding="utf-8")
        self.assert_skipped(self.run_wrapper(PUSH_WRAPPER, repository))
        self.assertFalse(pending.exists(), "pending attestation が残っている")

    def test_push_wrapper_reviews_code_branch(self) -> None:
        repository = self.create_repository_with_origin()
        self.commit_mixed_change(repository)
        self.assert_reviewed(self.run_wrapper(PUSH_WRAPPER, repository))
        self.assertTrue((repository.git_dir() / PUSH_CODEX_PENDING).exists())


@unittest.skipUnless(JQ, "merge wrapper integration requires jq")
class MergeWrapperTest(WrapperTestCase):
    """pre-merge-cross-review の wrapper。"""

    def install_fake_gh(self, repository: Repository) -> None:
        """PR の head を repository の HEAD、base を origin の master にした fake gh を
        PATH の先頭に置く (fake gh は tests/test_pre_merge_auto_mark.py のものを使う)。"""
        directory = self.root / "merge-wrapper-gh"
        directory.mkdir()
        fixture = _merge_wrapper_fixture.PreMergeCodexWrapperTest()
        bin_dir = fixture.install_fake_gh(directory, repository.path)
        self.env["PATH"] = f"{bin_dir}{os.pathsep}{self.env['PATH']}"

    def records(self, repository: Repository) -> tuple[Path, Path]:
        git_dir = repository.git_dir()
        return git_dir / MERGE_FINAL_RECORD, git_dir / MERGE_PENDING_RECORD

    def record_content(self, repository: Repository) -> str:
        return f"pr={_merge_wrapper_fixture.PR_NUMBER}\nhead={repository.head()}\n"

    def test_merge_wrapper_skips_document_only_pr(self) -> None:
        repository = self.create_repository_with_origin()
        self.commit_documents(repository)
        self.install_fake_gh(repository)
        final, pending = self.records(repository)
        final.write_text(self.record_content(repository), encoding="utf-8")
        pending.write_text(self.record_content(repository), encoding="utf-8")
        self.assert_skipped(self.run_wrapper(MERGE_WRAPPER, repository))
        self.assertFalse(final.exists(), "起動前の final attestation が残っている")
        self.assertFalse(pending.exists(), "pending attestation が残っている")

    def test_merge_wrapper_reviews_code_pr(self) -> None:
        repository = self.create_repository_with_origin()
        self.commit_mixed_change(repository)
        self.install_fake_gh(repository)
        self.assert_reviewed(self.run_wrapper(MERGE_WRAPPER, repository))
        _, pending = self.records(repository)
        self.assertEqual(
            pending.read_text(encoding="utf-8"), self.record_content(repository)
        )

    def test_merge_wrapper_keeps_failing_on_empty_net_diff(self) -> None:
        """net diff が空の PR は skip せず、従来どおり空の差分として失敗する。"""
        repository = self.create_repository_with_origin()
        self.add_and_remove_a_document(repository)
        self.install_fake_gh(repository)
        result = self.run_wrapper(MERGE_WRAPPER, repository)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn(SKIP_LINE, result.stdout)
        self.assertFalse(self.companion_log.exists(), "codex companion が起動された")
        _, pending = self.records(repository)
        self.assertFalse(pending.exists())


if __name__ == "__main__":
    unittest.main()
