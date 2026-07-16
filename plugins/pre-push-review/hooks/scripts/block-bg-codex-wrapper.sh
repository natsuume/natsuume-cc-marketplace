#!/bin/bash
# block-bg-codex-wrapper.sh
# `run-codex-review.sh` wrapper の background 起動、 および `pre-push-review:codex-reviewer`
# subagent 以外からの起動を deny する PreToolUse フック。
#
# policy: 本 hook 全体は fail-open (PreToolUse / defense-in-depth 補助) / agent_type gate は fail-closed
#   本 hook は補助的な regression 防御で、 真の push gate は block-pre-push.sh (= fail-closed)
#   が担う。 そのため jq 不在等の環境失敗時は silent に exit 0 で抜けて allow に倒す
#   (= 環境失敗で「合法な wrapper 起動」 が deny される false positive を避ける)。
#   検知ロジックに該当した場合のみ deny を返す。 真の保証は block-pre-push.sh が
#   marker hash check で行うため、 本 hook が抜けても未レビュー push は通らない。
#   ただし issue #267 で追加した agent_type gate (下記) 自体は fail-closed (agent_type 欠落 =
#   deny) で判定する。 jq 不在等の環境失敗時のみ本 hook 全体としての fail-open (= 上の
#   `command -v jq` チェック) に従う。
#
# ## なぜ必要か
#
# v1.1.0 で codex review は wrapper script (run-codex-review.sh) 経由に切替え、 wrapper 自身が
# 完了時に codex marker を書く設計に統一した。 これは silent failure 経路を排除する意図だが、
# **wrapper を Bash tool の `run_in_background: true` で起動すると新たな regression が発生する**:
#   - wrapper 内部の `node codex-companion.mjs review --wait --scope branch` は foreground で
#     完走するため codex review 自体は正しく実行される
#   - wrapper 完了時に codex marker は書き込まれる (= block-pre-push.sh の hash check は通る)
#   - **しかし主 Claude session は wrapper の stdout / stderr (= codex review の verdict /
#     findings) を観察しない**。 Bash tool は bg 起動の場合 `BashOutput` で後追い取得する
#     必要があるが、 push gate は marker の存在だけ確認するため、 主 session は review 結果を
#     見ずに push に進める経路ができる。 結果として review 指摘が修正されないまま push が
#     通過する **foreground review 要件の regression**
#
# v1.0.0 までは PreToolUse の `block-bg-codex-review.sh` が `run_in_background: true` を deny
# して同類の問題を防いでいたが、 v1.1.0 で Skill 経由 `/codex:review` 廃止に伴い不要として
# 削除した。 しかし wrapper を bg で起動するという新経路に対する gate が欠如していたため、
# 本 hook を再導入する。
#
# v3.0.0 で codex review は `pre-push-review:codex-reviewer` subagent 経由の起動に統一されたが、
# **メインセッションが wrapper を直接 Bash 実行しても同じく marker が書かれてしまい**、
# subagent 経由での起動は agents/codex-reviewer.md の指示文という prompt 規律だけで担保されて
# いた (issue #267)。 メインセッションによる直接実行は subagent が持つ context isolation
# (詳細出力を subagent context に閉じ込め、 親 session には report だけを返す設計) を毀損する。
# さらに marker は wrapper 自身が書き込む (= tool 呼び出し元を区別しない) ため、 **呼び出し元
# (caller) の検証こそが本 gate の唯一の防御層**になる。 v4.0.0 で agent_type gate (下記) を
# 追加し、 `pre-push-review:codex-reviewer` subagent 以外からの wrapper 起動を fail-closed に
# deny するようにした。
#
# ## 検知ロジック
#
# 1. **agent_type gate** (v4.0.0 / issue #267 / fail-closed): command に `run-codex-review.sh`
#    substring を含む場合、 hook payload のトップレベル `agent_type` が
#    `pre-push-review:codex-reviewer` に完全一致しなければ deny する (欠落・別値いずれも deny)。
#    一致した場合のみ後続の bg / pipeline 判定へ進む。 **実機検証済み (Claude Code 2.1.211)**:
#    メインセッションの Bash では `agent_type` がペイロードに含まれず、 plugin subagent の
#    Bash では namespace 付き `pre-push-review:codex-reviewer` が届くことを確認した。
# 2. **bg / pipeline 検知** (従来ロジック): Bash tool の `command` 文字列に `run-codex-review.sh`
#    substring を含み、 かつ `tool_input.run_in_background == true` の場合、 または shell-level
#    の `&` / `|` で wrapper を連結している場合に deny する。 wrapper の起動は通常
#    `bash <abs-path>/run-codex-review.sh` の形 (deny メッセージで案内) なので、 path のどこかに
#    `run-codex-review.sh` が現れる前提。 substring match なので、 ユーザ独自の wrapper alias
#    (例: `bash my-codex.sh`) は対象外 (= cooperative 利用前提)。

