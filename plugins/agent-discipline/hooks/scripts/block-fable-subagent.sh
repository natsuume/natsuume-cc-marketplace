#!/bin/bash
# block-fable-subagent.sh
# PreToolUse (matcher: Agent|Task) で、サブエージェントが Fable で実行される経路を判定する hook。
# Fable サブエージェントは `model: "fable"` を明示し、かつ Fable 週次枠の使用率が閾値以下の
# 場合に限り許可する (用途 = advisor と pre-merge review への限定は分業規律の prompt が担い、
# 本 hook は許可 agent の一覧を持たない)。メインセッションは Fable 以外で動かす前提で、
# メインセッションのモデル (session model state) は判定に使わない。fork の主防御は利用者の
# settings に置く permission rule (`permissions.deny` の `Agent(fork)`) で、本 hook は permission
# rule が捕捉しない経路 (サブエージェント内からの継承、env による上書き) の検知と、deny
# メッセージによる自己修正誘導を担う。モデル判定は deterministic な文字列判定、使用率判定は
# jq による cache の検査で、LLM 評価は使わない。fable 明示の使用率判定不能時は fail-closed = deny。
#
# Claude Code のモデル解決順序:
#   明示 model > agent 定義の frontmatter > CLAUDE_CODE_SUBAGENT_MODEL > メインセッション継承。
#   CLAUDE_CODE_SUBAGENT_MODEL_FORCE (1 / true / yes / on、大文字小文字は区別しない) が設定されている
#   場合のみ、env (未設定ならメインセッションのモデル) がこの順序の全てを上書きする。
#   subagent_type が fork のサブエージェントは、model 指定にも env にも依らず起動元のモデルを
#   継承する。
#
# 判定順序 (上から評価し、最初に該当した結果を返す):
#   1. fork (subagent_type が fork) → サブエージェント内 (入力に agent_id がある) からなら deny
#      (nested guard)。メインセッションからなら allow
#   2. CLAUDE_CODE_SUBAGENT_MODEL_FORCE が有効 → 実効モデルは env (非空ならその値、空なら
#      メインセッションのモデル)。env が fable なら model の明示に依らず deny し、model の明示では
#      直せないことを deny 理由に書く。それ以外 (env 空を含む) は allow
#   3. tool_input.model に fable が明示指定されている → Fable 週次枠の使用率判定で利用可なら
#      allow、利用不可 (閾値超過・使用率不明) なら deny
#   4. tool_input.model が非 fable の具体指定 → allow (明示は env より優先されるため)
#   5. tool_input.model 未指定 (= 継承経路):
#      - env が非空: fable なら deny、それ以外は allow (env が fable を指す場合は使用率に依らず
#        deny する。明示指定ではないため)
#      - env 不在でサブエージェント内 (入力に agent_id がある) からの起動は deny する (nested
#        guard)。継承先は起動元サブエージェントのモデルで、週次枠判定を通った Fable
#        サブエージェントの子が判定なしで Fable を継承しうるため
#      - それ以外 (env 不在のメインセッションからの起動) は allow
#
# Fable 週次枠の使用率判定 (3):
#   - 入力: ${XDG_CACHE_HOME:-$HOME/.cache}/natsuume-statusline/weekly-scoped.json (producer は
#     natsuume-statusline。本 hook は読むだけで書き込まず、OAuth usage API も呼ばない)
#   - 閾値: env FABLE_WEEKLY_MAX_PERCENT (前後空白を trim した 0〜100 の 10 進整数)。未設定・空・
#     範囲外・非整数は既定値 80
#   - weekly_scoped[] のうち display_name が大文字小文字を無視して fable を含み percent が数値の
#     entry の最大 percent を使い、percent <= 閾値 なら利用可 (比較は小数を扱える jq で行う)
#   - cache が symlink / 通常ファイルでない / 存在しない / 読めない / JSON document が 1 つで
#     ない / fetched_at が欠落・非数値 / now - fetched_at > 1800 (stale) / weekly_scoped が
#     欠落・非配列 / Fable entry が無い / 現在時刻を取得できない場合は使用率不明 = 利用不可。
#     fetched_at が未来時刻でも stale とはみなさない (時計ずれの許容)
#
# 正規化ポリシー:
#   - env / tool_input.model とも前後空白を trim し、"inherit" (case-insensitive) は
#     「未指定」に正規化する (inherit は継承の別表記であり具体的なモデル選択ではないため)
#   - 非空・非 inherit の env 値は、fable を含まない限り authoritative な非 fable 値として
#     信頼する (env の妥当性検証は Claude Code 本体と利用者の責務で、hook の確信境界の外)
#
# 既知の制約:
#   - agent 定義 frontmatter の model は tool_input に現れないため判定できない。model 未指定 +
#     frontmatter が fable を指す構成は、FORCE 併用時を除いて本 hook では捕捉不能
#   - Workflow ツール内部の agent() 呼び出しは PreToolUse では捕捉できない
#   - jq 不在時は何もせず exit 0 (jq は plugin 全体の前提であり、本 hook 単独では fail-closed に
#     しない)。hook_event_name が PreToolUse 以外の入力にも応答しない

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# 各フィールドは 1 行 1 値で読むため、値に含まれる改行 (CR / LF) は空白に置き換えて欄ずれを防ぐ
# (subagent_type に改行を含めても、後続の agent_id が別の欄に読み込まれない)。
{ read -r HOOK_EVENT; read -r TOOL_MODEL; read -r SUBAGENT_TYPE; read -r AGENT_ID; } < <(
  printf '%s' "$INPUT" | jq -r '
    [ (.hook_event_name // ""),
      (.tool_input.model // ""),
      (.tool_input.subagent_type // ""),
      (.agent_id // "") ]
    | map(tostring | gsub("[\r\n]"; " "))
    | .[]
  ' 2>/dev/null
)

# PreToolUse 以外 (入力不正含む) では何もしない。deny JSON の hookEventName は
# PreToolUse 固定で返すため、イベントが確認できない入力には応答しない。
if [ "$HOOK_EVENT" != "PreToolUse" ]; then
  exit 0
fi

deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
}

trim() {
  printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

# モデル名が Fable を指すか (alias `fable` / full ID `claude-fable-5-1` の双方を含む部分一致)。
is_fable() {
  [ -n "$1" ] && printf '%s' "$1" | grep -qi 'fable'
}

TOOL_MODEL=$(trim "$TOOL_MODEL")
SUBAGENT_TYPE=$(trim "$SUBAGENT_TYPE")
AGENT_ID=$(trim "$AGENT_ID")
ENV_SUB=$(trim "${CLAUDE_CODE_SUBAGENT_MODEL:-}")
FORCE_RAW=$(trim "${CLAUDE_CODE_SUBAGENT_MODEL_FORCE:-}")

# "inherit" (case-insensitive) は「未指定 = 継承」の別表記として正規化する
if printf '%s' "$TOOL_MODEL" | grep -qix 'inherit'; then
  TOOL_MODEL=""
fi
if printf '%s' "$ENV_SUB" | grep -qix 'inherit'; then
  ENV_SUB=""
fi

# FORCE の真値は Claude Code の boolean env の解釈に合わせて 1 / true / yes / on (大文字小文字は
# 区別しない)。それ以外の値・空・未設定は無効。
FORCE_ENABLED=0
case "$(printf '%s' "$FORCE_RAW" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on) FORCE_ENABLED=1 ;;
esac

# fable 明示を deny したときの代替手段 (advisor / reviewer はスキップ、それ以外は非 Fable を明示)。
FABLE_FALLBACK_GUIDE="fable-advisor-runner / fable-reviewer は再起動せずスキップしてください。それ以外の委任では Fable を使わず、model に非 Fable のモデル (例: \`model: \"opus\"\`) を明示してください。"

# 閾値: 前後空白を trim した 0〜100 の 10 進整数のみ受け付け、それ以外は既定値へ fallback する。
DEFAULT_MAX_PERCENT=80
MAX_PERCENT=$(trim "${FABLE_WEEKLY_MAX_PERCENT:-}")
if ! printf '%s' "$MAX_PERCENT" | grep -qE '^(0|[1-9][0-9]?|100)$'; then
  MAX_PERCENT="$DEFAULT_MAX_PERCENT"
fi

# 使用率超過の deny。$1 = 判定に使った使用率、$2 = reset 時刻 (空なら載せない)。
deny_usage_over() {
  RESETS_NOTE=""
  if [ -n "$2" ]; then
    RESETS_NOTE=" 枠のリセットは $2 です。"
  fi
  deny "agent-discipline: Fable 週次枠の使用率が ${1}% で、閾値 ${MAX_PERCENT}% を超えているため、Fable の明示指定を deny しました。${RESETS_NOTE}${FABLE_FALLBACK_GUIDE}"
}

# 使用率不明の deny。cache の producer の構成手順を添えて自己修復可能にする。$1 = 不明の理由。
deny_usage_unknown() {
  deny "agent-discipline: Fable の明示指定は、Fable 週次枠の使用率が閾値 (${MAX_PERCENT}%) 以下と確認できたときだけ許可します。$1 使用率を確認できない状態では Fable 枠を消費しないため deny します (fail-closed)。使用率 cache の producer は natsuume-statusline です。statusline として構成されていない場合は /natsuume-statusline:setup で構成してください。${FABLE_FALLBACK_GUIDE}"
}

# Fable 週次枠の使用率判定。利用可なら return 0、利用不可なら deny して終了する。
check_fable_weekly_usage() {
  CACHE_FILE="${XDG_CACHE_HOME:-${HOME:-}/.cache}/natsuume-statusline/weekly-scoped.json"

  if [ -L "$CACHE_FILE" ]; then
    deny_usage_unknown "使用率 cache ($CACHE_FILE) が symlink です。通常ファイル以外は読み取り対象にしません。"
  fi
  if [ ! -f "$CACHE_FILE" ]; then
    deny_usage_unknown "使用率 cache ($CACHE_FILE) が通常ファイルとして存在しません。"
  fi
  if [ ! -r "$CACHE_FILE" ]; then
    deny_usage_unknown "使用率 cache ($CACHE_FILE) を読み取れません。"
  fi

  NOW=$(date +%s 2>/dev/null)
  if ! printf '%s' "$NOW" | grep -qE '^[0-9]+$'; then
    deny_usage_unknown "現在時刻を取得できず、使用率 cache の鮮度を判定できません。"
  fi

  CACHE_CONTENT=$(cat "$CACHE_FILE" 2>/dev/null)

  # cache の検査と閾値比較を jq に閉じる: percent は小数を取りうるため、シェルの整数比較には
  # 頼らない。--slurp で入力全体を配列として受け取り、JSON document がちょうど 1 つの場合だけ
  # 判定する (複数 document の連結や空入力は不明扱い)。出力は 4 行 (status / percent /
  # resets_at / 固定終端 END)。終端行を置くのは、command substitution が末尾の改行を落として
  # も、resets_at が空のときに行数契約が崩れないようにするため。jq の exit status と出力行数・
  # 終端行も検証し、契約どおりの出力が得られない場合は不明として deny する (fail-closed)。
  # resets_at は ISO 8601 形式に一致する場合だけ deny 文に載せる (cache 由来の文字列を無検証で
  # 判定文へ反映しない)。
  GATE_OUTPUT=$(printf '%s' "$CACHE_CONTENT" | jq -rs \
    --argjson now "$NOW" --argjson threshold "$MAX_PERCENT" '
    def emit(status; percent; resets): "\(status)\n\(percent)\n\(resets)\nEND";
    if length != 1 then emit("invalid"; ""; "")
    else .[0] |
    if type != "object" then emit("invalid"; ""; "")
    elif (.fetched_at | type) != "number" then emit("fetched_at"; ""; "")
    elif ($now - .fetched_at) > 1800 then emit("stale"; ""; "")
    elif (.weekly_scoped | type) != "array" then emit("entries"; ""; "")
    else
      [ .weekly_scoped[]
        | select(type == "object")
        | select((.display_name | type) == "string")
        | select(.display_name | ascii_downcase | contains("fable"))
        | select((.percent | type) == "number")
      ] as $fable
      | if ($fable | length) == 0 then emit("entries"; ""; "")
        else
          ($fable | sort_by(.percent) | last) as $top
          | (if ($top.resets_at | type) == "string"
                and ($top.resets_at | test("\\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})\\z"))
             then $top.resets_at else "" end) as $resets
          | if $top.percent > $threshold
            then emit("over"; $top.percent; $resets)
            else emit("ok"; $top.percent; $resets)
            end
        end
    end
    end
  ' 2>/dev/null)
  GATE_RC=$?

  GATE_LINES=$(printf '%s\n' "$GATE_OUTPUT" | grep -c '')
  { read -r GATE_STATUS; read -r GATE_PERCENT; read -r GATE_RESETS; read -r GATE_END; } <<GATE_EOF
$GATE_OUTPUT
GATE_EOF
  if [ "$GATE_RC" -ne 0 ] || [ "$GATE_LINES" -ne 4 ] || [ "$GATE_END" != "END" ]; then
    deny_usage_unknown "使用率 cache を JSON として読み取れません。"
  fi

  case "$GATE_STATUS" in
    ok)
      return 0
      ;;
    over)
      deny_usage_over "$GATE_PERCENT" "$GATE_RESETS"
      ;;
    fetched_at)
      deny_usage_unknown "使用率 cache の fetched_at が欠落しているか数値ではありません。"
      ;;
    stale)
      deny_usage_unknown "使用率 cache が古すぎます (fetched_at が 1800 秒より前)。"
      ;;
    entries)
      deny_usage_unknown "使用率 cache に Fable の週次枠 entry (display_name が fable を含み percent が数値) が見つかりません。"
      ;;
    *)
      deny_usage_unknown "使用率 cache を JSON として読み取れません。"
      ;;
  esac
}

