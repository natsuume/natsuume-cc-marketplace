# repo-analytics プラグイン

GitHub の issue/PR タイムラインから AI タスクのリードタイム (着手→PR ready) を分析し、生存バイアス・サイズ交絡を統制した推移レポートを生成するプラグインです。

## バージョン

v0.2.0

### v0.1.3 → v0.2.0 の変更点

Codex 配布対応 (marketplace 移植) を廃止した。repo-analytics はもともと Codex 配布対象外だったため、Claude Code 版の leadtime Skill・生成物への影響はない。

### v0.1.2 → v0.1.3 の変更点 (#301, #305)

- `exclusions.prTimelineOverflow[].linkedIssues` を最終分類後に確定し、別の qualifying PR で `mainSeries` に入った issue、着手マーカー無し、NOT_PLANNED など overflow が分類除外の原因ではない issue の過大申告を解消した
- completion は証明できるが PR timeline overflow により ready 時刻だけが不明な着手済み issue を `exclusions.prReadyTimeUnknown` に分離し、open issue は `censored` から除外した。これにより全 `censored[].elapsedHoursLowerBound` の下限値解釈を維持する
- 集計 script / Skill / test の bug fix のため Claude Code version を patch bump した。repo-analytics は Codex 配布対象外なので Codex version / install surface は変更しない

### v0.1.1 → v0.1.2 の変更点 (#298)

- issue timeline が merge 済みと示す PR の収集 snapshot が古い場合、単一 PR を再取得して行全体を更新し、overflow を再検査するようにした
- 再取得の成功・失敗を `collection-diagnostics.json` に記録し、失敗時は旧 snapshot を維持したまま測定上の限界へ開示する fail-closed 経路を追加した

### v0.1.0 → v0.1.1 の変更点 (#300)

- leadtime 収集の認証確認と全 GitHub API query を `github.com` に固定し、`GH_HOST` が GitHub Enterprise を指す環境でも別 host の同名リポジトリを silent に分析しないようにした

## 概要

AI エージェントによるタスク実行が定着すると、「1 タスクあたりどれくらいの時間で完了しているか」「モデルや plugin の変更でリードタイムがどう変わったか」を継続的に把握したくなります。本プラグインは GitHub の issue/PR タイムライン (ラベル・コメント・close/reopen イベント) から着手時刻・PR ready 時刻・merge 時刻を推定し、打ち切り (censoring) や PR サイズによる交絡を統制したうえで、週次推移・区間統計を可視化した Artifact レポートとターミナルサマリを生成します。

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install repo-analytics@natsuume-plugins
```

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

- `gh` CLI (github.com に認証済み: `gh auth status --hostname github.com` が通ること)
- Python 3.11+ (標準ライブラリのみで動作)
- `jq 1.5+` (SKILL.md の収集手順で JSONL の overflow 検知・置換に使用)
- Claude Code (Artifact 発行・WebSearch が利用可能なセッション)