# 予期せぬエラー時の診断 trap を install (実装は lib/exit-trap.sh)。
_PRE_PUSH_REVIEW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/exit-trap.sh
source "$_PRE_PUSH_REVIEW_SCRIPT_DIR/lib/exit-trap.sh"
# cmd-parser.sh は line continuation 正規化 (`normalize_line_continuations`) のため source する。
# substring match の前に `\<LF>` を削除して隣接 token を連結することで、 `bash .../run-codex-revie
# w.\<LF>sh` のような line continuation 経由の検知 bypass を塞ぐ (= block-pre-push.sh も同じ
# 防御を行っている)。
# shellcheck source=lib/cmd-parser.sh
source "$_PRE_PUSH_REVIEW_SCRIPT_DIR/lib/cmd-parser.sh"
install_exit_trap "block-bg-codex-wrapper" "run-codex-review wrapper の background 起動 deny が機能していない可能性があり、 wrapper を bg で起動した際に marker が書かれて review 結果未観察のまま push が通る経路に戻っているかもしれません。"

INPUT=$(cat)

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -n "$COMMAND" ] || exit 0

# bash `$(...)` の trailing-LF trim で消えた `\<LF>` を復元 (詳細は cmd-parser.sh の
# 「末尾 `\<LF>` 復元の caller 側 inline パターン」 セクション)。
case "$COMMAND" in *\\) COMMAND="${COMMAND}"$'\n' ;; esac

# 行継続 `\<改行>` を **削除** して隣接 token を連結する (bash 実挙動と一致)。 これを
# やらないと `bash .../run-codex-revie\<LF>w.sh` のような書き方で substring match
# (`run-codex-review.sh`) を bypass される経路が残る。 fast-path で line continuation を
# 含まない 99% の入力は `$(...)` fork を回避する (cmd-parser.sh 関数内に fast-path あり)。
case "$COMMAND" in
  *\\$'\n'*) COMMAND=$(normalize_line_continuations "$COMMAND") ;;
esac

# 粗フィルタ: command 文字列に `run-codex-review.sh` が含まれなければ即抜け (fork なし)。
case "$COMMAND" in
  *run-codex-review.sh*) ;;
  *) exit 0 ;;
esac

# agent_type 検証 gate (fail-closed): wrapper 起動を許可する呼び出し元は
# `pre-push-review:codex-reviewer` subagent のみ。 hook payload のトップレベル `agent_type`
# が完全一致しない場合 (欠落含む) は deny する。 一致した場合のみ後続の bg / pipeline 判定
# (下記 sed strip 以降) へ進む。 jq 不在時は既に上の `command -v jq` チェックで fail-open
# 済みのため、 ここに到達する時点で jq は利用可能。
AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.agent_type // empty')
if [ "$AGENT_TYPE" != "pre-push-review:codex-reviewer" ]; then
  if [ -z "$AGENT_TYPE" ]; then
    REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 `run-codex-review.sh` wrapper は `pre-push-review:codex-reviewer` subagent 経由でのみ起動できます。

理由: 本 hook の payload に `agent_type` が含まれていません (欠落)。 これはメインセッションが wrapper を直接 Bash 実行した場合、 または `agent_type` を hook payload に含めない旧 Claude Code を使用している場合に発生します。

対応:
  - `/pre-push-review:review` で 3 レビューを並列起動してください (推奨)
  - codex review 単独が必要な場合は Agent / Task tool で subagent_type="pre-push-review:codex-reviewer" を起動してください
  - 上記を行っても `agent_type` 欠落が解消しない場合は、 Claude Code を 2.1.211 以上へ更新してください (本 gate は Claude Code 2.1.211 で実機検証済みです)
