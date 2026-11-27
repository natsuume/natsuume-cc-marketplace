"""テキスト系文書のみの変更でレビュー gate を通す契約テスト。

対象は次のとおり:

- 判定 lib `hooks/scripts/lib/text-paths.sh` (canonical は pre-push-review。
  pre-push-codex-review と pre-merge-cross-review が byte-identical なコピーを持つ) の
  3 関数
  - `is_text_document_path <path>`: repo 相対 path がテキスト系文書かを分類する
  - `is_text_only_range_in <target_cwd> <base_rev> <head_rev>`: 範囲内の変更が
    テキスト系文書だけかを判定する
  - `is_text_only_push_in <target_cwd> <base>`: index / working tree が clean で、
    `origin/<base>..HEAD` がテキスト系文書だけかを判定する
- push gate 2 つ (`block-pre-push.sh` / `block-pre-push-codex.sh`) と merge gate
  (`block-pre-merge.sh`) が、テキスト系文書のみの変更ではレビュー記録を要求しないこと
- codex review wrapper 2 つが、テキスト系文書のみの変更では codex review を実行せず、
  stdout に `Review skipped: text-only changes` の 1 行だけを出して exit 0 で終わり、
  pending attestation を書かないこと
- codex-reviewer の report 契約が `Status: skipped` を定義し、auto-mark と review
  cadence が `Status: skipped` の report を記録・計数しないこと

判定できない場合はレビュー必須に倒す (fail-closed) 契約のため、テキスト系文書のみと
判定してはならない変更 (コードの混在・merge commit・中間 commit のコード・symlink・
実行ビット・挙動を定義する Markdown 等) も固定する。

git repository はすべて tempfile の一時ディレクトリに作り、実行環境の git 設定
(system / global) を読まないよう隔離する。fixture を作った直後に差分の path と mode を
Python 側で確認してから判定や gate を実行し、fixture の取り違えで期待どおりの結果に
見えることを防ぐ。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
CORE_PLUGIN = PLUGINS / "pre-push-review"
CODEX_PLUGIN = PLUGINS / "pre-push-codex-review"
MERGE_PLUGIN = PLUGINS / "pre-merge-cross-review"
CORE_SCRIPTS = CORE_PLUGIN / "hooks" / "scripts"
CODEX_SCRIPTS = CODEX_PLUGIN / "hooks" / "scripts"
MERGE_SCRIPTS = MERGE_PLUGIN / "hooks" / "scripts"

TEXT_PATHS_LIB = CORE_SCRIPTS / "lib" / "text-paths.sh"
DIFF_HASH_LIB = CORE_SCRIPTS / "lib" / "diff-hash.sh"
CORE_PUSH_GATE = CORE_SCRIPTS / "block-pre-push.sh"
CODEX_PUSH_GATE = CODEX_SCRIPTS / "block-pre-push-codex.sh"
MERGE_GATE = MERGE_SCRIPTS / "block-pre-merge.sh"
PUSH_WRAPPER = CODEX_SCRIPTS / "run-pre-push-codex-review.sh"
MERGE_WRAPPER = MERGE_SCRIPTS / "run-pre-merge-codex-review.sh"
PUSH_AUTO_MARK = CODEX_SCRIPTS / "auto-mark.sh"
MERGE_AUTO_MARK = MERGE_SCRIPTS / "auto-mark.sh"
CADENCE_HOOK = CODEX_SCRIPTS / "manage-review-cadence.mjs"
PUSH_CODEX_AGENT = CODEX_PLUGIN / "agents" / "codex-reviewer.md"
MERGE_CODEX_AGENT = MERGE_PLUGIN / "agents" / "codex-reviewer.md"
MERGE_ORDER_RULES = MERGE_PLUGIN / "hooks" / "prompts" / "merge-order-rules.md"
PLUGIN_READMES = {
    "pre-push-review": CORE_PLUGIN / "README.md",
    "pre-push-codex-review": CODEX_PLUGIN / "README.md",
    "pre-merge-cross-review": MERGE_PLUGIN / "README.md",
}
REVIEW_COMMANDS = {
    "pre-push-review": CORE_PLUGIN / "commands" / "review.md",
    "pre-push-codex-review": CODEX_PLUGIN / "commands" / "review.md",
}

PUSH_CODEX_REVIEWER = "pre-push-codex-review:codex-reviewer"
MERGE_CODEX_REVIEWER = "pre-merge-cross-review:codex-reviewer"

# wrapper がテキスト系文書のみの変更で stdout に出す唯一の行と、そのとき
# codex-reviewer が返す report。
SKIP_LINE = "Review skipped: text-only changes"
SKIPPED_REPORT = "# Codex Review\n\nStatus: skipped\nReason: text-only changes"
PASS_REPORT = "# Codex Review\n\nStatus: pass\nFindings: 0"
HANDBACK_CLOSING_MESSAGE = (
    "Codex review complete. Report delivered to the parent session."
)

# push gate が「マーカーが未実行・失効」で deny したときの理由文に含まれる語。
# deny がマーカー不足によるもの (base 未解決や dirty tree ではない) ことの確認に使う。
MARKER_STATUS_PHRASE = "レビュー状態"

PUSH_CODEX_MARKER = ".claude-pre-push-codex-reviewed"
PUSH_CODEX_PENDING = ".claude-pre-push-codex-reviewed.pending"
PUSH_HANDBACK_PREFIX = ".claude-pre-push-handback-"
MERGE_FINAL_RECORD = ".claude-pre-merge-codex-reviewed"
MERGE_PENDING_RECORD = ".claude-pre-merge-codex-reviewed.pending"
MERGE_HANDBACK_PREFIX = ".claude-pre-merge-handback-"

PR_NUMBER = 123
# ローカルに object が無い head SHA / base commit SHA (merge gate の判定が成立しない状況の
# 再現に使う)。
ABSENT_HEAD_SHA = "fedcba0987654321fedcba0987654321fedcba09"
ABSENT_BASE_SHA = "0123456789abcdef0123456789abcdef01234567"

BASH = shutil.which("bash")
GIT = shutil.which("git")
JQ = shutil.which("jq")
NODE = shutil.which("node")

# fixture と hook の git 解決を実行環境から切り離すため、継承しない環境変数。
# XDG_CONFIG_HOME は git が global 設定を探す場所になるため外す (HOME は一時
# ディレクトリへ差し替える)。
INHERITED_ENV_TO_DROP = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "XDG_CONFIG_HOME",
)

# 分類関数が外部コマンドに依存しないことを確かめるため、lib を source した後に
# PATH を存在しないディレクトリへ向けてから分類する。
CLASSIFY_SCRIPT = (
    'source "$1" || exit 99; shift; '
    "PATH=/nonexistent-directory-for-text-paths-test; "
    'for candidate in "$@"; do '
    'is_text_document_path "$candidate"; printf "%s\\n" "$?"; '
    "done"
)
CALL_FUNCTION_SCRIPT = 'source "$1" || exit 99; shift; "$@"'

# テキスト系文書に分類される path (拡張子は md / markdown / rst / adoc で、大文字小文字を
# 区別しない。basename はディレクトリ要素の除外条件の対象外。除外条件は要素単位で
# 照合するため、除外名を一部に含むだけの要素や、片方だけの要素の組は除外しない)。
TEXT_DOCUMENT_PATHS = (
    "README.md",
    "docs/a.MD",
    "plugins/x/README.md",
    "docs/skills.md",
    "docs/commands.rst",
    "a.markdown",
    "a.MARKDOWN",
    "a.rst",
    "a.Rst",
    "a.adoc",
    "docs/Guide.Markdown",
    "x.sh.md",
    "agentsx/r.md",
    "my-skills/r.md",
    "output-styles.md",
    "hooks/x.md",
    "prompts/x.md",
    "prompts/hooks/x.md",
    "a/hooks/b/prompts/x.md",
    ".github/README.md",
    ".github/workflows/notes.md",
    "github/prompts/x.md",
    "docs/instructions/x.md",
    "docs/chatmodes/x.md",
    "roo/x.md",
    "kiro/notes.md",
    "docs/claude.md.rst",
    "docs/a b.md",
    "docs/日本語.md",
    "docs/tab\there.md",
    "docs/new\nline.md",
)

# テキスト系文書に分類されない path (対象外の拡張子・拡張子なし・コード、および coding
# agent が指示として読み込む Markdown。除外条件は大文字小文字を区別しない)。
NON_TEXT_PATHS = (
    # 対象外の拡張子 (txt / mdx を含む)・拡張子なし
    "notes.txt",
    "A.TXT",
    "requirements.txt",
    "CMakeLists.txt",
    "docs/CLAUDE.md.txt",
    "a.mdx",
    "docs/page.MDX",
    "x.sh",
    "Makefile",
    "README",
    "x.md.sh",
    "README.",
    "dir.md/file",
    "docs/notes.json",
    "image.png",
    "",
    # ディレクトリ要素
    "plugins/x/agents/r.md",
    "plugins/x/skills/s/SKILL.md",
    "a/rules/r.md",
    "agents/r.md",
    "commands/c.md",
    "skills/s/SKILL.md",
    "a/b/skills/c/d/e.md",
    "output-styles/concise.md",
    ".claude/notes.md",
    ".agents/x.md",
    ".cursor/x.md",
    ".gemini/x.md",
    ".codex/x.md",
    ".windsurf/x.md",
    ".clinerules/x.md",
    ".roo/rules-code/x.md",
    ".kiro/steering/x.md",
    ".continue/x.md",
    ".junie/guidelines.md",
    ".amazonq/x.md",
    "sub/.roo/x.md",
    # 連続する 2 つのディレクトリ要素
    "plugins/x/hooks/prompts/p.md",
    ".github/instructions/x.md",
    ".github/prompts/x.md",
    ".github/chatmodes/x.chatmode.md",
    "sub/.github/prompts/x.md",
    # basename
    ".claude/CLAUDE.md",
    "sub/CLAUDE.md",
    "AGENTS.md",
    "AGENTS.override.md",
    "sub/AGENTS.override.md",
    "CLAUDE.md",
    "CLAUDE.local.md",
    "GEMINI.md",
    "docs/AGENTS.md",
    ".github/copilot-instructions.md",
    "docs/copilot-instructions.md",
    # 大文字小文字違い
    "docs/Agents/x.md",
    "Skills/s/SKILL.md",
    "Output-Styles/x.md",
    ".Claude/x.md",
    ".CURSOR/x.md",
    "HOOKS/Prompts/x.md",
    ".GitHub/Instructions/x.md",
    ".Github/PROMPTS/x.md",
    "docs/claude.md",
    "docs/agents.md",
    "Agents.md",
    "gemini.MD",
    "Claude.Local.md",
    "Copilot-Instructions.MD",
    "agents.Override.md",
    ".ClineRules/x.md",
    ".ROO/x.md",
    ".Kiro/x.md",
    ".CONTINUE/x.md",
    ".Junie/x.md",
    ".AmazonQ/x.md",
    ".GitHub/ChatModes/x.md",
)

# bash 3.2 (macOS) に無い構文。lib に含まれないことを静的に確かめる。
BASH4_ONLY_CONSTRUCTS = (
    ",,}",
    "^^}",
    "declare -A",
    "local -A",
    "typeset -A",
    "mapfile",
    "readarray",
)

FAKE_GH_TEMPLATE = """#!{python}
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "gh-config.json"), encoding="utf-8") as fp:
    config = json.load(fp)

