#!/usr/bin/env python3
"""block-commit-lint.sh から呼び出される隔離ルート免除の判定器。

repo override (`git -C <dir> commit` / `cd <dir> && git commit` 等) を伴う commit
のうち、利用者が env ``CLAUDE_ISOLATED_GIT_ROOTS`` で明示した隔離ルート配下の repo
だけを対象とするものを、repo override deny から免除してよいかを判定する。免除した
commit は lint しない (隔離ルートは使い捨ての検証用 repo を置く場所であり、lint 対象の
プロジェクトではないため)。

呼び出し形式::

    python3 check-isolated-commit-target.py <command> <base_dir>

- ``<command>``: hook が受け取った Bash コマンド文字列 (raw のまま)
- ``<base_dir>``: hook プロセスの cwd (commit invocation の対象 dir の初期値)

判定結果は exit code で返す:

    10  免除する (全 commit invocation の対象が隔離ルート配下)
    11  免除しない

免除の exit code を 0 にしないのは、Python の正常終了 (0) や SyntaxError /
ImportError / 未捕捉例外 (1) と区別し、想定外の終了を必ず「免除しない」側に倒す
ため。呼び出し側は 10 のときだけ免除し、それ以外は従来どおり deny する。

許可ルート:

- ``CLAUDE_ISOLATED_GIT_ROOTS`` は PATH と同じコロン区切りの絶対パス列
- 空文字 entry・相対パス entry・存在しないディレクトリの entry は無視する
- 有効な entry は ``os.path.realpath`` で canonical 実パスに変換して比較に使う
- 未設定・空、または有効な entry が 1 つも無い場合は免除しない

免除条件 (全て満たす場合のみ免除):

1. コマンド内に mutating な commit invocation (``--dry-run`` / ``--help`` / ``-h``
   を除く) が 1 つ以上あり、その全てについて対象 dir を静的に解決できる
   (解決規則は ``resolve_commit_target_dirs`` を参照)
2. 各対象 dir の canonical 実パスが、いずれかの許可ルート配下にある
3. 各対象 dir で実行した ``git rev-parse --git-common-dir`` の canonical 実パスが、
   いずれかの許可ルート配下にある
4. hook プロセスの環境に ``GIT_DIR`` / ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE`` /
   ``GIT_COMMON_DIR`` が設定されていない (コマンド内の再代入で commit 先が変わり
   うるため)

「配下」はパス境界での前方一致で判定し、ルート自身との一致も配下とみなす
(``/a/b`` は ``/a/b`` と ``/a/b/c`` を含み、``/a/bc`` を含まない)。判定基準は
git-guardrails plugin の commit hook と同じにする。

fail-closed: 解析・パス解決・git 呼び出しのいずれかが失敗した場合、または判定に
必要な情報が静的に確定しない場合は「免除しない」を返す。
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

EXEMPT_EXIT_CODE = 10
NOT_EXEMPT_EXIT_CODE = 11

ISOLATED_ROOTS_ENV = "CLAUDE_ISOLATED_GIT_ROOTS"

# commit 先 repo の解決を変える環境変数。コマンド内での代入も hook 環境での設定も
# 免除しない理由になる。
REPO_ENV_NAMES: frozenset[str] = frozenset(
    {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"}
)

# コマンド内で代入されると cd / commit 先の解決を静的に追えなくなる変数。
UNRESOLVABLE_ASSIGNMENT_NAMES: frozenset[str] = REPO_ENV_NAMES | {"PWD", "CDPATH"}

# segment の先頭 word にあると、shell の cwd / 環境 / 実行コマンドの解決を静的に
# 追えなくなる shell keyword / builtin / 透過 wrapper。
UNRESOLVABLE_COMMAND_WORDS: frozenset[str] = frozenset(
    {
        "!", "time", "if", "then", "else", "elif", "fi", "while", "until", "do",
        "done", "for", "in", "case", "esac", "select", "function", "coproc",
        "[[", "]]",
        "pushd", "popd", "export", "declare", "typeset", "readonly", "unset",
        "local", "source", ".", "eval", "exec",
        "builtin", "command", "trap", "alias", "unalias", "shopt", "enable",
        "hash", "set",
        "env", "sudo", "doas", "nice", "ionice", "timeout", "chrt", "stdbuf",
    }
)

# commit 対象を切り替える global option のうち、静的解決の対象外とするもの。
UNRESOLVABLE_REPO_OPTIONS: frozenset[str] = frozenset({"--git-dir", "--work-tree"})

# git の global option で値を別 token に取るもの (parse-commit-command.py と同じ集合)。
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

# `2>&1` / `&>file` 等の redirection。redirection 内の `&` を segment 区切りと誤認
# しないよう、分割前に空白へ置換する (git-guardrails の commit hook と同じ規則)。
_REDIRECTION_RE = re.compile(r"[0-9]?(&>>|&>|>>|>&|<&|<<<|<<|<>)[ \t]*[A-Za-z0-9_./=+@:-]*")

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

_PARSER_PATH = Path(__file__).resolve().parent / "parse-commit-command.py"


def _load_commit_parser() -> ModuleType:
    """commit 引数の解釈 (non-mutating 判定) を parse-commit-command.py と共有する。"""
    spec = importlib.util.spec_from_file_location("parse_commit_command", _PARSER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {_PARSER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_dir(path: str) -> str | None:
    """絶対パス ``path`` が既存ディレクトリなら canonical 実パスを返す。"""
    if not path.startswith("/") or "\n" in path:
        return None
    if not os.path.isdir(path):
        return None
    resolved = os.path.realpath(path)
    if not os.path.isdir(resolved):
        return None
    return resolved


def load_isolated_roots(env_value: str | None) -> list[str]:
    """env 値から有効な許可ルートの canonical 実パス一覧を返す。

    空文字・相対パス・存在しないディレクトリの entry は無視する。有効な entry が
    無ければ空リストを返す。
    """
    if not env_value:
        return []
    roots: list[str] = []
    for entry in env_value.split(":"):
        canonical = _canonical_dir(entry)
        if canonical is not None:
            roots.append(canonical)
    return roots


def is_within_root(path: str, root: str) -> bool:
    """canonical 実パス ``path`` が ``root`` 自身または配下 (パス境界で前方一致) か。

    ``root`` が ``/`` の場合は全ての絶対パスを配下とみなす。
    """
    if not path.startswith("/") or not root.startswith("/"):
        return False
    if root == "/":
        return True
    return path == root or path.startswith(root + "/")


def _has_unresolvable_syntax(command: str) -> bool:
    """静的解決を妨げる構文を含むか (quote 文脈を追跡する文字 walk)。

    - quote 外の ``(`` / ``)`` / ``{`` / ``}`` (subshell・brace group・プロセス置換 等)
    - quote 外・double quote 内のバッククォートと ``$(`` (コマンド置換)
    - quote 外の ``<<`` (heredoc / here-string)
    - quote 外の ``$'`` / ``$"`` (ANSI-C / locale quoting)
    - quote 外の ``#`` (コメント)
    - quote 外の CR / VT / FF
    - 閉じていない quote
    """
    i = 0
    n = len(command)
    in_squote = False
    in_dquote = False
    while i < n:
        c = command[i]
        nc = command[i + 1] if i + 1 < n else ""
        if in_squote:
            if c == "'":
                in_squote = False
            i += 1
            continue
        if in_dquote:
            if c == "\\":
                i += 2
                continue
            if c == "`" or (c == "$" and nc == "("):
                return True
            if c == '"':
                in_dquote = False
            i += 1
            continue
        if c == "\\":
            i += 2
            continue
        if c == "'":
            in_squote = True
        elif c == '"':
            in_dquote = True
        elif c in "`(){}#\r\v\f":
            return True
        elif c == "$" and nc in ("'", '"'):
            return True
        elif c == "<" and nc == "<":
            return True
        i += 1
    return in_squote or in_dquote


def _split_segments(command: str) -> tuple[list[str], list[str]]:
    """quote 外の ``;`` / 改行 / ``&&`` / ``&`` / ``||`` / ``|`` で segment に分割する。

    戻り値は (segments, separators)。``separators[i]`` は ``segments[i]`` の直後の
    区切り (改行は ``;`` として扱う)。
    """
    segments: list[str] = []
    separators: list[str] = []
    current: list[str] = []
    i = 0
    n = len(command)
    in_squote = False
    in_dquote = False

    def flush(separator: str) -> None:
        segments.append("".join(current))
        separators.append(separator)
        current.clear()

    while i < n:
        c = command[i]
        nc = command[i + 1] if i + 1 < n else ""
        if in_squote:
            current.append(c)
            if c == "'":
                in_squote = False
            i += 1
            continue
        if in_dquote:
            if c == "\\" and nc:
                current.append(c + nc)
                i += 2
                continue
            current.append(c)
            if c == '"':
                in_dquote = False
            i += 1
            continue
        if c == "\\" and nc:
            current.append(c + nc)
            i += 2
            continue
        if c == "'":
            in_squote = True
        elif c == '"':
            in_dquote = True
        elif c in (";", "\n"):
            flush(";")
            i += 1
            continue
        elif c in ("&", "|"):
            if nc == c:
                flush(c + c)
                i += 2
            else:
                flush(c)
                i += 1
            continue
        current.append(c)
        i += 1
    segments.append("".join(current))
    separators.append("")
    return segments, separators


def _tokenize(segment: str) -> list[str]:
    """segment を quote 外の空白 (space / tab) で raw token に分割する (quote は残す)。"""
    tokens: list[str] = []
    current: list[str] = []
    in_squote = False
    in_dquote = False
    i = 0
    n = len(segment)
    while i < n:
        c = segment[i]
        nc = segment[i + 1] if i + 1 < n else ""
        if in_squote:
            current.append(c)
            if c == "'":
                in_squote = False
        elif in_dquote:
            if c == "\\" and nc:
                current.append(c + nc)
                i += 2
                continue
            current.append(c)
            if c == '"':
                in_dquote = False
        elif c == "\\" and nc:
            current.append(c + nc)
            i += 2
            continue
        elif c == "'":
            in_squote = True
            current.append(c)
        elif c == '"':
            in_dquote = True
            current.append(c)
        elif c in (" ", "\t"):
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(c)
        i += 1
    if current:
        tokens.append("".join(current))
    return tokens


def _normalize_word(raw: str) -> str:
    """shell が 1 word を組み立てる際の quote / escape 除去を反映した文字列を返す。"""
    result: list[str] = []
    in_squote = False
    in_dquote = False
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        nc = raw[i + 1] if i + 1 < n else ""
        if in_squote:
            if c == "'":
                in_squote = False
            else:
                result.append(c)
        elif in_dquote:
            if c == '"':
                in_dquote = False
            elif c == "\\" and nc in ("$", "`", '"', "\\"):
                result.append(nc)
                i += 2
                continue
            else:
                result.append(c)
        elif c == "'":
            in_squote = True
        elif c == '"':
            in_dquote = True
        elif c == "\\" and nc:
            result.append(nc)
            i += 2
            continue
        else:
            result.append(c)
        i += 1
    return "".join(result)


def _static_path_value(raw: str) -> str | None:
    """token が静的なパス文字列なら quote を外した値を返す。

    受け付ける形は、token 全体が quote されていない文字列・token 全体が 1 組の
    single quote・token 全体が 1 組の double quote (内部に ``$`` / バッククォート /
    ``\\`` を含まない) のいずれか。値が空・``-`` 始まり・``~`` 始まり・glob 文字
    (``*`` / ``?`` / ``[``)・改行・``..`` 要素を含む場合は ``None``。
    """
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        value = raw[1:-1]
        if "'" in value:
            return None
    elif len(raw) >= 2 and raw[0] == raw[-1] == '"':
        value = raw[1:-1]
        if any(ch in value for ch in ('"', "$", "`", "\\")):
            return None
    else:
        if any(ch in raw for ch in ("'", '"', "$", "`", "\\")):
            return None
        value = raw
    if not value or value[0] in ("-", "~"):
        return None
    if any(ch in value for ch in ("*", "?", "[", "\n")):
        return None
    if "/../" in f"/{value}/":
        return None
    return value


def _resolve_path_from(base: str, value: str) -> str | None:
    if value.startswith("/"):
        return _canonical_dir(value)
    return _canonical_dir(f"{base}/{value}")


def resolve_commit_target_dirs(command: str, base_dir: str) -> list[str] | None:
    """mutating な commit invocation ごとの対象 dir (canonical 実パス) を出現順に返す。

    commit invocation が無い、または 1 つでも静的に解決できない場合は ``None`` を
    返す。静的解決の規則 (これ以外の形は解決不能):

    - ``_has_unresolvable_syntax`` が検出する構文 (subshell / brace group /
      コマンド置換 / プロセス置換 / heredoc / コメント 等) を含まないこと
    - 先頭の simple command から最後の commit invocation までの区切りが ``&&`` と
      ``;`` (改行を含む) だけであること
    - commit invocation より前の simple command で cwd を変えるのは ``cd <path>``
      (引数ちょうど 1 個) だけであること。先頭 word が ``cd`` 以外の cwd / 環境を
      変えうる shell keyword・builtin・透過 wrapper (``pushd`` / ``popd`` /
      ``export`` / ``declare`` / ``source`` / ``eval`` / ``exec`` / ``builtin`` /
      ``command`` / ``env`` / ``sudo`` 等) の simple command、および ``GIT_DIR`` /
      ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE`` / ``GIT_COMMON_DIR`` / ``PWD`` /
      ``CDPATH`` への代入があれば解決不能
    - commit invocation の global option で対象を切り替えるものは ``-C <path>``
      だけを受け付ける (複数指定時は git と同じく順に相対解決する)。
      ``--git-dir`` / ``--work-tree`` (および ``=`` 形式)、``-C<path>`` の連結形は
      解決不能
    - ``cd`` / ``-C`` の <path> は ``_static_path_value`` が受け付ける静的な文字列で
      あること (変数展開・先頭の ``~``・glob 文字・バックスラッシュ・``-`` 始まり・
      ``..`` 要素は解決不能)
    - ``cd`` の相対パスは CDPATH の影響を受けない ``./`` 始まりに限る。``-C`` の
      相対パスは直前までに解決した dir を基準に解決する
    - 各段階で既存ディレクトリとして canonical 化できること
    """
    command = command.replace("\r\n", "\n").replace("\\\n", " ")
    if _has_unresolvable_syntax(command):
        return None
    current_dir = _canonical_dir(base_dir)
    if current_dir is None:
        return None
    parser = _load_commit_parser()

    segments, separators = _split_segments(_REDIRECTION_RE.sub(" ", command))
    targets: list[str] = []
    last_commit_index = -1
    for index, segment in enumerate(segments):
        tokens = _tokenize(segment)
        position = 0
        while position < len(tokens) and _ENV_ASSIGN_RE.match(
            _normalize_word(tokens[position])
        ):
            name = _normalize_word(tokens[position]).split("=", 1)[0]
            if name in UNRESOLVABLE_ASSIGNMENT_NAMES:
                return None
            position += 1
        if position >= len(tokens):
            continue
        word = _normalize_word(tokens[position])
        if word == "cd":
            if len(tokens) != position + 2:
                return None
            value = _static_path_value(tokens[position + 1])
            if value is None or not (value.startswith("/") or value.startswith("./")):
                return None
            current_dir = _resolve_path_from(current_dir, value)
            if current_dir is None:
                return None
            continue
        if word in UNRESOLVABLE_COMMAND_WORDS:
            return None
        if word != "git" and not word.endswith("/git"):
            continue

        values = [_normalize_word(token) for token in tokens]
        target = current_dir
        cursor = position + 1
        subcommand_index = -1
        while cursor < len(tokens):
            option = values[cursor]
            if option == "-C":
                if cursor + 1 >= len(tokens):
                    return None
                value = _static_path_value(tokens[cursor + 1])
                if value is None:
                    return None
                resolved = _resolve_path_from(target, value)
                if resolved is None:
                    return None
                target = resolved
                cursor += 2
                continue
            if option.startswith("-C") or option.split("=", 1)[0] in (
                UNRESOLVABLE_REPO_OPTIONS
            ):
                return None
            if option in GIT_GLOBAL_VALUE_FLAGS:
                cursor += 2
                continue
            if option.startswith("-"):
                cursor += 1
                continue
            subcommand_index = cursor
            break
        if subcommand_index < 0 or values[subcommand_index] != "commit":
            continue
        if parser._commit_is_non_mutating(values, subcommand_index):
            continue
        targets.append(target)
        last_commit_index = index

    if not targets:
        return None
    for separator in separators[:last_commit_index]:
        if separator not in ("&&", ";"):
            return None
    return targets


def dir_is_within_roots(target_dir: str, roots: list[str]) -> bool:
    """``target_dir`` と、そこで実行した ``git rev-parse --git-common-dir`` の
    canonical 実パスが、どちらもいずれかの許可ルート配下にあるか。

    ``--git-common-dir`` が相対パスで返る場合は ``target_dir`` 基準で解決する。
    rev-parse は hook プロセスの環境を継承して実行する (実際の commit と同じ
    解決結果を得るため)。git repo でない・rev-parse が失敗した場合は False。
    """
    if not any(is_within_root(target_dir, root) for root in roots):
        return False
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=target_dir,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    if result.returncode != 0:
        return False
    common_dir = result.stdout.rstrip("\n")
    if not common_dir:
        return False
    if not common_dir.startswith("/"):
        common_dir = f"{target_dir}/{common_dir}"
    canonical_common_dir = _canonical_dir(common_dir)
    if canonical_common_dir is None:
        return False
    return any(is_within_root(canonical_common_dir, root) for root in roots)


def commits_only_to_isolated_roots(
    command: str, base_dir: str, env_value: str | None
) -> bool:
    """全 commit invocation が免除条件を満たすときだけ True を返す。

    ``env_value`` が未設定・空ならコマンドを解析せずに False を返す。
    """
    if not env_value:
        return False
    if any(name in os.environ for name in REPO_ENV_NAMES):
        return False
    roots = load_isolated_roots(env_value)
    if not roots:
        return False
    targets = resolve_commit_target_dirs(command, base_dir)
    if not targets:
        return False
    return all(dir_is_within_roots(target, roots) for target in targets)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        return NOT_EXEMPT_EXIT_CODE
    command, base_dir = argv[1], argv[2]
    if commits_only_to_isolated_roots(
        command, base_dir, os.environ.get(ISOLATED_ROOTS_ENV)
    ):
        return EXEMPT_EXIT_CODE
    return NOT_EXEMPT_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main(sys.argv))
