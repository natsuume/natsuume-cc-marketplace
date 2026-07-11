# issue #227 Phase A: context cache dump の設計契約

本ファイルは TDD 2 段階の Phase A (設計記述 commit) であり、Phase B (実装本体) のマージ前に削除する。
実装が従うべき契約は issue #227 の body と本ファイルに記載のとおり。

## 目的

statusline stdin JSON の `context_window` データを per-session の一時 cache ファイルへ書き出す。
consumer は session-handoff plugin (#228)。

## ファイル構成

| ファイル | 変更 | 内容 |
|---|---|---|
| `statusline/context-cache-dump.sh` | 新規 | `dump_context_cache` 関数の定義 (line*.sh と同じ source 方式) |
| `statusline/main.sh` | 変更 | (1) jq eval に `session_id` 抽出を追加 (2) 末尾で source + 関数呼び出し |
| `.claude-plugin/plugin.json` | 変更 | version 0.5.1 → 0.6.0 |
| `README.md` (plugin) | 変更 | cache 機能と契約の記載 |
| `../../.claude-plugin/marketplace.json` | 変更 | version 同期 |
| `../../README.md` (root) | 変更 | version 記載があれば同期 |

## 関数シグネチャ

```bash
# 引数: $1=session_id (raw), $2=used_percentage, $3=total_input_tokens, $4=context_window_size
# stdout / stderr には何も出力しない。常に return 0 (fail-open)
dump_context_cache() { ...; }
```

main.sh 側の統合点:

- jq eval ブロックに `@sh "session_id=\(.session_id // "")"` を追加する
- line3 出力の後 (表示処理の完了後) に以下を追加する:

```bash
source "$SCRIPT_DIR/context-cache-dump.sh"
dump_context_cache "$session_id" "$ctx_pct" "$ctx_used" "$ctx_max"
```

## I/O 契約 (issue #227 / #228 に記載の plugin 間契約の実装詳細)

- 出力先: `${TMPDIR:-/tmp}/natsuume-context-cache/<sanitized_session_id>.json`
  - `sanitized_session_id` = `printf '%s' "$session_id" | tr -cd 'A-Za-z0-9._-'`
- 出力 JSON (jq -n で生成):
  - `updated_at`: `date +%s` の値 (number)
  - `session_id`: サニタイズ前の session_id (string)
  - `used_percentage`: $2 の値 (number)
  - `total_input_tokens`: $3 の値 (number)。検証に通らない場合はキーごと省略
  - `context_window_size`: $4 の値 (number)。検証に通らない場合はキーごと省略
- atomic write: サブシェル内で `umask 077` → cache ディレクトリ内に `mktemp` → 書き込み → `mv -f`
  (呼び出し元プロセスの umask を変更しない)

## 境界・異常系の挙動 (すべて無音 skip、return 0)

| 状況 | 挙動 |
|---|---|
| `session_id` が空、またはサニタイズ結果が空 | 書き込まない |
| `used_percentage` が空、または `^[0-9]+(\.[0-9]+)?$` に非マッチ | 書き込まない (context_window null / 欠落のケースを含む)。既存 cache は残す |
| `total_input_tokens` / `context_window_size` が空または `^[0-9]+$` に非マッチ | 当該キーのみ省略して書き込む |
| `jq` 不在 | 書き込まない (main.sh 冒頭の guard と二重の防御) |
| `mkdir -p` / `mktemp` / `mv` の失敗 | 書き込まない。エラー出力もしない |

## 非機能contract

- 表示への不干渉: 関数は stdout / stderr に一切出力しない。呼び出し位置は全表示出力の後
- Linux (WSL2) / macOS 両対応: GNU/BSD で挙動が異なるオプションを使わない
  (mktemp は TEMPLATE 引数形式 `mktemp "$dir/.ctx.XXXXXX"`、date は `+%s` のみ使用)
- 秘匿情報を書かない: 出力は上記スキーマの数値と session_id のみ
