---
name: setup-codex
description: Codex の組み込み status line を repository・git branch・context・5 時間/週次 rate limit の表示向けに構成する
---

# natsuume-statusline setup for Codex

Claude Code 版の任意 shell statusline は Codex に存在しないため、Codex の組み込み `/statusline` と `tui.status_line` で情報を近似する。独自 3 行 layout、色、gauge、statusline callback は再現しない。

## 推奨項目

次の順序を初期案とする。

```toml
[tui]
status_line = [
  "project-name",
  "current-dir",
  "git-branch",
  "context-used",
  "five-hour-limit",
  "weekly-limit"
]
```

対話的な Codex CLI では、まず `/statusline` を開き、同じ項目を選択・並べ替える方法を優先する。これにより Codex 自身が現在の version で有効な item ID だけを保存できる。

## config.toml を直接更新する場合

config が存在しない、通常ファイルとして安全に読めない、または backup を作れない場合は direct edit を
行わず、`/statusline` picker を使う。復元元を確保できない状態で新規 config を直接作らない。

1. `$CODEX_HOME/config.toml` を使う。`CODEX_HOME` が未設定なら `~/.codex/config.toml`
2. 現在の `[tui]` table と `status_line` を読み、ユーザーへ報告する
3. 書き換え前に確認を取り、同じ directory に timestamp 付き backup を作る。backup の作成と
   byte-for-byte 読み戻しが成功するまで config を変更しない
4. 既存 `[tui]` の他 key と他 table を保持し、`status_line` だけを更新した完全な TOML を config と
   同じ directory の一時ファイルへ書く。2 個目の `[tui]` table を追加せず、config 本体を
   in-place 編集しない
5. 一時ファイルの内容と権限を確認してから、同一 filesystem 内の rename で config を
   **atomic replace** する
6. `codex --strict-config app-server < /dev/null` で TOML と既知 config key を parse する。この command
   は stdin の EOF で終了し、model request は送信しない
7. strict parse が非 0 なら、他の処理へ進む前に backup を同じ directory の新しい一時ファイルへ
   コピーし、その一時ファイルを rename して config を **atomic restore** する。restore 後の config が
   backup と byte-identical であることを確認し、parse 失敗と restore 結果を報告して失敗終了する。
   restore 自体を検証できなければ、その重大な失敗と config/backup の両 path を報告し、成功とは
   絶対に報告しない
8. setup 完了を報告してよいのは atomic replace 後の strict parse が成功した場合だけとし、成功時は
   backup path も報告する

strict parse は status-line item ID の対応可否までは検査しないため、transaction 成功後の最終確認は
`/statusline` picker で行う。picker に存在しない item ID は自力で別名を推測せず、現在の picker が
提示する項目へ戻す。

一時ファイルの作成、backup、atomic replace のいずれかが strict parse より前に失敗した場合も、
変更の成否を確認して失敗として報告する。部分的な書き込みを setup 完了として扱わない。
