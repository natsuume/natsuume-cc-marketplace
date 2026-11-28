---
name: status
description: Antigravity CLI (agy) の利用枠 (モデルのグループごとの週次枠・5 時間枠の残り割合と reset 時刻) を取得する。「Antigravity・agy の利用枠・残量・使用率・rate limit」の確認や、agy への委任前の使用率ガード判定で使う
---

# /antigravity-rate-limit:status — Antigravity (agy) の利用枠の取得

`scripts/antigravity-rate-limit.sh` を実行し、出力された JSON をユーザに報告する。script は `agy -p "/usage" --output-format json --print-timeout 60s` を 1 回実行し、応答を検証・正規化する。agy 同梱の changelog によると、print mode の `/usage` は agent turn を始めず、quota を消費しない (agy 1.1.11 以上)。

この `SKILL.md` を含む `skills/status/` の 2 階層上を `<plugin-root>` として解決する。通常の Skill 実行では hook 用の `${CLAUDE_PLUGIN_ROOT}` が設定される保証はないため、実パスを優先する。

## 1. 実行

Bash ツールで以下を **foreground で 1 回**実行する。失敗しても自動で再実行しない (agy は実行のたびにログの作成・OAuth token の refresh・auto-update の起動を行うため)。script は agy に `--print-timeout 60s` を渡すが、agy の版や接続によってはこの上限が `/usage` の応答待ちに効かないことがあるため、Bash の `timeout` に 120000 を指定する。Bash が timeout した、または実行を background へ移した場合は、agy が応答していないものとして失敗を報告し、再実行しない。

```bash
bash "<plugin-root>/scripts/antigravity-rate-limit.sh"
```

呼び出し側で閾値判定が必要な場合 (委任可否のガード等) は `--max-used-percent <N>` (N は 0〜100 の整数) を付ける。

## 2. 出力 JSON の読み方

exit 0 と exit 2 のとき、stdout に次の JSON が返る。枠はモデル単位ではなくグループ単位で、同じグループのモデルが週次枠と 5 時間枠を共有する。

| フィールド | 意味 |
|---|---|
| `description` | 枠の仕組みの説明 (agy の原文) |
| `groups[].name` | グループ名 |
| `groups[].description` | グループに属するモデルの説明 (agy の原文) |
| `groups[].buckets[].id` | 枠の識別子 |
| `groups[].buckets[].name` | 枠の表示名 |
| `groups[].buckets[].window` | 枠の窓。実測値は `weekly` (週次) と `5h` (5 時間) |
| `groups[].buckets[].remainingFraction` | 残り割合 (0〜1) |
| `groups[].buckets[].usedPercent` | 使用率 (%)。`(1 - remainingFraction) * 100` を小数第 1 位に丸めた値 |
| `groups[].buckets[].resetTime` | reset 時刻 (RFC3339、UTC)。未使用の枠では `null` |
| `groups[].buckets[].description` | reset までの時間などの説明 (agy の原文)。使用済みの枠にだけ付き、無ければ `null` |

`description` と `name` は、agy の値が文字列でなければ (欠損を含む) `null` になる。`groups[].buckets` は空配列のことがある。

## 3. ユーザへの報告

グループごと・窓ごとに次を報告する。

- 残り割合: `remainingFraction` を % で表示する (例: `0.988` → 98.8%)。使用率 `usedPercent` も併記する
- reset 時刻: `resetTime` (UTC) と、ローカル時刻に直した値を併記する。ローカルのタイムゾーンのオフセットは `date +%z` で確認する。`resetTime` が `null` の枠は「未使用」と報告する
- グループに属するモデル: `groups[].description` の内容を伝える
- `buckets` が空配列のグループは「枠の情報なし」と報告する

`--max-used-percent <N>` を付けた場合は判定結果も報告する。exit 0 なら全枠の `usedPercent` が N 以下であること、exit 2 なら N を超えた枠 (グループ名・窓・`usedPercent`) を明示する。

## 4. exit code 契約

| exit | 意味 | stdout |
|---|---|---|
| 0 | 取得成功 (`--max-used-percent` の判定 OK を含む) | JSON あり |
| 1 | 取得失敗・引数不正 | なし |
| 2 | `--max-used-percent` 指定時のみ: いずれかの枠の `usedPercent` が N を超えた | JSON あり |

呼び出し側は 1 (取得失敗 → fail-closed の判断) と 2 (超過) を区別できる。agy 自身が終了コード 2 で終わった場合も、script は取得失敗として exit 1 を返す。

## 5. 失敗時 (exit 1)

stderr に失敗理由が日本語で出る。その内容をそのままユーザに報告し、次の案内を添える。

| stderr の内容 | 原因と案内 |
|---|---|
| agy が見つからない | Antigravity CLI (agy) が未インストール、または PATH に無い。agy のインストールを案内する |
| jq が見つからない | jq が未インストール。jq のインストールを案内する |
| agy が非 0 で終了した (agy の stderr に認証・ログインの旨) | agy に未ログイン、または認証が切れている。ターミナルで `agy` を起動してログインするよう案内する |
| agy が終了コード 2 で終了した | `GEMINI_API_KEY` で接続している可能性がある (agy 同梱の changelog の記載。未実測)。この接続では利用枠を取得できないため、`GEMINI_API_KEY` を設定しない状態で `agy` を起動してログインするよう案内する |
| `num_turns` が 0 でない | agy が 1.1.11 より古く、`/usage` が prompt として送られた可能性がある。quota を消費したおそれがある旨を伝え、agy の更新を案内する |
| 応答の形式が想定と異なる (`status` / `command.name` / `groups` / bucket) | agy の更新で出力の形式が変わった可能性がある。stderr の内容を報告する |
| 引数が不正 (usage を表示) | `--max-used-percent` を 1 回だけ、0〜100 の整数の値で指定して実行し直す |
