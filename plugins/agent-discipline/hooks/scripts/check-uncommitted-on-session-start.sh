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
# 起きない — bash のパラメータ展開置換は置換文字列内の $(...)/バックティックを再評価せず、
# 後段の jq が JSON エスケープする。 git porcelain の各行は status 2 文字 + space 始まりなので
# 閉じフェンス化は通常起きないが、 inline `$CWD` やレンダラ差異への defense-in-depth)。
DIRTY_SAFE=${DIRTY//\`/\'}
CWD_SAFE=${CWD//\`/\'}

# 注入本文のテンプレートは hooks/prompts/uncommitted-check.md に定義する (プロンプトを
# sh に直接埋め込むと視認性・メンテナンス性が下がるため分離)。 {{CWD}} / {{DIRTY}} の
# プレースホルダを bash のリテラル置換で埋める (sed と異なり置換文字列内の特殊文字に安全)。
# テンプレートが読めない場合は fail-open で無音終了する (マーカーは設置済みだが、 対象は
# 静的ファイルなので同 session 内の再試行に意味は無い)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)
TEMPLATE=$(cat "$PROMPTS_DIR/uncommitted-check.md" 2>/dev/null)
if [ -z "$TEMPLATE" ]; then
  exit 0
fi

CONTEXT=${TEMPLATE//'{{CWD}}'/$CWD_SAFE}
CONTEXT=${CONTEXT//'{{DIRTY}}'/$DIRTY_SAFE}

jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: $ctx
  }
}'
