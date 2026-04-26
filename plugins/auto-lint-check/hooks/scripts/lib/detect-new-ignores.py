#!/usr/bin/env python3
"""Detect *newly inserted* lint/formatter ignore comments in a Claude Code
Edit/Write/MultiEdit tool payload.

Reads a hook payload (JSON) from stdin and prints up to 3 newly-added matches
to stdout. Exit codes:
  0  no new ignore comments (allow)
  2  one or more new ignore comments detected (deny)
  1  malformed input / unexpected error (caller should treat as skip)

Newness is determined by multiset comparison of (linter_label, line_text)
keys between "old" and "new" texts:

  - Edit       old=tool_input.old_string, new=tool_input.new_string
  - MultiEdit  old=join(edits[].old_string), new=join(edits[].new_string)
  - Write      new=tool_input.content; old=existing file content (if any)

If the multiset count of a given (label, line_text) in "new" exceeds the count
in "old", the difference is reported as newly added. This avoids false
positives when an edit preserves an existing ignore comment in its surrounding
context.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

# eslint / prettier / ruff の代表的な ignore 構文。MVP のスコープ外 (mypy,
# ts-ignore, biome, stylelint 等) は意図的に含めていない。
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"//\s*eslint-(?:disable|enable|disable-line|disable-next-line)"), "ESLint"),
    (re.compile(r"/\*\s*eslint-(?:disable|enable|disable-next-line)"), "ESLint"),
    (re.compile(r"//\s*prettier-ignore"), "Prettier"),
    (re.compile(r"/\*\s*prettier-ignore\s*\*/"), "Prettier"),
    (re.compile(r"<!--\s*prettier-ignore\s*-->"), "Prettier"),
    (re.compile(r"#\s*noqa(?:[\s:]|$)"), "Ruff"),
    (re.compile(r"#\s*ruff:\s*noqa"), "Ruff"),
    (re.compile(r"#\s*fmt:\s*(?:off|on|skip)"), "Ruff"),
]


def _find_matches(text: str) -> list[tuple[str, int, str]]:
    """Yield (label, line_number_1_indexed, raw_line) for each line matching any pattern.

    A line is reported only once even if multiple patterns match it; the first
    matching pattern wins.
    """
    matches: list[tuple[str, int, str]] = []
    for line_num, line in enumerate(text.splitlines(), start=1):
        for pattern, label in _PATTERNS:
            if pattern.search(line):
                matches.append((label, line_num, line))
                break
    return matches


def _collect_old_new(payload: dict) -> tuple[str, str] | None:
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    if tool_name == "Write":
        new_text = tool_input.get("content", "") or ""
        file_path = tool_input.get("file_path", "")
        old_text = ""
        if file_path:
            try:
                old_text = Path(file_path).read_text()
            except (FileNotFoundError, OSError):
                old_text = ""
        return old_text, new_text

    if tool_name == "Edit":
        return (
            tool_input.get("old_string", "") or "",
            tool_input.get("new_string", "") or "",
        )

    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        old_text = "\n".join(e.get("old_string", "") or "" for e in edits)
        new_text = "\n".join(e.get("new_string", "") or "" for e in edits)
        return old_text, new_text

    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 1

    pair = _collect_old_new(payload)
    if pair is None:
        return 0
    old_text, new_text = pair

    new_hits = _find_matches(new_text)
    old_hits = _find_matches(old_text)

    # 多重集合 (label, line_text) で「新側に追加で現れた分」だけを抽出。
    old_counter: Counter[tuple[str, str]] = Counter((label, line) for label, _, line in old_hits)
    consumed: Counter[tuple[str, str]] = Counter()
    added: list[tuple[str, int, str]] = []
    for label, line_num, line in new_hits:
        key = (label, line)
        if consumed[key] < old_counter.get(key, 0):
            consumed[key] += 1
        else:
            added.append((label, line_num, line))

    if not added:
        return 0

    for label, line_num, line in added[:3]:
        sys.stdout.write(f"[{label}] new line {line_num}: {line}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
