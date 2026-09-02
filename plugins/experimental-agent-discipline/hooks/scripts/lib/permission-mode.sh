#!/bin/bash
# Claude Code の permission_mode を、agent-discipline が after 系規律を配送すべき
# literal auto mode か判定する helper。

is_agent_discipline_autonomous_mode() {
  _agent_discipline_mode=$1

  [ "$_agent_discipline_mode" = "auto" ]
}
