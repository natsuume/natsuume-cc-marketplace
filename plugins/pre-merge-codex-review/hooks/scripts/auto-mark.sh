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
#      拒否経路では、 pending attestation と投稿用の本文ファイルも破棄する (resume が
#      wrapper を再実行して書き直した pending / 本文を放置すると、 後続の別 stop がそれを
#      昇格できてしまう)。 中間 stop (2) と遷移保留 (4 の ln 失敗) は次の stop で完結する
#      non-terminal 経路のため、 pending と本文を保持する
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
#   8. 投稿用の本文ファイルが regular file として存在し、 その 1 行目が
#      `<!-- codex-review: head=<pending と同じ head> status=(pass|findings) -->` に一致する
#      ときのみ次へ進む (投稿する本文と attestation の head が乖離した組を final にしない)
#   9. 5〜8 をすべて満たす場合のみ、 pending を同一 filesystem 内 `mv` で final
#      attestation へ昇格する。 いずれかを欠けば pending と本文ファイルを削除して skip する
#      (fail-closed)。 本文ファイルは昇格後も残す — gate が投稿の `--body-file` として使う
#
# - PostToolUseFailure (hooks.json matcher: Agent|Task):
#   tool_name が Agent または Task で、 `tool_input.subagent_type` が
#   `pre-merge-codex-review:codex-reviewer` の場合に、 pending attestation と投稿用の
#   本文ファイルを best-effort で破棄する補助掃除経路。 async harness では tool call は
#   起動受理で成功するため主経路にはならない (失敗した subagent は Status 行の無い stop
#   として SubagentStop 側 3-5 で遮断される)
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

HOOK_EVENT_NAME=$(printf '%s' "$INPUT" | jq -r '.hook_event_name // empty')

# 以下の event 分岐は上記の設計契約に対応する骨格であり、 各分岐の判定・attestation 操作は
# 未実装である (現状はいずれも無操作の exit 0)。
case "$HOOK_EVENT_NAME" in
  SubagentStart)
    # 契約: SubagentStart 節 (開始時 HEAD を launch attestation として one-shot 記録)
    exit 0
    ;;

  SubagentStop)
    # 契約: SubagentStop 節 (launch attestation の one-shot 消費と pending → final 昇格)
    exit 0
    ;;

  PostToolUseFailure)
    # 契約: PostToolUseFailure 節 (pending attestation と本文ファイルの best-effort 破棄)
    exit 0
    ;;

  *)
    # 未知 / 対象外 event はすべて exit 0 (attestation を書かない)。
    exit 0
    ;;
esac
