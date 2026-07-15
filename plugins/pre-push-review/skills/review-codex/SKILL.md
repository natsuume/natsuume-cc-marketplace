---
name: review-codex
description: Codex で git push 前の correctness・独立 code review・security review を project-scoped named custom agent 3 本で並列実行し、SubagentStop hook に共有 push gate の hash marker を自動更新させる。pre-push-review に拒否された push の復旧時や、branch 全差分を push 前にレビューするときに使う
---

# pre-push-review for Codex

Claude Code の `/pre-push-review:review` と同じ branch 全差分を、Codex project custom agent 3 本で並列レビューする。agent profile と native `SubagentStop` event を使い、Skill 自身は marker helper を呼ばない。

## 1. Setup と対象差分

この `SKILL.md` の実パスから `skills/review-codex/` の 2 階層上を `<plugin-root>` とする。hook 用環境変数には依存しない。

最初に次を実行し、3 agent template が対象 repository の `.codex/agents` と byte-identical であることを確認する。

```bash
bash "<plugin-root>/scripts/setup-codex-agents.sh" inspect
```

1 つでも `missing` / `different` / `unsafe-*` なら marker を生成しない。`$pre-push-review:setup-pre-push-agents` を使って差分提示とユーザー承認を経た導入を完了し、新しい Codex thread でこの Skill を再実行する。

push 対象 repository の cwd で次を読み取り、default branch と branch 全差分、staged / unstaged diff を確定する。`git status` に出る untracked file は push されず共有 marker hash にも含まれないが、対象範囲の誤認を避けるため状態として報告する。

```bash
git status --short --branch
git symbolic-ref --quiet --short refs/remotes/origin/HEAD
git diff --stat origin/HEAD...
git diff --cached --stat
git diff --stat
```

`origin/HEAD` が無ければ `origin/master`、次に `origin/main` を確認する。対象を解決できない場合は marker を書かずに停止する。

## 2. named agent 3 本を並列実行

1 つの assistant turn から次の **agent_type を完全一致で指定した** 3 custom agent を同時に起動する。generic `default` / `worker` / `explorer` へ fallback しない。

1. `pre-push-correctness-reviewer`: 「custom agent の developer instructions に従い、現在の branch 全差分を correctness review して report を返す」
2. `pre-push-independent-reviewer`: 「他 review の結果を見ず、custom agent の developer instructions に従い、同じ branch 全差分を独立 review して report を返す」
3. `pre-push-security-reviewer`: 「custom agent の developer instructions に従い、現在の branch 全差分を security review して report を返す」

各 profile は `sandbox_mode = "read-only"`、high reasoning、role 固有 instructions と完了 footer を定義する。3 本を並列起動できない場合は同じ named agent を順次実行してよいが、別 agent type や親 agent による代行は禁止する。1 本でも spawn・実行に失敗したら残りの report だけで完了扱いにしない。

## 3. 完了と marker を検証する

3 本すべての最終 report を待つ。それぞれの先頭 heading と末尾 footer が profile の契約どおりであることを確認する。Codex plugin の `SubagentStop` hook が `agent_type`、`agent_id`、`turn_id`、`model`、`last_assistant_message`、`stop_hook_active` を検証し、role ごとの既存 marker を自動更新する。

`mark-review.sh` その他の marker writer を直接実行しない。marker は各 agent の停止時点における commit 列、merge-base、branch diff、staged/unstaged diff の hash に bind される。review 後に修正・commit・amend・rebase すると失効するため、修正後は 3 review をすべて再実行する。

marker が更新されない場合は、generic agent で代替したり helper を直接呼んだりせず、次を確認して同じ named agent を再実行する。

- `/hooks` で pre-push-review の hook が trust 済みか
- `jq` と SHA-256 command (`sha256sum` または `shasum`) が利用可能か
- agent report に正しい heading/footer があるか
- setup inspect が全ファイル `current` を返すか

## 4. 報告

3 report を観点別に要約し、finding が無い場合も各 named agent の完了を明示する。finding がある場合は marker 更新済みでも先に修正し、再レビューを終えるまで push しない。
