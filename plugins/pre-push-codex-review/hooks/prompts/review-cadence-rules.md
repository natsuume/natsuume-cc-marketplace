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

**指示**: `pre-push-codex-review:codex-reviewer` / `pre-merge-cross-review:codex-reviewer` が `Status: pass|findings` で完了した Codex review、または `cross-model-advisor:codex-review-runner` が成功した native review / adversarial review を 1 サイクルと数える。前回の根本方針 checkpoint から合計 5 サイクル完了したら、次の review または完了宣言より先に `/cross-model-advisor:consult` の「Codex review 5 サイクルごとの根本方針 checkpoint」節の手順で `cross-model-advisor:codex-advisor-runner` を起動する (`model: "sonnet"` のみを明示する)。Fable 週次枠の判定コマンド `cross-model-advisor-fable-usage` の 1 行目が `available` なら、同じメッセージで `cross-model-advisor:fable-advisor-runner` も `model: "fable"` で並列に起動し、同じ checkpoint request を渡す (それ以外、または deny された場合は Fable をスキップする)。起動 mode は Claude Code が決める (対話セッションでは background が既定) ため指定せず、助言は completion notification (SubagentHandback / SubagentStop) 経由で届く。相談の `<review_cycle_checkpoint>` には次の 4 項目を省略せず含める: Goal と受入基準・制約 / 直近 5 サイクルの review 履歴 / 現在の方針と不確実性 / course-correction の問い。

助言は独立した第二視点としてフラットに扱い、自分の証拠・推論と同じ土俵で採否を判断する。採用する course-correction、または現方針を維持する根拠を作業報告に記録してから review cycle を再開する。

enforcement の主体は本 plugin の lifecycle hook である: PreToolUse は checkpoint 要求中の次の review 起動 (Codex review wrapper・companion review コマンド) を deny し、Stop は checkpoint 要求中の main session の停止を block する。checkpoint の実行 (`cross-model-advisor:codex-advisor-runner` の起動と attestation の発行) は cross-model-advisor が担う。gate が検証する attestation は codex-advisor-runner の report だけが発行し、fable-advisor-runner の助言は判断材料として扱い gate の検証対象にしない。lifecycle hook は session ごとに計数対象の成功 review を同じカウンターへ加算し、5 回目の完了後は次の review 起動と Stop を block する。カウンターの reset は次の 4 経路に限られる: advisor runner が checkpoint request の成功を `Codex-Advisor-Review-Cadence: satisfied` で証明したとき、advisor runner が checkpoint request を完了できないことを `unavailable` で証明したとき、codex-advisor-runner の起動自体が失敗したとき、または codex-advisor-runner の起動が auto mode classifier に同じ checkpoint 要求中に 2 回拒否されたとき (後の 2 つは fail-open。詳細は次段落)。

**境界**: 通常の advisor 相談 (`<review_cycle_checkpoint>` を含まない相談の起動失敗を含む)、review runner の失敗・cancel、ユーザ interrupt (Esc/Ctrl+C) による abort、pre-push / pre-merge Codex review の実行失敗や不正な report、入力不備はカウンターを reset / increment しない。code-reviewer / security-reviewer は Codex review サイクルではないため数えない。Codex 未認証・timeout 等、実行を開始した checkpoint 相談 (相談 request に `<review_cycle_checkpoint>` を含む) の `cross-model-advisor:codex-advisor-runner` が失敗した場合に限り、その起動失敗 (PostToolUseFailure) をもって `unavailable` と同等に扱いカウンターが自動 reset され、block は解除されて続行できる。ただしこの場合、相談できなかったことを作業報告に含める。checkpoint 相談の起動が auto mode classifier に拒否された場合 (PermissionDenied) は、1 回目ではカウンターを reset せず hook が retry を要求する。このときは必ず `AskUserQuestion` でユーザに起動の可否を確認してから同じ相談をもう一度起動する (確認せずに即再起動しない)。同じ checkpoint 要求中の 2 回目の拒否で fail-open として reset され block は解除されるが、この場合も相談できなかったことを作業報告に含める。これらの経路で自動 reset されない失敗形・環境では、state の手動 reset (state ファイル削除) が解除手段になる (手順は plugin README を参照)。
