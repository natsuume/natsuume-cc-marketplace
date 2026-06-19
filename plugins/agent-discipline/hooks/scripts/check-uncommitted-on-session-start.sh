#!/bin/bash
# check-uncommitted-on-session-start.sh
# Auto mode セッションの最初の UserPromptSubmit でだけ発火し、cwd に
# 未コミットの変更があれば「Claude が出所を分析し、推奨アクションを
# まとめてユーザに簡潔に確認する」よう指示する additionalContext を注入する。
#
# 同 session 内では 2 回目以降は何もしない (session_id ベースのマーカーで制御)。
# auto モード以外、git リポジトリ外、jq 不在環境ではすべて無音終了する。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

{ read -r RAW_SESSION_ID; read -r PERMISSION_MODE; read -r CWD; } < <(
  printf '%s' "$INPUT" | jq -r '
    (.session_id // ""),
    (.permission_mode // ""),
    (.cwd // "")
  '
)

# auto モード以外、または情報不足ならば何もしない
if [ "$PERMISSION_MODE" != "auto" ] || [ -z "$RAW_SESSION_ID" ] || [ -z "$CWD" ]; then
  exit 0
fi

# session_id をマーカーファイル名に使うため英数とハイフンのみに sanitize する。
# (path injection 防止と、ファイル名の素直さを両立)
SESSION_ID=$(printf '%s' "$RAW_SESSION_ID" | tr -dc 'a-zA-Z0-9-')
if [ -z "$SESSION_ID" ]; then
  exit 0
fi

# git work-tree でなければ対象外 (bare リポジトリや worktree 不在は除外)
if ! git -C "$CWD" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  exit 0
fi

# session ごとに 1 回だけ発火させるためのマーカー
MARKER_DIR="${TMPDIR:-/tmp}/agent-discipline-markers"
MARKER_FILE="$MARKER_DIR/${SESSION_ID}.checked"
if [ -f "$MARKER_FILE" ]; then
  exit 0
fi
mkdir -p "$MARKER_DIR" 2>/dev/null || exit 0

# git status を走らせる前にマーカーを置く。失敗したら警告自体を出さない
# (マーカー無しで警告すると、毎プロンプトで再発火するループ事故になる)。
touch "$MARKER_FILE" 2>/dev/null || exit 0

# --untracked-files=normal を明示する。リポジトリ / ユーザ config の
# status.showUntrackedFiles=no で untracked が黙って落ちるのを防ぎつつ、
# =all の再帰列挙 (node_modules 等が gitignore されてない時に additionalContext を
# 吹き飛ばす) も避けるため。normal はディレクトリ単位 (例: `?? node_modules/`)
# で要約され、Claude が必要に応じて中身を別途調査できる。
DIRTY=$(git -C "$CWD" status --porcelain --untracked-files=normal 2>/dev/null)
if [ -z "$DIRTY" ]; then
  exit 0
fi

# markdown の inline code (`...`) / コードフェンス (```) 内に埋め込むため、 値に含まれる
# バックティックを single-quote に中立化してレンダリング崩れを防ぐ (injection は
# 起きない — unquoted heredoc は変数値内の $(...)/バックティックを再評価せず、 後段の jq が
# JSON エスケープする。 git porcelain の各行は status 2 文字 + space 始まりなので閉じフェンス化は
# 通常起きないが、 inline `$CWD` やレンダラ差異への defense-in-depth)。
DIRTY_SAFE=${DIRTY//\`/\'}
CWD_SAFE=${CWD//\`/\'}

CONTEXT=$(cat <<EOF
Auto mode セッション開始後・最初のプロンプト時点での未コミット変更チェック:

**まず以下の未コミット変更が「今回のタスクの対象」か「以前の残骸」かを Claude が一次分析し、独断で commit せず分類結果と推奨アクションをユーザに簡潔に報告して同意を取ること。**

\`$CWD_SAFE\` の未コミット変更:

\`\`\`
$DIRTY_SAFE
\`\`\`

分析手順:
1. \`git diff\` / \`git diff --staged\` / \`git log -5 --oneline\` を見て出所を推定
2. 各ファイルを以下のいずれかに分類:
   - **(a) 今回のタスクに関連** — 作業継続として stage / commit
   - **(b) 以前のタスクの残骸** — 別ブランチ / 別コミット / 削除
   - **(c) 中間状態** — \`git stash\` で退避
   - **(d) 不明** — ユーザに分類を依頼
3. 推奨アクションを 1〜数行でユーザに報告し**ユーザの明示的な応答を待つ** (例: 「X は (a) として commit に含めます。進めてよろしいですか?」)。応答を得るまで git add / commit / stash / branch 切り出し / push は行わない

すべてが明らかに (a) かつ小規模な場合でも、**1 行で良いので必ず報告し、ユーザの応答 (例: "ok" / "進めて") を確認してから進む**。auto mode でも本ステップは「report-and-wait」を必ず守ること (誤分類で誰かの未公開作業を push する事故を防ぐため)。agent-discipline の after 系 commit→PR→merge フローは、この分類確定とユーザ応答後に進めること。
EOF
)

jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: $ctx
  }
}'
