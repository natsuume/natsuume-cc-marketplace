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

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any


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
# 実際の gitleaks (fake ではない)。無い環境では実物を使うテストを skip する。
REAL_GITLEAKS = shutil.which("gitleaks")

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
    "GIT_GRAFT_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "XDG_CONFIG_HOME",
    # gitleaks の設定を利用者の環境から渡す変数 (判定 lib はこれがあると `--config` を渡さない)。
    "GITLEAKS_CONFIG",
    "GITLEAKS_CONFIG_TOML",
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
    "docs/café.md",
    "docs/Ärger.md",
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
    # 大文字小文字の変換や畳み込みで ASCII の文字になる非 ASCII 文字を含む (場所を問わない)
    "plugins/x/ſkills/s/SKILL.md",
    ".Kiro/x.md",
    "docs/İnstructions.md",
    "docs/ındex.md",
    "docs/straße.md",
    "docs/STRAẞE.md",
    "docs/ﬀ.md",
    "docs/ﬁle.md",
    "docs/ﬂow.md",
    "docs/ﬃx.md",
    "docs/ﬄx.md",
    "docs/ﬅyle.md",
    "docs/ﬆyle.md",
    # HFS+ が名前の比較で無視する不可視の文字を含む (場所を問わない)
    "plugins/x/sk‌ills/s/SKILL.md",
    "docs/a‍.md",
    "docs/a‎.md",
    "docs/a‏.md",
    "docs/a‪.md",
    "docs/a‫.md",
    "docs/a‬.md",
    "docs/a‭.md",
    "docs/a‮.md",
    "docs/a⁪.md",
    "docs/a⁫.md",
    "docs/a⁬.md",
    "docs/a⁭.md",
    "docs/a⁮.md",
    "docs/a⁯.md",
    "﻿CLAUDE.md",
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


# fake gitleaks は、受け取った引数と、起動時点の `--config` の内容・`--gitleaks-ignore-path`
# の中身・git に関する環境変数 (`GIT_DIR` / `GIT_WORK_TREE` / `GIT_NO_REPLACE_OBJECTS` と、
# 設定を渡す `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_0` / `GIT_CONFIG_VALUE_0`) を記録し、
# 環境変数で指定した終了コードで終わる。stdout と stderr に目印の文字列を出し、hook や
# wrapper の出力へ流れないことの確認に使う。
GITLEAKS_LOG_ENV = "TEXT_ONLY_SKIP_TEST_GITLEAKS_LOG"
GITLEAKS_EXIT_ENV = "TEXT_ONLY_SKIP_TEST_GITLEAKS_EXIT"
GITLEAKS_STDERR_ENV = "TEXT_ONLY_SKIP_TEST_GITLEAKS_STDERR"
GITLEAKS_OUTPUT_MARKER = "FAKE-GITLEAKS-FINDING ghp_textonlyskiptestsecret0000000000"
FAKE_GITLEAKS_TEMPLATE = """#!{python}
import json
import os
import sys

args = sys.argv[1:]


def option_value(name):
    if name in args and args.index(name) + 1 < len(args):
        return args[args.index(name) + 1]
    return None


config_path = option_value("--config")
config = None
if config_path is not None:
    with open(config_path, encoding="utf-8") as fp:
        config = fp.read()
ignore_path = option_value("--gitleaks-ignore-path")
ignore_files = None
if ignore_path is not None:
    ignore_files = {{}}
    for name in sorted(os.listdir(ignore_path)):
        with open(os.path.join(ignore_path, name), encoding="utf-8") as fp:
            ignore_files[name] = fp.read()
git_env = {{
    name: os.environ.get(name)
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_CONFIG_COUNT",
    )
}}
git_config = [
    (os.environ.get(f"GIT_CONFIG_KEY_{{index}}"), os.environ.get(f"GIT_CONFIG_VALUE_{{index}}"))
    for index in range(int(os.environ.get("GIT_CONFIG_COUNT") or 0))
]
record = {{
    "args": args,
    "config": config,
    "ignore_files": ignore_files,
    "git_env": git_env,
    "git_config": git_config,
}}
with open(os.environ["{log_env}"], "a", encoding="utf-8") as fp:
    fp.write(json.dumps(record) + "\\n")
exit_code = int(os.environ.get("{exit_env}", "0"))
print("{marker}")
# 実際の gitleaks は `--log-level error` の正常な走査では stderr に何も出さない。stderr に
# 出すのは、終了コードが 0 でないときと、内部の git の失敗を模す指定があるときだけにする。
if exit_code != 0 or os.environ.get("{stderr_env}") == "1":
    print("{marker}", file=sys.stderr)
sys.exit(exit_code)
"""
# `--log-opts` で範囲の前に付けるオプション (`git log -p` の出力をローカルの属性と設定で
# 変えさせない)。
GITLEAKS_LOG_OPTS_PREFIX = (
    "--no-textconv --no-ext-diff --text --no-color --src-prefix=a/ --dst-prefix=b/"
)
# base の tree に `.gitleaks.toml` が無いときに渡す設定 (gitleaks の既定のルールだけを使う)。
DEFAULT_GITLEAKS_CONFIG = "[extend]\nuseDefault = true\n"
# base の tree に置く設定・ignore と、working tree に置く (渡されてはならない) 設定・ignore。
BASE_GITLEAKS_CONFIG = (
    '[extend]\nuseDefault = true\n\n[allowlist]\ndescription = "base"\n'
)
BASE_GITLEAKS_IGNORE = "base-fingerprint:README.md:generic-api-key:1\n"
UNTRACKED_GITLEAKS_CONFIG = '[allowlist]\ndescription = "untracked"\npaths = [".*"]\n'
UNTRACKED_GITLEAKS_IGNORE = "untracked-fingerprint:README.md:generic-api-key:1\n"
# テキスト系文書だけだが gitleaks で止めたとき、gate が deny の理由文に加える 1 文。
GITLEAKS_DENY_SENTENCE = (
    "変更はテキスト系文書だけですが、gitleaks が秘匿情報の候補を検出したか"
    "実行に失敗したため、レビューを要求します。"
)

