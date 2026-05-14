#!/usr/bin/env python3
"""block-commit-lint.sh から呼び出される lint plan 構築器。

stdin から NUL 区切りで ``<source>\\t<rel_path>`` 形式のレコードを受け取り、
拡張子で linter を判定したうえで ``(linter, config-root)`` ごとにグルーピング
した plan を JSON で stdout に書き出す。shell 側はこの JSON を jq で
iterate しながら linter binary 解決と実行を行う。

本 helper の存在意義は ``block-commit-lint.sh`` から bash 4+ 依存 (連想配列・
キー列挙) を取り除き、macOS 標準 /bin/bash 3.2 でも hook が動くようにする
こと。集約とグルーピングは Python の dict で書く方が素直で、shell 側は
indexed array だけで済むようになる。

入力フォーマット:
    レコード区切り = NUL (``\\0``)、フィールド区切り = TAB (``\\t``)
    フィールドは [source, rel_path] の 2 つ。source は ``staged`` または
    ``working``。同じ rel_path が両方の source で現れた場合は
    dual-membership として両方を items に残す (既存仕様の維持)。

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
除外する (shell 側の既存挙動と同じ silent skip)。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

JS_LIKE_SUFFIXES: frozenset[str] = frozenset(
    {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
)
PYTHON_SUFFIXES: frozenset[str] = frozenset({".py"})

LINTER_LABEL: dict[str, str] = {
    "eslint": "ESLint",
    "ruff": "Ruff",
}

VALID_SOURCES: frozenset[str] = frozenset({"staged", "working"})
SOURCE_ORDER: dict[str, int] = {"staged": 0, "working": 1}

SCRIPT_DIR = Path(__file__).resolve().parent
FIND_CONFIG_ROOT = SCRIPT_DIR / "find-config-root.sh"


def detect_linter(rel_path: str) -> str | None:
    """拡張子から linter を判定する。マッチしなければ None。"""
    suffix = os.path.splitext(rel_path)[1]
    if suffix in JS_LIKE_SUFFIXES:
        return "eslint"
    if suffix in PYTHON_SUFFIXES:
        return "ruff"
    return None


def parse_records(raw: bytes) -> dict[str, list[str]]:
    """stdin の raw bytes を {rel_path: [source, ...]} に集約する。

    NUL 区切りのレコードを TAB 分割し、同一 rel_path に対する source 集合を
    SOURCE_ORDER で安定ソートして返す。空レコード・不正レコードはスキップ。
    """
    result: dict[str, set[str]] = {}
    for record in raw.split(b"\x00"):
        if not record:
            continue
        try:
            decoded = record.decode("utf-8")
        except UnicodeDecodeError:
            continue
        parts = decoded.split("\t", 1)
        if len(parts) != 2:
            continue
        source, rel_path = parts
        if source not in VALID_SOURCES or not rel_path:
            continue
        result.setdefault(rel_path, set()).add(source)
    return {
        path: sorted(sources, key=lambda s: SOURCE_ORDER[s])
        for path, sources in result.items()
    }


def resolve_config_root(
    rel_path: str,
    linter: str,
    cache: dict[tuple[str, str], str],
) -> str:
    """find-config-root.sh を呼んで root を返す。dirname 単位でキャッシュ。"""
    cache_key = (linter, os.path.dirname(rel_path))
    if cache_key in cache:
        return cache[cache_key]
    try:
        proc = subprocess.run(
            ["bash", str(FIND_CONFIG_ROOT), rel_path, linter],
            capture_output=True,
            text=True,
            check=False,
        )
        root = proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        root = ""
    cache[cache_key] = root
    return root


def build_plan(records: dict[str, list[str]]) -> dict[str, list[dict]]:
    """集約済みレコードから plan dict を構築する。"""
    cache: dict[tuple[str, str], str] = {}
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}

    for rel_path in sorted(records):
        linter = detect_linter(rel_path)
        if linter is None:
            continue
        root = resolve_config_root(rel_path, linter, cache)
        if not root:
            continue
        key = (linter, root)
        items = grouped.setdefault(key, [])
        for source in records[rel_path]:
            items.append({"file": rel_path, "source": source})

    groups: list[dict] = []
    for (linter, root) in sorted(grouped):
        groups.append(
            {
                "linter": linter,
                "label": LINTER_LABEL[linter],
                "root": root,
                "items": grouped[(linter, root)],
            }
        )
    return {"groups": groups}


def main() -> int:
    raw = sys.stdin.buffer.read()
    records = parse_records(raw)
    plan = build_plan(records)
    json.dump(plan, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
