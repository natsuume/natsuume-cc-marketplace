#!/bin/bash
# block-fable-subagent.sh
# PreToolUse (matcher: Agent|Task) でサブエージェントのモデル解決経路を判定する gate。
# Fable は effort low 固定の専用 agent
# (experimental-agent-discipline:fable-low-worker / experimental-agent-discipline:fable-low-explorer)
# への明示委任に限り、Fable 週次枠の使用率が閾値以下のあいだだけ許可し、それ以外の Fable 実行
# 経路は deny する。すべて deterministic な文字列判定で LLM 評価は使わない。
#
# Claude Code のモデル解決順序:
#   明示 model > agent 定義の frontmatter > CLAUDE_CODE_SUBAGENT_MODEL > メインセッション継承。
#   CLAUDE_CODE_SUBAGENT_MODEL_FORCE (1 / true / yes / on、大文字小文字は区別しない) が設定されている
#   場合のみ、env (未設定ならメインセッションのモデル) がこの順序の全てを上書きする。
#   subagent_type が fork のサブエージェントは、model 指定にも env にも依らずメインセッションの
#   モデルを継承する。
#
# 判定順序 (上から評価し、最初に該当した結果を返す):
#   1. fork (subagent_type が fork) → メインセッションのモデルで判定する (継承経路と同じ扱い)
#   2. CLAUDE_CODE_SUBAGENT_MODEL_FORCE が有効 → 実効モデルは env (非空ならその値、空なら
#      メインセッションのモデル)。fable なら model の明示に依らず deny する。非 fable でも
#      専用 agent への委任は deny する (FORCE 下では agent 定義の model: fable が無視され、
#      専用 agent が Fable 以外のモデルで effort low のまま実行されるため)
#   3. tool_input.model に fable が明示指定されている:
#      a. subagent_type が上記 2 種の専用 agent に完全一致 (namespace prefix 必須、前後空白は
#         trim、大文字小文字は区別):
#         - CLAUDE_CODE_EFFORT_LEVEL が設定されていて low 以外 → deny。この env は agent 定義
#           frontmatter の effort より優先されるため、専用 agent が low 以外の effort で Fable 枠を
#           消費してしまう
#         - それ以外 → 「Fable 週次枠の使用率判定」へ
#      b. それ以外の subagent_type → deny (Fable は専用 agent 経由に限る)
#   4. tool_input.model が非 fable の具体指定 → allow (明示は env より優先されるため)
#   5. tool_input.model 未指定 (= メインセッション継承経路):
#      a. subagent_type が専用 agent 2 種を指す → deny。専用 agent の frontmatter は
#         model: fable であり、継承経路では使用率判定を通さずに Fable が起動しうる。
#         `model: "fable"` を明示して再実行すれば使用率判定が働く
#      b. それ以外:
#         - env が非空: fable なら deny、それ以外は allow (env が実効モデルになるため)
#         - env 不在 + subagent 内 (hook 入力に agent_id あり) からの起動 → deny。継承先は起動元
#           subagent のモデルで、session model state では判定できない (Fable 専用 agent からの
#           起動で専用 agent 以外の subagent が Fable を継承する経路を閉じる)
#         - env 不在: session model state
#           (`${TMPDIR:-/tmp}/agent-discipline-state/model-<session_id>`、inject-always.sh が
#           SessionStart で記録し update-model-on-switch.sh が /model 切替で更新する) が
#           fable の場合のみ deny
#         - env 不在 + state 不明: pending マーカー
#           (`${TMPDIR:-/tmp}/agent-discipline-state/pending-model-<session_id>`) が存在すれば
#           deny する。モデル判定不能期間は継承先が Fable でも state から検知できないため。
#           マーカーも無い真の情報ゼロの場合は fail-open (allow)
#
# Fable 週次枠の使用率判定:
#   - 使用率は natsuume-statusline が書く cache
#     `${XDG_CACHE_HOME:-$HOME/.cache}/natsuume-statusline/weekly-scoped.json` から読む。
#     本 hook は cache を書かず、OAuth usage API も直接呼ばない
#   - 閾値は env EXPERIMENTAL_FABLE_SUBAGENT_MAX_PERCENT (0〜100 の 10 進整数)。未設定・空・
#     範囲外・非整数は 50 に fallback する (deny 理由にはしない)
#   - `weekly_scoped[]` のうち display_name が大文字小文字を無視して fable を含み、percent が
#     数値である entry を対象とする。複数該当する場合は percent の最大値で判定する
#   - percent <= 閾値 なら allow (無出力 exit 0)
#   - 閾値超過、および cache 不在・通常ファイルでない (symlink 含む)・読めない・JSON として
#     parse できない・fetched_at 欠落/非数値・fetched_at が 1800 秒より古い (stale)・
#     weekly_scoped 欠落/非配列/空・Fable entry 無し・percent が全て非数値 は deny する
#     (fail-closed)。使用率を確認できないまま Fable 枠を消費しないため
#   - fetched_at が未来の時刻でも stale とはみなさない (時計ずれを許容する)
#   - percent は小数を取りうるため、閾値との比較は jq で行う (シェルの整数比較を使わない)
#
# 正規化ポリシー:
#   - env / tool_input.model とも前後空白を trim し、"inherit" (case-insensitive) は
#     「未指定」に正規化する (inherit は継承の別表記であり具体的なモデル選択ではないため)
#   - 非空・非 inherit の env 値は、fable を含まない限り authoritative な非 fable 値として
#     信頼する (env の妥当性検証は Claude Code 本体と利用者の責務で、hook の確信境界の外)
#   - subagent_type は前後空白のみ trim し、許可の判定は正規の綴りとの完全一致
#     (大文字小文字を区別) に限る (許可対象を固定リストで閉じるため、別名を受け付けない)
#   - deny 側の判定は広く取る: Claude Code の agent 解決は大文字小文字と空白・`-`・`_` を
#     無視して一致させるため、小文字化して空白・`-`・`_` を除去した値が専用 agent
#     (namespace 付き / 無しのどちらでも) に一致すれば専用 agent とみなして deny する。綴りの
#     揺れた subagent_type が継承経路へ抜けて frontmatter の Fable が gate なしで起動するのを
#     防ぐ。NFKC 正規化 (全角英数等) は行わない
#
# 既知の制約:
#   - agent 定義 frontmatter の model / effort は tool_input に現れないため hook からは検証
#     できない。effort low の保証は専用 agent 定義の frontmatter と、上記の subagent_type
#     完全一致判定に依存する
#   - model 未指定 + frontmatter が fable を指す構成は、専用 agent 以外では捕捉不能
#     (専用 agent は継承経路の広い名前判定で捕捉する)
#   - effort の実効値は hook からは観測できない。CLAUDE_CODE_EFFORT_LEVEL の deny は env による
#     上書きだけを防ぎ、それ以外の経路 (将来の launch 既定値等) で frontmatter の effort: low が
#     上書きされる場合は検知できない
#   - 使用率判定は Agent / Task tool の起動時の入場判定に限る。起動後の継続 (SendMessage による
#     再開)・Workflow 内部の agent() は PreToolUse で観測できず再判定しない
#   - Workflow ツール内部の agent() 呼び出しは PreToolUse では捕捉できない
#   - jq 不在時は何もせず exit 0 (jq は plugin 全体の前提であり、本 hook 単独では fail-closed に
#     しない)。hook_event_name が PreToolUse 以外の入力にも応答しない

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# 各フィールドは 1 行 1 値で読むため、値に含まれる改行 (CR / LF) は空白に置き換えて欄ずれを防ぐ
# (subagent_type に改行を含めても、後続の session_id 等が別の欄に読み込まれない)。
{ read -r HOOK_EVENT; read -r TOOL_MODEL; read -r SUBAGENT_TYPE; read -r SESSION_ID; read -r AGENT_ID; } < <(
  printf '%s' "$INPUT" | jq -r '
    [ (.hook_event_name // ""),
      (.tool_input.model // ""),
      (.tool_input.subagent_type // ""),
      (.session_id // ""),
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

# Fable を許可する専用 agent の subagent_type (完全一致・大文字小文字を区別)。
is_dedicated_fable_agent() {
  case "$1" in
    experimental-agent-discipline:fable-low-worker) return 0 ;;
    experimental-agent-discipline:fable-low-explorer) return 0 ;;
  esac
  return 1
}

# deny 側の広い判定: Claude Code の agent 解決と同じく大文字小文字・空白・`-`・`_` を無視し、
# namespace の有無も問わずに専用 agent を指しているかを判定する。
resolves_to_dedicated_fable_agent() {
  normalized=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]_-')
  case "$normalized" in
    experimentalagentdiscipline:fablelowworker|fablelowworker) return 0 ;;
    experimentalagentdiscipline:fablelowexplorer|fablelowexplorer) return 0 ;;
  esac
  return 1
}

TOOL_MODEL=$(trim "$TOOL_MODEL")
SUBAGENT_TYPE=$(trim "$SUBAGENT_TYPE")
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

# session model state と pending マーカーを読む (メインセッションのモデル判定に使う)。
SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9._-')
STATE_DIR="${TMPDIR:-/tmp}/agent-discipline-state"
SESSION_MODEL=""
SESSION_MODEL_KNOWN=0
PENDING_MODEL=0
if [ -n "$SAFE_SESSION_ID" ]; then
  STATE_FILE="$STATE_DIR/model-$SAFE_SESSION_ID"
  if [ -r "$STATE_FILE" ]; then
    SESSION_MODEL=$(cat "$STATE_FILE" 2>/dev/null)
    SESSION_MODEL_KNOWN=1
  elif [ -e "$STATE_DIR/pending-model-$SAFE_SESSION_ID" ]; then
    PENDING_MODEL=1
  fi
fi

PENDING_DENY_REASON="agent-discipline: このセッションはモデル判定不能期間 (pending) のため、メインセッションのモデルを継承する起動 (model 未指定 / fork) を一時的に deny しています。継承先が Fable になる可能性があり、この期間は state が未確定で検知できません。model に非 Fable モデル (例: sonnet) を明示した新規起動に切り替えるか、会話を 1 turn 進めて one-shot 補正でモデルが確定するのを待ってから再実行してください。"

FORCE_DEDICATED_DENY_REASON="experimental-agent-discipline: CLAUDE_CODE_SUBAGENT_MODEL_FORCE が有効なため、Fable 専用 agent への委任を deny しました。FORCE が有効な間は agent 定義 frontmatter の model: fable が無視され、専用 agent が Fable 以外のモデルで effort low のまま実行されます。FORCE を解除してから再実行するか、model に sonnet / opus を明示して通常のサブエージェントへ委任してください。この env はセッションを超える設定のため独断で書き換えず、解除が必要な場合はユーザに依頼してください。"

# FORCE 有効 + env 未設定で pending 中の deny 理由。継承経路の pending 文言 (model の明示を
# 誘導する) は FORCE 下では従っても結果が変わらないため、実際に有効な対処だけを書く。
FORCE_PENDING_DENY_REASON="agent-discipline: CLAUDE_CODE_SUBAGENT_MODEL_FORCE が有効で CLAUDE_CODE_SUBAGENT_MODEL が未設定のため、全サブエージェントがメインセッションのモデルで実行されますが、このセッションはモデル判定不能期間 (pending) のため継承先が Fable かどうかを検知できません。model を明示しても実効モデルは変わらないため、会話を 1 turn 進めて one-shot 補正でモデルが確定するのを待ってから再実行するか、FORCE の解除または CLAUDE_CODE_SUBAGENT_MODEL への sonnet / opus の設定をユーザに依頼してください (どちらもセッションを超える設定のため独断で書き換えない)。"

# メインセッションのモデルで allow / deny を決める (継承経路・fork・FORCE で共有する)。
# $1 = メインセッションが Fable と判明した場合の deny 理由。
# $2 = pending 中 (モデル判定不能期間) の deny 理由。省略時は継承経路の文言。
decide_by_session_model() {
  if [ "$SESSION_MODEL_KNOWN" -eq 1 ]; then
    if is_fable "$SESSION_MODEL"; then
      deny "$1"
    fi
    exit 0
  fi
  if [ "$PENDING_MODEL" -eq 1 ]; then
    deny "${2:-$PENDING_DENY_REASON}"
  fi
  exit 0
}

# 閾値: 0〜100 の 10 進整数のみ受け付け、それ以外は既定値へ fallback する。
DEFAULT_MAX_PERCENT=50
MAX_PERCENT=$(trim "${EXPERIMENTAL_FABLE_SUBAGENT_MAX_PERCENT:-}")
if ! printf '%s' "$MAX_PERCENT" | grep -qE '^(0|[1-9][0-9]?|100)$'; then
  MAX_PERCENT="$DEFAULT_MAX_PERCENT"
fi

# 使用率判定の deny には、cache の producer である natsuume-statusline の構成手順と、
# 通常のサブエージェントへの切り替え手段を必ず添える (自己修復可能な deny にするため)。
deny_usage() {
  deny "experimental-agent-discipline: Fable 専用 agent への委任は、Fable 週次枠の使用率が閾値 (${MAX_PERCENT}%) 以下のときだけ許可されます。$1 使用率を確認できない状態では Fable 枠を消費しないため deny します (fail-closed)。使用率 cache の producer は natsuume-statusline です。statusline として構成されていない場合は /natsuume-statusline:setup で構成してください。すぐに委任したい場合は model に sonnet / opus (機械的作業なら haiku) を明示して通常のサブエージェントへ委任してください。"
}

check_fable_weekly_usage() {
  CACHE_FILE="${XDG_CACHE_HOME:-${HOME:-}/.cache}/natsuume-statusline/weekly-scoped.json"

  if [ -L "$CACHE_FILE" ]; then
    deny_usage "使用率 cache ($CACHE_FILE) が symlink です。通常ファイル以外は読み取り対象にしません。"
  fi
  if [ ! -f "$CACHE_FILE" ]; then
    deny_usage "使用率 cache ($CACHE_FILE) が通常ファイルとして存在しません。"
  fi
  if [ ! -r "$CACHE_FILE" ]; then
    deny_usage "使用率 cache ($CACHE_FILE) を読み取れません。"
  fi

  NOW=$(date +%s 2>/dev/null)
  if ! printf '%s' "$NOW" | grep -qE '^[0-9]+$'; then
    deny_usage "現在時刻を取得できず、使用率 cache の鮮度を判定できません。"
  fi

  CACHE_CONTENT=$(cat "$CACHE_FILE" 2>/dev/null)

  # cache の検査と閾値比較を jq に閉じる: percent は小数を取りうるため、シェルの整数比較には
  # 頼らない。--slurp で入力全体を配列として受け取り、JSON document がちょうど 1 つの場合だけ
  # 判定する (複数 document の連結や空入力は invalid)。出力は 4 行 (status / percent /
  # resets_at / 固定終端 END) で、status が ok 以外は deny 理由。終端行を置くのは、command
  # substitution が末尾の改行を落としても、resets_at が空のときに行数契約が崩れないようにする
  # ため。jq の exit status と出力行数・終端行も検証し、契約どおりの出力が得られない場合は
  # parse 失敗として deny する (fail-closed)。resets_at は ISO 8601 形式に一致する場合だけ
  # deny 文に載せる (cache 由来の文字列を無検証で判定文へ反映しない)。
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
    deny_usage "使用率 cache を JSON として読み取れません。"
  fi

  case "$GATE_STATUS" in
    ok)
      return 0
      ;;
    over)
      RESETS_NOTE=""
      if [ -n "$GATE_RESETS" ]; then
        RESETS_NOTE=" 枠のリセットは $GATE_RESETS です。"
      fi
      deny_usage "Fable 週次枠の使用率が ${GATE_PERCENT}% で、閾値 ${MAX_PERCENT}% を超えています。${RESETS_NOTE}"
      ;;
    fetched_at)
      deny_usage "使用率 cache の fetched_at が欠落しているか数値ではありません。"
      ;;
    stale)
      deny_usage "使用率 cache が古すぎます (fetched_at が 1800 秒より前)。"
      ;;
    entries)
      deny_usage "使用率 cache に Fable の週次枠 entry (display_name が fable を含み percent が数値) が見つかりません。"
      ;;
    *)
      deny_usage "使用率 cache を JSON として読み取れません。"
      ;;
  esac
}

