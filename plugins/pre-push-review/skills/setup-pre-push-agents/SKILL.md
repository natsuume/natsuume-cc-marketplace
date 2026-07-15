---
name: setup-pre-push-agents
description: pre-push-review の Codex project custom agents 3 本を、対象 git repository の .codex/agents へ差分確認と明示承認を経て安全に導入・更新する。$pre-push-review:review-codex が named agent を見つけられない場合、初回セットアップ時、または plugin 更新後の agent template 同期時に使う
---

# Setup pre-push-review agents

対象 repository に correctness・independent・security の read-only custom agent profile を導入する。承認前にファイルを変更しない。

## 1. Plugin root を解決する

この `SKILL.md` の実パスから `skills/setup-pre-push-agents/` の 2 階層上を `<plugin-root>` とする。hook 用環境変数には依存しない。対象 repository は現在の作業 repository とし、曖昧なら変更せず対象を確認する。

## 2. 変更計画を検査する

対象 repository の cwd で次を実行する。

```bash
bash "<plugin-root>/scripts/setup-codex-agents.sh" inspect
```

出力された target、各 TOML の `missing` / `current` / `different` / `unsafe-*` 状態、`plan-token` をユーザーへ提示する。`different` があれば destination と template の diff も読み取り専用で提示する。`unsafe-*` は自動解決せず停止する。

## 3. 明示承認を得る

作成・上書き対象を示し、`.codex/agents` を変更してよいか明示的な承認を求める。承認前に `write` を実行しない。すべて `current` なら変更不要と報告して終了する。

## 4. 承認した計画だけを適用する

承認後、inspect が返した token をそのまま渡す。

```bash
bash "<plugin-root>/scripts/setup-codex-agents.sh" write --plan-token "<approved-plan-token>"
```

token mismatch なら inspect 後に destination が変化している。再 inspect、差分提示、再承認を行い、古い token を再利用しない。script は symlink・非通常ファイルを変更せず、同一 directory 内の一時ファイルから atomic rename する。

## 5. 有効化を案内する

導入後は新しい Codex thread を開始し、`$pre-push-review:review-codex` を実行する。plugin hook が未承認なら `/hooks` で pre-push-review の hook 定義を確認して trust する必要があることも伝える。
