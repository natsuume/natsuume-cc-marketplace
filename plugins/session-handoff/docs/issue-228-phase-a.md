# issue #228 Phase A: session-handoff plugin の設計契約

本ファイルは TDD 2 段階の Phase A (設計記述 commit) であり、Phase B (実装本体) のマージ前に削除する。
仕様の一次ソースは issue #228 body。本ファイルはそれを実装構造 (ファイル・関数・判定順序) に落とす。
codex review の 5 指摘 (安定 launcher・lossless 保存・dir 先行作成・rename-first claim・state dir 防御)
への対応を rescue approve 済みの形で織り込んでいる。

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
| `scripts/setup-wrapper.sh` | setup skill から呼ぶ設置スクリプト (安定 launcher の生成・settings.json 書き換え) |
| `scripts/context-cache-dump.sh` | cache dump 関数 (natsuume-statusline v0.6.0 の同名ファイルの同梱コピー) |
| `README.md` | 機能・producer 依存・契約・at-most-once 動作・スコープ外の説明 |
| `../../.claude-plugin/marketplace.json` | plugin エントリ追加 (0.1.0) |
| `../../README.md` | plugin 一覧テーブルに行追加 |

## 共通契約

- 全 hook スクリプトは bash。fail-open: jq 不在・入力 JSON 異常・IO 失敗・uid 取得不能はすべて無音 exit 0
- additionalContext 出力: `{"hookSpecificOutput": {"hookEventName": "<入力の hook_event_name>", "additionalContext": "<本文>"}}`
- Linux (WSL2) / macOS 両対応。stat が必要な箇所は `stat -c ... || stat -f ...` の 2 段 fallback (natsuume-statusline/lib.sh と同方式)
- `sanitized_session_id` = `tr -cd 'A-Za-z0-9._-'` (空になったら無音終了)
- 読み取る cache の契約 (producer 側 #227、実装済み): `${TMPDIR:-/tmp}/natsuume-context-cache-<uid>/<sanitized_session_id>.json`、必須フィールドは `used_percentage` のみ
- plugin 内部の状態パス: `${TMPDIR:-/tmp}/session-handoff-<uid>/markers/<sanitized_session_id>.notified` (`<uid>` = `id -u`)。
  marker は**ディレクトリ**であり、作成は `mkdir` による atomic claim で行う (並列 PostToolUse が同時に
  marker 不在を観測しても、`mkdir` の成功者 1 プロセスだけが通知を発行できる。touch はファイル既存でも
  成功するため排他にならない)
- 状態ルート `${TMPDIR:-/tmp}/session-handoff-<uid>/` の作成時は #227 producer と同一の防御を適用する:
  subshell 内 `umask 077` → `mkdir -p` → symlink 拒否 (`-L`) → 所有確認 (`-O`) → `chmod 700`、失敗はすべて無音 skip

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
10. handoff ディレクトリを準備: `<git-dir 絶対パス>/session-handoff/` を `mkdir -p` し、同ディレクトリに一時ファイルの作成→削除 probe で書き込み可能性を実確認する (`[ -w ]` では NFS 等で偽陽性になるため)。失敗 → marker を作らず exit (次のツール実行で再検知される)
11. 保存パスを組み立て (`pending-<sanitized_session_id>-<epoch 秒>.md`)、`handoff-instruction.md` を読んで `__HANDOFF_PATH__` を置換し、出力 JSON の組み立てまで成功させる。置換は bash 5.2 の
    `patsub_replacement` (置換文字列中の `&` 展開) を避けるため `${var//}` を使わず、プレースホルダが
    **ちょうど 1 個**存在することを先に検証したうえで `prefix=${template%%__HANDOFF_PATH__*}` /
    `suffix=${template#*__HANDOFF_PATH__}` に分割して `"$prefix$path$suffix"` と連結する。
    プレースホルダが不在・複数の場合 (テンプレート破損) は marker を作らず無音終了する
12. すべて準備できてから marker を `mkdir` で atomic claim し (状態ルートは共通契約の防御手順で作成。
    mkdir 失敗 = 並列 hook が先行 = 無音 exit)、additionalContext を出力する

marker の意味は「通知を発行済み」であり「handoff が保存済み」ではない (Claude の Write 失敗までは再通知しない。
この割り切りは README に記載する)。

## inject-pending-handoff.sh (SessionStart, matcher `clear|startup`)

stdin: hook input JSON (`source` / `cwd` / `hook_event_name` を使用)。

1. jq 不在 → exit
2. `source` が `clear` / `startup` 以外 → exit (hooks.json の matcher と二重防御)
3. git dir 解決不能 → exit
4. `<git-dir>/session-handoff/` の `pending-*.md` を走査し、mtime (stat 2 段 fallback) を取得
   - mtime が 30 日超の `pending-*.md` / `consumed-*.md` は削除 (best-effort)
   - mtime が 24 時間以内の pending を新しい順に候補とする
5. 候補なし → 無音 exit
6. **rename-first claim**: 最新候補を `pending-` → `consumed-` prefix へ同一ディレクトリ内 `mv` する。
   mv 失敗 (= 並行セッションが先に claim) → 次の候補で再試行、候補が尽きたら無音 exit。
   mv の atomic 性により同じ handoff を 2 セッションが注入することは起きない (**at-most-once**:
   claim 後・出力前にプロセスが死ぬと注入は失われる。この割り切りは README に記載する)
7. claim した consumed ファイルを読み、`inject-preamble.md` + handoff 全文 + (他に 24h 以内 pending があればそのパス列挙) を連結して additionalContext として出力する
8. 読み取り・JSON 組み立てに失敗した場合は、出力前に限り best-effort で `consumed-` → `pending-` に戻してから無音 exit する

## prompts

`handoff-instruction.md` の要件 (注入プロンプトは必要な内容だけを過不足なく。設計背景は README へ):

- context 使用率が閾値を超えたことを伝える
- 現在の作業を切りの良い単位まで進めてから、`__HANDOFF_PATH__` に handoff ドキュメントを書くこと
- handoff は 6 項目: 背景・目的 / 完了済み作業 / 進行中の作業と現在の状態 / 残作業・次の一手 / 決定事項・制約 / 参照ファイルパス・関連 issue・PR
- 未コミットの変更があればその旨と所在を handoff に明記すること
- 書き終えたらパスと要点をユーザに報告し、/clear を案内すること (新セッションへ自動注入される旨も伝える)

`inject-preamble.md` の要件: これが前セッションの handoff であること、内容を踏まえて作業を継続すべきこと、を簡潔に伝える。

## setup skill (`/session-handoff:setup`) と launcher

分岐 (issue #228 受入基準どおり):

1. 現セッションの cache ファイル存在 → 構成済みと報告して終了
2. `~/.claude/settings.json` の statusLine.command が natsuume-statusline (0.6.0+) → 構成済み報告。cache 未生成なら plugin update 案内
3. statusLine.command が**自 launcher** (`session-handoff-statusline-launcher.sh`) を指す → 構成済みとして扱う (「他の statusline」と誤分類して再ラップしない — 自己再帰と元 command 喪失の防止)。launcher の再生成が必要な場合は、既存 launcher 内の固定形式の代入行 (例: `WRAPPED_COMMAND_B64='...'`) から base64 inner command を一意に抽出して引き継ぐ。抽出・検証に失敗した場合は launcher と settings.json のいずれも変更せず setup を終了する
4. 他 statusline 設定済み → wrapper 化の変更を提示し AskUserQuestion で確認後、設置を実行
5. statusline 未設定 → AskUserQuestion で「natsuume-statusline を導入」or「cache-only launcher を登録」を選択させ実行
6. いずれの書き換えも実行前に現在の設定値を報告する

設置の設計 (codex review P1 2 件への対応):

- settings.json に plugin cache の version 固有パスを焼き込まない。`~/.claude/session-handoff-statusline-launcher.sh`
  という**安定 launcher** を生成して登録する (natsuume-statusline / rate-limit の setup.sh と同じ間接化)
- launcher は実行時に plugin cache から session-handoff の最新版 `scripts/context-cache-dump.sh` を解決して dump を行う。
  解決できない場合は dump をスキップする (fail-open)
- 元の statusline command は launcher 生成時に **base64 エンコードして launcher 内に埋め込む** (rate-limit の
  launcher と同じ lossless 表現。single quote・改行・空白を含む command でも再クォート問題が起きない)。
  launcher は decode した文字列を `bash -c` に渡して stdin を中継し、exit code を伝播する
- 元 command が無い場合 (cache-only 登録) は dump のみ行い、表示は何も出力しない
- 設置順序: launcher を atomic write (mktemp + mv、実行権付与) で設置 → 設置成功を確認してから settings.json を
  書き換える。launcher の dump 部が壊れても元 statusline への委譲は維持される構造にする (dump 失敗で表示を殺さない)

## 境界・異常系 (受入基準の実装対応)

| 状況 | 挙動 |
|---|---|
| subagent 内の PostToolUse (`agent_id` あり) | 検知しない |
| 非 git プロジェクト | 検知・注入とも無音で何もしない |
| cache 不在 / 破損 / used_percentage 欠落 | 検知しない (producer 未構成は setup skill で解消する導線) |
| `SESSION_HANDOFF_THRESHOLD` 不正値 | 60 に fallback |
| handoff ディレクトリを作成・書き込みできない | marker を作らず終了 (次ツール実行で再検知) |
| handoff-instruction.md のプレースホルダが不在・複数 | marker を作らず通知しない (テンプレート破損) |
| marker の mkdir claim 失敗 (並列 hook の先行) | 通知しない (1 セッション 1 回を排他的に保証) |
| setup 再実行時に statusLine.command が自 launcher | 再ラップしない。inner command の抽出・検証失敗時は launcher / settings.json とも変更しない |
| SessionStart source=resume / compact | 注入しない |
| 24h 超の pending | 注入しない (30 日超で削除) |
| 24h 以内の pending 複数 | rename-first claim の勝者が最新 1 件を注入、残りはパス列挙 |
| 並行セッションの同時起動 | mv の atomic 性で二重注入なし (at-most-once) |
| claim 後・出力前の失敗 | best-effort で pending に戻す。戻せなければその handoff は消費済み扱い |
| 状態ルート / launcher 設置先の symlink・非所有 | 書き込まない (producer と同一防御) |
