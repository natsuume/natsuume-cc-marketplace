# antigravity-rate-limit プラグイン

Antigravity CLI (`agy`) の利用枠 (モデルのグループごとの週次枠・5 時間枠の残り割合と reset 時刻) を、Claude がセッション内で取得できる `/antigravity-rate-limit:status` Skill を提供するプラグインです。`--max-used-percent <N>` を付けると、使用率が N% を超えた枠があるかを exit code で返すため、agy へ作業を委任する前のガード判定にも使えます。

本プラグインは Claude Code 専用で、Codex marketplace では配布していません。

## バージョン

v0.1.0

## インストール

```bash
claude plugin marketplace add natsuume/natsuume-cc-marketplace
claude plugin install antigravity-rate-limit@natsuume-plugins
```

## 前提

- `agy` 1.1.11 以上が PATH にあること。1.1.11 より古い agy では `/usage` が model への prompt として扱われ、quota を消費しえます
- `agy` にログイン済みであること。未ログインなら、ターミナルで `agy` を起動してログインします
- `jq`

## Skill の動作

`/antigravity-rate-limit:status` は `scripts/antigravity-rate-limit.sh` を foreground で 1 回実行し、結果をユーザに報告します。失敗しても自動で再実行しません。報告する内容は次のとおりです。

- グループごと・窓 (週次 / 5 時間) ごとの残り割合 (% 表示) と使用率
- reset 時刻 (UTC と、ローカル時刻に直した値)。未使用の枠は「未使用」と報告します
- グループに属するモデルの説明 (agy の原文)
- `--max-used-percent <N>` を付けた場合は判定の結果

失敗時 (exit 1) は stderr の理由を報告し、原因に応じて agy のインストール、`agy` を起動してのログイン、agy の更新などを案内します。

## script の I/O 契約

```bash
bash "<plugin-root>/scripts/antigravity-rate-limit.sh" [--max-used-percent <N>]
```

stdin は使いません。進捗とエラーは stderr に日本語で出します。

### 引数

`--max-used-percent <N>` だけを受け付けます。N は 10 進整数 (数字のみ・最大 3 桁) で 0〜100 の範囲です。空文字を含む範囲外の値・値の欠落・未知の引数は、usage を stderr に出して exit 1 になります。このとき agy は起動しません。

### stdout

取得に成功したとき (exit 0 / 2) だけ、次の形の JSON を 1 つ出します。exit 1 では何も出しません。

```json
{
  "description": "<枠の仕組みの説明>",
  "groups": [
    {
      "name": "<グループ名>",
      "description": "<グループに属するモデルの説明>",
      "buckets": [
        {
          "id": "gemini-weekly",
          "name": "Weekly Limit Remaining",
          "window": "weekly",
          "remainingFraction": 0.9880305528640747,
          "usedPercent": 1.2,
          "resetTime": "2026-12-04T09:00:59Z",
          "description": "... fully refresh in 6 days, 23 hours."
        }
      ]
    }
  ]
}
```

| フィールド | 値 |
|---|---|
| `remainingFraction` | agy の `remaining_fraction` (0〜1) |
| `usedPercent` | `(1 - remainingFraction) * 100` を小数第 1 位に丸めた数値 |
| `resetTime` | agy の `reset_time` (RFC3339、UTC)。未使用の枠を表す `1970-01-01T00:00:00Z` は `null` |
| `description` (bucket) | agy の原文。使用済みの枠にだけ付き、無ければ `null` |

`description` と `name` は、agy の値が文字列ならそのまま、それ以外なら `null` にします。グループと枠の順序は agy の応答のままです。

### exit code

| exit | 意味 | stdout |
|---|---|---|
| 0 | 取得成功 (`--max-used-percent` の判定 OK を含む) | JSON あり |
| 1 | 取得失敗・引数不正 | なし |
| 2 | `--max-used-percent` 指定時のみ: いずれかの枠の `usedPercent` が N を超えた | JSON あり |

判定は全グループの全枠を同じ N で比べ、丸めた後の `usedPercent` が N を超える枠が 1 つでもあれば exit 2 です。

exit 1 になる取得失敗は次のとおりです。

- agy または jq が PATH に無い (jq が無いときは agy を起動しません)
- agy が非 0 で終了した。agy の終了コード 2 も exit 1 として返します。stderr には agy の終了コードと、agy の stderr の先頭数行を出します
- agy の応答が次の条件を満たさない
  - JSON として解釈できる
  - `status` が `"SUCCESS"`
  - `num_turns` が 0。0 でなければ agent turn が実行された (古い agy で `/usage` が prompt として送られた) 可能性があり、quota を消費したおそれがある旨を stderr に出します
  - `command.name` が `"usage"`
  - `command.data.groups` が 1 件以上の配列
  - 各枠が文字列の `id`・`window` と、0〜1 の数値の `remaining_fraction` を持つ

## 取得方式

script は agy を次の引数で 1 回だけ起動します。

```bash
agy -p "/usage" --output-format json --print-timeout 60s
```

- print mode (`-p`) の `/usage` は agy 同梱の changelog に記載された機能で、agent turn を始めず、quota を消費せず、conversation を残しません (agy 1.1.11 以上)
- `--disable-slash-commands` を付けると `/usage` が通常の prompt として送られるため、付けません
- 出力の形式は agy の更新で変わりえます。応答が上記の条件を満たさなければ exit 1 になります
- 2026-11-27 に agy 1.5.18 / WSL2 で、上記の引数で利用枠の JSON が返ることを確認しています。このときグループは 2 つで、それぞれに週次枠 (`weekly`) と 5 時間枠 (`5h`) がありました

## agy 自身が行う副作用

script は agy を起動するだけですが、agy は実行のたびに次を行います (agy 自身の挙動で、本プラグインからは制御できません。2026-11-27 agy 1.5.18 / WSL2 で確認)。

- `~/.gemini/antigravity-cli/log/` にログを作る
- 期限切れの OAuth token を refresh する
- 前回の実行から 15 分以上空いていれば auto-update を起動する

## 既知の制約

- macOS では実測していません (script は bash 3.2 と BSD ツールで動くように書いています)
- 枠はモデル単位ではなくグループ単位です。特定のモデルの残量は、そのモデルが属するグループの枠として読みます
- `GEMINI_API_KEY` で接続している agy では取得できません。agy 同梱の changelog によると、この場合 agy は終了コード 2 で終わります (未実測)。script は exit 1 を返します
- 1.1.11 より古い agy では `/usage` が prompt として扱われ、quota を消費しえます。script は `num_turns` が 0 でない応答を exit 1 にしますが、消費そのものは防げません

## ファイル構成

| パス | 役割 |
|---|---|
| `.claude-plugin/plugin.json` | plugin manifest |
| `skills/status/SKILL.md` | `/antigravity-rate-limit:status` Skill。script を実行して結果を報告する手順 |
| `scripts/antigravity-rate-limit.sh` | agy の `/usage` の実行、応答の検証と正規化、閾値判定 |
| `README.md` | この文書 |

## キーワード

`antigravity` `agy` `rate-limit` `usage-limit` `quota` `skill`
