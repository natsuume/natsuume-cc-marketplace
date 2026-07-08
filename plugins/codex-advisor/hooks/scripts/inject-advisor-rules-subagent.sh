#!/bin/sh
# inject-advisor-rules-subagent.sh — codex-advisor plugin の SubagentStart hook スクリプト
#
# I/O 契約 (issue #219):
#   stdin  : SubagentStart hook input JSON (本 plugin では内容を使用しない)
#   stdout : {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": "<advisor-rules-subagent.md 全文 (プレースホルダ置換済み)>"}}
#   exit   : 常に 0 (テンプレート・wrapper 欠落時は注入をスキップして exit 0。fail-open でセッションを壊さない)
#
# 制約 (issue #219):
#   - Linux (WSL2) / macOS (BSD sed/awk, bash 3.2 環境の /bin/sh) の両方で動作すること
#   - agent_type による条件分岐を持たない (常に同一内容を注入する)

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

# テンプレート中の {{WRAPPER_PATH}} (複数出現しうる) を解決済み絶対パスへ置換する。
# sed のデリミタ (/) はパス自体に含まれるため衝突する。区切り文字に "|" を使うことで回避する
# (WRAPPER_PATH に "|" が含まれることは無い前提。POSIX sh + BSD/GNU 両方の sed で動く書き方)。
CONTEXT=$(printf '%s\n' "$TEMPLATE" | sed "s|{{WRAPPER_PATH}}|$WRAPPER|g")

if [ -z "$CONTEXT" ]; then
  exit 0
fi

jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "SubagentStart",
    additionalContext: $ctx
  }
}'
