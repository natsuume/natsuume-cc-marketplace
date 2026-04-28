---
description: 公式 codex プラグインの commands/review.md と commands/adversarial-review.md をパッチして Skill tool から呼び出し可能にする
allowed-tools: Bash(bash:*)
---

# /codex-review-customize:setup

公式 codex プラグインの 2 つの review 系コマンド定義をローカルでパッチし、frontmatter の `disable-model-invocation: true` を削除して **Skill tool から呼び出し可能** にします。

| 対象ファイル | 対応コマンド |
|---|---|
| `commands/review.md` | `/codex:review` |
| `commands/adversarial-review.md` | `/codex:adversarial-review` |

## 動作

`${CLAUDE_PLUGIN_ROOT}/scripts/apply-patch.sh` を実行します。スクリプトは:

1. `~/.claude/plugins/marketplaces/*/plugins/codex/commands/<file>.md` を動的に解決 (`review.md` と `adversarial-review.md` の双方)
2. それぞれについて、既にパッチ済み (末尾マーカーで判定) なら何もしない
3. 未適用なら一時ファイルへパッチ内容を書き出し、frontmatter 健全性をチェックしてから atomic に上書き
4. すべての対象ファイルの処理が完了したら、対応する codex の cache (`~/.claude/plugins/cache/*/codex`) を 1 度だけ削除し、次回 `/reload-plugins` で marketplace clone から再 build されるようにする

いずれか 1 ファイルの処理に失敗したらその時点で中断します (already-patched は失敗扱いではない)。

## 実行

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/apply-patch.sh"
```

実行後は Claude Code で `/reload-plugins` を実行してください。これ以降 Claude が Skill tool 経由で `/codex:review` および `/codex:adversarial-review` を呼び出せるようになります (会話入力としての両コマンドは従前通り利用可能)。

## 復元 / 再適用

- **復元**: codex プラグインを再インストール、または marketplace clone で `git checkout commands/review.md commands/adversarial-review.md` (本プラグインは backup を残しません — git 管理が backup を兼ねます)
- **再適用 (codex update 後)**: `/codex-review-customize:setup` を再実行
