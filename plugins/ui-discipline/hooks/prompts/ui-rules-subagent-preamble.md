<!--
  ui-discipline: subagent 向け前置き注記
  SubagentStart hook (inject-ui-rules-subagent.sh) が ui-rules.md の直前に連結して subagent に注入する
  (UI を実装しない agent は除外する。除外リストは inject-ui-rules-subagent.sh に定義する)。
  本体ルールは SessionStart と単一ソース (ui-rules.md) を共有し、subagent 向けの差分は本注記のみに閉じる
  (2 ファイル間の rule 同期・drift を構造的に排除するため)。
  本文中のプレースホルダ (波括弧 2 重の UI_PATTERNS_SKILL_PATH) は injector が ui-patterns skill の
  SKILL.md 絶対パスへ置換する (subagent の実行環境では ${CLAUDE_PLUGIN_ROOT} が空になりうるため、
  注入時に解決済みパスを埋め込む)。
  このヘッダコメントは保守者向けのメモで、注入時に除かれる
  (hooks/scripts/lib/prompt-body.sh の read_ui_discipline_prompt)。
-->

# ui-discipline: subagent 向け注記

以下の UI 実装規律はメインセッションに配送されるものと同一であり、このタスクが UI (フロントエンド) の実装・変更を含む場合に適用される。subagent には AskUserQuestion が無いため、次の 2 点のみ読み替える:

- rule:visual-direction (ui-patterns skill が提供する) の「3〜4 案の視覚方向を提案してユーザの選択を得る」: 委任指示が視覚方向を指定している場合はそれに従う。指定が無いままオープンエンドな視覚デザインが必要になった場合は、実装に進まず、視覚方向の候補 (3〜4 案、配色・タイポグラフィ・トーンを各 1 行) と判断材料を最終報告に含めて親セッションへエスカレーションする
- 「ui-patterns skill を参照」: skill を呼び出せない場合は `{{UI_PATTERNS_SKILL_PATH}}` を Read で参照する
