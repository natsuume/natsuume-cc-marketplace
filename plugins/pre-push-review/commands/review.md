---
description: pre-push gate を通すための 3 レビューを同じアシスタントメッセージで 3 つの subagent として並列に起動する
---

# /pre-push-review:review

このコマンドは `git push` 前に必須となる **3 つのレビュー** を、 **同じアシスタントメッセージで並列に** 3 つの subagent として起動する確定的フローです。 順序入れ替えや引数指定は受け付けません — このコマンド本文に固定された 3 subagent 並列発出のみが正解です。

## 必ず実行すること: 3 subagent の並列発出

### Phase 文脈の決定

3 subagent を発出する直前に、呼び出し側 (メインセッションの Claude) が現在の会話、issue、commit 履歴から branch 差分を **Phase A / Phase B / 判定不能** のいずれかに分類し、`{{PHASE_CONTEXT}}` を次の値に置換してください。この判定だけを目的に `AskUserQuestion` で作業を止めてはいけません。確信を持って判定できない場合は判定不能に倒します。

- **Phase A**: `現在の差分は spec-first 2 段階の Phase A (テストと設計骨格の先行 commit) です。テスト一括先行は本ワークフローの仕様であり、それ自体は指摘対象外です。`
- **Phase B**: `現在の差分は spec-first 2 段階の Phase B (実装本体の commit) です。`
- **判定不能の場合**: `{{PHASE_CONTEXT}}` とその直後の空白を空文字列に置換し、従来どおり Phase 文脈なしの prompt にします。spec-first 2 段階を適用していない作業もこの扱いです。

Phase 文脈を渡すのは code-reviewer と security-reviewer だけです。codex-reviewer が起動する通常の Codex review は branch target と custom focus text を同時に受け取れないため、codex-reviewer の prompt には `{{PHASE_CONTEXT}}` を追加せず、wrapper・agent 定義・marker 仕様も変更しません。

次のアシスタントメッセージ (= このコマンドへの最初の応答) で、 **以下 3 つの Agent / Task tool を 1 つのメッセージ内に同時に含めて** 並列発出してください:

1. **Agent / Task tool**: `subagent_type: "pre-push-review:code-reviewer"`、 prompt: "{{PHASE_CONTEXT}} branch の差分に対して self-contained に correctness バグ検出を実行し、 agent body の契約に従う parent-safe markdown report を返してください。実行可能な詳細を親 session に返さないでください。"、 description: "branch 差分の code review"
2. **Agent / Task tool**: `subagent_type: "pre-push-review:security-reviewer"`、 prompt: "{{PHASE_CONTEXT}} branch の差分に対して self-contained に security review を実行し、 agent body の契約に従う parent-safe markdown report を返してください。実行可能な詳細を親 session に返さないでください。"、 description: "branch 差分の security review"
3. **Agent / Task tool**: `subagent_type: "pre-push-review:codex-reviewer"`、 prompt: "codex review wrapper を foreground で 1 回起動し、 agent body の契約に従う parent-safe markdown report を返してください。実行可能な詳細を親 session に返さないでください。"、 description: "codex review wrapper の foreground 実行"

## 確定的フローの理由

- **順次起動ではなく並列起動**: wall-clock が最遅レビュー 1 本の時間で完了します (順次より大幅に高速)。 3 レビューは互いに独立しているため並列化に乗ります。
- **Skill / Bash ではなく foreground subagent**: 3 レビューを subagent 呼び出しに統一することで、 (1) raw output・具体的な再現手順・実行可能な詳細は subagent context に閉じ込められ、 (2) 親 session に返るのは severity / location / impact / fix direction 等を保った parent-safe report だけになり、 (3) v4.1.0 の lifecycle 検知 (SubagentStart が発行する launch attestation + SubagentStop での report 検証) が subagent の完了を捕捉するため、 background 起動でも launch をレビュー完了と誤認せず final report の `Status` を親と hook が確認できます。 v3.0.0 で Skill (code-review) と Bash (codex wrapper の直接起動) を subagent 経由に移行し、 v4.0.1 で返却 report の context isolation と completion 検証を契約化し、 v4.1.0 で completion 検証を subagent lifecycle hook (SubagentStart / SubagentStop) へ完全移行しました。
- **Claude による自律判断ではなく確定的実行**: Claude が判断するのは上記の Phase 分類だけで、「どのレビューを走らせるか / どの順番で / 引数は何か」は判断しません。 Phase 文脈を置換した上記 3 つを **そのまま** 並列発出するだけです。 これによりレビューの抜けや順序揺れによる無駄ループが構造的に排除されます。
- **`/code-review` / `/codex:review` / `/security-review` 標準 skill を直接呼ばない理由**: いずれの標準 skill も末尾で「最終応答をマークダウンレポートだけにする」 ことを Claude に指示するか、 内部で sub-task (Task tool) を spawn する設計です。 主 session の Claude が直接呼ぶと turn が終了して push まで進めず、 subagent 内から呼んでも nested subagent 制約で sub-task が動かない degraded mode に倒れます。 `pre-push-review:code-reviewer` / `pre-push-review:codex-reviewer` / `pre-push-review:security-reviewer` の 3 subagent はそれぞれ同等のレビュー内容を self-contained に持つか、 wrapper を foreground 起動するだけの最小実装で、 親 session の turn を止めずに report を返します。

## 並列発出が技術的に成立しない / 一部のレビューが失敗した場合

