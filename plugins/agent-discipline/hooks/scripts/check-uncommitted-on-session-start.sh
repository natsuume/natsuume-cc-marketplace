#!/bin/bash
# check-uncommitted-on-session-start.sh
# Claude Code Auto セッションで、最初の UserPromptSubmit にだけ発火し、cwd に
# 未コミットの変更があれば「Claude が出所を分析し、推奨アクションを
# まとめてユーザに簡潔に確認する」よう指示する additionalContext を注入する。
# Codex は Auto preset を hook input から識別できないため全 permission mode を no-op とする。
#
# 同 session 内では 2 回目以降は何もしない (session_id ベースのマーカーで制御)。
# 対象外 permission mode、git リポジトリ外、jq 不在環境ではすべて無音終了する。

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)

# cwd の改行を壊さないよう、Linux / macOS bash 3.2 で扱える NUL delimiter を使う。
{
  IFS= read -r -d '' RAW_SESSION_ID
  IFS= read -r -d '' PERMISSION_MODE
  IFS= read -r -d '' CWD
  IFS= read -r -d '' TURN_ID
} < <(
  printf '%s' "$INPUT" | jq -j '
    (.session_id // ""), "\u0000",
    (.permission_mode // ""), "\u0000",
    (.cwd // ""), "\u0000",
    (.turn_id // ""), "\u0000"
  '
)

# runtime ごとの permission_mode を安全側へ正規化する。Claude では従来どおり auto のみ、
# Codex は Auto を証明できないため全 mode を対象外にする。
SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
if [ -z "$SCRIPT_DIR" ] || [ ! -r "$SCRIPT_DIR/lib/permission-mode.sh" ]; then
  exit 0
fi
# shellcheck source=plugins/agent-discipline/hooks/scripts/lib/permission-mode.sh
source "$SCRIPT_DIR/lib/permission-mode.sh" || exit 0
if ! is_agent_discipline_autonomous_mode "$PERMISSION_MODE" "$TURN_ID" || [ -z "$RAW_SESSION_ID" ] || [ -z "$CWD" ]; then
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
# sh に直接埋め込むと視認性・メンテナンス性が下がるため分離)。
# {{CWD}} / {{DIRTY}} の穴埋めは ${var//pat/repl} を使わず、 プレースホルダ位置で
# テンプレートを 3 分割してから連結する。 bash 5.2+ の patsub_replacement (既定 on) は
# 置換文字列中の unquoted & をマッチ文字列へ展開するため、 & を含む path / status 行が
# 「a&b」→「a{{CWD}}b」 のように壊れる (codex review P2)。 分割・連結は全 bash で完全に
# リテラル扱いで、 分割をすべて元テンプレートに対して行うため置換値が再走査されることも
# 無い (値に {{...}} が含まれても安全)。 bash 3.2 (macOS) 互換。
# テンプレートが読めない、 またはプレースホルダが「{{CWD}} → {{DIRTY}} の順に各 1 回」 の
# 形状でない場合は fail-open で無音終了する (マーカーは設置済みだが、 対象は静的ファイル
# なので同 session 内の再試行に意味は無い)。
PROMPTS_DIR=$(cd "$(dirname "$0")/../prompts" 2>/dev/null && pwd)
TEMPLATE=$(cat "$PROMPTS_DIR/uncommitted-check.md" 2>/dev/null)
case $TEMPLATE in
  *'{{CWD}}'*'{{DIRTY}}'*) ;;
  *) exit 0 ;;
esac

T1=${TEMPLATE%%'{{CWD}}'*}
REST=${TEMPLATE#*'{{CWD}}'}
T2=${REST%%'{{DIRTY}}'*}
T3=${REST#*'{{DIRTY}}'}
case "$T1$T2$T3" in
  *'{{CWD}}'* | *'{{DIRTY}}'*) exit 0 ;;
esac

CONTEXT="$T1$CWD_SAFE$T2$DIRTY_SAFE$T3"

jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: $ctx
  }
}'