# gitleaks を含まない PATH (実行環境の PATH にある実行ファイルのうち gitleaks 以外への
# symlink を集めたディレクトリ)。プロセス内で 1 度だけ作る。
_GITLEAKS_FREE_BIN: tempfile.TemporaryDirectory[str] | None = None


def gitleaks_free_bin() -> Path:
    global _GITLEAKS_FREE_BIN
    if _GITLEAKS_FREE_BIN is None:
        _GITLEAKS_FREE_BIN = tempfile.TemporaryDirectory()
        farm = Path(_GITLEAKS_FREE_BIN.name)
        seen: set[str] = set()
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            candidate = Path(directory)
            if not directory or not candidate.is_dir():
                continue
            for entry in candidate.iterdir():
                if entry.name in seen or entry.name == "gitleaks":
                    continue
                try:
                    if entry.is_file() and os.access(entry, os.X_OK):
                        (farm / entry.name).symlink_to(entry)
                        seen.add(entry.name)
                except OSError:
                    continue
    return Path(_GITLEAKS_FREE_BIN.name)


def wrapper_body_lines(stdout: str) -> list[str]:
    """wrapper の出力から、terminal sentinel の案内行・終了行と空行を除いた行。"""
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
        # 実行環境の gitleaks の有無に結果が左右されないよう、既定では PATH の先頭に fake
        # gitleaks (終了コード 0 = 検出なし) を置く。
        self.fake_gitleaks_dir = self.root / "fake-gitleaks-bin"
        self.fake_gitleaks_dir.mkdir()
        fake_gitleaks = self.fake_gitleaks_dir / "gitleaks"
        fake_gitleaks.write_text(
            FAKE_GITLEAKS_TEMPLATE.format(
                python=sys.executable,
                log_env=GITLEAKS_LOG_ENV,
                exit_env=GITLEAKS_EXIT_ENV,
                stderr_env=GITLEAKS_STDERR_ENV,
                marker=GITLEAKS_OUTPUT_MARKER,
            ),
            encoding="utf-8",
        )
        fake_gitleaks.chmod(fake_gitleaks.stat().st_mode | stat.S_IXUSR)
        self.gitleaks_log = self.root / "gitleaks-calls.jsonl"
        env[GITLEAKS_LOG_ENV] = str(self.gitleaks_log)
        env[GITLEAKS_EXIT_ENV] = "0"
        env["PATH"] = f"{self.fake_gitleaks_dir}{os.pathsep}{env.get('PATH', '')}"
        self.env = env

    # ------------------------------------------------------------------
    # gitleaks
    # ------------------------------------------------------------------

    def set_gitleaks_exit(self, code: int) -> None:
        self.env[GITLEAKS_EXIT_ENV] = str(code)

    def gitleaks_records(self) -> list[dict[str, Any]]:
        """fake gitleaks の起動ごとの記録 (args / config / ignore_files / git_env)。"""
        if not self.gitleaks_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.gitleaks_log.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def gitleaks_calls(self) -> list[list[str]]:
        return [record["args"] for record in self.gitleaks_records()]

    def remove_gitleaks_from_path(self) -> None:
        """PATH から gitleaks を取り除く (fake を外し、gitleaks を含むディレクトリを
        gitleaks 以外の実行ファイルの symlink 集合に置き換える)。"""
        entries: list[str] = []
        replaced = False
        for entry in self.env["PATH"].split(os.pathsep):
            if entry == str(self.fake_gitleaks_dir):
                continue
            if entry and (Path(entry) / "gitleaks").exists():
                if not replaced:
                    entries.append(str(gitleaks_free_bin()))
                    replaced = True
                continue
            entries.append(entry)
        self.env["PATH"] = os.pathsep.join(entries)
        self.assertIsNone(shutil.which("gitleaks", path=self.env["PATH"]))

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

    def test_replace_ref_does_not_hide_code_commit(self) -> None:
        """ローカルの replace ref でコードを足す commit をテキスト系文書だけの commit に
        見せかけても、置き換え前の object で判定する。"""
        repository = self.create_repository()
        repository.write("README.md", "# replacement\n")
        repository.git("add", "README.md")
        replacement = repository.git(
            "commit-tree",
            repository.git("write-tree").strip(),
            "-p",
            repository.rev("master"),
            "-m",
            "replacement",
        ).strip()
        repository.write("helper.py", "print('added')\n")
        real = repository.commit_all("code")
        repository.git("replace", real, replacement)
        # replace ref を使う通常の git の呼び出しでは、HEAD は README.md だけを変える commit に
        # 見える。
        self.assertEqual(repository.changed_paths("master", "HEAD"), ["README.md"])
        self.assertEqual(self.range_status(repository), 1)

    def test_graft_does_not_hide_intermediate_code_commit(self) -> None:
        """graft で、コードを足して後で消す中間 commit を範囲から隠しても判定しない。"""
        repository = self.create_repository()
        repository.write("helper.py", "print('added')\n")
        repository.commit_all("code")
        repository.remove("helper.py")
        repository.write("README.md", "# updated\n")
        head = repository.commit_all("docs")
        self.assertEqual(self.range_status(repository), 1)

        graft_file = repository.git_dir() / "info" / "grafts"
        graft_file.parent.mkdir(exist_ok=True)
        graft_file.write_text(f"{head} {repository.rev('master')}\n", encoding="utf-8")
        # graft を読む通常の git の呼び出しでは、範囲は README.md だけを変える 1 commit に見える。
        self.assertEqual(len(repository.range_commits("master")), 1)
        # `--git-path` が返す相対 path を、target_cwd を指定した場合と現在の cwd の場合の
        # どちらでも正しく解決する。
        with self.subTest(target_cwd="repository path"):
            self.assertEqual(self.range_status(repository), 1)
        with self.subTest(target_cwd="empty"):
            self.assertEqual(
                self.call_text_paths(
                    "is_text_only_range_in", "", "master", "HEAD", cwd=repository.path
                ),
                1,
            )

    def test_graft_file_from_environment_is_detected(self) -> None:
        """`GIT_GRAFT_FILE` が指す graft ファイルも検出する。"""
        repository = self.create_repository()
        repository.write("helper.py", "print('added')\n")
        repository.commit_all("code")
        repository.remove("helper.py")
        repository.write("README.md", "# updated\n")
        head = repository.commit_all("docs")
        graft_file = self.root / "outside-grafts"
        graft_file.write_text(f"{head} {repository.rev('master')}\n", encoding="utf-8")
        self.env["GIT_GRAFT_FILE"] = str(graft_file)
        self.assertEqual(len(repository.range_commits("master")), 1)
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


