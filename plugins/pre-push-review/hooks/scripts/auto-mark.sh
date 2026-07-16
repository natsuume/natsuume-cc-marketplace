#!/bin/bash
# auto-mark.sh
# pre-push-review の 3 reviewer subagent の実行完了を subagent lifecycle hook
# (SubagentStart / SubagentStop) で検知し、対応するレビューマーカーを更新する。
#
# ============================================================================
# 設計契約 (issue #285 Phase A — 本ヘッダが契約の正本。実装本体は Phase B で
# 本契約へ更新される。受入テスト: tests/test_pre_push_auto_mark.py)
# ============================================================================
#
# 背景 (#285): Claude Code v2.1.198 以降、Agent tool は既定で background 起動に
# なり、PostToolUse は起動受理時 (tool_response.status="async_launched") に
# 1 回発火するのみで、subagent 完了時には発火しない。v4.0.x までの本 hook は
# PostToolUse の completed payload を検証していたため、async 起動 harness では
# marker が永遠に書かれず push gate が恒久 deny になっていた。v4.1.0 で
# completion 検知を SubagentStop へ完全移行し、SubagentStart の launch
# attestation で「レビューがこの差分に対して開始された」ことを束縛する。
#
# イベント別契約:
#
# - SubagentStart (hooks.json matcher: ^pre-push-review:(code|codex|security)-reviewer$):
#   1. agent_type が 3 reviewer の完全一致でなければ exit 0 (script 側でも再検証。
#      matcher の regex 解釈には依存しない)
#   2. agent_id が ^[A-Za-z0-9._-]{1,128}$ に一致しなければ exit 0 (path 混入防止。
#      filesystem 操作は一切行わない)
#   3. base 検出不能 / branch 取得不能 / master・main / hash 計算失敗 →
#      attestation を書かず exit 0 (block-pre-push.sh の pass-through / deny 条件と
#      整合)
#   4. 1 日より古い launch attestation (.claude-pre-push-launch-*) を
#      opportunistic に削除する (stale 掃除、best-effort)
#   5. 開始時 review hash を launch attestation
#      (git-dir/.claude-pre-push-launch-<agent_id>) へ temp file + mv で atomic に
#      書く
#
# - SubagentStop (同 matcher):
#   1. agent_type / agent_id を SubagentStart と同じ基準で検証。不一致は exit 0
#      (attestation に触れない)
#   2. stop_hook_active が boolean false でなければ、attestation を消費せず exit 0
#      (stop hook 継続中の中間 stop。最終 stop で改めて検証する)
#   3. launch attestation が無ければ exit 0 (SendMessage resume 後の再 stop・
#      移行前起動・main session からの偽装 stop はここで遮断される)
#   4. attestation は最初の SubagentStop で必ず消費する (読み取り後に削除。
#      one-shot。以降の検証が失敗しても再 stop で marker を書ける経路を残さない)
#   5. last_assistant_message (string) 全体を行分割し、
#      ^Status: (pass|findings|execution-failed)$ に一致する行がちょうど 1 つ、
#      かつ値が pass|findings のときのみ有効な report とみなす。execution-failed /
#      欠落 / 重複 / 未知値 / 非 string は fail-closed に skip
#   6. base / branch / master・main / 現在 hash の計算は従来どおり (失敗は skip)
#   7. launch attestation の開始時 hash と現在 hash が一致するときのみ marker を
#      書く (レビュー開始後の差分変更を fail-closed に遮断)
#   8. codex-reviewer はさらに wrapper (run-codex-review.sh) の pending
#      attestation が symlink でない regular file かつ内容が現在 hash と一致する
#      場合のみ、同一 filesystem 内 rename で final marker へ昇格する。不一致・
#      symlink・欠落は pending を消費 (削除) して skip
#
# - PostToolUseFailure (hooks.json matcher: Agent|Task):
#   Agent tool 呼び出し自体の失敗時に codex pending attestation を破棄する
#   best-effort の補助掃除経路。async harness では tool call は起動受理で成功する
#   ため主経路にはならない (失敗した subagent は Status 行の無い stop として
#   SubagentStop 側 4-5 で遮断される)
#
# - 旧 PostToolUse completion payload (status="completed" + final text) では
#   marker を書かない (完全移行。hooks.json の PostToolUse 配線も撤去する)
#
# policy: environment errors are fail-open, review completion is fail-closed
#   git / 環境の失敗は 2>/dev/null + exit 0 で silent skip する (正常完了後の処理を
#   阻害しない設計判断)。対照: PreToolUse 側の block-pre-push.sh は fail-closed。
#   同じ失敗が Pre=deny / Post=skip という非対称は意図的 (#90)。
#   ただし completion 証明は fail-closed: 上記 SubagentStop 契約の 1〜8 を
#   すべて満たす場合だけ marker を書く。
#
# 検知対象 (3 マーカー構成 / v3.0.0、v4.1.0 で lifecycle hook へ移行):
#   - `pre-push-review:code-reviewer` の SubagentStop
#     (launch attestation + parent-safe report pass/findings) → code-reviewed marker
#   - `pre-push-review:security-reviewer` の SubagentStop (同上) → security-reviewed marker
#   - `pre-push-review:codex-reviewer` の SubagentStop (同上 + wrapper pending
#     attestation の現在 hash 一致) → codex-reviewed marker
#
# **v3.0.0 で Skill 検知を全廃**: v2.x までは `/code-review` / `/security-review` 標準 skill を
# Skill tool 経由で直接呼ぶケースも検知して marker を書いていた (後方互換 + ユーザの誤起動
# 救済)。 v3.0.0 で 3 レビューすべてを subagent に統一したため、 Skill 検知は不要になり全廃
# した。 標準 skill を直接呼んだ場合は subagent 経由を案内する block-pre-push.sh の deny
# メッセージで誘導される (主 session の Claude が `/code-review` を直接呼ぶと turn が終了して
# 後続フローが止まるため、 そもそも実用上のパスではない)。
#
# **subagent 内 Skill invoke の silent-pass 防止**: 各 subagent (code-reviewer / security-reviewer
# / codex-reviewer) は tools から `Skill` を外している。 subagent が標準 skill を invoke する
# ことを構造的に塞いでおり、 「subagent → 標準 skill → sub-task が nested 制約で動かず degraded
# mode で完了 → でも Agent 完了で marker は書かれる」 経路は発生しない。
#
# 設計意図:
#   - マーカー: 「Claude が手動で mark-reviewed を呼ぶ」 方式は修正後の状態を
#     レビュー済みと偽装できる経路 (= ループが強制されない) を残す。 各 subagent の
#     実走完了を hook が捕捉しハッシュを書き込むことで、 すべてが「現在のブランチ全差分」
#     に対して直近で走ったことを保証する。codex review は wrapper が review 開始時点の hash
#     を pending attestation に束縛し、本 hook が report 正常完了後に final marker へ昇格する。
#   - 「subagent の完了」は Task / Agent の PostToolUse 発火だけでは証明にならない。
#     Claude Code の Agent は background 起動時にも `async_launched` で正常 return し、
#     subagent が内部失敗を parent-safe report の `Status: execution-failed` として返した場合も
#     外側の tool call 自体は成功する。そこで tool_response.status と final report の Status
#     を併せて検証し、 launch 時点や内部失敗時には marker を書かない
#     (= push gate がそのまま deny) ことを担保する。
#
# レビュー対象 repo の前提 (block-pre-push.sh との非対称について):
#   本 hook は dirty 判定 / base 検出 / branch / ハッシュ計算 / marker パスを **すべて
#   PostToolUse 発火時の cwd** で行う (= 「いま居る repo を review した」と記録する)。
#   一方 block-pre-push.sh は `git push` コマンド引数から実 push target を解決し、 その
#   target cwd でハッシュ・marker パスを決める。 この非対称は意図的かつ安全:
#     - review は通常メイン session の cwd (= push 対象 repo) で実行されるため、
#       両者の cwd は一致し marker は正しく照合される。
#     - もし review を repo A の cwd で行い、 別の repo B を target-override
#       (`git -C B push` / `cd B && git push`) で push した場合、 marker は A に書かれ
#       push gate は B のハッシュを要求するため **hash 不一致で deny** に倒れる
#       (= fail-closed)。 「push する repo をその cwd で review し直す」という正しい挙動を
#       強制するだけで、 未レビュー push を通す bypass にはならない。
#   したがって運用前提は「**review は push 対象 repo の cwd で実行する**」。 target-override
#   push を多用する場合はこの前提に留意する (auto-mark を push 引数依存にしないのは、 本 hook
#   の発火契機が review 完了であって push コマンドではなく、 解決すべき push target が
#   存在しないため)。