args = sys.argv[1:]
with open(os.path.join(HERE, "gh-calls.jsonl"), "a", encoding="utf-8") as fp:
    fp.write(json.dumps(args) + "\\n")

if args[:2] == ["pr", "view"]:
    print(json.dumps(config["pr"]))
sys.exit(0)
"""

# fake codex companion は起動されたこと (tripwire) を記録してから review 本文を出す。
COMPANION_TRIPWIRE_ENV = "TEXT_ONLY_SKIP_TEST_COMPANION_TRIPWIRE"
FAKE_COMPANION_SCRIPT = (
    'import { appendFileSync } from "node:fs";\n'
    f"appendFileSync(process.env.{COMPANION_TRIPWIRE_ENV}, "
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


class Repository:
    """一時ディレクトリ上の git repository を操作する fixture helper。"""

    def __init__(self, path: Path, env: dict[str, str]) -> None:
        self.path = path
        self.env = env

    def run_git(self, *args: str) -> bytes:
        assert GIT is not None
        result = subprocess.run(
            [GIT, *args],
            cwd=self.path,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"git {' '.join(args)} が失敗しました: "
                f"{result.stderr.decode('utf-8', errors='replace')}"
            )
        return result.stdout

    def git(self, *args: str) -> str:
        return self.run_git(*args).decode("utf-8", errors="surrogateescape")

    def write(self, relative: str, content: str = "content\n") -> Path:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def remove(self, relative: str) -> None:
        (self.path / relative).unlink()

    def commit_all(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.head()

    def commit_index(self, message: str) -> str:
        """index の内容だけを commit する (working tree の mode を index へ戻さない)。"""
        self.git("commit", "-q", "-m", message)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def rev(self, revision: str) -> str:
        return self.git("rev-parse", revision).strip()

    def git_dir(self) -> Path:
        return Path(self.git("rev-parse", "--absolute-git-dir").strip())

    def merge_base(self, base: str, head: str = "HEAD") -> str:
        return self.git("merge-base", base, head).strip()

    def raw_entries(self, *revisions: str) -> list[tuple[str, str, str]]:
        """raw 差分の各エントリを (変更前 mode, 変更後 mode, path) で返す。

        rename 検出なし・NUL 区切りで読む (判定 lib に課す読み方と同じ)。
        """
        output = self.run_git(
            "diff-tree", "-r", "-z", "--no-renames", "--no-commit-id", *revisions
        )
        fields = output.split(b"\0")
        entries: list[tuple[str, str, str]] = []
        index = 0
        while index + 1 < len(fields) and fields[index]:
            metadata = fields[index].decode("ascii").lstrip(":").split()
            path = fields[index + 1].decode("utf-8", errors="surrogateescape")
            entries.append((metadata[0], metadata[1], path))
            index += 2
        return entries

    def changed_paths(self, *revisions: str) -> list[str]:
        return sorted(path for _, _, path in self.raw_entries(*revisions))

    def range_commits(self, base: str, head: str = "HEAD") -> list[str]:
        return self.git("rev-list", f"{base}..{head}").split()

    def merge_commit_count(self, base: str, head: str = "HEAD") -> int:
        return int(
            self.git("rev-list", "--min-parents=2", "--count", f"{base}..{head}")
        )


@unittest.skipUnless(BASH and GIT, "requires bash and git")
class WorkspaceTestCase(unittest.TestCase):
    """隔離した一時ディレクトリと git 環境を用意する共通 base。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # macOS の一時ディレクトリは symlink なので、git が出力する path と比較できる
        # よう解決済みの path を使う。
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in INHERITED_ENV_TO_DROP
        }
        env["HOME"] = str(self.home)
        env["TMPDIR"] = str(self.tmp)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CEILING_DIRECTORIES"] = str(self.root)
        env["GIT_AUTHOR_NAME"] = "Text Only Skip Test"
        env["GIT_AUTHOR_EMAIL"] = "text-only-skip@example.invalid"
        env["GIT_COMMITTER_NAME"] = "Text Only Skip Test"
        env["GIT_COMMITTER_EMAIL"] = "text-only-skip@example.invalid"
        self.env = env

    # ------------------------------------------------------------------
    # fixture
    # ------------------------------------------------------------------

    def create_repository(self, name: str = "repo") -> Repository:
        """master に base commit (テキスト 2 つとコード 1 つ) を持ち、feature branch に
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

    def attach_origin(self, repository: Repository) -> None:
        """bare な origin を作って master を push し、origin/HEAD を master に向ける。"""
        origin = self.root / f"{repository.path.name}-origin.git"
        bare = Repository(self.root, self.env)
        bare.git("-c", "init.defaultBranch=master", "init", "-q", "--bare", str(origin))
        repository.git("remote", "add", "origin", str(origin))
        repository.git("push", "-q", "origin", "master")
        repository.git("remote", "set-head", "origin", "master")

    def assert_all_regular_text_changes(
        self, repository: Repository, paths: list[str], *revisions: str
    ) -> None:
        """fixture の差分が期待した path だけで、mode がすべて通常ファイルか不在であること。"""
        entries = repository.raw_entries(*revisions)
        self.assertEqual(sorted(path for _, _, path in entries), sorted(paths))
        for old_mode, new_mode, path in entries:
            with self.subTest(path=path):
                self.assertIn(old_mode, ("000000", "100644"))
                self.assertIn(new_mode, ("000000", "100644"))

    # ------------------------------------------------------------------
    # 判定 lib の呼び出し
    # ------------------------------------------------------------------

    def call_text_paths(
        self, function: str, *arguments: str, cwd: Path | None = None
    ) -> int:
        assert BASH is not None
        result = subprocess.run(
            [
                BASH,
                "-c",
                CALL_FUNCTION_SCRIPT,
                "text-paths-test",
                str(TEXT_PATHS_LIB),
                function,
                *arguments,
            ],
            cwd=cwd or self.root,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
        return result.returncode

    def range_status(
        self,
        repository: Repository,
        base: str = "master",
        head: str = "HEAD",
    ) -> int:
        return self.call_text_paths(
            "is_text_only_range_in", str(repository.path), base, head
        )

    def push_status(self, repository: Repository, base: str = "master") -> int:
        return self.call_text_paths("is_text_only_push_in", str(repository.path), base)


class TextDocumentPathClassificationTest(WorkspaceTestCase):
    """is_text_document_path の分類 (外部コマンドを使わない pure bash)。"""

    def classify(self, paths: tuple[str, ...]) -> dict[str, int]:
        assert BASH is not None
        result = subprocess.run(
            [
                BASH,
                "-c",
                CLASSIFY_SCRIPT,
                "text-paths-test",
                str(TEXT_PATHS_LIB),
                *paths,
            ],
            cwd=self.root,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        statuses = result.stdout.decode().splitlines()
        self.assertEqual(len(statuses), len(paths), result.stdout.decode())
        return {path: int(status) for path, status in zip(paths, statuses)}

    def test_text_document_paths_are_classified_as_text(self) -> None:
        statuses = self.classify(TEXT_DOCUMENT_PATHS)
        for path in TEXT_DOCUMENT_PATHS:
            with self.subTest(path=path):
                self.assertEqual(statuses[path], 0)

    def test_non_text_paths_are_classified_as_non_text(self) -> None:
        statuses = self.classify(NON_TEXT_PATHS)
        for path in NON_TEXT_PATHS:
            with self.subTest(path=path):
                self.assertEqual(statuses[path], 1)

    def test_lib_avoids_constructs_missing_from_bash_3_2(self) -> None:
        # コメント行は禁止構文の名前を説明に含むため対象外にする。
        code = "\n".join(
            line
            for line in TEXT_PATHS_LIB.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        for construct in BASH4_ONLY_CONSTRUCTS:
            with self.subTest(construct=construct):
                self.assertNotIn(construct, code)


class TextOnlyRangeDetectsTextOnlyChangesTest(WorkspaceTestCase):
    """is_text_only_range_in がテキスト系文書のみの範囲で 0 を返す。"""

    def test_text_only_commits_are_text_only(self) -> None:
        repository = self.create_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("update readme")
        repository.write("docs/new.md", "new\n")
        repository.write("docs/notes.rst", "notes\n")
        repository.write("docs/manual.adoc", "manual\n")
        repository.commit_all("add docs")
        self.assert_all_regular_text_changes(
            repository,
            ["README.md", "docs/manual.adoc", "docs/new.md", "docs/notes.rst"],
            repository.merge_base("master"),
            "HEAD",
        )
        self.assertEqual(self.range_status(repository), 0)

    def test_text_paths_with_special_characters_are_text_only(self) -> None:
        repository = self.create_repository()
        paths = [
            "docs/a b.md",
            "docs/日本語.md",
            "docs/tab\there.md",
            "docs/new\nline.md",
            'docs/"quoted".md',
            "docs/back\\slash.md",
            "docs/-dash.md",
        ]
        for path in paths:
            repository.write(path, "special\n")
        repository.commit_all("add documents with special characters")
        self.assert_all_regular_text_changes(
            repository, paths, repository.merge_base("master"), "HEAD"
        )
        self.assertEqual(self.range_status(repository), 0)

    def test_deleting_and_renaming_text_files_is_text_only(self) -> None:
        repository = self.create_repository()
        repository.remove("README.md")
        repository.commit_all("delete readme")
        repository.git("mv", "docs/guide.md", "docs/manual.md")
        repository.commit_index("rename guide")
        self.assert_all_regular_text_changes(
            repository,
            ["README.md", "docs/guide.md", "docs/manual.md"],
            repository.merge_base("master"),
            "HEAD",
        )
        self.assertEqual(self.range_status(repository), 0)

    def test_base_advanced_with_code_commits_is_still_text_only(self) -> None:
        """base 側にコードの commit が進んでも、範囲 (merge-base 以降) が
        テキスト系文書だけなら成立する。"""
        repository = self.create_repository()
        repository.write("README.md", "# feature\n")
        repository.commit_all("feature docs")
        repository.git("switch", "-q", "master")
        repository.write("tool.py", "print('advanced')\n")
        repository.commit_all("advance base with code")
        repository.git("switch", "-q", "feature")
        self.assertEqual(
            repository.changed_paths("master", "HEAD"), ["README.md", "tool.py"]
        )
        self.assert_all_regular_text_changes(
            repository, ["README.md"], repository.merge_base("master"), "HEAD"
        )
        self.assertEqual(self.range_status(repository), 0)

    def test_empty_target_cwd_uses_current_directory(self) -> None:
        repository = self.create_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("update readme")
        self.assertEqual(
            self.call_text_paths(
                "is_text_only_range_in", "", "master", "HEAD", cwd=repository.path
            ),
            0,
        )


class TextOnlyRangeRejectsOtherChangesTest(WorkspaceTestCase):
    """is_text_only_range_in がテキスト系文書のみと言えない範囲で 1 を返す。"""

    def test_code_mixed_with_text_is_not_text_only(self) -> None:
        repository = self.create_repository()
        repository.write("README.md", "# updated\n")
        repository.write("tool.py", "print('changed')\n")
        repository.commit_all("mixed change")
        self.assertEqual(
            repository.changed_paths(repository.merge_base("master"), "HEAD"),
            ["README.md", "tool.py"],
        )
        self.assertEqual(self.range_status(repository), 1)

    def test_txt_and_mdx_files_are_not_text_only(self) -> None:
        """依存宣言・ビルド定義に使われる txt と、コードを埋め込める mdx は対象外。"""
        for index, path in enumerate(
            ("requirements.txt", "CMakeLists.txt", "docs/notes.txt", "docs/page.mdx")
        ):
            with self.subTest(path=path):
                repository = self.create_repository(f"repo-{index}")
                repository.write("README.md", "# updated\n")
                repository.write(path, "content\n")
                repository.commit_all("docs and txt or mdx")
                self.assert_all_regular_text_changes(
                    repository,
                    ["README.md", path],
                    repository.merge_base("master"),
                    "HEAD",
                )
                self.assertEqual(self.range_status(repository), 1)

    def test_code_commit_next_to_text_commit_is_not_text_only(self) -> None:
        repository = self.create_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        repository.write("src/main.sh", "echo hi\n")
        repository.commit_all("code")
        self.assertEqual(self.range_status(repository), 1)

    def test_merge_commit_in_range_is_not_text_only(self) -> None:
        repository = self.create_repository()
        repository.write("README.md", "# feature\n")
        repository.commit_all("feature docs")
        repository.git("switch", "-q", "-c", "side", "master")
        repository.write("docs/side.md", "side\n")
        repository.commit_all("side docs")
        repository.git("switch", "-q", "feature")
        repository.git("merge", "-q", "--no-ff", "-m", "merge side", "side")
        self.assertEqual(repository.merge_commit_count("master"), 1)
        self.assert_all_regular_text_changes(
            repository,
            ["README.md", "docs/side.md"],
            repository.merge_base("master"),
            "HEAD",
        )
        self.assertEqual(self.range_status(repository), 1)

    def test_code_added_then_reverted_in_intermediate_commits_is_not_text_only(
        self,
    ) -> None:
        """net diff がテキスト系文書だけでも、中間 commit のコードは履歴に残る。"""
        repository = self.create_repository()
        repository.write("helper.py", "print('temporary')\n")
        repository.commit_all("add helper")
        repository.remove("helper.py")
        repository.commit_all("remove helper")
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        self.assert_all_regular_text_changes(
            repository, ["README.md"], repository.merge_base("master"), "HEAD"
        )
        self.assertEqual(self.range_status(repository), 1)

    def test_symlinks_are_not_text_only(self) -> None:
        # (symlink を置く path, symlink の参照先)。2 つ目は既存の通常ファイルを
        # symlink に置き換える (mode 100644 → 120000)。
        cases = {
            "new_symlink": ("docs/link.md", "guide.md"),
            "file_replaced_by_symlink": ("README.md", "docs/guide.md"),
        }
        for label, (link_path, link_target) in cases.items():
            with self.subTest(case=label):
                repository = self.create_repository(f"repo-{label}")
                link = repository.path / link_path
                if link.exists():
                    link.unlink()
                os.symlink(link_target, link)
                repository.commit_all("symlink")
                modes = {
                    path: new_mode
                    for _, new_mode, path in repository.raw_entries(
                        repository.merge_base("master"), "HEAD"
                    )
                }
                self.assertEqual(modes, {link_path: "120000"})
                self.assertEqual(self.range_status(repository), 1)

    def test_executable_bit_is_not_text_only(self) -> None:
        cases = {
            "new_executable_text_file": "docs/run.md",
            "mode_change_only": "README.md",
        }
        for label, path in cases.items():
            with self.subTest(case=label):
                repository = self.create_repository(f"repo-{label}")
                target = repository.path / path
                if not target.exists():
                    repository.write(path, "run\n")
                    repository.git("add", path)
                target.chmod(target.stat().st_mode | stat.S_IXUSR)
                repository.git("update-index", "--chmod=+x", path)
                repository.commit_index("executable")
                modes = {
                    entry_path: new_mode
                    for _, new_mode, entry_path in repository.raw_entries(
                        repository.merge_base("master"), "HEAD"
                    )
                }
                self.assertEqual(modes, {path: "100755"})
                self.assertEqual(self.range_status(repository), 1)

    def test_submodule_gitlink_is_not_text_only(self) -> None:
        repository = self.create_repository()
        repository.git(
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{repository.rev('master')},docs/vendored.md",
        )
        repository.commit_index("add gitlink")
        modes = {
            path: new_mode
            for _, new_mode, path in repository.raw_entries(
                repository.merge_base("master"), "HEAD"
            )
        }
        self.assertEqual(modes, {"docs/vendored.md": "160000"})
        self.assertEqual(self.range_status(repository), 1)

    def test_behavior_defining_markdown_is_not_text_only(self) -> None:
        for index, path in enumerate(
            (
                "plugins/x/agents/r.md",
                "plugins/x/skills/s/SKILL.md",
                "plugins/x/hooks/prompts/p.md",
                ".claude/CLAUDE.md",
                "sub/AGENTS.md",
                ".github/copilot-instructions.md",
                ".github/instructions/review.md",
                "output-styles/concise.md",
                "Plugins/X/Agents/r.md",
                "docs/claude.md",
            )
        ):
            with self.subTest(path=path):
                repository = self.create_repository(f"repo-{index}")
                repository.write(path, "behavior\n")
                repository.commit_all("behavior-defining markdown")
                self.assert_all_regular_text_changes(
                    repository, [path], repository.merge_base("master"), "HEAD"
                )
                self.assertEqual(self.range_status(repository), 1)

    def test_code_paths_with_special_characters_are_not_text_only(self) -> None:
        for index, path in enumerate(
            ("scripts/a b.sh", "src/日本語.py", "bin/new\nline.sh")
        ):
            with self.subTest(path=path):
                repository = self.create_repository(f"repo-{index}")
                repository.write("README.md", "# updated\n")
                repository.write(path, "code\n")
                repository.commit_all("docs and code")
                self.assertEqual(
                    repository.changed_paths(repository.merge_base("master"), "HEAD"),
                    sorted(["README.md", path]),
                )
                self.assertEqual(self.range_status(repository), 1)

    def test_renaming_code_file_to_markdown_is_not_text_only(self) -> None:
        """rename を検出しないため、変更前の path (コード) も検査対象になる。"""
        repository = self.create_repository()
        repository.git("mv", "tool.py", "tool.md")
        repository.commit_index("rename code to markdown")
        self.assertEqual(
            repository.changed_paths(repository.merge_base("master"), "HEAD"),
            ["tool.md", "tool.py"],
        )
        self.assertEqual(self.range_status(repository), 1)

    def test_deleting_code_file_is_not_text_only(self) -> None:
        repository = self.create_repository()
        repository.remove("tool.py")
        repository.commit_all("delete code")
        self.assertEqual(
            repository.raw_entries(repository.merge_base("master"), "HEAD"),
            [("100644", "000000", "tool.py")],
        )
        self.assertEqual(self.range_status(repository), 1)

    def test_empty_range_is_not_text_only(self) -> None:
        repository = self.create_repository()
        self.assertEqual(repository.range_commits("master"), [])
        self.assertEqual(self.range_status(repository), 1)

    def test_parentless_commit_in_range_is_not_text_only(self) -> None:
        repository = self.create_repository()
        # `git switch --orphan` は追跡中のファイルを working tree と index から取り除く。
        repository.git("switch", "-q", "--orphan", "orphan")
        repository.write("README.md", "# orphan\n")
        repository.commit_all("orphan docs")
        self.assertEqual(len(repository.range_commits("master")), 1)
        self.assertEqual(
            repository.git(
                "rev-list", "--max-parents=0", "--count", "master..HEAD"
            ).strip(),
            "1",
        )
        self.assertEqual(self.range_status(repository), 1)

    def test_git_failures_are_not_text_only(self) -> None:
        repository = self.create_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        plain = self.root / "plain"
        plain.mkdir()
        cases = {
            "unknown_base": (
                "is_text_only_range_in",
                str(repository.path),
                "no-such-base",
                "HEAD",
            ),
            "unknown_head": (
                "is_text_only_range_in",
                str(repository.path),
                "master",
                ABSENT_HEAD_SHA,
            ),
            "not_a_repository": ("is_text_only_range_in", str(plain), "master", "HEAD"),
            "missing_directory": (
                "is_text_only_range_in",
                str(self.root / "missing"),
                "master",
                "HEAD",
            ),
        }
        for label, call in cases.items():
            with self.subTest(case=label):
                self.assertEqual(self.call_text_paths(*call), 1)


class TextOnlyPushPredictionTest(WorkspaceTestCase):
    """is_text_only_push_in: index / working tree の clean と origin/<base>..HEAD の判定。"""

    def create_text_only_branch(self) -> Repository:
        repository = self.create_repository()
        repository.git(
            "update-ref", "refs/remotes/origin/master", repository.rev("master")
        )
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        self.assertEqual(repository.git("status", "--porcelain"), "")
        self.assert_all_regular_text_changes(
            repository, ["README.md"], repository.merge_base("origin/master"), "HEAD"
        )
        return repository

    def test_clean_text_only_branch_is_predicted_text_only(self) -> None:
        repository = self.create_text_only_branch()
        self.assertEqual(self.push_status(repository), 0)

    def test_staged_change_is_not_predicted_text_only(self) -> None:
        repository = self.create_text_only_branch()
        repository.write("docs/guide.md", "staged\n")
        repository.git("add", "docs/guide.md")
        self.assertEqual(self.push_status(repository), 1)

    def test_unstaged_change_is_not_predicted_text_only(self) -> None:
        repository = self.create_text_only_branch()
        repository.write("docs/guide.md", "unstaged\n")
        self.assertEqual(self.push_status(repository), 1)

    def test_code_in_range_is_not_predicted_text_only(self) -> None:
        repository = self.create_text_only_branch()
        repository.write("tool.py", "print('changed')\n")
        repository.commit_all("code")
        self.assertEqual(self.push_status(repository), 1)

    def test_missing_remote_base_is_not_predicted_text_only(self) -> None:
        repository = self.create_text_only_branch()
        self.assertEqual(self.push_status(repository, base="no-such-base"), 1)


@unittest.skipUnless(JQ, "push gate integration requires jq")
class PushGateTextOnlySkipTest(WorkspaceTestCase):
    """2 つの push gate がテキスト系文書のみの push をマーカー無しで通す。"""

    GATES = {
        "pre-push-review": CORE_PUSH_GATE,
        "pre-push-codex-review": CODEX_PUSH_GATE,
    }

    def create_pushable_repository(self) -> Repository:
        repository = self.create_repository()
        self.attach_origin(repository)
        return repository

    def run_gate(
        self, gate: Path, repository: Repository
    ) -> subprocess.CompletedProcess[bytes]:
        assert BASH is not None
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin HEAD"},
            "cwd": str(repository.path),
        }
        result = subprocess.run(
            [BASH, str(gate)],
            cwd=repository.path,
            input=json.dumps(payload).encode("utf-8"),
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result

    def assert_marker_deny(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertTrue(result.stdout.strip(), "gate が deny を返していない")
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(MARKER_STATUS_PHRASE, output["permissionDecisionReason"])

    def test_text_only_push_passes_without_markers(self) -> None:
        repository = self.create_pushable_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        repository.write("docs/new.md", "new\n")
        repository.commit_all("more docs")
        self.assert_all_regular_text_changes(
            repository,
            ["README.md", "docs/new.md"],
            repository.merge_base("origin/master"),
            "HEAD",
        )
        for name, gate in self.GATES.items():
            with self.subTest(gate=name):
                result = self.run_gate(gate, repository)
                self.assertEqual(result.stdout, b"", result.stdout.decode())

    def test_mixed_push_still_requires_markers(self) -> None:
        repository = self.create_pushable_repository()
        repository.write("README.md", "# updated\n")
        repository.write("tool.py", "print('changed')\n")
        repository.commit_all("mixed")
        for name, gate in self.GATES.items():
            with self.subTest(gate=name):
                self.assert_marker_deny(self.run_gate(gate, repository))

    def test_behavior_defining_markdown_push_still_requires_markers(self) -> None:
        repository = self.create_pushable_repository()
        repository.write("plugins/x/agents/reviewer.md", "agent\n")
        repository.commit_all("agent definition")
        for name, gate in self.GATES.items():
            with self.subTest(gate=name):
                self.assert_marker_deny(self.run_gate(gate, repository))

    def test_reverted_code_commit_push_still_requires_markers(self) -> None:
        repository = self.create_pushable_repository()
        repository.write("helper.py", "print('temporary')\n")
        repository.commit_all("add helper")
        repository.remove("helper.py")
        repository.commit_all("remove helper")
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        for name, gate in self.GATES.items():
            with self.subTest(gate=name):
                self.assert_marker_deny(self.run_gate(gate, repository))


class FakeGhMixin:
    """PATH の先頭に置く fake gh。`pr view` に設定済みの PR 情報を返し、全呼び出しを記録する。"""

    root: Path
    env: dict[str, str]

    def install_fake_gh(self) -> Path:
        bin_dir = self.root / "fake-bin"
        bin_dir.mkdir()
        gh_path = bin_dir / "gh"
        gh_path.write_text(
            FAKE_GH_TEMPLATE.format(python=sys.executable), encoding="utf-8"
        )
        gh_path.chmod(
            gh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        self.env["PATH"] = f"{bin_dir}{os.pathsep}{self.env.get('PATH', '')}"
        self.fake_bin_dir = bin_dir
        return bin_dir

    def configure_fake_gh(self, pr: dict[str, object]) -> None:
        (self.fake_bin_dir / "gh-config.json").write_text(
            json.dumps({"pr": pr}, ensure_ascii=False), encoding="utf-8"
        )


@unittest.skipUnless(JQ, "merge gate integration requires jq")
class MergeGateTextOnlySkipTest(FakeGhMixin, WorkspaceTestCase):
    """merge gate がテキスト系文書のみの PR を記録無しで通し、判定できなければ記録を照合する。"""

    def setUp(self) -> None:
        super().setUp()
        self.install_fake_gh()

    def create_pr_repository(self, name: str = "repo") -> Repository:
        repository = self.create_repository(name)
        repository.git(
            "update-ref", "refs/remotes/origin/master", repository.rev("master")
        )
        return repository

    def configure_pr(self, head: str, base_oid: str | None) -> None:
        pr: dict[str, object] = {"number": PR_NUMBER, "headRefOid": head}
        if base_oid is not None:
            pr["baseRefOid"] = base_oid
        self.configure_fake_gh(pr)

    def write_record(self, repository: Repository, head: str) -> None:
        (repository.git_dir() / MERGE_FINAL_RECORD).write_text(
            f"pr={PR_NUMBER}\nhead={head}\n", encoding="utf-8"
        )

    def run_gate(self, repository: Repository) -> subprocess.CompletedProcess[bytes]:
        assert BASH is not None
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"gh pr merge {PR_NUMBER} --squash"},
            "cwd": str(repository.path),
        }
        result = subprocess.run(
            [BASH, str(MERGE_GATE)],
            cwd=repository.path,
            input=json.dumps(payload).encode("utf-8"),
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result

    def assert_record_deny(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.assertTrue(result.stdout.strip(), "gate が deny を返していない")
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(MERGE_CODEX_REVIEWER, output["permissionDecisionReason"])

    def test_text_only_pr_passes_without_record(self) -> None:
        repository = self.create_pr_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        repository.write("docs/new.md", "new\n")
        repository.commit_all("more docs")
        self.assert_all_regular_text_changes(
            repository,
            ["README.md", "docs/new.md"],
            repository.merge_base("refs/remotes/origin/master"),
            "HEAD",
        )
        self.configure_pr(repository.head(), repository.rev("master"))
        self.assertFalse((repository.git_dir() / MERGE_FINAL_RECORD).exists())
        result = self.run_gate(repository)
        self.assertEqual(result.stdout, b"", result.stdout.decode())

    def test_mixed_pr_still_requires_record(self) -> None:
        repository = self.create_pr_repository()
        repository.write("README.md", "# updated\n")
        repository.write("tool.py", "print('changed')\n")
        repository.commit_all("mixed")
        self.configure_pr(repository.head(), repository.rev("master"))
        self.assert_record_deny(self.run_gate(repository))

    def test_unusable_base_commit_falls_back_to_record(self) -> None:
        """base commit SHA が欠落・形式不正・ローカルに無い場合は判定せず、記録の有無で
        決まる。

        形式不正の値には、git が解決できてしまう値 (大文字の SHA・短縮 SHA・branch 名) を
        使い、形式を検証せずに判定すると通ってしまう状態にしておく。
        """
        cases: dict[str, Callable[[str], str] | None] = {
            "missing": None,
            "uppercase": lambda base: base.upper(),
            "abbreviated": lambda base: base[:12],
            "branch_name": lambda base: "master",
            "absent_object": lambda base: ABSENT_BASE_SHA,
        }
        # 判定関数に渡せば成立してしまう (gate の形式検証だけが止める) 値。
        resolvable_cases = ("uppercase", "abbreviated", "branch_name")
        for label, make_base in cases.items():
            with self.subTest(case=label):
                repository = self.create_pr_repository(f"repo-{label}")
                base = repository.rev("master")
                repository.write("README.md", "# updated\n")
                repository.commit_all("docs")
                head = repository.head()
                base_oid = None if make_base is None else make_base(base)
                if label in resolvable_cases:
                    assert base_oid is not None
                    self.assertEqual(self.range_status(repository, base=base_oid), 0)
                self.configure_pr(head, base_oid)
                self.assert_record_deny(self.run_gate(repository))
                self.write_record(repository, head)
                self.assertEqual(self.run_gate(repository).stdout, b"")

    def test_missing_head_object_falls_back_to_record(self) -> None:
        repository = self.create_pr_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        self.configure_pr(ABSENT_HEAD_SHA, repository.rev("master"))
        self.assert_record_deny(self.run_gate(repository))
        self.write_record(repository, ABSENT_HEAD_SHA)
        self.assertEqual(self.run_gate(repository).stdout, b"")

    def test_local_base_ref_containing_pr_code_commit_does_not_hide_it(self) -> None:
        """ローカルの origin/<base> が PR の head にあるコード commit を含んでいても、
        PR の base commit (baseRefOid) がその commit を含まなければ skip しない。"""
        repository = self.create_pr_repository()
        base = repository.rev("master")
        repository.write("tool.py", "print('changed')\n")
        code_commit = repository.commit_all("code")
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        repository.git("update-ref", "refs/remotes/origin/master", code_commit)
        # ローカルの ref を起点にすると、コード commit が範囲から外れてテキストだけになる。
        self.assertEqual(
            self.range_status(repository, base="refs/remotes/origin/master"), 0
        )
        self.assertEqual(self.range_status(repository, base=base), 1)
        self.configure_pr(repository.head(), base)
        self.assert_record_deny(self.run_gate(repository))


@unittest.skipUnless(NODE, "wrapper integration requires node")
class WrapperTextOnlySkipTest(FakeGhMixin, WorkspaceTestCase):
    """2 つの codex review wrapper がテキスト系文書のみの変更で codex review を実行しない。

    fake codex companion は起動されると tripwire ファイルに追記するため、tripwire の
    有無で companion が起動されたかを確かめる。
    """

    def setUp(self) -> None:
        super().setUp()
        assert NODE is not None
        real_node = subprocess.check_output(
            [NODE, "-e", "process.stdout.write(process.execPath)"],
            env=os.environ.copy(),
        ).decode()
        self.env["PATH"] = (
            f"{Path(real_node).parent}{os.pathsep}{self.env.get('PATH', '')}"
        )
        self.tripwire = self.root / "companion-invocations.log"
        self.env[COMPANION_TRIPWIRE_ENV] = str(self.tripwire)
        companion = (
            self.home
            / ".claude"
            / "plugins"
            / "cache"
            / "openai-codex"
            / "codex"
            / "1.0.0"
            / "scripts"
            / "codex-companion.mjs"
        )
        companion.parent.mkdir(parents=True)
        companion.write_text(FAKE_COMPANION_SCRIPT, encoding="utf-8")

    def run_wrapper(
        self, wrapper: Path, repository: Repository
    ) -> subprocess.CompletedProcess[str]:
        assert BASH is not None
        return subprocess.run(
            [BASH, str(wrapper)],
            cwd=repository.path,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=180,
        )

    # -- pre-push-codex-review --------------------------------------------

    def create_push_repository(self) -> Repository:
        repository = self.create_repository()
        self.attach_origin(repository)
        return repository

    def test_push_wrapper_skips_text_only_branch(self) -> None:
        repository = self.create_push_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        self.assert_all_regular_text_changes(
            repository, ["README.md"], repository.merge_base("origin/master"), "HEAD"
        )
        pending = repository.git_dir() / PUSH_CODEX_PENDING
        pending.write_text("stale\n", encoding="utf-8")

        result = self.run_wrapper(PUSH_WRAPPER, repository)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(wrapper_body_lines(result.stdout), [SKIP_LINE], result.stdout)
        self.assertFalse(self.tripwire.exists(), "codex companion が起動された")
        self.assertFalse(pending.exists(), "pending attestation が残っている")
        guidance = [
            line
            for line in result.stderr.splitlines()
            if line.startswith("[run-pre-push-codex-review]")
            and "テキスト系文書" in line
        ]
        self.assertEqual(len(guidance), 1, result.stderr)

    def test_push_wrapper_reviews_mixed_branch(self) -> None:
        repository = self.create_push_repository()
        repository.write("README.md", "# updated\n")
        repository.write("tool.py", "print('changed')\n")
        repository.commit_all("mixed")

        result = self.run_wrapper(PUSH_WRAPPER, repository)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(SKIP_LINE, result.stdout)
        self.assertTrue(self.tripwire.exists(), result.stderr)
        self.assertTrue(
            (repository.git_dir() / PUSH_CODEX_PENDING).exists(), result.stderr
        )

    # -- pre-merge-cross-review -------------------------------------------

    def create_merge_repository(self) -> Repository:
        self.install_fake_gh()
        repository = self.create_repository()
        self.attach_origin(repository)
        return repository

    def configure_merge_pr(self, repository: Repository) -> None:
        """PR の base commit は master branch の commit (fixture の base commit)。"""
        self.configure_fake_gh(
            {
                "number": PR_NUMBER,
                "headRefOid": repository.head(),
                "baseRefName": "master",
                "baseRefOid": repository.rev("master"),
            }
        )

    @unittest.skipUnless(JQ, "merge wrapper requires jq")
    def test_merge_wrapper_skips_text_only_pr(self) -> None:
        repository = self.create_merge_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        self.assert_all_regular_text_changes(
            repository,
            ["README.md"],
            repository.merge_base("refs/remotes/origin/master"),
            "HEAD",
        )
        self.configure_merge_pr(repository)
        git_dir = repository.git_dir()
        head = repository.head()
        final = git_dir / MERGE_FINAL_RECORD
        pending = git_dir / MERGE_PENDING_RECORD
        final.write_text(f"pr={PR_NUMBER}\nhead={head}\n", encoding="utf-8")
        pending.write_text(f"pr={PR_NUMBER}\nhead={head}\n", encoding="utf-8")

        result = self.run_wrapper(MERGE_WRAPPER, repository)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(wrapper_body_lines(result.stdout), [SKIP_LINE], result.stdout)
        self.assertFalse(self.tripwire.exists(), "codex companion が起動された")
        self.assertFalse(pending.exists(), "pending attestation が残っている")
        self.assertFalse(final.exists(), "起動前の final attestation が残っている")

    @unittest.skipUnless(JQ, "merge wrapper requires jq")
    def test_merge_wrapper_reviews_mixed_pr(self) -> None:
        repository = self.create_merge_repository()
        repository.write("README.md", "# updated\n")
        repository.write("tool.py", "print('changed')\n")
        repository.commit_all("mixed")
        self.configure_merge_pr(repository)

        result = self.run_wrapper(MERGE_WRAPPER, repository)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(SKIP_LINE, result.stdout)
        self.assertTrue(self.tripwire.exists(), result.stderr)
        self.assertEqual(
            (repository.git_dir() / MERGE_PENDING_RECORD).read_text(encoding="utf-8"),
            f"pr={PR_NUMBER}\nhead={repository.head()}\n",
        )

    @unittest.skipUnless(JQ, "merge wrapper requires jq")
    def test_merge_wrapper_reviews_when_local_base_ref_contains_pr_code_commit(
        self,
    ) -> None:
        """ローカルの origin/<base> が PR の head にあるコード commit を含んでいても、
        PR の base commit (baseRefOid) がその commit を含まなければ skip しない。"""
        repository = self.create_merge_repository()
        repository.write("tool.py", "print('changed')\n")
        code_commit = repository.commit_all("code")
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        repository.git("update-ref", "refs/remotes/origin/master", code_commit)
        # ローカルの ref を起点にすると、コード commit が範囲から外れてテキストだけになる。
        self.assertEqual(
            self.range_status(repository, base="refs/remotes/origin/master"), 0
        )
        self.configure_merge_pr(repository)

        result = self.run_wrapper(MERGE_WRAPPER, repository)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(SKIP_LINE, result.stdout)
        self.assertTrue(self.tripwire.exists(), result.stderr)
        self.assertTrue(
            (repository.git_dir() / MERGE_PENDING_RECORD).exists(), result.stderr
        )


@unittest.skipUnless(JQ, "auto-mark integration requires jq")
class AutoMarkIgnoresSkippedReportTest(WorkspaceTestCase):
    """auto-mark は `Status: skipped` の report では marker / 記録を書かない。

    同じ fixture で `Status: pass` の report なら書かれることを先に確かめ、書かれない
    理由が report の Status だけであることを示す。
    """

    def run_hook(
        self, hook: Path, repository: Repository, payload: dict[str, object]
    ) -> None:
        assert BASH is not None
        result = subprocess.run(
            [BASH, str(hook)],
            cwd=repository.path,
            input=json.dumps(payload).encode("utf-8"),
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    @staticmethod
    def start_payload(agent_type: str, agent_id: str) -> dict[str, object]:
        return {
            "hook_event_name": "SubagentStart",
            "session_id": "text-only-skip-session",
            "agent_id": agent_id,
            "agent_type": agent_type,
        }

    @staticmethod
    def stop_payload(agent_type: str, agent_id: str, message: str) -> dict[str, object]:
        return {
            "hook_event_name": "SubagentStop",
            "session_id": "text-only-skip-session",
            "agent_id": agent_id,
            "agent_type": agent_type,
            "last_assistant_message": message,
            "stop_hook_active": False,
        }

    @staticmethod
    def handback_payload(
        agent_type: str, agent_id: str, message: str
    ) -> dict[str, object]:
        return {
            "hook_event_name": "PostToolUse",
            "session_id": "text-only-skip-session",
            "agent_id": agent_id,
            "agent_type": agent_type,
            "tool_name": "SubagentHandback",
            "tool_input": {"message": message},
            "tool_response": {
                "success": True,
                "message": "Report delivered to your caller.",
            },
            "tool_use_id": "toolu_text_only_skip",
        }

    def create_reviewed_repository(self) -> Repository:
        repository = self.create_repository()
        self.attach_origin(repository)
        repository.write("tool.py", "print('changed')\n")
        repository.commit_all("code")
        return repository

    def review_hash(self, repository: Repository) -> str:
        assert BASH is not None
        return (
            subprocess.check_output(
                [
                    BASH,
                    "-c",
                    'source "$1" && compute_review_hash master',
                    "diff-hash",
                    str(DIFF_HASH_LIB),
                ],
                cwd=repository.path,
                env=self.env,
            )
            .decode()
            .strip()
        )

    def test_push_auto_mark_does_not_promote_skipped_report(self) -> None:
        repository = self.create_reviewed_repository()
        git_dir = repository.git_dir()
        marker = git_dir / PUSH_CODEX_MARKER
        pending = git_dir / PUSH_CODEX_PENDING

        def cycle(agent_id: str, report: str, *, handback: bool) -> None:
            marker.unlink(missing_ok=True)
            self.run_hook(
                PUSH_AUTO_MARK,
                repository,
                self.start_payload(PUSH_CODEX_REVIEWER, agent_id),
            )
            pending.write_text(self.review_hash(repository), encoding="utf-8")
            if handback:
                self.run_hook(
                    PUSH_AUTO_MARK,
                    repository,
                    self.handback_payload(PUSH_CODEX_REVIEWER, agent_id, report),
                )
                closing = HANDBACK_CLOSING_MESSAGE
            else:
                closing = report
            self.run_hook(
                PUSH_AUTO_MARK,
                repository,
                self.stop_payload(PUSH_CODEX_REVIEWER, agent_id, closing),
            )

        cycle("controlpass0", PASS_REPORT, handback=False)
        self.assertTrue(marker.exists(), "control: pass report で marker が書かれない")

        for label, handback in (("stop", False), ("handback", True)):
            with self.subTest(path=label):
                cycle(f"skipped{label}0", SKIPPED_REPORT, handback=handback)
                self.assertFalse(marker.exists())
                self.assertFalse(pending.exists())
                self.assertFalse(
                    (git_dir / f"{PUSH_HANDBACK_PREFIX}skipped{label}0").exists()
                )

    def test_merge_auto_mark_does_not_promote_skipped_report(self) -> None:
        repository = self.create_reviewed_repository()
        git_dir = repository.git_dir()
        final = git_dir / MERGE_FINAL_RECORD
        pending = git_dir / MERGE_PENDING_RECORD

        def cycle(agent_id: str, report: str, *, handback: bool) -> None:
            final.unlink(missing_ok=True)
            self.run_hook(
                MERGE_AUTO_MARK,
                repository,
                self.start_payload(MERGE_CODEX_REVIEWER, agent_id),
            )
            pending.write_text(
                f"pr={PR_NUMBER}\nhead={repository.head()}\n", encoding="utf-8"
            )
            if handback:
                self.run_hook(
                    MERGE_AUTO_MARK,
                    repository,
                    self.handback_payload(MERGE_CODEX_REVIEWER, agent_id, report),
                )
                closing = HANDBACK_CLOSING_MESSAGE
            else:
                closing = report
            self.run_hook(
                MERGE_AUTO_MARK,
                repository,
                self.stop_payload(MERGE_CODEX_REVIEWER, agent_id, closing),
            )

        cycle("controlpass0", PASS_REPORT, handback=False)
        self.assertTrue(final.exists(), "control: pass report で記録が昇格しない")

        for label, handback in (("stop", False), ("handback", True)):
            with self.subTest(path=label):
                cycle(f"skipped{label}0", SKIPPED_REPORT, handback=handback)
                self.assertFalse(final.exists())
                self.assertFalse(pending.exists())
                self.assertFalse(
                    (git_dir / f"{MERGE_HANDBACK_PREFIX}skipped{label}0").exists()
                )


@unittest.skipUnless(NODE, "review cadence integration requires node")
class ReviewCadenceIgnoresSkippedReportTest(unittest.TestCase):
    """review cadence は `Status: skipped` の report を完了 review として数えない。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state_root = Path(self.temporary.name) / "state"

    def run_hook(self, payload: dict[str, object]) -> None:
        assert NODE is not None
        env = os.environ.copy()
        env["PRE_PUSH_CODEX_REVIEW_CADENCE_STATE_ROOT"] = str(self.state_root)
        result = subprocess.run(
            [NODE, str(CADENCE_HOOK)],
            input=json.dumps(payload),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            cwd=self.temporary.name,
            env=env,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def completed_reviews(self, session_id: str) -> int:
        if not self.state_root.exists():
            return 0
        for path in self.state_root.rglob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("sessionId") == session_id:
                return int(record["completedReviews"])
        return 0

    def complete_review(
        self, agent_type: str, session_id: str, report: str, *, handback: bool
    ) -> None:
        agent_id = f"{session_id}-agent"
        self.run_hook(
            {
                "hook_event_name": "SubagentStart",
                "session_id": session_id,
                "agent_id": agent_id,
                "agent_type": agent_type,
            }
        )
        if handback:
            self.run_hook(
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "agent_type": agent_type,
                    "tool_name": "SubagentHandback",
                    "tool_input": {"message": report},
                    "tool_response": {
                        "success": True,
                        "message": "Report delivered to your caller.",
                    },
                    "tool_use_id": "toolu_text_only_skip",
                }
            )
        self.run_hook(
            {
                "hook_event_name": "SubagentStop",
                "session_id": session_id,
                "agent_id": agent_id,
                "agent_type": agent_type,
                "last_assistant_message": HANDBACK_CLOSING_MESSAGE
                if handback
                else report,
                "stop_hook_active": False,
            }
        )

    def test_skipped_report_is_not_counted(self) -> None:
        for agent_type in (PUSH_CODEX_REVIEWER, MERGE_CODEX_REVIEWER):
            for handback in (False, True):
                label = (
                    f"{agent_type.split(':')[0]}-{'handback' if handback else 'stop'}"
                )
                with self.subTest(case=label):
                    self.complete_review(
                        agent_type, f"control-{label}", PASS_REPORT, handback=handback
                    )
                    self.assertEqual(self.completed_reviews(f"control-{label}"), 1)
                    self.complete_review(
                        agent_type,
                        f"skipped-{label}",
                        SKIPPED_REPORT,
                        handback=handback,
                    )
                    self.assertEqual(self.completed_reviews(f"skipped-{label}"), 0)


