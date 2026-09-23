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

    10  免除する (免除条件を全て満たす)
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

1. コマンドが改行 (LF / CR) を含まず、mutating な commit invocation (``--dry-run`` /
   ``--help`` / ``-h`` を除く) が 1 つ以上あり、最後の mutating な commit invocation より前に
   ``cd <path>`` と ``git add`` / ``git commit`` 以外の simple command が無く、
   それらの git invocation 全ての対象 dir を静的に解決できる (解決規則は
   ``resolve_commit_target_dirs`` を参照。判定後・commit 前に対象 dir や repo
   レイアウトを差し替える前段コマンドを排除するため)。認識した mutating な commit
   invocation の件数が、parse-commit-command.py が検出する件数と一致する
2. 各対象 dir の canonical 実パスが、いずれかの許可ルート配下にある
3. 各対象 dir で実行した ``git rev-parse --git-common-dir`` の canonical 実パスが、
   いずれかの許可ルート配下にある。common-dir 直下の ``refs`` / ``objects`` /
   ``HEAD`` / ``packed-refs`` / ``logs`` と、worktree 固有 git dir の ``HEAD`` /
   ``index`` の実体と、HEAD が指す branch ref の格納先も許可ルート配下にある
   (``dir_is_within_roots`` を参照)
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

# simple command の先頭 word にあると、後続の word を command として実行しうる、または
# shell の cwd / 環境の解決を静的に追えなくする shell keyword / builtin / 透過 wrapper。
# commit invocation を隠しうるため、コマンド内のどの位置にあっても解決不能とする。
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

# common-dir 直下で、実体が許可ルート配下にあることを要求する entry。
COMMON_DIR_ENTRIES: tuple[str, ...] = ("refs", "objects", "HEAD", "packed-refs", "logs")

# worktree 固有 git dir が common-dir と異なる場合に、同じ条件を要求する entry。
WORKTREE_GIT_DIR_ENTRIES: tuple[str, ...] = ("HEAD", "index")

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

_PARSER_PATH = Path(__file__).resolve().parent / "parse-commit-command.py"


def _load_commit_parser() -> ModuleType:
    """commit invocation の検出 (``_collect_invocations``) と commit 引数の解釈
    (``_commit_is_non_mutating``) を parse-commit-command.py と共有する。"""
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


def _has_newline(command: str) -> bool:
    """改行 (LF / CR) を quote の内外・行継続か否かを問わず 1 文字でも含むか。

    行末の escape された ``\\\\`` を行継続と誤認するような正規化の食い違いで、別
    コマンドを 1 つに結合して commit を隠す経路を塞ぐため、改行を含むコマンドは
    免除しない (複数行の commit メッセージは ``-F <file>`` で渡す)。
    """
    return "\n" in command or "\r" in command


