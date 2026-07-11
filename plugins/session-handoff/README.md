# session-handoff プラグイン

context 使用率が閾値を超えたら handoff ドキュメントの作成を Claude に促し、次のセッション
(`/clear` または起動直後) にその内容を自動注入するプラグインです。長時間セッションが
context 圧縮や `/clear` を挟んでも、直前までの背景・進行中の作業・残作業を新セッションへ
引き継げるようにします。

## バージョン

v0.1.0

## 機能概要

2 つの hook で構成されます。

1. **検知 (`detect-context-threshold.sh`, PostToolUse)**: context 使用率が閾値
   (既定 60%) を超えたことをツール実行のたびに検知し、超えていれば handoff ドキュメントの
   作成を Claude に促す指示を注入する
2. **注入 (`inject-pending-handoff.sh`, SessionStart)**: `/clear` または起動直後の新セッションで、
   直近 24 時間以内に作成された未消費の handoff があれば、その内容を前置き文とともに自動注入する

## 2 hook の動作と境界

### 検知 hook (PostToolUse, matcher `*`)

- 対象は cwd が git リポジトリのセッションのみ (非 git プロジェクトでは検知しない)
- context 使用率は natsuume-statusline (後述) が書き出すキャッシュファイルから読む
- **1 セッション 1 回**: 一度通知したセッションでは再通知しない (marker による排他)
- **marker の意味は「通知を発行済み」であり「handoff が保存済み」ではない**: 通知後に
  Claude が実際に handoff ファイルを書く前にセッションが終了しても、次回以降のそのセッションで
  再通知はされない (割り切り。marker はあくまで「注入した」ことの記録であり、Claude が実際に
  指示に従ったかどうかまでは追跡しない)
- marker の claim は `mkdir` による atomic 操作で行うため、並列なツール実行があっても通知は
  1 回しか発行されない

### 注入 hook (SessionStart, matcher `clear|startup`)

- `source` が `resume` / `compact` のときは注入しない (`/clear` と起動直後のみが対象)
- 走査対象は `<git-dir>/session-handoff/` 配下の `pending-*.md` (mtime 24 時間以内のもの)。
  30 日を超えたファイルは best-effort で削除する
- **at-most-once**: 複数セッションが同時に起動しても、rename (`pending-` → `consumed-`)
  の atomic 性により同じ handoff が 2 重に注入されることはない。ただし、claim (rename) の
  直後・出力の直前にプロセスが異常終了した場合、その handoff は「消費済み」のまま失われる
  (再送しない割り切り)
- **staleness**: 24 時間を超えた pending は注入しない (作成から時間が経ちすぎた handoff を
  無条件に新セッションへ流し込まない)。24 時間以内に複数の pending がある場合、最新の 1 件のみを
  本文注入し、残りはパスの列挙に留める

## producer 依存 (context 使用率キャッシュ)

検知 hook が読む context 使用率は、自プラグインでは取得できません。以下のいずれかで
`${TMPDIR:-/tmp}/natsuume-context-cache-<uid>/<session_id>.json` にキャッシュが書き出されている
ことが前提です。

- **natsuume-statusline (v0.6.0+)** を `statusLine.command` に登録している (通常の利用経路)
- 上記を使わない場合は、本プラグインの `/session-handoff:setup` で cache 専用の
  安定 launcher を登録する (下記)

キャッシュが無い/古い間、検知 hook は無音でスキップします (エラーにはなりません)。

## setup skill と launcher

`/session-handoff:setup` は、context 使用率キャッシュの producer を用意するための任意の
セットアップです。現在の `~/.claude/settings.json` の `statusLine.command` を分類し、以下の
いずれかを行います。

- natsuume-statusline (0.6.0+) が既に構成済み、または cache が既に稼働中と判断できる場合は
  何もしない (構成済みとして報告する)
- 他の statusline が設定済みの場合は、既存の表示を変えずに包む安定 launcher
  (`~/.claude/session-handoff-statusline-launcher.sh`) を設置する。設置前に承認を得る
- statusline が未設定の場合は、natsuume-statusline の導入または表示無しの
  cache-only launcher 登録のいずれかを選ばせる