EOF
)
  else
    REASON=$(cat <<EOF
プッシュ前レビューをブロックしました。 \`run-codex-review.sh\` wrapper は \`pre-push-review:codex-reviewer\` subagent 経由でのみ起動できます。

理由: 検出された \`agent_type\` は \`${AGENT_TYPE}\` で、 \`pre-push-review:codex-reviewer\` と一致しません。 メインセッションが wrapper を直接 Bash 実行した場合や、 \`agent_type\` を hook payload に含めない旧 Claude Code を使用している場合にも同様の deny になります。

対応:
  - \`/pre-push-review:review\` で 3 レビューを並列起動してください (推奨)
  - codex review 単独が必要な場合は Agent / Task tool で subagent_type="pre-push-review:codex-reviewer" を起動してください
  - お使いの Claude Code が \`agent_type\` を正しく送信しない場合は 2.1.211 以上へ更新してください (本 gate は Claude Code 2.1.211 で実機検証済みです)
EOF
)
  fi
  jq -n --arg reason "$REASON" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
fi

# `&` を含む shell redirection (`2>&1` / `&>file` / `<<EOF` 等) を空白に置換する。
# cmd-parser は `&` を一律 separator として扱うため、 redirection 内の `&` を parallel
# separator と誤認して false-positive deny を起こす経路を塞ぐ目的 (block-pre-push.sh と
# 同じ理由・同じ sed パターン)。 特に deny message が案内する
# `bash run-codex-review.sh > codex.log 2>&1` (= 推奨 logging 形式) を素通させるため必須。
COMMAND=$(printf '%s' "$COMMAND" \
  | sed -E 's/[0-9]?(&>>|&>|>>|>\&|<\&|<<<|<<|<>)[[:space:]]*[A-Za-z0-9_./=+@:-]*/ /g')

# 2 種類の bg 起動経路を検知する:
#   (1) Bash tool option `run_in_background: true`
#   (2) shell-level backgrounding (`bash run-codex-review.sh &`) や pipeline (`bash run-codex-review.sh | tee log`)
#       — Bash tool option は false だが shell が wrapper を bg / 並列起動して主 session が
#       review 結果を観察しない経路。 block-pre-push.sh も同じ理由で単独 `&` / `|` を deny
#       している (markers gate 検証後の race 経路も同型)。
RUN_IN_BG=$(printf '%s' "$INPUT" | jq -r '.tool_input.run_in_background // false')

# (2) shell-level backgrounding / pipeline の検知。 cmd-parser の split_command で segment と
# separator を取り、 wrapper を含む segment の **隣接** (直前 / 直後) separator が `&`
# (background) または `|` (pipeline) のときだけ deny する。 SEPARATORS[i-1] が segment[i] の
# 直前、 SEPARATORS[i] が segment[i] の直後を指す (= split_command が segment と separator を
# 交互に出力する仕様)。 wrapper と無関係な segment 間の `&` / `|` (例: `bash run-codex-review.sh
# && echo done | tee log`) は false positive にしない。 `&&` / `||` / `;` は逐次実行なので
# race にならず許容。
SEGMENTS=()
SEPARATORS=()
while IFS= read -r line; do
  if [[ "$line" == SEP:* ]]; then
    SEPARATORS+=("${line#SEP:}")
    continue
  fi
  SEGMENTS+=("$line")
done < <(split_command "$COMMAND")

_SHELL_BG=0
for i in "${!SEGMENTS[@]}"; do
  case "${SEGMENTS[$i]}" in
    *run-codex-review.sh*) ;;
    *) continue ;;
  esac
  # 直前 separator (SEPARATORS[i-1], i==0 なら無し)
  if [ "$i" -gt 0 ]; then
    case "${SEPARATORS[$((i-1))]}" in
      "&"|"|") _SHELL_BG=1; break ;;
    esac
  fi
  # 直後 separator (SEPARATORS[i], 最後の segment なら無し)
  if [ "$i" -lt "${#SEPARATORS[@]}" ]; then
    case "${SEPARATORS[$i]}" in
      "&"|"|") _SHELL_BG=1; break ;;
    esac
  fi
done

# どちらの経路でもなければ allow。
if [ "$RUN_IN_BG" != "true" ] && [ "$_SHELL_BG" -eq 0 ]; then
  exit 0
fi

if [ "$RUN_IN_BG" = "true" ]; then
  REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 `run-codex-review.sh` wrapper を `run_in_background: true` で起動することはできません。

理由: wrapper 自身は foreground で codex review を実行して marker を書きますが、 Bash tool の `run_in_background: true` で起動すると **主 Claude session は wrapper の stdout / stderr (= codex review の verdict / findings) を観察しません**。 主 session は marker の存在だけで push gate を通過してしまうため、 review 指摘が修正されないまま push が成立する **foreground review 要件の regression** になります。

対応: `run_in_background: true` を使わず、 wrapper を plain foreground の単独コマンドとして再実行してください。 wrapper は内部で codex companion を `--wait` で foreground 起動するため、 Bash 呼び出し自体が review 完了まで block しますが、 これが本プラグインの想定する正しい使い方です (= review 結果を観察してから push 判断する)。 この deny メッセージを親 session が report 経由で見た場合は、 wrapper を直接起動せず `pre-push-review:codex-reviewer` subagent を再起動してください。
EOF
)
else
  REASON=$(cat <<'EOF'
プッシュ前レビューをブロックしました。 `run-codex-review.sh` wrapper を shell-level の `&` (background) や `|` (pipeline) で起動することはできません。

理由: `bash run-codex-review.sh &` のような shell-level backgrounding、 `bash run-codex-review.sh | tee log` のような pipeline で wrapper を起動すると、 Bash tool option `run_in_background: false` で呼び出しても shell が wrapper を別 process で並列起動するため、 **主 Claude session は wrapper の stdout / stderr (= codex review の verdict / findings) を観察しない / 途中でしか観察しない** 経路ができます。 主 session は marker の存在だけで push gate を通過してしまうため、 review 指摘が修正されないまま push が成立する **foreground review 要件の regression** になります。

対応: `&` / `|` を外して wrapper を plain foreground の単独コマンドとして再実行するか、 `&&` (success-and) / `;` (sequential) で連結してください。 logging が必要なら file redirection (`bash run-codex-review.sh > codex.log 2>&1`) で代替できます (= wrapper 完了後に file を読めば review 結果を観察可能)。 この deny メッセージを親 session が report 経由で見た場合は、 wrapper を直接起動せず `pre-push-review:codex-reviewer` subagent を再起動してください。
EOF
)
fi

jq -n --arg reason "$REASON" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason: $reason
  }
}'

exit 0
