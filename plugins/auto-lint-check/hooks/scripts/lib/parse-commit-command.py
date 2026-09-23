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

判定結果は exit code で返す (Python が SyntaxError / ImportError 等で返す 1 と
衝突しないよう、commit ありを意味する正常 return は 5 を使う):

    0  HAS_STAGING (working tree も lint 対象に含めるべき)
    5  commit はあるが staging trigger なし (staged blob のみ lint で十分)
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
from dataclasses import dataclass

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
# - `--pathspec-from-file`: file から pathspec を読み込んで working tree
#   から commit。`--pathspec-from-file=<path>` の = 形式は別途判定。
PATHSPEC_MODE_FLAGS: frozenset[str] = frozenset(
    {"-o", "--only", "-i", "--include", "-p", "--patch", "--interactive",
     "--pathspec-from-file"}
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


# 安全に除去できる heredoc command substitution を検出する正規表現。
#
# === 安全性の三重要件 ===
#
# (1) **delimiter が quote されていること** (`<<'DELIM'` / `<<"DELIM"`)
#     bash は quoted delimiter の heredoc 本文を verbatim に扱い、本文内の
#     `$(...)`, `` `...` ``, `$VAR` 等の expansion を一切行わない。よって
#     `cat` が本文を echo した結果は事実上の静的文字列。 unquoted delimiter
#     (`<<EOF`) は本文で expansion が走るため (`<<EOF\n$(git commit -am bypass)
#     \nEOF` で実際に commit が走る) 対象から外している (= 後段の `$(` deny に
#     落ちる)。
#
# (2) **substitution 全体が double-quoted string ``"..."`` の中に置かれている
#     こと** (substitution の前後に `"` を要求する)
#     ``"..."`` は word splitting を抑制し、 substitution 結果を 1 つの word
#     として扱わせる。これは bypass を塞ぐ最低限の構文条件 (例えば
#     ``$(cat <<'EOF'\nrm -rf /\nEOF\n) somecmd`` のように word splitting で
#     複数 token に分裂してから shell に解釈される形を排除する) だが、
#     これ単独では不十分: ``"$(echo git)" commit -m msg`` のように word
#     splitting されなくても、 結果の 1 word が command position や subcommand
#     position に置かれれば bash は ``git commit -m msg`` として実行する。
#
# (3) **substitution が value-taking flag (`-m` / `--message`) の引数位置に
#     置かれていること**
#     codex review 2 回目の指摘で必要性が判明した最終条件。 git commit の
#     flag parser が「 ``-m`` の直後は値」と確定的に扱う位置に substitution が
#     あれば、 結果が command/subcommand token に昇格する経路は構文的に
#     存在しなくなる (flag parser のスコープを抜けて command 構造に流れる
#     ことが無い)。 Claude Code が複数行 commit message に使う標準形
#     ``git commit -m "$(cat <<'EOF' ... EOF)"`` はこの形に常に合致する。
#     ``--message`` 長形 (`--message ` 空白区切り および `--message=` 形式)
#     も同等に安全。
#
# === bypass attempt と対応 ===
#
# - ``"$(cat <<'EOF'\ngit\nEOF\n)" commit -m msg`` (substitution が command
#   position): bash は ``git commit -m msg`` を実行。本 regex は ``-m`` /
#   ``--message`` prefix が無いので match せず → ``$(`` deny。
# - ``git "$(cat <<'EOF'\ncommit\nEOF\n)" -m msg`` (substitution が
#   subcommand position): 上と同様、 prefix 不在で deny。
# - ``"$(cat <<'EOF'\nfake\nEOF\nrm -rf /\nEOF\n)"`` (複数の閉じ delimiter
#   候補): body matching で内部の閉じ delim 行を negative lookahead が検出
#   して match に失敗 → deny。 body matching の詳細は次節参照。
#
# === 本文 (body) matching に関する詳細 ===
#
# 本文の matching は naive な ``.*?`` ではなく **本文中に閉じ delimiter 行が
# 現れないことを negative lookahead で明示** している。これを怠ると
# ``-m "$(cat <<'EOF'\nfake\nEOF\nrm -rf /\nEOF\n)"`` のように複数の閉じ
# delimiter 候補を持つ入力で、 ``.*?`` の non-greedy が最終的に最後の `EOF`
# まで拡張されてしまい、 bash 的には「heredoc は最初の `EOF` で終了、 その後
# `rm -rf /` が subshell 内で実行される」入力を hook 側だけ 1 個の heredoc
# とみなして `""` に置換してしまう bypass 経路ができる。 bash の「delimiter
# 行 (= 行頭から delimiter のみ) の最初の出現で heredoc は終端する」
# セマンティクスを regex で忠実に表現するため、 negative lookahead で
# 「次が closing line (`\n DELIM (line end | `)`))) でないこと」を 1 文字ずつ
# 確認している。
#
# 空本文 (``-m "$(cat <<'EOF'\nEOF\n)"``) も対応するため、本文部分は optional
# group にしている。
# closing delim の indent 規則は bash 仕様に合わせて分岐:
# - `<<'DELIM'` (dash なし): closing delim は行頭 (column 0) 必須
# - `<<-'DELIM'` (dash あり, indent-strip variant): closing delim 前の leading
#   tab のみ許容 (bash は tab のみ strip、space は strip しない)
#
# regex 内では group 2 で dash の有無をキャプチャし、 conditional pattern
# `(?(2)\\t*|)` で「dash があれば \\t*、 無ければ空」を表現する。これにより
# 本文に偶然 ` EOF` (空白インデント) の行が含まれた場合に regex が誤って
# closing 候補と判定して match に失敗 → false-positive deny する経路を塞ぐ
# (codex review 3 回目指摘の P3 対応)。
_HEREDOC_CAT_RE = re.compile(
    r"("                                       # GROUP 1: value flag prefix (re-emit 用)
    r"  -m[ \t]*"                              #   `-m ` / `-m`(attached)
    r"  |"
    r"  --message[ \t]+"                       #   `--message ` (long form, space)
    r"  |"
    r"  --message[ \t]*=[ \t]*"                #   `--message=` (long form, equals)
    r")"
    r'"[ \t]*'                                 # 囲み " の開始 (word splitting 抑制)
    r"\$\("                                    # $(
    r"\s*cat\s+"                               # cat (前後 whitespace 許容)
    r"<<(-)?"                                  # GROUP 2: dash 有無 ('-' が match なら group participates, 無ければ None)
    r"(['\"])"                                 # GROUP 3: 開始 quote
    r"([A-Za-z_]\w*)"                          # GROUP 4: delimiter 名
    r"\3"                                      # 終了 quote (GROUP 3 と一致)
    r"[ \t]*\n"                                # opening line 終端
    r"(?:"                                     # 本文行 (0 行以上)
    r"  (?!(?(2)\t*|)\4[ \t]*(?:\n|\)|$))"     # この行は closing delim 行ではない (dash なら \\t* 許容、 非 dash は column 0 必須)
    r"  [^\n]*\n"                              # 1 行 (改行で終端)
    r")*"
    r"(?(2)\t*|)\4[ \t]*"                      # closing delim 行 (dash 変種のみ leading tab 許容)
    r"\n?"                                     # closing 後の改行 (省略可: 入力末尾の場合)
    r"[ \t\n]*"                                # `)` までの whitespace
    r"\)"                                      # 閉じ )
    r'[ \t]*"',                                # 囲み " の終了
    re.DOTALL | re.VERBOSE,
)


# 同一コマンド内で `cat` を function として再定義するパターンを検出する。
#
# bash では command name は実行時解決されるため、 ``$(cat <<'EOF' ... EOF)`` の
# `cat` が system cat ではなく command 文字列内で前に定義された function を呼ぶ
# 可能性がある。 例:
#
#     cat() { git add bad.py; }; git commit -m "$(cat <<'EOF'\nmsg\nEOF\n)"
#
# この場合、 hook が whitelist で substitution を空置換すると plain commit と
# 誤判定し、 working tree lint を skip する。 しかし bash が実際に command を
# 実行すると、 substitution 内で function `cat` が呼ばれて `git add bad.py` で
# 副作用 (staging) を起こしてから outer commit が実行され、 lint 未通過の
# bad.py が commit に紛れ込む。
#
# 検出対象は bash の典型的な function 定義構文:
#   - ``cat() { ... }`` (POSIX shell function 定義)
#   - ``function cat { ... }`` / ``function cat() { ... }`` (bash 拡張)
#
# alias (``alias cat=evil``) / PATH manipulation (``PATH=/evil:$PATH``) /
# sourced script 経由の function inheritance は静的検出できないが、 lint hook
# の threat model (= 典型的な bug / sloppiness を防ぐ。 adversarial bypass の
# 完全防御は対象外) では narrow detection で十分。 これらの未対応経路は
# documented limitation として残す。
_CAT_FUNCTION_REDEF_RE = re.compile(
    r"""(?:^|[\s;&|])                       # 行頭 or shell separator の直後
        (?:
            cat\s*\(\s*\)\s*\{              # cat() { ... }
            |function\s+cat\s*(?:\(\s*\)\s*)?\{  # function cat { ... } / function cat() { ... }
        )
    """,
    re.VERBOSE,
)
# 注: function 形にも明示的に開き ``{`` を要求しているのは、 commit message
# 本文に "fix function cat regression" のような自然文が含まれるケースで
# `function\s+cat\b` だけだと false-positive を起こすため。 開き ``{`` まで
# match 要求することで bash function 定義シンタックスに限定し、 単なる
# 自然文 (function という単語 + cat という単語) を除外する。


def _has_cat_function_redef(command: str) -> bool:
    """同一コマンド内で `cat` を function として再定義しているかを判定する。

    True を返した場合、 ``$(cat <<...)`` の `cat` が system cat ではなく
    定義された function を呼ぶ可能性があるため、 heredoc whitelist の前提
    (= substitution が静的文字列出力に等価) が崩れる。 呼び出し側は strip を
    skip し、 後段の ``$(`` fail-closed deny に委ねるべき。
    """
    return bool(_CAT_FUNCTION_REDEF_RE.search(command))


def _strip_safe_heredocs(command: str) -> str:
    """``-m "$(cat <<'DELIM' ... DELIM)"`` (``-m`` / ``--message`` の value 位置に
    置かれた quoted-delim heredoc substitution) を空文字列リテラル ``""`` に
    置換し、 flag prefix (``-m `` 等) は形を整えて残す。

    Claude Code が複数行 commit message を渡すために常用する
    ``git commit -m "$(cat <<'EOF' ... EOF)"`` パターンを parser の
    fail-closed (`$(` 検出 → exit 3) から救済するための前処理。 安全性の
    根拠 (delimiter quoting + surrounding ``"..."`` + ``-m`` value 位置) は
    ``_HEREDOC_CAT_RE`` の docstring を参照。

    置換結果の形を flag prefix の形式に応じて使い分けるのは、shlex が
    attached form ``-m""`` を 1 token ``-m`` に潰してしまう (``""`` は空 quote
    として無視される) ことへの対応:

    - ``-m "$(...)"`` (separator form) / ``-m"$(...)"`` (attached form):
      どちらも separator form ``<flag> ""`` (space + 空 quote) に正規化して
      置換する。 shlex は 2 token ``['-m', '']`` として取り込み、
      ``_commit_triggers_staging`` が ``-m`` を value flag として認識して
      次 token ``''`` を value として消費する。 attached form をそのまま空
      置換 (``-m""``) すると shlex token が ``-m`` 1 個になり、 続く path-spec
      まで value として誤吸収されて HAS_STAGING の検出を取りこぼす経路が
      生まれる (codex review 4 回目指摘の P2)。
    - ``--message="$(...)"`` (equals form): ``--message=""`` の attached 形を
      保つ。 shlex は 1 token ``--message=`` として取り込み、 後段の parser
      は ``--`` prefix で flag 扱い → expect_val を立てずに skip するため、
      続く path-spec が flag value として誤吸収されない (= space を挟むと
      逆に ``--message=`` と ``""`` が 2 token に分かれ、 後者が path-spec
      として誤検出される)。
    """
    def _substitute(match: re.Match[str]) -> str:
        flag = match.group(1)
        if flag.endswith("="):
            # equals form: attached を維持
            return flag + '""'
        # separator / attached form: space-separated に正規化
        return flag.rstrip() + ' ""'

    # 2-pass approach: まず tentative に strip を行い、 その後の文字列に対して
    # cat function redef を検査する。 順序がこの逆だと、 heredoc 本文に
    # ``cat() { ... }`` のような text が含まれた場合 (commit message が
    # function 定義のシンタックスについて言及している場合等) に raw command
    # スキャンが本文 text を構文と誤判定して redef 検出を発火させ、 strip が
    # skip されて legitimate な commit が deny される false-positive 経路が
    # できる (codex review 6 回目指摘の P3)。 tentative strip 後の文字列は
    # 「本文 text を除いた構文構造」を表しているため、 残った部分に redef
    # があれば本物の outer redef 確定、 無ければ body text の偶然マッチに過ぎ
    # ないと判別できる。
    stripped = _HEREDOC_CAT_RE.sub(_substitute, command)
    if _has_cat_function_redef(stripped):
        # outer に cat redef が残っている → whitelist の前提が崩れるので strip
        # を revert し、 後段の ``$(`` fail-closed deny に委ねる。
        return command
    return stripped


# heredoc の delimiter WORD を終える文字 (引用符の外に現れた場合)。
_HEREDOC_WORD_END_CHARS: frozenset[str] = frozenset(" \t\n;&|()<>")


def _read_heredoc_word(command: str, start: int) -> tuple[str | None, bool, int]:
    """heredoc 演算子 (``<<`` / ``<<-``) の直後の ``start`` から delimiter WORD
    を読む。

    戻り値は (引用符と backslash を外した WORD, WORD の一部でも引用符または
    backslash で quote されているか, WORD の直後の index)。WORD が無い場合
    の WORD は ``None``。WORD に行継続 (backslash + 改行)、改行を含む引用符、
    backslash を含む二重引用符、引用符の外の ``$`` が現れる場合も、bash と同じ WORD を読める保証が無いため ``None`` を返す
    (呼び出し側はそのコマンドのすべての heredoc 本文を除去しない)。
    """
    n = len(command)
    i = start
    while i < n and command[i] in " \t":
        i += 1
    word_start = i
    chars: list[str] = []
    quoted = False
    while i < n and command[i] not in _HEREDOC_WORD_END_CHARS:
        ch = command[i]
        if ch in ("'", '"'):
            close = command.find(ch, i + 1)
            if close == -1:
                close = n
            if "\n" in command[i + 1 : close]:
                return None, False, word_start
            if ch == '"' and "\\" in command[i + 1 : close]:
                # 二重引用符内の backslash escape は bash が除去するが、閉じ
                # 引用符の位置判定を含め同じ解釈を再現しないため解決不能とする。
                return None, False, word_start
            quoted = True
            chars.append(command[i + 1 : close])
            i = close + 1
            continue
        if ch == "\\" and i + 1 < n and command[i + 1] == "\n":
            return None, False, word_start
        if ch == "$":
            # ``${...}`` / ``$[...]`` 等の展開は内側の空白を同じ語に含めるが、
            # その範囲を再現しないため解決不能とする。
            return None, False, word_start
        if ch == "\\" and i + 1 < n:
            quoted = True
            chars.append(command[i + 1])
            i += 2
            continue
        chars.append(ch)
        i += 1
    if i == word_start:
        return None, False, i
    return "".join(chars), quoted, min(i, n)


def _find_heredoc_body_end(
    command: str, start: int, word: str, strip_tabs: bool
) -> tuple[int, bool]:
    """本文 1 行目の先頭 ``start`` から、終端行 (``WORD`` と完全一致する行。
    ``strip_tabs`` なら先頭タブを除去して比較) を探す。

    戻り値は (終端行の直後の index, 終端行が見つかったか)。終端行が無ければ
    (``len(command)``, False)。"""
    n = len(command)
    pos = start
    while pos < n:
        newline = command.find("\n", pos)
        line_end = n if newline == -1 else newline
        next_pos = n if newline == -1 else newline + 1
        line = command[pos:line_end]
        if (line.lstrip("\t") if strip_tabs else line) == word:
            return next_pos, True
        pos = next_pos
    return n, False


@dataclass
class _PendingHeredoc:
    """演算子を検出済みで本文をまだ読んでいない heredoc。"""

    word: str
    quoted: bool
    strip_tabs: bool


# heredoc 本文をデータとしてのみ扱う (本文をコマンドとして実行しない)
# command name (basename)。コマンド全体の simple command がすべてこの集合に
# 含まれる場合に限り、heredoc 本文を除去する。
HEREDOC_DATA_ONLY_COMMANDS: frozenset[str] = frozenset(
    {"cd", "cat", "tee", "git", "gh", "echo", "printf", "mkdir", "true"}
)

# ``git`` / ``gh`` は subcommand や起動するエディタによって stdin をシェルへ
# 渡しうる (``git submodule foreach``、``!`` で始まる alias、``gh`` の shell
# alias、stdin を読むよう設定したエディタ等)。そのため、command name の直後
# の語 (subcommand) がこの集合に含まれ、かつ subcommand より前にオプションが
# 無い場合に限り、データ専用とみなす。値が空集合でない subcommand は、stdin
# をメッセージ・入力として読むフラグ (値) のいずれかを伴い、エディタを起動
# するフラグ (``HEREDOC_EDITOR_FLAGS``) を伴わない場合に限る。
HEREDOC_DATA_ONLY_SUBCOMMANDS: dict[str, dict[str, frozenset[str]]] = {
    "git": {
        "commit": frozenset({"-F -", "-F-", "--file=-", "--file -"}),
        "tag": frozenset({"-F -", "-F-", "--file=-", "--file -"}),
        "notes": frozenset({"-F -", "-F-", "--file=-", "--file -"}),
        "hash-object": frozenset(),
        "apply": frozenset(),
    },
    "gh": {
        "pr": frozenset({"--body-file -", "--body-file=-", "-F -"}),
        "issue": frozenset({"--body-file -", "--body-file=-", "-F -"}),
        "release": frozenset({"--notes-file -", "--notes-file=-", "-F -"}),
        "api": frozenset({"--input -", "--input=-"}),
    },
}

# エディタを起動するフラグ。
HEREDOC_EDITOR_FLAGS: frozenset[str] = frozenset({"-e", "--edit"})

# 走査が bash と同じ quote 解釈をしない quoting の開始記号 (ANSI-C quoting
# ``$'...'`` と locale 翻訳 quoting ``$"..."``)。command に含まれる場合は
# heredoc 演算子の検出が bash と食い違いうるため、本文を除去しない。
_UNMODELED_QUOTE_OPENERS: tuple[str, ...] = ("$'", '$"')

# 除去する本文以外の部分に現れた場合に本文除去をやめる構文。コマンド置換
# (``$(`` / backtick) と parameter expansion (``${``) は二重引用符内の入れ子の
# quoting を、here-string (``<<<``) はその語の読み飛ばしを、行継続
# (backslash + 改行) は複数文字の開始記号を分断した場合の文脈を、それぞれ
# 走査が bash と同じように再現しない。
_UNMODELED_OUTSIDE_BODY_MARKERS: tuple[str, ...] = (
    "$(",
    "`",
    "${",
    "<<<",
    "\\\n",
    # extglob のパターン (``shopt -s extglob`` 時は内側の ``<<`` が
    # heredoc 演算子にならない)
    "?(",
    "*(",
    "+(",
    "@(",
    "!(",
)

# 本文除去の対象にする heredoc delimiter (引用符を外した WORD) の形。
_HEREDOC_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# 関数定義 (``function NAME`` / ``NAME ()``)。allowlist 内の名前を再定義して
# stdin を実行させうるため、関数定義を含むコマンドでは本文を除去しない。
_FUNCTION_DEFINITION_RE = re.compile(r"(?:^|[\s;&|(){}])function(?:\s|$)|\(\s*\)")

# この文字の直後にある ``#`` はコメントの開始 (= 語の先頭)。
_COMMENT_START_PRECEDERS: frozenset[str] = frozenset(" \t\n;&|()")

# target が別トークンに分かれている redirection 演算子 (``>`` ``out`` 等)。
_BARE_REDIRECT_RE = re.compile(r"^\d*(?:<<-?|<>|>>|>\||<&|>&|<|>)$")

# 算術コンテキストの開始記号と、その内側で深さを数える (開き, 閉じ) 括弧。
# 開始記号は括弧を 2 個 (``((``) または 1 個 (``$[``) 開く。
_ARITH_OPENERS: tuple[tuple[str, str, str, int], ...] = (
    ("$((", "(", ")", 2),
    ("((", "(", ")", 2),
    ("$[", "[", "]", 1),
)


def _is_backslash_escaped(command: str, i: int) -> bool:
    """``command[i]`` の直前に連続する backslash が奇数個あり、``command[i]``
    が escape されているか判定する。"""
    count = 0
    j = i - 1
    while j >= 0 and command[j] == "\\":
        count += 1
        j -= 1
    return count % 2 == 1


def _is_segment_separator(command: str, i: int) -> bool:
    """``command[i]`` が simple command を区切る文字 (``;`` ``&`` ``|`` ``(``
    ``)`` 改行) か判定する。``2>&1`` / ``&>`` / ``>|`` のように redirection
    の一部として現れる ``&`` / ``|`` と、``|&`` の ``&`` は区切りにしない。"""
    ch = command[i]
    # escape された直前文字 (``\>`` 等) はリテラルであり redirection を作らない。
    prev = (
        command[i - 1] if i > 0 and not _is_backslash_escaped(command, i - 1) else ""
    )
    nxt = command[i + 1] if i + 1 < len(command) else ""
    if ch in ";()\n":
        return True
    if ch == "&":
        return prev not in ("<", ">", "|") and nxt != ">"
    if ch == "|":
        return prev != ">"
    return False


def _simple_command_words(segment: str) -> list[str] | None:
    """simple command の文字列から、command name 以降の語 (command name と
    引数。redirection とその target は除く) を返す。

    代入 (``X=y``)、shell keyword、redirection (target を含む) を読み飛ばした
    最初の語を command name とする。``env`` や透過 wrapper は読み飛ばさず、
    それ自体を command name とする。command name を持たない segment (空、
    代入のみ、keyword のみ、算術コマンド ``(( ... ))``) は空リストを返す。
    トークン化できない場合は ``None``。"""
    try:
        words = shlex.split(segment, comments=False, posix=True)
    except ValueError:
        return None
    if words and words[0].startswith("(("):
        # ``((`` は算術コマンドとして不正な中身のとき bash が入れ子の subshell
        # として実行しうる。算術かどうかを確定できないため解決不能とする。
        return None
    result: list[str] = []
    skip_next = False
    for word in words:
        if skip_next:
            skip_next = False
            continue
        if _is_redirection_token(word):
            skip_next = bool(_BARE_REDIRECT_RE.match(word))
            continue
        if not result and (_is_env_assignment(word) or word in SHELL_KEYWORDS):
            continue
        result.append(word)
    return result


def _simple_command_name(segment: str) -> str | None:
    """simple command の文字列から command name を返す (``_simple_command_words``
    の先頭語)。command name を持たない segment は空文字列、トークン化できない
    場合は ``None``。"""
    words = _simple_command_words(segment)
    if words is None:
        return None
    return words[0] if words else ""


def _is_data_only_segment(segment: str) -> bool:
    """simple command が heredoc 本文をデータとしてのみ扱うか判定する。
    command name を持たない segment は True。command name が解決できない
    場合、相対パス (``./x`` 等) の場合、basename が
    ``HEREDOC_DATA_ONLY_COMMANDS`` に無い場合は False。絶対パス
    (``/bin/cat``) は basename で判定する。basename が
    ``HEREDOC_DATA_ONLY_SUBCOMMANDS`` のキーの場合は、command name の直後の語
    が許可された subcommand であり、その subcommand が要求する stdin 読み込み
    フラグを伴い、エディタ起動フラグを伴わないことも要求する (subcommand
    より前にオプションがある場合も False)。環境変数の代入を前置した simple
    command (``GIT_EDITOR=... git ...`` 等) も False。"""
    if _has_env_assignment_prefix(segment):
        return False
    words = _simple_command_words(segment)
    if words is None:
        return False
    if not words:
        return True
    name = words[0]
    if "/" in name and not name.startswith("/"):
        return False
    basename = name.rsplit("/", 1)[-1]
    if basename not in HEREDOC_DATA_ONLY_COMMANDS:
        return False
    allowed_subcommands = HEREDOC_DATA_ONLY_SUBCOMMANDS.get(basename)
    if allowed_subcommands is None:
        return True
    if len(words) < 2 or words[1] not in allowed_subcommands:
        return False
    stdin_flags = allowed_subcommands[words[1]]
    args = words[2:]
    if any(arg in HEREDOC_EDITOR_FLAGS for arg in args):
        return False
    if not stdin_flags:
        return True
    joined_args = " ".join(args)
    return any(
        flag in args or f" {flag} " in f" {joined_args} " for flag in stdin_flags
    )


def _has_env_assignment_prefix(segment: str) -> bool:
    """simple command が command name の前に環境変数の代入 (``X=y``) を持つか
    判定する。トークン化できない場合は True (保守側)。"""
    try:
        words = shlex.split(segment, comments=False, posix=True)
    except ValueError:
        return True
    for word in words:
        if _is_env_assignment(word):
            return True
        if _is_redirection_token(word) or word in SHELL_KEYWORDS:
            continue
        return False
    return False


def _is_single_trailing_heredoc(
    command: str, consumed_heredocs: list[tuple[_PendingHeredoc, bool, int]]
) -> bool:
    """本文除去の対象になる構造か判定する。

    コマンド中の heredoc がちょうど 1 つで、delimiter が引用符付きの識別子
    (``<<-`` ではない)、かつ終端行がコマンドの最終行 (後ろに空白以外が無い)
    の場合に限り True。終端行より後ろに行が無いため、本文の範囲の推定が
    bash と食い違っても後続のコマンドを除去する経路が生じない。"""
    if len(consumed_heredocs) != 1:
        return False
    heredoc, terminated, end = consumed_heredocs[0]
    return (
        heredoc.quoted
        and not heredoc.strip_tabs
        and _HEREDOC_IDENTIFIER_RE.fullmatch(heredoc.word) is not None
        and terminated
        and command[end:].strip() == ""
    )


def _strip_heredoc_bodies(command: str) -> str:
    """heredoc の本文行と終端行を command から除去し、演算子の行は残す。

    heredoc 本文を読むコマンドがデータとしてのみ扱う場合、本文中に
    ``(git commit)`` のような文字列があっても commit invocation として
    トークン化されないよう、トークン化の前に取り除く。

    契約:

    - 引用符の外にある ``<<WORD`` / ``<<-WORD`` / ``<<'WORD'`` /
      ``<<"WORD"`` を heredoc 演算子として検出する。次の ``<<`` は heredoc
      演算子として扱わない: ``<<<`` (here-string)、算術コンテキスト
      (``((`` ... ``))`` / ``$((`` ... ``))`` / ``$[`` ... ``]``) の内側、
      parameter expansion (``${`` ... ``}``、ネストを追跡) の内側、語の境界
      (行頭・空白・区切り文字の直後) にある ``#`` から行末までのコメントの
      内側。語の途中の ``#`` (``a#b``) はコメントではない
    - 本文を除去するのは、heredoc がコマンド中にちょうど 1 つで、その
      delimiter が引用符付きの識別子 (``<<-`` ではない) で、終端行が
      コマンドの最終行である場合 (``_is_single_trailing_heredoc``) に限る。
      複数の heredoc、引用符なし delimiter、``<<-``、終端行が無い heredoc、
      終端行の後ろに行が続く heredoc は、本文を除去せず command をそのまま
      返す
    - 加えて、コマンド全体 (heredoc 本文を除いた部分) が次をすべて満たす
      必要がある。1 つでも満たさなければ本文を除去せず command をそのまま
      返す
      - すべての simple command の command name (代入・shell keyword・
        redirection を読み飛ばした最初の語) が解決でき、その basename が
        ``HEREDOC_DATA_ONLY_COMMANDS`` に含まれる。command name を持たない
        simple command (代入のみ・算術コマンド等) は許容する。``env`` /
        透過 wrapper / ``eval`` 等の前置語は command name として扱うため
        不成立になる。相対パス (``./x``) の command name も不成立とし、
        絶対パス (``/bin/cat``) は basename で判定する。basename が ``git`` /
        ``gh`` の場合は、直後の語が ``HEREDOC_DATA_ONLY_SUBCOMMANDS`` の
        subcommand であり、stdin を読むフラグを伴いエディタ起動フラグを伴わ
        ないことも要求する (subcommand より前のオプションは不可)。環境変数の
        代入を前置した simple command は不成立
      - プロセス置換 (``<(`` / ``>(``) を含まない
      - 除去する本文以外の部分に、コマンド置換 (``$(`` / backtick)、
        parameter expansion (``${``)、here-string (``<<<``)、行継続
        (backslash + 改行) を含まない
        (``_UNMODELED_OUTSIDE_BODY_MARKERS``。コマンド置換を含むコマンドは
        後段の fail-closed または safe heredoc の除去で扱う)
      - ANSI-C quoting (``$'``) と locale 翻訳 quoting (``$"``) を含まない
      - 引用符なし delimiter の heredoc 本文に行継続 (backslash + 改行) を
        含まない
      - ``((`` で始まる simple command を含まない (算術コマンドか入れ子の
        subshell かを確定できないため)
      - すべての heredoc 演算子の delimiter WORD を読める
      - 関数定義 (``function NAME`` / ``NAME ()``) を含まない
    - 除去する場合、演算子を含む行の次の行から、``WORD`` (引用符を外した
      文字列) と完全一致する行までを本文として除去する。終端行自体も除去
      する。``<<-`` の場合は各行の先頭タブを除去してから終端判定する。
      ``EOFX`` のような部分一致行は終端にしない
    - 走査では、同一行に複数の演算子がある場合 (``cmd <<A <<B``) は演算子
      の出現順に本文を消費し、終端行が見つからない場合は演算子の行より
      後ろの全行を本文とみなす (いずれも除去の対象外と判定するため)
    - 引用符なしの ``WORD`` の本文は bash が展開する (``$(...)`` / backtick
      が実行される) ため、本文に ``$(`` または backtick を含む場合は除去
      せず残し、後段の substitution fail-closed 判定 (exit 3) に委ねる

    ``_strip_safe_heredocs`` より前に呼ぶこと (``-m "$(cat <<'EOF' ... EOF)"``
    は二重引用符内にあり演算子として検出しないため、この関数を通過しても
    ``_HEREDOC_CAT_RE`` で後から除去できる。逆順では ``_HEREDOC_CAT_RE`` が
    別の heredoc の本文データに一致して本文境界を壊しうる)。
    """
    out: list[str] = []
    # 演算子を検出済みで、本文をまだ読んでいない heredoc。本文は演算子の行の
    # 改行の後から出現順に読む。
    pending: list[_PendingHeredoc] = []
    found_heredoc = False
    has_process_substitution = False
    # 引用符なし delimiter の本文に行継続がある場合、bash は行を連結してから
    # 終端判定するため、終端行の位置を物理行で判定できない。
    has_body_line_continuation = False
    # delimiter WORD を読めなかった heredoc 演算子がある場合、その本文の範囲が
    # 決まらず、以降の本文境界もすべて bash と食い違いうる。
    has_unresolved_heredoc_word = False
    # 本文を消費した heredoc の (演算子情報, 終端行が見つかったか, 本文終端の
    # 直後の index)。
    consumed_heredocs: list[tuple[_PendingHeredoc, bool, int]] = []
    # heredoc 本文とコメントを除いた simple command の文字列。
    segments: list[str] = []
    # 現在の simple command の開始 index。
    segment_start = 0
    quote: str | None = None
    # 算術コンテキストの内側で未対応の括弧の数と、数える (開き, 閉じ) 括弧。
    # 0 より大きい間は ``<<`` を shift 演算子とみなし、heredoc 演算子として
    # 扱わない。
    arith_depth = 0
    arith_open_char = ""
    arith_close_char = ""
    # parameter expansion (``${`` ... ``}``) の内側で未対応の ``${`` の数。
    param_depth = 0
    n = len(command)
    i = 0
    while i < n:
        ch = command[i]
        if quote == "'":
            out.append(ch)
            if ch == "'":
                quote = None
            i += 1
            continue
        if ch == "\\":
            out.append(command[i : i + 2])
            i += 2
            continue
        if quote == '"':
            out.append(ch)
            if ch == '"':
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if arith_depth:
            if ch == arith_open_char:
                arith_depth += 1
            elif ch == arith_close_char:
                arith_depth -= 1
            out.append(ch)
            i += 1
            continue
        if param_depth:
            if command.startswith("${", i):
                param_depth += 1
                out.append("${")
                i += 2
                continue
            if ch == "}":
                param_depth -= 1
            out.append(ch)
            i += 1
            continue
        arith_opener = next(
            (op for op in _ARITH_OPENERS if command.startswith(op[0], i)), None
        )
        if arith_opener is not None:
            opener, arith_open_char, arith_close_char, arith_depth = arith_opener
            out.append(opener)
            i += len(opener)
            continue
        if command.startswith("${", i):
            param_depth = 1
            out.append("${")
            i += 2
            continue
        if ch == "#" and (
            i == 0
            or (
                command[i - 1] in _COMMENT_START_PRECEDERS
                and not _is_backslash_escaped(command, i - 1)
            )
        ):
            line_end = command.find("\n", i)
            if line_end == -1:
                line_end = n
            segments.append(command[segment_start:i])
            segment_start = line_end
            out.append(command[i:line_end])
            i = line_end
            continue
        if ch in ("<", ">") and command.startswith("(", i + 1):
            has_process_substitution = True
        if command.startswith("<<<", i):
            out.append("<<<")
            i += 3
            continue
        if command.startswith("<<", i):
            j = i + 2
            strip_tabs = command.startswith("-", j)
            if strip_tabs:
                j += 1
            word, quoted, j = _read_heredoc_word(command, j)
            if word is None:
                has_unresolved_heredoc_word = True
            else:
                found_heredoc = True
                pending.append(_PendingHeredoc(word, quoted, strip_tabs))
            out.append(command[i:j])
            i = j
            continue
        if _is_segment_separator(command, i):
            segments.append(command[segment_start:i])
            segment_start = i + 1
        if ch == "\n" and pending:
            # 本文除去後は演算子の行の末尾と後続行が改行 1 つで隣接する。正規化で
            # 改行が ``;`` に置き換わったとき、行末の記号 (``&&`` / ``)`` 等) と
            # 結合して区切りと認識されないトークンにならないよう、前後を空白で
            # 挟んで独立させる。
            out.append(f" {ch} ")
            i += 1
            for heredoc in pending:
                end, terminated = _find_heredoc_body_end(
                    command, i, heredoc.word, heredoc.strip_tabs
                )
                consumed_heredocs.append((heredoc, terminated, end))
                body = command[i:end]
                if not heredoc.quoted and "\\\n" in body:
                    has_body_line_continuation = True
                if not heredoc.quoted and ("$(" in body or "`" in body):
                    out.append(body)
                i = end
            pending = []
            segment_start = i
            continue
        out.append(ch)
        i += 1
    segments.append(command[segment_start:n])
    if not found_heredoc:
        return command
    if not _is_single_trailing_heredoc(command, consumed_heredocs):
        return command
    if (
        has_process_substitution
        or any(marker in "".join(out) for marker in _UNMODELED_OUTSIDE_BODY_MARKERS)
        or has_body_line_continuation
        or has_unresolved_heredoc_word
        or _FUNCTION_DEFINITION_RE.search("".join(out)) is not None
        or any(quote in command for quote in _UNMODELED_QUOTE_OPENERS)
        or not all(_is_data_only_segment(segment) for segment in segments)
    ):
        return command
    return "".join(out)


def _normalize_command(command: str) -> str:
    """hook script から渡された raw command を shlex tokenize 可能な形に整形する。

    順序が重要 (heredoc は real newline に依存するため step 1-3 を先に処理):

    1. CRLF (``\\r\\n``) を LF (``\\n``) に正規化 (Windows / WSL クライアント
       からの input でも heredoc 検出と shlex tokenize が正しく動くように)
    2. heredoc の本文行を除去 (``_strip_heredoc_bodies``)。二重引用符内の
       安全な heredoc は演算子として検出しないため、この段階では残る。
       step 3 より先に行うのは、step 3 の正規表現が別の heredoc の本文データ
       に一致して本文境界を壊さないようにするため
    3. 安全な heredoc (`$(cat <<'DELIM' ... DELIM)`) を空文字列に除去
    4. line continuation ``\\<newline>`` を space に変換 (bash 継続行を 1 行展開)
    5. real newline を ``;`` に変換 (shlex は newline を separator として扱わない)

    bash 側 (block-commit-lint.sh / post-commit-lint.sh) はこの関数に依存
    して raw command を渡してくる前提。bash 側で先に改行を ``;`` に潰すと
    heredoc 構造が壊れて step 2-3 が機能しなくなるため、両 hook の正規化は
    本関数に集約してある。
    """
    command = command.replace("\r\n", "\n")
    command = _strip_heredoc_bodies(command)
    command = _strip_safe_heredocs(command)
    command = command.replace("\\\n", " ")
    command = command.replace("\n", ";")
    return command


def _is_repo_override_env(tok: str) -> bool:
    if "=" not in tok:
        return False
    key = tok.split("=", 1)[0]
    return key in REPO_OVERRIDE_ENV_VARS


# attached value を取る short option の prefix。`-madd` (= `-m add`) のように
# flag 直後に value が連結する形式では、後続文字は value の一部であり short
# cluster の auto-stage 判定対象にしない。`-S` も attached value (`-Skeyid`)
# を取る。
_SHORT_VALUE_PREFIXES: frozenset[str] = frozenset({"m", "F", "t", "c", "C", "S"})

# 別 command を実行する transparent wrapper。`command git commit` / `exec git
# commit` / `sudo git commit` / `nice git commit` / `timeout 5 git commit` 等。
# wrapper の引数仕様 (`sudo` options, `timeout` の DURATION 等) は多岐にわたり
# 静的解析が複雑なため、silent bypass を避けるため出現時は fail closed (exit 3)。
COMMAND_WRAPPERS: frozenset[str] = frozenset(
    {"command", "exec", "sudo", "doas", "nice", "ionice", "timeout", "chrt", "stdbuf"}
)


def _short_cluster_has_a(t: str) -> bool:
    """`-am` / `-ma` / `-aS` のような short cluster で `a` を含むものを
    auto-stage と判定。`--amend` は `--` 開始なので除外、`-m` は `a` を含まない。
    `-madd` / `-Fpath` 等の value attached 形式は cluster 扱いせず除外。"""
    if len(t) < 2 or t[0] != "-" or t[1] == "-":
        return False
    if t[1] in _SHORT_VALUE_PREFIXES:
        return False
    return "a" in t[1:]


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
        elif u.startswith("--pathspec-from-file="):
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
    # 1) raw command を tokenize 可能な形に正規化する (heredoc 除去 → 行継続展開
    # → 改行 → ;)。詳細は _normalize_command の docstring を参照。
    command = _normalize_command(command)

    # 2) Backtick command substitution は shell が文字列内部で commit を実行する
    # シンタックスだが、shlex は backtick を quote / substitution として扱わ
    # ないため内部の `git commit` が token 列に現れず parser を bypass する。
    # `$(...)` も同様。silent bypass を避けるため、これらの substitution を
    # 含むコマンドは fail closed (exit 3) する。
    #
    # `_normalize_command` が `$(cat <<'EOF' ... EOF)` (quoted delimiter) を
    # 事前に除去しているため、ここに残る `$(...)` / backtick は本当に
    # 「中で何かが実行されうる」substitution に限られる (= fail close 対象)。
    if "`" in command or "$(" in command:
        return 3

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
    # has_repo_override_at_invocation) を抽出する。`git` は command position (コマンド先頭、
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
    sticky_cd = False
    while i < n:
        tok = toks[i]
        if tok in SEPARATORS:
            pending_env_override = False
            at_command_position = True
            i += 1
            continue
        if at_command_position and tok == "cd":
            # `cd dir && git commit` は cwd を切り替えてから commit を実行。
            # 本 hook は元の cwd repo を見るため、cd 先 repo の lint を素通り
            # する silent bypass 経路になる。ただし `cd docs && grep "git
            # commit" .` のような非 commit コマンドで即 fail closed すると
            # false-positive deny になるので、sticky flag を立て、後で
            # 実 commit invocation を見つけた場合のみ repo override と同等
            # の exit 3 を返す。
            sticky_cd = True
            i += 1
            continue
        if at_command_position and tok in COMMAND_WRAPPERS:
            # command / exec / sudo / nice / timeout 等の透過 wrapper。
            # 後続の git commit が silent bypass する経路になるため fail
            # closed (exit 3) で deny。利用者は wrapper を外して別 Bash
            # 呼び出しで commit すれば通る。
            return 3
        if at_command_position and tok == "env":
            # `env [VAR=val ...] git commit` の env wrapper。env 自身は
            # builtin で、続く env-var assignment 列と command 名を渡す。
            # 後続を env-var prefix と同じく扱う (pending_env_override
            # ロジックに乗せる) ことで `env GIT_DIR=... git commit` のような
            # repo override も正しく検出される。
            # env -i / env -u / env -- などフラグ付き呼び出しは環境を完全に
            # 操作し commit 挙動が不定 (= cwd や HEAD と異なる経路で commit
            # が走る可能性) なので silent bypass を避けるため fail closed。
            i += 1
            if i < n and toks[i].startswith("-"):
                return 3
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
        # `cd` は以後の simple command の cwd を変えるが、既に抽出済みの invocation
        # へ遡及してはならない。各 invocation を見つけた時点の state に焼き込む。
        if sticky_cd:
            has_override = True
        pending_env_override = False
        invocations.append((sub, sub_idx, has_override))
        i = sub_idx + 1
        at_command_position = False

    # Phase 2: invocation 列を解析。`add` / `stage` で repo override がないもの
    # は cwd repo の staging trigger としてフラグ立て (後続の cwd commit で
    # 取り込まれる)。最初の cwd commit invocation を見つけたら、その commit
    # の引数を見て staging trigger を判定し、結論を返す。
    #
    # repo override commit は先に全件確認する。先行する cwd commit が `-a` 等で
    # return 0 しても、後続の `cd ... && git commit` を見落としてはならない。
    for sub, sub_idx, has_override in invocations:
        if (
            sub == "commit"
            and has_override
            and not _commit_is_non_mutating(toks, sub_idx)
        ):
            # 別 repo に対する commit (`-C` / `--git-dir` / `--work-tree` /
            # `GIT_DIR=` 等) または同一 Bash 内で先行 `cd` で cwd を切り替えた
            # 状態での commit。本 hook は元の cwd を見るため、いずれも
            # silent に間違った repo を lint する経路になる。fail closed (deny)
            # を要求する (bash 側で exit 3 を受け取って emit_deny する)。
            return 3

    cwd_add_seen = False
    saw_cwd_commit = False
    for sub, sub_idx, _has_override in invocations:
        if sub in ("add", "stage"):
            if not _has_override:
                cwd_add_seen = True
            continue
        if sub != "commit":
            continue
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
        return 5
    # 実 commit (非変更 mode でない git commit) が無かった (例:
    # `echo "git commit"` / `xargs git commit` / quoted 文字列のみ /
    # `git commit --dry-run` のみ)。
    return 4


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(2)
    sys.exit(_classify(sys.argv[1]))
