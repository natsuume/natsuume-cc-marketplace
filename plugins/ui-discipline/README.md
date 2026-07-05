# ui-discipline

UI (フロントエンド) 実装時の規律を配送するプラグイン。UI を持つプロジェクトでのみ enable して使う。

(Phase B で本文を執筆)

## 構成 (設計記述、issue #197)

- `hooks/hooks.json` — SessionStart に inject-ui-rules.sh を 1 entry 登録
- `hooks/scripts/inject-ui-rules.sh` — ui-rules.md 全文を additionalContext として注入 (I/O 契約はスクリプトヘッダを参照)
- `hooks/prompts/ui-rules.md` — 常時注入する 9 ルール (rule ID 一覧はファイルヘッダを参照)
- `skills/ui-patterns/SKILL.md` — 9 ルール対応のコード例・チェックリストを提供する skill
