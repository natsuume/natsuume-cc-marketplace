# repo-analytics プラグイン

GitHub の issue/PR タイムラインから AI タスクのリードタイム (着手→PR ready) を分析し、生存バイアス・サイズ交絡を統制した推移レポートを生成するプラグインです。

## バージョン

v0.2.1

## 概要

AI エージェントによるタスク実行が定着すると、「1 タスクあたりどれくらいの時間で完了しているか」「モデルや plugin の変更でリードタイムがどう変わったか」を継続的に把握したくなります。本プラグインは GitHub の issue/PR タイムライン (ラベル・コメント・close/reopen イベント) から着手時刻・PR ready 時刻・merge 時刻を推定し、打ち切り (censoring) や PR サイズによる交絡を統制したうえで、週次推移・区間統計を可視化した Artifact レポートとターミナルサマリを生成します。

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install repo-analytics@natsuume-plugins
```

本プラグインは Claude Code 専用で、Codex marketplace では配布していません。

## 機能一覧

### Skills

#### leadtime

**ファイル**: `skills/leadtime/SKILL.md`

**呼び出し**: `/repo-analytics:leadtime`

**引数**:

| 引数 | 説明 |
|---|---|
| 対象 (省略可) | 省略時はカレントの git リポジトリ。ディレクトリパスを指定すると配下の git リポジトリを再帰探索する。`owner/repo` のカンマ区切りリストも指定できる |
| `since=YYYY-MM-DD` (省略可) | この日付以降に着手されたタスクのみを主要な集計対象にする |

**出力**: Artifact として発行するリードタイムレポート (散布図・区間分解・週次推移・イベント年表など) と、要点・主要数値・測定上の限界を要約したターミナルサマリ。

**副作用**: `gh` CLI による GitHub API の read-only query のみを行う。中間ファイル (収集した JSONL・集計結果) はプロジェクト内には作成せず、セッションの scratchpad にのみ保存する。

## ディレクトリ構成

```
repo-analytics/
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   └── leadtime/
│       ├── SKILL.md
│       └── scripts/
│           ├── fetch-issues.graphql
│           ├── fetch-prs.graphql
│           ├── fetch-issue-timeline.graphql
│           ├── fetch-pr-closing-issues.graphql
│           ├── fetch-pr-snapshot.graphql
│           └── compute_leadtime.py
└── README.md
```

## 必要な実行環境

- `gh` CLI (github.com に認証済み: `gh auth status --hostname github.com` が通ること)。全 GitHub API query は `GH_HOST` の設定に依らず github.com へ固定されます
- Python 3.11+ (標準ライブラリのみで動作)
- `jq 1.5+` (SKILL.md の収集手順で JSONL の overflow 検知・置換に使用)
- Claude Code (Artifact 発行・WebSearch が利用可能なセッション)
