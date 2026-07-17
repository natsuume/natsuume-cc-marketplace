#!/bin/bash
# auto-mark.sh
# pre-push-review の 3 reviewer subagent の実行完了を subagent lifecycle hook
# (SubagentStart / SubagentStop) で検知し、対応するレビューマーカーを更新する。
#
# ============================================================================
# 設計契約 (issue #285 — 本ヘッダが契約の正本。実装済み。
# 受入テスト: tests/test_pre_push_auto_mark.py)
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
#   3. launch tombstone (.claude-pre-push-done-<agent_id>) が存在すれば exit 0
#      (同一 agent_id はフル review 1 回のみ。 resume での SubagentStart 再発火に
#      よる attestation 再鋳造を遮断する。 issue #285 codex review P1 指摘)
#   4. 既存 launch attestation が存在する場合も exit 0 (上書き禁止。 中間 stop を
#      挟む重複 Start で開始 hash が更新される経路を塞ぐ)
#   5. base 検出不能 / branch 取得不能 / master・main / hash 計算失敗 →
#      attestation を書かず exit 0 (block-pre-push.sh の pass-through / deny 条件と
#      整合)
#   6. 1 日より古い launch attestation (.claude-pre-push-launch-*) と 30 日より
#      古い launch tombstone (.claude-pre-push-done-*) をそれぞれ opportunistic に
#      削除する (stale 掃除、best-effort。 tombstone の保持期間は Claude Code の
#      transcript 既定保持 30 日に合わせる — resume は transcript が生きている間
#      だけ成立しうるため、 それより長く tombstone を残す必要がない)
#   7. 開始時 review hash を launch attestation
#      (git-dir/.claude-pre-push-launch-<agent_id>) へ、 同一ディレクトリ内 temp
#      file + 排他 `ln` (create-if-absent。 既存なら失敗) で atomic に書く
#
# **resume での再レビューは意図的に拒否する** (issue #285 codex review P1 指摘):
# 一度 SubagentStop まで到達した agent_id で SubagentStart が再発火しても
# (SendMessage resume 等)、 その再発火は branch 全差分に対する自律的なフル review の
# 実行を証明しない (resume は既存の subagent context を継続するだけ)。 再レビューが
# 必要な場合は新規 subagent の spawn (= 新しい agent_id での launch attestation) で
# 行う。
#
# - SubagentStop (同 matcher):
#   1. agent_type / agent_id を SubagentStart と同じ基準で検証。不一致は exit 0
#      (attestation に触れない)
#   2. stop_hook_active が boolean false でなければ、attestation を消費せず exit 0
#      (stop hook 継続中の中間 stop。最終 stop で改めて検証する)
#   3. launch attestation が無ければ exit 0 (SendMessage resume 後の再 stop・
#      移行前起動・main session からの偽装 stop はここで遮断される)。なお別の
#      SubagentStop hook が stop を block して subagent が継続する環境でも、本 hook は
#      stop_hook_active == false の最初の有効 report で marker を書き、以降の再 stop は
#      attestation 消費済みのため marker を更新しない (「最終 stop」ではなく
#      「初回有効 report」を採用する意図的なセマンティクス)
#   4. attestation は最初の SubagentStop (stop_hook_active=false) で必ず消費する:
#      以降の検証 (5〜8) の成否に関わらず、 まず launch tombstone
#      (.claude-pre-push-done-<agent_id>) を排他 `ln` で作り (中身は attestation の
#      hash。 作成失敗 = 既存なら無視して続行)、 続けて attestation を rm する
#      (読み取り後に削除。 one-shot。 「最初の false stop で成否に関わらず
#      attestation→tombstone へ不可逆遷移する」ことで、 以降の検証が失敗しても
#      再 stop で marker を書ける経路も、 同一 agent_id での SubagentStart 再発火も
#      残さない)
#   5. last_assistant_message (string) 全体を行分割し、`Status: ` で始まる行が
#      ちょうど 1 つ、かつその行が ^Status: (pass|findings)$ に一致するときのみ
#      有効な report とみなす。execution-failed / 未知値 / 欠落 / 重複 / 非 string は
#      fail-closed に skip (収集を許可値に限定すると未知値との併存を受理してしまう)
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
#   - 「subagent の完了」は Task / Agent の tool call 成功だけでは証明にならない。
#     Claude Code の Agent は background 起動時にも `async_launched` で正常 return し、
#     subagent が内部失敗を parent-safe report の `Status: execution-failed` として返した場合も
#     外側の tool call 自体は成功する。そこで SubagentStop (subagent 自身の応答完了に
#     紐づく lifecycle event) の last_assistant_message にある Status と launch
#     attestation の hash 束縛を併せて検証し、 launch 時点・内部失敗・resume 再 stop では
#     marker を書かない (= push gate がそのまま deny) ことを担保する。
#
# レビュー対象 repo の前提 (block-pre-push.sh との非対称について):
#   本 hook は dirty 判定 / base 検出 / branch / ハッシュ計算 / marker パスを **すべて
#   hook 発火時の cwd** で行う (= 「いま居る repo を review した」と記録する)。
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
# 本 hook の通常パスは「対象 event / agent_type でない → exit 0」「対象だが未完了 /
# エラー / execution-failed / report 不正 / hash 不一致 → exit 0」「対象完了 +
# report 正常 + hash 一致 → marker 書き込み → exit 0」のいずれも exit 0 で抜ける
# silent skip 設計。想定外の非ゼロ終了が発生した場合のみ stderr に診断ログを出して
# ユーザに知らせる。
_PRE_PUSH_REVIEW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/exit-trap.sh
source "$_PRE_PUSH_REVIEW_SCRIPT_DIR/lib/exit-trap.sh"
install_exit_trap "auto-mark" "レビュー marker の書き込みが skip された可能性があり、 次の \`git push\` 時に block-pre-push.sh が「marker 未生成」 で deny する経路があります。"