def _has_unresolvable_syntax(command: str) -> bool:
    """静的解決を妨げる構文を含むか (quote 文脈を追跡する文字 walk)。

    - quote 外の ``(`` / ``)`` / ``{`` / ``}`` (subshell・brace group・プロセス置換 等)
    - quote 外・double quote 内のバッククォートと ``$(`` (コマンド置換)
    - quote 外の ``<`` / ``>`` (redirection 演算子全般。``>>`` / ``<>`` / ``>&`` /
      ``<&`` / ``&>`` / ``>|`` / heredoc ``<<`` / here-string ``<<<`` と数字 fd 前置を
      含む。fd 番号とパス末尾の数字を区別できず、``>/dev/null git commit`` のように
      redirection が先行する commit を parser と異なる形で解析しうるため、コマンド内の
      どの位置にあっても解決不能とする)
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
        elif c in "<>":
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


def _skip_env_assignments(tokens: list[str]) -> int | None:
    """先頭の env assignment 列を読み飛ばした位置を返す。

    解決を変える変数 (``UNRESOLVABLE_ASSIGNMENT_NAMES``) への代入があれば ``None``。
    """
    position = 0
    while position < len(tokens):
        word = _normalize_word(tokens[position])
        if not _ENV_ASSIGN_RE.match(word):
            break
        if word.split("=", 1)[0] in UNRESOLVABLE_ASSIGNMENT_NAMES:
            return None
        position += 1
    return position


def _git_subcommand_index(values: list[str], git_index: int) -> int:
    """``git`` token の位置から global option を読み飛ばし、subcommand の位置を返す。

    option の walk は parse-commit-command.py の commit invocation 検出と同じ規則。
    subcommand が無ければ -1。
    """
    cursor = git_index + 1
    while cursor < len(values):
        option = values[cursor]
        if option in GIT_GLOBAL_VALUE_FLAGS:
            cursor += 2
            continue
        if option.startswith("-"):
            cursor += 1
            continue
        return cursor
    return -1


def _is_git_word(word: str) -> bool:
    return word == "git" or word.endswith("/git")


def _resolve_git_target(
    tokens: list[str], values: list[str], git_index: int, current_dir: str
) -> tuple[str, int] | None:
    """git invocation の ``-C`` を順に解決し、(対象 dir, subcommand の位置) を返す。

    ``--git-dir`` / ``--work-tree`` / ``-C<path>`` の連結形・静的でない ``-C`` の値・
    subcommand 不在は ``None``。
    """
    target = current_dir
    cursor = git_index + 1
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
        return target, cursor
    return None


def _parser_mutating_commit_count(parser: ModuleType, command: str) -> int | None:
    """parse-commit-command.py が検出する mutating な commit invocation の件数を返す。

    parser が解析前に結論を出す形 (substitution / wrapper / トークン化失敗等) は
    ``None``。
    """
    collected = parser._collect_invocations(command)
    if isinstance(collected, int):
        return None
    toks, invocations = collected
    return sum(
        1
        for subcommand, subcommand_index, _has_override in invocations
        if subcommand == "commit"
        and not parser._commit_is_non_mutating(toks, subcommand_index)
    )


def resolve_commit_target_dirs(command: str, base_dir: str) -> list[str] | None:
    """最後の mutating な commit invocation までの各 git invocation (``git add`` /
    ``git commit``) の対象 dir (canonical 実パス) を出現順に返す。

    呼び出し側はその全てに免除条件を要求する。mutating な commit invocation が無い、
    または規則どおりに解決できない場合は ``None`` を返す。静的解決の規則 (これ以外の
    形は解決不能):

    - 元のコマンド文字列が改行 (LF / CR) を含まないこと (``_has_newline``。行継続の
      正規化・分割より前に判定する)
    - ``_has_unresolvable_syntax`` が検出する構文 (subshell / brace group /
      コマンド置換 / プロセス置換 / redirection / heredoc / コメント 等) を、
      コマンド内のどの位置にも含まないこと
    - 本関数が認識した mutating な commit invocation の件数が、parse-commit-command.py
      (``_collect_invocations`` と ``_commit_is_non_mutating``) が検出する件数と一致
      すること (parser と本関数の解析が食い違い、免除判定が一部の commit を見落とす
      経路を塞ぐ)
    - 先頭の simple command から最後の mutating な commit invocation までの区切りが
      ``&&`` だけであること (``;`` / 改行は cd が実行時に失敗しても commit が元の
      cwd で実行されるため、``||`` / ``|`` / ``&`` は cd の効果が commit に及ぶかを
      静的に確定できないため解決不能)
    - 最後の mutating な commit invocation より前の simple command は、次のいずれか
      であること。それ以外 (判定時点のファイルシステム状態を commit 前に変えうる
      任意のコマンド。対象 dir の削除・移動・symlink 化や ``git config`` /
      ``git init`` 等による repo レイアウトの変更を含む) があれば解決不能:

      1. ``cd <path>`` (引数ちょうど 1 個)
      2. subcommand が ``add`` または ``commit`` の git invocation (その対象 dir も
         戻り値に含め、commit と同じ免除条件を要求する)

      最後の mutating な commit invocation より後ろの simple command は、次の規則を
      除き判定に影響しない
    - commit invocation を隠しうる先頭 word (shell keyword / ``eval`` / ``exec`` /
      ``command`` / ``builtin`` / ``env`` / ``sudo`` 等。``UNRESOLVABLE_COMMAND_WORDS``)
      の simple command は、コマンド内のどの位置にあっても解決不能
    - env assignment のみの simple command は上記のいずれにも当たらない。git / cd
      直前の env assignment のうち ``GIT_DIR`` / ``GIT_WORK_TREE`` /
      ``GIT_INDEX_FILE`` / ``GIT_COMMON_DIR`` / ``PWD`` / ``CDPATH`` への代入は
      解決不能
    - git invocation の global option で対象を切り替えるものは ``-C <path>`` だけを
      受け付ける (複数指定時は git と同じく順に相対解決する)。``--git-dir`` /
      ``--work-tree`` (および ``=`` 形式)、``-C<path>`` の連結形は解決不能
    - ``cd`` / ``-C`` の <path> は ``_static_path_value`` が受け付ける静的な文字列で
      あること (変数展開・先頭の ``~``・glob 文字・バックスラッシュ・``-`` 始まり・
      ``..`` 要素は解決不能)
    - ``cd`` の相対パスは CDPATH の影響を受けない ``./`` 始まりに限る。``-C`` の
      相対パスは直前までに解決した dir を基準に解決する
    - 各段階で既存ディレクトリとして canonical 化できること
    """
    # 改行 (LF / CR) は正規化・分割より前に、元のコマンド文字列で判定する。
    if _has_newline(command):
        return None
    parser = _load_commit_parser()
    expected_commit_count = _parser_mutating_commit_count(parser, command)
    if expected_commit_count is None:
        return None

    if _has_unresolvable_syntax(command):
        return None
    current_dir = _canonical_dir(base_dir)
    if current_dir is None:
        return None

    # redirection は _has_unresolvable_syntax で解決不能にしているため、元のコマンドを
    # そのまま分割する (パス解決にも元の token を使う)。
    segments, separators = _split_segments(command)
    tokenized = [_tokenize(segment) for segment in segments]

    # 1 周目: 最後の mutating な commit invocation の位置を特定する。
    last_commit_index = -1
    commit_count = 0
    for index, tokens in enumerate(tokenized):
        position = _skip_env_assignments(tokens)
        if position is None:
            return None
        if position >= len(tokens):
            continue
        values = [_normalize_word(token) for token in tokens]
        if values[position] in UNRESOLVABLE_COMMAND_WORDS:
            return None
        if not _is_git_word(values[position]):
            continue
        subcommand_index = _git_subcommand_index(values, position)
        if subcommand_index < 0 or values[subcommand_index] != "commit":
            continue
        if parser._commit_is_non_mutating(values, subcommand_index):
            continue
        last_commit_index = index
        commit_count += 1
    if last_commit_index < 0 or commit_count != expected_commit_count:
        return None

    # 2 周目: 最後の mutating な commit invocation までの simple command を解決する。
    targets: list[str] = []
    for tokens in tokenized[: last_commit_index + 1]:
        if not tokens:
            continue
        position = _skip_env_assignments(tokens)
        if position is None or position >= len(tokens):
            return None
        values = [_normalize_word(token) for token in tokens]
        word = values[position]
        if word == "cd":
            if len(tokens) != position + 2:
                return None
            value = _static_path_value(tokens[position + 1])
            if value is None or not (value.startswith("/") or value.startswith("./")):
                return None
            resolved_dir = _resolve_path_from(current_dir, value)
            if resolved_dir is None:
                return None
            current_dir = resolved_dir
            continue
        if not _is_git_word(word):
            return None
        resolved = _resolve_git_target(tokens, values, position, current_dir)
        if resolved is None:
            return None
        target, subcommand_index = resolved
        if values[subcommand_index] not in ("add", "commit"):
            return None
        targets.append(target)

    for separator in separators[:last_commit_index]:
        if separator != "&&":
            return None
    return targets


def _is_within_any_root(path: str, roots: list[str]) -> bool:
    return any(is_within_root(path, root) for root in roots)


def _git_entry_is_within_roots(path: str, roots: list[str]) -> bool:
    """canonical 化した git dir 直下の entry ``path`` が存在しないか、実体が許可ルート
    配下にあるか。

    ディレクトリ (symlink 経由を含む) は canonical 実パスで判定する。ファイルは
    それ自体が symlink (リンク切れを含む) なら False、そうでなければ親 dir の
    canonical 実パスで判定する。
    """
    if os.path.isdir(path):
        canonical = _canonical_dir(path)
        return canonical is not None and _is_within_any_root(canonical, roots)
    if os.path.islink(path):
        return False
    if not os.path.exists(path):
        return True
    parent = _canonical_dir(os.path.dirname(path))
    return parent is not None and _is_within_any_root(parent, roots)


def _canonical_git_dir(path: str, target_dir: str) -> str | None:
    if not path:
        return None
    if not path.startswith("/"):
        path = f"{target_dir}/{path}"
    return _canonical_dir(path)


def dir_is_within_roots(target_dir: str, roots: list[str]) -> bool:
    """次の全てを満たすか。

    - ``target_dir`` がいずれかの許可ルート配下にある
    - ``target_dir`` で実行した ``git rev-parse --git-common-dir`` の canonical 実パスが、
      いずれかの許可ルート配下にある
    - common-dir 直下の ``refs`` / ``objects`` / ``HEAD`` / ``packed-refs`` / ``logs``
      のうち存在するものの実体が、いずれかの許可ルート配下にある
      (``_git_entry_is_within_roots``)
    - ``git rev-parse --git-dir`` (worktree 固有 dir) が common-dir と異なる場合は、
      その直下の ``HEAD`` / ``index`` についても同じ条件を満たす
    - HEAD が指す branch ref の格納先が許可ルート配下にある
      (``_head_ref_is_within_roots``)

    git dir 内部の symlink でルート外 repo の ref / object を更新する経路を塞ぐ。
    rev-parse の出力が相対パスなら ``target_dir`` 基準で解決する。rev-parse は hook
    プロセスの環境を継承して実行する (実際の commit と同じ解決結果を得るため)。
    git repo でない・rev-parse が失敗した場合は False。
    """
    if not _is_within_any_root(target_dir, roots):
        return False
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir", "--git-dir"],
            cwd=target_dir,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    if result.returncode != 0:
        return False
    lines = result.stdout.rstrip("\n").split("\n")
    if len(lines) != 2:
        return False
    canonical_common_dir = _canonical_git_dir(lines[0], target_dir)
    canonical_git_dir = _canonical_git_dir(lines[1], target_dir)
    if canonical_common_dir is None or canonical_git_dir is None:
        return False
    if not _is_within_any_root(canonical_common_dir, roots):
        return False
    for entry in COMMON_DIR_ENTRIES:
        if not _git_entry_is_within_roots(f"{canonical_common_dir}/{entry}", roots):
            return False
    if canonical_git_dir != canonical_common_dir:
        for entry in WORKTREE_GIT_DIR_ENTRIES:
            if not _git_entry_is_within_roots(f"{canonical_git_dir}/{entry}", roots):
                return False
    return _head_ref_is_within_roots(target_dir, canonical_common_dir, roots)


def _head_ref_is_within_roots(
    target_dir: str, canonical_common_dir: str, roots: list[str]
) -> bool:
    """commit が更新する branch ref の格納先が許可ルート配下にあるか。

    ``target_dir`` で ``git symbolic-ref -q HEAD`` を実行して ref 名 (例
    ``refs/heads/master``) を得る。終了コード 1 (detached HEAD) なら ref 更新が無い
    ので True。それ以外の失敗・``refs/`` で始まらない ref 名・``..`` 要素や改行を含む
    ref 名は False。ref ファイル (``<canonical_common_dir>/<ref 名>``) 自体が symlink
    なら False。そうでなければ、その親ディレクトリ (未作成なら存在する最も近い祖先。
    途中の entry がリンク切れの symlink やディレクトリ以外なら False) の canonical
    実パスが許可ルート配下であることを要求する (``refs/heads`` 等の入れ子の symlink で
    ルート外 repo の branch を更新する経路を塞ぐため)。objects / logs 配下の深い階層の
    symlink は branch を動かさないため検査しない。
    """
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"],
            cwd=target_dir,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    ref_name = result.stdout.rstrip("\n")
    if result.returncode == 1 and not ref_name:
        return True
    if result.returncode != 0:
        return False
    if not ref_name.startswith("refs/") or "\n" in ref_name:
        return False
    if "/../" in f"/{ref_name}/":
        return False
    ref_path = f"{canonical_common_dir}/{ref_name}"
    if os.path.islink(ref_path):
        return False
    parent = os.path.dirname(ref_path)
    while not os.path.isdir(parent):
        if os.path.islink(parent) or os.path.exists(parent):
            return False
        next_parent = os.path.dirname(parent)
        if next_parent == parent:
            return False
        parent = next_parent
    canonical_parent = _canonical_dir(parent)
    return canonical_parent is not None and _is_within_any_root(canonical_parent, roots)


def commits_only_to_isolated_roots(
    command: str, base_dir: str, env_value: str | None
) -> bool:
    """``resolve_commit_target_dirs`` が返した全対象 dir (git add / git commit) が
    免除条件を満たすときだけ True を返す。

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
