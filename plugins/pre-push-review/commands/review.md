---
description: pre-push gate を通すための 3 レビューを同じアシスタントメッセージで 3 つの subagent として並列に起動する
---

# /pre-push-review:review

このコマンドは `git push` 前に必須となる **3 つのレビュー** を、 **同じアシスタントメッセージで並列に** 3 つの subagent として起動する確定的フローです。 順序入れ替えや引数指定は受け付けません — このコマンド本文に固定された 3 subagent 並列発出のみが正解です。

## 必ず実行すること: 3 subagent の並列発出

次のアシスタントメッセージ (= このコマンドへの最初の応答) で、 **以下 3 つの Agent / Task tool を 1 つのメッセージ内に同時に含めて** 並列発出してください:

1. **Agent / Task tool**: `subagent_type: "pre-push-review:code-reviewer"`、 prompt: "branch の差分に対して self-contained に correctness バグ検出を実行し、 markdown report を返してください。"、 description: "branch 差分の code review"
2. **Agent / Task tool**: `subagent_type: "pre-push-review:security-reviewer"`、 prompt: "branch の差分に対して self-contained に security review を実行し、 markdown report を返してください。"、 description: "branch 差分の security review"
3. **Agent / Task tool**: `subagent_type: "pre-push-review:codex-reviewer"`、 prompt: "codex review wrapper を foreground で 1 回起動し、 stdout / stderr をまとめた markdown report を返してください。"、 description: "codex review wrapper の foreground 実行"

## 確定的フローの理由

- **順次起動ではなく並列起動**: wall-clock が最遅レビュー 1 本の時間で完了します (順次より大幅に高速)。 3 レビューは互いに独立しているため並列化に乗ります。
- **Skill / Bash ではなく subagent**: 3 レビューを subagent に統一することで、 (1) 各レビューの詳細出力は subagent context に閉じ込められ親 session の context が圧迫されず、 (2) 親 session に返るのは markdown report (要約) だけになり、 (3) 失敗時の検知 (subagent の `tool_response.is_error` / `interrupted`) が 3 軸で対称になります。 v3.0.0 で Skill (code-review) と Bash (codex wrapper の直接起動) を subagent 経由に移行しました。
- **Claude による自律判断ではなく確定的実行**: Claude は「どのレビューを走らせるか / どの順番で / 引数は何か」 を判断しません。 上記 3 つを **そのまま** 並列発出するだけです。 これによりレビューの抜けや順序揺れによる無駄ループが構造的に排除されます。
- **`/code-review` / `/codex:review` / `/security-review` 標準 skill を直接呼ばない理由**: いずれの標準 skill も末尾で「最終応答をマークダウンレポートだけにする」 ことを Claude に指示するか、 内部で sub-task (Task tool) を spawn する設計です。 主 session の Claude が直接呼ぶと turn が終了して push まで進めず、 subagent 内から呼んでも nested subagent 制約で sub-task が動かない degraded mode に倒れます。 `pre-push-review:code-reviewer` / `pre-push-review:codex-reviewer` / `pre-push-review:security-reviewer` の 3 subagent はそれぞれ同等のレビュー内容を self-contained に持つか、 wrapper を foreground 起動するだけの最小実装で、 親 session の turn を止めずに report を返します。

## 並列発出が技術的に成立しない / 一部のレビューが失敗した場合

- subagent (`pre-push-review:code-reviewer` / `pre-push-review:codex-reviewer` / `pre-push-review:security-reviewer`) が見つからない → プラグインの install を確認してください (`claude plugin install pre-push-review@natsuume-plugins`)
- codex review wrapper が「codex プラグインが見つかりません」 で失敗 → 公式 codex プラグインを install (`claude plugin install codex@openai-codex`) してから codex-reviewer subagent を再起動
- 並列発出が技術的に困難な場合 (Claude Code の harness 都合等) は、 同じ 3 subagent を順次起動しても push gate の構造的保証は同じ (= 3 マーカーの hash 一致が成立すれば push 可)。 wall-clock が伸びるだけのトレードオフです。 順次起動する場合は **code-reviewer → security-reviewer → codex-reviewer** の順を推奨します (codex-reviewer は wrapper が codex CLI を foreground で hold するため最長になりやすく、 後段に置くと前段の review 結果を主 session が並行確認できる)。
- 一部の marker のみ失効している場合は、 3 subagent 全部を再走させる必要はありません。 該当 subagent だけを Agent / Task tool で単独再起動するのが正規経路です (block-pre-push.sh の deny メッセージも同じ案内をします)。 3 subagent 並列発出が既定であることは変わりません (= 初回実行や複数 marker が失効した場合は引き続き並列 3 起動を使う)。

## レビュー指摘の修正フロー (3 subagent 完了後)

3 subagent から markdown report が返ってきたら以下の規律で対応してください:

1. 指摘ごとに修正方針を言語化する (どの指摘をどう直すか / 代替案 / トレードオフ)。
2. **codex-reviewer subagent からの指摘** は `/codex:rescue --wait` で方針を壁打ちし、 「指摘の根本原因に対する解として妥当か / 場当たり的でないか / 全体設計と一貫しているか」 の 3 観点で approve を得てから実装する (rescue 自体はマーカー対象外)。

   ⚠ **`/codex:rescue --wait` のハング対策**: rescue は **しばしばハングします** (応答が一向に返ってこない / プロンプトを出したまま固まる)。 数分待っても進展がない場合は次の手順で復旧:
   - `ps -eo pid,ppid,lstart,etime,command | grep -E 'codex-companion(\.m[jt]s)?.*[[:space:]]task' | grep -v grep` で `codex-companion.mjs task ...` プロセスを列挙
   - 起動時刻 (lstart) / 経過時間 (etime) / 親プロセス (PPID) で **現在ハング中の rescue 呼び出しと一致する PID** を確認 (確信できない場合は kill せず主 session を終了)
   - `kill <pid>` で終了させ、 同じ入力で `/codex:rescue --wait` をやり直す
   - rescue 自体はマーカー対象外なので、 何回やり直しても push gate には影響しません。
3. **code-reviewer / security-reviewer subagent からの指摘** は通常具体的な対処 (バグ修正 / input validation / 秘匿情報削除 / injection 対策) なので `/codex:rescue` 壁打ちは optional。 ただし設計判断が絡む修正では rescue 推奨。
4. 修正後に branch 差分が変わるとマーカーは自動失効するため、 再度 `/pre-push-review:review` を実行して 3 subagent を再走させる。

3 マーカーすべてが「✓ 最新の差分でレビュー済み」 になったら `git push` を実行してください。
