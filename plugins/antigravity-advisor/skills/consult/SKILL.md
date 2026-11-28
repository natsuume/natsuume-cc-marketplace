---
name: consult
description: Antigravity CLI (agy) を read-only の advisor として相談し、方針にコミットする前・行き詰まったとき・方針転換を検討するときに plan / course-correction の助言を受け取る。「Antigravity に相談」「agy に相談」「Antigravity でセカンドオピニオン」依頼で使う
---

# /antigravity-advisor:consult — Antigravity への相談

相談 request を Antigravity CLI (agy) に渡し、助言を受け取る。agy はリポジトリのファイルを絶対パスで読んで裏取りしたうえで助言を返す。wrapper は agy を git の work tree の外の空のディレクトリで起動するため、相談対象のリポジトリの `.agents/` (hook・MCP・rules・skills) は読み込まれない。あわせて、ファイルの書き込み・shell の実行・MCP・URL の取得と、`$HOME` の下の認証情報の置き場所 (`.ssh`・`.aws` 等) の読み取りを拒否する権限設定を渡し、git の work tree 内では実行前後の `.git` の設定・hook、HEAD の branch とその commit、working tree の内容の変化も検査する。model は agy の既定を使い、環境変数 `ANTIGRAVITY_ADVISOR_MODEL` が設定されているときだけその model を指定する。

この保証には次の制約が残る (詳細は plugin の README の「read-only の保証の範囲」)。

- agy の読み取りは相談対象のリポジトリに限られず、`$HOME` の下の認証情報の置き場所を除くローカルのどのファイルにも及ぶ。読んだ内容は model の提供元に送られ、助言の本文として返りうる
- global の `~/.gemini/config` の設定 (hook を含む) は効く
- 権限の種別が分からない tool (ブラウザー系・web 検索・subagent 等) は拒否の対象外になりうる
- `--mode plan` は権限の境界ではない

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

- agy はディレクトリの一覧や検索のツールを使えない可能性があるため、読ませたいファイルは「集めた証拠」に絶対パスで列挙する
- 1 つの相談には問いを 1 つだけ書く。論点が複数あるときは相談を分ける
- 助言と手元の証拠の衝突を解消するための再相談では、前回の助言の要点と、それと衝突する証拠を「集めた証拠」に書き、問いを「どの制約が決め手か」の形にする。前回の相談の文脈は引き継がれないため、毎回 self-contained な新規の相談として書く
- agy は request と同じ言語で答える (wrapper の指示による)
- token や credential などの秘密情報を request に書かない

## 2. runner の起動

`Agent` tool で次の runner を 1 体起動し、組み立てた request の全文を prompt として渡す。

- `subagent_type: "antigravity-advisor:antigravity-advisor-runner"`、`model: "sonnet"`
- 起動 mode は指定しない (Claude Code が決める)
- runner の report は completion notification (auto mode では SubagentHandback、それ以外では SubagentStop) で後続の turn に届く。report を受け取るまで相談を完了扱いにしない

runner が request を session scratchpad のファイルに書き、wrapper (`scripts/run-antigravity-advisor.sh`) を通じて agy を 1 回起動する。main session から wrapper や agy を Bash で直接実行せず、request の本文を Bash の command 文字列に載せない。

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
| workspace-modified | 相談の前後で、`.git` の設定・hook・info、HEAD の branch とその commit、または working tree の状態や内容が変わった。agy による変更か、相談中に自分やユーザーが同じ work tree を変更したかは区別されない。`git status`・`git diff`・`git log` と `.git` の設定・hook で変更内容を確認し、意図しない変更があれば作業報告に含める |
| other | 上記以外 (runner が request のファイルを書けない、wrapper が見つからない、wrapper が git の work tree の外に一時ディレクトリを作れない、Bash の実行が background へ移った等) |
