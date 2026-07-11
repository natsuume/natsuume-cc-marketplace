# issue #228 Phase A: session-handoff plugin の設計契約

本ファイルは TDD 2 段階の Phase A (設計記述 commit) であり、Phase B (実装本体) のマージ前に削除する。
仕様の一次ソースは issue #228 body。本ファイルはそれを実装構造 (ファイル・関数・判定順序) に落とす。

## ファイル構成

| ファイル | 内容 |
|---|---|
| `.claude-plugin/plugin.json` | name=session-handoff, version=0.1.0 |
| `hooks/hooks.json` | PostToolUse (matcher `*`) → detect-context-threshold.sh、SessionStart (matcher `clear\|startup`) → inject-pending-handoff.sh |
| `hooks/scripts/detect-context-threshold.sh` | 閾値検知 + handoff 作成指示の注入 |
| `hooks/scripts/inject-pending-handoff.sh` | /clear・startup 後の新セッションへの handoff 自動注入 |
| `hooks/prompts/handoff-instruction.md` | 検知時に注入する指示文 (6 項目テンプレート込み、`__HANDOFF_PATH__` プレースホルダ) |
| `hooks/prompts/inject-preamble.md` | 注入時に handoff 本文の前に置く前置き文 |
| `skills/setup/SKILL.md` | `/session-handoff:setup` — cache producer の構成 |
| `scripts/statusline-cache-wrapper.sh` | 既存 statusline をラップして cache dump を追加する wrapper |
| `scripts/cache-only-statusline.sh` | statusline 未設定環境向けの dump 専用 statusline (表示は空) |
| `README.md` | 機能・producer 依存・契約・スコープ外の説明 |
| `../../.claude-plugin/marketplace.json` | plugin エントリ追加 (0.1.0) |
| `../../README.md` | plugin 一覧テーブルに行追加 |

## 共通契約