### 連鎖検査

他の statusline を包む前に、**1 段の連鎖検査**を行います。既存の statusline が指すスクリプト
ファイルの中に、(a) 自 launcher ファイル名への平文参照、または (b) 既知形式の base64 代入行
(例: `rate-limit` の `INNER_COMMAND_B64=...`) を decode した中身への参照、のいずれかが見つかった
場合は「既にラップ済み」と判断して再ラップせず、連鎖の存在を報告するに留めます。代入行はあるが
decode に失敗し、かつ自 launcher が既に存在する場合は、循環リスクありとして変更せず曖昧な連鎖を
報告します。

### 実行時の再帰ガード

連鎖検査は setup 時点の静的な検査であり、検査をすり抜けた循環構成 (例えば手動編集や他 plugin の
将来変更) が万一残っていた場合の安全網として、launcher は実行時にも固有名の環境変数
(`NATSUUME_SESSION_HANDOFF_LAUNCHER_ACTIVE`) で自己再帰を検知します。既に設定済みなら何も出力
せず正常終了するため、無限再帰・ハングにはなりません (ただし循環構成が残っている間、表示は
空になります)。

### launcher の設計

- settings.json には plugin cache の version 固有パスを直接焼き込みません。安定パス
  (`~/.claude/session-handoff-statusline-launcher.sh`) を生成して登録し、launcher が実行時に
  plugin cache から session-handoff 最新版の `scripts/context-cache-dump.sh` を解決します
  (`/plugin update` 後も再 setup 不要)
- 元の statusline command は base64 で launcher 内に損失なく埋め込みます (single quote・改行を
  含むコマンドでも壊れません)
- dump の解決・書き込みに失敗しても、元の statusline への委譲は継続します (fail-open)

## 環境変数

| 変数 | 意味 | 既定値 |
|---|---|---|
| `SESSION_HANDOFF_THRESHOLD` | 検知 hook が使う context 使用率の閾値 (1〜99 の整数)。不正値・未設定は既定値にフォールバック | `60` |

## スコープ外

- **Stop hook による handoff 作成の強制**: 閾値超過時の指示注入のみを行い、Claude が実際に
  ファイルを書いたかどうかまでは検証・強制しません
- **transcript のパース**: context 使用率はキャッシュ経由でのみ取得し、会話 transcript を
  解析して使用率を独自算出することはしません
- **同一セッション内の再警告**: 1 セッション 1 回の通知のみで、閾値を超え続けても再通知は
  行いません

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install session-handoff@natsuume-plugins
```

context 使用率キャッシュの producer が未構成の場合は、続けて setup skill を実行してください。

```
/session-handoff:setup
```

## 機能一覧

### Hooks

| Hook 名 | イベント | 説明 |
|---------|---------|------|
| `detect-context-threshold` | PostToolUse (`*`) | context 使用率が閾値を超えたことを検知し、handoff 作成指示を注入する (1 セッション 1 回) |
| `inject-pending-handoff` | SessionStart (`clear\|startup`) | 直近 24 時間以内の未消費 handoff があれば自動注入する (at-most-once) |

### Skills

| スキル名 | コマンド | 説明 |
|---------|---------|------|
| setup | `/session-handoff:setup` | context 使用率キャッシュの producer (natsuume-statusline または安定 launcher) を構成する |

### スクリプト

| ファイル | 用途 |
|---------|------|
| `scripts/setup-wrapper.sh` | setup skill から呼ばれる設置スクリプト本体 (inspect / install-wrap / install-cache-only / regenerate-launcher) |
| `scripts/context-cache-dump.sh` | natsuume-statusline v0.6.0 の同名ファイルの同梱コピー (`dump_context_cache` 関数)。launcher の dump 処理から解決・source される |

## 関連情報

- [natsuume-statusline プラグイン](../natsuume-statusline/README.md) — context 使用率キャッシュの標準 producer
- [rate-limit プラグイン](../rate-limit/README.md) — 同種の launcher パターン (`INNER_COMMAND_B64`) を採用する姉妹プラグイン

## キーワード

`session-handoff` `context-window` `handoff` `session-start` `statusline` `cache` `hook` `skill`