INPUT=$(cat)

# substring pre-filter: SubagentStart / SubagentStop の payload は top-level
# `agent_type` を、 PostToolUseFailure の payload は `tool_input.subagent_type` を
# 含む (旧 PostToolUse completion payload も同フィールドを持つが、後段の
# hook_event_name 分岐で構造的に除外される)。 この 2 つの substring を superset として
# 弾くことで、 対象外の event / payload では jq を一切起動せず即離脱する
# (hot path コスト対策。 false negative を生まない superset 関係)。
case "$INPUT" in
  *'"agent_type"'*|*'"subagent_type"'*) ;;
  *) exit 0 ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# lib 群を case 分岐前に source。 marker 書き込みで diff-hash.sh / markers.sh のみ必要。
SCRIPT_DIR="$_PRE_PUSH_REVIEW_SCRIPT_DIR"
# shellcheck source=lib/diff-hash.sh
source "$SCRIPT_DIR/lib/diff-hash.sh"
# shellcheck source=lib/markers.sh
source "$SCRIPT_DIR/lib/markers.sh"

# agent_id の validation 正規表現 (SubagentStart / SubagentStop 共通)。 path 混入防止の
# ため、 マッチしない agent_id は filesystem 操作を一切行わずに exit 0 する。
AGENT_ID_RE='^[A-Za-z0-9._-]{1,128}$'

HOOK_EVENT_NAME=$(printf '%s' "$INPUT" | jq -r '.hook_event_name // empty')

