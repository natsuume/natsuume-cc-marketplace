---
name: review-codex
description: Codex で git push 前の correctness・独立 code review・security review を project-scoped custom agent 3 本で並列実行し、agent type selector が無い runtime では byte-identical template を読む default agent へ安全に互換 fallback して、SubagentStop hook に共有 push gate の hash marker を自動更新させる。pre-push-review に拒否された push の復旧時や、branch 全差分を push 前にレビューするときに使う
---

# pre-push-review for Codex

Claude Code の `/pre-push-review:review` と同じ branch 全差分を、Codex project custom agent 3 本で並列レビューする。runtime が提供する agent profile selector の有無に応じて named profile または限定的な `default` fallback を使い、native `SubagentStop` event から marker を更新する。Skill 自身は marker helper を呼ばない。

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

`agent_type` selector が無い場合だけ、Codex 0.144.4 互換の **`default` fallback** を使う。各 role につき別の `default` agent を起動し、その task に次の契約をすべて明記する。

1. 対応する `.codex/agents/pre-push-correctness-reviewer.toml`、`.codex/agents/pre-push-independent-reviewer.toml`、または `.codex/agents/pre-push-security-reviewer.toml` を先頭から末尾まで完全に読み取る
2. setup で byte-identical と確認済みのその 1 ファイルにある `developer_instructions` を、追加・省略・置換せずに全て遵守して review する
3. repository を変更せず、別 agent を spawn せず、role 固有の report heading と完了 footer を最終応答にそのまま含める

fallback では custom profile の `sandbox_mode = "read-only"` を selector 経由で適用できない。したがって read-only は template の `developer_instructions` と task による **instruction-enforced** な制約であり、sandbox による強制ではない。この保証差を最終報告に明示する。fallback 対象は runtime が返す `agent_type=default` だけであり、`worker` / `explorer` その他の generic type へ広げない。

named profile は `sandbox_mode = "read-only"`、high reasoning、role 固有 instructions と完了 footer を定義する。3 本を並列起動できない場合は、選択した同じ経路 (named または `default` fallback) で順次実行してよい。別 agent type や親 agent による代行は禁止する。1 本でも spawn・実行に失敗したら残りの report だけで完了扱いにしない。

## 3. 完了と marker を検証する

3 本すべての最終 report を待つ。それぞれの先頭 heading と末尾 footer が profile の契約どおりであることを確認する。Codex plugin の `SubagentStop` hook が `agent_type`、`agent_id`、`turn_id`、`model`、`last_assistant_message`、`stop_hook_active` を検証し、role ごとの既存 marker を自動更新する。named type は type ごとに定めた heading/footer を検証する。`default` は先頭の最初の非空行にある heading と最後の非空行にある完了 footer の完全一致ペアだけから role を確定し、未知 type、未知 heading、role が一致しない footer は受理しない。

Codex の既定 sandbox では `.git` が read-only なので、hook は writable な plugin data 内の `pre-push-review/markers/<repo-key>/` を使う。`PLUGIN_DATA` を優先し、未設定時は `CLAUDE_PLUGIN_DATA` を互換 fallback として使う。`repo-key` は physical git-dir の絶対 path を SHA-256 にした値で、repository と linked worktree を分離する。marker filename、review hash、push gate の判定契約は Claude Code の `.git` marker と共通である。選択した plugin data path が空・relative path の場合は `.git` へ fallback せず marker 更新を skip し、push gate が fail-closed に deny する。

`mark-review.sh` その他の marker writer を直接実行しない。marker は各 agent の停止時点における commit 列、merge-base、branch diff、staged/unstaged diff の hash に bind される。review 後に修正・commit・amend・rebase すると失効するため、修正後は 3 review をすべて再実行する。

Codex では `.git` marker の存在や mtime を完了確認に使わない。各 `SubagentStop` hook の marker update output を確認し、同じ repository state の push gate が許可することを完了条件とする。

marker が更新されない場合は、別の generic agent で代替したり helper を直接呼んだりせず、次を確認して同じ起動経路を再実行する。

- `/hooks` で pre-push-review の hook が trust 済みか
- hook output に `PLUGIN_DATA` / `CLAUDE_PLUGIN_DATA` の欠落・relative path・storage 作成失敗が出ていないか
- `jq` と SHA-256 command (`sha256sum` または `shasum`) が利用可能か
- agent report に正しい heading/footer があるか
- setup inspect が全ファイル `current` を返すか
- `spawn_agent` の `agent_type` selector の有無と、hook output の `agent_type` が選択した named / `default` 経路に一致しているか

## 4. 報告

3 report を観点別に要約し、finding が無い場合も各 reviewer agent の完了を明示する。`default` fallback を使った場合は、custom profile の read-only sandbox ではなく instruction-enforced read-only だったことも明示する。finding がある場合は marker 更新済みでも先に修正し、再レビューを終えるまで push しない。