# 予期せぬエラー時の診断 trap を install (実装は lib/exit-trap.sh)。
# 本 hook の通常パスは「対象ツールでない → exit 0」「対象ツールだが未完了 / エラー /
# execution-failed / report 不正 → exit 0」「対象ツール完了 + report 正常 → marker 書き込み
# → exit 0」 のいずれも exit 0 で抜ける silent skip 設計。
# 想定外の非ゼロ終了が発生した場合のみ stderr に診断ログを出してユーザに知らせる。
_PRE_PUSH_REVIEW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/exit-trap.sh
source "$_PRE_PUSH_REVIEW_SCRIPT_DIR/lib/exit-trap.sh"
install_exit_trap "auto-mark" "レビュー marker の書き込みが skip された可能性があり、 次の \`git push\` 時に block-pre-push.sh が「marker 未生成」 で deny する経路があります。"

INPUT=$(cat)

# 本 hook は hooks.json で matcher: "*" (wildcard) を指定しており、すべての tool 完了で
# 発火する。tool 名に依存させない理由は、Claude Code 公式ドキュメントで Agent/Task matcher の
# 挙動が tool 名リストに明示されておらず、特定の tool 名 (`Agent` / `Task` など) を信頼できる
# matcher にできないため。代わりに本スクリプト側で bash 内蔵の正規表現マッチを唯一の
# ゲートにする。
#
# `grep` を呼ぶと hot path 上で毎回 fork が走るため、bash の `[[ =~ ]]` を使って
# subprocess を立てずに済ませる (Read/Edit/Write 等の対象外 tool 完了でも本 hook が
# 呼ばれるが、ここで即離脱できればフォーク無しで通り抜けられる)。
#
# **コスト注記 (#90)**: matcher "*" で全 tool 完了に発火するため、巨大な tool_response が
# INPUT に乗ると毎回 INPUT サイズに比例した ERE 評価が走る (fork は無いが in-process コスト)。
# `case "$INPUT" in *'"subagent_type"'*) ;; *) exit 0 ;; esac` の substring pre-filter で
# 大半を弾く設計 (この substring は下記 PRECHECK_RE の全 match の superset なので false
# negative を生まない)。
#
# PRECHECK_RE は subagent_type が **本プラグインの namespace prefix 付き**
# `pre-push-review:code-reviewer` / `pre-push-review:codex-reviewer` /
# `pre-push-review:security-reviewer` の完全一致のみを粗フィルタする。 v3.0.0 で 3 レビューすべてを
# subagent 経由に統一したため Skill 検知は全廃した。 **後段 case 文と必ず同期させること** (片方のみ
# 更新だと silent skip 経路ができる)。
#
# **v3.0.0 で name-only 受理を廃止**: v2.x までの auto-mark は `code-reviewer` / `security-reviewer`
# の name-only 形式も受理していた (PRECHECK_RE prefix が `[^"]*` で任意 namespace を吸収していた)。
# しかし他 plugin (pr-review-toolkit / feature-dev) が同名 `code-reviewer` subagent を提供する場合、
# ユーザが name-only で別 plugin の subagent を呼ぶと PostToolUse の subagent_type が name-only 文字列
# で届き、 本 hook が pre-push-review:code-reviewer の marker を誤って書く push gate bypass 経路に
# なる。 v3.0.0 では namespace prefix `pre-push-review:` を必須として構造的に塞ぐ。 `/pre-push-review:review`
# slash command と block-pre-push.sh の deny メッセージは v2.x から namespace 付きで案内している
# ため、 正常運用パスへの影響は無い。
#
# hook payload の JSON 整形 (`"subagent_type":"pre-push-review:code-reviewer"` / `"subagent_type": "pre-push-review:code-reviewer"`
# 等) に左右されないよう whitespace を寛容に許容する。 false negative (= 本来通すべき payload を
# 弾く) はマーカー未生成 → 永久 push ブロックの致命経路になるため、 フィルタは寛容に倒す
# (false positive は jq 後段の名前一致判定で正しく弾かれるので無害)。
#
# **substring pre-filter** (v2.0.1 で導入 / v3.0.0 で `"skill"` substring を削除): PRECHECK_RE
# は ERE 評価で INPUT 全文を走査する hot path コストがある (matcher: "*" で全 tool 完了に発火
# するため)。 PRECHECK_RE の全 match の superset となる `"subagent_type"` substring が無いなら
# ERE を走らせず即抜ける。 substring case 評価は ERE よりかなり軽量なので、 PostToolUse 多発時
# (slash command で 3 並列発火するため 3 倍) のオーバヘッド削減になる。 superset 関係なので
# false negative は構造的に発生しない。
case "$INPUT" in
  *'"subagent_type"'*) ;;
  *) exit 0 ;;
