---
name: setup-codex
description: Codex の組み込み status line に5時間・週次 rate limitを表示し、詳細確認を /usage と codex-status Skillへ接続する。Claude statusLine wrapperの代替を設定するときに使う
---

# Codex rate-limit display setup

Codex の stock TUI には任意の statusline command wrapper がない。Claude の
`statusLine.command` を変更せず、組み込み表示と取得 Skill へ意図を分割する。

1. 対話的 CLI では `/statusline` を開く。
2. `five-hour-limit` と `weekly-limit` を有効にする。必要なら
   `project-name`、`current-dir`、`git-branch`、`context-used` も並べる。
3. Codex が picker から保存した config を再読込し、値を報告する。保存先は
   `$CODEX_HOME/config.toml`、`CODEX_HOME` が未設定なら `~/.codex/config.toml` とする。
4. 詳細な使用率、reset時刻、plan種別が必要なら `/usage` または
   `$rate-limit:codex-status` を使う。

`config.toml` を直接編集する必要がある場合は、既存の `[tui]` tableを読み、変更案を
提示して承認を得る。次の transaction 契約に従い、他のkeyを保持したまま対象だけを設定する。
config が存在しない、通常ファイルとして安全に読めない、または backup を作れない場合は direct edit を
行わず `/statusline` picker を使い、復元元なしで新規 config を直接作らない。

```toml
[tui]
status_line = ["five-hour-limit", "weekly-limit"]
```

1. config と同じ directory に timestamp 付き backup を作り、byte-for-byte 読み戻せることを
   確認する。backup 成功前に config を変更しない。
2. 完全な更新後 TOML を config と同じ directory の一時ファイルへ書く。config 本体を in-place
   編集せず、内容と権限を確認した一時ファイルを rename して **atomic replace** する。
3. `codex --strict-config app-server < /dev/null` で TOML と既知 config key を検証する。この command
   は stdin の EOF で終了し、model request は送信しない。
4. strict parse が非 0 なら、他の処理へ進む前に backup を同じ directory の新しい一時ファイルへ
   コピーし、その一時ファイルを rename して config を **atomic restore** する。restore 後の config が
   backup と byte-identical であることを確認し、parse 失敗と restore 結果を報告して失敗終了する。
   restore を検証できない場合は重大な失敗として config/backup の両 path を報告し、成功とは
   絶対に報告しない。
5. setup 完了を報告してよいのは atomic replace 後の strict parse が成功した場合だけとし、成功時は
   backup path も報告する。backup、一時ファイル、atomic replace が先に失敗した場合も失敗として
   報告し、部分的な書き込みを成功扱いしない。

strict parse は status-line item ID の対応可否までは検査しないため、transaction 成功後の最終確認は
`/statusline` picker で行う。picker に無い item ID を推測しない。

この代替はCodexのlimit情報を常時表示できるが、ClaudeとCodexのcacheを任意scriptで
合成する機能、独自gauge、複数行renderer、statusline実行時の副作用は提供しない。