case "$HOOK_EVENT_NAME" in
  SubagentStart)
    # ------------------------------------------------------------------
    # SubagentStart: 開始時 review hash を launch attestation として one-shot 記録する。
    # ------------------------------------------------------------------
    AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.agent_type // empty')
    case "$AGENT_TYPE" in
      pre-push-review:code-reviewer|pre-push-review:codex-reviewer|pre-push-review:security-reviewer) ;;
      *) exit 0 ;;
    esac

    AGENT_ID=$(printf '%s' "$INPUT" | jq -r '.agent_id // empty')
    [[ "$AGENT_ID" =~ $AGENT_ID_RE ]] || exit 0

    GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0

    # tombstone (この agent_id は一度 SubagentStop まで到達した) が存在すれば exit 0。
    # 同一 agent_id はフル review 1 回のみ許す (resume で SubagentStart が再発火する
    # 環境での attestation 再鋳造を遮断する。 issue #285 codex review P1 指摘)。
    TOMBSTONE_PATH=$(launch_tombstone_path "$GIT_DIR" "$AGENT_ID") || exit 0
    if [ -e "$TOMBSTONE_PATH" ]; then
      exit 0
    fi

    # 既存 attestation が存在する場合も exit 0 (上書き禁止。 中間 stop を挟む重複
    # Start で開始 hash が更新される経路を塞ぐ)。
    ATTESTATION_PATH=$(launch_attestation_path "$GIT_DIR" "$AGENT_ID") || exit 0
    if [ -e "$ATTESTATION_PATH" ]; then
      exit 0
    fi

    # base 検出不能 / branch 取得不能 / master・main / hash 計算失敗では、いずれも
    # attestation を書かず exit 0 (block-pre-push.sh の pass-through / deny 条件と整合)。
    BASE=$(detect_base_branch) || exit 0
    BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null) || exit 0
    case "$BRANCH" in
      master|main) exit 0 ;;
    esac
    HASH=$(compute_review_hash "$BASE") || exit 0

    STORAGE_DIR=$(marker_storage_dir "$GIT_DIR") || exit 0
    # 1 日 (1440 分) より古い launch attestation を opportunistic に削除する
    # (stale 掃除、best-effort)。 find の失敗 (権限不足等) は無視する。
    find "$STORAGE_DIR" -maxdepth 1 -name "${LAUNCH_ATTESTATION_PREFIX}*" -type f -mmin +1440 -delete 2>/dev/null || true
    # tombstone は 30 日 (43200 分) より古いものを opportunistic に削除する。
    # Claude Code の transcript 既定保持期間 (30 日) に合わせている: resume は
    # transcript が生きている間だけ成立しうるため、 それより長く tombstone を残す
    # 必要がない。
    find "$STORAGE_DIR" -maxdepth 1 -name "${LAUNCH_TOMBSTONE_PREFIX}*" -type f -mmin +43200 -delete 2>/dev/null || true

    # 同一ディレクトリ内 temp file (mktemp) → 排他 `ln` (create-if-absent。 既存なら
    # 失敗) → tmp を rm、の atomic 書き込み。 `mv` ではなく `ln` を使うのは、 上の
    # 存在チェックと実書き込みの間に別プロセスが attestation を作る TOCTOU を
    # filesystem レベルでも防ぐため (`ln` は target が既存なら常に失敗する)。
    # 書き込み失敗 (tmp 作成失敗・書き込み失敗・ln 失敗) はいずれも silent exit 0。
    ATTESTATION_TMP=$(mktemp "${STORAGE_DIR}/${LAUNCH_ATTESTATION_PREFIX}tmp.XXXXXX" 2>/dev/null) || exit 0
    if ! printf '%s' "$HASH" > "$ATTESTATION_TMP" 2>/dev/null; then
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
    # SubagentStop: launch attestation を one-shot 消費し、report / hash 検証を経て
    # marker を書く。
    # ------------------------------------------------------------------
    AGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.agent_type // empty')
    case "$AGENT_TYPE" in
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

    AGENT_ID=$(printf '%s' "$INPUT" | jq -r '.agent_id // empty')
    [[ "$AGENT_ID" =~ $AGENT_ID_RE ]] || exit 0

    # stop_hook_active が boolean false でなければ、 stop hook による継続中の中間 stop
    # とみなし、 attestation を消費せず exit 0 (最終 stop で改めて検証する)。
    STOP_HOOK_ACTIVE_IS_FALSE=$(printf '%s' "$INPUT" | jq -r '
      if (.stop_hook_active | type) == "boolean" and (.stop_hook_active == false)
      then "true" else "false" end
    ')
    if [ "$STOP_HOOK_ACTIVE_IS_FALSE" != "true" ]; then
      exit 0
    fi

    GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0
    ATTESTATION_PATH=$(launch_attestation_path "$GIT_DIR" "$AGENT_ID") || exit 0

    # launch attestation が無ければ exit 0 (SendMessage resume 後の再 stop・移行前起動・
    # main session からの偽装 stop はここで遮断される)。 symlink は regular file
    # 扱いしない (= 「無い」と同じ経路で exit 0)。
    if [ -L "$ATTESTATION_PATH" ] || [ ! -f "$ATTESTATION_PATH" ]; then
      exit 0
    fi

    ATTESTED_HASH=$(cat "$ATTESTATION_PATH" 2>/dev/null)
    # attestation → tombstone の不可逆遷移: 最初の (stop_hook_active=false の)
    # SubagentStop で、 以降の検証 (Status / hash 一致等) の成否に関わらず、 常に
    # tombstone を作り attestation を消費する。 tombstone は同一 agent_id での
    # SubagentStart 再発火 (resume 等) を恒久的に拒否するため、 検証結果を問わず
    # 「この agent_id は一度ここまで到達した」事実だけを記録する。
    # 排他 `ln` (create-if-absent) を使う: 既に tombstone がある場合 (通常発生しない
    # はずだが二重 stop 等の異常系) は失敗を無視して続行する (best-effort)。
    TOMBSTONE_PATH=$(launch_tombstone_path "$GIT_DIR" "$AGENT_ID") || exit 0
    TOMBSTONE_TMP=$(mktemp "$(dirname "$TOMBSTONE_PATH")/${LAUNCH_TOMBSTONE_PREFIX}tmp.XXXXXX" 2>/dev/null) || true
    if [ -n "$TOMBSTONE_TMP" ]; then
      if printf '%s' "$ATTESTED_HASH" > "$TOMBSTONE_TMP" 2>/dev/null; then
        ln "$TOMBSTONE_TMP" "$TOMBSTONE_PATH" 2>/dev/null || true
      fi
      rm -f "$TOMBSTONE_TMP" 2>/dev/null || true
    fi
    # attestation は最初の SubagentStop で必ず消費する (読み取り後に削除。one-shot。
    # 以降の検証が失敗しても再 stop で marker を書ける経路を残さない)。
    rm -f "$ATTESTATION_PATH" 2>/dev/null || true

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

    # last_assistant_message (string) 全体を行分割し、`Status: ` で始まる行を
    # 全件収集する。ちょうど 1 行で、かつその行が ^Status: (pass|findings)$ に一致する
    # ときのみ有効な report とみなす。execution-failed / 未知値 / 欠落 / 重複 /
    # 非 string は fail-closed に skip する (許可値の行だけを数えると
    # 「Status: pass + Status: unknown」の併存を pass として受理してしまうため、
    # 収集は許可値に限定しない)。
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
      *) skip_marker ;;
    esac

    # default branch が検出できない場合は skip (block-pre-push.sh も同条件で
    # pass-through するため整合性が保たれる)。
    BASE=$(detect_base_branch) || skip_marker

    # detached HEAD などで現在ブランチが取れない場合も skip。
    BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null) || skip_marker

    # default branch (master/main) では gate しない (= block-pre-push.sh も skip する
    # ため、こちらでも markers を書く必要がない)。
    case "$BRANCH" in
      master|main) skip_marker ;;
    esac

    # branch diff 計算失敗時は markers を書かない。
    HASH=$(compute_review_hash "$BASE") || skip_marker

    # 開始時 hash (launch attestation) と stop 時点の現在 hash が一致するときのみ
    # marker を書く (レビュー開始後の差分変更を fail-closed に遮断する)。
    if [ "$ATTESTED_HASH" != "$HASH" ]; then
      skip_marker
    fi

    if [ "$IS_CODEX_REVIEW" = "true" ]; then
      # wrapper が review 開始時点の hash を pending attestation に書く。regular file
      # かつ現在 hash と一致する場合だけ、同一 filesystem 内 rename で final marker へ
      # 昇格する。不一致・symlink・欠落は pending を消費して fail-closed に skip。
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

    MARKER_PATH=$("$MARKER_FN" "$GIT_DIR") || skip_marker
    printf '%s' "$HASH" > "$MARKER_PATH"
    exit 0
    ;;

  PostToolUseFailure)
    # ------------------------------------------------------------------
    # Agent tool 呼び出し自体の失敗時、codex pending attestation を best-effort で
    # 破棄する補助掃除経路。async harness では tool call は起動受理で成功するため
    # 主経路にはならない (失敗した subagent は Status 行の無い stop として
    # SubagentStop 側で遮断される)。
    # ------------------------------------------------------------------
    TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty')
    case "$TOOL_NAME" in
      Agent|Task) ;;
      *) exit 0 ;;
    esac
    SUBAGENT_TYPE=$(printf '%s' "$INPUT" | jq -r '.tool_input.subagent_type // empty')
    if [ "$SUBAGENT_TYPE" = "pre-push-review:codex-reviewer" ]; then
      GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || exit 0
      CODEX_PENDING_PATH=$(codex_pending_marker_path "$GIT_DIR") || exit 0
      rm -f "$CODEX_PENDING_PATH" 2>/dev/null || true
    fi
    exit 0
    ;;

  *)
    # 旧 PostToolUse completion payload を含む未知 / 対象外 event はすべて exit 0
    # (marker を書かない。完全移行の回帰方向ガード)。
    exit 0
    ;;
esac
