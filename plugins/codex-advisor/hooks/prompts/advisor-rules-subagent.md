<!--
  codex-advisor: subagent 向け相談規律 (issue #219)
  配送は SubagentStart hook (inject-advisor-rules-subagent.sh) が全 subagent に行う。
  本文中の wrapper パスは injector が注入時に絶対パスへ置換する。
-->

# codex-advisor: Codex への相談 (subagent)

OpenAI Codex を助言役 (advisor) として利用できる。Codex は read-only でリポジトリを読んで裏取りし、plan / course-correction の助言を返す (実行はしない)。

- **許可**: 相談は課金を伴う外部サービス呼び出しである。委任指示が codex-advisor の使用を明示的に許可している場合のみ相談する。許可がなければ相談せず、通常どおり作業を続ける。コードレビュー用途には使わない
- **タイミング**: 非自明なタスクで方針にコミットする前、または行き詰まったとき (エラー反復・アプローチが収束しない)。直前の結果が次の一手を一意に決める作業では相談しない
- **実行**: 相談プロンプト (タスク・証拠・質問 1 つを self-contained に書く) を stdin で渡し、foreground で実行する (`run_in_background` 禁止。Bash の timeout は 600000 を指定):

  ```bash
  bash "{{WRAPPER_PATH}}" <<'EOF'
  (相談内容)
  EOF
  ```

- **助言の扱い**: フラットに扱う — 自分の証拠・推論と同じ土俵で採否を判断し、採否と理由を最終報告に含める。証拠と衝突して自分で判断できないときは、両論とそれぞれの根拠を添えてエスカレーションする
- **失敗時**: 相談できなくても作業は続行し、その旨を最終報告に含める
