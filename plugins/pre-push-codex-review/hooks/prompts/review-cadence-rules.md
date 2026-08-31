<!--
  pre-push-codex-review: Codex review cadence 規律
  この文書は「意図 (なぜ) + 指示 + 境界」で記述する (agent-discipline / ui-discipline と同形式)。
  配送は SessionStart hook (inject-review-cadence-rules.sh) が常時行う。
  サイズ制約: 全文で 8,000 文字 (UTF-16 code unit 基準の inline 閾値の安全マージン込み運用値)
  を超えないこと。超えると persisted-output 化され注入が 2KB プレビューに劣化する。
-->

# pre-push-codex-review: Codex review cadence 規律

このセッションでは、Codex review が連続するとき、5 サイクルごとに根本方針の advisor checkpoint を要求する review cadence が適用される。以下はその計数対象・checkpoint の実行手順・enforcement の主体・カウンターの reset 契約である。

<!-- rule:review-cadence -->
## Codex review 5 サイクルごとの根本方針 checkpoint

**なぜ**: 同じ方針のまま局所修正と Codex review を反復すると、個別 finding は減っても問題設定・設計境界・検証戦略の誤りを温存し、多数サイクル規模まで収束しないことがある。5 サイクルごとに review とは独立した advisor へ根本方針を問い直せば、局所最適化を続ける前に course-correction を判断できる。

**指示**: `pre-push-codex-review:codex-reviewer` / `pre-merge-codex-review:codex-reviewer` が `Status: pass|findings` で完了した Codex review、または `codex-advisor:review-runner` が成功した native review / adversarial review を 1 サイクルと数える。前回の根本方針 checkpoint から合計 5 サイクル完了したら、次の review または完了宣言より先に `/codex-advisor:consult` の review cadence mode で `codex-advisor:advisor-runner` を foreground 起動する (`model: "sonnet"`, `run_in_background: false`)。相談の `<review_cycle_checkpoint>` には次の 4 項目を省略せず含める: Goal と受入基準・制約 / 直近 5 サイクルの review 履歴 / 現在の方針と不確実性 / course-correction の問い。

助言は独立した第二視点としてフラットに扱い、自分の証拠・推論と同じ土俵で採否を判断する。採用する course-correction、または現方針を維持する根拠を作業報告に記録してから review cycle を再開する。

enforcement の主体は本 plugin の lifecycle hook である: PreToolUse は checkpoint 要求中の次の review 起動 (Codex review wrapper・companion review コマンド) を deny し、Stop は checkpoint 要求中の main session の停止を block する。checkpoint の実行 (`codex-advisor:advisor-runner` の起動と attestation の発行) は従来どおり codex-advisor が担う。lifecycle hook は session ごとに計数対象の成功 review を同じカウンターへ加算し、5 回目の完了後は次の review 起動と Stop を block する。カウンターの reset は次の 3 経路に限られる: advisor runner が checkpoint request の成功を `Codex-Advisor-Review-Cadence: satisfied` で証明したとき、advisor runner が checkpoint request を完了できないことを `unavailable` で証明したとき、または advisor-runner の起動自体が失敗したとき (fail-open。詳細は次段落)。

**境界**: 通常の advisor 相談、review runner の失敗・cancel、pre-push / pre-merge Codex review の実行失敗や不正な report はカウンターを reset / increment しない。code-reviewer / security-reviewer は Codex review サイクルではないため数えない。codex-advisor 未 install・未認証・timeout 等で checkpoint 相談 (相談 request に `<review_cycle_checkpoint>` を含む) の `codex-advisor:advisor-runner` 起動自体が失敗した場合に限り、その起動失敗 (PostToolUseFailure) をもって `unavailable` と同等に扱いカウンターが reset され、block は解除されて続行できる。ただしこの場合、相談できなかったことを作業報告に含める。同じ `codex-advisor:advisor-runner` でも、`<review_cycle_checkpoint>` を含まない通常の advisor 相談の起動失敗はこの fail-open の対象外であり、カウンターを reset しない。入力不備・cancel・通常の advisor 相談ではカウンターを reset / bypass しない。
