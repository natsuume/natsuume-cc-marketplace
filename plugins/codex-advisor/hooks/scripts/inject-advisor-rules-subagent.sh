#!/bin/sh
# inject-advisor-rules-subagent.sh — codex-advisor plugin の SubagentStart hook スクリプト
#
# I/O 契約 (issue #219):
#   stdin  : SubagentStart hook input JSON (本 plugin では内容を使用しない)
#   stdout : {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": "<advisor-rules-subagent.md 全文 (プレースホルダ置換済み)>"}}
#   exit   : 常に 0 (テンプレート・wrapper 欠落時は注入をスキップして exit 0。fail-open でセッションを壊さない)
#
# 制約 (issue #219):
#   - Linux (WSL2) / macOS (bash 3.2 環境の /bin/sh) の両方で動作すること
#   - agent_type による条件分岐を持たない (常に同一内容を注入する)
#
# テンプレートの {{WRAPPER_PATH_SH}} は jq の gsub + @sh で shell-quote 済み絶対パスへ
# 置換する。sed を使わないのは、パスに sed の置換メタ文字 (& / \ / デリミタ) が含まれる
# install 環境で置換が壊れるため。@sh により $ / バッククォート / 空白 / literal ' を含む
# パスも安全な単一 shell word として本文に埋め込まれる (codex review P3 指摘への対処)。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# prompt テンプレートは hooks/scripts/../prompts/ に配置されている
# (inject-advisor-rules.sh と同じパス解決方式)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)

# テンプレート本文を読み込む。読めない・空の場合は fail-open で無音終了する
# (inject-advisor-rules.sh と同じ方針。壊れた・欠けた注入で誤誘導するより
# 注入しない方が安全)。
TEMPLATE=$(cat "$PROMPTS_DIR/advisor-rules-subagent.md" 2>/dev/null)

if [ -z "$TEMPLATE" ]; then
  exit 0
fi

# wrapper (run-codex-advisor.sh) の絶対パスを script 自身の位置から解決する。
# hooks/scripts/ から見て ../../scripts/run-codex-advisor.sh。
# cd + pwd で正規化した絶対パスを組み立てる。
SCRIPTS_DIR=$(cd "$(dirname "$0")/../../scripts" 2>/dev/null && pwd)

if [ -z "$SCRIPTS_DIR" ]; then
  exit 0
fi

WRAPPER="$SCRIPTS_DIR/run-codex-advisor.sh"

# 解決したパスにファイルが実在しない場合は fail-open (壊れた注入で誤誘導しない)。
if [ ! -f "$WRAPPER" ]; then
  exit 0
fi

# プレースホルダ置換と JSON 出力を jq 1 回で行う。--arg で渡した値は gsub の置換値
# として literal に扱われるため、パス中のメタ文字で置換が壊れない (ヘッダコメント参照)。
# jq が万一失敗しても header の「exit 常に 0」契約を守る (fail-open)。
jq -n --arg tpl "$TEMPLATE" --arg wrapper "$WRAPPER" '{
  hookSpecificOutput: {
    hookEventName: "SubagentStart",
    additionalContext: ($tpl | gsub("\\{\\{WRAPPER_PATH_SH\\}\\}"; ($wrapper | @sh)))
  }
}' || exit 0
