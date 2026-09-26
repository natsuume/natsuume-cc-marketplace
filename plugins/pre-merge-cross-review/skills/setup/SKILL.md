---
name: setup
description: pre-merge-cross-review の hooks module (Claude Mods) を読み込ませるため、user settings の env に CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 を書き込む。auto mode で merge や gh pr view が classifier に拒否されるときの設定で使う
---

# pre-merge-cross-review setup — hooks module の有効化

本 plugin の hooks module (`hooks/module/register.ts`) は、auto mode で merge gate を通過した単独の
`gh pr merge` と、単独の `gh pr view` / `gh pr checks` を、auto mode classifier の判定を経ずに
実行させる。hooks module は early access の機能で、Claude Code プロセスの env
`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` が無いと読み込まれない。本 skill はこの env を user settings に
書き込む。

## 1. 書き込む

次のコマンドを 1 回実行する。

```bash
pre-merge-cross-review-enable-function-hooks
```

出力は常に 2 行で、1 行目が結果、2 行目が説明である。

| 1 行目 | 意味 |
|---|---|
| `created` | settings.json が無かったため作成した |
| `updated` | 既存の settings.json の `env` に書き込んだ |
| `already-set` | 既に設定済みのため書き込まなかった |
| `aborted` | 書き込まずに中止した (2 行目が理由) |

## 2. 報告する

結果と 2 行目の説明をユーザに報告する。`created` / `updated` / `already-set` の場合は、あわせて次を伝える。

- 設定は次に起動する Claude Code から有効になるため、再起動が必要である
- hooks module は workspace trust を承諾したディレクトリでのみ読み込まれる
- managed settings の `disableAllHooks` / `allowManagedHooksOnly`、`--bare`、Safe mode では読み込まれない

`aborted` の場合は settings.json を手で直すか、シェルで `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` を
export してから Claude Code を起動する方法を案内する。settings.json を本 skill 内で手作業で
書き換えない。
