#!/usr/bin/env python3
"""Emit edited file paths from Claude hook input as NUL records."""

from __future__ import annotations

import json
import os
import sys


def extract_paths(payload: dict[object, object]) -> list[str]:
    tool_name = payload.get("tool_name")
    raw_tool_input = payload.get("tool_input")
    tool_input = raw_tool_input if isinstance(raw_tool_input, dict) else {}

    candidates: list[str]
    if tool_name in {"Write", "Edit", "MultiEdit"}:
        file_path = tool_input.get("file_path")
        candidates = [file_path] if isinstance(file_path, str) else []
    else:
        candidates = []

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or "\0" in candidate:
            continue
        path = os.path.abspath(candidate)
        if path not in seen:
            seen.add(path)
            normalized.append(path)
    return normalized


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 1
    if not isinstance(payload, dict):
        return 1
    for path in extract_paths(payload):
        sys.stdout.buffer.write(path.encode("utf-8") + b"\0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
