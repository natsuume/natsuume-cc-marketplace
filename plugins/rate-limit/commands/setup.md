---
description: rate-limit プラグインの statusline キャッシュ連携 (安定 launcher) を ~/.claude/settings.json に登録する
allowed-tools: Bash(bash:*)
---

# /rate-limit:setup

`/rate-limit:status` が経路① (statusline キャッシュ、公式データ) を使えるようにするための任意セットアップです。**必須ではありません** — 未実行でも `/rate-limit:status` は経路② (非公式 OAuth endpoint) 単独で動作します。

## 動作

`${CLAUDE_PLUGIN_ROOT}/scripts/setup.sh` を実行します。スクリプトは次を行います。

1. `~/.claude/settings.json` のタイムスタンプ付きバックアップを作成 (同秒衝突は連番回避)
2. 既存の `statusLine.command` 文字列を読み取る (`statusLine` 未設定なら空)
3. 安定 launcher `~/.claude/rate-limit-statusline-launcher.sh` を settings 更新より先に atomic 設置。launcher は plugin cache の version dir が変わっても active な version の cache-write-wrapper.sh を自動解決し、既存の `statusLine.command` をそのまま内側コマンドとして包む (既存 statusline の表示は変化しません)
4. `statusLine` を launcher を指す設定に atomic 更新 (更新前後で JSON validate)

既に `statusLine.command` が launcher を指している場合は二重に wrap せず、launcher の中身だけ最新化して終了します (再実行しても idempotent)。

> **なぜ launcher を挟むか**: plugin cache は `~/.claude/plugins/cache/<marketplace>/<plugin>/<VERSION>/...` という version 固有パスに展開され、`statusLine.command` では `${CLAUDE_PLUGIN_ROOT}` 等が展開されません ([Claude Code bug #52079](https://github.com/anthropics/claude-code/issues/52079))。version 固有パスを直接焼き込むと `/plugin update` で旧 dir が消えた際に statusline が無言で壊れます。settings には不変の launcher パスを書き、launcher が実行時に最新版を解決することで update に自動追従させます。

## 実行

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/setup.sh"
```

実行結果 (バックアップ位置・登録した launcher パス・完了メッセージまたはエラー内容) をそのままユーザに報告してください。
