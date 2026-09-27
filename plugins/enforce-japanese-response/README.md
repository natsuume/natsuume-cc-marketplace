# enforce-japanese-response プラグイン

日本語で応答する設定 (settings の `language`) なのに Claude のメッセージが英語で書かれた場合に、それを検知し、Claude に日本語で書き直させるプラグインです。turn 末尾の応答に加えて、tool 呼び出しの合間に表示されるメッセージも検知します。

## バージョン

v0.2.0

## 概要

検知の対象と使う hook は次のとおりです。

- **turn 末尾の応答**: `Stop` hook で直前の応答本文 (`last_assistant_message`) を調べ、英語の応答と判定したら `{"decision": "block", "reason": "..."}` を返します。reason が Claude に渡されて turn が継続し、Claude は同じ内容を日本語で書き直します。英語の応答でなければ何も出力しません
- **tool 呼び出しの合間のメッセージ**: `MessageDisplay` hook と `PostToolBatch` hook を組み合わせて検知します
  - `MessageDisplay` は表示されるすべての assistant のテキストを受け取りますが、表示専用のイベントで Claude には何も返せません。そこで、表示されたメッセージが英語なら、書き直し指示待ちの印 (`pending`) を状態ディレクトリー (後述) に残すだけにします。表示の内容は変えません
  - `PostToolBatch` は並列の tool 呼び出しの batch 全体が終わった後、次のモデル呼び出しの前に 1 回だけ発火し、`additionalContext` で Claude に文脈を渡せます。`pending` があればそれを消し、直前のメッセージを日本語で書き直して再掲してから作業を続けるよう指示します

turn 末尾の応答は Stop hook 自身が扱うため、Stop hook は `pending` を消して次の turn に持ち越さないようにします。

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

**入力**: Stop hook の stdin JSON のうち `last_assistant_message` (直前の応答本文)、`stop_hook_active`、`cwd`、`session_id` を使います。環境変数は `CLAUDE_PROJECT_DIR` (Claude Code が渡すプロジェクトのルート)、`HOME`、`TMPDIR` を使います。

**出力**: 英語の応答と判定した場合のみ、stdout に次の JSON を出します。それ以外は何も出力しません。exit code は常に 0 です。

```json
{"decision": "block", "reason": "直前の応答が英語で書かれています。内容を変えずに日本語で書き直してください。ユーザが英語での出力 (翻訳・英文の文面等) を明示的に求めていた場合は書き直さず、その旨を日本語 1 文で添えて終えてください。"}
```

reason には、直前の応答が英語で書かれていること、内容を変えずに日本語で書き直すこと、ユーザが英語での出力 (翻訳・英文の文面等) を明示的に求めていた場合は書き直さずその旨を日本語 1 文で添えて終えること、の 3 点を含めます。

入力を JSON object として解析できたら、`stop_hook_active` や目標言語に依らず、判定より前にその session の `pending` を消します。

#### record-english-message

**ファイル**: `hooks/scripts/record-english-message.sh`
**イベント**: MessageDisplay

**入力**: MessageDisplay hook の stdin JSON のうち `session_id`、`prompt_id` (任意)、`cwd`、`message_id`、`index`、`final`、`delta`、`agent_id` (subagent の中でのみ付く) を使います。環境変数は `CLAUDE_PROJECT_DIR`、`HOME`、`TMPDIR` を使います。

**出力**: stdout には何も出力しません (出力すると表示が置き換わるため)。exit code は常に 0 です。

**処理**: 1 つのメッセージは複数の batch に分かれて届くため、`delta` を `message_id` ごとのバッファーに連結します。`index` が 0 の batch でバッファーを作り直し、それ以外の batch では追記します。`final` が true の batch でバッファー全体をメッセージ本文として取り出してバッファーを消し、目標言語が日本語で、本文が英語と判定されたら `pending` を作ります。`pending` の内容はそのときの `prompt_id` です。`claude -p` などの非対話の実行では、メッセージ全体が `index` 0・`final` true の 1 回の呼び出しで届き、同じ処理で扱います。

