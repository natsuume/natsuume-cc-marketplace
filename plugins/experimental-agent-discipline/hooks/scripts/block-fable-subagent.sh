#!/bin/bash
# block-fable-subagent.sh
# PreToolUse (matcher: Agent|Task) でサブエージェントのモデル解決経路を判定する gate。
# Fable は effort low 固定の専用 agent
# (experimental-agent-discipline:fable-low-worker / experimental-agent-discipline:fable-low-explorer)
# への明示委任に限り、Fable 週次枠の使用率が閾値以下のあいだだけ許可し、それ以外の Fable 実行
# 経路は deny する。
#
# 判定順序は Claude Code のモデル解決順序 (CLAUDE_CODE_SUBAGENT_MODEL env > tool_input.model
# 明示指定 > agent frontmatter > メインセッション継承) と一致させる。すべて deterministic な
# 文字列判定で LLM 評価は使わない。
#
#   0. env が fable を指す → tool_input.model の値に依らず無条件 deny
#      (env は明示指定より優先されるため、明示 sonnet/opus でも実行モデルは fable になる)
#   1. tool_input.model に fable が明示指定されている:
#      a. subagent_type が上記 2 種の専用 agent に完全一致 (namespace prefix 必須、前後空白は
#         trim、大文字小文字は区別):
#         - env が非空 (= Step 0 を通過した非 fable 値) → deny。env は明示指定より優先されるため、
#           専用 agent が Fable 以外のモデルで effort low 実行されてしまう
#         - env 未指定 → 「Fable 週次枠の使用率判定」へ
#      b. それ以外の subagent_type → deny (Fable は専用 agent 経由に限る)
#   2. tool_input.model が非 fable の具体指定 → allow (Step 0 より env は非 fable 確定)
#   3. tool_input.model 未指定 (= 継承経路):
#      a. subagent_type が専用 agent 2 種に完全一致 → deny。専用 agent の frontmatter は
#         model: fable であり、継承経路では使用率判定を通さずに Fable が起動しうる。
#         `model: "fable"` を明示して再実行すれば使用率判定が働く
#      b. それ以外:
#         - env が非空 → allow (env が継承を非 fable モデルへ上書きするため安全)
#         - env 不在: inject-always.sh が SessionStart で記録した session model state が
#           fable の場合のみ deny
#         - env 不在 + state 不明: pending マーカー
#           `${TMPDIR:-/tmp}/agent-discipline-state/pending-model-<session_id>` が存在する場合は
#           deny する。判定不能セッションの実体が Fable のとき、未指定継承は継承先が Fable に
#           なり、この時点では state も未確定のため他の防御が効かない。deny メッセージには
#           「モデル確定 (one-shot 補正) までは model に非 Fable (sonnet 等) を明示して再実行
#           する」自己修復誘導を含める。明示非 Fable 指定は Step 2 で allow 済みのため、
#           pending 中でも明示指定の委任は妨げない
#         - env 不在 + state 不明 + pending マーカーも無し → allow (真の情報ゼロは fail-open)
#
# Fable 週次枠の使用率判定 (Step 1a):
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
#   - subagent_type は前後空白のみ trim し、許可 (Step 1a) の判定は正規の綴りとの完全一致
#     (大文字小文字を区別) に限る (許可対象を固定リストで閉じるため、別名を受け付けない)
#   - deny 側 (Step 3a) の判定は広く取る: Claude Code の agent 解決は大文字小文字と空白・`-`・
#     `_` を無視して一致させるため、小文字化して空白・`-`・`_` を除去した値が専用 agent
#     (namespace 付き / 無しのどちらでも) に一致すれば専用 agent とみなして deny する。綴りの
#     揺れた subagent_type が継承経路 (Step 3b) へ抜けて frontmatter の Fable が gate なしで
#     起動するのを防ぐ。NFKC 正規化 (全角英数等) は行わない
#
# 既知の制約:
#   - agent 定義 frontmatter の model / effort は tool_input に現れないため hook からは検証
#     できない。effort low の保証は専用 agent 定義の frontmatter と、上記の subagent_type
#     完全一致判定に依存する
#   - env 不在 + model 未指定 + frontmatter が fable を指す構成は、専用 agent 以外では捕捉
#     不能 (env 側でカバー)
#   - fork subagent (model 指定を無視して親モデルを継承する型) は deny しない
#     (誘導層の「原則使用しない」文言のみで運用する設計判断)
#   - Workflow ツール内部の agent() 呼び出しは PreToolUse では捕捉できない (env 側でカバー)
#   - セッション途中の /model 切替は検知できない (model を含む hook 入力は SessionStart のみで、
#     $CLAUDE_MODEL 環境変数も存在しない)。env 不在時は state file が次の SessionStart まで
#     stale になり、fable への切替は素通り (Step 3b が旧 state で allow)、fable からの切替は
#     誤 deny になる (deny メッセージの model 明示誘導で自己修復可能)。README の既知の制約参照
#   - jq 不在時は何もせず exit 0 (jq は plugin 全体の前提であり、本 hook 単独では fail-closed に
#     しない)。hook_event_name が PreToolUse 以外の入力にも応答しない

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# 各フィールドは 1 行 1 値で読むため、値に含まれる改行 (CR / LF) は空白に置き換えて欄ずれを防ぐ
# (subagent_type に改行を含めても、後続の session_id 等が別の欄に読み込まれない)。
{ read -r HOOK_EVENT; read -r TOOL_MODEL; read -r SUBAGENT_TYPE; read -r SESSION_ID; } < <(
  printf '%s' "$INPUT" | jq -r '
    [ (.hook_event_name // ""),
      (.tool_input.model // ""),
      (.tool_input.subagent_type // ""),
      (.session_id // "") ]
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

# "inherit" (case-insensitive) は「未指定 = 継承」の別表記として正規化する
if printf '%s' "$TOOL_MODEL" | grep -qix 'inherit'; then
  TOOL_MODEL=""
fi
if printf '%s' "$ENV_SUB" | grep -qix 'inherit'; then
  ENV_SUB=""
fi

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

# 0. env が fable を強制していれば、モデル解決の最上位で fable が確定するため無条件 deny
if [ -n "$ENV_SUB" ] && printf '%s' "$ENV_SUB" | grep -qi 'fable'; then
  deny "agent-discipline: CLAUDE_CODE_SUBAGENT_MODEL が fable を指しており、model の明示指定より優先されて全サブエージェントが Fable で実行されます。この env はセッションを超える設定のため独断で書き換えず、この状態をユーザに報告して、settings.json 等の env 設定を sonnet / opus へ修正するよう依頼してください。"
fi

# 1. fable の明示指定は専用 agent 経由 + 週次枠に余裕がある場合に限り許可する
if printf '%s' "$TOOL_MODEL" | grep -qi 'fable'; then
  if is_dedicated_fable_agent "$SUBAGENT_TYPE"; then
    # 1a: env は Step 0 を通過しているので非 fable 値。ただし env は明示指定より優先される
    # ため、この委任は Fable 以外のモデルが effort low で走る (専用 agent の前提が崩れる)。
    if [ -n "$ENV_SUB" ]; then
      deny "experimental-agent-discipline: CLAUDE_CODE_SUBAGENT_MODEL が設定されているため、Fable 専用 agent への委任を deny しました。この env は model の明示指定より優先されるため、専用 agent が Fable 以外のモデルで effort low のまま実行されてしまいます。env を解除してから再実行するか、model に sonnet / opus を明示して通常のサブエージェントへ委任してください。この env はセッションを超える設定のため独断で書き換えず、解除が必要な場合はユーザに依頼してください。"
    fi
    check_fable_weekly_usage
    exit 0
  fi
  # 1b: 専用 agent 以外への fable 明示指定は許可経路の外
  deny "experimental-agent-discipline: Fable のサブエージェントは専用 agent (experimental-agent-discipline:fable-low-worker / experimental-agent-discipline:fable-low-explorer) への委任に限り許可されます (effort は agent 定義の frontmatter で low 固定)。subagent_type をこの 2 種のいずれかに変更して再実行するか、model に sonnet / opus (機械的作業なら haiku) を明示して通常のサブエージェントへ委任してください。"
fi

# 2. 非 fable の具体指定は allow (Step 0 より env は非 fable 確定なので上書きされても安全)
if [ -n "$TOOL_MODEL" ]; then
  exit 0
fi

# 3a. 専用 agent を model 未指定で起動すると、frontmatter の model: fable が使用率判定を
# 通らずに適用されるため deny する (明示指定に直せば Step 1a の使用率判定が働く)。綴りの揺れた
# subagent_type も Claude Code 側では同じ agent に解決されるため、広い判定で捕捉する。
if resolves_to_dedicated_fable_agent "$SUBAGENT_TYPE"; then
  deny "experimental-agent-discipline: Fable 専用 agent (experimental-agent-discipline:fable-low-worker / experimental-agent-discipline:fable-low-explorer) は model 未指定 (継承) では起動できません。専用 agent の frontmatter は model: fable のため、継承経路では Fable 週次枠の使用率判定を通さずに Fable が起動しえます。subagent_type を上記の正規の綴りにしたうえで \`model: \"fable\"\` を明示して再実行してください (明示することで使用率判定が働きます)。"
fi

# 3b. それ以外の model 未指定 = メインセッション継承経路
if [ -n "$ENV_SUB" ]; then
  # 非空・非 inherit の env は authoritative な非 fable 値として信頼する (正規化ポリシー参照)
  exit 0
fi

SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9._-')
if [ -z "$SAFE_SESSION_ID" ]; then
  exit 0
fi

STATE_FILE="${TMPDIR:-/tmp}/agent-discipline-state/model-$SAFE_SESSION_ID"
if [ ! -r "$STATE_FILE" ]; then
  # state 不明。pending マーカーが存在する場合、このセッションはモデル判定不能期間中であり、
  # 実体が Fable なら未指定継承の継承先が Fable になる。pending マーカーも無い真の情報ゼロの
  # 場合のみ fail-open。
  PENDING_MARKER="${TMPDIR:-/tmp}/agent-discipline-state/pending-model-$SAFE_SESSION_ID"
  if [ -e "$PENDING_MARKER" ]; then
    deny "agent-discipline: このセッションはモデル判定不能期間 (pending) のため、model 未指定 (継承) のサブエージェント起動を一時的に deny しています。継承先が Fable になる可能性があり、この期間は state が未確定で検知できません。model に非 Fable モデル (例: sonnet) を明示して再実行するか、会話を 1 turn 進めて one-shot 補正でモデルが確定するのを待ってから再実行してください。"
  fi
  exit 0
fi

SESSION_MODEL=$(cat "$STATE_FILE" 2>/dev/null)
if printf '%s' "$SESSION_MODEL" | grep -qi 'fable'; then
  deny "agent-discipline: model 未指定のサブエージェントはメインセッション (Fable) のモデルを継承します。model に sonnet / opus (機械的作業なら haiku) を明示して再実行してください。この deny が出た時点で CLAUDE_CODE_SUBAGENT_MODEL は未設定 (または inherit) のため、主防御である env の設定 (sonnet 等) をユーザに提案するのも有効です。"
fi

exit 0
