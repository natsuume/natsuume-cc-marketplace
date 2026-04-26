---
description: statusline プラグインを ~/.claude/settings.json の statusLine.command に登録する
allowed-tools: Bash(bash:*)
---

# /statusline:setup

このプラグインに含まれる statusline スクリプトを Claude Code の `statusLine.command` として登録します。

## 動作

`${CLAUDE_PLUGIN_ROOT}/scripts/setup.sh` を実行します。スクリプトは次を行います。

1. `~/.claude/settings.json` のタイムスタンプ付きバックアップを `~/.claude/settings.statusline-backup.<timestamp>.json` として作成
2. `statusLine` フィールドをこのプラグインのエントリポイント (`bash <plugin-root>/statusline/entrypoint.sh`) で上書き
3. 完了メッセージで バックアップ位置と新しいエントリポイントを表示

既存の `statusLine` 設定がある場合もバックアップに残るので、元に戻したいときはバックアップで `settings.json` を上書きしてください。

## 実行

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/setup.sh"
```

実行後は次回のプロンプト更新からプラグイン版 statusline が有効になります。プラグインを更新した際は再度このコマンドを実行してパスを再登録してください。
