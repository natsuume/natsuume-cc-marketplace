全てのshファイルは、Linux環境（WSL2環境）およびmacOS環境で動作するように実装しなければならない

plugin version (semver) は `plugins/<plugin>/` 配下の hooks / commands / agents / skills / scripts / lib 等を変更した場合に必ず bump すること。bump を忘れるとプラグインキャッシュが無効化されず、利用者が update しても古い実装が使われ続ける。`plugins/<plugin>/README.md` は release documentation のため、内容変更時も version を bump する。

version の正本は以下の 1 と 2 であり、常に一致させる。3 と 4 も表示箇所として同期する:

1. `plugins/<plugin>/.claude-plugin/plugin.json` の `version`
2. `.claude-plugin/marketplace.json` の対応する `plugins[].version`
3. リポジトリ直下 `README.md` の plugin 一覧テーブルのバージョン
4. `plugins/<plugin>/README.md` の `## バージョン` 直下にある `vX.Y.Z`

bump 幅は semver に従う:
- bug fix → patch (例: `0.5.0` → `0.5.1`)
- 後方互換のある機能追加 → minor (例: `0.5.0` → `0.6.0`)
- 互換破壊 → major (例: `0.5.0` → `1.0.0`)

version 整合 (変更 plugin の bump 漏れ・4 箇所の表示不一致・marketplace と plugins/ ディレクトリの集合不一致) は `python3 scripts/check_plugin_versions.py <base_revision>` が CI (PR / master push) で検査する。

issue を起票する際は、優先度ラベル (P1 / P2 / P3) を必ず付与すること。判断基準は各ラベルの description に従う:
- P1: 優先度: 高 (正規操作を壊す・広範な影響・自走の停止/安全性の毀損)
- P2: 優先度: 中 (実害あり・発生条件が限定的)
- P3: 優先度: 低 (文書整備・軽微な改善)
