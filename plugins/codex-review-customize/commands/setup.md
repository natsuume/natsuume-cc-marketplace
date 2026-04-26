---
description: 公式 codex プラグインの commands/review.md をパッチして Skill tool 呼び出し許可 + 日本語出力にする
allowed-tools: Bash(bash:*)
---

# /codex-review-customize:setup

公式 codex プラグインの `commands/review.md` をローカルでパッチし、`/codex:review` の挙動を 2 点変更します。

1. frontmatter の `disable-model-invocation: true` を削除して **Skill tool から呼び出し可能** にする
2. 本文末尾に「Codex の出力を日本語に翻訳してから提示」する指示を追記

## 動作

`${CLAUDE_PLUGIN_ROOT}/scripts/apply-patch.sh` を実行します。スクリプトは:

1. `~/.claude/plugins/marketplaces/*/plugins/codex/commands/review.md` を動的に解決
2. 既にパッチ済み (末尾マーカーで判定) なら何もせず終了
3. 未適用なら一時ファイルへパッチ内容を書き出し、frontmatter 健全性をチェックしてから atomic に上書き
4. 対応する codex の cache (`~/.claude/plugins/cache/*/codex`) を削除し、次回 `/reload-plugins` で marketplace clone から再 build されるようにする

## 実行

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/apply-patch.sh"
```

実行後は Claude Code で `/reload-plugins` を実行してください。これ以降の `/codex:review` (Skill 経由含む) の出力が日本語化されます。

## 復元 / 再適用

- **復元**: codex プラグインを再インストール、または marketplace clone で `git checkout commands/review.md` (本プラグインは backup を残しません — git 管理が backup を兼ねます)
- **再適用 (codex update 後)**: `/codex-review-customize:setup` を再実行
