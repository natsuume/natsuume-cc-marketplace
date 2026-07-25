#!/bin/bash
# inject-discipline.sh
# UserPromptSubmit で発火し、分業規律 (discipline-*.md、モデル別) を additionalContext として
# 配送する (issue #236、注入ペイロード分割の設計契約 §4.3)。旧設計では inject-always.sh が
# 常時ルールと同一 additionalContext の末尾に分業規律を連結していたが、合計文字数が inline
# 配送閾値を超えるため、本スクリプトが独立した要素として UserPromptSubmit 側で配送する。
# PR2 (agent-discipline 0.21.0) で配送を fable / opus / その他非 fable の 3-way に拡張した
# (Opus 5 公式ガイド対応の discipline-opus.md を新設し、fable 判定を常に最優先に維持したまま
# opus 判定を追加した)。
#
# ## マーカーの 3 状態
#
# マーカー `delivered-discipline-<session_id>` は内容として 3 状態を持つ:
#
# - **無し**: 未配送。pending 優先・state 次点 (設計契約 §5) で分岐する:
#   - pending あり → discipline-preamble-self-gate.md + discipline-sonnet.md
#     (見出し「# agent-discipline: 分業規律 (Sonnet)」付き) を注入、マーカーを `sonnet-gate` にする
#   - pending 無し + state が fable → 分業規律 fable 版 (見出し + discipline-preamble-fable.md +
#     discipline-fable.md) を注入、マーカーを `final` にする (fable 判定は常に最優先)
#   - pending 無し + state が非 fable かつ opus → 分業規律 Opus 版 (見出し
#     「# agent-discipline: 分業規律 (Opus)」+ discipline-opus.md) を注入、マーカーを `final`
#     にする
#   - pending 無し + state が非 fable かつ非 opus (haiku 等) → 分業規律 sonnet 版
#     (見出し + discipline-sonnet.md) を注入、マーカーを `final` にする
#   - pending も state も無い異常系 → pending 時と同じ自己ゲート付き配送、マーカーを
#     `sonnet-gate` にする
# - **`sonnet-gate`**: 判定不能時の自己ゲート付き分業規律を配送済み。one-shot 補正の対象:
#   - pending 無し + state が fable に確定していた → 補正前置き + 分業規律 fable 版を注入し、
#     マーカーを `final` に更新する (自己ゲート付きで配送済みの Sonnet 版分業規律を破棄し
#     本要素を優先する旨を前置きに含める)
#   - pending 無し + state が非 fable かつ opus に確定していた → 補正前置き (fable 補正と
#     同型の Opus 版) + 分業規律 Opus 版を注入し、マーカーを `final` に更新する
#   - pending 無し + state が非 fable かつ非 opus に確定していた → 注入なしでマーカーを
#     `final` に更新する (配送済みの Sonnet 版がそのまま確定内容であるため)
#   - pending あり、または state 無し → 何もしない (次プロンプトで再確認。resolve-model-on-prompt.sh
#     の state 書込 → pending 削除が同一 event 内で完了する前に本スクリプトが読んだ場合の
#     最大 1 プロンプト遅延、設計契約 §8-2 の既知の制約)
# - **`final`**: 確定済み。即 exit 0
#
# マーカーの書き込みは注入本文と出力 JSON の生成に成功した後に行う (`sonnet-gate` → `final` の
# 「注入なし」更新は本文生成が無いため直接書く)。マーカーの読み書きは同一ディレクトリ内
# temp file → mv の atomic 書込にする。
#
# ## 出力 JSON 形状 (配送する場合のみ)
#
#   {
#     "hookSpecificOutput": {
#       "hookEventName": "<入力の hook_event_name をそのまま echo>",
#       "additionalContext": "<分岐に応じた分業規律ブロック>"
#     }
#   }
#
# ## fail-open 条件
#
# - jq 不在 / 不正 stdin / hook_event_name・session_id が空
# - 配送対象のペイロード (discipline-preamble-self-gate.md / discipline-sonnet.md /
#   discipline-preamble-fable.md / discipline-fable.md / discipline-opus.md) のいずれかが
#   読めない (空文字列を含む) → 無音 exit 0、マーカーは書かない (次プロンプトで再試行)

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

