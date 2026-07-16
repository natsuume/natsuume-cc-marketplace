---
name: setup-codex-semantic-validator
description: Inspect, explicitly enable, or disable agent-discipline's repo/worktree-scoped Codex semantic validator after disclosing that issue/PR payloads may be sent to a nested Codex provider. Use when a user asks to configure this validator or a scoped PreToolUse denial says opt-in is required.
---

# Setup Codex semantic validator

Treat provider and payload disclosure as an approval boundary. Never enable merely because a hook denied a command.

1. Resolve this Skill's plugin root, then run:

   ```bash
   bash <plugin-root>/scripts/setup-codex-semantic-validator.sh inspect --repo <target-worktree>
   ```

2. Show the user the returned `repository`, `marker`, `status`, and full `disclosure`. Explain that the nested `codex exec` can use a provider different from the parent and receives the hook payload, shell-pre-extracted inline or `--body-file` content, and current branch. It runs outside the repository with project instructions, config, web search, and shell/search tools disabled; its model is `gpt-5.6-sol` by default or explicitly `gpt-5.6-luna`.
3. Ask for an explicit enable or disable confirmation in the current turn. A token proves only that inspection used the current state; it is not user approval.
4. After confirmation, pass the matching token from that same inspection exactly once:

   ```bash
   bash <plugin-root>/scripts/setup-codex-semantic-validator.sh enable --repo <target-worktree> --approval-token <enableApprovalToken>
   bash <plugin-root>/scripts/setup-codex-semantic-validator.sh disable --repo <target-worktree> --approval-token <disableApprovalToken>
   ```

5. Run `inspect` again and report the final state. If the helper reports a stale token or any `unsafe-*` / `invalid-*` state, stop. Do not edit, replace, chmod, or remove the marker manually; report its path and status for user-directed remediation.

Consent is scoped to the resolved Git worktree and persists until disabled. It does not guarantee provider identity or verdict equality with Claude; it does guarantee that this adapter requests only `gpt-5.6-sol` or `gpt-5.6-luna`.
