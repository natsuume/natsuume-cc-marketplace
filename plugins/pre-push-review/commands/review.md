---
description: pre-push gate を通すための 3 レビューを同じアシスタントメッセージで並列に起動する
---

# /pre-push-review:review

このコマンドは `git push` 前に必須となる **3 つのレビュー** を、 **同じアシスタントメッセージで並列に** 起動する確定的フローです。 順序入れ替えや引数指定は受け付けません — このコマンド本文に固定された 3 tool 並列発出のみが正解です。

## 必ず実行すること: 3 ツールの並列発出

次のアシスタントメッセージ (= このコマンドへの最初の応答) で、 **以下 3 つの tool_use を 1 つのメッセージ内に同時に含めて** 並列発出してください:

1. **Skill tool**: `skill: "code-review"` (Anthropic read-only バグ検出)
2. **Agent / Task tool**: `subagent_type: "pre-push-review:security-reviewer"`、 prompt: "branch の差分に対して self-contained に security review を実行し、 markdown report を返してください。"、 description: "branch 差分の security review"
3. **Bash tool**: `command: bash "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/run-codex-review.sh"`、 description: "codex review wrapper (foreground 実行)" — `run_in_background` は **false** のままにすること

## 確定的フローの理由

- **順次起動ではなく並列起動**: wall-clock が最遅レビュー 1 本の時間で完了します (順次より大幅に高速)。 3 レビューは互いに独立しているため並列化に乗ります。
- **Skill での自律判断ではなく確定的実行**: Claude は「どのレビューを走らせるか / どの順番で / 引数は何か」 を判断しません。 上記 3 つを **そのまま** 並列発出するだけです。 これによりレビューの抜けや順序揺れによる無駄ループが構造的に排除されます。
- **`/codex:review` slash command を経由しない理由**: codex 公式の `review.md` は AskUserQuestion で background 起動を推奨する prompt 設計のため、 Claude が bg を選ぶと PostToolUse が marker を書けず silent failure する経路がありました。 wrapper `run-codex-review.sh` は内部で `--wait --scope branch` を hardcode して foreground 起動するため、 主 session が review 結果を観察してから push 判断する経路を構造的に保証します。
- **`/security-review` 標準 skill を直接呼ばない理由**: 標準 skill の prompt は最終応答をマークダウンレポートだけにするよう指示するため、 主 session の Claude が直接呼ぶと turn が終了して push まで進めません。 `pre-push-review:security-reviewer` subagent は同等のレビュー内容を self-contained に実行し、 親 session の turn を止めずに report を返します。

## 並列発出が技術的に成立しない / 一部のレビューが失敗した場合

- `pre-push-review:security-reviewer` subagent が見つからない → プラグインの install を確認してください (`claude plugin install pre-push-review@natsuume-plugins`)
- codex review wrapper が「codex プラグインが見つかりません」 で失敗 → 公式 codex プラグインを install (`claude plugin install codex@openai-codex`) してから再実行
- 並列発出が技術的に困難な場合 (Claude Code の harness 都合等) は、 同じ 3 ツールを順次起動しても push gate の構造的保証は同じ (= 3 マーカーの hash 一致が成立すれば push 可)。 wall-clock が伸びるだけのトレードオフです。

## レビュー指摘の修正フロー (3 ツール完了後)

3 レビューから指摘が返ってきたら以下の規律で対応してください:

1. 指摘ごとに修正方針を言語化する (どの指摘をどう直すか / 代替案 / トレードオフ)。
2. **codex review からの指摘** は `/codex:rescue --wait` で方針を壁打ちし、 「指摘の根本原因に対する解として妥当か / 場当たり的でないか / 全体設計と一貫しているか」 の 3 観点で approve を得てから実装する (rescue 自体はマーカー対象外)。

   ⚠ **`/codex:rescue --wait` のハング対策**: rescue は **しばしばハングします** (応答が一向に返ってこない / プロンプトを出したまま固まる)。 数分待っても進展がない場合は次の手順で復旧:
   - `ps -eo pid,ppid,lstart,etime,command | grep -E 'codex-companion(\.m[jt]s)?.*[[:space:]]task' | grep -v grep` で `codex-companion.mjs task ...` プロセスを列挙
   - 起動時刻 (lstart) / 経過時間 (etime) / 親プロセス (PPID) で **現在ハング中の rescue 呼び出しと一致する PID** を確認 (確信できない場合は kill せず主 session を終了)
   - `kill <pid>` で終了させ、 同じ入力で `/codex:rescue --wait` をやり直す
   - rescue 自体はマーカー対象外なので、 何回やり直しても push gate には影響しません。
3. **`/code-review` / security-reviewer subagent からの指摘** は通常具体的な対処 (バグ修正 / input validation / 秘匿情報削除 / injection 対策) なので `/codex:rescue` 壁打ちは optional。 ただし設計判断が絡む修正では rescue 推奨。
4. 修正後に branch 差分が変わるとマーカーは自動失効するため、 再度 `/pre-push-review:review` を実行して 3 レビューを再走させる。

3 マーカーすべてが「✓ 最新の差分でレビュー済み」 になったら `git push` を実行してください。
