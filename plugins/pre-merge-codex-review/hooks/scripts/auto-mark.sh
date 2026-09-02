#!/bin/bash
# auto-mark.sh
# `pre-merge-codex-review:codex-reviewer` subagent の実行完了を subagent lifecycle hook
# (SubagentStart / SubagentStop) で検知し、 codex review wrapper が書いた pending
# attestation を final attestation へ昇格する。
#
# ============================================================================
# 設計契約 (本ヘッダが契約の正本)
# ============================================================================
#
# 本 plugin では PR へのレビューコメント投稿を merge gate (block-pre-merge.sh) が行う。
# wrapper は codex review を実行して「投稿用の本文ファイル」と「pending attestation」を
# git-dir 直下に書くだけで投稿せず、 本 hook が subagent の完了を検証して pending を
# final attestation へ昇格する。 gate は PR にレビューコメントが無いとき、 final
# attestation と本文ファイルを検証して `gh pr review <PR> --comment --body-file <本文>`
# で投稿してから merge を通す。
#
# Claude Code の Agent tool は既定で background 起動になり、 PostToolUse は起動受理時
# (tool_response.status="async_launched") に 1 回発火するのみで、 subagent 完了時には
# 発火しない。 そのため completion 検知は SubagentStop で行い、 SubagentStart の launch
# attestation で「レビューがこの HEAD に対して開始された」ことを束縛する。
#
# 束縛キーは **ローカル HEAD の full SHA** (`git rev-parse HEAD`) である。 レビュー対象は
# PR の merge-base..head 全差分だが、 wrapper はローカル HEAD が PR の head SHA と一致し
# working tree が clean であることを確認してからレビューするため、 ローカル HEAD の SHA が
# レビュー対象を一意に表す。
#
# **final attestation と本文ファイルは対 (pair) で gate に使われる**。 そのため本 hook の
# terminal な掃除経路 (launch attestation 無し / 既存 tombstone / report 無効 / 検証
# 不一致による skip / PostToolUseFailure) では、 pending は常に破棄するが、 本文ファイルは
# final attestation が存在しない場合にのみ削除する (昇格済みの pair を、 後続の別 stop や
# Agent 失敗イベントが壊さないため)。
#
# イベント別契約:
#
# - SubagentStart (hooks.json matcher: ^pre-merge-codex-review:codex-reviewer$):
#   1. agent_type が `pre-merge-codex-review:codex-reviewer` の完全一致でなければ exit 0
#      (script 側でも再検証する。 matcher の regex 解釈には依存しない)
#   2. agent_id が ^[A-Za-z0-9._-]{1,128}$ に一致しなければ exit 0 (path 混入防止。
#      filesystem 操作は一切行わない)
#   3. launch tombstone (.claude-pre-merge-done-<agent_id>) が存在すれば exit 0
#      (同一 agent_id はフル review 1 回のみ。 resume での SubagentStart 再発火による
#      attestation 再鋳造を遮断する)
#   4. 既存 launch attestation が存在する場合も上書きせず exit 0 (中間 stop を挟む重複
#      Start で開始 HEAD が更新される経路を塞ぐ)
#   5. ローカル HEAD の取得に失敗した場合は attestation を書かず exit 0
#   6. 1 日 (1440 分) より古い launch attestation (.claude-pre-merge-launch-*) を
#      opportunistic に削除する (stale 掃除、 best-effort)。 launch tombstone
#      (.claude-pre-merge-done-*) は prune せず無期限に保持する — resume の成立期間は
#      transcript の保持期間に従い、 それは cleanupPeriodDays 設定で任意に延長できるため、
#      期限付き prune では設定次第で再鋳造の穴が復活する。 1 件 64 byte 未満の SHA file
#      なので恒久保持しても実害がない
#   7. SubagentStart 時点のローカル HEAD を launch attestation
#      (git-dir/.claude-pre-merge-launch-<agent_id>) へ、 同一ディレクトリ内 temp file
#      (mktemp) + 排他 `ln` (create-if-absent。 既存なら失敗) で atomic に書く
#
# **resume での再レビューは意図的に拒否する**: 一度 SubagentStop まで到達した agent_id で
# SubagentStart が再発火しても (SendMessage resume 等)、 その再発火は PR 全差分に対する
# 自律的なフル review の実行を証明しない (resume は既存の subagent context を継続する
# だけ)。 再レビューが必要な場合は新規 subagent の spawn (= 新しい agent_id での launch
# attestation) で行う。
#
# - SubagentStop (同 matcher):
#   1. agent_type / agent_id を SubagentStart と同じ基準で検証する。 不一致は exit 0
#      (attestation に触れない)
#   2. stop_hook_active が boolean false でなければ、 何も消費せず exit 0 (stop hook
#      継続中の中間 stop。 最終 stop で改めて検証する)
#   3. launch attestation が無ければ exit 0 (resume 後の再 stop・ main session からの
#      偽装 stop はここで遮断される)。 launch tombstone が既に存在する場合も、
#      attestation の残存有無に関わらず昇格せず skip する (過去の stop で attestation の
#      rm に失敗して残存した場合に、 その残存 attestation を resume 再 stop が再利用する
#      経路の遮断。 残存 attestation の掃除だけを再試行する)。 これら 2 つの terminal な
#      拒否経路では、 pending attestation を破棄し、 本文ファイルも (final が無い場合に
#      限り) 破棄する (resume が wrapper を再実行して書き直した pending / 本文を放置
#      すると、 後続の別 stop がそれを昇格できてしまう)。 中間 stop (2) と遷移保留 (4 の
#      ln 失敗) は次の stop で完結する non-terminal 経路のため、 pending と本文を保持する
#   4. attestation は最初の SubagentStop (stop_hook_active=false) で tombstone へ不可逆
#      遷移させて消費する: 以降の検証 (5〜9) の成否に関わらず、 まず launch tombstone を
#      排他 `ln` で作り (中身は attestation の HEAD。 既存なら失敗を無視)、 tombstone の
#      存在を確認できた場合のみ attestation を rm する (読み取り後に削除。 one-shot)。
#      tombstone が存在しない (ストレージ障害等で ln が失敗した) 場合は attestation を
#      消費せず exit 0 し、 遷移を次の stop まで保留する (tombstone 無しで attestation
#      だけが消えると、 同一 agent_id の SubagentStart 再発火を拒否する記録が残らない)
#   5. last_assistant_message (string) 全体を行分割し、 `Status: ` で始まる行がちょうど
#      1 つ、 かつその行が ^Status: (pass|findings)$ に一致するときのみ有効な report と
#      みなす。 execution-failed / 未知値 / 欠落 / 重複 / 非 string は fail-closed に skip
#      する (収集を許可値に限定すると未知値との併存を受理してしまうため、 収集は許可値に
#      限定しない)
#   6. 現在のローカル HEAD を取得し、 launch attestation の HEAD と一致するときのみ次へ
#      進む (レビュー開始後の commit 追加・切り替えを fail-closed に遮断する)
#   7. pending attestation が symlink でない regular file であり、 内容が
#      「1 行目 pr=<全数字>」「2 行目 head=<40 hex>」の形式で、 その head が現在の HEAD と
#      一致するときのみ次へ進む
#   8. 投稿用の本文ファイルが symlink でない regular file として存在し、 その 1 行目が
#      `<!-- codex-review: head=<pending と同じ head> status=(pass|findings) -->` に完全
#      一致するときのみ次へ進む (投稿する本文と attestation の head が乖離した組を final に
#      しない)
#   9. 5〜8 をすべて満たす場合のみ、 pending を同一 filesystem 内 `mv` で final
#      attestation へ昇格する。 いずれかを欠けば pending を削除し、 本文ファイルも
#      (final が無い場合に限り) 削除して skip する (fail-closed)。 昇格に成功した場合、
#      本文ファイルは残す — gate が投稿の `--body-file` として使う
#
# - PostToolUseFailure (hooks.json matcher: Agent|Task):
#   tool_name が Agent または Task で、 `tool_input.subagent_type` が
#   `pre-merge-codex-review:codex-reviewer` の場合に、 pending attestation を best-effort
#   で破棄し、 本文ファイルも (final が無い場合に限り) 破棄する補助掃除経路。 async
#   harness では tool call は起動受理で成功するため主経路にはならない (失敗した subagent は
#   Status 行の無い stop として SubagentStop 側 3-5 で遮断される)
#
# policy: environment errors are fail-open, review completion is fail-closed
#   git / 環境の失敗は 2>/dev/null + exit 0 で silent skip する (正常完了後の処理を阻害
#   しない設計判断)。 対照: PreToolUse 側の block-pre-merge.sh は fail-closed。 同じ失敗が
#   Pre=deny / Post=skip という非対称は意図的である。 ただし completion 証明は
#   fail-closed: 上記 SubagentStop 契約の 1〜9 をすべて満たす場合だけ final attestation を
#   書く。
#
# **subagent 内 Skill invoke の silent-pass 防止**: codex-reviewer subagent は tools から
# `Skill` を外している。 subagent が標準 skill を invoke することを構造的に塞いでおり、
# 「subagent → 標準 skill → sub-task が nested 制約で動かず degraded mode で完了 → でも
# Agent 完了で attestation は書かれる」 経路は発生しない。
#
# 設計意図:
#   - 「subagent の完了」は Task / Agent の tool call 成功だけでは証明にならない。
#     Claude Code の Agent は background 起動時にも `async_launched` で正常 return し、
#     subagent が内部失敗を parent-safe report の `Status: execution-failed` として返した
#     場合も外側の tool call 自体は成功する。 そこで SubagentStop (subagent 自身の応答完了に
#     紐づく lifecycle event) の last_assistant_message にある Status と launch
#     attestation の HEAD 束縛を併せて検証し、 launch 時点・内部失敗・resume 再 stop では
#     final attestation を書かない (= merge gate が投稿せず deny のままになる) ことを
#     担保する。
#   - 投稿主体を gate に置くため、 本 hook が保証するのは「投稿してよい状態か」の判定と
#     その永続化までである。 投稿の成否と投稿後の掃除は gate の責務。
#
# レビュー対象 repo の前提:
#   本 hook は HEAD 取得と attestation path をすべて hook 発火時の cwd で行う (= 「いま
#   居る repo をレビューした」と記録する)。 gate は merge が実行される repo (hook payload
#   の `cwd`) で attestation を探すため、 別 repo でレビューして別 repo で merge した場合は
#   attestation が見つからず deny に倒れる (fail-closed)。
#
# 診断:
#   本 hook の通常パスは「対象 event / agent_type でない → exit 0」「対象だが未完了 /
#   エラー / report 不正 / HEAD 不一致 → exit 0」「対象完了 + 検証通過 → 昇格 → exit 0」の
#   いずれも exit 0 で抜ける silent skip 設計である。 想定外の非ゼロ終了が発生した場合のみ
#   EXIT trap が stderr に診断ログを出してユーザに知らせる (trap は exit code を変更しない
#   ため、 subagent の動作には影響しない)。 pre-push-codex-review の lib/exit-trap.sh と
#   同等の機能を本 script 内にインラインで持つのは、 本 plugin が保持する pre-push からの
#   コピーを codex companion 解決ロジックの 1 ファイルに限る契約のため。

