#!/bin/bash
# inject-auto-context.sh
# permission_mode == "auto" のとき、変更の commit / PR 作成 / マージ完了まで
# 一気通貫で進めるよう Claude に指示する additionalContext を注入する。
# UserPromptSubmit / PostToolBatch から共有で呼び出される。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# hook_event_name と permission_mode を 1 回の jq 呼び出しで取得する
{ read -r HOOK_EVENT; read -r PERMISSION_MODE; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.hook_event_name // ""),
    (.permission_mode // "")
  '
)

# auto 以外のモードでは何もしない (default / plan / acceptEdits / bypassPermissions など)
if [ "$PERMISSION_MODE" != "auto" ]; then
  exit 0
fi

# hook_event_name が取れなければイベント名を正しくエコーできないので無音終了する。
# 誤った既定値で hookSpecificOutput.hookEventName を返すと別 event の文脈に誘導する恐れがある。
if [ -z "$HOOK_EVENT" ]; then
  exit 0
fi

CONTEXT=$(cat <<'EOF'
Auto mode (permission_mode = "auto") が有効です。以下の方針で**自走**してください:

- ユーザの依頼に対する変更が一段落したら、確認のために停止せず以下まで進める:
  1. 作業ブランチで変更を git commit する (master ブランチで作業中なら作業ブランチを切る)
  2. リモートに push し、`gh pr create` で PR を作成する
  3. PR がマージ可能な状態になったらマージまで完了させる
- 各ステップは reasonable assumption で前進し、軽微な判断は都度ユーザに聞き返さない
- ただし以下は引き続き禁止 / 要確認:
  - master / 既定ブランチへの直接 push、master 上での直接コミット
  - force push / 履歴改変 / 共有データ削除 等の破壊的操作の独断実行
  - 秘匿情報を含むファイル (.env, credentials.json 等) のコミット
- すでに対象が commit / PR / マージ済みの場合、そのステップはスキップして次へ進む
- このリポジトリの他プラグイン (pre-commit-review, post-pr-review 等) が要求するレビュー手順は引き続き従う
EOF
)

jq -n --arg evt "$HOOK_EVENT" --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: $evt,
    additionalContext: $ctx
  }
}'
