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

「配下」はパス境界での前方一致で判定し、ルート自身との一致も配下とみなす
(``/a/b`` は ``/a/b`` と ``/a/b/c`` を含み、``/a/bc`` を含まない)。判定基準は
git-guardrails plugin の commit hook と同じにする。

fail-closed: 解析・パス解決・git 呼び出しのいずれかが失敗した場合、または判定に
必要な情報が静的に確定しない場合は「免除しない」を返す。
"""

from __future__ import annotations

import os
import sys

EXEMPT_EXIT_CODE = 10
NOT_EXEMPT_EXIT_CODE = 11

ISOLATED_ROOTS_ENV = "CLAUDE_ISOLATED_GIT_ROOTS"


def load_isolated_roots(env_value: str | None) -> list[str]:
    """env 値から有効な許可ルートの canonical 実パス一覧を返す。

    空文字・相対パス・存在しないディレクトリの entry は無視する。有効な entry が
    無ければ空リストを返す。
    """
    return []


def is_within_root(path: str, root: str) -> bool:
    """canonical 実パス ``path`` が ``root`` 自身または配下 (パス境界で前方一致) か。

    ``root`` が ``/`` の場合は全ての絶対パスを配下とみなす。
    """
    return False


def resolve_commit_target_dirs(command: str, base_dir: str) -> list[str] | None:
    """mutating な commit invocation ごとの対象 dir (canonical 実パス) を出現順に返す。

    commit invocation が無い、または 1 つでも静的に解決できない場合は ``None`` を
    返す。静的解決の規則 (これ以外の形は解決不能):

    - コマンド全体に subshell (``(``)、brace group (``{``)、コマンド置換
      (``$(`` / バッククォート)、プロセス置換 (``<(`` / ``>(``) が無いこと
    - 先頭の simple command から最後の commit invocation までの区切りが ``&&`` と
      ``;`` (改行を含む) だけであること
    - commit invocation より前の simple command のうち cwd / repo 解決に影響する
      ものは ``cd <path>`` (引数ちょうど 1 個) だけであること。``cd`` 単独・
      ``cd -``・``pushd`` / ``popd``・``export`` / ``declare`` / ``typeset`` /
      ``readonly`` / ``unset``・``source`` / ``.`` / ``eval`` / ``exec``、transparent
      wrapper (``command`` / ``sudo`` / ``env`` 等)、および ``GIT_DIR`` /
      ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE`` / ``GIT_COMMON_DIR`` の assignment が
      あれば解決不能
    - commit invocation の global option で対象を切り替えるものは ``-C <path>``
      だけを受け付ける (複数指定時は git と同じく順に相対解決する)。
      ``--git-dir`` / ``--work-tree`` (および ``=`` 形式)、invocation 直前の上記
      env assignment は解決不能
    - ``cd`` / ``-C`` の <path> は quote を外した結果が静的な文字列であること。
      変数展開 (``$``)、先頭の ``~``、glob 文字 (``*`` / ``?`` / ``[``)、
      バックスラッシュ、``-`` 始まりを含む場合は解決不能
    - 相対パスは直前までに解決した dir を基準に解決し、各段階で既存ディレクトリ
      として canonical 化できること
    """
    return None


def dir_is_within_roots(target_dir: str, roots: list[str]) -> bool:
    """``target_dir`` と、そこで実行した ``git rev-parse --git-common-dir`` の
    canonical 実パスが、どちらもいずれかの許可ルート配下にあるか。

    ``--git-common-dir`` が相対パスで返る場合は ``target_dir`` 基準で解決する。
    rev-parse は hook プロセスの環境を継承して実行する (実際の commit と同じ
    解決結果を得るため)。git repo でない・rev-parse が失敗した場合は False。
    """
    return False


def commits_only_to_isolated_roots(
    command: str, base_dir: str, env_value: str | None
) -> bool:
    """全 commit invocation が免除条件を満たすときだけ True を返す。

    ``env_value`` が未設定・空ならコマンドを解析せずに False を返す。
    """
    return False


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
