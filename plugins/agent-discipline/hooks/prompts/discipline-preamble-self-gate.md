<!--
  agent-discipline: 分業規律の自己ゲート前置き (モデル判定不能時)
  #193 で fable-discipline/hooks/prompts/preamble-self-gate.md から本文無変更で移設。
  文面の Sonnet 版対応 (非 Fable セッションも分業規律を読む前提への更新) は #194 のスコープ。
-->

(自己ゲート) このセッションのモデルを hook からは判定できなかった。あなた (メインセッション) のモデルが Fable の場合のみ、以下 (本注入メッセージの全文、セクション 1〜4) を適用すること。Fable でない場合は全文を無視してよい。自分のモデルは自身の system prompt のモデル情報 (例: Environment セクション) で確認できる。
