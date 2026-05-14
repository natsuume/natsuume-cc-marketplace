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
        "--author",
        "--date",
        "--cleanup",
        "--fixup",
        "--squash",
        "--trailer",
    }
)

# `-S` / `--gpg-sign` は意図的に COMMIT_VALUE_FLAGS に含めない: man git-commit
# によれば value は `-S<keyid>` / `--gpg-sign=<keyid>` のように stuck 形式しか
# 受け付けず space 区切りは不可。VALUE_FLAGS に入れると `git commit -S foo.py`
# の `foo.py` を value として消費して pathspec 検出を取り逃す。

# 以下のフラグが付くと commit は working tree から内容を取り込む:
# - `-o` / `--only`, `-i` / `--include`: 続く pathspec を working tree から
#   合成して commit
# - `-p` / `--patch`, `--interactive`: 対話的に hunk を選択して staging
#   (本 hook の発火後に working tree から index に取り込まれる)
PATHSPEC_MODE_FLAGS: frozenset[str] = frozenset(
    {"-o", "--only", "-i", "--include", "-p", "--patch", "--interactive"}
)

SEPARATORS: frozenset[str] = frozenset({";", "&&", "||", "|", "&", "(", ")"})

# bash の shell keywords / control 構造の prefix。これらの直後は新しい simple
# command の開始位置 (= command position) になる。SEPARATORS と同列に扱う
# ことで `if ... ; then git commit ; fi` の git commit や `time git commit`
# のような形を正しく検出する。
SHELL_KEYWORDS: frozenset[str] = frozenset(
    {
        "if", "then", "else", "elif", "fi",
        "while", "until", "do", "done",
        "for", "in", "case", "esac",
        "time", "!", "{", "}", "function",
    }
)

# `>`, `<`, `>>`, `<<`, `2>`, `2>&1` などの redirection token を識別する。
# shlex は `punctuation_chars` に `<>` を含めないため `>log` / `2>` が単一
# トークンとして残り、これを pathspec と誤認すると無関係な working tree を
# lint してしまう。`commit` 引数解析時に redirection token に到達したら
# 「シェルの redirection 開始 = commit args 終端」として break する。
_REDIRECT_TOKEN_RE = re.compile(r"^\d*[<>]")


def _is_redirection_token(tok: str) -> bool:
    return bool(_REDIRECT_TOKEN_RE.match(tok))


def _is_command_boundary(tok: str) -> bool:
    """新しい simple command の開始位置 (command position) の境界判定。
    SEPARATORS / SHELL_KEYWORDS / redirection を含む。subcommand 検出
    (`_find_subcommand_after_git`) のように「command position に shell
    keyword が現れたら別 command の開始」を扱う場面で使う。"""
    return tok in SEPARATORS or tok in SHELL_KEYWORDS or _is_redirection_token(tok)


def _is_args_boundary(tok: str) -> bool:
    """commit / git の引数解析中の境界判定。SHELL_KEYWORDS は含めない:
    `git commit if` のように pathspec が shell keyword と同名の場合に、
    その引数を pathspec として認識するため (args 位置の `if` は keyword
    としての意味を持たない)。"""
    return tok in SEPARATORS or _is_redirection_token(tok)

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
        # expect_val を boundary より先に判定: global option value が shell
        # keyword と一致するケース (`git -c "in" commit`) でも value を消費。
        if expect_val:
            if pending_override:
                has_override = True
                pending_override = False
            expect_val = False
            j += 1
            continue
        if _is_command_boundary(u):
            return None
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


# `git commit --dry-run` / `--help` / `-h` は実 commit を作らない。検出時は
# skip コードを返して何も lint しない。
NON_MUTATING_COMMIT_FLAGS: frozenset[str] = frozenset({"--dry-run", "--help", "-h"})


def _commit_is_non_mutating(toks: list[str], sub_idx: int) -> bool:
    """commit invocation の引数に `--dry-run` / `--help` / `-h` が含まれるか
    判定する。これらが含まれる場合、実際の commit は走らないため lint も不要。

    VALUE_FLAGS の値や `--` 以降の pathspec として `--dry-run` 等が現れる
    ケース (`git commit -m --dry-run` / `git commit -- --help`) では flag
    扱いしないよう、_commit_triggers_staging と同じ value-skip / `--` 処理
    を行う。"""
    n = len(toks)
    j = sub_idx + 1
    expect_val = False
    after_dash_dash = False
    while j < n:
        u = toks[j]
        # expect_val を boundary より先に判定: option value が shell keyword
        # / separator と一致するケース (`-m "in" path.py` 等) でも value を
        # 正しく消費する。
        if expect_val:
            expect_val = False
            j += 1
            continue
        if _is_args_boundary(u):
            break
        if after_dash_dash:
            j += 1
            continue
        if u == "--":
            after_dash_dash = True
        elif u in NON_MUTATING_COMMIT_FLAGS:
            return True
        elif u in COMMIT_VALUE_FLAGS:
            expect_val = True
        j += 1
    return False