- 全 hook スクリプトは bash。fail-open: jq 不在・入力 JSON 異常・IO 失敗・uid 取得不能はすべて無音 exit 0
- additionalContext 出力: `{"hookSpecificOutput": {"hookEventName": "<入力の hook_event_name>", "additionalContext": "<本文>"}}`
- Linux (WSL2) / macOS 両対応。stat が必要な箇所は `stat -c ... || stat -f ...` の 2 段 fallback (natsuume-statusline/lib.sh と同方式)
- `sanitized_session_id` = `tr -cd 'A-Za-z0-9._-'` (空になったら無音終了)
- 読み取る cache の契約 (producer 側 #227、実装済み): `${TMPDIR:-/tmp}/natsuume-context-cache-<uid>/<sanitized_session_id>.json`、必須フィールドは `used_percentage` のみ
- plugin 内部の状態パス: `${TMPDIR:-/tmp}/session-handoff-<uid>/markers/<sanitized_session_id>.notified` (`<uid>` = `id -u`)

## detect-context-threshold.sh (PostToolUse, matcher `*`)

stdin: hook input JSON (`session_id` / `agent_id` / `cwd` / `hook_event_name` を使用)。

判定順序 (すべて該当しない場合のみ次へ。途中失敗は無音 exit 0):

1. jq 不在 → exit
2. `agent_id` が非空 (subagent 内) → exit
3. `session_id` 欠落 / サニタイズ後空 → exit
4. uid 取得不能 → exit
5. marker 存在 → exit (1 セッション 1 回)
6. `git -C "$cwd" rev-parse --git-dir` 解決不能 → exit (非 git)
7. cache 読み取り: 不在 / JSON 破損 / `used_percentage` 非数値 (`^[0-9]+(\.[0-9]+)?$`) → exit
8. 閾値: `SESSION_HANDOFF_THRESHOLD` を検証 (`^[0-9]+$` かつ 1〜99)。不正・未設定は 60
9. float 比較は awk (`used_percentage >= threshold` が偽 → exit)
10. marker を **先に** touch (ディレクトリ作成 + touch。失敗したら注入せず exit — 再発火ループ防止優先)
11. handoff 保存パスを組み立て: `<git-dir 絶対パス>/session-handoff/pending-<sanitized_session_id>-<epoch 秒>.md`
12. `hooks/prompts/handoff-instruction.md` を読み、`__HANDOFF_PATH__` を保存パスに置換 (bash の `${var//}`。sed 不使用) して additionalContext として出力

## inject-pending-handoff.sh (SessionStart, matcher `clear|startup`)

stdin: hook input JSON (`source` / `cwd` / `hook_event_name` を使用)。

1. jq 不在 → exit
2. `source` が `clear` / `startup` 以外 → exit (hooks.json の matcher と二重防御)
3. git dir 解決不能 → exit
4. `<git-dir>/session-handoff/` の `pending-*.md` を走査し、mtime (stat 2 段 fallback) を取得
   - mtime が 30 日超の `pending-*.md` / `consumed-*.md` は削除 (best-effort)
   - mtime が 24 時間以内の pending を対象とし、その中で mtime 最大の 1 件を選ぶ
5. 対象なし → 無音 exit
6. 選んだ 1 件を `inject-preamble.md` + handoff 全文 + (他に 24h 以内 pending があればそのパス列挙) の順で連結し additionalContext として出力
7. 出力に成功したら選んだファイルを `pending-` → `consumed-` prefix へ mv (atomic rename。失敗しても注入自体は成立済みなので無音)

## prompts

`handoff-instruction.md` の要件 (注入プロンプトは必要な内容だけを過不足なく。設計背景は README へ):

- context 使用率が閾値を超えたことを伝える
- 現在の作業を切りの良い単位まで進めてから、`__HANDOFF_PATH__` に handoff ドキュメントを書くこと
- handoff は 6 項目: 背景・目的 / 完了済み作業 / 進行中の作業と現在の状態 / 残作業・次の一手 / 決定事項・制約 / 参照ファイルパス・関連 issue・PR
- 未コミットの変更があればその旨と所在を handoff に明記すること
- 書き終えたらパスと要点をユーザに報告し、/clear を案内すること (新セッションへ自動注入される旨も伝える)

`inject-preamble.md` の要件: これが前セッションの handoff であること、内容を踏まえて作業を継続すべきこと、を簡潔に伝える。

## setup skill (`/session-handoff:setup`)

分岐 (issue #228 受入基準どおり):

1. 現セッションの cache ファイル存在 → 構成済みと報告して終了
2. `~/.claude/settings.json` の statusLine.command が natsuume-statusline (0.6.0+) → 構成済み報告。cache 未生成なら plugin update 案内
3. 他 statusline 設定済み → wrapper でラップする変更を提示し AskUserQuestion で確認後、settings.json を書き換え (`"command": "<wrapper 絶対パス> '<元の command 文字列>'"`)
4. statusline 未設定 → AskUserQuestion で「natsuume-statusline を導入」or「cache-only-statusline.sh を登録」を選択させ実行
5. いずれの書き換えも実行前に現在の設定値を報告する

## scripts

`statusline-cache-wrapper.sh`:

- `$1` = 元の statusline command (シェル文字列)。stdin を一度読み、(a) cache dump、(b) `printf '%s' "$input" | bash -c "$1"` で元 statusline へ中継し exit code を伝播
- dump 部は natsuume-statusline の `statusline/context-cache-dump.sh` の**同梱コピー** (plugin 間でファイル参照できないため。契約・挙動は #227 実装と同一: received_at 採時、per-UID dir、symlink/非所有拒否、mkdir lock + bounded retry、monotonic guard、atomic write)。コピー元とバージョンを README とスクリプトヘッダに明記する

`cache-only-statusline.sh`: stdin を読んで dump のみ行い、表示は何も出力しない (dump 部は wrapper と共通の同梱コピーを source)

## 境界・異常系 (受入基準の実装対応)

| 状況 | 挙動 |
|---|---|
| subagent 内の PostToolUse (`agent_id` あり) | 検知しない |
| 非 git プロジェクト | 検知・注入とも無音で何もしない |
| cache 不在 / 破損 / used_percentage 欠落 | 検知しない (producer 未構成は setup skill で解消する導線) |
| `SESSION_HANDOFF_THRESHOLD` 不正値 | 60 に fallback |
| marker touch 失敗 | 注入しない |
| SessionStart source=resume / compact | 注入しない |
| 24h 超の pending | 注入しない (30 日超で削除) |
| 24h 以内の pending 複数 | 最新 1 件のみ注入、残りはパス列挙 |
| 注入後の rename 失敗 | 無音 (注入は成立) |
