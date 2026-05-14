#!/usr/bin/env python3
"""block-commit-lint.sh から呼び出される lint plan 構築器。

stdin から NUL 区切りで ``<source>\\t<rel_path>`` 形式のレコードを受け取り、
拡張子で linter を判定したうえで ``(linter, config-root)`` ごとにグルーピング
した plan を JSON で stdout に書き出す。shell 側はこの JSON を jq で
iterate しながら linter binary 解決と実行を行う。

入力フォーマット:
    レコード区切り = NUL (``\\0``)、フィールド区切り = TAB (``\\t``)
    フィールドは [source, rel_path] の 2 つ。source は ``staged`` または
    ``working``。同じ rel_path が両方の source で現れた場合は両方を items
    に残す (dual-membership: shell 側の lint 実行で staged/working を別個
    に検査する必要があるため)。

出力フォーマット (stdout, JSON):
    {
      "groups": [
        {
          "linter": "eslint",      # eslint | ruff
          "label":  "ESLint",      # 利用者向け表示名
          "root":   "/abs/path",   # find-config-root.sh が返したディレクトリ
          "items": [
            {"file": "src/a.ts", "source": "staged"},
            {"file": "src/a.ts", "source": "working"}
          ]
        }
      ]
    }

linter 判定でマッチしないファイル、root 解決に失敗したファイルは plan から
除外する (shell 側の既存挙動と同じ silent skip)。find-config-root.sh の
subprocess 起動自体が失敗した場合 (PATH に bash が無い等の異常) は silent
skip ではなく exit 非 0 で死に、shell 側で fail-closed deny に倒す。

注: ``JS_LIKE_SUFFIXES`` / ``PYTHON_SUFFIXES`` は ``lib/common.sh`` の
``is_js_like`` / ``is_python`` の case パターンと同期して維持すること。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

Linter = Literal["eslint", "ruff"]
Source = Literal["staged", "working"]

JS_LIKE_SUFFIXES: frozenset[str] = frozenset(
    {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
)
PYTHON_SUFFIXES: frozenset[str] = frozenset({".py"})

LINTER_LABEL: dict[Linter, str] = {
    "eslint": "ESLint",
    "ruff": "Ruff",
}

VALID_SOURCES: frozenset[str] = frozenset({"staged", "working"})
# items の出力順を決めるためのスコア。staged → working で固定すると、
# dual-membership 時の lint 実行順が決定的になり review の noise が減る。
SOURCE_ORDER: dict[Source, int] = {"staged": 0, "working": 1}

SCRIPT_DIR = Path(__file__).resolve().parent
FIND_CONFIG_ROOT = SCRIPT_DIR / "find-config-root.sh"


def detect_linter(rel_path: str) -> Linter | None:
    suffix = os.path.splitext(rel_path)[1]
    if suffix in JS_LIKE_SUFFIXES:
        return "eslint"
    if suffix in PYTHON_SUFFIXES:
        return "ruff"
    return None


def parse_records(raw: bytes) -> dict[str, set[Source]]:
    """stdin の raw bytes を {rel_path: {source, ...}} に集約する。

    git のパスはバイト列で UTF-8 valid とは限らないが、本 helper は path を
    str として扱い JSON 出力するため UTF-8 デコードが必須。失敗時に silent
    skip すると非 UTF-8 ファイル名を持つ .js/.py が lint をすり抜けるため、
    fail-closed のため呼び出し側に exit 2 を伝播させる (shell 側で deny)。
    """
    result: dict[str, set[Source]] = {}
    for record in raw.split(b"\x00"):
        if not record:
            continue
        try:
            decoded = record.decode("utf-8")
        except UnicodeDecodeError:
            print(
                "build-lint-plan: non-UTF-8 path in stdin; cannot route to linter safely",
                file=sys.stderr,
            )
            raise SystemExit(2)
        parts = decoded.split("\t", 1)
        if len(parts) != 2:
            continue
        source, rel_path = parts
        if source not in VALID_SOURCES or not rel_path:
            continue
        result.setdefault(rel_path, set()).add(source)  # type: ignore[arg-type]
    return result


def _resolve_root(
    rel_path: str,
    linter: Linter,
    cache: dict[tuple[Linter, str], str],
) -> str:
    """find-config-root.sh を呼んで root を返す。

    探索結果は ``(linter, dirname(rel_path))`` ごとに同じになるため、cache は
    dirname 単位で取る。ただし find-config-root.sh は内部で dirname を計算して
    そこから上向きに探索する仕様なので、引数には rel_path をそのまま渡す
    (dirname を渡すと探索開始位置が 1 段上にずれて、ファイル直下の config が
    見落とされる)。

    subprocess の起動自体が失敗した場合は呼び出し側で fail-closed deny に倒す
    べき重大エラーなので、exception は握りつぶさず上に投げる (find-config-root
    が空文字 + exit 0 を返した「マーカー無し = 該当 root なし」とは区別する)。
    """
    cache_key = (linter, os.path.dirname(rel_path))
    if cache_key in cache:
        return cache[cache_key]
    proc = subprocess.run(
        ["bash", str(FIND_CONFIG_ROOT), rel_path, linter],
        capture_output=True,
        text=True,
        check=False,
    )
    root = proc.stdout.strip()
    cache[cache_key] = root
    return root


def build_plan(records: dict[str, set[Source]]) -> dict[str, list[dict]]:
    """集約済みレコードから plan dict を構築する。

    groups は ``(linter, root)`` で安定ソート、各 group の items は (file
    アルファベット順, SOURCE_ORDER) の安定ソートで返す。
    """
    grouped: dict[tuple[Linter, str], list[dict[str, str]]] = {}
    cache: dict[tuple[Linter, str], str] = {}

    for rel_path in sorted(records):
        linter = detect_linter(rel_path)
        if linter is None:
            continue
        root = _resolve_root(rel_path, linter, cache)
        if not root:
            continue
        items = grouped.setdefault((linter, root), [])
        for source in sorted(records[rel_path], key=SOURCE_ORDER.__getitem__):
            items.append({"file": rel_path, "source": source})

    return {
        "groups": [
            {
                "linter": linter,
                "label": LINTER_LABEL[linter],
                "root": root,
                "items": grouped[(linter, root)],
            }
            for (linter, root) in sorted(grouped)
        ]
    }


def main() -> int:
    raw = sys.stdin.buffer.read()
    records = parse_records(raw)
    plan = build_plan(records)
    json.dump(plan, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