# 予期せぬ非ゼロ終了をユーザの stderr に通知する EXIT trap。
_pre_merge_auto_mark_exit_handler() {
  local exit_code=$?
  if [ "$exit_code" -ne 0 ]; then
    printf '[pre-merge-codex-review/auto-mark] 予期せぬエラーで hook が exit %s で終了しました。\n' \
      "$exit_code" >&2
    printf '[pre-merge-codex-review/auto-mark] codex review の attestation 更新が skip された可能性があり、 次の `gh pr merge` 時に merge gate が「レビュー未実行」 で deny する経路があります。 marketplace https://github.com/natsuume/natsuume-cc-marketplace に hook 実装の bug として報告してください。\n' >&2
  fi
}
trap _pre_merge_auto_mark_exit_handler EXIT

INPUT=$(cat)

# substring pre-filter: SubagentStart / SubagentStop の payload は top-level `agent_type`
# を、 PostToolUseFailure の payload は `tool_input.subagent_type` を含む。 この 2 つの
# substring を superset として弾くことで、 対象外の event / payload では jq を一切起動せず
# 即離脱する (hot path コスト対策。 false negative を生まない superset 関係)。
case "$INPUT" in
  *'"agent_type"'*|*'"subagent_type"'*) ;;
  *) exit 0 ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

