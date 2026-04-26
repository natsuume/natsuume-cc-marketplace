#!/usr/bin/env python3
"""Predict file content after a Claude Code Edit/Write/MultiEdit tool call.

Reads a hook payload (JSON) from stdin and writes the predicted post-edit
content to stdout. Used by auto-lint-check.sh to feed predicted content
to linters via stdin without touching the actual file.

Exit codes:
  0  predicted content emitted to stdout
  1  unable to predict (missing fields, missing file, malformed JSON)
"""

import json
import sys
from pathlib import Path


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 1

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path", "")

    if not file_path:
        return 1

    if tool_name == "Write":
        sys.stdout.write(tool_input.get("content", ""))
        return 0

    if tool_name == "Edit":
        try:
            current = Path(file_path).read_text()
        except (FileNotFoundError, OSError):
            return 1
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")
        replace_all = bool(tool_input.get("replace_all", False))
        count = -1 if replace_all else 1
        sys.stdout.write(current.replace(old_string, new_string, count))
        return 0

    if tool_name == "MultiEdit":
        try:
            content = Path(file_path).read_text()
        except (FileNotFoundError, OSError):
            return 1
        for edit in tool_input.get("edits") or []:
            old_string = edit.get("old_string", "")
            new_string = edit.get("new_string", "")
            replace_all = bool(edit.get("replace_all", False))
            count = -1 if replace_all else 1
            content = content.replace(old_string, new_string, count)
        sys.stdout.write(content)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
