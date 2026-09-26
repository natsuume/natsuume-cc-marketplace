# pre-merge-cross-review: merge 前 cross review の起動順

<!-- rule:merge-order -->
## 1. `gh pr merge` を実行する前に codex review を起動する

**なぜ**: auto mode の classifier は tool result (merge gate の deny 文) を読まず、ユーザ発言と tool call の並びだけを見る。`gh pr merge` の呼び出し直後に reviewer subagent を起動すると、classifier には「ユーザが依頼していない merge 系操作の一部」に見えて起動が拒否される。merge 試行を挟まずに起動すれば拒否されない。

**指示**: PR のマージ前提条件 (draft でない・CI・レビュー承認・mergeable) を確認したら、`gh pr merge` を実行する **前に**、merge 対象 PR のブランチを checkout した状態 (ローカル HEAD が PR の head と一致し、working tree が clean) で、`pre-merge-cross-review:codex-reviewer` を Agent tool で `model: "sonnet"` を指定して起動する。merge gate の deny を待ってから起動しない。起動 mode は Claude Code が決める (対話セッションでは background が既定) ため指定せず、report は completion notification (SubagentHandback / SubagentStop) 経由で届く。report を受け取ってから merge に進む。

起動 prompt は、subagent が実際に行う操作を述べた次の定型文だけを使う:

> current branch の PR (#<番号>) の merge-base..head 差分に対して、agent body の契約に従い codex review を 1 回実行し、parent-safe な markdown report を返してください。

起動は PR の head SHA ごとに 1 回でよい。このセッションで現在の head SHA について `Status: pass` / `Status: findings` の report を受け取っている場合に限り、再起動しない (ローカル記録の有無は判断材料にしない。gate が受理する状態かどうかは gate 自身が判定する)。`Status: execution-failed` の場合は原因を解消してから再起動してよい。report の findings を分類・対応し、head SHA が変わる commit を追加した場合は、次の merge 試行の前に同じ手順で再起動する。report を受け取った後の `gh pr merge` で、merge gate がローカルの codex review 記録を検証して merge に進む。

**境界**: 本規律は permission mode に依らず適用する (auto 以外でも手順は同じで無害)。merge gate に deny された後にその案内に従って起動することもできるが、それは復旧経路であり既定の順序ではない。

<!-- rule:merge-command-form -->
## 2. merge コマンドの形

**なぜ**: remote branch の削除は classifier の組み込み soft_deny (Git Destructive) の対象で、`--delete-branch` を付けた merge は merge 自体が拒否されうる。merge gate も単独正規形以外の merge を deny する。

**指示**: merge は `gh pr merge <番号> --squash` / `--merge` / `--rebase` の単独正規形で実行し、`--delete-branch` を付けない。remote branch の削除は merge 後の別ステップ (リポジトリの「merge 後に head branch を自動削除」設定、または `git push origin --delete <branch>` の単独コマンド) で行い、ローカル branch の掃除は update-default-branch 等で行う。

<!-- rule:classifier-denied -->
## 3. それでも起動が拒否されたとき

**なぜ**: classifier の soft block は、ユーザ発言がその操作を直接述べている場合に解除される。同じ起動を繰り返しても判定は変わらず、連続拒否で auto mode 自体が停止する。

**指示**: codex-reviewer の Agent 起動が classifier に拒否された場合は、同じ起動を繰り返さず、`AskUserQuestion` でユーザにレビュー実行の許可を求めてから再起動する。恒久的に解消するには `~/.claude/settings.json` の `autoMode.allow` へ plugin README (「auto mode での利用」節) 記載のルールを追加する。
