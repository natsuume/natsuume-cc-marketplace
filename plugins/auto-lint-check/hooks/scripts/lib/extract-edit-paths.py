#!/usr/bin/env python3
"""Emit edited file paths from Claude or Codex hook input as NUL records."""

from __future__ import annotations

import json
import os
import re
import sys


FILE_HEADER_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")
MOVE_HEADER_RE = re.compile(r"^\*\*\* Move to: (.+)$")


def _paths_from_apply_patch(command: str) -> list[str]:
    paths: list[str] = []
    action: str | None = None
    candidate: str | None = None

    def flush() -> None:
        nonlocal action, candidate
        if action in {"Add", "Update"} and candidate:
            paths.append(candidate)
        action = None
        candidate = None

    for line in command.splitlines():
        file_match = FILE_HEADER_RE.match(line)
        if file_match is not None:
            flush()
            action, candidate = file_match.groups()
            continue
        move_match = MOVE_HEADER_RE.match(line)
        if move_match is not None and action == "Update":
            candidate = move_match.group(1)
    flush()
    return paths


def extract_paths(payload: dict[object, object]) -> list[str]:
    tool_name = payload.get("tool_name")
    raw_tool_input = payload.get("tool_input")
    tool_input = raw_tool_input if isinstance(raw_tool_input, dict) else {}

    candidates: list[str]
    if tool_name in {"Write", "Edit", "MultiEdit"}:
        file_path = tool_input.get("file_path")
        candidates = [file_path] if isinstance(file_path, str) else []
    elif tool_name == "apply_patch":
        command = tool_input.get("command")
        candidates = _paths_from_apply_patch(command) if isinstance(command, str) else []
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