次の場合は状態を読み書きせずに終わります。

- `agent_id` が空でない文字列 (subagent のメッセージ)
- `session_id` または `message_id` が `^[A-Za-z0-9_-]{1,128}$` に一致しない
- `delta` が文字列でない、`index` が 0 以上の整数でない、`final` が真偽値でない
- jq が無い・入力 JSON を解析できない (fail-open)

#### request-japanese-rewrite

**ファイル**: `hooks/scripts/request-japanese-rewrite.sh`
**イベント**: PostToolBatch

**入力**: PostToolBatch hook の stdin JSON のうち `session_id`、`prompt_id` (任意)、`agent_id` (subagent の中でのみ付く) を使います。環境変数は `TMPDIR` を使います。

**出力**: `pending` があれば消し、stdout に次の JSON を出します。`systemMessage` はユーザ向けの警告で、`additionalContext` が次のモデル呼び出しの前に Claude に渡されます。exit code は常に 0 です。

```json
{"systemMessage": "enforce-japanese-response: 英語のメッセージを検知したため、日本語での書き直しを指示しました。", "hookSpecificOutput": {"hookEventName": "PostToolBatch", "additionalContext": "直前に表示したメッセージは英語で書かれていました (settings の language は日本語です)。そのメッセージを内容を変えずに日本語で書き直して再掲してから作業を続け、以降のメッセージも日本語で書いてください。ユーザが英語での出力 (翻訳・英文の文面等) を明示的に求めていた場合は、書き直さずにそのまま作業を続けてください。"}}
```

次の場合は何も出力しません。

- `pending` が無い
- `pending` の内容 (記録したときの `prompt_id`) と入力の `prompt_id` がどちらも空でなく、異なる (前の turn の印を持ち越さないため。`pending` は消します)
- `agent_id` が空でない文字列 (subagent の tool batch。親 session の `pending` は消さずに残します)
- `session_id` が `^[A-Za-z0-9_-]{1,128}$` に一致しない
- jq が無い・入力 JSON を解析できない (fail-open)

## 状態ディレクトリー

MessageDisplay hook と PostToolBatch hook の間の受け渡しには、session ごとの状態ディレクトリーを使います。

```
${TMPDIR:-/tmp}/enforce-japanese-response/<session_id>/
├── pending                # 英語のメッセージを表示したが、まだ書き直しを指示していない印 (内容は prompt_id)
└── buffers/<message_id>   # メッセージの delta を連結するバッファー
```

環境変数 `TMPDIR` が未設定または空文字列なら `/tmp` を使います。ディレクトリーとファイルは所有者のみが読み書きできる権限で作ります。

## 判定基準

turn 末尾の応答と tool 呼び出しの合間のメッセージに、同じ基準を使います。