# サブエージェント内 (agent_id あり) からの継承・fork は、継承先が起動元サブエージェントの
# モデルになる。週次枠判定を通った Fable サブエージェントの子が判定なしで Fable を継承しうるため
# deny する。
NESTED_DENY_REASON="agent-discipline: サブエージェント内からの model 未指定 (継承) / fork のサブエージェント起動を deny しました。継承先は起動元サブエージェントのモデルになり、Fable サブエージェントからの起動では Fable 週次枠の使用率判定を通らずに Fable で実行されえます。fork をやめ、model に非 Fable のモデル (例: \`model: \"opus\"\`) を明示して再実行してください。"

# 1. fork は model 指定も env も無視して起動元のモデルを継承する
if [ "$SUBAGENT_TYPE" = "fork" ]; then
  if [ -n "$AGENT_ID" ]; then
    deny "$NESTED_DENY_REASON"
  fi
  exit 0
fi

# 2. FORCE が有効なら実効モデルは env (非空ならその値、空ならメインセッションのモデル)
if [ "$FORCE_ENABLED" -eq 1 ]; then
  if is_fable "$ENV_SUB"; then
    deny "agent-discipline: CLAUDE_CODE_SUBAGENT_MODEL_FORCE が有効で CLAUDE_CODE_SUBAGENT_MODEL が fable を指すため、全サブエージェントが Fable で実行されます。model を明示しても実効モデルは変わらないため、FORCE の解除または env の非 Fable のモデル (例: opus) への修正が必要です。どちらもセッションを超える設定のため独断で書き換えず、この状態をユーザに報告して修正を依頼してください。"
  fi
  exit 0
fi

# 3. fable の明示指定は週次枠に余裕がある場合だけ allow
if is_fable "$TOOL_MODEL"; then
  check_fable_weekly_usage
  exit 0
fi

# 4. 非 fable の具体指定は allow (明示が env より優先されるため env の値に依らない)
if [ -n "$TOOL_MODEL" ]; then
  exit 0
fi

# 5. model 未指定 = 継承経路。env が非空ならその値が実効モデルになる。
if [ -n "$ENV_SUB" ]; then
  if is_fable "$ENV_SUB"; then
    deny "agent-discipline: model 未指定のサブエージェントは CLAUDE_CODE_SUBAGENT_MODEL の値 (fable) で実行されます。model に非 Fable のモデル (例: \`model: \"opus\"\`) を明示して再実行してください。env 自体を非 Fable のモデルへ直す場合は、セッションを超える設定のため独断で書き換えず、ユーザに依頼してください。"
  fi
  exit 0
fi

if [ -n "$AGENT_ID" ]; then
  deny "$NESTED_DENY_REASON"
fi

exit 0
