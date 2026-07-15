---
name: auto-codex
description: Apply agent-discipline's after-work follow-through explicitly in a Codex session when the user asks for the auto-codex workflow; this is the opt-in semantic substitute for Claude Code auto-mode injection, which Codex hooks cannot detect reliably.
---

# Auto follow-through for Codex

Apply this workflow only for the current user-requested task. Never infer activation from Codex `permission_mode`, approval policy, or sandbox mode.

1. Continue implementation, relevant tests, and evidence-backed verification without stopping at an arbitrary intermediate point, while obeying the current sandbox and approval policy.
2. Inspect pre-existing uncommitted changes before editing. Preserve unrelated user changes and identify which changes belong to this task.
3. Commit only changes within the requested scope. Push or create/update a PR only when the user's request includes publishing or delivery; invoking this Skill does not expand a local-only request into external publication.
4. Merge only when the user's request includes merge and all four gates are evidenced: the PR is ready (not draft), required checks pass, required approvals exist, and GitHub reports `mergeable == MERGEABLE` plus `mergeStateStatus == CLEAN`.
5. Never bypass a denied approval, weaken the sandbox, push directly to the default branch, commit secrets, or use destructive operations merely to complete the workflow. Stop and request the missing authority when an in-scope next step needs it.

This Skill reproduces the intent of follow-through, not Claude Code's Auto permission semantics. Its instructions cannot attest that Codex is in an Auto preset or grant additional tool authority.