# 1. fork は model 指定も env も無視してメインセッションのモデルを継承する
if [ "$SUBAGENT_TYPE" = "fork" ]; then
  decide_by_session_model "agent-discipline: fork のサブエージェントは model 指定にも env にも依らずメインセッション (Fable) のモデルを継承します。fork をやめ、必要な文脈を指示文に埋め込んだ新規起動で model に sonnet / opus (機械的作業なら haiku) を明示して再実行してください。"
fi

# 2. FORCE が有効なら実効モデルは env (非空ならその値、空ならメインセッションのモデル)
if [ "$FORCE_ENABLED" -eq 1 ]; then
  if [ -n "$ENV_SUB" ]; then
    if is_fable "$ENV_SUB"; then
      deny "agent-discipline: CLAUDE_CODE_SUBAGENT_MODEL_FORCE が有効で CLAUDE_CODE_SUBAGENT_MODEL が fable を指すため、全サブエージェントが Fable で実行されます。model を明示しても実効モデルは変わらないため、FORCE の解除または env の sonnet / opus への修正が必要です。どちらもセッションを超える設定のため独断で書き換えず、この状態をユーザに報告して修正を依頼してください。"
    fi
    if resolves_to_dedicated_fable_agent "$SUBAGENT_TYPE"; then
      deny "$FORCE_DEDICATED_DENY_REASON"
    fi
    exit 0
  fi
  if [ "$SESSION_MODEL_KNOWN" -eq 1 ] && is_fable "$SESSION_MODEL"; then
    deny "agent-discipline: CLAUDE_CODE_SUBAGENT_MODEL_FORCE が有効で CLAUDE_CODE_SUBAGENT_MODEL が未設定のため、全サブエージェントがメインセッション (Fable) のモデルで実行されます。model を明示しても実効モデルは変わらないため、FORCE の解除または CLAUDE_CODE_SUBAGENT_MODEL への sonnet / opus の設定が必要です。どちらもセッションを超える設定のため独断で書き換えず、この状態をユーザに報告して修正を依頼してください。"
  fi
  if resolves_to_dedicated_fable_agent "$SUBAGENT_TYPE"; then
    deny "$FORCE_DEDICATED_DENY_REASON"
  fi
  decide_by_session_model "agent-discipline: CLAUDE_CODE_SUBAGENT_MODEL_FORCE が有効で CLAUDE_CODE_SUBAGENT_MODEL が未設定のため、全サブエージェントがメインセッション (Fable) のモデルで実行されます。model を明示しても実効モデルは変わらないため、FORCE の解除または CLAUDE_CODE_SUBAGENT_MODEL への sonnet / opus の設定が必要です。どちらもセッションを超える設定のため独断で書き換えず、この状態をユーザに報告して修正を依頼してください。" "$FORCE_PENDING_DENY_REASON"
