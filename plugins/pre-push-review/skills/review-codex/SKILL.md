---
name: review-codex
description: Codex で git push 前の correctness・独立 code review・security review を project-scoped custom agent 3 本で並列実行し、SubagentStop hook に共有 push gate の hash marker を自動更新させる。runtime が spawn_agent の agent_type selector を公開しない場合は generic agent を起動せず fail-closed に停止する
---

# pre-push-review for Codex

Claude Code の `/pre-push-review:review` と同じ branch 全差分を、Codex project custom agent 3 本で並列レビューする。runtime が `spawn_agent` tool の **`agent_type` selector** を公開し、named profile の identity が `SubagentStop` event へ引き継がれる場合だけ marker を更新する。Skill 自身は marker helper を呼ばない。

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

## 2. agent 起動 capability を確認して 3 本を並列実行

起動前に、現在の `spawn_agent` tool schema が **`agent_type` selector** を公開しているか確認する。selector の有無を推測したり、schema に無い引数を渡したりしない。

`agent_type` selector がある場合は、1 つの assistant turn から次の **agent_type を完全一致で指定した** 3 custom agent を同時に起動する。

1. `pre_push_correctness_reviewer`: 「custom agent の developer instructions に従い、現在の branch 全差分を correctness review して report を返す」
2. `pre_push_independent_reviewer`: 「他 review の結果を見ず、custom agent の developer instructions に従い、同じ branch 全差分を独立 review して report を返す」
3. `pre_push_security_reviewer`: 「custom agent の developer instructions に従い、現在の branch 全差分を security review して report を返す」

`agent_type` selector が無い場合は、generic agent を spawn しない。heading/footer や task instructions は reviewer identity の認証にならず、任意の generic agent が同じ出力を作れるためである。marker を生成せず停止し、「インストール済み Codex runtime は `spawn_agent` の `agent_type` selector を公開していないため、pre-push-review workflow を安全に実行できない」と報告する。helper の直接実行、generic agent、親 agent による代行は禁止する。

named profile は `sandbox_mode = "read-only"`、high reasoning、role 固有 instructions と完了 footer を定義する。3 本を並列起動できない場合は、同じ named agent type を完全一致で指定して順次実行してよい。1 本でも spawn・実行に失敗したら残りの report だけで完了扱いにしない。

## 3. 完了と marker を検証する

3 本すべての最終 report を待つ。それぞれの先頭 heading と末尾 footer が profile の契約どおりであることを確認する。Codex plugin の `SubagentStop` hook が 3 つの named `agent_type`、`agent_id`、`turn_id`、`model`、`last_assistant_message`、`stop_hook_active` を検証し、role ごとの既存 marker を自動更新する。generic type、未知 type、未知 heading、role が一致しない footer は受理しない。

Codex の既定 sandbox では `.git` が read-only なので、hook は writable な plugin data 内の `pre-push-review/markers/<repo-key>/` を使う。`PLUGIN_DATA` を優先し、未設定時は `CLAUDE_PLUGIN_DATA` を互換 fallback として使う。`repo-key` は physical git-dir の絶対 path を SHA-256 にした値で、repository と linked worktree を分離する。marker filename、review hash、push gate の判定契約は Claude Code の `.git` marker と共通である。選択した plugin data path が空・relative path の場合は `.git` へ fallback せず marker 更新を skip し、push gate が fail-closed に deny する。

`mark-review.sh` その他の marker writer を直接実行しない。marker は各 agent の停止時点における commit 列、merge-base、branch diff、staged/unstaged diff の hash に bind される。review 後に修正・commit・amend・rebase すると失効するため、修正後は 3 review をすべて再実行する。

Codex では `.git` marker の存在や mtime を完了確認に使わない。各 `SubagentStop` hook の marker update output を確認し、同じ repository state の push gate が許可することを完了条件とする。

marker が更新されない場合は、別の generic agent で代替したり helper を直接呼んだりせず、次を確認して同じ起動経路を再実行する。

- `/hooks` で pre-push-review の hook が trust 済みか
- hook output に `PLUGIN_DATA` / `CLAUDE_PLUGIN_DATA` の欠落・relative path・storage 作成失敗が出ていないか
- `jq` と SHA-256 command (`sha256sum` または `shasum`) が利用可能か
- agent report に正しい heading/footer があるか
- setup inspect が全ファイル `current` を返すか
- `spawn_agent` の `agent_type` selector が公開され、hook output の `agent_type` が指定した named profile に完全一致しているか

## 4. 報告

3 report を観点別に要約し、finding が無い場合も各 reviewer agent の完了を明示する。`agent_type` selector が無く開始前に停止した場合は、marker が生成されていないことと、現在の runtime ではこの workflow を安全に実行できないことを報告する。finding がある場合は marker 更新済みでも先に修正し、再レビューを終えるまで push しない。
