# enforce-japanese-response プラグイン

日本語で応答する設定 (settings の `language`) なのに turn 末尾の応答が英語で書かれた場合に、Stop hook でそれを検知し、Claude に日本語で書き直させるプラグインです。

## バージョン

v0.1.0

## 概要

`Stop` hook で直前の応答本文 (`last_assistant_message`) を調べ、英語の応答と判定したら `{"decision": "block", "reason": "..."}` を返します。reason が Claude に渡されて turn が継続し、Claude は同じ内容を日本語で書き直します。英語の応答でなければ何も出力しません。

判定の前に、settings の `language` から目標言語を決めます。目標言語が日本語でなければ判定を行いません。

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install enforce-japanese-response@natsuume-plugins
```

本プラグインは Claude Code 専用で、Codex marketplace では配布していません。

## 機能一覧

### Hooks

#### enforce-japanese-response

**ファイル**: `hooks/scripts/enforce-japanese-response.sh`
**イベント**: Stop

**入力**: Stop hook の stdin JSON のうち `last_assistant_message` (直前の応答本文)、`stop_hook_active`、`cwd` を使います。

**出力**: 英語の応答と判定した場合のみ、stdout に次の JSON を出します。それ以外は何も出力しません。exit code は常に 0 です。

```json
{"decision": "block", "reason": "直前の応答が英語で書かれています。内容を変えずに日本語で書き直してください。ユーザが英語での出力 (翻訳・英文の文面等) を明示的に求めていた場合は書き直さず、その旨を日本語 1 文で添えて終えてください。"}
```

reason には、直前の応答が英語で書かれていること、内容を変えずに日本語で書き直すこと、ユーザが英語での出力 (翻訳・英文の文面等) を明示的に求めていた場合は書き直さずその旨を日本語 1 文で添えて終えること、の 3 点を含めます。

## 判定基準

1. `last_assistant_message` から次の範囲を取り除きます (この順に処理します)
   - fenced code block: ```` ``` ```` から次の ```` ``` ```` までの範囲 (閉じる ```` ``` ```` が無い場合は本文末尾まで)
   - インライン code: `` ` `` から次の `` ` `` までの範囲
   - URL: `http://` または `https://` で始まり、次の空白文字の直前までの文字列
2. 残りの本文で、ASCII 英字 (A-Z, a-z) の数を L、ひらがな・カタカナ・漢字の数を J として数えます
3. `L >= 40` かつ `J / (J + L) < 0.05` のとき英語の応答と判定します

コードや URL だけで英字が多くなる応答や、短い英語の定型句 (英字 39 字以下) は英語の応答とみなしません。日本語の文に英語の用語が多く混ざる応答も、ひらがな・カタカナ・漢字が全体の 5% 以上あれば英語の応答とみなしません。

## block しない条件

次のいずれかに当てはまる場合、hook は何も出力せず exit 0 で終わります。

- `stop_hook_active` が true (この hook の block で継続した turn の応答は再び block しない)
- `last_assistant_message` が無い・空文字列・文字列でない
- 除去後の本文で `L < 40`、または `J / (J + L) >= 0.05`
- 目標言語が日本語でない、または未設定 (次節)
- jq が無い・入力 JSON を解析できない等で判定できない (fail-open)

## 目標言語の決め方

次の順に settings ファイルを探し、`language` キーを持つ最初のファイルの値を目標言語とします。

1. `<cwd>/.claude/settings.local.json`
2. `<cwd>/.claude/settings.json`
3. `~/.claude/settings.json`

`<cwd>` は hook 入力の `cwd` です。`cwd` が無い場合は 1 と 2 を飛ばします。ファイルが存在しない・JSON として解析できない・`language` キーを持たない場合は、そのファイルを無いものとして次を探します。

値が次のいずれかなら日本語とみなします (英字の大文字小文字は区別しません)。

- `日本語`
- `ja`
- `ja-` で始まる値 (例: `ja-JP`)
- `japanese` (例: `Japanese`、`JAPANESE`)

値が上記以外、またはどのファイルにも `language` が無い場合は判定を行いません。

## 対象外

- **tool 呼び出しの合間の英語**: Stop hook は turn 末尾の応答だけを見るため、tool 呼び出しの合間に出力される英語の実況は検知しません
- **subagent の応答**: SubagentStop には登録しないため、subagent の応答は検知しません。subagent から受け取った英語の報告を受けて親 session が英語で応答した場合は、その親 session の応答が判定の対象になります

## 既知の制約

- 判定は文字種の比率だけで行います。英語の応答でも、ひらがな・カタカナ・漢字が 5% 以上含まれていれば検知しません
- `language` は Claude Code 本体と同じ settings の優先順位で解決しますが、managed settings・コマンドライン引数・`CLAUDE_CONFIG_DIR` による設定は参照しません

## ディレクトリ構成

```
enforce-japanese-response/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       └── enforce-japanese-response.sh
└── README.md
```

## 必要な実行環境

- `bash`
- `jq` (無い場合は判定を行わず、何も出力しません)

## 関連情報

- [Claude Code Hooks ドキュメント](https://code.claude.com/docs/en/hooks)
