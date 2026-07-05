#!/bin/bash
# inject-always.sh
# SessionStart で「permission_mode に依らず常時適用」 すべき agent-discipline ルールを
# まとめて additionalContext として注入する。
#
# 注入する本文は hooks/prompts/always-rules.md に定義する (プロンプトを sh に直接埋め込むと
# 視認性・メンテナンス性が下がるため分離。 章立て = 物理層 / before 系 / during 系 / 排他系は
# md 側の見出しを参照)。
#
# auto mode 限定の方針 (after 系 = commit→push→PR→merge 自走) は inject-auto.sh が別途配送する。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# hook_event_name は入力からそのまま読み取る (既定値を埋めると別 event の
# 文脈に誘導する恐れがあるため)。INPUT が不正な JSON / 空の場合 jq は parse error を
# stderr に吐くため 2>/dev/null で抑制し、HOOK_EVENT 空判定でフォールバックさせる
# (hook の stderr は利用者に見えるため、解析失敗をノイズとして表に出さない)。
HOOK_EVENT=$(printf '%s' "$INPUT" | jq -r '.hook_event_name // ""' 2>/dev/null)
if [ -z "$HOOK_EVENT" ]; then
  exit 0
fi

# 注入本文を prompts/ から読み込む。読めない場合は fail-open で無音終了する
# (jq 不在時と同じ方針。壊れた・欠けた注入で誤誘導するより注入しない方が安全)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)
CONTEXT=$(cat "$PROMPTS_DIR/always-rules.md" 2>/dev/null)
if [ -z "$CONTEXT" ]; then
  exit 0
fi

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'