class SkippedReportContractTest(unittest.TestCase):
    """codex-reviewer の report 契約が `Status: skipped` を定義する。"""

    AGENTS = {
        "pre-push-codex-review": PUSH_CODEX_AGENT,
        "pre-merge-cross-review": MERGE_CODEX_AGENT,
    }

    def test_codex_reviewers_allow_skipped_status(self) -> None:
        for name, path in self.AGENTS.items():
            with self.subTest(agent=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn(
                    "Allowed status values are "
                    "`Status: pass | findings | execution-failed | skipped`.",
                    body,
                )

    def test_codex_reviewers_define_the_skipped_report(self) -> None:
        for name, path in self.AGENTS.items():
            with self.subTest(agent=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn(f"`{SKIP_LINE}`", body)
                self.assertIn(f"```markdown\n{SKIPPED_REPORT}\n```", body)
                self.assertIn("exited zero", body)

    def test_merge_reviewer_checks_skip_before_inconclusive_body(self) -> None:
        body = MERGE_CODEX_AGENT.read_text(encoding="utf-8")
        skip_index = body.find(f"`{SKIP_LINE}`")
        inconclusive_index = body.find("If the body is inconclusive")
        self.assertNotEqual(skip_index, -1)
        self.assertNotEqual(inconclusive_index, -1)
        self.assertLess(skip_index, inconclusive_index)


class TextOnlySkipDocumentationTest(unittest.TestCase):
    """利用者向け文書がテキスト系文書のみの変更の扱いと判定 lib の所在を示す。"""

    def test_plugin_readmes_document_text_only_skip(self) -> None:
        for name, path in PLUGIN_READMES.items():
            with self.subTest(plugin=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn("## テキスト系文書のみの変更", body)
                self.assertIn("hooks/scripts/lib/text-paths.sh", body)

    def test_plugin_readmes_state_the_current_definition(self) -> None:
        """拡張子は md / markdown / rst / adoc の 4 つで、追加した除外対象と大文字小文字を
        区別しない照合を説明する。"""
        for name, path in PLUGIN_READMES.items():
            with self.subTest(plugin=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn("`md` / `markdown` / `rst` / `adoc`", body)
                self.assertNotIn("`md` / `mdx` /", body)
                for excluded in (
                    "`output-styles`",
                    "`.cursor`",
                    "`.gemini`",
                    "`.codex`",
                    "`.windsurf`",
                    "`.clinerules`",
                    "`.roo`",
                    "`.kiro`",
                    "`.continue`",
                    "`.junie`",
                    "`.amazonq`",
                    "`.github` → `instructions`",
                    "`.github` → `prompts`",
                    "`.github` → `chatmodes`",
                    "`copilot-instructions.md`",
                    "`AGENTS.override.md`",
                ):
                    self.assertIn(excluded, body)
                self.assertIn("大文字小文字を区別しません", body)
                self.assertIn(
                    "次に列挙した場所・名前の Markdown はコードとして扱います", body
                )

    def test_plugin_readmes_state_the_limits_of_the_exclusion_list(self) -> None:
        """既知の制約に、列挙に無い指示ファイルと間接的に読み込まれる文書を書く。"""
        for name, path in PLUGIN_READMES.items():
            with self.subTest(plugin=name):
                body = path.read_text(encoding="utf-8")
                constraints = body[body.index("## 既知の制約") :]
                self.assertIn(
                    "列挙に無い場所にある agent の指示ファイルはテキスト系文書と判定される",
                    constraints,
                )
                self.assertIn(
                    "除外対象から間接的に読み込まれる文書は検出しない", constraints
                )
                self.assertIn("`@path` で import される文書", constraints)

    def test_readmes_say_text_only_skip_does_not_scan_for_secrets(self) -> None:
        """skip ではレビューを行わないため認証情報も検査されず、secret scanner の併用を
        推奨する。"""
        for name, path in {**PLUGIN_READMES, "root": ROOT / "README.md"}.items():
            with self.subTest(readme=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn("文書に書き込まれた token などの認証情報", body)
                self.assertIn(
                    "gitleaks 等の secret scanner を pre-commit / pre-push hook として併用",
                    body,
                )

    def test_split_plugin_readmes_list_the_lib_in_file_table(self) -> None:
        for name in ("pre-push-codex-review", "pre-merge-cross-review"):
            with self.subTest(plugin=name):
                body = PLUGIN_READMES[name].read_text(encoding="utf-8")
                self.assertIn("| `hooks/scripts/lib/text-paths.sh` |", body)

    def test_merge_readme_uses_pr_base_commit_as_range_start(self) -> None:
        body = PLUGIN_READMES["pre-merge-cross-review"].read_text(encoding="utf-8")
        self.assertIn("baseRefOid", body)
        self.assertNotIn("baseRefName`)", body)
        self.assertNotIn("base branch 名が英数字", body)

    def test_review_commands_say_gate_does_not_deny_text_only_push(self) -> None:
        """コマンドは「gate が deny しないため実行不要」と述べ、該当するかの判定を
        session に委ねない。"""
        for name, path in REVIEW_COMMANDS.items():
            with self.subTest(plugin=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn("テキスト系文書だけの push", body)
                self.assertIn("deny しないため", body)
                self.assertIn("push gate が判定します", body)

    def test_merge_order_rules_always_launch_the_reviewer(self) -> None:
        """テキスト系文書だけの PR でも codex-reviewer を起動し、`Status: skipped` を
        受け取った head SHA では再起動しない。起動を省く判定を session にさせない。"""
        body = MERGE_ORDER_RULES.read_text(encoding="utf-8")
        self.assertIn(
            "`Status: pass` / `Status: findings` / `Status: skipped` の report を"
            "受け取っている場合に限り、再起動しない",
            body,
        )
        self.assertIn(
            "PR の差分がテキスト系文書だけの場合も起動手順は同じで、"
            "codex-reviewer を起動する",
            body,
        )
        self.assertIn("セッション自身で判定しない", body)
        self.assertNotIn("判定に迷う場合は起動してよい", body)
        self.assertNotIn("起動は不要", body)

    def test_merge_docs_allow_one_relaunch_after_skipped_and_deny(self) -> None:
        """skipped を受け取った後に merge gate に deny された場合は、同じ head SHA でも
        1 回だけ再起動してよい (注入文と README の両方に書く)。"""
        rules = MERGE_ORDER_RULES.read_text(encoding="utf-8")
        self.assertIn(
            "`Status: skipped` を受け取った後でも merge gate に deny された場合は、"
            "同じ head SHA でも codex-reviewer を 1 回だけ再起動してよい",
            rules,
        )
        readme = PLUGIN_READMES["pre-merge-cross-review"].read_text(encoding="utf-8")
        self.assertIn(
            "同じ head SHA で codex-reviewer を 1 回だけ再起動してかまいません", readme
        )
        for body in (rules, readme):
            self.assertIn("base を fetch", body)


if __name__ == "__main__":
    unittest.main()
