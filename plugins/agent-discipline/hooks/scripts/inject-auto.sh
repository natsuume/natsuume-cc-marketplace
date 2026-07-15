#!/bin/bash
# inject-auto.sh
# Claude Code の permission_mode == "auto" のとき、after 系 (変更が一段落した後の
# commit→push→PR→merge 自走パイプライン) の方針を additionalContext として注入する。
# UserPromptSubmit から呼ばれる。Codex の permission_mode は Auto preset を一意に表さない
# ため全 mode を no-op とし、明示 Skill auto-codex が意図を代替する。
#
# 注入する本文は hooks/prompts/auto-mode.md に定義する (プロンプトを sh に直接埋め込むと
# 視認性・メンテナンス性が下がるため分離)。
#
# Claude Code では auto 以外、Codex では既知値を含む全 mode で何もしない。
# during 系 (実装自走の判断境界) と他の常時適用ルール (物理層 / before 系 / closing keyword)
# は inject-always.sh が SessionStart で配送する (v0.1.1 で during 系を inject-always 側に移動)。
#
# v0.1.1 で旧来の PostToolBatch 経路 (+ once-per-turn dedup logic) を撤去。 per-turn 2 回
# inject (UserPromptSubmit + PostToolBatch) が 1 回 (UserPromptSubmit のみ) に削減された。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# hook_event_name / permission_mode / Codex extension の turn_id を 1 回の jq 呼び出しで取得する。
# 値境界は Linux / macOS bash 3.2 で扱える NUL delimiter とする。
{
  IFS= read -r -d '' HOOK_EVENT
  IFS= read -r -d '' PERMISSION_MODE
  IFS= read -r -d '' TURN_ID
} < <(
  printf '%s' "$INPUT" | jq -j '
    (.hook_event_name // ""), "\u0000",
    (.permission_mode // ""), "\u0000",
    (.turn_id // ""), "\u0000"
  '
)

# runtime ごとの permission_mode を安全側へ正規化する。Claude では従来どおり auto のみ、
# Codex は Auto を証明できないため全 mode を対象外にする。
SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
if [ -z "$SCRIPT_DIR" ] || [ ! -r "$SCRIPT_DIR/lib/permission-mode.sh" ]; then
  exit 0
fi
# shellcheck source=plugins/agent-discipline/hooks/scripts/lib/permission-mode.sh
source "$SCRIPT_DIR/lib/permission-mode.sh" || exit 0
if ! is_agent_discipline_autonomous_mode "$PERMISSION_MODE" "$TURN_ID"; then
  exit 0
fi

# hook_event_name が取れなければイベント名を正しくエコーできないので無音終了する。
# 誤った既定値で hookSpecificOutput.hookEventName を返すと別 event の文脈に誘導する恐れがある。
if [ -z "$HOOK_EVENT" ]; then
  exit 0
fi

# 注入本文を prompts/ から読み込む。読めない場合は fail-open で無音終了する
# (jq 不在時と同じ方針。壊れた・欠けた注入で誤誘導するより注入しない方が安全)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)
CONTEXT=$(cat "$PROMPTS_DIR/auto-mode.md" 2>/dev/null)
if [ -z "$CONTEXT" ]; then
  exit 0
fi

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'
