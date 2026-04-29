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
  3. 以下のマージ前提条件を **すべて** 満たしている場合のみ PR をマージする。
     1 つでも未充足なら手を止めて、未充足項目をユーザに報告する:
     - PR が draft ではない (ready for review)
     - リポジトリで required に設定されている CI checks が **全て成功** (`gh pr checks` で確認)
     - レビューが要求されている場合、必要な承認が揃っている (`gh pr view --json reviewDecision`)
     - post-pr-review が促す `/codex:adversarial-review --wait --scope branch` の verdict が
       `needs-attention` でないか、重大指摘 (priority high 以上) が解消済みであること
     - ブランチ保護ルールに違反しない (`mergeable` が `MERGEABLE` かつ `mergeStateStatus` が `CLEAN`)
- 各ステップは reasonable assumption で前進し、軽微な判断は都度ユーザに聞き返さない
- ただし以下は引き続き禁止 / 要確認:
  - master / 既定ブランチへの直接 push、master 上での直接コミット
  - force push / 履歴改変 / 共有データ削除 等の破壊的操作の独断実行
  - 秘匿情報を含むファイル (.env, credentials.json 等) のコミット
  - マージ前提条件 (上記 3 の bullet 群) を満たさない PR の独断マージ
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
