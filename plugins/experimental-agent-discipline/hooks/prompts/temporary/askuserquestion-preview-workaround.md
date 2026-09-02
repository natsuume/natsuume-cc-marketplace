<!--
  agent-discipline: 暫定ルール — AskUserQuestion preview 不使用
  AskUserQuestion の preview 表示がスクロールできず、一定行数以上が「hidden XX lines」で
  隠される Claude Code 側の問題への暫定対応。
  撤去条件: Claude Code 側で preview のスクロール問題が修正されたら本ファイルを削除する
  (inject-temporary.sh は temporary/ 配下に md が無ければ何も注入しない)。
  撤去時も plugin version bump は必要。
-->

# agent-discipline: 暫定ルール — AskUserQuestion の preview 不使用

以下は Claude Code 本体の問題が修正されるまでの暫定ルールであり、恒久規律 (常時適用ルール・分業規律) とは別に配送されている。修正され次第このルールは撤去される。

**なぜ**: AskUserQuestion の preview 表示は現在スクロールできず、一定行数を超えた内容が「hidden XX lines」で隠されてユーザが全文を確認できない。隠れた部分を含む preview を根拠に選択を求めると、ユーザは見えていない情報で判断させられる。

**指示**: AskUserQuestion では選択肢の `preview` フィールドを使わない (行数に依らず全面的に使わない)。選択肢の比較に必要な内容 (コード案・mockup・設定例・diff 等) は、AskUserQuestion を発行する前のテキスト応答で説明し、そのうえで preview 無しの AskUserQuestion で質問する。

**境界**: 例外なし。preview で見せたい内容が長大でテキスト応答でも読みにくい場合も、preview に載せるのではなく応答本文の code block や表で提示する。
