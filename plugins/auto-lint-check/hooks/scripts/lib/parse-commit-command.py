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


# `$(cat <<'DELIM' ... DELIM)` または `$(cat <<"DELIM" ... DELIM)` を検出する
# 正規表現。indent-strip 変種 (`<<-`) にも対応する。
#
# bash の heredoc セマンティクスでは、delimiter を quote (`'EOF'` / `"EOF"`)
# した場合、本文内の `$(...)`, `` `...` ``, `$VAR` 等の expansion は一切行わ
# れず、`cat` は本文を verbatim に echo する。よって `$(cat <<'EOF' ... EOF)`
# の substitution 結果は事実上の静的文字列リテラルであり、bypass 経路には
# ならない。 → parser の fail-closed 対象 (`$(` 検出 → exit 3) から外す。
#
# 一方 unquoted delimiter (`<<EOF`) は本文内で expansion が走るため
# (`<<EOF\n$(git commit -am bypass)\nEOF` で実際に commit が走る)、対象から
# 外したままにする (= 後段の `$(` チェックで fail close される)。
#
# 重要: 本文の matching は naive な ``.*?`` ではなく **本文中に閉じ delimiter
# 行が現れないことを negative lookahead で明示** している。これを怠ると
# ``$(cat <<'EOF'\nfake\nEOF\nrm -rf /\nEOF\n)`` のように複数の閉じ delimiter
# 候補を持つ入力で、 ``.*?`` の non-greedy が最終的に最後の `EOF` まで拡張
# されてしまい、bash 的には「heredoc は最初の `EOF` で終了、その後 `rm -rf /`
# が subshell 内で実行される」入力を hook 側だけ 1 個の heredoc とみなして
# `''` に置換してしまう bypass 経路ができる。bash の「delimiter 行 (= 行頭
# から delimiter のみ) の最初の出現で heredoc は終端する」セマンティクスを
# regex で忠実に表現するため、negative lookahead で「次が closing line
# (`\n DELIM (line end | `)`))) でないこと」を 1 文字ずつ確認している。
#
# 空本文 (``$(cat <<'EOF'\nEOF\n)``) も対応するため、本文部分は optional
# group にしている。
_HEREDOC_CAT_RE = re.compile(
    r"\$\("                                # $(
    r"\s*cat\s+"                           # cat (前後 whitespace 許容)
    r"<<-?"                                # << または <<- (indent-strip variant)
    r"(['\"])"                             # 開始 quote (group 1)
    r"([A-Za-z_]\w*)"                      # delimiter 名 (group 2)
    r"\1"                                  # 終了 quote (group 1 と一致)
    r"[ \t]*\n"                            # opening line 終端
    r"(?:"                                 # 本文行 (空可)
    r"  (?:"                               # 本文 1 文字
    r"    (?![ \t]*\2[ \t]*(?:\n|\)|$))"   # この時点で「行頭が closing delim」ではない
    r"    (?!\n[ \t]*\2[ \t]*(?:\n|\)|$))" # この時点で「次行が closing delim 行」ではない
    r"    [\s\S]"                          # 任意 1 文字 (改行含む)
    r"  )*"
    r"  \n"                                # 本文は改行で終端 (closing delim を行頭から match させるため)
    r")?"
    r"[ \t]*\2[ \t]*"                      # closing delim 行 (leading whitespace は <<- 用に許容)
    r"\n?"                                 # closing 後の改行 (省略可: 入力末尾の場合)
    r"[ \t\n]*"                            # `)` までの whitespace
    r"\)",                                 # 閉じ )
    re.DOTALL | re.VERBOSE,
)


def _strip_safe_heredocs(command: str) -> str:
    """`$(cat <<'DELIM' ... DELIM)` (quoted delimiter のみ) を空文字列リテラル
    ``''`` に置換する。

    Claude Code が複数行 commit message を渡すために常用する
    ``git commit -m "$(cat <<'EOF' ... EOF)"`` パターンを parser の
    fail-closed (`$(` 検出 → exit 3) から救済するための前処理。詳細な安全性
    の論拠は ``_HEREDOC_CAT_RE`` の docstring を参照。

    置換結果が ``''`` (空) で十分なのは、parser が ``-m`` の値文字列の内容を
    一切参照しないため。shlex は ``"''"`` を ``''`` という 2 文字 token として
    取り込み、 ``_commit_triggers_staging`` 等は flag 識別だけ行う。
    """
    return _HEREDOC_CAT_RE.sub("''", command)


def _normalize_command(command: str) -> str:
    """hook script から渡された raw command を shlex tokenize 可能な形に整形する。

    順序が重要 (heredoc は real newline に依存するため最初に処理):

    1. 安全な heredoc (`$(cat <<'DELIM' ... DELIM)`) を空文字列に除去
    2. line continuation ``\\<newline>`` を space に変換 (bash 継続行を 1 行展開)
    3. real newline を ``;`` に変換 (shlex は newline を separator として扱わない)

    bash 側 (block-commit-lint.sh / post-commit-lint.sh) はこの関数に依存
    して raw command を渡してくる前提。bash 側で先に改行を ``;`` に潰すと
    heredoc 構造が壊れて step 1 が機能しなくなるため、両 hook の正規化は
    本関数に集約してある。
    """
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
        if has_override or sticky_cd:
            # 別 repo に対する commit (`-C` / `--git-dir` / `--work-tree` /
            # `GIT_DIR=` 等) または同一 Bash 内で先行 `cd` で cwd を切り替えた
            # 状態での commit。本 hook は元の cwd を見るため、いずれも
            # silent に間違った repo を lint する経路になる。fail closed (deny)
            # を要求する (bash 側で exit 3 を受け取って emit_deny する)。
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
        return 5
    # 実 commit (非変更 mode でない git commit) が無かった (例:
    # `echo "git commit"` / `xargs git commit` / quoted 文字列のみ /
    # `git commit --dry-run` のみ)。
    return 4


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(2)
    sys.exit(_classify(sys.argv[1]))
