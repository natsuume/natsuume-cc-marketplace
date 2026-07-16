#!/bin/bash
# auto-mark.sh
# pre-push-review:code-reviewer / pre-push-review:security-reviewer subagent の
# 実行完了を PostToolUse で検知し、対応するレビューマーカーを更新する。
#
# policy: environment errors are fail-open, review completion is fail-closed
#   git / 環境の失敗は 2>/dev/null + exit 0 で silent skip する (正常完了後の処理を
#   阻害しない設計判断)。対照: PreToolUse 側の block-pre-push.sh は fail-closed。
#   同じ失敗が Pre=deny / Post=skip という非対称は意図的 (#90)。
#   ただし Agent の completion 証明は fail-closed: tool_response.status が completed で、
#   final text に単一の正規 Status (`pass` / `findings`) がある場合だけ marker を書く。
#   async_launched / execution-failed / status 欠落・重複・未知値はすべて skip する。
#
# 検知対象 (3 マーカー構成 / v3.0.0):
#   - Agent/Task で `pre-push-review:code-reviewer` が foreground 完了し、
#     parent-safe report が pass/findings → code-reviewed marker
#   - Agent/Task で `pre-push-review:security-reviewer` が foreground 完了し、
#     parent-safe report が pass/findings → security-reviewed marker
#
# codex review は wrapper script (run-codex-review.sh) が自身で marker を書く設計のため
# 本 hook の対象外 (codex-reviewer subagent も wrapper を内部で foreground 起動するだけで、
# subagent 完了タイミングでの marker 書き込みは wrapper に委譲する。 もし subagent 完了で
# 二重に書くと、 wrapper が non-zero exit したのに subagent が報告だけ返して完了した場合に
# 「失敗した review なのに marker が書かれる」 silent-pass の経路を作るため、 検知しない
# 設計に倒している)。
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
#     に対して直近で走ったことを保証する。 codex review だけは wrapper が直接書く
#     例外設計 (= Claude が wrapper の中身を編集しない限り偽装できない範囲)。
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
# PRECHECK_RE は subagent_type が **本プラグインの namespace prefix 付き** `pre-push-review:code-reviewer`
# / `pre-push-review:security-reviewer` の完全一致のみを粗フィルタする。 v3.0.0 で 3 レビューすべてを
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
PRECHECK_RE='"subagent_type"[[:space:]]*:[[:space:]]*"pre-push-review:(code|security)-reviewer"'
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
    # subagent 名は namespace 付き (`pre-push-review:code-reviewer` / `pre-push-review:security-reviewer`)
    # のみを受け付ける。 v2.x までの name-only 受理は v3.0.0 で廃止 (他 plugin の同名 subagent
    # との衝突で push gate bypass する経路を塞ぐため。 詳細は PRECHECK_RE のコメント参照)。
    #
    # **PRECHECK_RE (上の subagent_type alternation) と同期させること**: 片方だけ更新すると、
    # PRECHECK_RE で通過するが case で `*) exit 0 ;;` に落ちる silent skip を作りうる。
    #
    # codex-reviewer subagent はここでは検知しない (= marker を書かない): wrapper script
    # (run-codex-review.sh) が自身の正常完了 (exit 0) でのみ codex-reviewed marker を atomic
    # rename で書く設計。 subagent 完了タイミングで二重に書くと、 wrapper が non-zero exit
    # したのに subagent が報告だけ返して完了した場合に「失敗した review なのに marker が
    # 書かれる」 silent-pass の経路を作るため、 検知しない。
    case "$SUBAGENT_TYPE" in
      pre-push-review:code-reviewer)
        MARKER_FN=code_reviewed_marker_path ;;
      pre-push-review:security-reviewer)
        MARKER_FN=security_marker_path ;;
      *)
        exit 0 ;;
    esac
    ;;
  *)
    exit 0
    ;;
esac

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
{ read -r IS_ERROR; read -r INTERRUPTED; read -r REPORT_STATUS; } < <(
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
        true, false, "invalid"
      else
        ($response.is_error // $response.isError // false),
        ($response.interrupted // false),
        ($response | normalized_report_status)
      end
  '
)
if [ "$IS_ERROR" = "true" ] || [ "$INTERRUPTED" = "true" ]; then
  exit 0
fi
case "$REPORT_STATUS" in
  pass|findings) ;;
  *) exit 0 ;;
esac

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0

# default branch が検出できない場合はマーカー更新を skip (block-pre-push.sh も同条件で
# pass-through するため、整合性が保たれる)。
BASE=$(detect_base_branch) || exit 0

# detached HEAD などで現在ブランチが取れない場合も skip。
BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null) || exit 0

# default branch (master/main) では gate しない (= block-pre-push.sh も skip するため、
# こちらでも markers を書く必要がない)。
case "$BRANCH" in
  master|main) exit 0 ;;
esac

# branch diff 計算失敗時は markers を書かない (block-pre-push.sh は失敗を deny に倒すため、
# こちらでも書かないことで整合性を保つ。中途半端なハッシュ値で marker 書き込みを許すと、
# 後続 push で誤判定の元になる)。
if ! HASH=$(compute_review_hash "$BASE"); then
  exit 0
fi
printf '%s' "$HASH" > "$("$MARKER_FN" "$GIT_DIR")"
