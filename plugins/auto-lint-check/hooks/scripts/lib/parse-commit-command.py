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
    1  commit はあるが staging trigger なし (staged blob のみ lint で十分)
    2  parse failure (呼び出し側で安全側に倒すこと)
    3  repo override (cwd と異なる repo を commit するため、cwd repo を
       silently lint しないよう呼び出し側で skip すべき)
    4  実際の commit subcommand なし (例: `echo "; git commit"` のような
       quoted 文字列が初期 regex に誤マッチしただけ → skip すべき)

bash の `case` / `=~` ベース検出ではコミットメッセージ内 (`-m "git add"`)
を staging 操作として誤検出するため、shlex 経由のトークン解析に集約している。
"""

from __future__ import annotations

import re
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

# 環境変数 prefix で repo を切り替える同等の効果がある assignment。
# `GIT_DIR=/other git commit ...` は `git --git-dir=/other commit ...` と
# 同義 (commit 対象が cwd repo と異なる)。
REPO_OVERRIDE_ENV_VARS: frozenset[str] = frozenset(
    {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"}
)

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _is_env_assignment(tok: str) -> bool:
    return bool(_ENV_ASSIGN_RE.match(tok))


def _is_repo_override_env(tok: str) -> bool:
    if "=" not in tok:
        return False
    key = tok.split("=", 1)[0]
    return key in REPO_OVERRIDE_ENV_VARS


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


def _commit_triggers_staging(toks: list[str], sub_idx: int) -> bool:
    """`commit` subcommand 以降の引数を見て、working tree を取り込む形式
    (-a / --all / -am 系 / -o / --only / -i / --include / `--` / pathspec)
    が含まれるか判定する。"""
    n = len(toks)
    j = sub_idx + 1
    expect_val = False
    while j < n:
        u = toks[j]
        if u in SEPARATORS:
            break
        if expect_val:
            expect_val = False
        elif u == "--":
            return True
        elif u in PATHSPEC_MODE_FLAGS:
            return True
        elif u == "--all":
            return True
        elif u in COMMIT_VALUE_FLAGS:
            expect_val = True
        elif u.startswith("--"):
            pass
        elif u.startswith("-") and _short_cluster_has_a(u):
            return True
        elif u.startswith("-"):
            pass
        else:
            return True
        j += 1
    return False


def _classify(command: str) -> int:
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lex.whitespace_split = True
    try:
        toks = list(lex)
    except ValueError:
        return 2

    # Phase 1: コマンド全体を走査し、各 git invocation の (subcommand, index,
    # has_repo_override) を抽出する。env override (`GIT_DIR=...` 等) は次の
    # simple command にのみ effect する POSIX セマンティクスを尊重し、
    # SEPARATORS や他の command で消費される。
    invocations: list[tuple[str, int, bool]] = []
    n = len(toks)
    i = 0
    pending_env_override = False
    while i < n:
        tok = toks[i]
        if tok in SEPARATORS:
            pending_env_override = False
            i += 1
            continue
        if _is_env_assignment(tok):
            if _is_repo_override_env(tok):
                pending_env_override = True
            i += 1
            continue
        if tok != "git":
            pending_env_override = False
            i += 1
            continue
        result = _find_subcommand_after_git(toks, i)
        if result is None:
            pending_env_override = False
            i += 1
            continue
        sub_idx, sub, has_override = result
        if pending_env_override:
            has_override = True
        pending_env_override = False
        invocations.append((sub, sub_idx, has_override))
        i = sub_idx + 1

    # Phase 2: invocation 列を解析。`add` / `stage` で repo override がないもの
    # は cwd repo の staging trigger としてフラグ立て (後続の cwd commit で
    # 取り込まれる)。最初の cwd commit invocation を見つけたら、その commit
    # の引数を見て staging trigger を判定し、結論を返す。
    cwd_add_seen = False
    for sub, sub_idx, has_override in invocations:
        if sub in ("add", "stage"):
            if not has_override:
                cwd_add_seen = True
            continue
        if sub != "commit":
            continue
        if has_override:
            # 別 repo に対する commit → cwd repo を silently lint しないよう
            # skip を要求する (bash 側で exit 3 を受け取って exit 0 で抜ける)。
            return 3
        # cwd repo に対する commit。同 invocation 内の `-a` / pathspec 等で
        # 引き起こされる staging を見る。先行 cwd add があれば既に staging
        # trigger 確定。どちらか true なら HAS_STAGING。
        if cwd_add_seen or _commit_triggers_staging(toks, sub_idx):
            return 0
        return 1
    # commit subcommand を一度も見つけなかった (初期 regex が quoted 文字列
    # 等に誤マッチしたケース)。
    return 4


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(2)
    sys.exit(_classify(sys.argv[1]))