_PRE_MERGE_AUTO_MARK_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/markers.sh
source "$_PRE_MERGE_AUTO_MARK_SCRIPT_DIR/lib/markers.sh"

# 対象 subagent の完全一致名 (hooks.json の matcher とは独立に script 側でも検証する)。
REVIEWER_AGENT_TYPE="pre-merge-codex-review:codex-reviewer"

# agent_id の validation 正規表現 (SubagentStart / SubagentStop 共通)。 path 混入防止の
# ため、 マッチしない agent_id は filesystem 操作を一切行わずに exit 0 する。
AGENT_ID_RE='^[A-Za-z0-9._-]{1,128}$'
# pending attestation の 2 行の形式。
PENDING_PR_LINE_RE='^pr=[0-9]+$'
PENDING_HEAD_LINE_RE='^head=[0-9a-f]{40}$'

HOOK_EVENT_NAME=$(printf '%s' "$INPUT" | jq -r '.hook_event_name // empty')

case "$HOOK_EVENT_NAME" in
  SubagentStart)
    # ------------------------------------------------------------------
    # SubagentStart: 開始時のローカル HEAD を launch attestation として one-shot 記録する。
    # ------------------------------------------------------------------
    AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.agent_type // empty')
    if [ "$AGENT_TYPE" != "$REVIEWER_AGENT_TYPE" ]; then
      exit 0
    fi

    AGENT_ID=$(printf '%s' "$INPUT" | jq -r '.agent_id // empty')
    [[ "$AGENT_ID" =~ $AGENT_ID_RE ]] || exit 0

    GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0

    # tombstone (この agent_id は一度 SubagentStop まで到達した) が存在すれば exit 0。
    # 同一 agent_id はフル review 1 回のみ許す (resume で SubagentStart が再発火する
    # 環境での attestation 再鋳造を遮断する)。
    TOMBSTONE_PATH=$(launch_tombstone_path "$GIT_DIR" "$AGENT_ID") || exit 0
    if [ -e "$TOMBSTONE_PATH" ]; then
      exit 0
    fi

    # 既存 attestation が存在する場合も exit 0 (上書き禁止。 中間 stop を挟む重複 Start で
    # 開始 HEAD が更新される経路を塞ぐ)。
    ATTESTATION_PATH=$(launch_attestation_path "$GIT_DIR" "$AGENT_ID") || exit 0
    if [ -e "$ATTESTATION_PATH" ]; then
      exit 0
    fi

    # HEAD を解決できない状態 (commit が 1 つも無い等) では attestation を書かない。
    HEAD_SHA=$(git rev-parse HEAD 2>/dev/null) || exit 0
    [ -n "$HEAD_SHA" ] || exit 0

    STORAGE_DIR=$(marker_storage_dir "$GIT_DIR") || exit 0
    # 1 日 (1440 分) より古い launch attestation を opportunistic に削除する
    # (stale 掃除、 best-effort)。 find の失敗 (権限不足等) は無視する。
    find "$STORAGE_DIR" -maxdepth 1 -name "${LAUNCH_ATTESTATION_PREFIX}*" -type f -mmin +1440 -delete 2>/dev/null || true
    # tombstone は prune せず無期限に保持する (理由は本ヘッダ SubagentStart 契約 6)。

    # 同一ディレクトリ内 temp file (mktemp) → 排他 `ln` (create-if-absent。 既存なら
    # 失敗) → tmp を rm、 の atomic 書き込み。 `mv` ではなく `ln` を使うのは、 上の存在
    # チェックと実書き込みの間に別プロセスが attestation を作る TOCTOU を filesystem
    # レベルでも防ぐため (`ln` は target が既存なら常に失敗する)。 書き込み失敗 (tmp
    # 作成失敗・書き込み失敗・ln 失敗) はいずれも silent exit 0。
    ATTESTATION_TMP=$(mktemp "${STORAGE_DIR}/${LAUNCH_ATTESTATION_PREFIX}tmp.XXXXXX" 2>/dev/null) || exit 0
    if ! printf '%s' "$HEAD_SHA" > "$ATTESTATION_TMP" 2>/dev/null; then
      rm -f "$ATTESTATION_TMP" 2>/dev/null
      exit 0
    fi
    if ! ln "$ATTESTATION_TMP" "$ATTESTATION_PATH" 2>/dev/null; then
      rm -f "$ATTESTATION_TMP" 2>/dev/null
      exit 0
    fi
    rm -f "$ATTESTATION_TMP" 2>/dev/null
    exit 0
    ;;

  SubagentStop)
    # ------------------------------------------------------------------
    # SubagentStop: launch attestation を one-shot 消費し、 report / HEAD / pending /
    # 本文の検証を経て pending を final attestation へ昇格する。
    # ------------------------------------------------------------------
    AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.agent_type // empty')
    if [ "$AGENT_TYPE" != "$REVIEWER_AGENT_TYPE" ]; then
      exit 0
    fi

    AGENT_ID=$(printf '%s' "$INPUT" | jq -r '.agent_id // empty')
    [[ "$AGENT_ID" =~ $AGENT_ID_RE ]] || exit 0

    # stop_hook_active が boolean false でなければ、 stop hook による継続中の中間 stop と
    # みなし、 何も消費せず exit 0 (最終 stop で改めて検証する)。
    STOP_HOOK_ACTIVE_IS_FALSE=$(printf '%s' "$INPUT" | jq -r '
      if (.stop_hook_active | type) == "boolean" and (.stop_hook_active == false)
      then "true" else "false" end
    ')
    if [ "$STOP_HOOK_ACTIVE_IS_FALSE" != "true" ]; then
      exit 0
    fi

    GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0
    ATTESTATION_PATH=$(launch_attestation_path "$GIT_DIR" "$AGENT_ID") || exit 0
    PENDING_PATH=$(pre_merge_pending_marker_path "$GIT_DIR") || exit 0
    BODY_PATH=$(pre_merge_comment_body_path "$GIT_DIR") || exit 0
    FINAL_PATH=$(pre_merge_final_marker_path "$GIT_DIR") || exit 0

    # terminal な拒否経路と検証失敗経路で共有する掃除。 pending は常に破棄し、 本文
    # ファイルは final attestation が無い場合にのみ破棄する (final と本文は gate が投稿に
    # 使う対であり、 昇格済みの対を壊さないため)。
    discard_pending_and_orphan_body() {
      rm -f "$PENDING_PATH" 2>/dev/null || true
      if [ ! -e "$FINAL_PATH" ]; then
        rm -f "$BODY_PATH" 2>/dev/null || true
      fi
    }

    skip_promotion() {
      discard_pending_and_orphan_body
      exit 0
    }

    # launch attestation が無ければ exit 0 (resume 後の再 stop・ main session からの偽装
    # stop はここで遮断される)。 symlink は regular file 扱いしない (= 「無い」と同じ経路)。
    if [ -L "$ATTESTATION_PATH" ] || [ ! -f "$ATTESTATION_PATH" ]; then
      discard_pending_and_orphan_body
      exit 0
    fi

    ATTESTED_HEAD=$(cat "$ATTESTATION_PATH" 2>/dev/null)
    # 既存 tombstone は「この agent_id は過去の stop で attestation を消費済み」の永続的な
    # 証拠。 通常は attestation 側の遮断で到達しないが、 過去の stop で attestation の rm に
    # 失敗して残存した場合 (immutable file・一時的 I/O 障害等)、 その残存 attestation を
    # resume 再 stop が再利用して昇格できる経路が開く。 既存 tombstone を検出したら残存
    # attestation の掃除だけを再試行し、 昇格せずに skip する (one-shot 消費の保証を rm の
    # 成否ではなく tombstone の存在という永続的事実に束縛する)。
    TOMBSTONE_PATH=$(launch_tombstone_path "$GIT_DIR" "$AGENT_ID") || exit 0
    if [ -e "$TOMBSTONE_PATH" ]; then
      rm -f "$ATTESTATION_PATH" 2>/dev/null || true
      discard_pending_and_orphan_body
      exit 0
    fi
    # attestation → tombstone の不可逆遷移: 最初の (stop_hook_active=false の)
    # SubagentStop で、 以降の検証の成否に関わらず、 常に tombstone を作り attestation を
    # 消費する。 tombstone は同一 agent_id での SubagentStart 再発火 (resume 等) を恒久的に
    # 拒否するため、 検証結果を問わず「この agent_id は一度ここまで到達した」事実だけを
    # 記録する。 排他 `ln` (create-if-absent) を使うため、 既存 tombstone の内容が上書き
    # されることはない。
    TOMBSTONE_TMP=$(mktemp "$(dirname "$TOMBSTONE_PATH")/${LAUNCH_TOMBSTONE_PREFIX}tmp.XXXXXX" 2>/dev/null) || true
    if [ -n "$TOMBSTONE_TMP" ]; then
      if printf '%s' "$ATTESTED_HEAD" > "$TOMBSTONE_TMP" 2>/dev/null; then
        ln "$TOMBSTONE_TMP" "$TOMBSTONE_PATH" 2>/dev/null || true
      fi
      rm -f "$TOMBSTONE_TMP" 2>/dev/null || true
    fi
    # tombstone の存在を確認できた場合のみ attestation を消費する (読み取り後に削除。
    # one-shot)。 tombstone が存在しない (ストレージ障害等で ln が失敗した) 場合は
    # attestation を rm せず exit 0 し、 遷移を次の stop まで保留する — tombstone 無しで
    # attestation だけが消えると、 同一 agent_id の SubagentStart 再発火を拒否する記録が
    # 残らず、 resume 再鋳造ガードが失われるため。 この経路は non-terminal なので pending と
    # 本文は保持する。
    if [ ! -e "$TOMBSTONE_PATH" ]; then
      exit 0
    fi
    rm -f "$ATTESTATION_PATH" 2>/dev/null || true

    # last_assistant_message (string) 全体を行分割し、 `Status: ` で始まる行を全件収集
    # する。 ちょうど 1 行で、 かつその行が ^Status: (pass|findings)$ に一致するときのみ
    # 有効な report とみなす。 execution-failed / 未知値 / 欠落 / 重複 / 非 string は
    # fail-closed に skip する (許可値の行だけを数えると「Status: pass + Status: unknown」の
    # 併存を pass として受理してしまうため、 収集は許可値に限定しない)。
    REPORT_STATUS=$(printf '%s' "$INPUT" | jq -r '
      if ((.last_assistant_message | type) != "string") then
        "invalid"
      else
        ([
          .last_assistant_message
          | gsub("\r\n"; "\n")
          | split("\n")[]
          | select(test("^Status: "))
        ]) as $status_lines
        | if ($status_lines | length) != 1 then
            "invalid"
          elif ($status_lines[0] | test("^Status: (pass|findings)$")) then
            ($status_lines[0] | capture("^Status: (?<status>pass|findings)$").status)
          else
            "invalid"
          end
      end
    ')
    case "$REPORT_STATUS" in
      pass|findings) ;;
      *) skip_promotion ;;
    esac

    # 現在の HEAD を取得できない場合と、 レビュー開始時の HEAD と食い違う場合は昇格しない
    # (レビュー開始後の commit 追加・切り替えを fail-closed に遮断する)。
    CURRENT_HEAD=$(git rev-parse HEAD 2>/dev/null) || skip_promotion
    if [ -z "$CURRENT_HEAD" ] || [ "$ATTESTED_HEAD" != "$CURRENT_HEAD" ]; then
      skip_promotion
    fi

    # pending attestation は symlink でない regular file で、 2 行の形式を満たし、 head が
    # 現在の HEAD と一致する必要がある。
    if [ -L "$PENDING_PATH" ] || [ ! -f "$PENDING_PATH" ]; then
      skip_promotion
    fi
    PENDING_PR_LINE=$(sed -n '1p' "$PENDING_PATH" 2>/dev/null)
    PENDING_HEAD_LINE=$(sed -n '2p' "$PENDING_PATH" 2>/dev/null)
    PENDING_PR_LINE="${PENDING_PR_LINE%$'\r'}"
    PENDING_HEAD_LINE="${PENDING_HEAD_LINE%$'\r'}"
    [[ "$PENDING_PR_LINE" =~ $PENDING_PR_LINE_RE ]] || skip_promotion
    [[ "$PENDING_HEAD_LINE" =~ $PENDING_HEAD_LINE_RE ]] || skip_promotion
    PENDING_HEAD="${PENDING_HEAD_LINE#head=}"
    if [ "$PENDING_HEAD" != "$CURRENT_HEAD" ]; then
      skip_promotion
    fi

    # 投稿用の本文ファイルは symlink でない regular file で、 1 行目が pending と同じ head の
    # 機械可読 header である必要がある (投稿する本文と attestation の head が乖離した組を
    # final にしない)。
    if [ -L "$BODY_PATH" ] || [ ! -f "$BODY_PATH" ]; then
      skip_promotion
    fi
    BODY_FIRST_LINE=$(sed -n '1p' "$BODY_PATH" 2>/dev/null)
    BODY_FIRST_LINE="${BODY_FIRST_LINE%$'\r'}"
    case "$BODY_FIRST_LINE" in
      "<!-- codex-review: head=${PENDING_HEAD} status=pass -->") ;;
      "<!-- codex-review: head=${PENDING_HEAD} status=findings -->") ;;
      *) skip_promotion ;;
    esac

    # 同一 filesystem 内 rename で final attestation へ昇格する。 本文ファイルは gate が
    # 投稿の --body-file として使うため残す。
    mv "$PENDING_PATH" "$FINAL_PATH" 2>/dev/null || skip_promotion
    exit 0
    ;;

  PostToolUseFailure)
    # ------------------------------------------------------------------
    # Agent tool 呼び出し自体の失敗時、 pending attestation と (final が無い場合のみ)
    # 本文ファイルを best-effort で破棄する補助掃除経路。 async harness では tool call は
    # 起動受理で成功するため主経路にはならない (失敗した subagent は Status 行の無い stop と
    # して SubagentStop 側で遮断される)。
    # ------------------------------------------------------------------
    TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty')
    case "$TOOL_NAME" in
      Agent|Task) ;;
      *) exit 0 ;;
    esac
    SUBAGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.tool_input.subagent_type // empty')
    if [ "$SUBAGENT_TYPE" = "$REVIEWER_AGENT_TYPE" ]; then
      GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0
      PENDING_PATH=$(pre_merge_pending_marker_path "$GIT_DIR") || exit 0
      BODY_PATH=$(pre_merge_comment_body_path "$GIT_DIR") || exit 0
      FINAL_PATH=$(pre_merge_final_marker_path "$GIT_DIR") || exit 0
      rm -f "$PENDING_PATH" 2>/dev/null || true
      if [ ! -e "$FINAL_PATH" ]; then
        rm -f "$BODY_PATH" 2>/dev/null || true
      fi
    fi
    exit 0
    ;;

  *)
    # 未知 / 対象外 event はすべて exit 0 (attestation を書かない)。
    exit 0
    ;;
esac
