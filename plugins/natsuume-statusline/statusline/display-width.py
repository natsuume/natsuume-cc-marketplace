from __future__ import annotations

import re
import sys
import unicodedata


ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")
RESET = "\x1b[00m"


def cell_width(char: str) -> int:
    codepoint = ord(char)
    if unicodedata.combining(char):
        return 0
    if unicodedata.category(char) in {"Cc", "Cf"}:
        return 0
    if 0x1F3FB <= codepoint <= 0x1F3FF:
        return 0
    if unicodedata.east_asian_width(char) in {"W", "F"}:
        return 2
    return 1


def visible_width(text: str) -> int:
    return sum(cell_width(char) for char in ANSI_SGR.sub("", text))


def truncate_visible(text: str, maximum: int) -> str:
    result: list[str] = []
    width = 0
    index = 0
    while index < len(text):
        ansi = ANSI_SGR.match(text, index)
        if ansi:
            result.append(ansi.group(0))
            index = ansi.end()
            continue
        char = text[index]
        char_width = cell_width(char)
        if char_width > 0 and width + char_width > maximum:
            break
        result.append(char)
        width += char_width
        index += 1
    result.append(RESET)
    return "".join(result)


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    text = sys.stdin.read()
    if sys.argv[1] == "width" and len(sys.argv) == 2:
        sys.stdout.write(str(visible_width(text)))
        return 0
    if sys.argv[1] == "truncate" and len(sys.argv) == 3:
        try:
            maximum = int(sys.argv[2])
        except ValueError:
            return 2
        if maximum < 0:
            return 2
        sys.stdout.write(truncate_visible(text, maximum))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
