#!/bin/bash
# statusline エントリポイント。Claude Code の statusLine.command から呼ばれ、
# 同じディレクトリの main.sh に処理を委譲する。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIR/main.sh"