1. 本文から次の範囲を取り除きます (この順に処理します)
   - fenced code block: ```` ``` ```` から次の ```` ``` ```` までの範囲 (閉じる ```` ``` ```` が無い場合は本文末尾まで)
   - インライン code: `` ` `` から次の `` ` `` までの範囲
   - URL: `http://` または `https://` と、それに続く 1 文字以上の URL 本体。URL 本体は印字可能な ASCII (0x21〜0x7E) のうち `(` `)` `<` `>` `[` `]` `"` を除いた文字の並びで、空白文字・印字可能な ASCII 以外の文字 (日本語など)・上記 7 文字のいずれかの直前で終わります。そのため、URL の直後に空白なしで続く日本語 (`https://example.com/を参照`) や Markdown のリンクの閉じ括弧 (`[PR](https://...)`) は URL に含めません
2. 残りの本文で、ASCII 英字 (A-Z, a-z) の数を L、ひらがな・カタカナ・漢字の数を J として数えます
3. `L >= 40` かつ `J / (J + L) < 0.05` のとき英語と判定します

コードや URL だけで英字が多くなるメッセージや、短い英語の定型句 (英字 39 字以下) は英語とみなしません。日本語の文に英語の用語が多く混ざるメッセージも、ひらがな・カタカナ・漢字が全体の 5% 以上あれば英語とみなしません。

## block しない条件 (Stop hook)

次のいずれかに当てはまる場合、Stop hook は何も出力せず exit 0 で終わります。

- `stop_hook_active` が true (この hook の block で継続した turn の応答は再び block しない)
- `last_assistant_message` が無い・空文字列・文字列でない
- 除去後の本文で `L < 40`、または `J / (J + L) >= 0.05`
- 目標言語が日本語でない、または未設定 (次節)
- jq が無い・入力 JSON を解析できない等で判定できない (fail-open)

## 目標言語の決め方

次の順に settings ファイルを探し、`language` キーを持つ最初のファイルの値を目標言語とします。

1. `<project>/.claude/settings.local.json`
2. `<project>/.claude/settings.json`
3. `~/.claude/settings.json`

`<project>` は、環境変数 `CLAUDE_PROJECT_DIR` が空でなければその値、空または未設定なら hook 入力の `cwd` です。セッションの作業ディレクトリがプロジェクトのサブディレクトリに移っても、プロジェクトのルートの settings を読みます。`CLAUDE_PROJECT_DIR` も `cwd` も無い場合は 1 と 2 を飛ばします。ファイルが存在しない・JSON として解析できない・`language` キーを持たない場合は、そのファイルを無いものとして次を探します。

値が次のいずれかなら日本語とみなします (英字の大文字小文字は区別しません)。

- `日本語`
- `ja`
- `ja-` で始まる値 (例: `ja-JP`)
- `japanese` (例: `Japanese`、`JAPANESE`)

値が上記以外、またはどのファイルにも `language` が無い場合は判定を行いません。

## 対象外

- **subagent のメッセージ**: SubagentStop には登録せず、MessageDisplay hook と PostToolBatch hook も入力に `agent_id` があれば何もしないため、subagent のメッセージは検知しません。subagent から受け取った英語の報告を受けて親 session が英語で応答した場合は、その親 session のメッセージが判定の対象になります

## 既知の制約

- 判定は文字種の比率だけで行います。英語のメッセージでも、ひらがな・カタカナ・漢字が 5% 以上含まれていれば検知しません
- `language` は Claude Code 本体と同じ settings の優先順位で解決しますが、managed settings・コマンドライン引数・`CLAUDE_CONFIG_DIR` による設定は参照しません
- ユーザが英語での出力を明示的に求めている間も、tool 呼び出しの合間の英語のメッセージごとに書き直しの指示が注入されます。指示には「英語での出力を明示的に求められていた場合は書き直さずに作業を続ける」旨を含めていますが、指示そのものは止まりません
- MessageDisplay hook は、メッセージが表示されるたび (対話モードでは確定した行の batch ごと) に起動します。英語の判定と目標言語の解決はメッセージの最後の batch でだけ行いますが、hook の起動そのものは batch ごとに発生します
- 書き直しの指示は tool 呼び出しの batch が終わった時点で出るため、英語のメッセージの後に tool 呼び出しが無く turn が終わった場合は、Stop hook が turn 末尾の応答として判定します

## ディレクトリ構成

```
enforce-japanese-response/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       ├── enforce-japanese-response.sh   # Stop hook
│       ├── record-english-message.sh      # MessageDisplay hook
│       ├── request-japanese-rewrite.sh    # PostToolBatch hook
│       └── lib/
│           ├── language-judgement.sh      # 目標言語の解決と英語の判定
│           └── pending-state.sh           # 状態ディレクトリーの pending とバッファーの操作
└── README.md
```

## 必要な実行環境

- `bash`
- `jq` (無い場合は判定を行わず、何も出力しません)

## 関連情報

- [Claude Code Hooks ドキュメント](https://code.claude.com/docs/en/hooks)
