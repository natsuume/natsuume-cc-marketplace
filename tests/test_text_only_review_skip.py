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
import re
import secrets
import shlex
import shutil
import stat
import string
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


def git_version() -> tuple[int, ...]:
    """PATH の git の版 (`git --version` の先頭の 3 つの数字。3 つ目が無ければ 0)。
    取れなければ空の tuple。"""
    if GIT is None:
        return ()
    output = subprocess.run(
        [GIT, "--version"], capture_output=True, text=True, check=False
    ).stdout
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", output)
    if not match:
        return ()
    return tuple(int(part) if part is not None else 0 for part in match.groups())


GIT_VERSION = git_version()

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
    "GIT_NO_LAZY_FETCH",
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


# fake gitleaks は、起動ごとに、受け取った引数・標準入力の内容・cwd とその中身・起動時点の
# `--config` と `--report-template` の内容・`--gitleaks-ignore-path` の中身・git に関する環境
# 変数 (`GIT_DIR` / `GIT_WORK_TREE` / `GIT_NO_REPLACE_OBJECTS` / `GIT_CONFIG_COUNT`) を記録する。
# 結果は環境変数 (GITLEAKS_RESULT_ENV) で切り替える:
#
#   - `canary` (既定): canary だけを検出した実物の gitleaks と同じく振る舞う。標準入力の 1 行目が
#     canary の形 (`ghp_` + 英数字 36 文字) なら `--report-path` に `github-pat 1\n` を書いて
#     `--exit-code` の値で終わり、そうでなければ空の report を書いて終了コード 0 で終わる
#   - `finding`: canary に加えて別の候補も検出した (report に 5 行目の候補の行も書き、
#     `--exit-code` の値で終わる)
#   - `no-canary`: canary も検出しなかった (空の report を書き、終了コード 0 で終わる)
#   - `canary-with-stderr`: `canary` と同じ結果で、stderr にも出力する
#   - `exit:<n>`: report を書かず、stderr に出力して終了コード n で終わる (実行エラー)
#
# CI には実物の gitleaks が無いため、検査する内容が標準入力に届いていること (`/dev/null` に
# なっていないこと) はこの記録で確かめる。stdout と stderr に目印の文字列を出し、hook や
# wrapper の出力へ流れないことの確認に使う。
GITLEAKS_LOG_ENV = "TEXT_ONLY_SKIP_TEST_GITLEAKS_LOG"
GITLEAKS_RESULT_ENV = "TEXT_ONLY_SKIP_TEST_GITLEAKS_RESULT"
GITLEAKS_OUTPUT_MARKER = "FAKE-GITLEAKS-FINDING ghp_textonlyskiptestsecret0000000000"
FAKE_GITLEAKS_TEMPLATE = """#!{python}
import json
import os
import re
import sys

args = sys.argv[1:]
stdin_text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
cwd = os.getcwd()


def option_value(name):
    if name in args and args.index(name) + 1 < len(args):
        return args[args.index(name) + 1]
    return None


def read_option_file(name):
    path = option_value(name)
    if path is None:
        return None
    # 改行を変換せずに読む (template はバイト単位で比べる)。
    with open(path, encoding="utf-8", newline="") as fp:
        return fp.read()


config = read_option_file("--config")
report_template = read_option_file("--report-template")
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
record = {{
    "args": args,
    "stdin": stdin_text,
    "cwd": cwd,
    "cwd_entries": sorted(os.listdir(cwd)),
    "config": config,
    "report_template": report_template,
    "ignore_files": ignore_files,
    "git_env": git_env,
}}
with open(os.environ["{log_env}"], "a", encoding="utf-8") as fp:
    fp.write(json.dumps(record) + "\\n")

result = os.environ.get("{result_env}") or "canary"
exit_code_on_findings = int(option_value("--exit-code") or "1")
report_path = option_value("--report-path")
canary_found = re.match(r"ghp_[A-Za-z0-9]{{36}}\\n", stdin_text) is not None


def write_report(content):
    if report_path is not None:
        with open(report_path, "w", encoding="utf-8") as fp:
            fp.write(content)


print("{marker}")
# 実際の gitleaks は `--log-level error` の正常な走査では stderr に何も出さない。stderr に
# 出すのは、実行エラーを模すときと、stderr への出力を指定したときだけにする。
if result.startswith("exit:") or result == "canary-with-stderr":
    print("{marker}", file=sys.stderr)
if result.startswith("exit:"):
    sys.exit(int(result[len("exit:"):]))
if result == "finding":
    write_report("github-pat 1\\ngithub-pat 5\\n")
    sys.exit(exit_code_on_findings)
if result == "no-canary" or not canary_found:
    write_report("")
    sys.exit(0)
write_report("github-pat 1\\n")
sys.exit(exit_code_on_findings)
"""
# 判定 lib が gitleaks に渡す入力を書き出す `git log` の引数 (lib の git の共通の option と
# `-C <target_cwd>` に続く部分)。範囲 (`<base の OID>..<head の OID>`) はこの後に付く。
SCAN_INPUT_GIT_LOG_ARGS = (
    "-c",
    "diff.relative=false",
    "log",
    "-p",
    "-U0",
    "--no-textconv",
    "--no-ext-diff",
    "--text",
    "--no-color",
    "--no-show-signature",
    "--encoding=UTF-8",
    "--format=commit %H%n%n%B",
)
# 判定 lib が gitleaks の入力の 1 行目に置く canary の形 (gitleaks の既定の `github-pat` rule に
# 当たる偽 token)。canary の行は改行を含めて 41 バイト。
CANARY_PATTERN = re.compile(r"ghp_[A-Za-z0-9]{36}")
CANARY_LINE_LENGTH = len("ghp_") + 36 + 1
# gitleaks に `--report-template` で渡す template と、canary だけを検出したときの report。
GITLEAKS_REPORT_TEMPLATE = "{{ range . }}{{ .RuleID }} {{ .StartLine }}\n{{ end }}"
CANARY_ONLY_REPORT = "github-pat 1\n"
# base の tree に `.gitleaks.toml` が無いときに渡す設定 (gitleaks の既定のルールだけを使う)。
DEFAULT_GITLEAKS_CONFIG = "[extend]\nuseDefault = true\n"
# base の tree に置く設定・ignore (設定だけを渡し、ignore は渡さない) と、working tree に置く
# (どちらも渡されてはならない) 設定・ignore。
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
        # gitleaks (canary だけを検出する = 秘匿情報の候補なし) を置く。
        self.fake_gitleaks_dir = self.root / "fake-gitleaks-bin"
        self.fake_gitleaks_dir.mkdir()
        fake_gitleaks = self.fake_gitleaks_dir / "gitleaks"
        fake_gitleaks.write_text(
            FAKE_GITLEAKS_TEMPLATE.format(
                python=sys.executable,
                log_env=GITLEAKS_LOG_ENV,
                result_env=GITLEAKS_RESULT_ENV,
                marker=GITLEAKS_OUTPUT_MARKER,
            ),
            encoding="utf-8",
        )
        fake_gitleaks.chmod(fake_gitleaks.stat().st_mode | stat.S_IXUSR)
        self.gitleaks_log = self.root / "gitleaks-calls.jsonl"
        env[GITLEAKS_LOG_ENV] = str(self.gitleaks_log)
        env["PATH"] = f"{self.fake_gitleaks_dir}{os.pathsep}{env.get('PATH', '')}"
        self.env = env

    # ------------------------------------------------------------------
    # gitleaks
    # ------------------------------------------------------------------

    def set_gitleaks_result(self, result: str) -> None:
        """fake gitleaks の結果 (`canary` / `finding` / `no-canary` /
        `canary-with-stderr` / `exit:<n>`) を切り替える。"""
        self.env[GITLEAKS_RESULT_ENV] = result

    def gitleaks_records(self) -> list[dict[str, Any]]:
        """fake gitleaks の起動ごとの記録 (args / stdin / cwd / cwd_entries / config /
        report_template / ignore_files / git_env)。"""
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

    def commit_with_encoding_header(
        self, repository: Repository, message: str, encoding: str | None
    ) -> str:
        """index の tree と HEAD を親にした commit を、`encoding` header を encoding にして作り、
        今の branch を進める (encoding が None なら header を付けない)。git の commit 系の
        コマンドは UTF-8 の header を書かないため、commit の object を直接書く。message の
        バイト列はそのまま (変換せずに) 書く。"""
        assert GIT is not None
        tree = repository.git("write-tree").strip()
        parent = repository.head()
        identity = "Text Only Skip Test <text-only-skip@example.invalid> 1700000000 +0000"
        lines = [
            f"tree {tree}",
            f"parent {parent}",
            f"author {identity}",
            f"committer {identity}",
        ]
        if encoding is not None:
            lines.append(f"encoding {encoding}")
        content = "\n".join(lines) + f"\n\n{message}\n"
        result = subprocess.run(
            [GIT, "hash-object", "-t", "commit", "-w", "--stdin"],
            cwd=repository.path,
            env=self.env,
            input=content.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        commit = result.stdout.decode("ascii").strip()
        branch = repository.git("symbolic-ref", "HEAD").strip()
        repository.git("update-ref", branch, commit)
        return commit

    def scan_input_git_log(self, repository: Repository, base: str, head: str) -> bytes:
        """判定 lib が gitleaks の入力 (canary の行の後ろ) に書き出す `git log` の出力。"""
        return repository.run_git(
            "--no-replace-objects",
            "-c",
            "core.commitGraph=false",
            *SCAN_INPUT_GIT_LOG_ARGS,
            f"{base}..{head}",
        )

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
        # 範囲の判定と、base の tree からの gitleaks の設定の取り出しと、gitleaks に渡す入力の
        # 書き出し (`git log`) を含む。
        self.assertTrue(any(" diff-tree " in f" {call} " for call in calls), calls)
        self.assertTrue(any(" cat-file " in f" {call} " for call in calls), calls)
        self.assertTrue(any(" log " in f" {call} " for call in calls), calls)
        for call in calls:
            with self.subTest(call=call):
                self.assertTrue(
                    call.startswith("--no-replace-objects -c core.commitGraph=false "),
                    call,
                )

    def test_commit_count_stops_after_the_limit(self) -> None:
        """条件 1 の commit 数は上限 + 1 で数えるのをやめる (上限を超える範囲を全部数えない)。"""
        repository = self.create_branch_with_text_commits(3)
        git_log = self.install_logging_git_shim()
        self.assertEqual(self.range_status(repository), 0)
        count_calls = [
            call
            for call in git_log.read_text(encoding="utf-8").splitlines()
            if " rev-list " in f" {call} "
            and " --count " in f" {call} "
            and "--min-parents" not in call
        ]
        self.assertEqual(len(count_calls), 1, count_calls)
        self.assertIn(f" --max-count={self.MAX_RANGE_COMMITS + 1} ", f" {count_calls[0]} ")

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


@unittest.skipUnless(
    GIT_VERSION >= (2, 45, 1), "requires git 2.45.1 or later (GIT_NO_LAZY_FETCH)"
)
class TextOnlyPartialCloneTest(WorkspaceTestCase):
    """partial clone で手元に無い object を、判定のために remote から取りに行かない。

    remote の upload-pack を遅らせ、取りに行けば時間の上限 (10 秒) を超える状態にする。
    """

    UPLOAD_PACK_DELAY_SECONDS = 15

    def create_partial_clone(self) -> Repository:
        """feature branch に文書の commit を 2 つ持つ repository を `--filter=tree:0` で clone
        し、clone の後で origin の upload-pack を遅らせる。"""
        assert GIT is not None
        source = self.create_repository("source")
        source.write("README.md", "# updated\n")
        source.commit_all("docs")
        source.write("docs/new.md", "new\n")
        source.commit_all("more docs")
        source.git("config", "uploadpack.allowFilter", "true")
        source.git("config", "uploadpack.allowAnySHA1InWant", "true")
        clone = Repository(self.root / "clone", self.env)
        Repository(self.root, self.env).git(
            "clone",
            "-q",
            "--filter=tree:0",
            "--branch",
            "feature",
            f"file://{source.path}",
            str(clone.path),
        )
        # merge-base の tree が手元に無いこと。確かめる git 自体が取りに行かないよう
        # GIT_NO_LAZY_FETCH を付け、tree の OID は commit の object から読む (`<commit>^{tree}`
        # は tree の object を読もうとする)。
        no_fetch = Repository(clone.path, {**self.env, "GIT_NO_LAZY_FETCH": "1"})
        merge_base = no_fetch.merge_base("origin/master")
        tree_line = no_fetch.git("cat-file", "commit", merge_base).splitlines()[0]
        self.assertTrue(tree_line.startswith("tree "), tree_line)
        lookup = subprocess.run(
            [GIT, "cat-file", "-e", tree_line[len("tree ") :]],
            cwd=clone.path,
            env=no_fetch.env,
            capture_output=True,
            check=False,
            timeout=60,
        )
        self.assertNotEqual(lookup.returncode, 0, "merge-base の tree が手元にある")
        slow_upload_pack = self.root / "slow-upload-pack"
        slow_upload_pack.write_text(
            "#!/bin/sh\n"
            f"sleep {self.UPLOAD_PACK_DELAY_SECONDS}\n"
            f'exec {shlex.quote(GIT)} upload-pack "$@"\n',
            encoding="utf-8",
        )
        slow_upload_pack.chmod(slow_upload_pack.stat().st_mode | stat.S_IXUSR)
        clone.git("config", "remote.origin.uploadpack", str(slow_upload_pack))
        return clone

    def test_missing_objects_are_not_fetched_during_the_check(self) -> None:
        clone = self.create_partial_clone()
        for label, call in {
            "range": ("is_text_only_range_in", str(clone.path), "origin/master", "HEAD"),
            "push": ("is_text_only_push_in", str(clone.path), "master"),
        }.items():
            with self.subTest(function=label):
                started = time.monotonic()
                status = self.call_text_paths(*call)
                elapsed = time.monotonic() - started
                self.assertEqual(status, 1)
                self.assertLess(elapsed, 10)


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

    # 短い名前 `origin/master` の解決で、remote-tracking ref より先に選ばれる ref。
    SHADOW_REFS = {
        "branch": "refs/heads/origin/master",
        "tag": "refs/tags/origin/master",
        "refs_root": "refs/origin/master",
    }

    def test_refs_shadowing_the_remote_base_do_not_hide_code_commits(self) -> None:
        """`origin/<base>` という名前の branch・tag・`refs/origin/<base>` があっても、base は
        remote-tracking ref (`refs/remotes/origin/<base>`) で解決する。"""
        for label, shadow in self.SHADOW_REFS.items():
            with self.subTest(shadow=label):
                repository = self.create_repository(f"repo-{label}")
                repository.git(
                    "update-ref", "refs/remotes/origin/master", repository.rev("master")
                )
                repository.write("tool.py", "print('changed')\n")
                code = repository.commit_all("code")
                repository.write("README.md", "# updated\n")
                repository.commit_all("docs")
                repository.git("update-ref", shadow, code)
                # 短い名前は shadow の ref に解決され、コードの commit が範囲から外れる。
                self.assertEqual(repository.rev("origin/master"), code)
                self.assertEqual(self.range_status(repository, base="origin/master"), 0)
                self.assertEqual(self.push_status(repository), 1)

    def test_shadow_ref_without_the_remote_base_is_not_predicted_text_only(
        self,
    ) -> None:
        """remote-tracking ref が無ければ、同名の別の ref があっても 1 を返す。"""
        for label, shadow in self.SHADOW_REFS.items():
            with self.subTest(shadow=label):
                repository = self.create_repository(f"repo-{label}")
                repository.write("README.md", "# updated\n")
                repository.commit_all("docs")
                repository.git("update-ref", shadow, repository.rev("master"))
                self.assertEqual(self.range_status(repository, base="origin/master"), 0)
                self.assertEqual(self.push_status(repository), 1)


class TextOnlySecretsScanTest(WorkspaceTestCase):
    """テキスト系文書だけの範囲は、PATH に gitleaks があれば skip の前に秘匿情報を検査する。

    戻り値は 0 = skip してよい / 1 = テキスト系文書だけではない / 2 = テキスト系文書だけだが
    gitleaks が候補を検出した、または検査を実行できなかった (`git log` の失敗・空の入力を
    含む)。
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

    def assert_no_gitleaksignore_is_read(self, record: dict[str, Any]) -> None:
        """`--gitleaks-ignore-path` と cwd が同じ空の一時ディレクトリであること (gitleaks は
        両方の `.gitleaksignore` を読むため)。"""
        args = record["args"]
        ignore_path = args[args.index("--gitleaks-ignore-path") + 1]
        self.assertTrue(
            ignore_path.startswith(f"{self.tmp}{os.sep}text-paths-gitleaks."),
            ignore_path,
        )
        self.assertEqual(record["ignore_files"], {})
        self.assertEqual(record["cwd"], ignore_path)
        self.assertEqual(record["cwd_entries"], [])

    def create_branch_for_scan_input(self) -> tuple[Repository, list[str]]:
        """範囲外 (master) と範囲内 (feature) の commit の message と行を区別できる repository と、
        範囲内の commit (古い順)。master の commit は docs/guide.md に文脈行を置く。"""
        repository = self.create_repository()
        repository.git("switch", "-q", "master")
        repository.write(
            "docs/guide.md", "context-line-before\noriginal-line\ncontext-line-after\n"
        )
        repository.commit_all("outside-range-subject\n\noutside-range-body")
        repository.git("switch", "-q", "feature")
        repository.git("reset", "-q", "--hard", "master")
        repository.write(
            "docs/guide.md", "context-line-before\nrange-added-line\ncontext-line-after\n"
        )
        first = repository.commit_all(
            "range-first-subject\n\nrange-first-body-line-1\nrange-first-body-line-2"
        )
        repository.write("docs/new.md", "range-new-file-line\n")
        second = repository.commit_all("range-second-subject")
        self.assertEqual(repository.range_commits("master"), [second, first])
        return repository, [first, second]

    def test_gitleaks_receives_the_resolved_range_and_fixed_options(self) -> None:
        repository, commits = self.create_branch_for_scan_input()
        self.assertEqual(self.range_status(repository, base="master"), 0)
        record = self.single_gitleaks_record()
        args = record["args"]
        # 一時ファイル・一時ディレクトリの path は実行ごとに変わるため、位置だけを確かめる。
        # ignore path は cwd を兼ねる空の一時ディレクトリで、repo ではない。設定・template・
        # report のファイルはその外に置く。
        config_path = args[args.index("--config") + 1]
        template_path = args[args.index("--report-template") + 1]
        report_path = args[args.index("--report-path") + 1]
        ignore_path = args[args.index("--gitleaks-ignore-path") + 1]
        for temporary in (config_path, template_path, report_path):
            with self.subTest(temporary=temporary):
                self.assertTrue(
                    temporary.startswith(f"{self.tmp}{os.sep}text-paths-gitleaks."),
                    temporary,
                )
                self.assertFalse(
                    temporary.startswith(f"{ignore_path}{os.sep}"), temporary
                )
        self.assertEqual(len({config_path, template_path, report_path}), 3)
        # `--timeout` は `git log` と共有する 20 秒の期限の残り秒数 (`SECONDS` が `git log` の
        # 間に進むと 19 になる)。
        timeout = args[args.index("--timeout") + 1]
        self.assertTrue(timeout.isdigit(), timeout)
        self.assertTrue(1 <= int(timeout) <= 20, timeout)
        self.assertEqual(
            args,
            [
                "stdin",
                "--no-banner",
                "--redact",
                "--ignore-gitleaks-allow",
                "--log-level",
                "error",
                "--exit-code",
                "3",
                "--timeout",
                timeout,
                "--report-format",
                "template",
                "--report-template",
                template_path,
                "--report-path",
                report_path,
                "--config",
                config_path,
                "--gitleaks-ignore-path",
                ignore_path,
            ],
        )
        self.assertEqual(record["report_template"], GITLEAKS_REPORT_TEMPLATE)
        self.assert_no_gitleaksignore_is_read(record)
        # gitleaks には repo を渡さない (git に関する環境変数も渡さない)。
        self.assertEqual(
            record["git_env"],
            {
                "GIT_DIR": None,
                "GIT_WORK_TREE": None,
                "GIT_NO_REPLACE_OBJECTS": None,
                "GIT_CONFIG_COUNT": None,
            },
        )
        # 標準入力の 1 行目は canary で、2 行目以降は lib と同じ option の `git log` の出力。
        canary, stdin = record["stdin"].split("\n", 1)
        self.assertIsNotNone(CANARY_PATTERN.fullmatch(canary), canary)
        self.assertEqual(
            stdin,
            self.scan_input_git_log(
                repository, repository.rev("master"), repository.head()
            ).decode("utf-8", errors="replace"),
        )
        # `git log` の出力は範囲内の commit の header・message (本文を含む)・追加行と削除行で、
        # 範囲外の commit と差分の文脈行を含まない。
        for expected in (
            f"commit {commits[0]}\n",
            f"commit {commits[1]}\n",
            "range-first-subject\n",
            "range-first-body-line-1\n",
            "range-first-body-line-2\n",
            "range-second-subject\n",
            "\n-original-line\n",
            "\n+range-added-line\n",
            "\n+range-new-file-line\n",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, stdin)
        # 文脈行は diff では先頭に空白を付けた行になる (`-U0` では出ない)。
        for unexpected in (
            f"commit {repository.rev('master')}",
            "outside-range-subject",
            "outside-range-body",
            "\n context-line-before\n",
            "\n context-line-after\n",
        ):
            with self.subTest(unexpected=unexpected):
                self.assertNotIn(unexpected, stdin)
        self.assert_no_leftover_temporary_files()

    def install_git_wrapper(self, log_command: str) -> None:
        """PATH の先頭に、`log` サブコマンドのときだけ log_command (sh の文。本物の git は
        `"$real_git"`、引数は `"$@"`) を実行し、それ以外は本物の git を実行する wrapper を置く。
        サブコマンドは、git の option (`-c` / `-C` の値を含む) を飛ばした最初の引数で見分ける。"""
        assert GIT is not None
        wrapper_dir = self.root / "git-wrapper-bin"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        wrapper.write_text(
            "#!/bin/sh\n"
            f"real_git={shlex.quote(GIT)}\n"
            "subcommand=\n"
            "skip_next=\n"
            'for argument in "$@"; do\n'
            '  if [ -n "$skip_next" ]; then skip_next=; continue; fi\n'
            '  case "$argument" in\n'
            "    -c | -C) skip_next=1 ;;\n"
            "    -*) ;;\n"
            '    *) subcommand="$argument"; break ;;\n'
            "  esac\n"
            "done\n"
            'if [ "$subcommand" = log ]; then\n'
            f"  {log_command}\n"
            "fi\n"
            'exec "$real_git" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        self.env["PATH"] = f"{wrapper_dir}{os.pathsep}{self.env['PATH']}"

    def test_scan_input_comes_from_git_log_with_fixed_options(self) -> None:
        """入力は、lib の git の共通の option と固定の option を付けた `git log` で書き出す。"""
        repository = self.create_text_only_branch()
        arguments_path = self.root / "git-log-arguments"
        self.install_git_wrapper(
            f"printf '%s\\0' \"$@\" > {shlex.quote(str(arguments_path))}"
        )
        self.assertEqual(self.range_status(repository), 0)
        arguments = arguments_path.read_bytes().decode("utf-8").split("\0")[:-1]
        self.assertEqual(
            arguments,
            [
                "--no-replace-objects",
                "-c",
                "core.commitGraph=false",
                "-C",
                str(repository.path),
                *SCAN_INPUT_GIT_LOG_ARGS,
                f"{repository.rev('master')}..{repository.head()}",
            ],
        )

    def test_failed_git_log_stops_the_skip(self) -> None:
        """`git log` が 0 以外で終われば、出力があっても 2 を返し、gitleaks を起動しない。"""
        repository = self.create_text_only_branch()
        self.install_git_wrapper('"$real_git" "$@"; exit 1')
        self.assertEqual(self.range_status(repository), 2)
        self.assertEqual(self.gitleaks_calls(), [])
        self.assert_no_leftover_temporary_files()

    def test_empty_git_log_output_stops_the_skip(self) -> None:
        """`git log` が成功しても出力が空なら、検査できていない入力として 2 を返す
        (gitleaks は空の入力を「検出なし」で終える)。"""
        repository = self.create_text_only_branch()
        self.install_git_wrapper("exit 0")
        self.assertEqual(self.range_status(repository), 2)
        self.assertEqual(self.gitleaks_calls(), [])
        self.assert_no_leftover_temporary_files()

    def test_missing_object_in_the_range_stops_the_skip(self) -> None:
        """範囲内の blob が欠けて `git log` が失敗した場合は 2 を返す (raw 差分による範囲の判定は
        blob を読まないため通る)。"""
        repository = self.create_repository()
        repository.write("README.md", "# updated\n")
        head = repository.commit_all("docs")
        blob = repository.rev(f"{head}:README.md")
        loose_object = repository.git_dir() / "objects" / blob[:2] / blob[2:]
        loose_object.chmod(loose_object.stat().st_mode | stat.S_IWUSR)
        loose_object.unlink()
        self.assertEqual(self.range_status(repository), 2)
        self.assertEqual(self.gitleaks_calls(), [])
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

    def test_config_comes_from_the_base_tree_and_base_ignore_is_not_used(self) -> None:
        """設定は base の tree から読む。base の tree に `.gitleaksignore` があっても使わない。"""
        repository = self.create_branch_on_base_with_gitleaks_files()
        # working tree の内容を base と変えておき、base の tree から読むことを確かめる。
        repository.write(".gitleaks.toml", UNTRACKED_GITLEAKS_CONFIG)
        repository.write(".gitleaksignore", UNTRACKED_GITLEAKS_IGNORE)
        self.assertEqual(self.range_status(repository), 0)
        record = self.single_gitleaks_record()
        self.assertEqual(record["config"], BASE_GITLEAKS_CONFIG)
        self.assert_no_gitleaksignore_is_read(record)
        self.assert_no_leftover_temporary_files()

    def test_default_rules_without_base_config(self) -> None:
        repository = self.create_text_only_branch()
        self.assertEqual(self.range_status(repository), 0)
        record = self.single_gitleaks_record()
        self.assertEqual(record["config"], DEFAULT_GITLEAKS_CONFIG)
        self.assert_no_gitleaksignore_is_read(record)

    def test_untracked_config_and_ignore_are_not_passed(self) -> None:
        """working tree に未追跡の `.gitleaks.toml` / `.gitleaksignore` を置いても、
        gitleaks に渡すのは base の tree の設定 (ここでは既定のルール) だけで、ignore は読ませない
        (cwd も repo にしない)。"""
        repository = self.create_text_only_branch()
        repository.write(".gitleaks.toml", UNTRACKED_GITLEAKS_CONFIG)
        repository.write(".gitleaksignore", UNTRACKED_GITLEAKS_IGNORE)
        self.assertEqual(self.range_status(repository), 0)
        record = self.single_gitleaks_record()
        self.assertEqual(record["config"], DEFAULT_GITLEAKS_CONFIG)
        self.assert_no_gitleaksignore_is_read(record)
        self.assertNotEqual(record["cwd"], str(repository.path))
        self.assertNotIn(str(repository.path), record["args"])
        self.assertNotIn(str(repository.path / ".gitleaks.toml"), record["args"])

    def create_branch_on_base(
        self, name: str, prepare_base: Callable[[Repository], None]
    ) -> Repository:
        """prepare_base で base (master) の tree を用意し、その上の feature branch で README.md
        だけを変える repository。"""
        repository = self.create_repository(name)
        repository.git("switch", "-q", "master")
        prepare_base(repository)
        repository.commit_all("base config")
        repository.git("switch", "-q", "feature")
        repository.git("reset", "-q", "--hard", "master")
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        return repository

    def test_unreadable_base_config_stops_the_skip(self) -> None:
        """base の tree に `.gitleaks.toml` があるのに読めない場合 (blob が手元に無い・通常
        ファイルでない) は、既定のルールに切り替えずに 2 を返し、gitleaks を起動しない。"""

        def config_with_custom_rule(repository: Repository) -> None:
            repository.write(
                ".gitleaks.toml",
                "[extend]\nuseDefault = true\n\n"
                '[[rules]]\nid = "custom-rule"\n'
                "regex = '''custom-secret-[0-9]{8}'''\n",
            )

        def config_as_symlink(repository: Repository) -> None:
            config_with_custom_rule(repository)
            (repository.path / ".gitleaks.toml").rename(
                repository.path / "other.toml"
            )
            os.symlink("other.toml", repository.path / ".gitleaks.toml")

        for label, prepare_base in {
            "missing_blob": config_with_custom_rule,
            "symlink": config_as_symlink,
        }.items():
            with self.subTest(case=label):
                self.gitleaks_log.unlink(missing_ok=True)
                repository = self.create_branch_on_base(f"repo-{label}", prepare_base)
                entry = repository.git("ls-tree", "master", "--", ".gitleaks.toml")
                if label == "missing_blob":
                    self.assertTrue(entry.startswith("100644 blob "), entry)
                    blob = repository.rev("master:.gitleaks.toml")
                    loose_object = (
                        repository.git_dir() / "objects" / blob[:2] / blob[2:]
                    )
                    loose_object.chmod(loose_object.stat().st_mode | stat.S_IWUSR)
                    loose_object.unlink()
                else:
                    self.assertTrue(entry.startswith("120000 blob "), entry)
                self.assertEqual(self.range_status(repository), 2)
                self.assertEqual(self.gitleaks_calls(), [])
                self.assert_no_leftover_temporary_files()

    def test_base_config_is_read_from_the_tree_root_in_a_subdirectory(self) -> None:
        """target_cwd が subdirectory でも、base の tree の root にある `.gitleaks.toml` を
        読む。"""
        repository = self.create_branch_on_base_with_gitleaks_files()
        (repository.path / "docs").mkdir()
        self.assertEqual(
            self.call_text_paths(
                "is_text_only_range_in",
                str(repository.path / "docs"),
                "master",
                "HEAD",
            ),
            0,
        )
        self.assertEqual(self.single_gitleaks_record()["config"], BASE_GITLEAKS_CONFIG)

    def test_user_config_environment_is_respected(self) -> None:
        """`GITLEAKS_CONFIG` / `GITLEAKS_CONFIG_TOML` が空でなければ `--config` を渡さない
        (ignore の一時ディレクトリは渡す)。空文字なら未設定と同じに扱う。`GITLEAKS_CONFIG` は
        読めるファイルを指す (読めなければ検査の失敗になる)。"""
        user_config = self.root / "user-gitleaks.toml"
        user_config.write_text(DEFAULT_GITLEAKS_CONFIG, encoding="utf-8")
        cases = {
            "GITLEAKS_CONFIG": (str(user_config), False),
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

    # path を条件にした rule (`path =` の key) を含む設定。key の書き方を変えて用意する。
    CONFIGS_WITH_PATH_KEY = {
        "rule_path": (
            "[extend]\nuseDefault = true\n\n"
            '[[rules]]\nid = "internal-id"\n'
            "regex = '''INTERNAL-[0-9]{8}'''\npath = '''^docs/'''\n"
        ),
        "extend_path": '[extend]\npath = "other-gitleaks.toml"\n',
        "inline_table": (
            "[extend]\nuseDefault = true\n\n"
            'rules = [{ id = "internal-id", regex = '
            "'''INTERNAL-[0-9]{8}''', path = '''^docs/''' }]\n"
        ),
        "quoted_key": (
            '[[rules]]\nid = "internal-id"\n'
            "regex = '''INTERNAL-[0-9]{8}'''\n\"path\" = '''^docs/'''\n"
        ),
        "dotted_key": 'extend.path = "other-gitleaks.toml"\n',
        "upper_case_key": (
            '[[rules]]\nid = "internal-id"\n'
            "regex = '''INTERNAL-[0-9]{8}'''\nPath = '''^docs/'''\n"
        ),
        "escaped_quoted_key": (
            '[[rules]]\nid = "internal-id"\n'
            "regex = '''INTERNAL-[0-9]{8}'''\n\"p\\u0061th\" = '''^docs/'''\n"
        ),
    }

    def test_base_config_with_a_path_key_stops_the_skip(self) -> None:
        """base の `.gitleaks.toml` に `path =` の key があれば (path を条件にした rule・
        `[extend]` の path・inline table の中・引用符で囲んだ key)、gitleaks を起動せずに 2 を
        返す。"""
        for label, config in self.CONFIGS_WITH_PATH_KEY.items():
            with self.subTest(case=label):
                self.gitleaks_log.unlink(missing_ok=True)
                repository = self.create_branch_on_base(
                    f"repo-{label}",
                    lambda repository, config=config: repository.write(
                        ".gitleaks.toml", config
                    ),
                )
                self.assertEqual(self.range_status(repository), 2)
                self.assertEqual(self.gitleaks_calls(), [])
                self.assert_no_leftover_temporary_files()

    def test_user_config_with_a_path_key_stops_the_skip(self) -> None:
        """`GITLEAKS_CONFIG` が指すファイル (相対 path は判定を呼んだ cwd から読む) と
        `GITLEAKS_CONFIG_TOML` の値に `path =` の key があれば、gitleaks を起動せずに 2 を返す。
        `GITLEAKS_CONFIG` のファイルを読めない場合も 2。"""
        config = self.CONFIGS_WITH_PATH_KEY["rule_path"]
        absolute_config = self.root / "user-with-path.toml"
        absolute_config.write_text(config, encoding="utf-8")
        # call_text_paths は self.root を cwd にして判定を呼ぶ。
        relative_config = Path("relative-user-with-path.toml")
        (self.root / relative_config).write_text(config, encoding="utf-8")
        cases: dict[str, dict[str, str]] = {
            "GITLEAKS_CONFIG": {"GITLEAKS_CONFIG": str(absolute_config)},
            "relative_GITLEAKS_CONFIG": {"GITLEAKS_CONFIG": str(relative_config)},
            "GITLEAKS_CONFIG_TOML": {"GITLEAKS_CONFIG_TOML": config},
            "both_with_path_only_in_toml": {
                "GITLEAKS_CONFIG_TOML": config,
                "GITLEAKS_CONFIG": str(self.root / "user-without-path.toml"),
            },
            "unreadable_GITLEAKS_CONFIG": {
                "GITLEAKS_CONFIG": str(self.root / "no-such-gitleaks.toml")
            },
        }
        (self.root / "user-without-path.toml").write_text(
            DEFAULT_GITLEAKS_CONFIG, encoding="utf-8"
        )
        for label, variables in cases.items():
            with self.subTest(case=label):
                self.gitleaks_log.unlink(missing_ok=True)
                repository = self.create_repository(f"repo-{label}")
                repository.write("README.md", "# updated\n")
                repository.commit_all("docs")
                self.env.update(variables)
                try:
                    self.assertEqual(self.range_status(repository), 2)
                finally:
                    for name in variables:
                        del self.env[name]
                self.assertEqual(self.gitleaks_calls(), [])
                self.assert_no_leftover_temporary_files()

    def test_allowlist_paths_key_does_not_stop_the_skip(self) -> None:
        """allowlist の `paths =` だけを持つ設定は、今までどおり skip できる。backslash を
        含む値 (key ではない) も skip を止めない。"""
        config = (
            "[extend]\nuseDefault = true\n\n"
            '[allowlist]\ndescription = "values like C:\\\\docs"\n'
            "paths = ['''^vendor/''', '''(^|/)node_modules/''']\n"
            'regexes = ["\\\\d{4}-example"]\n'
        )
        repository = self.create_branch_on_base(
            "repo", lambda repository: repository.write(".gitleaks.toml", config)
        )
        self.assertEqual(self.range_status(repository), 0)
        self.assertEqual(self.single_gitleaks_record()["config"], config)
        self.gitleaks_log.unlink()
        self.env["GITLEAKS_CONFIG_TOML"] = config
        try:
            self.assertEqual(self.range_status(repository), 0)
        finally:
            del self.env["GITLEAKS_CONFIG_TOML"]
        self.assertNotIn("--config", self.single_gitleaks_record()["args"])

    def create_branch_with_encoding_headers(
        self, name: str, encodings: list[str | None]
    ) -> Repository:
        """feature branch に、README.md を変える commit を encodings の header で 1 つずつ積む。"""
        repository = self.create_repository(name)
        for index, encoding in enumerate(encodings):
            repository.write("README.md", f"# updated {index}\n")
            repository.git("add", "README.md")
            commit = self.commit_with_encoding_header(
                repository, f"docs {index}", encoding
            )
            header = repository.run_git("cat-file", "commit", commit)
            if encoding is None:
                self.assertNotIn(b"\nencoding ", header)
            else:
                self.assertIn(f"\nencoding {encoding}\n".encode(), header)
        return repository

    def test_non_utf8_encoding_header_stops_the_skip(self) -> None:
        """範囲に UTF-8 以外の `encoding` header を持つ commit があれば、gitleaks を起動せずに
        2 を返す (範囲の一部の commit だけの場合も同じ)。"""
        cases: dict[str, list[str | None]] = {
            "IBM037": ["IBM037"],
            "ISO-8859-1": ["ISO-8859-1"],
            "one_of_two_commits": [None, "IBM037"],
        }
        for label, encodings in cases.items():
            with self.subTest(case=label):
                self.gitleaks_log.unlink(missing_ok=True)
                repository = self.create_branch_with_encoding_headers(
                    f"repo-{label}", encodings
                )
                self.assertEqual(self.range_status(repository), 2)
                self.assertEqual(self.gitleaks_calls(), [])
                self.assert_no_leftover_temporary_files()

    def test_utf8_or_missing_encoding_header_keeps_the_skip(self) -> None:
        """header が無い commit (git は UTF-8 とみなす) と、`UTF-8` / `utf8` の header の commit
        は今までどおり skip できる。"""
        repository = self.create_branch_with_encoding_headers(
            "repo", [None, "UTF-8", "utf8"]
        )
        self.assertEqual(self.range_status(repository), 0)
        self.assertEqual(len(self.gitleaks_calls()), 1)

    # gitleaks に渡す入力 (canary の行 + `git log` の出力) の上限 (バイト)。
    MAX_SCAN_INPUT_BYTES = 100_000

    def create_branch_with_scan_input_of(self, name: str, size: int) -> Repository:
        """gitleaks に渡す入力の大きさがちょうど size バイトになる文書の commit を持つ branch。

        文書は 80 バイトの行を並べ、最後の行の長さで大きさを合わせる (1 度 commit して
        `git log` の出力の大きさを測り、最後の行の長さを決めて commit し直す)。"""
        repository = self.create_repository(name)
        document = repository.path / "docs" / "large.md"
        line = "a" * 79 + "\n"

        def content(last_line_length: int) -> str:
            return line * 1200 + "b" * last_line_length + "\n"

        document.write_text(content(100), encoding="utf-8")
        repository.commit_all("large document")
        base = repository.rev("master")

        def scan_input_size() -> int:
            return CANARY_LINE_LENGTH + len(
                self.scan_input_git_log(repository, base, repository.head())
            )

        last_line_length = 100 + size - scan_input_size()
        self.assertGreaterEqual(last_line_length, 0)
        document.write_text(content(last_line_length), encoding="utf-8")
        repository.git("add", "-A")
        repository.git("commit", "-q", "--amend", "-m", "large document")
        self.assertEqual(scan_input_size(), size)
        return repository

    def test_scan_input_up_to_the_limit_keeps_the_skip(self) -> None:
        repository = self.create_branch_with_scan_input_of(
            "repo", self.MAX_SCAN_INPUT_BYTES
        )
        self.assertEqual(self.range_status(repository), 0)
        self.assertEqual(
            len(self.single_gitleaks_record()["stdin"].encode("utf-8")),
            self.MAX_SCAN_INPUT_BYTES,
        )

    def test_scan_input_over_the_limit_stops_the_skip(self) -> None:
        """gitleaks に渡す入力が上限を 1 バイトでも超えれば、gitleaks を起動せずに 2 を返す。"""
        repository = self.create_branch_with_scan_input_of(
            "repo", self.MAX_SCAN_INPUT_BYTES + 1
        )
        self.assertEqual(self.range_status(repository), 2)
        self.assertEqual(self.gitleaks_calls(), [])
        self.assert_no_leftover_temporary_files()

    def test_canary_differs_for_each_scan(self) -> None:
        """canary は検査ごとにランダムに作る (2 回の検査で値が異なる)。"""
        repository = self.create_text_only_branch()
        self.assertEqual(self.range_status(repository), 0)
        self.assertEqual(self.range_status(repository), 0)
        canaries = [
            record["stdin"].split("\n", 1)[0] for record in self.gitleaks_records()
        ]
        self.assertEqual(len(canaries), 2, canaries)
        for canary in canaries:
            with self.subTest(canary=canary):
                self.assertIsNotNone(CANARY_PATTERN.fullmatch(canary), canary)
        self.assertNotEqual(canaries[0], canaries[1])

    def test_anything_but_the_canary_alone_stops_the_skip(self) -> None:
        """検出なしとするのは、終了コード 3・stderr が空・report が canary の 1 行だけ、を
        すべて満たすときに限る。どれかを欠く結果はすべて 2 (レビュー必須) を返す。"""
        cases = {
            # 終了コード 0 で report が空 (canary も検出されない = 検査されていない)。
            "canary_not_detected": "no-canary",
            # report に canary 以外の行がある (秘匿情報の候補を検出した)。
            "other_finding_in_report": "finding",
            # 致命的なエラー (終了コード 1)。
            "fatal_error": "exit:1",
            # 終了コード 3 で report も canary だけだが、stderr に出力がある。
            "canary_with_stderr": "canary-with-stderr",
        }
        for label, result in cases.items():
            with self.subTest(case=label):
                repository = self.create_repository(f"repo-{label}")
                repository.write("README.md", "# updated\n")
                repository.commit_all("docs")
                self.set_gitleaks_result(result)
                self.assertEqual(self.range_status(repository), 2)
                self.assert_no_leftover_temporary_files()

    def test_gitleaks_stderr_output_stops_the_skip(self) -> None:
        """gitleaks が canary だけを検出しても、stderr に何か出していれば (エラーを log に出した
        等)、2 (レビュー必須) を返す。stderr の内容は出力しない。"""
        repository = self.create_text_only_branch()
        self.set_gitleaks_result("canary-with-stderr")
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
        self.set_gitleaks_result("finding")
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

    def test_failure_to_make_the_canary_requires_review(self) -> None:
        """canary を 36 文字の英数字で作れない場合は 2 (レビュー必須) を返し、gitleaks を
        起動しない。"""
        repository = self.create_text_only_branch()
        empty_tr = self.fake_gitleaks_dir / "tr"
        empty_tr.write_text("#!/bin/sh\ncat >/dev/null\nexit 0\n", encoding="utf-8")
        empty_tr.chmod(empty_tr.stat().st_mode | stat.S_IXUSR)
        self.assertEqual(self.range_status(repository), 2)
        self.assertEqual(self.gitleaks_calls(), [])
        self.assert_no_leftover_temporary_files()

    def test_gitleaks_findings_or_failures_stop_the_skip(self) -> None:
        for index, result in enumerate(("finding", "exit:2", "exit:126")):
            with self.subTest(result=result):
                repository = self.create_repository(f"repo-{index}")
                repository.git(
                    "update-ref", "refs/remotes/origin/master", repository.rev("master")
                )
                repository.write("README.md", "# updated\n")
                repository.commit_all("docs")
                self.set_gitleaks_result(result)
                self.assertEqual(self.range_status(repository), 2)
                self.assertEqual(self.push_status(repository), 2)

    def test_gitleaks_is_not_run_for_ranges_that_are_not_text_only(self) -> None:
        repository = self.create_repository()
        repository.write("tool.py", "print('changed')\n")
        repository.commit_all("code")
        self.set_gitleaks_result("finding")
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
    """実際の gitleaks で、ローカルの未追跡ファイル・base の ignore・git の設定・文書の置き場所が
    検出を抑止しないこと、commit message と削除行も検査すること、入力の形式の判定 (tar の
    magic) で検査を読み飛ばさせても skip しないこと。

    fake gitleaks は受け取った引数と標準入力しか記録できないため、gitleaks 自身の検出 (path の
    allowlist と `.gitleaksignore` の扱い、入力のどこを検査するか) は実物で確かめる。抑止の
    手段を使う各テストは、その手段が素朴な起動 (repo を走査元にした `gitleaks git` に範囲だけを
    渡す) では実際に検出を抑止することを先に確かめ、抑止されない gitleaks の版では skip する。
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

    def naive_scan_detects(self, repository: Repository, *extra_args: str) -> bool:
        """repo を走査元にした `gitleaks git` に範囲だけを渡す素朴な起動で検出するか。"""
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
                *extra_args,
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

    def skip_unless_naive_scan_is_suppressed(
        self, repository: Repository, *extra_args: str
    ) -> None:
        if self.naive_scan_detects(repository, *extra_args):
            self.skipTest("この gitleaks では素朴な起動でも検出が抑止されない")

    def test_clean_text_only_range_passes(self) -> None:
        repository = self.create_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        self.assertEqual(self.range_status(repository), 0)

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

    def test_base_config_path_allowlist_does_not_hide_the_document(self) -> None:
        """base の `.gitleaks.toml` の path の allowlist が文書の path に当たっていても検出する
        (差分の接頭辞をローカル設定で変えても同じ)。"""
        cases: dict[str, tuple[tuple[str, str], ...]] = {
            "default": (),
            "noprefix": (("diff.noprefix", "true"),),
            "dstPrefix": (("diff.dstPrefix", "b/vendor/"),),
        }
        for label, configs in cases.items():
            with self.subTest(case=label):
                repository = self.create_repository(f"repo-{label}")
                repository.git("switch", "-q", "master")
                repository.write(
                    ".gitleaks.toml",
                    "[extend]\nuseDefault = true\n\n"
                    "[allowlist]\npaths = ['''(^|/)notes\\.md$''']\n",
                )
                repository.commit_all("gitleaks config")
                repository.git("switch", "-q", "feature")
                repository.git("reset", "-q", "--hard", "master")
                repository.write("docs/notes.md", f"example = {DUMMY_GITHUB_PAT}\n")
                repository.commit_all("docs")
                for key, value in configs:
                    repository.git("config", key, value)
                self.skip_unless_naive_scan_is_suppressed(repository)
                self.assertEqual(self.range_status(repository), 2)

    def test_documents_at_default_allowlisted_paths_are_scanned(self) -> None:
        """gitleaks の既定の設定の path の allowlist (場所を問わず当たる `node_modules` 等) に
        当たる場所に置いた文書の token も検出する。"""
        documents = (
            "docs/node_modules/a.md",
            "vendor/bundle/a.md",
            "docs/gitleaks.toml.md",
        )
        for index, document in enumerate(documents):
            with self.subTest(document=document):
                repository = self.create_repository(f"repo-{index}")
                repository.write(document, f"example = {DUMMY_GITHUB_PAT}\n")
                repository.commit_all("docs")
                self.assert_all_regular_text_changes(
                    repository, [document], repository.merge_base("master"), "HEAD"
                )
                self.skip_unless_naive_scan_is_suppressed(repository)
                self.assertEqual(self.range_status(repository), 2)

    def test_base_gitleaksignore_does_not_suppress_detection(self) -> None:
        """base の tree の `.gitleaksignore` に、`gitleaks git` が報告する形式
        (`<commit>:<path>:<rule>:<行>`) の fingerprint を書いても検出する。"""
        repository = self.create_branch_with_secret()
        secret_commit = repository.head()
        ignore = f"{secret_commit}:README.md:github-pat:3\n"
        # fingerprint は token を書き込んだ commit を指すため、その commit の後に master を
        # 進めて `.gitleaksignore` を置く (範囲 `master..HEAD` は token を書き込んだ commit
        # だけで、`.gitleaksignore` は base の tree にある)。
        repository.git("switch", "-q", "master")
        repository.write(".gitleaksignore", ignore)
        repository.commit_all("gitleaks ignore")
        repository.git("switch", "-q", "feature")
        self.assertEqual(repository.range_commits("master"), [secret_commit])
        self.assertEqual(repository.git("show", "master:.gitleaksignore"), ignore)
        # 素朴な起動は working tree の `.gitleaksignore` を読むが、feature の working tree には
        # 無いため、同じ内容を `--gitleaks-ignore-path` で渡して抑止されることを確かめる。
        ignore_dir = self.root / "base-gitleaksignore"
        ignore_dir.mkdir()
        (ignore_dir / ".gitleaksignore").write_text(ignore, encoding="utf-8")
        self.skip_unless_naive_scan_is_suppressed(
            repository, "--gitleaks-ignore-path", str(ignore_dir)
        )
        self.assertEqual(self.range_status(repository), 2)

    def test_secret_in_commit_message_body_stops_the_skip(self) -> None:
        """commit message の本文 (2 行目以降) にある token も検出する。"""
        repository = self.create_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all(
            f"docs\n\nsee the example below\nexample = {DUMMY_GITHUB_PAT}"
        )
        self.assertNotIn(DUMMY_GITHUB_PAT, repository.git("show", "--format=", "HEAD"))
        self.assertEqual(self.range_status(repository), 2)

    def test_oneline_pretty_format_does_not_drop_the_message_body(self) -> None:
        """ローカル設定 `format.pretty = oneline` でも、commit message の本文の token を検出する。"""
        repository = self.create_repository()
        repository.write("README.md", "# updated\n")
        repository.commit_all(f"docs\n\nexample = {DUMMY_GITHUB_PAT}")
        repository.git("config", "format.pretty", "oneline")
        # この設定では、format を指定しない `git log -p` の出力から message の本文が落ちる。
        self.assertNotIn(DUMMY_GITHUB_PAT, repository.git("log", "-p", "master..HEAD"))
        self.assertEqual(self.range_status(repository), 2)

    def test_diff_relative_does_not_hide_changes_outside_the_target_directory(
        self,
    ) -> None:
        """ローカル設定 `diff.relative = true` で target_cwd を subdirectory にしても、その外の
        文書の token を検出する。文書は既定の allowlist の場所に置き、repo を path ごとに走査
        した場合にも検出から外れる条件と重ねる。"""
        repository = self.create_repository()
        document = "vendor/bundle/notes.md"
        repository.write(document, f"example = {DUMMY_GITHUB_PAT}\n")
        repository.commit_all("docs")
        repository.git("config", "diff.relative", "true")
        target_cwd = repository.path / "docs"
        self.assertTrue(target_cwd.is_dir())
        # この設定では、subdirectory で実行した `git log -p` の出力から、その外の差分が落ちる。
        self.assertNotIn(
            DUMMY_GITHUB_PAT,
            repository.git("-C", str(target_cwd), "log", "-p", "master..HEAD"),
        )
        self.assertEqual(
            self.call_text_paths(
                "is_text_only_range_in", str(target_cwd), "master", "HEAD"
            ),
            2,
        )

    def test_secret_on_a_deleted_line_stops_the_skip(self) -> None:
        """既存の文書から token の行を消す commit も検出する (削除行も検査する)。"""
        repository = self.create_repository()
        repository.git("switch", "-q", "master")
        repository.write(
            "docs/notes.md", f"intro\nexample = {DUMMY_GITHUB_PAT}\noutro\n"
        )
        repository.commit_all("add example")
        repository.git("switch", "-q", "feature")
        repository.git("reset", "-q", "--hard", "master")
        repository.write("docs/notes.md", "intro\noutro\n")
        repository.commit_all("remove example")
        # 範囲内で token は削除行にだけ現れる。
        self.assertIn(
            f"\n-example = {DUMMY_GITHUB_PAT}\n",
            repository.git("log", "-p", "master..HEAD"),
        )
        self.assertNotIn(
            f"\n+example = {DUMMY_GITHUB_PAT}\n",
            repository.git("log", "-p", "master..HEAD"),
        )
        self.assertEqual(self.range_status(repository), 2)

    # gitleaks stdin が入力を tar と判定する magic (offset 257 の `ustar` + NUL + `00`)。
    TAR_MAGIC = b"ustar\x0000"
    TAR_MAGIC_OFFSET = 257

    def naive_stdin_scan_status(
        self, data: bytes, *extra_args: str, from_file: bool = False
    ) -> int:
        """data をそのまま `gitleaks stdin` に渡したときの終了コード (0 = 検出なし、1 = 検出)。
        cwd は空のディレクトリにする。from_file なら、判定 lib と同じく stdin を file から
        渡す (pipe とは gitleaks が 1 回に読める量が違い、入力を分割する位置も変わる)。"""
        cwd = Path(tempfile.mkdtemp(dir=self.root))
        arguments = [
            "gitleaks",
            "stdin",
            "--no-banner",
            "--redact",
            "--log-level",
            "error",
            "--exit-code",
            "1",
            "--timeout",
            "30",
            *extra_args,
        ]
        if from_file:
            input_file = self.root / f"{cwd.name}.input"
            input_file.write_bytes(data)
            with input_file.open("rb") as stdin:
                result = subprocess.run(
                    arguments,
                    cwd=cwd,
                    stdin=stdin,
                    env=self.env,
                    capture_output=True,
                    check=False,
                    timeout=60,
                )
        else:
            result = subprocess.run(
                arguments,
                cwd=cwd,
                input=data,
                env=self.env,
                capture_output=True,
                check=False,
                timeout=60,
            )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode

    def create_branch_with_tar_magic(
        self, name: str, magic_offset_in_git_log: int, with_secret: bool
    ) -> tuple[Repository, bytes]:
        """lib と同じ option の `git log` の出力で、先頭から magic_offset_in_git_log バイトの
        位置に tar の magic が来るよう文書を書き込んだ feature branch と、その `git log` の出力。
        commit message には NUL を入れられないため、magic は文書の内容 (`--text` で NUL が
        そのまま出る) に置く。with_secret なら文書の 2 行目にダミーの token を書く。"""
        repository = self.create_repository(name)
        document = repository.path / "docs" / "archive.md"

        def content(padding: int) -> bytes:
            body = b"a" * padding + self.TAR_MAGIC + b"\n"
            if with_secret:
                body += f"example = {DUMMY_GITHUB_PAT}\n".encode()
            return body

        # 詰め物なしで commit して magic の位置を測り、詰め物の長さを決めて commit し直す
        # (`git log` の出力のうち文書の内容より前の部分の長さは、詰め物の長さで変わらない)。
        document.write_bytes(content(0))
        repository.commit_all("docs")
        base = repository.rev("master")
        unpadded = self.scan_input_git_log(repository, base, repository.head())
        padding = magic_offset_in_git_log - unpadded.index(self.TAR_MAGIC)
        self.assertGreaterEqual(padding, 0)
        document.write_bytes(content(padding))
        repository.git("add", "-A")
        repository.git("commit", "-q", "--amend", "-m", "docs")
        output = self.scan_input_git_log(repository, base, repository.head())
        self.assertEqual(output.index(self.TAR_MAGIC), magic_offset_in_git_log)
        self.assert_all_regular_text_changes(
            repository, ["docs/archive.md"], repository.merge_base("master"), "HEAD"
        )
        return repository, output

    def test_tar_magic_without_a_canary_line_does_not_hide_the_secret(self) -> None:
        """canary の行が無い入力 (`git log` の出力だけ) の offset 257 に tar の magic を置き、
        gitleaks に何も検査させない細工をした範囲も、2 を返す。"""
        repository, output = self.create_branch_with_tar_magic(
            "repo", self.TAR_MAGIC_OFFSET, with_secret=True
        )
        # この配置の入力を素朴に `gitleaks stdin` に渡すと、token があっても検出されない
        # (1 バイトずらすと検出される)。
        self.assertEqual(self.naive_stdin_scan_status(output), 0)
        self.assertEqual(self.naive_stdin_scan_status(b"\n" + output), 1)
        self.assertEqual(self.range_status(repository), 2)

    def test_tar_magic_after_the_canary_line_stops_the_skip(self) -> None:
        """canary の行の後ろに続く入力の offset 257 に tar の magic を置き、gitleaks に canary
        ごと何も検査させない細工をした範囲は、canary が検出されないため 2 を返す (秘匿情報の
        有無によらない)。"""
        for with_secret in (True, False):
            with self.subTest(with_secret=with_secret):
                repository, output = self.create_branch_with_tar_magic(
                    f"repo-{with_secret}",
                    self.TAR_MAGIC_OFFSET - CANARY_LINE_LENGTH,
                    with_secret=with_secret,
                )
                canary = "ghp_" + "".join(
                    secrets.choice(string.ascii_letters + string.digits)
                    for _ in range(36)
                )
                canary_line = f"{canary}\n".encode()
                self.assertEqual(len(canary_line), CANARY_LINE_LENGTH)
                # canary の行 + この `git log` の出力を素朴に `gitleaks stdin` に渡すと、canary も
                # 検出されない (1 バイトずらすと検出される)。
                self.assertEqual(self.naive_stdin_scan_status(canary_line + output), 0)
                self.assertEqual(
                    self.naive_stdin_scan_status(canary_line + b"\n" + output), 1
                )
                self.assertEqual(self.range_status(repository), 2)

    def test_config_without_the_default_rules_stops_the_skip(self) -> None:
        """base の `.gitleaks.toml` が既定のルールを extend しない (独自のルールだけを持つ)
        場合は、canary が検出されないため、秘匿情報の無い文書の範囲でも 2 を返す。"""
        config = "[[rules]]\nid = \"custom-rule\"\nregex = '''custom-secret-[0-9]{8}'''\n"
        # この設定自体は gitleaks が読める (秘匿情報の無い入力は終了コード 0 で終わる)。
        config_file = self.root / "custom-only.toml"
        config_file.write_text(config, encoding="utf-8")
        self.assertEqual(
            self.naive_stdin_scan_status(
                b"clean\n", "--config", str(config_file)
            ),
            0,
        )
        repository = self.create_repository()
        repository.git("switch", "-q", "master")
        repository.write(".gitleaks.toml", config)
        repository.commit_all("gitleaks config")
        repository.git("switch", "-q", "feature")
        repository.git("reset", "-q", "--hard", "master")
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        self.assertEqual(self.range_status(repository), 2)

    def test_non_utf8_encoding_header_does_not_hide_the_message_secret(self) -> None:
        """commit の `encoding` header を `IBM037` にして message に token を入れた範囲は、
        `git log --encoding=UTF-8` の変換で token が入力から消えるが、skip せず 2 を返す。"""
        repository = self.create_repository()
        repository.write("README.md", "# updated\n")
        repository.git("add", "README.md")
        commit = self.commit_with_encoding_header(
            repository, f"docs\n\nexample = {DUMMY_GITHUB_PAT}", "IBM037"
        )
        raw = repository.run_git("cat-file", "commit", commit)
        self.assertIn(b"\nencoding IBM037\n", raw)
        self.assertIn(DUMMY_GITHUB_PAT.encode(), raw)
        # lib と同じ option (`--encoding=UTF-8`) で書き出すと、message が IBM037 から変換されて
        # token が消える (この環境の iconv が IBM037 を変換できない場合は skip する)。
        output = self.scan_input_git_log(repository, repository.rev("master"), commit)
        if DUMMY_GITHUB_PAT.encode() in output:
            self.skipTest("この環境の git は IBM037 の message を変換しない")
        self.assertEqual(self.range_status(repository), 2)

    # gitleaks stdin が分割の境界をまたぐ token を見逃す、token の行の始まりの位置 (入力の先頭
    # からの offset)。stdin が file のとき、gitleaks は最初に 100,000 バイトを読み、空行が
    # 見つかるまで最大 25,000 バイトを読み足してから分割するため、空行を含まない入力は
    # 125,000 バイトの位置で分割される (gitleaks 8.30.1 で、この位置をまたぐ token の行
    # (124,960〜124,998 から始まる行) を見逃すことを確認した)。
    SPLIT_BOUNDARY_OFFSET = 124_980

    def test_secret_across_the_split_boundary_stops_the_skip(self) -> None:
        """gitleaks が入力を分割する境界をまたぐ位置に token の行を置いた範囲も、skip せず 2 を
        返す。"""
        repository = self.create_repository()
        document = repository.path / "docs" / "large.md"
        filler = "a" * 78 + "\n"
        token_line = DUMMY_GITHUB_PAT.ljust(78, "b") + "\n"

        def content(first_line_length: int) -> str:
            return (
                "c" * first_line_length
                + "\n"
                + filler * 1550
                + token_line
                + filler * 400
            )

        # lib の入力は canary の行 (41 バイト) + `git log` の出力。token の行 (`+` で始まる) が
        # 入力の SPLIT_BOUNDARY_OFFSET から始まるよう、1 行目の長さを合わせて commit し直す。
        target = self.SPLIT_BOUNDARY_OFFSET - CANARY_LINE_LENGTH
        marker = f"+{DUMMY_GITHUB_PAT}".encode()
        document.write_text(content(100), encoding="utf-8")
        repository.commit_all("large document")
        base = repository.rev("master")
        first_line_length = 100 + target - self.scan_input_git_log(
            repository, base, repository.head()
        ).index(marker)
        self.assertGreaterEqual(first_line_length, 0)
        document.write_text(content(first_line_length), encoding="utf-8")
        repository.git("add", "-A")
        repository.git("commit", "-q", "--amend", "-m", "large document")
        output = self.scan_input_git_log(repository, base, repository.head())
        self.assertEqual(output.index(marker), target)
        # canary の代わりに同じ長さの token でない行を置いた同じ配置の入力を、lib と同じく
        # file から素朴に `gitleaks stdin` に渡すと、token は検出されない (この gitleaks で
        # 境界の位置が異なる場合は skip する)。
        placeholder_line = b"x" * (CANARY_LINE_LENGTH - 1) + b"\n"
        if self.naive_stdin_scan_status(placeholder_line + output, from_file=True) != 0:
            self.skipTest("この gitleaks では分割の境界の位置が異なる")
        self.assertEqual(self.range_status(repository), 2)

    def test_rule_with_a_path_condition_stops_the_skip(self) -> None:
        """base の `.gitleaks.toml` に path を条件にした独自の rule がある場合、path を持たない
        入力ではその rule が働かないため、その rule だけが当たる値の範囲でも skip せず 2 を返す。"""
        config = (
            "[extend]\nuseDefault = true\n\n"
            '[[rules]]\nid = "internal-id"\n'
            "regex = '''INTERNAL-[0-9]{8}'''\npath = '''^docs/'''\n"
        )
        repository = self.create_repository()
        repository.git("switch", "-q", "master")
        repository.write(".gitleaks.toml", config)
        repository.commit_all("gitleaks config")
        repository.git("switch", "-q", "feature")
        repository.git("reset", "-q", "--hard", "master")
        repository.write("docs/README.md", "id = INTERNAL-12345678\n")
        repository.commit_all("docs")
        # 同じ範囲を path を持つ入力 (`gitleaks git`) と同じ設定で走査すると検出される。
        self.assertTrue(
            self.naive_scan_detects(
                repository, "--config", str(repository.path / ".gitleaks.toml")
            )
        )
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
        for result in ("finding", "exit:1"):
            self.set_gitleaks_result(result)
            for name, gate in self.GATES.items():
                with self.subTest(gate=name, result=result):
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
        self.set_gitleaks_result("finding")
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

    def configure_pr(
        self, head: str, base_oid: str | None, base_ref_name: str | None = "master"
    ) -> None:
        """fake gh が返す PR 情報を設定する。base branch 名の既定は master
        (create_pr_repository が refs/remotes/origin/master を向ける branch)。base_oid や
        base_ref_name が None なら、その field を応答に含めない。"""
        pr: dict[str, object] = {"number": PR_NUMBER, "headRefOid": head}
        if base_oid is not None:
            pr["baseRefOid"] = base_oid
        if base_ref_name is not None:
            pr["baseRefName"] = base_ref_name
        self.configure_fake_gh(pr)

    def is_ancestor(
        self,
        repository: Repository,
        ancestor: str,
        descendant: str,
        *git_options: str,
    ) -> bool:
        """`git [git_options] merge-base --is-ancestor <ancestor> <descendant>` が成功するか。"""
        assert GIT is not None
        result = subprocess.run(
            [GIT, *git_options, "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=repository.path,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr.decode())
        return result.returncode == 0

    def create_pr_on_rewritten_base(self, name: str = "repo") -> tuple[Repository, str]:
        """PR の base commit (baseRefOid) が、base branch の書き換えで base の履歴から外れた
        PR を作り、repository と base commit を返す。

        master にコード commit X を足して baseRefOid を X にし、PR の branch は X の上で文書
        だけを変える。refs/remotes/origin/master は X の親から分岐した、X を含まない commit
        に向ける (force push で master から X を消した状態)。
        """
        repository = self.create_pr_repository(name)
        parent = repository.rev("master")
        repository.write("tool.py", "print('code on the old base')\n")
        stale_base = repository.commit_all("code on the old base")
        repository.git("update-ref", "refs/heads/master", stale_base)
        repository.write("README.md", "# updated\n")
        repository.commit_all("docs")
        rewritten_base = repository.git(
            "commit-tree", f"{parent}^{{tree}}", "-p", parent, "-m", "rewritten base"
        ).strip()
        repository.git("update-ref", "refs/remotes/origin/master", rewritten_base)
        self.configure_pr(repository.head(), stale_base)
        return repository, stale_base

    def assert_skip_without_reachability_check(
        self, repository: Repository, base: str
    ) -> None:
        """`<base>..HEAD` がテキスト系文書だけで、判定関数に渡せば skip が成立すること
        (base の到達確認が無ければ通ってしまう状態であることの確認)。"""
        self.assert_all_regular_text_changes(repository, ["README.md"], base, "HEAD")
        self.assertEqual(self.range_status(repository, base=base), 0)

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
        for index, gitleaks_result in enumerate(("finding", "exit:1")):
            with self.subTest(result=gitleaks_result):
                repository = self.create_pr_repository(f"repo-{index}")
                repository.write("README.md", "# updated\n")
                repository.commit_all("docs")
                head = repository.head()
                self.configure_pr(head, repository.rev("master"))
                self.set_gitleaks_result(gitleaks_result)
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

    def test_unusable_head_sha_falls_back_to_record(self) -> None:
        """gh が返す head SHA が 40 桁の小文字 16 進数でなければ、skip の判定をせずに記録を
        照合する。判定関数に渡せば成立してしまう値 (短縮・大文字) を使う。"""
        cases: dict[str, Callable[[str], str]] = {
            "abbreviated": lambda head: head[:12],
            "uppercase": lambda head: head.upper(),
        }
        for label, make_head in cases.items():
            with self.subTest(case=label):
                repository = self.create_pr_repository(f"repo-{label}")
                base = repository.rev("master")
                repository.write("README.md", "# updated\n")
                repository.commit_all("docs")
                head = make_head(repository.head())
                self.assertEqual(
                    self.range_status(repository, base=base, head=head), 0
                )
                self.configure_pr(head, base)
                self.assert_record_deny(self.run_gate(repository))

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

    def test_base_commit_dropped_from_base_branch_falls_back_to_record(self) -> None:
        """PR の base commit (baseRefOid) がローカルの refs/remotes/origin/<base> から到達
        できない (base branch の書き換えで履歴から外れた) 場合は skip せず、記録を照合する。
        base commit を起点にした範囲はテキスト系文書だけだが、merge で base に入る変更には
        書き換えで base から消えたコード commit が含まれる。"""
        repository, stale_base = self.create_pr_on_rewritten_base()
        self.assert_skip_without_reachability_check(repository, stale_base)
        self.assertFalse(
            self.is_ancestor(repository, stale_base, "refs/remotes/origin/master")
        )
        self.assertIn(
            "tool.py",
            repository.changed_paths(
                repository.merge_base("refs/remotes/origin/master"), "HEAD"
            ),
        )
        self.assert_record_deny(self.run_gate(repository))
        self.write_record(repository, repository.head())
        self.assertEqual(self.run_gate(repository).stdout, b"")

    def test_unusable_base_branch_name_falls_back_to_record(self) -> None:
        """base branch 名 (baseRefName) が欠落・`-` 始まり・ref 名として不正・
        remote-tracking ref の無い名前の場合は skip せず、記録を照合する。base commit は
        refs/remotes/origin/master から到達できる (base branch 名が master なら skip される)
        状態にしておく。"""
        cases: dict[str, str | None] = {
            "missing": None,
            "leading_dash": "-master",
            "invalid_ref_name": "bad..name",
            "no_remote_tracking_ref": "develop",
        }
        for label, base_ref_name in cases.items():
            with self.subTest(case=label):
                repository = self.create_pr_repository(f"repo-{label}")
                base = repository.rev("master")
                repository.write("README.md", "# updated\n")
                repository.commit_all("docs")
                head = repository.head()
                self.assert_skip_without_reachability_check(repository, base)
                self.assertTrue(
                    self.is_ancestor(repository, base, "refs/remotes/origin/master")
                )
                if label == "leading_dash":
                    # 同じ名前の remote-tracking ref を置き、名前の検証だけが止める状態にする。
                    repository.git("update-ref", "refs/remotes/origin/-master", base)
                if label == "no_remote_tracking_ref":
                    # 短い名前 origin/develop で引けば解決できる branch と tag を置き、
                    # 完全な名前 refs/remotes/origin/develop で引かなければ通ってしまう状態に
                    # する。
                    repository.git("update-ref", "refs/heads/origin/develop", base)
                    repository.git("update-ref", "refs/tags/origin/develop", base)
                    self.assertTrue(
                        self.is_ancestor(repository, base, "origin/develop")
                    )
                self.configure_pr(head, base, base_ref_name=base_ref_name)
                self.assert_record_deny(self.run_gate(repository))
                self.write_record(repository, head)
                self.assertEqual(self.run_gate(repository).stdout, b"")

    def test_replace_ref_does_not_make_base_commit_reachable(self) -> None:
        """refs/remotes/origin/<base> の commit の親を replace ref で PR の base commit に
        差し替えても、gate は replace ref を読まずに到達を確かめ、記録を照合する。"""
        repository, stale_base = self.create_pr_on_rewritten_base()
        rewritten_base = repository.rev("refs/remotes/origin/master")
        repository.git("replace", "--graft", rewritten_base, stale_base)
        # replace ref を読む git では到達でき、読まない git では到達できない。
        self.assertTrue(
            self.is_ancestor(repository, stale_base, "refs/remotes/origin/master")
        )
        self.assertFalse(
            self.is_ancestor(
                repository,
                stale_base,
                "refs/remotes/origin/master",
                "--no-replace-objects",
            )
        )
        self.assert_skip_without_reachability_check(repository, stale_base)
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
        self.set_gitleaks_result("finding")

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

    def add_and_remove_a_document(self, repository: Repository) -> None:
        """文書を足す commit と消す commit を積む (net diff は空で、各 commit はテキスト系
        文書だけ)。"""
        repository.write("docs/temporary.md", "temporary\n")
        repository.commit_all("add a document")
        repository.remove("docs/temporary.md")
        repository.commit_all("remove the document")
        self.assertEqual(
            repository.changed_paths(
                repository.merge_base("refs/remotes/origin/master"), "HEAD"
            ),
            [],
        )
        self.assertEqual(self.range_status(repository), 0)

    @unittest.skipUnless(JQ, "merge wrapper requires jq")
    def test_merge_wrapper_skips_pr_that_adds_and_removes_a_document(self) -> None:
        """net diff が空でもテキスト系文書だけの PR は、空の差分の判定より先に skip する
        (merge gate も同じ判定で記録を要求しない)。"""
        repository = self.create_merge_repository()
        self.add_and_remove_a_document(repository)
        self.configure_merge_pr(repository)
        pending = repository.git_dir() / MERGE_PENDING_RECORD

        result = self.run_wrapper(MERGE_WRAPPER, repository)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_skip_only_output(result)
        self.assertFalse(self.tripwire.exists(), "codex companion が起動された")
        self.assertFalse(pending.exists(), "pending attestation が書かれた")

    def test_push_wrapper_skips_branch_that_adds_and_removes_a_document(self) -> None:
        """push の wrapper も、文書を足して消した branch を skip する (空 push の判定は各
        commit の tree も見るため、この branch を空 push とは扱わない)。"""
        repository = self.create_push_repository()
        self.add_and_remove_a_document(repository)
        pending = repository.git_dir() / PUSH_CODEX_PENDING

        result = self.run_wrapper(PUSH_WRAPPER, repository)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_skip_only_output(result)
        self.assertFalse(self.tripwire.exists(), "codex companion が起動された")
        self.assertFalse(pending.exists(), "pending attestation が書かれた")

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
        self.set_gitleaks_result("finding")

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
        """gitleaks の検査で skip を止める条件、`gitleaks stdin` に渡す入力と検査する範囲、
        `--ignore-gitleaks-allow` の理由、repo の設定ファイルと path の allowlist の扱いを書く。"""
        for name, path in PLUGIN_READMES.items():
            with self.subTest(plugin=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn(
                    "秘匿情報の候補を検出した場合や gitleaks の実行に失敗した場合", body
                )
                self.assertIn("path を持たない入力として `gitleaks stdin", body)
                self.assertNotIn("`gitleaks git", body)
                self.assertIn(
                    "commit message は本文まで、差分は追加行に加えて削除行も検査する",
                    body,
                )
                self.assertIn("秘匿情報を含む行を消す変更でも skip しません", body)
                self.assertIn("`git log` が失敗した場合や出力が空の場合", body)
                self.assertIn("入力の 1 行目には毎回ランダムに作る canary", body)
                self.assertIn(
                    "gitleaks が canary だけを検出したときに限って検出なしとします", body
                )
                self.assertIn(
                    "binary の形式 (tar など) と判定すると何も検査せずに終了コード 0 で終わ", body
                )
                self.assertIn("既定の `github-pat` rule を含まない場合", body)
                self.assertIn("`--ignore-gitleaks-allow` を付けるのは", body)
                self.assertIn("`gitleaks:allow` コメント", body)
                self.assertIn("gitleaks の設定 (`.gitleaks.toml`) は", body)
                self.assertIn(
                    "working tree ではなく範囲の起点 (base の commit) の tree から読みます",
                    body,
                )
                self.assertIn("path の allowlist", body)
                self.assertIn("では検出から外れません", body)
                self.assertIn("`.gitleaksignore` は使いません", body)
                self.assertNotIn("`.gitleaks.toml` / `.gitleaksignore`", body)
                self.assertNotIn("差分の path の表記", body)
                self.assertIn(
                    "`git log` に `--text --no-textconv --no-color` を付け", body
                )
                self.assertIn("`format.pretty`", body)
                self.assertIn("`diff.relative`", body)
                self.assertIn("`GITLEAKS_CONFIG` / `GITLEAKS_CONFIG_TOML`", body)
                self.assertNotIn("gitleaks の既定どおり読まれます", body)
                self.assertIn("deny の理由文にその旨を 1 文加えます", body)

    def test_plugin_readmes_state_that_unreadable_config_stops_the_skip(self) -> None:
        for name, path in PLUGIN_READMES.items():
            with self.subTest(plugin=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn(
                    "`.gitleaks.toml` があっても読めない場合 (symlink である、blob が手元に"
                    "無い等) は、既定のルールに切り替えず、skip もしません",
                    body,
                )

    def test_plugin_readmes_state_the_encoding_and_size_conditions(self) -> None:
        for name, path in PLUGIN_READMES.items():
            with self.subTest(plugin=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn(
                    "範囲に UTF-8 以外の `encoding` header を持つ commit がある場合", body
                )
                self.assertIn(
                    "検査の入力が約 100 KB (100,000 バイト) を超える場合", body
                )

    def test_plugin_readmes_state_that_a_path_key_stops_the_skip(self) -> None:
        for name, path in PLUGIN_READMES.items():
            with self.subTest(plugin=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn(
                    "に `path =` の key (path を条件にした rule や、`[extend]` の `path` に"
                    "よる別の設定の読み込み) が 1 つでもある場合も、path を持たない入力では"
                    "その条件で検査できないため skip しません",
                    body,
                )

    def test_plugin_readmes_state_that_missing_objects_are_not_fetched(self) -> None:
        for name, path in PLUGIN_READMES.items():
            with self.subTest(plugin=name):
                body = path.read_text(encoding="utf-8")
                self.assertIn("判定に必要な object が手元に無い", body)
                self.assertIn("判定では remote から object を取りに行かない", body)

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
                self.assertIn(
                    "`git log` の書き出しと gitleaks の実行は合わせて 20 秒で打ち切り", body
                )

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
        self.assertNotIn("--json number,headRefOid,baseRefName`", body)
        self.assertIn(
            "`baseRefOid` がローカルの `refs/remotes/origin/<base>` から到達できること",
            body,
        )
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