{ read -r HOOK_EVENT; read -r SESSION_ID; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.hook_event_name // ""),
    (.session_id // "")
  ' 2>/dev/null
)

if [ -z "$HOOK_EVENT" ] || [ -z "$SESSION_ID" ]; then
  exit 0
fi

SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9._-')
if [ -z "$SAFE_SESSION_ID" ]; then
  exit 0
fi

STATE_DIR="${TMPDIR:-/tmp}/agent-discipline-state"
MARKER="$STATE_DIR/delivered-discipline-$SAFE_SESSION_ID"
PENDING_FILE="$STATE_DIR/pending-model-$SAFE_SESSION_ID"
STATE_FILE="$STATE_DIR/model-$SAFE_SESSION_ID"

MARKER_STATE=""
if [ -f "$MARKER" ]; then
  MARKER_STATE=$(cat "$MARKER" 2>/dev/null)
fi

if [ "$MARKER_STATE" = "final" ]; then
  exit 0
fi

PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)

# $1 = マーカーに書く内容 (sonnet-gate|final)。同一ディレクトリ内 temp file → mv で atomic に書く。
write_marker() {
  local content tmp
  content="$1"
  if ! mkdir -p "$STATE_DIR" 2>/dev/null; then
    return 1
  fi
  tmp="$MARKER.tmp.$$"
  # 2>/dev/null は「>」より前に置く (bash の出力リダイレクト失敗は後置の 2>/dev/null では
  # 抑制できないため、無音 fail-open のため先に stderr を /dev/null へ向ける)。
  if ! printf '%s' "$content" 2>/dev/null > "$tmp"; then
    rm -f "$tmp" 2>/dev/null
    return 1
  fi
  if ! mv "$tmp" "$MARKER" 2>/dev/null; then
    rm -f "$tmp" 2>/dev/null
    return 1
  fi
  return 0
}

# $1 = additionalContext 本文。成功したら JSON を stdout に出力し 0 を返す。
emit() {
  local out
  out=$(jq -n --arg evt "$HOOK_EVENT" --arg ctx "$1" '{
    hookSpecificOutput: {
      hookEventName: $evt,
      additionalContext: $ctx
    }
  }')
  if [ -z "$out" ]; then
    return 1
  fi
  printf '%s\n' "$out"
  return 0
}

if [ -z "$MARKER_STATE" ]; then
  if [ -f "$PENDING_FILE" ]; then
    SELF_GATE_PREAMBLE=$(cat "$PROMPTS_DIR/discipline-preamble-self-gate.md" 2>/dev/null)
    SONNET_BODY=$(cat "$PROMPTS_DIR/discipline-sonnet.md" 2>/dev/null)
    if [ -z "$SELF_GATE_PREAMBLE" ] || [ -z "$SONNET_BODY" ]; then
      exit 0
    fi
    CONTEXT="# agent-discipline: 分業規律 (Sonnet)

$SELF_GATE_PREAMBLE

$SONNET_BODY"
    if emit "$CONTEXT"; then
      write_marker "sonnet-gate"
    fi
    exit 0
  fi

  if [ -f "$STATE_FILE" ]; then
    MODEL=$(cat "$STATE_FILE" 2>/dev/null)
    if printf '%s' "$MODEL" | grep -qi 'fable'; then
      FABLE_PREAMBLE=$(cat "$PROMPTS_DIR/discipline-preamble-fable.md" 2>/dev/null)
      FABLE_BODY=$(cat "$PROMPTS_DIR/discipline-fable.md" 2>/dev/null)
      if [ -z "$FABLE_PREAMBLE" ] || [ -z "$FABLE_BODY" ]; then
        exit 0
      fi
      CONTEXT="# agent-discipline: 分業規律 (Fable セッション)

$FABLE_PREAMBLE

$FABLE_BODY"
    elif printf '%s' "$MODEL" | grep -qi 'opus'; then
      OPUS_BODY=$(cat "$PROMPTS_DIR/discipline-opus.md" 2>/dev/null)
      if [ -z "$OPUS_BODY" ]; then
        exit 0
      fi
      CONTEXT="# agent-discipline: 分業規律 (Opus)

