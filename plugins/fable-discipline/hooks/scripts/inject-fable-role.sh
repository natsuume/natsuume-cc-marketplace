#!/bin/bash
# inject-fable-role.sh
# SessionStart で Fable セッション向けの分業規律 (誘導層) を additionalContext として注入する。
#
# 注入判定 (ハイブリッド方式):
#   - stdin の model フィールドが fable → 無条件文を注入
#   - model フィールドが取得できない (/clear 直後や会話復元時に欠落しうる、公式仕様) →
#     自己ゲート文付きで注入 (モデルは自身の system prompt で自分が Fable か判別できるため、
#     ゲートは受信側で確実に機能する)
#   - model フィールドが fable 以外 → 何もしない (非 Fable セッションを汚さない)
#
# 併せて、判定できた model 値を session_id キーの state file に書き出す
# (block-fable-subagent.sh が「model 未指定 = メインセッション継承」経路の判定に使う)。
#
# jq が無い環境では何もしない (fail-open)。SessionStart は startup / resume / clear / compact
# の全 source で発火するため、長いセッションでも compact 後に規律が再注入される。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# hook_event_name / model / session_id を 1 回の jq 呼び出しで取得する。
# INPUT が不正な JSON の場合の parse error は 2>/dev/null で抑制し、
# HOOK_EVENT 空判定でフォールバックさせる (hook の stderr は利用者に見えるため)。
{ read -r HOOK_EVENT; read -r MODEL; read -r SESSION_ID; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.hook_event_name // ""),
    (.model // ""),
    (.session_id // "")
  ' 2>/dev/null
)

# hook_event_name が取れなければイベント名を正しくエコーできないので無音終了する
# (誤った既定値で hookSpecificOutput.hookEventName を返すと別 event の文脈に誘導する恐れがある)。
if [ -z "$HOOK_EVENT" ]; then
  exit 0
fi

# state file: block-fable-subagent.sh との共有。session_id はパス文字を除去してから使う。
# 書き込み失敗は無視する (state はあくまで防波堤 hook の補助情報で、無ければ fail-open)。
if [ -n "$MODEL" ] && [ -n "$SESSION_ID" ]; then
  SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9._-')
  if [ -n "$SAFE_SESSION_ID" ]; then
    STATE_DIR="${TMPDIR:-/tmp}/fable-discipline-state"
    if mkdir -p "$STATE_DIR" 2>/dev/null; then
      printf '%s' "$MODEL" > "$STATE_DIR/model-$SAFE_SESSION_ID" 2>/dev/null
    fi
  fi
fi

IS_FABLE=0
if printf '%s' "$MODEL" | grep -qi 'fable'; then
  IS_FABLE=1
fi

# 非 Fable と判定できたセッションには注入しない
if [ -n "$MODEL" ] && [ "$IS_FABLE" -eq 0 ]; then
  exit 0
fi

if [ "$IS_FABLE" -eq 1 ]; then
  PREAMBLE='以下のルールは、Fable で動作しているこのメインセッションに適用される。'
else
  PREAMBLE='(自己ゲート) このセッションのモデルを hook からは判定できなかった。あなた (メインセッション) のモデルが Fable の場合のみ以下を適用し、Fable でない場合はこのセクション全体を無視すること。自分のモデルは自身の system prompt (Environment セクション) で確認できる。'
fi

BODY=$(cat <<'EOF'
## 1. 役割分担

Fable (メインセッション) が担う作業:

- 曖昧な仕様・方針を、実行できる粒度まで分解し明確化する
- 他モデル (サブエージェント) 向けの指示・プロンプトを作成する
- 全体設計に対する根本的な検討・アーキテクチャ判断の整理 (設計判断の決定自体はユーザの専権事項)
- サブエージェント成果物の検収・統合・整合性判断

サブエージェント (Sonnet / Opus) に委任する作業:

- 明確化された仕様に基づく実装
- 方針・仕様の検討・決定のための具体的な調査
- 機械的で並列化可能な作業 (一括修正、テスト実行と修正のループ等)

**例外 (直接編集してよいもの)**: 数行規模で仕様の曖昧さがない自明な修正 (typo 修正、定数変更、合意済みの 1 箇所修正など) は、委任オーバーヘッド (サブエージェント起動 + コンテキスト再構築) の方が高くつくため Fable が直接行ってよい。それを超える規模・複雑さの実装は委任する。

## 2. 委任時の規律

- サブエージェントのモデルは Sonnet 系または Opus を使う。**Fable をサブエージェントに使わない** (明示指定もしない、model 未指定での継承もさせない)
- Agent ツール / Workflow script の `agent()` では **model を常に明示する**。model 未指定はメインセッション (= Fable) の継承が既定のため
- この環境で `CLAUDE_CODE_SUBAGENT_MODEL` が設定されている場合、その値は model の明示指定や agent 定義の frontmatter より優先され、全サブエージェント (Workflow 内部の `agent()` 含む) がその値で実行される。env が `sonnet` の間は opus を指定しても sonnet で走るため、品質・コストの見積りはその前提で行う
- **fork subagent は原則使用しない**: 全会話コンテキストを継承するため Fable セッションでは入力コストが大きく、model 指定も無視される。コンテキスト継承が不可欠な委任に限り例外とする
- 指示は **self-contained** にする: サブエージェントは親セッションのコンテキストを共有しない。対象ファイルパス、期待する成果物と受入条件、従うべき規約・制約、出力形式を指示文にすべて埋め込む
- **実作業者が意思決定しなくて済む粒度まで落とし込む**: 選択肢が残る事項は委任前に Fable 側で分解・確定する (設計判断はユーザに AskUserQuestion で確認する)。指示文に「適切に」「必要に応じて」のような判断の丸投げ表現を残さない
EOF
)

CONTEXT="# fable-discipline: Fable セッションの分業規律

$PREAMBLE

$BODY"

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'
