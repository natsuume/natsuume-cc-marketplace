#!/bin/bash
# inject-auto.sh
# permission_mode == "auto" のとき、 during 系 (自律作業中の判断境界) と after 系
# (変更が一段落した後の commit→push→PR→merge 自走パイプライン) の方針を
# additionalContext として注入する。 UserPromptSubmit / PostToolBatch から共有で呼ばれる。
#
# auto 以外のモード (default / plan / acceptEdits / bypassPermissions) では何もしない。
# 常時適用ルール (物理層 / before 系 / closing keyword) は inject-always.sh が SessionStart で配送する。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# hook_event_name / permission_mode / session_id を 1 回の jq 呼び出しで取得する
{ read -r HOOK_EVENT; read -r PERMISSION_MODE; read -r RAW_SESSION_ID; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.hook_event_name // ""),
    (.permission_mode // ""),
    (.session_id // "")
  '
)

# auto 以外のモードでは何もしない
if [ "$PERMISSION_MODE" != "auto" ]; then
  exit 0
fi

# hook_event_name が取れなければイベント名を正しくエコーできないので無音終了する。
# 誤った既定値で hookSpecificOutput.hookEventName を返すと別 event の文脈に誘導する恐れがある。
if [ -z "$HOOK_EVENT" ]; then
  exit 0
fi

# session_id をマーカーファイル名に使うため英数とハイフンのみに sanitize する。
# (path injection 防止と、ファイル名の素直さを両立)
SESSION_ID=$(printf '%s' "$RAW_SESSION_ID" | tr -dc 'a-zA-Z0-9-')
MARKER_DIR="${TMPDIR:-/tmp}/agent-discipline-markers"
BATCH_MARKER="$MARKER_DIR/${SESSION_ID}.batch-injected"

# UserPromptSubmit はターン開始の signal なので per-turn dedup マーカーをクリアし、
# 次の PostToolBatch で 1 回だけ context を再注入できる状態にする。
# PostToolBatch は同一ターン内に複数回発火しうるため、マーカー有無で once-per-turn 化する
# (transcript 肥大化と古い文脈の埋没を防ぐ)。
case "$HOOK_EVENT" in
  UserPromptSubmit)
    if [ -n "$SESSION_ID" ]; then
      rm -f "$BATCH_MARKER" 2>/dev/null
    fi
    ;;
  PostToolBatch)
    if [ -n "$SESSION_ID" ] && [ -f "$BATCH_MARKER" ]; then
      exit 0
    fi
    if [ -n "$SESSION_ID" ]; then
      mkdir -p "$MARKER_DIR" 2>/dev/null && touch "$BATCH_MARKER" 2>/dev/null
    fi
    ;;
esac

CONTEXT=$(cat <<'EOF'
Auto mode (permission_mode = "auto") が有効です。以下の方針で**自走**してください:

## during 系: 自律作業中の判断境界

- 実装は自走する。 **設計 / 仕様レベルの事項 (= issue 起票時の壁打ちで決まっているはずの内容) を再確認する場面で止まらない**
- ただし以下の場合は一度止まる:
  - issue に明記されていない要件を発見した場合 (= 起票時の壁打ちで見落とされた事項)
  - 既存実装と矛盾する判断が必要で、 後戻りコストが大きい場合
- 軽微な判断 (変数名 / import 順 / docstring など) は都度ユーザに聞き返さない

## after 系: 変更が一段落した後の自走パイプライン

- ユーザの依頼に対する変更が一段落したら、確認のために停止せず以下まで進める:
  1. 作業ブランチで変更を git commit する (master ブランチで作業中なら作業ブランチを切る)
  2. リモートに push し、`gh pr create` で PR を作成する
  3. 以下のマージ前提条件を **すべて** 満たしている場合のみ PR をマージする。
     1 つでも未充足なら手を止めて、未充足項目をユーザに報告する:
     - PR が draft ではない (ready for review)
     - リポジトリで required に設定されている CI checks が **全て成功** (`gh pr checks` で確認)
     - レビューが要求されている場合、必要な承認が揃っている (`gh pr view --json reviewDecision`)
     - ブランチ保護ルールに違反しない (`mergeable` が `MERGEABLE` かつ `mergeStateStatus` が `CLEAN`)
- 各ステップは reasonable assumption で前進し、軽微な判断は都度ユーザに聞き返さない
- ただし以下は引き続き禁止 / 要確認:
  - master / 既定ブランチへの直接 push、master 上での直接コミット
  - force push / 履歴改変 / 共有データ削除 等の破壊的操作の独断実行
  - 秘匿情報を含むファイル (.env, credentials.json 等) のコミット
  - マージ前提条件 (上記 3 の bullet 群) を満たさない PR の独断マージ
- すでに対象が commit / PR / マージ済みの場合、そのステップはスキップして次へ進む
- このリポジトリの他プラグイン (pre-push-review 等) が要求するレビュー手順は引き続き従う
EOF
)

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'