fi

# 3. fable の明示指定は専用 agent 経由 + 週次枠に余裕がある場合に限り許可する
if is_fable "$TOOL_MODEL"; then
  if is_dedicated_fable_agent "$SUBAGENT_TYPE"; then
    # CLAUDE_CODE_EFFORT_LEVEL は agent 定義 frontmatter の effort より優先されるため、low 以外が
    # 設定されていると専用 agent の effort low 固定が崩れる (Fable 枠を高 effort で消費する)。
    EFFORT_ENV=$(trim "${CLAUDE_CODE_EFFORT_LEVEL:-}")
    if [ -n "$EFFORT_ENV" ] && ! printf '%s' "$EFFORT_ENV" | grep -qix 'low'; then
      deny "experimental-agent-discipline: CLAUDE_CODE_EFFORT_LEVEL が low 以外 (${EFFORT_ENV}) に設定されているため、Fable 専用 agent への委任を deny しました。この env は agent 定義 frontmatter の effort: low より優先されるため、専用 agent が低 effort の前提を崩して Fable 枠を消費してしまいます。env を解除 (または low に設定) してから再実行するか、model に sonnet / opus を明示して通常のサブエージェントへ委任してください。この env はセッションを超える設定のため独断で書き換えず、変更が必要な場合はユーザに依頼してください。"
    fi
    check_fable_weekly_usage
    exit 0
  fi
  # 専用 agent 以外への fable 明示指定は許可経路の外
  deny "experimental-agent-discipline: Fable のサブエージェントは専用 agent (experimental-agent-discipline:fable-low-worker / experimental-agent-discipline:fable-low-explorer) への委任に限り許可されます (effort は agent 定義の frontmatter で low 固定)。subagent_type をこの 2 種のいずれかに変更して再実行するか、model に sonnet / opus (機械的作業なら haiku) を明示して通常のサブエージェントへ委任してください。"
