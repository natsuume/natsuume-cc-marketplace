#!/bin/bash
# Claude Code の permission_mode を、agent-discipline が after 系規律を配送すべき
# literal auto mode か判定する。
#
# Codex の turn-scoped hook input で観測できる permission_mode は approval policy の投影で
# あり、Auto preset を一意に表さない。default / acceptEdits / dontAsk / bypassPermissions の
# いずれも Auto と同義にはできないため、turn_id がある runtime は既知値を含め全て no-op
# とする。Codex で同じ意図が必要な場合は明示 Skill `auto-codex` を使用する。

is_agent_discipline_autonomous_mode() {
  _agent_discipline_mode=$1
  _agent_discipline_turn_id=${2:-}

  # turn_id は Codex の turn-scoped hook input で必須の extension。Claude Code input には無い。
  # 先に runtime を分けることで、将来 Codex が literal auto を渡しても誤検出しない。
  if [ -n "$_agent_discipline_turn_id" ]; then
    return 1
  fi

  [ "$_agent_discipline_mode" = "auto" ]
}