esac
PRECHECK_RE='"subagent_type"[[:space:]]*:[[:space:]]*"pre-push-review:(code|codex|security)-reviewer"'
if ! [[ "$INPUT" =~ $PRECHECK_RE ]]; then
  exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# lib 群を case 分岐前に source。 marker 書き込みで diff-hash.sh / markers.sh のみ必要。
SCRIPT_DIR="$_PRE_PUSH_REVIEW_SCRIPT_DIR"
# shellcheck source=lib/diff-hash.sh
source "$SCRIPT_DIR/lib/diff-hash.sh"
# shellcheck source=lib/markers.sh
source "$SCRIPT_DIR/lib/markers.sh"

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty')

case "$TOOL_NAME" in
  Agent|Task)
    # Claude Code の Agent tool は内部的に "Agent" / "Task" 2 つの名前で公開されている。
    # PostToolUse の tool_name はそのどちらかが入りうるので両方 match させる。
    SUBAGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.tool_input.subagent_type // empty')
    # subagent 名は namespace 付き (`pre-push-review:code-reviewer` /
    # `pre-push-review:codex-reviewer` / `pre-push-review:security-reviewer`) のみを
    # 受け付ける。 v2.x までの name-only 受理は v3.0.0 で廃止 (他 plugin の同名 subagent
    # との衝突で push gate bypass する経路を塞ぐため。 詳細は PRECHECK_RE のコメント参照)。
    #
    # **PRECHECK_RE (上の subagent_type alternation) と同期させること**: 片方だけ更新すると、
    # PRECHECK_RE で通過するが case で `*) exit 0 ;;` に落ちる silent skip を作りうる。
    #
    case "$SUBAGENT_TYPE" in
      pre-push-review:code-reviewer)
        MARKER_FN=code_reviewed_marker_path
        IS_CODEX_REVIEW=false ;;
      pre-push-review:codex-reviewer)
        MARKER_FN=codex_marker_path
        IS_CODEX_REVIEW=true ;;
      pre-push-review:security-reviewer)
        MARKER_FN=security_marker_path
        IS_CODEX_REVIEW=false ;;
      *)
        exit 0 ;;
    esac
    ;;
  *)
    exit 0
    ;;