$OPUS_BODY"
    else
      SONNET_BODY=$(cat "$PROMPTS_DIR/discipline-sonnet.md" 2>/dev/null)
      if [ -z "$SONNET_BODY" ]; then
        exit 0
      fi
      CONTEXT="# agent-discipline: 分業規律 (Sonnet)

$SONNET_BODY"
    fi
    if emit "$CONTEXT"; then
      write_marker "final"
    fi
    exit 0
  fi

  # pending も state も無い異常系: pending 時と同じ自己ゲート付き配送にフォールバックする。
  SELF_GATE_PREAMBLE=$(cat "$PROMPTS_DIR/discipline-preamble-self-gate.md" 2>/dev/null)
  SONNET_BODY=$(cat "$PROMPTS_DIR/discipline-sonnet.md" 2>/dev/null)
  if [ -z "$SELF_GATE_PREAMBLE" ] || [ -z "$SONNET_BODY" ]; then
    exit 0
  fi
  CONTEXT="# agent-discipline: 分業規律 (Sonnet)

$SELF_GATE_PREAMBLE

$SONNET_BODY"
  if emit "$CONTEXT"; then
    write_marker "sonnet-gate"
  fi
  exit 0
fi

if [ "$MARKER_STATE" = "sonnet-gate" ]; then
  if [ -f "$PENDING_FILE" ] || [ ! -f "$STATE_FILE" ]; then
    # pending がまだ残っている、または state が無い: モデル未確定として何もしない
    # (resolve-model-on-prompt.sh の state 書込 → pending 削除の完了待ち、最大 1 プロンプト遅延)。
    exit 0
  fi

  MODEL=$(cat "$STATE_FILE" 2>/dev/null)
  if printf '%s' "$MODEL" | grep -qi 'fable'; then
    FABLE_PREAMBLE=$(cat "$PROMPTS_DIR/discipline-preamble-fable.md" 2>/dev/null)
    FABLE_BODY=$(cat "$PROMPTS_DIR/discipline-fable.md" 2>/dev/null)
    if [ -z "$FABLE_PREAMBLE" ] || [ -z "$FABLE_BODY" ]; then
      exit 0
    fi
    CORRECTION_PREFIX="(one-shot 補正) セッション開始時点ではモデルを判定できず、自己ゲート付きで SONNET 向けの分業規律を暫定配送していた。会話の進行によりこのセッションのモデルが Fable であると確定したため、以後は本メッセージ以下の Fable 版分業規律を優先し、先に配送済みの Sonnet 版分業規律は破棄すること。"
    CONTEXT="$CORRECTION_PREFIX

# agent-discipline: 分業規律 (Fable セッション)

$FABLE_PREAMBLE

$FABLE_BODY"
    if emit "$CONTEXT"; then
      write_marker "final"
    fi
    exit 0
  fi

  if printf '%s' "$MODEL" | grep -qi 'opus'; then
    OPUS_BODY=$(cat "$PROMPTS_DIR/discipline-opus.md" 2>/dev/null)
    if [ -z "$OPUS_BODY" ]; then
      exit 0
    fi
    CORRECTION_PREFIX="(one-shot 補正) セッション開始時点ではモデルを判定できず、自己ゲート付きで SONNET 向けの分業規律を暫定配送していた。会話の進行によりこのセッションのモデルが Opus 系であると確定したため、以後は本メッセージ以下の Opus 版分業規律を優先し、先に配送済みの Sonnet 版分業規律は破棄すること。"
    CONTEXT="$CORRECTION_PREFIX

# agent-discipline: 分業規律 (Opus)

$OPUS_BODY"
    if emit "$CONTEXT"; then
      write_marker "final"
    fi
    exit 0
  fi

  # 非 fable かつ非 opus に確定: 自己ゲート付きで配送済みの Sonnet 版がそのまま確定内容のため、
  # 追加の注入はせずマーカーのみ final に更新する。
  write_marker "final"
  exit 0
fi

exit 0