- subagent (`pre-push-review:code-reviewer` / `pre-push-review:codex-reviewer` / `pre-push-review:security-reviewer`) が見つからない → プラグインの install を確認してください (`claude plugin install pre-push-review@natsuume-plugins`)
- codex review wrapper が「codex プラグインが見つかりません」 で失敗 → 公式 codex プラグインを install (`claude plugin install codex@openai-codex`) してから codex-reviewer subagent を再起動
- 並列発出が技術的に困難な場合 (Claude Code の harness 都合等) は、 同じ 3 subagent を順次起動しても push gate の構造的保証は同じ (= 3 マーカーの hash 一致が成立すれば push 可)。 wall-clock が伸びるだけのトレードオフです。 順次起動する場合は **code-reviewer → security-reviewer → codex-reviewer** の順を推奨します (codex-reviewer は wrapper が codex CLI を foreground で hold するため最長になりやすく、 後段に置くと前段の review 結果を主 session が並行確認できる)。
- 一部の marker のみ失効している場合は、 3 subagent 全部を再走させる必要はありません。 該当 subagent だけを Agent / Task tool で単独再起動するのが正規経路です (block-pre-push.sh の deny メッセージも同じ案内をします)。 3 subagent 並列発出が既定であることは変わりません (= 初回実行や複数 marker が失効した場合は引き続き並列 3 起動を使う)。

## レビュー指摘の修正フロー (3 subagent 完了後)

3 subagent から markdown report が返ってきたら以下の規律で対応してください:

マーカーは各 reviewer が現在の差分に対してレビューを完了したことと freshness の証明であり、変更の approve や findings が 0 件であることの証明ではありません。`Status: findings` の report でも正規完了条件を満たせばマーカーは書かれるため、親 session が以下の分類と対応を完了してください。3 report すべてで findings が 0 件の場合は分類ステップをスキップし、マーカー確認へ進みます。

1. 各 finding を、どの入力・状態で何がどう壊れるかを示す具体的な failure scenario に変換する。
2. 現在の codebase でその scenario が成立するかを、コード読解と必要な実行検証で裏取りする。parent-safe report だけでは検証材料が不足する場合は、後述の同一 reviewer resume 経路を使う。
3. 裏取り結果を finding ごとに次の 3 値へ分類する:
   - **valid**: failure scenario が成立する。scenario の不成立を積極的に確認できず確信が持てない場合も、fail-safe の既定として valid 側に倒す。
   - **invalid**: 現在の codebase では scenario が成立しないことを積極的に確認できた。成立しない根拠を user-facing summary に記録し、修正しない。
   - **needs-user-decision**: finding の扱いに設計・仕様判断が必要。`AskUserQuestion` でユーザーの決定を得てから、修正するか棄却するかを確定する。
4. valid finding と、needs-user-decision からユーザー判断により修正対象となった finding は、修正方針を言語化する (どの指摘をどう直すか / 代替案 / トレードオフ)。invalid finding はこの修正工程へ進めない。
5. **codex-reviewer subagent の修正対象 finding** は `/codex:rescue --wait` で方針を壁打ちし、 「指摘の根本原因に対する解として妥当か / 場当たり的でないか / 全体設計と一貫しているか」 の 3 観点で approve を得てから実装する (rescue 自体はマーカー対象外)。

   ⚠ **`/codex:rescue --wait` のハング対策**: rescue は **しばしばハングします** (応答が一向に返ってこない / プロンプトを出したまま固まる)。 数分待っても進展がない場合は次の手順で復旧:
   - `ps -eo pid,ppid,lstart,etime,command | grep -E 'codex-companion(\.m[jt]s)?.*[[:space:]]task' | grep -v grep` で `codex-companion.mjs task ...` プロセスを列挙
   - 起動時刻 (lstart) / 経過時間 (etime) / 親プロセス (PPID) で **現在ハング中の rescue 呼び出しと一致する PID** を確認 (確信できない場合は kill せず主 session を終了)
   - `kill <pid>` で終了させ、 同じ入力で `/codex:rescue --wait` をやり直す
   - rescue 自体はマーカー対象外なので、 何回やり直しても push gate には影響しません。
6. **code-reviewer / security-reviewer subagent の修正対象 finding** は通常具体的な対処 (バグ修正 / input validation / 秘匿情報削除 / injection 対策) なので `/codex:rescue` 壁打ちは optional。 ただし設計判断が絡む修正では rescue 推奨。
7. parent-safe report だけでは追加検証が必要な場合は、 raw detail を親へ要求せず、 対象の同一 reviewer subagent を resume して focused question を渡す。 reviewer は自分の context / transcript に残る詳細で検証し、 結果だけを parent-safe report で返す。 resume 後の再 stop では launch attestation が消費済みのため marker は更新されない (marker 更新には該当 reviewer の新規起動が必要)。
8. 修正後に branch 差分が変わるとマーカーは自動失効するため、 再度 `/pre-push-review:review` を実行して 3 subagent を再走させる。

親 session の user-facing summary には agent ID、output file、transcript path、raw tool metadata を含めないでください。これらは review の方針判断に不要な orchestration detail であり、parent-safe report の外へ relay しません。同一 reviewer を resume する場合も、内部の Agent tool state をそのまま使い、ID や path をユーザ向け本文へ表示しません。

3 マーカーすべてが「✓ 最新の差分でレビュー済み」 になったら `git push` を実行してください。
