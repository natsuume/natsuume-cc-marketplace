---
description: natsuume-statusline プラグインを ~/.claude/settings.json の statusLine.command に登録する
allowed-tools: Bash(bash:*)
---

# /natsuume-statusline:setup

このプラグインに含まれる statusline スクリプトを Claude Code の `statusLine.command` として登録します。

## 動作

`${CLAUDE_PLUGIN_ROOT}/scripts/setup.sh` を実行します。スクリプトは次を行います。

1. `~/.claude/settings.json` のタイムスタンプ付きバックアップを `~/.claude/settings.natsuume-statusline-backup.<timestamp>.json` として作成
2. plugin cache 配下から実行された場合は、`~/.claude/natsuume-statusline-entrypoint.sh` という安定した wrapper を設置し、`statusLine` フィールドをこの wrapper を指すよう上書き (ローカル clone 等の安定パスから実行された場合は wrapper を介さず entrypoint を直接登録)
3. 完了メッセージで バックアップ位置・登録ターゲット・エントリポイントを表示

既存の `statusLine` 設定がある場合もバックアップに残るので、元に戻したいときはバックアップで `settings.json` を上書きしてください。

> **なぜ wrapper を挟むか**: plugin cache は `~/.claude/plugins/cache/<marketplace>/<plugin>/<VERSION>/...` という version 固有パスに展開され、`statusLine.command` では `${CLAUDE_PLUGIN_ROOT}` 等が展開されません ([Claude Code bug #52079](https://github.com/anthropics/claude-code/issues/52079))。version 固有パスを直接焼き込むと `/plugin update` で旧 dir が消えた際に statusline が無言で壊れます。settings には不変の wrapper パスを書き、wrapper が実行時に最新版を解決することで update に自動追従させます。

## 実行

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/setup.sh"
```

実行後は次回のプロンプト更新からプラグイン版 statusline が有効になります。**cache 経由でインストールした場合は `/plugin update` 後の再 setup は不要**です (wrapper が実行時に active な version を自動解決します)。別の marketplace から入れ直した場合や wrapper を削除した場合のみ、再度このコマンドを実行してください。(ローカル clone 等の安定パスから setup した場合は wrapper を介さず、clone を入れ替えても自動追従はしません。)