def _commit_triggers_staging(toks: list[str], sub_idx: int) -> bool:
    """`commit` subcommand 以降の引数を見て、working tree を取り込む形式
    (-a / --all / -am 系 / -o / --only / -i / --include / -p / --patch /
    --interactive / `--` / pathspec) が含まれるか判定する。"""
    n = len(toks)
    j = sub_idx + 1
    expect_val = False
    while j < n:
        u = toks[j]
        # expect_val を boundary より先に判定。詳細は _commit_is_non_mutating の
        # 同パターンのコメント参照。
        if expect_val:
            expect_val = False
            j += 1
            continue
        if _is_args_boundary(u):
            break
        if u == "--":
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
    # punctuation_chars に `()` を含める: `(git commit ...)` の `(` `)` を独立
    # トークン化し subshell 内の commit を検出するため。`<>` は含めない:
    # `2>&1` / `>log` の redirection 構造が崩れるので、redirection は 1 トークン
    # で受け取り `_is_redirection_token` で break する。
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lex.whitespace_split = True
    try:
        toks = list(lex)
    except ValueError:
        return 2

    # Phase 1: コマンド全体を走査し、各 git invocation の (subcommand, index,
    # has_repo_override) を抽出する。`git` は command position (コマンド先頭、
    # SEPARATORS の直後、または env-var assignment 列の直後) でのみ意味を持つ
    # ため、`echo git commit` のような他コマンドの引数として現れる `git` は
    # 無視する。env override (`GIT_DIR=...` 等) は次の simple command にのみ
    # effect する POSIX セマンティクス。
    invocations: list[tuple[str, int, bool]] = []
    n = len(toks)
    i = 0
    pending_env_override = False
    # at_command_position: 現在の token がシェルの simple command 先頭
    # (= command name) に相当するか。SEPARATORS 直後、または env-var
    # assignment 列の途中まで True を維持する。non-env / non-git の token を
    # 1 つ消費した時点で False に倒し、以降のトークンは command の引数として
    # 解釈する (= `echo git commit` の "git" を invocation と誤認しない)。
    at_command_position = True
    while i < n:
        tok = toks[i]
        if tok in SEPARATORS:
            pending_env_override = False
            at_command_position = True
            i += 1
            continue
        if at_command_position and tok in SHELL_KEYWORDS:
            # shell keywords は bash 構文上 command position に出現した時のみ
            # keyword (例: `time git commit` の `time`、`if foo; then git ...`
            # の `if` / `then`)。command position で消費し、次の token を再び
            # command name として扱うことで `git` を正しく検出する。引数として
            # 現れた keyword (例: `echo time git commit` の `time`) は通常
            # トークン扱いになり、誤って command position を立てない。
            i += 1
            continue
        if at_command_position and _is_env_assignment(tok):
            if _is_repo_override_env(tok):
                pending_env_override = True
            i += 1
            continue
        if not at_command_position:
            i += 1
            continue
        if tok != "git":
            pending_env_override = False
            at_command_position = False
            i += 1
            continue
        result = _find_subcommand_after_git(toks, i)
        if result is None:
            pending_env_override = False
            at_command_position = False
            i += 1
            continue
        sub_idx, sub, has_override = result
        if pending_env_override:
            has_override = True
        pending_env_override = False
        invocations.append((sub, sub_idx, has_override))
        i = sub_idx + 1
        at_command_position = False

    # Phase 2: invocation 列を解析。`add` / `stage` で repo override がないもの
    # は cwd repo の staging trigger としてフラグ立て (後続の cwd commit で
    # 取り込まれる)。最初の cwd commit invocation を見つけたら、その commit
    # の引数を見て staging trigger を判定し、結論を返す。
    cwd_add_seen = False
    saw_cwd_commit = False
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
        if _commit_is_non_mutating(toks, sub_idx):
            # `--dry-run` / `--help` / `-h`: 実 commit は走らない → skip して
            # 次の invocation の解析を続ける。
            continue
        # cwd repo に対する実 commit。先行 cwd add や、commit 自体の `-a` /
        # pathspec / -p などが staging trigger なら HAS_STAGING 確定で即 return。
        if cwd_add_seen or _commit_triggers_staging(toks, sub_idx):
            return 0
        # plain commit (staged blob のみ)。後続に `git add ... && git commit`
        # のような staging trigger 列が来るかもしれないので終了せず、commit 後
        # に index がクリアされる前提で cwd_add_seen を消費する形で続行。
        saw_cwd_commit = True
        cwd_add_seen = False
    # 実 commit が見つかったが staging trigger は無かった → staged blob のみ lint
    if saw_cwd_commit:
        return 1
    # 実 commit (非変更 mode でない git commit) が無かった (例:
    # `echo "git commit"` / `xargs git commit` / quoted 文字列のみ /
    # `git commit --dry-run` のみ)。
    return 4


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(2)
    sys.exit(_classify(sys.argv[1]))