esac

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0

CODEX_PENDING_PATH=""
if [ "$IS_CODEX_REVIEW" = "true" ]; then
  CODEX_PENDING_PATH=$(codex_pending_marker_path "$GIT_DIR") || exit 0
fi

skip_marker() {
  if [ -n "$CODEX_PENDING_PATH" ]; then
    rm -f "$CODEX_PENDING_PATH" 2>/dev/null || true
  fi
  exit 0
}

# Agent tool の正常 return だけでは review 完遂を証明できない:
#   - background 起動は `status: async_launched` で正常 return する
#   - subagent 内部の command / diff 取得失敗は、outer tool error ではなく
#     parent-safe report の `Status: execution-failed` として返りうる
# そのため outer error flags に加え、Claude Code 公式の Agent PostToolUse response
# (`status` + `content[].text`) から final report の Status を検証する。
#
# 受理条件は `status: completed` かつ、全 text block を通じて完全一致する Status 行が
# ちょうど 1 個で、その値が pass / findings のいずれかであること。execution-failed、
# Status 欠落・重複・未知値、content shape 不正は fail-closed に marker を skip する。
# tool_input.prompt は検査対象に含めないため、prompt 内の偽 Status 行では spoof できない。
#
# `tool_response.status` が無い PostToolUse payload は旧 Claude Code / 未知の schema を示す
# 可能性が高い。marker は従来どおり書かないが、何度 review しても未実行に見える silent
# failure を避けるため、その場合だけ stderr に互換性診断を出す。PostToolUseFailure は
# tool_response を持たない正規 payload なので診断対象外。
{
  read -r IS_ERROR
  read -r INTERRUPTED
  read -r MISSING_COMPLETION_STATUS
  read -r REPORT_STATUS
} < <(
  printf '%s' "$INPUT" | jq -r '
    def normalized_report_status:
      if ((.status // "") != "completed") or ((.content | type) != "array") then
        "invalid"
      else
        [
          .content[]
          | select(
              (type == "object")
              and (.type == "text")
              and ((.text | type) == "string")
            )
          | .text
          | gsub("\r\n"; "\n")
          | split("\n")[]
          | select(test("^Status: (pass|findings|execution-failed)$"))
          | capture(
              "^Status: (?<status>pass|findings|execution-failed)$"
            ).status
        ] as $statuses
        | if ($statuses | length) == 1 then $statuses[0] else "invalid" end
      end;

    .tool_response as $response
    | if ($response | type) != "object" then
        true,
        false,
        (.hook_event_name == "PostToolUse"),
        "invalid"
      else
        ($response.is_error // $response.isError // false),
        ($response.interrupted // false),
        (
          (.hook_event_name == "PostToolUse")
          and ($response | has("status") | not)
        ),
        ($response | normalized_report_status)
      end
  '
)
if [ "$MISSING_COMPLETION_STATUS" = "true" ]; then
  printf '%s\n' \
    "[pre-push-review/auto-mark] Agent completion payload に必須の tool_response.status がありません。Claude Code 2.1.211 で本 schema を実機検証済みです。marker は更新しないため、Claude Code を 2.1.211 以上へ更新して reviewer を再実行してください。" \
    >&2
fi
if [ "$IS_ERROR" = "true" ] || [ "$INTERRUPTED" = "true" ]; then
  skip_marker
fi
case "$REPORT_STATUS" in
  pass|findings) ;;
  *) skip_marker ;;
esac

# default branch が検出できない場合はマーカー更新を skip (block-pre-push.sh も同条件で
# pass-through するため、整合性が保たれる)。
BASE=$(detect_base_branch) || skip_marker

# detached HEAD などで現在ブランチが取れない場合も skip。
BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null) || skip_marker

# default branch (master/main) では gate しない (= block-pre-push.sh も skip するため、
# こちらでも markers を書く必要がない)。
case "$BRANCH" in
  master|main) skip_marker ;;
