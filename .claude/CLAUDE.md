全てのshファイルは、Linux環境（WSL2環境）およびmacOS環境で動作するように実装しなければならない

プラグイン (`plugins/<plugin>/` 配下) に変更を加えた場合は、必ず version (semver) を bump すること。`plugin.json` を直接編集していなくても、配下の hooks / commands / agents / skills / scripts / lib 等の動作に影響しうる変更はすべて bump 対象に含む。bump を忘れると Claude Code がプラグインキャッシュを無効化せず、利用者が `plugins update` しても古い実装が使われ続ける。

bump 対象は以下の各所をすべて同期する:

1. `plugins/<plugin>/.claude-plugin/plugin.json` の `version`
2. `.claude-plugin/marketplace.json` の対応する `plugins[].version`
3. リポジトリ直下 `README.md` の plugin 一覧テーブルに version 記載があればそれも同期 (informational だが、 不一致は利用者の混乱を招く)

`plugin.json` と `marketplace.json` がドリフトすると plugin 単独 install と marketplace 経由 install で異なる version が解決されるため、最低でもこの 2 箇所は必ず一致させる。bump 幅は semver に従う:
- bug fix → patch (例: `0.5.0` → `0.5.1`)
- 後方互換のある機能追加 → minor (例: `0.5.0` → `0.6.0`)
- 互換破壊 → major (例: `0.5.0` → `1.0.0`)

issue を起票する際は、優先度ラベル (P1 / P2 / P3) を必ず付与すること。判断基準は各ラベルの description に従う:
- P1: 優先度: 高 (正規操作を壊す・広範な影響・自走の停止/安全性の毀損)
- P2: 優先度: 中 (実害あり・発生条件が限定的)
- P3: 優先度: 低 (文書整備・軽微な改善)
