<!--
  codex-advisor: subagent 向け相談規律 (issue #219)
  配送は SubagentStart hook (inject-advisor-rules-subagent.sh) が全 subagent に行う。
  通常 subagent は Codex wrapper を直接起動せず、相談 request を親へ返す。
-->

# codex-advisor: Codex への相談 (subagent)

OpenAI Codex を助言役 (advisor) として利用できる。Codex は read-only でリポジトリを読んで裏取りし、plan / course-correction の助言を返す (実行はしない)。

`agent_type` が `codex-advisor:rescue-runner` / `codex-advisor:review-runner` / `codex-advisor:advisor-runner` のいずれかである role 固有 runner は、各 agent 本文の job tracking 手順を優先する。以下の「通常 subagent は直接起動しない」という境界は、それ以外の subagent に適用する。

- **許可**: 相談は課金を伴う外部サービス呼び出しである。委任指示が codex-advisor の使用を明示的に許可している場合のみ相談する。許可がなければ相談せず、通常どおり作業を続ける。コードレビュー用途には使わない
- **タイミング**: 非自明なタスクで方針にコミットする前、または行き詰まったとき (エラー反復・アプローチが収束しない)。直前の結果が次の一手を一意に決める作業では相談しない
- **実行**: 通常 subagent は Codex wrapper / companion を Bash で直接起動しない。相談プロンプト (タスク・証拠・質問 1 つ) を self-contained に組み立てて最終報告で親へ返し、`codex-advisor:advisor-runner` の foreground Agent (`model: "sonnet"`、`run_in_background: false`) として実行するよう依頼する。PreToolUse gate は role 固有 runner 以外の直接起動を deny する
- **許可範囲**: 委任指示による codex-advisor の使用許可は、親が当該相談 request を `codex-advisor:advisor-runner` へ渡すことを許可する。通常 subagent 自身による wrapper 実行、project への prompt 書き出し、その他の副作用操作は含まれない
- **助言の扱い**: フラットに扱う — 自分の証拠・推論と同じ土俵で採否を判断し、採否と理由を最終報告に含める。証拠と衝突して自分で判断できないときは、両論とそれぞれの根拠を添えてエスカレーションする
- **失敗時**: 相談できなくても作業は続行し、その旨を最終報告に含める
