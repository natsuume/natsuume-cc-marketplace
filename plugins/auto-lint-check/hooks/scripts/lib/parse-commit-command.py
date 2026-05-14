#!/usr/bin/env python3
"""block-commit-lint.sh から呼び出される commit コマンド解析器。

引数として渡された Bash コマンド文字列を ``shlex.shlex`` でクォート対応
トークン化し、`git ... commit` invocation を検出して以下を判定する。

- 同一コマンド内に `git add` / `git stage` が含まれるか
- `git commit` 自体に `-a` / `--all` / `-am` 系の auto-stage フラグがあるか
- `git commit` 自体に `-o` / `--only` / `-i` / `--include` / pathspec が
  含まれるか (working tree から取り込む形式)
- `git -C ...` / `--git-dir` / `--work-tree` のように commit 対象 repo を
  切り替える global option を伴っているか

判定結果は exit code で返す:

    0  HAS_STAGING (working tree も lint 対象に含めるべき)
    1  no staging (staged blob のみ lint で十分)
    2  parse failure (呼び出し側で安全側に倒すこと)
    3  repo override (cwd と異なる repo を commit するため、cwd repo を
       silently lint しないよう呼び出し側で skip すべき)

bash の `case` / `=~` ベース検出ではコミットメッセージ内 (`-m "git add"`)
を staging 操作として誤検出するため、shlex 経由のトークン解析に集約している。
"""

from __future__ import annotations

import shlex
import sys

COMMIT_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "-m",
        "--message",
        "-F",
        "--file",
        "-t",
        "--template",
        "-c",
        "--reedit-message",
        "-C",
        "--reuse-message",
        "-S",
        "--gpg-sign",
        "--author",
        "--date",
        "--cleanup",
        "--fixup",
        "--squash",
        "--trailer",
    }
)

# `-o` (`--only`) / `-i` (`--include`) は flag に続く pathspec を working
# tree から取り込む → 出現で pathspec mode 確定。
PATHSPEC_MODE_FLAGS: frozenset[str] = frozenset({"-o", "--only", "-i", "--include"})

SEPARATORS: frozenset[str] = frozenset({";", "&&", "||", "|", "&"})

# `git` の global option で value を取るもの (subcommand を見つけるために skip)。
GIT_GLOBAL_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "-C",
        "-c",
        "--exec-path",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--super-prefix",
        "--list-cmds",
        "--attr-source",
    }
)

# `git` の global option で repo の場所自体を切り替えるもの。これらが
# `git ... commit` の前にあると commit 対象が cwd repo と異なる。
REPO_OVERRIDE_FLAGS: frozenset[str] = frozenset({"-C", "--git-dir", "--work-tree"})


def _short_cluster_has_a(t: str) -> bool:
    """`-am` / `-ma` / `-aS` のような short cluster で `a` を含むものを
    auto-stage と判定。`--amend` は `--` 開始なので除外、`-m` は `a` を含まない。"""
    return len(t) >= 2 and t[0] == "-" and t[1] != "-" and "a" in t[1:]


def _find_subcommand_after_git(toks: list[str], start: int) -> tuple[int, str, bool] | None:
    """`git` トークンの位置 (``start``) から global option を読み飛ばし、最初の
    non-option トークン (subcommand) の (index, value, has_repo_override) を返す。
    無ければ ``None``。"""
    j = start + 1
    n = len(toks)
    expect_val = False
    pending_override = False
    has_override = False
    while j < n:
        u = toks[j]
        if u in SEPARATORS:
            return None
        if expect_val:
            if pending_override:
                has_override = True
                pending_override = False
            expect_val = False
            j += 1
            continue
        if u in GIT_GLOBAL_VALUE_FLAGS:
            if u in REPO_OVERRIDE_FLAGS:
                pending_override = True
            expect_val = True
            j += 1
            continue
        if u.startswith("--") and "=" in u:
            key = u.split("=", 1)[0]
            if key in REPO_OVERRIDE_FLAGS:
                has_override = True
            j += 1
            continue
        if u.startswith("-"):
            j += 1
            continue
        return (j, u, has_override)
    return None


def _classify(command: str) -> int:
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lex.whitespace_split = True
    try:
        toks = list(lex)
    except ValueError:
        return 2

    n = len(toks)
    i = 0
    while i < n:
        if toks[i] != "git":
            i += 1
            continue
        result = _find_subcommand_after_git(toks, i)
        if result is None:
            i += 1
            continue
        sub_idx, sub, has_override = result
        if sub in ("add", "stage"):
            return 0
        if sub != "commit":
            i = sub_idx + 1
            continue
        if has_override:
            return 3
        # ここで最初の commit invocation を見つけた。判定対象はこの commit
        # なので、これ以降の token (例: `commit -m ok && git add foo` の `&&`
        # 以降) は同じ Bash コマンドでも commit が走った後に実行されるため、
        # この commit の判定には影響しない。flag を見終えたら即 return する。
        j = sub_idx + 1
        expect_val = False
        while j < n:
            u = toks[j]
            if u in SEPARATORS:
                break
            if expect_val:
                expect_val = False
            elif u == "--":
                return 0
            elif u in PATHSPEC_MODE_FLAGS:
                return 0
            elif u == "--all":
                return 0
            elif u in COMMIT_VALUE_FLAGS:
                expect_val = True
            elif u.startswith("--"):
                pass
            elif u.startswith("-") and _short_cluster_has_a(u):
                return 0
            elif u.startswith("-"):
                pass
            else:
                return 0
            j += 1
        return 1
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(2)
    sys.exit(_classify(sys.argv[1]))
