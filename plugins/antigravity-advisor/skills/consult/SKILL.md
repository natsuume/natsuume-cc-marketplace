---
name: consult
description: Antigravity CLI (agy) を read-only の advisor として相談し、方針にコミットする前・行き詰まったとき・方針転換を検討するときに plan / course-correction の助言を受け取る。「Antigravity に相談」「agy に相談」「Antigravity でセカンドオピニオン」依頼で使う
---

# /antigravity-advisor:consult — Antigravity への相談

相談 request を Antigravity CLI (agy) に渡し、助言を受け取る。agy は plan mode で起動され、リポジトリのファイルを読んで裏取りしたうえで助言を返す。shell コマンドの実行とファイルの作成・編集は wrapper が agy への指示で禁じ、git の work tree 内では wrapper が実行前後の HEAD と working tree の変化を検査する。model は agy の既定を使い、環境変数 `ANTIGRAVITY_ADVISOR_MODEL` が設定されているときだけその model を指定する。

## 1. 相談 request の組み立て

agy はこの会話の文脈を一切持たないため、request は self-contained に書く。次の 6 項目を含める。

| 項目 | 書く内容 |
|---|---|
| 目的 | 取り組んでいるタスクと、この相談で決めたいこと |
| 制約・受入基準 | 変えてはならない制約と、満たすべき受入基準 |
| 現在の方針 | 採っている、または採ろうとしているアプローチとその理由 |
| 集めた証拠 | 試したこと・観測した事実。関連するリポジトリ内のファイルは絶対パスで示す (agy がファイルを読めるため、本文の貼り込みは不要) |
| 具体的な問い | 相談したい問いを 1 つ |
| 期待する回答形式 | 推奨する方針・理由・リスク・次の一手など、採否の判断に必要な形 |

```
## 目的
<タスクと、この相談で決めたいこと>

## 制約・受入基準
- <変えてはならない制約>
- <受入基準>

## 現在の方針
<アプローチとその理由>

## 集めた証拠
- <試したこと・観測した事実>
- 関連ファイル: /absolute/path/to/file

## 問い
<問いを 1 つ>

## 期待する回答形式
推奨する方針・理由・リスク・次の一手を簡潔に述べる。主張は読んだファイルと観測した事実に基づける。推測は推測と明記し、確認できないことは確認できないと述べる。
```

- 1 つの相談には問いを 1 つだけ書く。論点が複数あるときは相談を分ける
- 助言と手元の証拠の衝突を解消するための再相談では、前回の助言の要点と、それと衝突する証拠を「集めた証拠」に書き、問いを「どの制約が決め手か」の形にする。前回の相談の文脈は引き継がれないため、毎回 self-contained な新規の相談として書く
- agy は request と同じ言語で答える (wrapper の指示による)
- token や credential などの秘密情報を request に書かない

## 2. runner の起動

`Agent` tool で次の runner を 1 体起動し、組み立てた request の全文を prompt として渡す。

- `subagent_type: "antigravity-advisor:antigravity-advisor-runner"`、`model: "sonnet"`
- 起動 mode は指定しない (Claude Code が決める)
- runner の report は completion notification (auto mode では SubagentHandback、それ以外では SubagentStop) で後続の turn に届く。report を受け取るまで相談を完了扱いにしない

runner が request を一時ファイルに書き、wrapper (`scripts/run-antigravity-advisor.sh`) を通じて agy を 1 回起動する。main session から wrapper や agy を Bash で直接実行せず、request の本文を Bash の command 文字列に載せない。

## 3. 助言の扱い

- 助言はフラットに扱う。自分の証拠・推論と同じ土俵で突き合わせて採否を決め、採否と理由を作業報告に明示する。黙って無視しない
- 助言の要点を勝手に落とさない
- コード差分の finding を得る用途には使わない
- 設計・仕様の決定はユーザーの専権事項であり、助言は判断材料に留める。助言を根拠にユーザーへの確認を省略しない

## 4. 失敗時

runner が `Status: execution-failed` を返した場合は、相談なしで作業を続けてよい。相談できなかったことと理由 (`Failure class` と `Recovery direction` の要点) を作業報告に含める。

| Failure class | 意味 |
|---|---|
| invalid-request | request が空、または `ANTIGRAVITY_ADVISOR_MODEL` の値が不正 |
| agy-unavailable | agy CLI または jq が PATH に無い |
| agy-failed | agy が失敗した (未ログイン、model 名の誤り、利用枠の超過等) |
| incomplete-response | agy の応答が途中で終わった、または拒否された操作があった |
| workspace-modified | 相談の前後で HEAD または working tree が変わった。agy による変更か、相談中に自分やユーザーが同じ work tree を変更したかは区別されない。`git status` と `git log` で変更内容を確認し、意図しない変更があれば作業報告に含める |
| other | 上記以外 (wrapper が見つからない、一時ディレクトリを作れない、Bash の実行が background へ移った等) |
