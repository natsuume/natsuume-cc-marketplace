<!--
  codex-advisor: subagent 向け相談規律 (issue #219)
  配送は SubagentStart hook (inject-advisor-rules-subagent.sh) が全 subagent に行う。
  本文中の wrapper パスは injector が注入時に shell-quote 済み絶対パスへ置換する。
-->

# codex-advisor: Codex への相談 (subagent)

OpenAI Codex を助言役 (advisor) として利用できる。Codex は read-only でリポジトリを読んで裏取りし、plan / course-correction の助言を返す (実行はしない)。

- **許可**: 相談は課金を伴う外部サービス呼び出しである。委任指示が codex-advisor の使用を明示的に許可している場合のみ相談する。許可がなければ相談せず、通常どおり作業を続ける。コードレビュー用途には使わない
- **タイミング**: 非自明なタスクで方針にコミットする前、または行き詰まったとき (エラー反復・アプローチが収束しない)。直前の結果が次の一手を一意に決める作業では相談しない
- **実行**: 相談プロンプト (タスク・証拠・質問 1 つを self-contained に書く) を **Write ツール**でセッションの scratchpad ディレクトリ配下の一意な名前の一時ファイルに書き出す (相談ごとに新規のパスを割り当て、他の相談で再利用しない)。続けて Bash ツールで以下を foreground 実行する (`run_in_background` 禁止。timeout は 600000 を指定)。プロンプト本文を Bash の command 文字列に載せない — heredoc・引数直渡し・`echo` / `printf` / `cat` heredoc によるファイル生成も同様に禁止する (guardrail hook の誤 deny を避けるため):

  ```bash
  bash {{WRAPPER_PATH_SH}} < "/absolute/path/to/prompt.md"
  ```

  Write ツールを持たない場合、または Write が失敗・deny された場合は、Bash によるファイル生成に退避せず相談を行わない — 通常どおり作業を続行し、相談できなかった旨を最終報告に含める
- **許可範囲**: 委任指示による codex-advisor の使用許可には、当該相談のプロンプトファイルをセッションの scratchpad ディレクトリへ Write する操作が含まれる (scratchpad 外・プロジェクト内への書き出しやその他の副作用操作は含まれない)
- **助言の扱い**: フラットに扱う — 自分の証拠・推論と同じ土俵で採否を判断し、採否と理由を最終報告に含める。証拠と衝突して自分で判断できないときは、両論とそれぞれの根拠を添えてエスカレーションする
- **失敗時**: 相談できなくても作業は続行し、その旨を最終報告に含める