fi

# 4. 非 fable の具体指定は allow (明示が env より優先されるため env の値に依らない)
if [ -n "$TOOL_MODEL" ]; then
  exit 0
fi

# 5a. 専用 agent を model 未指定で起動すると、frontmatter の model: fable が使用率判定を
# 通らずに適用されるため deny する (明示指定に直せば使用率判定が働く)。綴りの揺れた
# subagent_type も Claude Code 側では同じ agent に解決されるため、広い判定で捕捉する。
if resolves_to_dedicated_fable_agent "$SUBAGENT_TYPE"; then
  deny "experimental-agent-discipline: Fable 専用 agent (experimental-agent-discipline:fable-low-worker / experimental-agent-discipline:fable-low-explorer) は model 未指定 (継承) では起動できません。専用 agent の frontmatter は model: fable のため、継承経路では Fable 週次枠の使用率判定を通さずに Fable が起動しえます。subagent_type を上記の正規の綴りにしたうえで \`model: \"fable\"\` を明示して再実行してください (明示することで使用率判定が働きます)。"
fi

# 5b. それ以外の model 未指定 = 継承経路。env が非空ならその値が実効モデルになる。
if [ -n "$ENV_SUB" ]; then
  if is_fable "$ENV_SUB"; then
    deny "agent-discipline: model 未指定のサブエージェントは CLAUDE_CODE_SUBAGENT_MODEL の値 (fable) で実行されます。model に sonnet / opus (機械的作業なら haiku) を明示して再実行してください。env 自体を sonnet / opus へ直す場合は、セッションを超える設定のため独断で書き換えず、ユーザに依頼してください。"
  fi
  exit 0
fi

# subagent 内 (agent_id あり) からの起動は、継承先がその subagent のモデルであり session model
# state (メインセッションのモデル) では判定できない。Fable 専用 agent から model 未指定で起動
# されると、専用 agent 以外の subagent が使用率判定を通らずに Fable を継承しうるため deny する。
if [ -n "$AGENT_ID" ]; then
  deny "experimental-agent-discipline: subagent 内からの model 未指定 (継承) のサブエージェント起動を deny しました。継承先は起動元 subagent のモデルになり、Fable 専用 agent からの起動では専用 agent 以外の subagent が使用率判定を通らずに Fable で実行されえます。model に sonnet / opus (機械的作業なら haiku) を明示して再実行してください。"
fi

decide_by_session_model "agent-discipline: model 未指定のサブエージェントはメインセッション (Fable) のモデルを継承します。model に sonnet / opus (機械的作業なら haiku) を明示して再実行してください。"