esac

# branch diff 計算失敗時は markers を書かない (block-pre-push.sh は失敗を deny に倒すため、
# こちらでも書かないことで整合性を保つ。中途半端なハッシュ値で marker 書き込みを許すと、
# 後続 push で誤判定の元になる)。
if ! HASH=$(compute_review_hash "$BASE"); then
  skip_marker
fi

if [ "$IS_CODEX_REVIEW" = "true" ]; then
  # wrapper が review 開始時点の hash を pending attestation に書く。regular file かつ
  # 現在 hash と一致する場合だけ、同一 filesystem 内 rename で final marker へ昇格する。
  # report failure / hash mismatch / stale・symlink pending は消費して fail-closed に skip。
  if [ ! -f "$CODEX_PENDING_PATH" ] || [ -L "$CODEX_PENDING_PATH" ]; then
    skip_marker
  fi
  PENDING_HASH=$(cat "$CODEX_PENDING_PATH" 2>/dev/null) || skip_marker
  if [ "$PENDING_HASH" != "$HASH" ]; then
    skip_marker
  fi
  MARKER_PATH=$("$MARKER_FN" "$GIT_DIR") || skip_marker
  mv "$CODEX_PENDING_PATH" "$MARKER_PATH" 2>/dev/null || skip_marker
  CODEX_PENDING_PATH=""
  exit 0
fi

printf '%s' "$HASH" > "$("$MARKER_FN" "$GIT_DIR")"