class TextOnlyRangeScanCostTest(WorkspaceTestCase):
    """範囲の走査を hook の時間内に終えるための commit 数の上限と判定順。"""

    MAX_RANGE_COMMITS = 100

    def create_branch_with_text_commits(self, count: int) -> Repository:
        """master の上に、README.md だけを変える commit を count 個積んだ feature branch。

        fixture を速く作るため `git commit-tree` で積む (2 個目以降は親と同じ tree の commit)。
        """
        repository = self.create_repository(f"repo-{count}")
        repository.write("README.md", "# updated\n")
        repository.git("add", "README.md")
        tree = repository.git("write-tree").strip()
        tip = repository.rev("master")
        for index in range(count):
            tip = repository.git(
                "commit-tree", tree, "-p", tip, "-m", f"docs {index}"
            ).strip()
        repository.git("update-ref", "refs/heads/feature", tip)
        self.assertEqual(len(repository.range_commits("master")), count)
        return repository

    def test_range_at_the_commit_limit_is_text_only(self) -> None:
        repository = self.create_branch_with_text_commits(self.MAX_RANGE_COMMITS)
        self.assertEqual(self.range_status(repository), 0)

    def test_range_over_the_commit_limit_requires_review(self) -> None:
        repository = self.create_branch_with_text_commits(self.MAX_RANGE_COMMITS + 1)
        self.assertEqual(self.range_status(repository), 1)
        self.assertEqual(self.gitleaks_calls(), [])

    def test_code_in_net_diff_is_rejected_before_scanning_each_commit(self) -> None:
        """net diff にコードがある範囲は、各 commit の差分を取らずに 1 を返す。

        コードを足す commit を範囲の最も古い位置に置く (rev-list は新しい順に並べるため、
        各 commit を先に走査すると、テキストだけの commit の差分を取った後でコードに当たる)。
        """
        repository = self.create_repository()
        repository.write("helper.py", "print('added')\n")
        repository.commit_all("code")
        for index in range(4):
            repository.write("README.md", f"# updated {index}\n")
            repository.commit_all(f"docs {index}")
        git_log = self.install_logging_git_shim()

        self.assertEqual(self.range_status(repository), 1)

        diff_tree_calls = [
            line
            for line in git_log.read_text(encoding="utf-8").splitlines()
            if " diff-tree " in f" {line} "
        ]
        self.assertEqual(len(diff_tree_calls), 1, diff_tree_calls)

    def install_logging_git_shim(self) -> Path:
        """PATH の先頭に、受け取った引数を 1 行ずつ記録してから本物の git を実行する shim を
        置き、記録先の path を返す (shim を置いた後の git の呼び出しだけが記録される)。"""
        assert GIT is not None
        shim_dir = self.root / "git-shim-bin"
        shim_dir.mkdir()
        git_log = self.root / "git-calls.log"
        shim = shim_dir / "git"
        shim.write_text(
            f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{git_log}"\nexec "{GIT}" "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
        self.env["PATH"] = f"{shim_dir}{os.pathsep}{self.env['PATH']}"
        return git_log

    def test_every_git_call_ignores_replace_refs_and_commit_graph(self) -> None:
        """判定 lib の git の呼び出しはすべて replace ref と commit-graph を読まない。"""
        repository = self.create_base_with_gitleaks_files_and_docs_commit()
        git_log = self.install_logging_git_shim()
        self.assertEqual(self.range_status(repository), 0)
        calls = git_log.read_text(encoding="utf-8").splitlines()
        # 範囲の判定と、base の tree からの gitleaks の設定の取り出しを含む。
        self.assertTrue(any(" diff-tree " in f" {call} " for call in calls), calls)
        self.assertTrue(any(" cat-file " in f" {call} " for call in calls), calls)
        for call in calls:
            with self.subTest(call=call):
                self.assertTrue(
                    call.startswith("--no-replace-objects -c core.commitGraph=false "),
                    call,
                )

    def create_base_with_gitleaks_files_and_docs_commit(self) -> Repository:
        repository = self.create_repository("repo-with-gitleaks-files")
        repository.git("switch", "-q", "master")
        repository.write(".gitleaks.toml", DEFAULT_GITLEAKS_CONFIG)
        repository.commit_all("gitleaks config")
        repository.git("switch", "-q", "feature")
        repository.git("reset", "-q", "--hard", "master")
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        return repository

    def test_diff_check_stops_at_the_deadline(self) -> None:
        """期限 (bash の `SECONDS` の値) に達していれば、git を呼ばずに 1 を返す。"""
        repository = self.create_repository()
        repository.write("README.md", "# updated\n")
        head = repository.commit_all("docs")
        master = repository.rev("master")
        self.assertEqual(
            self.call_text_paths(
                "_text_paths_diff_is_text_only",
                str(repository.path),
                master,
                head,
                "1000000",
            ),
            0,
        )
        git_log = self.install_logging_git_shim()
        self.assertEqual(
            self.call_text_paths(
                "_text_paths_diff_is_text_only", str(repository.path), master, head, "0"
            ),
            1,
        )
        self.assertFalse(git_log.exists())

    def test_raw_entry_parser_stops_at_the_deadline(self) -> None:
        """raw 差分の解析は、各エントリを読む前に期限を確かめる。"""
        assert BASH is not None
        script = (
            'source "$1" || exit 99; '
            "printf ':100644 100644 0000000 1111111 M\\0README.md\\0' "
            '| _text_paths_raw_entries_are_text_only "$2"'
        )
        for deadline, expected in (("1000000", 0), ("0", 1)):
            with self.subTest(deadline=deadline):
                result = subprocess.run(
                    [BASH, "-c", script, "text-paths-test", str(TEXT_PATHS_LIB), deadline],
                    cwd=self.root,
                    env=self.env,
                    capture_output=True,
                    check=False,
                    timeout=60,
                )
                self.assertEqual(result.returncode, expected, result.stderr)


class TextOnlyTimeLimitTest(WorkspaceTestCase):
    """_text_paths_run_with_time_limit: 上限を超えた command を打ち切る。"""

    def test_command_over_the_limit_is_killed(self) -> None:
        started = time.monotonic()
        status = self.call_text_paths(
            "_text_paths_run_with_time_limit", "1", "sleep", "30"
        )
        elapsed = time.monotonic() - started
        self.assertEqual(status, 124)
        self.assertLess(elapsed, 10)

    def test_command_within_the_limit_returns_its_status(self) -> None:
        cases = ((("true",), 0), (("sh", "-c", "exit 3"), 3))
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(
                    self.call_text_paths(
                        "_text_paths_run_with_time_limit", "5", *command
                    ),
                    expected,
                )


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


class TextOnlySecretsScanTest(WorkspaceTestCase):
    """テキスト系文書だけの範囲は、PATH に gitleaks があれば skip の前に秘匿情報を検査する。

    戻り値は 0 = skip してよい / 1 = テキスト系文書だけではない / 2 = テキスト系文書だけだが
    gitleaks が候補を検出した、または gitleaks の実行が失敗した。
    """

    def create_text_only_branch(self) -> Repository:
        repository = self.create_repository()
        repository.git(
            "update-ref", "refs/remotes/origin/master", repository.rev("master")
        )
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        return repository

    def single_gitleaks_record(self) -> dict[str, Any]:
        records = self.gitleaks_records()
        self.assertEqual(len(records), 1, records)
        return records[0]

    def assert_no_leftover_temporary_files(self) -> None:
        leftovers = sorted(path.name for path in self.tmp.glob("text-paths-gitleaks*"))
        self.assertEqual(leftovers, [])

    def test_gitleaks_receives_the_resolved_range_and_fixed_options(self) -> None:
        repository = self.create_text_only_branch()
        self.assertEqual(self.range_status(repository, base="master"), 0)
        record = self.single_gitleaks_record()
        args = record["args"]
        # 一時ファイル・一時ディレクトリの path は実行ごとに変わるため、位置だけを確かめる。
        # 走査元 (位置引数) は ignore の置き場を兼ねる一時ディレクトリで、repo ではない。
        config_path = args[args.index("--config") + 1]
        source_path = args[args.index("--gitleaks-ignore-path") + 1]
        for temporary in (config_path, source_path):
            self.assertTrue(
                temporary.startswith(f"{self.tmp}{os.sep}text-paths-gitleaks."),
                temporary,
            )
        self.assertFalse(config_path.startswith(f"{source_path}{os.sep}"), config_path)
        # repo は GIT_DIR で渡し、git に repo の working tree・replace ref・commit-graph を
        # 参照させず、差分の path の表記を git の既定に固定する。
        self.assertEqual(
            record["git_env"],
            {
                "GIT_DIR": str(repository.git_dir()),
                "GIT_WORK_TREE": source_path,
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_CONFIG_COUNT": "5",
            },
        )
        self.assertEqual(
            record["git_config"],
            [
                ["core.commitGraph", "false"],
                ["diff.noprefix", "false"],
                ["diff.mnemonicPrefix", "false"],
                ["diff.relative", "false"],
                ["core.quotePath", "true"],
            ],
        )
        self.assertEqual(
            args,
            [
                "git",
                "--no-banner",
                "--redact",
                "--ignore-gitleaks-allow",
                "--log-level",
                "error",
                "--exit-code",
                "1",
                "--timeout",
                "20",
                "--config",
                config_path,
                "--gitleaks-ignore-path",
                source_path,
                f"--log-opts={GITLEAKS_LOG_OPTS_PREFIX} "
                f"{repository.rev('master')}..{repository.head()}",
                source_path,
            ],
        )
        self.assert_no_leftover_temporary_files()

    def create_branch_on_base_with_gitleaks_files(self) -> Repository:
        """base (master) の tree に `.gitleaks.toml` と `.gitleaksignore` を持つ repository。"""
        path = self.root / "repo-with-config"
        path.mkdir()
        repository = Repository(path, self.env)
        repository.git("-c", "init.defaultBranch=master", "init", "-q")
        repository.write("README.md", "# base\n")
        repository.write(".gitleaks.toml", BASE_GITLEAKS_CONFIG)
        repository.write(".gitleaksignore", BASE_GITLEAKS_IGNORE)
        repository.commit_all("base")
        repository.git("switch", "-q", "-c", "feature")
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        return repository

    def test_config_and_ignore_come_from_the_base_tree(self) -> None:
        repository = self.create_branch_on_base_with_gitleaks_files()
        # working tree の内容を base と変えておき、base の tree から読むことを確かめる。
        repository.write(".gitleaks.toml", UNTRACKED_GITLEAKS_CONFIG)
        repository.write(".gitleaksignore", UNTRACKED_GITLEAKS_IGNORE)
        self.assertEqual(self.range_status(repository), 0)
        record = self.single_gitleaks_record()
        self.assertEqual(record["config"], BASE_GITLEAKS_CONFIG)
        self.assertEqual(
            record["ignore_files"], {".gitleaksignore": BASE_GITLEAKS_IGNORE}
        )
        self.assert_no_leftover_temporary_files()

    def test_default_rules_and_empty_ignore_without_base_files(self) -> None:
        repository = self.create_text_only_branch()
        self.assertEqual(self.range_status(repository), 0)
        record = self.single_gitleaks_record()
        self.assertEqual(record["config"], DEFAULT_GITLEAKS_CONFIG)
        self.assertEqual(record["ignore_files"], {})

    def test_untracked_config_and_ignore_are_not_passed(self) -> None:
        """working tree に未追跡の `.gitleaks.toml` / `.gitleaksignore` を置いても、
        gitleaks に渡すのは base の tree の内容 (ここでは既定のルールと空の ignore)。"""
        repository = self.create_text_only_branch()
        repository.write(".gitleaks.toml", UNTRACKED_GITLEAKS_CONFIG)
        repository.write(".gitleaksignore", UNTRACKED_GITLEAKS_IGNORE)
        self.assertEqual(self.range_status(repository), 0)
        record = self.single_gitleaks_record()
        self.assertEqual(record["config"], DEFAULT_GITLEAKS_CONFIG)
        self.assertEqual(record["ignore_files"], {})
        self.assertNotIn(str(repository.path), record["args"])
        self.assertNotIn(str(repository.path / ".gitleaks.toml"), record["args"])

    def test_user_config_environment_is_respected(self) -> None:
        """`GITLEAKS_CONFIG` / `GITLEAKS_CONFIG_TOML` が空でなければ `--config` を渡さない
        (ignore の一時ディレクトリは渡す)。空文字なら未設定と同じに扱う。"""
        cases = {
            "GITLEAKS_CONFIG": ("/path/to/user/gitleaks.toml", False),
            "GITLEAKS_CONFIG_TOML": ("[extend]\nuseDefault = true\n", False),
            "empty_GITLEAKS_CONFIG": ("", True),
        }
        for label, (value, expects_config) in cases.items():
            with self.subTest(case=label):
                repository = self.create_repository(f"repo-{label}")
                repository.write("README.md", "# updated\n")
                repository.commit_all("docs")
                name = label.removeprefix("empty_")
                self.gitleaks_log.unlink(missing_ok=True)
                self.env[name] = value
                try:
                    self.assertEqual(self.range_status(repository), 0)
                finally:
                    del self.env[name]
                args = self.single_gitleaks_record()["args"]
                self.assertEqual("--config" in args, expects_config, args)
                self.assertIn("--gitleaks-ignore-path", args)

    def test_gitleaks_stderr_output_with_exit_zero_stops_the_skip(self) -> None:
        """gitleaks が終了コード 0 でも stderr に何か出していれば (内部の git の失敗等)、
        2 (レビュー必須) を返す。stderr の内容は出力しない。"""
        repository = self.create_text_only_branch()
        self.env[GITLEAKS_STDERR_ENV] = "1"
        result = subprocess.run(
            [
                BASH,
                "-c",
                CALL_FUNCTION_SCRIPT,
                "text-paths-test",
                str(TEXT_PATHS_LIB),
                "is_text_only_range_in",
                str(repository.path),
                "master",
                "HEAD",
            ],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(GITLEAKS_OUTPUT_MARKER, result.stdout + result.stderr)
        self.assert_no_leftover_temporary_files()

    def test_temporary_files_are_removed_even_when_gitleaks_fails(self) -> None:
        repository = self.create_text_only_branch()
        self.set_gitleaks_exit(1)
        self.assertEqual(self.range_status(repository), 2)
        self.assert_no_leftover_temporary_files()

    def test_failure_to_prepare_temporary_files_requires_review(self) -> None:
        """一時ディレクトリを作れない場合は 2 (レビュー必須) を返し、gitleaks を起動しない。"""
        repository = self.create_text_only_branch()
        failing_mktemp = self.fake_gitleaks_dir / "mktemp"
        failing_mktemp.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        failing_mktemp.chmod(failing_mktemp.stat().st_mode | stat.S_IXUSR)
        self.assertEqual(self.range_status(repository), 2)
        self.assertEqual(self.gitleaks_calls(), [])

    def test_gitleaks_findings_or_failures_stop_the_skip(self) -> None:
        for code in (1, 2, 126):
            with self.subTest(exit_code=code):
                repository = self.create_repository(f"repo-{code}")
                repository.git(
                    "update-ref", "refs/remotes/origin/master", repository.rev("master")
                )
                repository.write("README.md", "# updated\n")
                repository.commit_all("docs")
                self.set_gitleaks_exit(code)
                self.assertEqual(self.range_status(repository), 2)
                self.assertEqual(self.push_status(repository), 2)

    def test_gitleaks_is_not_run_for_ranges_that_are_not_text_only(self) -> None:
        repository = self.create_repository()
        repository.write("tool.py", "print('changed')\n")
        repository.commit_all("code")
        self.set_gitleaks_exit(1)
        self.assertEqual(self.range_status(repository), 1)
        self.assertEqual(self.gitleaks_calls(), [])

    def test_text_only_range_is_skipped_without_gitleaks_on_path(self) -> None:
        repository = self.create_text_only_branch()
        self.remove_gitleaks_from_path()
        self.assertEqual(self.range_status(repository), 0)
        self.assertEqual(self.push_status(repository), 0)
        self.assertEqual(self.gitleaks_calls(), [])


# 実際の gitleaks の既定のルール (github-pat) に一致するダミー値。実在の token ではない。この
# ファイル自体が secret scanner に検出されないよう、値は実行時に組み立てる (連番のような
# 規則的な値は gitleaks が検出しないため、固定の文字列の sha256 から作る)。
DUMMY_GITHUB_PAT = "ghp_" + hashlib.sha256(b"text-only-skip-test").hexdigest()[:36]


@unittest.skipUnless(REAL_GITLEAKS, "requires gitleaks on PATH")
class TextOnlySecretsScanWithRealGitleaksTest(WorkspaceTestCase):
    """実際の gitleaks で、ローカルの未追跡ファイルや git の設定が検出を抑止しないこと。

    fake gitleaks は受け取った引数しか記録できないため、gitleaks が引数以外から読む入力
    (走査元の `.gitleaksignore`) と、`git log -p` の出力の変化は実物で確かめる。抑止の手段を
    使う各テストは、その手段が素朴な起動 (repo を走査元にし、`--log-opts` に範囲だけを渡す)
    では実際に検出を抑止することを先に確かめ、抑止されない gitleaks の版では skip する。
    """

    def setUp(self) -> None:
        super().setUp()
        assert REAL_GITLEAKS is not None
        fake = self.fake_gitleaks_dir / "gitleaks"
        fake.unlink()
        fake.symlink_to(REAL_GITLEAKS)

    def create_branch_with_secret(self, name: str = "repo") -> Repository:
        """feature branch の commit で README.md の 3 行目にダミーの token を書き込む。"""
        repository = self.create_repository(name)
        repository.write("README.md", f"# docs\n\nexample = {DUMMY_GITHUB_PAT}\n")
        repository.commit_all("docs")
        return repository

    def naive_scan_detects(self, repository: Repository) -> bool:
        result = subprocess.run(
            [
                "gitleaks",
                "git",
                "--no-banner",
                "--redact",
                "--log-level",
                "error",
                "--exit-code",
                "1",
                "--timeout",
                "30",
                f"--log-opts={repository.rev('master')}..{repository.head()}",
                str(repository.path),
            ],
            cwd=repository.path,
            env=self.env,
            capture_output=True,
            check=False,
            timeout=60,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode == 1

    def skip_unless_naive_scan_is_suppressed(self, repository: Repository) -> None:
        if self.naive_scan_detects(repository):
            self.skipTest("この gitleaks では素朴な起動でも検出が抑止されない")

    def test_clean_text_only_range_passes(self) -> None:
        repository = self.create_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        self.assertEqual(self.range_status(repository), 0)

    def test_failed_internal_git_log_stops_the_skip(self) -> None:
        """範囲内の blob が欠けて gitleaks の内部の `git log` が失敗した場合は 2 を返す
        (gitleaks 自身はエラーを log に出して終了コード 0 で終わることがある)。"""
        repository = self.create_repository()
        repository.write("README.md", "# updated\n")
        head = repository.commit_all("docs")
        blob = repository.rev(f"{head}:README.md")
        loose_object = repository.git_dir() / "objects" / blob[:2] / blob[2:]
        loose_object.chmod(loose_object.stat().st_mode | stat.S_IWUSR)
        loose_object.unlink()
        self.assertEqual(self.range_status(repository), 2)

    def test_secret_in_text_only_range_stops_the_skip(self) -> None:
        repository = self.create_branch_with_secret()
        self.assertTrue(self.naive_scan_detects(repository))
        self.assertEqual(self.range_status(repository), 2)

    def test_untracked_gitleaksignore_does_not_suppress_detection(self) -> None:
        repository = self.create_branch_with_secret()
        repository.write(
            ".gitleaksignore", f"{repository.head()}:README.md:github-pat:3\n"
        )
        self.skip_unless_naive_scan_is_suppressed(repository)
        self.assertEqual(self.range_status(repository), 2)

    def test_local_git_attributes_and_config_do_not_hide_the_diff(self) -> None:
        cases: dict[str, tuple[str | None, tuple[tuple[str, str], ...]]] = {
            "binary": ("*.md binary\n", ()),
            "textconv": ("*.md diff=hide\n", (("diff.hide.textconv", "true"),)),
            "color": (None, (("color.ui", "always"),)),
        }
        for label, (attributes, configs) in cases.items():
            with self.subTest(case=label):
                repository = self.create_branch_with_secret(f"repo-{label}")
                if attributes is not None:
                    info = repository.git_dir() / "info"
                    info.mkdir(exist_ok=True)
                    (info / "attributes").write_text(attributes, encoding="utf-8")
                for key, value in configs:
                    repository.git("config", key, value)
                self.skip_unless_naive_scan_is_suppressed(repository)
                self.assertEqual(self.range_status(repository), 2)

    def test_diff_prefix_settings_do_not_move_the_secret_into_an_allowlisted_path(
        self,
    ) -> None:
        """`diff.noprefix` / `diff.dstPrefix` で gitleaks が報告する path がずれても、base の
        allowlist (`^vendor/`) に当たって検出から外れない。"""
        cases = {
            "noprefix": ("diff.noprefix", "true", "docs/vendor/notes.md"),
            "dstPrefix": ("diff.dstPrefix", "b/vendor/", "notes.md"),
        }
        for label, (key, value, document) in cases.items():
            with self.subTest(case=label):
                repository = self.create_repository(f"repo-{label}")
                repository.git("switch", "-q", "master")
                repository.write(
                    ".gitleaks.toml",
                    "[extend]\nuseDefault = true\n\n"
                    "[allowlist]\npaths = ['''^vendor/''']\n",
                )
                repository.commit_all("gitleaks config")
                repository.git("switch", "-q", "feature")
                repository.git("reset", "-q", "--hard", "master")
                repository.write(document, f"example = {DUMMY_GITHUB_PAT}\n")
                repository.commit_all("docs")
                repository.git("config", key, value)
                self.skip_unless_naive_scan_is_suppressed(repository)
                self.assertEqual(self.range_status(repository), 2)

    def test_replace_ref_does_not_hide_the_secret(self) -> None:
        """token を書き込む commit を、replace ref で token の無い commit に置き換えても検出する。"""
        repository = self.create_repository()
        repository.write("README.md", "# replacement\n")
        repository.git("add", "README.md")
        replacement = repository.git(
            "commit-tree",
            repository.git("write-tree").strip(),
            "-p",
            repository.rev("master"),
            "-m",
            "replacement",
        ).strip()
        repository.write("README.md", f"# docs\n\nexample = {DUMMY_GITHUB_PAT}\n")
        real = repository.commit_all("docs")
        repository.git("replace", real, replacement)
        self.skip_unless_naive_scan_is_suppressed(repository)
        self.assertEqual(self.range_status(repository), 2)


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
        # 通すのは gitleaks (fake、終了コード 0) の検査を経た後である。
        self.assertEqual(len(self.gitleaks_calls()), len(self.GATES))

    def create_text_only_push(self) -> Repository:
        repository = self.create_pushable_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        return repository

    def test_gitleaks_finding_or_failure_requires_markers(self) -> None:
        """gitleaks が検出・失敗したら skip せず、deny の理由文にその旨の 1 文を加える。
        gitleaks の出力は hook の出力に出さない。"""
        repository = self.create_text_only_push()
        for code in (1, 3):
            self.set_gitleaks_exit(code)
            for name, gate in self.GATES.items():
                with self.subTest(gate=name, exit_code=code):
                    result = self.run_gate(gate, repository)
                    self.assert_marker_deny(result)
                    reason = json.loads(result.stdout)["hookSpecificOutput"][
                        "permissionDecisionReason"
                    ]
                    self.assertIn(GITLEAKS_DENY_SENTENCE, reason)
                    self.assertNotIn(
                        GITLEAKS_OUTPUT_MARKER.encode(), result.stdout + result.stderr
                    )

    def test_code_push_deny_does_not_mention_gitleaks(self) -> None:
        repository = self.create_pushable_repository()
        repository.write("tool.py", "print('changed')\n")
        repository.commit_all("code")
        self.set_gitleaks_exit(1)
        for name, gate in self.GATES.items():
            with self.subTest(gate=name):
                result = self.run_gate(gate, repository)
                self.assert_marker_deny(result)
                self.assertNotIn("gitleaks", result.stdout.decode())

    def test_text_only_push_passes_without_gitleaks_on_path(self) -> None:
        repository = self.create_text_only_push()
        self.remove_gitleaks_from_path()
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
        # 通すのは gitleaks (fake、終了コード 0) の検査を経た後である。
        self.assertEqual(len(self.gitleaks_calls()), 1)

    def create_text_only_pr(self) -> Repository:
        repository = self.create_pr_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        self.configure_pr(repository.head(), repository.rev("master"))
        return repository

    def test_gitleaks_finding_or_failure_falls_back_to_record(self) -> None:
        """gitleaks が検出・失敗したら記録を照合し、deny の理由文にその旨の 1 文を加える。
        gitleaks の出力は hook の出力に出さない。"""
        for code in (1, 3):
            with self.subTest(exit_code=code):
                repository = self.create_pr_repository(f"repo-{code}")
                repository.write("README.md", "# updated\n")
                repository.commit_all("docs")
                head = repository.head()
                self.configure_pr(head, repository.rev("master"))
                self.set_gitleaks_exit(code)
                result = self.run_gate(repository)
                self.assert_record_deny(result)
                reason = json.loads(result.stdout)["hookSpecificOutput"][
                    "permissionDecisionReason"
                ]
                self.assertIn(GITLEAKS_DENY_SENTENCE, reason)
                self.assertNotIn(
                    GITLEAKS_OUTPUT_MARKER.encode(), result.stdout + result.stderr
                )
                self.write_record(repository, head)
                self.assertEqual(self.run_gate(repository).stdout, b"")

    def test_text_only_pr_passes_without_gitleaks_on_path(self) -> None:
        repository = self.create_text_only_pr()
        self.remove_gitleaks_from_path()
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

    def assert_skip_only_output(self, result: subprocess.CompletedProcess[str]) -> None:
        """skip の経路の出力: stdout は skip の 1 行だけ、stderr は terminal sentinel の
        案内行だけで、両者が混ざって届いても skip の 1 行以外は sentinel の行と空行だけ。"""
        self.assertEqual(wrapper_body_lines(result.stdout), [SKIP_LINE], result.stdout)
        self.assertEqual(wrapper_body_lines(result.stderr), [], result.stderr)
        self.assertEqual(
            wrapper_body_lines(result.stdout + result.stderr),
            [SKIP_LINE],
            result.stdout + result.stderr,
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
        self.assert_skip_only_output(result)
        self.assertFalse(self.tripwire.exists(), "codex companion が起動された")
        self.assertFalse(pending.exists(), "pending attestation が残っている")
        self.assertEqual(len(self.gitleaks_calls()), 1)

    def test_push_wrapper_reviews_text_only_branch_when_gitleaks_stops_it(
        self,
    ) -> None:
        repository = self.create_push_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        self.set_gitleaks_exit(1)

        result = self.run_wrapper(PUSH_WRAPPER, repository)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(SKIP_LINE, result.stdout)
        self.assertTrue(self.tripwire.exists(), result.stderr)
        self.assertTrue(
            (repository.git_dir() / PUSH_CODEX_PENDING).exists(), result.stderr
        )
        self.assertNotIn(GITLEAKS_OUTPUT_MARKER, result.stdout + result.stderr)

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
        self.assert_skip_only_output(result)
        self.assertFalse(self.tripwire.exists(), "codex companion が起動された")
        self.assertFalse(pending.exists(), "pending attestation が残っている")
        self.assertFalse(final.exists(), "起動前の final attestation が残っている")
        self.assertEqual(len(self.gitleaks_calls()), 1)

    @unittest.skipUnless(JQ, "merge wrapper requires jq")
    def test_merge_wrapper_skip_after_fetching_the_base_writes_nothing_to_stderr(
        self,
    ) -> None:
        """PR の base commit がローカルに無く fetch した場合も、skip の経路では fetch の
        案内と出力を stderr に出さない。"""
        repository = self.create_merge_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        # 別の clone から origin の master を進め、ローカルには無い base commit を作る。
        other = Repository(self.root / "other", self.env)
        Repository(self.root, self.env).git(
            "clone", "-q", str(self.root / "repo-origin.git"), str(other.path)
        )
        other.write("docs/base-update.md", "base update\n")
        advanced_base = other.commit_all("advance base")
        other.git("push", "-q", "origin", "HEAD:master")
        assert GIT is not None
        lookup = subprocess.run(
            [GIT, "cat-file", "-e", f"{advanced_base}^{{commit}}"],
            cwd=repository.path,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(lookup.returncode, 0, "base commit がローカルに既にある")
        self.configure_fake_gh(
            {
                "number": PR_NUMBER,
                "headRefOid": repository.head(),
                "baseRefName": "master",
                "baseRefOid": advanced_base,
            }
        )

        result = self.run_wrapper(MERGE_WRAPPER, repository)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(repository.rev("refs/remotes/origin/master"), advanced_base)
        self.assert_skip_only_output(result)
        self.assertFalse(self.tripwire.exists(), "codex companion が起動された")

    @unittest.skipUnless(JQ, "merge wrapper requires jq")
    def test_merge_wrapper_reviews_text_only_pr_when_gitleaks_stops_it(self) -> None:
        repository = self.create_merge_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        self.configure_merge_pr(repository)
        self.set_gitleaks_exit(1)

        result = self.run_wrapper(MERGE_WRAPPER, repository)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(SKIP_LINE, result.stdout)
        self.assertTrue(self.tripwire.exists(), result.stderr)
        self.assertTrue(
            (repository.git_dir() / MERGE_PENDING_RECORD).exists(), result.stderr
        )
        self.assertNotIn(GITLEAKS_OUTPUT_MARKER, result.stdout + result.stderr)

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

    def test_skip_condition_holds_on_interleaved_stdout_and_stderr(self) -> None:
        """skip の条件は stdout と stderr の分離に依存しない (出力全体に skip の行が
        ちょうど 1 行あり、他は sentinel の行と空行だけ)。"""
        for name, path in self.AGENTS.items():
            with self.subTest(agent=name):
                body = " ".join(path.read_text(encoding="utf-8").split())
                self.assertIn(
                    f"its output contains exactly one `{SKIP_LINE}` line, and every "
                    "other line is a `terminal sentinel:` announcement line, the "
                    "`terminal sentinel end` line or a blank line",
                    body,
                )
                self.assertIn(
                    "when stdout and stderr arrive interleaved in one stream, check "
                    "that combined stream",
                    body,
                )
                self.assertNotIn("its stdout, apart from", body)

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
        """既知の制約に、列挙に無い指示ファイル・間接的に読み込まれる文書・実行される文書を
        書く。"""
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
                self.assertIn(
                    "実行される文書もテキスト系文書と判定される", constraints
                )
                self.assertIn("doctest や文書のビルド", constraints)

    def test_readmes_say_text_only_skip_does_not_scan_for_secrets(self) -> None:
        """skip ではレビューを行わないため認証情報も検査されず、secret scanner の併用を
        推奨する。"""
        for name, path in {**PLUGIN_READMES, "root": ROOT / "README.md"}.items():
            with self.subTest(readme=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn("PATH に gitleaks がある環境では", body)
                self.assertIn("文書に書き込まれた token などの認証情報", body)
                self.assertIn(
                    "gitleaks 等の secret scanner を pre-commit / pre-push hook として併用",
                    body,
                )

    def test_plugin_readmes_describe_the_gitleaks_scan(self) -> None:
        """gitleaks の検査で skip を止める条件、`--ignore-gitleaks-allow` の理由、repo の
        設定ファイルの扱いを書く。"""
        for name, path in PLUGIN_READMES.items():
            with self.subTest(plugin=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn(
                    "秘匿情報の候補を検出した場合や gitleaks の実行に失敗した場合", body
                )
                self.assertIn("`--ignore-gitleaks-allow` を付けるのは", body)
                self.assertIn("`gitleaks:allow` コメント", body)
                self.assertIn("`.gitleaks.toml` / `.gitleaksignore`", body)
                self.assertIn(
                    "working tree ではなく範囲の起点 (base の commit) の tree から読みます",
                    body,
                )
                self.assertIn(
                    "`git log` に `--text --no-textconv --no-color` を付け", body
                )
                self.assertIn("`GITLEAKS_CONFIG` / `GITLEAKS_CONFIG_TOML`", body)
                self.assertNotIn("gitleaks の既定どおり読まれます", body)
                self.assertIn("deny の理由文にその旨を 1 文加えます", body)

    def test_plugin_readmes_list_the_commit_limit(self) -> None:
        for name, path in PLUGIN_READMES.items():
            with self.subTest(plugin=name):
                self.assertIn(
                    "範囲の commit が 100 を超える", path.read_text(encoding="utf-8")
                )

    def test_plugin_readmes_exclude_ascii_folding_characters(self) -> None:
        for name, path in PLUGIN_READMES.items():
            with self.subTest(plugin=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn(
                    "大文字小文字の変換で ASCII の文字になる非 ASCII 文字", body
                )
                self.assertIn("HFS+ が名前の比較で無視する不可視の文字", body)

    def test_plugin_readmes_state_the_time_limits(self) -> None:
        for name, path in PLUGIN_READMES.items():
            with self.subTest(plugin=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn("判定に 10 秒を超えてかかる", body)
                self.assertIn("gitleaks の実行は 20 秒で打ち切り", body)

    def test_plugin_readmes_state_that_object_tampering_is_not_detected(self) -> None:
        for name, path in PLUGIN_READMES.items():
            with self.subTest(plugin=name):
                body = path.read_text(encoding="utf-8")
                constraints = body[body.index("## 既知の制約") :]
                self.assertIn("git dir の object の改ざんは検出しない", constraints)

    def test_plugin_readmes_list_the_graft_condition(self) -> None:
        for name, path in PLUGIN_READMES.items():
            with self.subTest(plugin=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn("git の graft ファイル", body)
                self.assertIn("`GIT_GRAFT_FILE`", body)

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
