#!/usr/bin/env python3
"""GitHub issue/PR タイムラインから AI タスクのリードタイムを集計する CLI (issue #288)。

## 目的

`/repo-analytics:leadtime` skill が収集した issue/PR のタイムライン JSONL を読み、
「着手 (claim) → PR ready → merge」のリードタイムを、生存バイアス (打ち切り
censoring) とサイズ交絡 (PR の大きさ) を統制したうえで集計する。結果は 1 つの
JSON オブジェクトとして stdout にのみ出力し、SKILL.md はこの JSON をそのまま
可視化・レポートへ投影する (再計算・改変しない)。

このファイルは issue #288 Phase A (spec-first) の公開契約であり、公開関数の
シグネチャ・データ構造・docstring がその契約の本体である。各関数本体は
Phase B (issue #288 実装本体) で実装するため、現時点では `NotImplementedError`
を送出する。`SCHEMA_VERSION` / `SIZE_BANDS` / 各 dataclass / `ClaimPatternsError`
/ `parse_args` はデータ・CLI 引数の設計そのものであり、Phase A で実体を持つ。

## 完了根拠 (qualifying completion) の一般規則

主指標 (`mainSeries`) の完了根拠 (qualifying completion) と見なす PR は、
`state == "MERGED"` の PR、または現在 `state == "OPEN"` かつ
`isDraft == False` の PR に限る。merge されず close された PR
(closed-unmerged PR) は破棄された試行であり完了根拠にしない —
破棄試行をタスク完了と数えると、放棄されたタスクが速く完了したように
見えるバイアスを生むため。`--prs` の収集契約 (`states: [OPEN, MERGED]`、
`fetch-prs.graphql` 参照) はこの規則の実装であり、closed-unmerged PR は
そもそも収集対象そのものに含まれない。

## 入力 JSONL 契約 (詳細は SKILL.md セクション 4 「claim 判定パターン」および
issue #288 Phase A 契約ドキュメント セクション 4 が正本)

- `--issues`: 1 行 1 issue (OPEN + CLOSED 全件) の JSONL。各行は少なくとも
  `repo`, `number`, `title`, `state`, `stateReason`, `createdAt`, `closedAt`,
  `timelineItems.totalCount`, `timelineItems.nodes` を持つ。`nodes` は
  `__typename` が `LabeledEvent` / `UnlabeledEvent` / `IssueComment` /
  `ClosedEvent` / `ReopenedEvent` のいずれかのイベントの配列。
  `timelineItems.totalCount > len(nodes)` の行は timeline 取得が不完全であり、
  主系列・打ち切り系列から除外され `exclusions.timelineOverflow` に列挙される。
  `ClosedEvent.closer` が `PullRequest` (`__typename == "PullRequest"`) の
  場合、その closer は `merged` フィールド (bool) を必須で持つ
  (`resolve_close_linkage` の category 判定の一次情報。`prs_by_key` への
  存在有無だけでは判定しない)。
- `--prs`: 1 行 1 PR (OPEN + MERGED。merge 済みか否かに依らず収集する) の
  JSONL。各行は `repo`, `number`, `state` (`"OPEN" | "MERGED"`), `isDraft`
  (bool), `createdAt`, `mergedAt` (`state == "OPEN"` の行は `null` を許容
  する), `additions`, `deletions`, `timelineItems` (`ReadyForReviewEvent` /
  `ConvertToDraftEvent` の配列), `closingIssuesReferences.totalCount` /
  `closingIssuesReferences.nodes` を持つ。`closingIssuesReferences.totalCount
  > len(closingIssuesReferences.nodes)` の行が 1 件でも存在する場合、skill の
  収集手順 (`fetch-pr-closing-issues.graphql` による追加ページング) が
  `closingIssuesReferences` を完全化している前提が破れているとみなし、
  `main` は入力エラー (exit code `2`) として fail-closed に中断する
  (不完全な closing references は issue↔PR 結合を欠落させ、該当 issue を
  誤って `censored` に分類するため部分継続しない)。
- `--claim-patterns-file`: claim 判定パターン (SKILL.md 「claim 判定パターン
  (正本)」セクションの JSON block をそのまま書き出したファイル)。
  `inProgressLabel: str`, `strict: [{"id", "regex"}]`, `loose: [{"id", "regex"}]`
  を持つ。既定値はこのスクリプト側に一切持たない。
- `--as-of`: 集計基準時刻 (UTC, ISO8601, 例 `2026-07-17T04:00:00Z`)。打ち切り
  (censored) 系列の経過時間や出力の `asOf` に使う。
- `--since` (省略可): `YYYY-MM-DD`。`firstStartAt` (UTC 日付) がこの日付以上の
  タスクのみを主系列・打ち切り系列・週次集計に含める inclusive filter。
- `--boundaries-file` (省略可): `[{"id": str, "label": str, "at": ISO8601}]`。
  `intervalStats` の区間境界。

## 出力 JSON 契約 (stdout, 詳細は各関数 docstring および issue #288 Phase A
契約ドキュメント セクション 5 が正本)

トップレベルキー: `schemaVersion`, `asOf`, `since`, `sizeBands`, `repos`,
`mainSeries`, `censored`, `auxiliarySeries`, `prSeries`, `weeklyCohorts`,
`markerCoverage`, `claimDetection`, `exclusions`, `intervalStats`,
`dataQuality`。フィールドの意味は `compute()` の docstring を正本とする。

## exit code 契約

- `0`: 成功 (集計対象が 0 件の空データも含む)。
- `2`: 入力エラー (`--issues` / `--prs` のファイル不存在・JSONL parse 失敗・
  必須フィールド欠落・`--as-of` / `--since` の形式不正・`--prs` の行に
  `closingIssuesReferences.totalCount > len(closingIssuesReferences.nodes)`
  が 1 件でも存在する場合)。
- `3`: claim patterns file (`--claim-patterns-file`) の契約違反 (必須キー欠落・
  regex compile 失敗)。`ClaimPatternsError` を捕捉して変換する。

いずれの exit code でも部分データのまま黙って処理を続行しない (fail-closed)。
診断メッセージはすべて stderr に出力し、stdout には結果 JSON のみを書く。

## タイムゾーン

すべて UTC。入力の ISO8601 `Z` サフィックスは UTC を表す aware `datetime` に
parse する (naive datetime は扱わない)。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

SCHEMA_VERSION = 1
"""出力 JSON の `schemaVersion` 固定値。破壊的にキー定義を変更する場合のみ上げる。"""

SIZE_BANDS: list[tuple[str, int, int | None]] = [
    ("S", 0, 50),
    ("M", 51, 300),
    ("L", 301, 1000),
    ("XL", 1001, None),
]
"""PR サイズ帯の定義。各要素は (band, min, max)。

- 値は `additions + deletions` (行数) に対する帯。
- `min` / `max` は両端 inclusive (例: "S" は 0〜50 行、"M" は 51〜300 行)。
- `max is None` は上限なし ("XL" は 1001 行以上すべて)。
- 出力 JSON の `sizeBands` はこの定数をそのまま
  `[{"band": b, "min": lo, "max": hi}, ...]` へ投影したもの。
"""


class ClaimPatternsError(Exception):
    """claim patterns file (`--claim-patterns-file` が指す patterns.json) が
    公開契約 (SKILL.md 「claim 判定パターン (正本)」セクション) に違反する場合に
    `load_claim_patterns` が送出する例外。

    `main` はこの例外を捕捉し、診断メッセージを stderr に出力したうえで
    exit code `3` に変換する。
    """


@dataclass(frozen=True)
class ClaimPattern:
    """claim 判定パターン 1 件 (strict または loose のいずれかの要素)。

    Attributes:
        id: パターンの識別子 (patterns.json の `id`)。`StartResolution.strict_sources`
            の `patternId` や診断メッセージで使う。
        regex_template: patterns.json の `regex` そのまま。プレースホルダ
            `{issue_number}` を含みうる (行頭一致を要求する `^` を含む場合もある)。
            実際の issue 番号への置換 (`{issue_number}` → `re.escape(str(issue_number))`)
            と `re.MULTILINE` での compile は `resolve_start` が判定対象 issue ごとに行う。
    """

    id: str
    regex_template: str


@dataclass(frozen=True)
class ClaimPatterns:
    """`load_claim_patterns` が返す、検証済みの claim 判定パターン集合。

    Attributes:
        in_progress_label: 着手中を表す label 名 (patterns.json の
            `inProgressLabel`)。`LabeledEvent.label.name` との比較に使う。
        strict: 「着手の確定的な根拠」とみなす comment パターンの一覧。
            1 件以上一致した comment は `StartResolution.strict_sources` に
            `kind="comment"` として計上される。
        loose: strict には一致しないが「取りこぼし候補」として記録すべき
            ゆるいパターンの一覧。loose のみ一致した comment は
            `StartResolution.loose_only_matches` に計上される。
    """

    in_progress_label: str
    strict: list[ClaimPattern]
    loose: list[ClaimPattern]


@dataclass(frozen=True)
class StartResolution:
    """1 件の issue に対する着手時刻の解決結果 (`resolve_start` の戻り値)。

    Attributes:
        first_start: 着手候補のうち最も早い createdAt。候補が 1 件も無ければ
            `None` (= 着手マーカーなし issue)。
        last_start: 着手候補のうち最も遅い createdAt。候補が無ければ `None`。
            例えば labeled → unlabeled → labeled のように複数回着手した issue では
            `first_start` と `last_start` が異なりうる。
        strict_sources: 着手候補として採用された各イベントを
            `{"kind": "comment" | "label", "at": datetime, "patternId": str | None}`
            で列挙したリスト (candidates の内訳)。
            - `kind == "comment"`: strict パターンに一致した `IssueComment`。
              `patternId` は一致した `ClaimPattern.id`。
            - `kind == "label"`: `inProgressLabel` が付与された `LabeledEvent`。
              `patternId` は常に `None` (strict パターン一覧に基づく判定ではないため)。
        loose_only_matches: loose のいずれかに一致し、かつどの strict にも
            一致しなかった `IssueComment` を `{"commentCreatedAt": datetime}` で
            列挙したリスト (「取りこぼし候補」)。着手候補には含めない。
    """

    first_start: datetime | None
    last_start: datetime | None
    strict_sources: list[dict]
    loose_only_matches: list[dict]


@dataclass(frozen=True)
class ReadyResolution:
    """1 件の PR に対する ready 化時刻の解決結果 (`resolve_ready` の戻り値)。

    Attributes:
        ready_at: PR の `state` / `isDraft` に応じて次のように解決する。

            - `state == "MERGED"`: 最後 (createdAt が最大) の
              `ReadyForReviewEvent.createdAt`。`ReadyForReviewEvent` が
              1 件も無ければ `pr["createdAt"]` にフォールバックする
              (従来どおり)。
            - `state == "OPEN"` かつ `isDraft == False`: 最後の
              `ReadyForReviewEvent.createdAt`、無ければ `pr["createdAt"]`
              (作成時から ready であった扱い)。
            - `state == "OPEN"` かつ `isDraft == True`: `None`
              (ready 未到達。過去に `ReadyForReviewEvent` があっても現在
              draft に戻っているため、完了とは見なさない)。
        redraft_count: `ConvertToDraftEvent` の件数 (= draft に戻された回数)。
            例えば draft で作成 → ready → draft → ready は 1、**ready で作成 →
            draft 化 → ready 化も 1** (`ConvertToDraftEvent` が 1 件のため)。
            イベントが無ければ 0。`state` / `isDraft` に関わらず同じ規則で
            数える。
        via_draft: `ReadyForReviewEvent` が 1 件以上存在すれば `True`
            (= 実際に draft から ready 化した実績がある)。存在しなければ
            `False`。この定義は `state` / `isDraft` によらず変わらない
            (`ready_at` が `None` になる場合でも `via_draft` は独立に判定する)。
    """

    ready_at: datetime | None
    redraft_count: int
    via_draft: bool


@dataclass(frozen=True)
class CloseLinkage:
    """1 件の issue に対する最終クローズ状態の分類結果 (`resolve_close_linkage` の戻り値)。

    Attributes:
        category: 次のいずれか。closer が `PullRequest` の場合の分類は
            closer 自身の `merged` フィールド (bool、必須。モジュール docstring
            「入力 JSONL 契約」参照) を一次情報とする —
            `prs_by_key` への存在有無だけでは判定しない。

            - `"open"`: issue が CLOSED 状態でない (まだ開いている)。
            - `"not_planned"`: `issue["stateReason"] == "NOT_PLANNED"`
              (最終 ClosedEvent の closer 種別より優先して判定する)。
            - `"merged_pr"`: 最終 `ClosedEvent.closer` が `PullRequest` であり、
              `closer["merged"] == True` かつその PR が `prs_by_key`
              (= 収集対象リポジトリの PR 集合) に存在する。
            - `"merged_pr_external"`: 最終 `ClosedEvent.closer` が
              `PullRequest` であり `closer["merged"] == True` だが、その PR が
              `prs_by_key` に存在しない (収集範囲外リポジトリの PR 等、
              closer 情報以外に手掛かりが無い)。ready 到達時刻が得られないため
              `mainSeries` には含めない (`auxiliarySeries.externalMergedClose`
              側で分離集計する。`compute` の docstring 参照)。
            - `"unmerged_pr"`: 最終 `ClosedEvent.closer` が `PullRequest` であり
              `closer["merged"] == False` (真の破棄試行)。`prs_by_key` への
              存在有無は問わない。
            - `"commit"`: 最終 `ClosedEvent.closer` が `Commit`。
            - `"manual"`: 最終 `ClosedEvent.closer` が無い (null)、または
              CLOSED 状態なのに `ClosedEvent` が timeline 中に見つからない
              (timeline 不完全時のフォールバック)。
        linked_pr: `category == "merged_pr"` のときのみ
            `{"repo": str, "number": int}` を設定する。それ以外の category では
            `None` (`merged_pr_external` / `unmerged_pr` であっても closer の
            PR 番号は保持しない — 必要な場合は `resolve_close_linkage` の
            呼び出し元が `issue` の timeline から別途参照する)。

    境界:
        「最終 ClosedEvent」は `timelineItems.nodes` を createdAt 昇順に見て、
        `ReopenedEvent` を挟んで複数回 CLOSED になっている場合は最後に出現した
        `ClosedEvent` を採用する (close → reopen → close なら 2 番目の
        `ClosedEvent`)。
    """

    category: str
    linked_pr: dict | None


def load_claim_patterns(path: Path) -> ClaimPatterns:
    """patterns.json (claim 判定パターンファイル。正本は SKILL.md
    「claim 判定パターン (正本)」セクションの JSON block) を読み込み検証する。

    Args:
        path: patterns.json への Path。

    Returns:
        ClaimPatterns: 検証済みのパターン集合。

    Raises:
        ClaimPatternsError: 次のいずれかに該当する場合に送出する
            (`main` はこれを捕捉し exit code `3` に変換する)。
            - ファイルが存在しない、または JSON として parse できない。
            - トップレベルキー `inProgressLabel` / `strict` / `loose` のいずれかが
              欠落している、または型が不一致 (`inProgressLabel` は非空文字列、
              `strict` / `loose` はリスト)。
            - `strict` / `loose` の要素が `{"id": str, "regex": str}` の形を
              満たさない (`id` または `regex` の欠落・型不一致・空文字列)。
            - 同一リスト内 (`strict` または `loose`) で `id` が重複している。
            - `regex` が Python `re` 構文として compile できない。
              (`{issue_number}` プレースホルダはそのままのリテラルとして
              compile を試行する。実際の issue 番号への置換と `re.MULTILINE`
              モードでの適用は `resolve_start` が issue ごとに行う)

    境界:
        - `strict` / `loose` が空リストであることはエラーにしない
          (該当パターンが 0 件として扱う)。
        - 未知のトップレベルキーは無視する (将来の拡張でエラーにしないため)。
    """
    raise NotImplementedError("Phase A: 実装は Phase B (issue #288) で行う")


def resolve_start(issue: dict, patterns: ClaimPatterns) -> StartResolution:
    """issue の `timelineItems` から着手時刻を解決する。

    着手候補 (candidates) は次の 2 種類の createdAt の和集合である。

    - strict パターン (`patterns.strict`) のいずれかに一致する `IssueComment.body`。
      パターンの `{issue_number}` は `issue["number"]` を `re.escape` した文字列に
      置換してから `re.MULTILINE` で `re.search` する
      (他の comment イベント種別は対象外)。
    - `label.name == patterns.in_progress_label` である `LabeledEvent`
      (`UnlabeledEvent` は候補集合から何も除去しない。既に採用した候補には影響しない)。

    Args:
        issue: `issues.jsonl` の 1 行 (モジュール docstring の入力契約を参照)。
            `timelineItems.nodes` を走査する。
        patterns: `load_claim_patterns` が返した検証済みパターン。

    Returns:
        StartResolution: `first_start` / `last_start` / `strict_sources` /
        `loose_only_matches` の定義は `StartResolution` の docstring を参照。

    境界:
        - strict にも loose にも一致しない `IssueComment` は無視する
          (どちらの結果リストにも現れない)。
        - 一致判定は body 全体に対する `re.search` (`re.MULTILINE`) であり、
          出現位置は問わない。ただし正規表現が `^` を含む場合は行頭でなければ
          一致しない (例: 行頭以外に出現する `🔒 ai:claim` は不一致、2 行目
          以降の行頭は `re.MULTILINE` により一致しうる)。
        - `timelineItems.totalCount > len(nodes)` (timeline 不完全) の判定と
          `exclusions.timelineOverflow` への計上は呼び出し側 (`compute`) の
          責務であり、本関数はそれを行わない (本関数は渡された `nodes` のみを見る)。
    """
    raise NotImplementedError("Phase A: 実装は Phase B (issue #288) で行う")


def resolve_ready(pr: dict) -> ReadyResolution:
    """PR の `state` / `isDraft` / `timelineItems` から ready 化時刻を解決する。

    対象イベントは `ReadyForReviewEvent` と `ConvertToDraftEvent`。

    Args:
        pr: `prs.jsonl` の 1 行 (モジュール docstring の入力契約を参照)。
            `state` (`"OPEN" | "MERGED"`) と `isDraft` (bool) によって
            `ready_at` の解決規則が分岐する
            (`ReadyResolution.ready_at` の docstring を参照)。

    Returns:
        ReadyResolution: `ready_at` / `redraft_count` / `via_draft` の定義は
        `ReadyResolution` の docstring を参照。

    境界:
        - `ready_at` の解決規則 (`state` / `isDraft` による分岐) は
          `ReadyResolution.ready_at` の docstring を正本とする。
          `state == "OPEN"` かつ `isDraft == True` のときは `ready_at` を
          `None` として返す (この場合も `redraft_count` / `via_draft` は
          他の分岐と同じ規則で計算する)。
        - `ConvertToDraftEvent` は `ready_at` の計算に使わず、`redraft_count`
          の算出にのみ使う (`via_draft` は `ReadyForReviewEvent` の有無だけで
          判定する)。
        - イベントの並び順は `timelineItems.nodes` の配列順を信頼せず、
          各イベントの `createdAt` の最大値・件数に基づいて判定する。
    """
    raise NotImplementedError("Phase A: 実装は Phase B (issue #288) で行う")


def resolve_close_linkage(issue: dict, prs_by_key: dict) -> CloseLinkage:
    """issue の最終クローズ状態から closer の分類を解決する。

    Args:
        issue: `issues.jsonl` の 1 行。`timelineItems.nodes` 内の `ClosedEvent` /
            `ReopenedEvent` を走査する。
        prs_by_key: `(repo, number)` をキーとした PR の辞書 (`prs.jsonl` から
            構築済み)。最終 `ClosedEvent.closer` が `PullRequest` かつ
            `closer["merged"] == True` のとき、その PR が収集対象リポジトリの
            PR 集合 (= `prs_by_key`) に存在するかどうかで `"merged_pr"` と
            `"merged_pr_external"` を区別する判定に使う (`category` の
            docstring 参照。`closer["merged"] == False` の場合は
            `prs_by_key` を参照せず `"unmerged_pr"` とする)。

    Returns:
        CloseLinkage: `category` / `linked_pr` の定義は `CloseLinkage` の
        docstring を参照。

    境界:
        - `issue["state"] != "CLOSED"` の場合は無条件に `category="open"`,
          `linked_pr=None` を返す (timeline の内容は見ない)。
        - `issue["state"] == "CLOSED"` かつ `issue["stateReason"] == "NOT_PLANNED"`
          の場合は closer 種別によらず `category="not_planned"` を返す
          (stateReason による判定を closer 種別より優先する)。
        - 上記いずれにも該当しない CLOSED issue で `timelineItems.nodes` 中に
          `ClosedEvent` が 1 件も見つからない場合は `category="manual"`
          にフォールバックする (timeline 不完全時の best-effort)。
    """
    raise NotImplementedError("Phase A: 実装は Phase B (issue #288) で行う")


def compute(
    issues: list[dict],
    prs: list[dict],
    patterns: ClaimPatterns,
    as_of: datetime,
    since: date | None,
    boundaries: list[dict] | None,
) -> dict:
    """issues / prs から結果 JSON (stdout 契約、モジュール docstring 参照) を組み立てる純粋関数。

    ファイル I/O は行わない。`issues` / `prs` は JSONL を 1 行ずつ `json.loads`
    した dict のリスト (日時フィールドはまだ ISO8601 文字列)。内部で各 issue /
    PR ごとに `resolve_start` / `resolve_ready` / `resolve_close_linkage` を
    呼び出して分類する。戻り値の dict はそのまま `json.dumps` して stdout に
    出力できる状態 (datetime は ISO8601 UTC 文字列、`date` は `YYYY-MM-DD`
    文字列) でなければならない。

    Args:
        issues: `issues.jsonl` の各行を `json.loads` した dict のリスト。
        prs: `prs.jsonl` の各行を `json.loads` した dict のリスト
            (OPEN + MERGED PR)。
        patterns: `load_claim_patterns` が返した検証済みパターン。
        as_of: 集計基準時刻 (UTC, aware datetime)。打ち切り系列の経過時間と
            出力の `asOf` に使う。
        since: `firstStartAt` (UTC 日付) がこの日付以上のタスクのみを対象にする
            inclusive filter。`None` のときは全期間を対象にする。
        boundaries: `[{"id": str, "label": str, "at": datetime}]` (`intervalStats`
            の区間境界、時刻は事前に aware datetime へ parse済みとする)。
            `None` または空リストのとき `intervalStats` は `[]`。

    Returns:
        dict: 次のトップレベルキーを持つ、JSON 直列化可能な dict。

        - `schemaVersion` (int): 常に `SCHEMA_VERSION`。
        - `asOf` (str): `as_of` を ISO8601 UTC 文字列化したもの。
        - `since` (str | None): `since` を `YYYY-MM-DD` 文字列化したもの (無指定なら `None`)。
        - `sizeBands` (list[dict]): `SIZE_BANDS` を
          `[{"band": b, "min": lo, "max": hi}, ...]` に投影したもの。
        - `repos` (list[dict]): repo 別の `{"repo", "issues", "closedIssues",
          "mergedPrs", "openReadyPrs"}`。`since` によるフィルタは適用しない
          (全期間)。`issues` はその repo の issue 総数、`closedIssues` は
          `state == "CLOSED"` の数、`mergedPrs` はその repo の merged PR 数
          (従来どおり)、`openReadyPrs` はその repo の `state == "OPEN"` かつ
          ready 到達済み (`isDraft == False`) の PR 数。
        - `mainSeries` (list[dict]): 着手マーカーあり (`first_start is not None`)
          かつ次のいずれかを満たす issue を 1 件 1 要素で列挙する。

          - (a) CLOSED issue で最終クローズが `category == "merged_pr"`
            (従来どおり。選択される PR は常に MERGED のため
            `completionBasis = "merged"`)。
          - (b) OPEN issue で、`closingIssuesReferences` にその issue を
            含む qualifying completion PR (モジュール docstring 「完了根拠
            (qualifying completion) の一般規則」を参照。`state ==
            "MERGED"` の PR、または現在 `state == "OPEN"` かつ
            `isDraft == False` の PR。`resolve_ready` の `ready_at` が
            非 `None` であることと同値) が 1 件以上存在する。
            qualifying completion PR が複数存在する場合は `readyAt` が
            最も早い PR を採用し、該当した issue 件数を
            `dataQuality.multipleReadyPrIssues` に計上する (選択された
            PR の状態や `completionBasis` は問わずカウントする)。

          `completionBasis` は (a) (b) いずれの経路でも、選択された PR
          (= (a) では最終クローズの merged PR、(b) では上記で選択した
          qualifying completion PR) の**実状態**で決める。選択 PR が
          `state == "MERGED"` なら `completionBasis = "merged"` とし
          `mergedAt` / `phaseHours.readyToMerge` を実データで埋める
          (issue 自体がまだ `state == "OPEN"` であっても — 例えば (b)
          経路で選択された PR が MERGED である場合に起こりうる)。選択
          PR が `state == "OPEN"` かつ `isDraft == False` なら
          `completionBasis = "ready_unmerged"` で `mergedAt = None` /
          `phaseHours.readyToMerge = None` とする。

          各要素は `{"repo", "issue", "firstStartAt", "lastStartAt",
          "startWeek", "pr", "prCreatedAt", "readyAt", "mergedAt",
          "completionBasis", "leadTimeHours", "phaseHours": {"startToPrCreated",
          "prCreatedToReady", "readyToMerge"}, "sizeBand", "sizeLines",
          "viaDraft", "redraftCount", "negativeInterval"}`。`completionBasis`
          は `"merged" | "ready_unmerged"`。`completionBasis ==
          "ready_unmerged"` の要素は `mergedAt` が `None`、
          `phaseHours.readyToMerge` が `None` (ready から先の区間が未確定
          のため)。`leadTimeHours` は `firstStartAt` → `readyAt`
          の経過時間 (`lastStartAt` ではない。複数着手の解釈が必要な場合に
          備えて `lastStartAt` 自体は保持する)。丸め前の値では
          `startToPrCreated + prCreatedToReady` が `leadTimeHours` に、
          それに `readyToMerge` を加えた値が着手→merge の総時間に一致する。
          出力値は各フィールドを独立に小数第 2 位へ丸めるため、丸め後の
          `startToPrCreated + prCreatedToReady` は `leadTimeHours` と最大
          ±0.01 時間ずれうる (許容誤差であり、Phase B 実装は丸め後の等式を
          強制しない。`readyToMerge` は両辺に同じ丸め済み値が現れるため
          誤差上限に影響しない)。`since` フィルタ適用対象。
        - `censored` (list[dict]): 着手マーカーがあり `category == "open"`
          (まだクローズしていない) issue のうち、qualifying completion PR
          (`closingIssuesReferences` にその issue を含む、`state ==
          "MERGED"` または `state == "OPEN"` かつ `isDraft == False` の
          PR。`mainSeries` (b) の qualifying completion PR の定義と同じ)
          がリンクされていないもの。これにより `censored` の経過時間は
          常に「着手→ready」の下限値として解釈できる。`censored` の
          適用範囲は `category == "open"` の issue に限られる — CLOSED
          issue で `mainSeries` のいずれにも該当しないものは `censored`
          には含めず `auxiliarySeries` 側で分類する。
          各要素は `{"repo", "issue", "firstStartAt", "startWeek",
          "elapsedHoursLowerBound"}`。`elapsedHoursLowerBound` は
          `as_of - firstStartAt` (時間)。`since` フィルタ適用対象。
        - `auxiliarySeries` (dict): 着手マーカーはあるが `category` が
          `merged_pr` でも `open` でもない issue を category 別に振り分けた、
          次の 5 キー固定 shape の dict — `{"manualClose": [...],
          "commitClose": [...], "notPlanned": [...], "unmergedPr": [...],
          "externalMergedClose": [...]}`。該当 issue が 0 件のカテゴリでも
          キー自体は省略せず空リストを出力する。各要素は少なくとも `repo` /
          `issue` / `firstStartAt` / `lastStartAt` / `startWeek` を含む
          (確定契約)。category 固有の追加フィールド (例: `unmergedPr` の PR
          番号) は Phase B 実装時に後方互換な追加として拡張しうる。
          `externalMergedClose` は `resolve_close_linkage(...).category ==
          "merged_pr_external"` (最終クローズの closer が `merged == true` の
          `PullRequest` だが `prs_by_key` に存在しない、収集範囲外リポジトリの
          merged PR 等) だった着手済み issue を列挙する。この分離により、
          収集範囲外リポジトリの merged PR で close された issue は ready
          到達時刻が得られず、`mainSeries` にも `censored` にも入らない。
          `since` フィルタ適用対象 (mainSeries と揃える)。
        - `prSeries` (list[dict]): ready 到達済み PR (`state == "MERGED"`、
          または `state == "OPEN"` かつ `isDraft == False`) を 1 件 1 要素
          で列挙する。ready 未到達 (`state == "OPEN"` かつ
          `isDraft == True`) の PR は `prSeries` に含めない。各要素は
          `{"repo", "pr", "state", "createdAt", "readyAt", "mergedAt",
          "createdToReadyHours", "readyToMergeHours", "sizeBand", "sizeLines",
          "viaDraft", "redraftCount"}`。`state` は `"OPEN" | "MERGED"`。
          `state == "OPEN"` の要素は `mergedAt` / `readyToMergeHours` が
          `None`。issue 側の着手マーカー有無に関わらず対象条件を満たす PR
          であれば列挙する (issue 起点の主系列とは独立)。
          `since` の適用: `pr["createdAt"]` (UTC date) `>= since` の
          inclusive filter を適用する (issue 系列と異なり着手時刻を持たない
          ため PR createdAt を基準にする。`markerCoverage` / `repos` は
          全期間・非フィルタ)。
        - `weeklyCohorts` (list[dict]): 母集団は `mainSeries` と `censored`
          の `startWeek` (= `firstStartAt` の ISO 8601 週、`%G-W%V`、
          月曜始まり、UTC。`closedAt` 週ではない) の和集合であり、
          `censored` しか存在しない週も必ず 1 行を出す。各要素は
          `{"week", "n", "medianLeadTimeHours", "phaseMedians": {...},
          "bySizeBand": {band: {"n", "medianLeadTimeHours"}, ...}, "censoredN",
          "viaDraftRate", "medianSizeLines"}`。`n` はその週に割り当てられた
          `mainSeries` 要素数 (`negativeInterval` な要素も含む)。`mainSeries`
          が 0 件で `censored` のみの週は `n: 0`、`medianLeadTimeHours: None`、
          `phaseMedians` 各値 `None`、`bySizeBand: {}`、`viaDraftRate: None`、
          `medianSizeLines: None`、`censoredN` はその週の `censored` 件数、
          とする。中央値系 (`medianLeadTimeHours` / `phaseMedians` /
          `bySizeBand` 内の中央値) は `negativeInterval == True` の要素を
          除外して計算する (該当週の全要素が `negativeInterval` なら
          中央値は `null`)。`phaseMedians.readyToMerge` はさらに
          `completionBasis == "ready_unmerged"` (= `phaseHours.readyToMerge
          is None`) の要素を除外して計算する (該当週に merge 済みの要素が
          1 件も無ければ `null`)。`bySizeBand` は当該週に 1 件以上存在した
          帯のみをキーとして含める (0 件の帯は省略する疎な dict)。
          `censoredN` はその週に割り当てられた `censored` 要素数。
          `viaDraftRate` / `medianSizeLines` は `negativeInterval` を
          除外せず `n` 全体で計算する (`n == 0` の週は前述のとおり
          `None`)。中央値は `statistics.median` に準拠 (偶数個は中央 2 値の
          算術平均)。`medianLeadTimeHours` 等の中央値は完了タスクのみから
          計算される記述的統計であり、censor-aware 推定 (Kaplan–Meier 等)
          は行わない。解釈は `censoredN` と併せて行う。
        - `markerCoverage` (list[dict]): repo × 月 (`issue["closedAt"]` を
          `YYYY-MM` に truncate、UTC) 別の `{"repo", "month", "closedIssues",
          "withMarker", "coverage"}`。`closedIssues` はその repo・月に
          クローズした issue 数、`withMarker` はそのうち着手マーカーが
          あった数、`coverage = withMarker / closedIssues` (`closedIssues == 0`
          のときは `0.0`)。`since` によるフィルタは適用しない (全期間。
          比較可能な coverage 期間の判定材料とするため意図的に除外する)。
        - `claimDetection` (dict): `{"strictIssues": int, "looseOnlyIssues":
          list[dict]}`。`strictIssues` は `strict_sources` が 1 件以上ある
          (= `first_start is not None`) issue の件数 (issue 単位で重複排除)。
          `looseOnlyIssues` は `loose_only_matches` を issue 横断でフラットに
          列挙した `{"repo", "issue", "commentCreatedAt"}` のリスト
          (comment 単位。同一 issue に複数の loose-only comment があれば
          複数エントリになる。当該 issue が strict でも判定済みかどうかは問わない)。
        - `exclusions` (dict): `{"timelineOverflow": list[dict],
          "prTimelineOverflow": list[dict]}`。
          `timelineOverflow` は `timelineItems.totalCount > len(timelineItems.nodes)`
          だった issue を `{"repo", "issue", "totalCount", "fetched"}` で列挙する
          (`fetched == len(nodes)`)。これらの issue は `mainSeries` /
          `censored` / `auxiliarySeries` のいずれにも含めない。
          `prTimelineOverflow` は `timelineItems.totalCount > len(timelineItems.nodes)`
          だった **PR** を `{"repo", "pr", "totalCount", "fetched", "linkedIssues"}`
          で列挙する (`fetched == len(nodes)`。`linkedIssues` はこの除外により
          `mainSeries` から除外された issue 番号の昇順リスト。該当なしなら空リスト)。
          この判定は PR の `state` (OPEN / MERGED) を問わず同様に適用する
          (OPEN PR も `timelineItems.totalCount > len(nodes)` なら同じ扱いで
          除外する)。overflow した PR は `prSeries` から除外し、`mainSeries`
          (b) の ready 到達済みリンク PR としても採用しない (timeline 不完全な
          PR の ready 時刻は信頼できないため)。
          `resolve_close_linkage(...).linked_pr` が overflow PR と一致する issue は
          `mainSeries` から除外し、`auxiliarySeries` のいずれのカテゴリにも
          再分類しない (timeline 不完全な PR の ready 時刻は信頼できないため)。
          除外された issue 番号は当該 PR の `linkedIssues` に列挙する。
        - `intervalStats` (list[dict]): `boundaries` を createdAt 昇順
          (= `at` 昇順) に並べ、`[開始, b1)`, `[b1, b2)`, ..., `[bn, 終端]` の
          各区間に `mainSeries` と `censored` を `firstStartAt` で割り当てた
          集計。各要素は `{"from": str | None, "to": str | None, "label", "n",
          "medianLeadTimeHours", "medianSizeLines", "smallOnlyMedianLeadTimeHours",
          "smallOnlyN", "censoredN"}`。`from` / `to` は区間端の
          `boundaries[].id` (開始側端が無ければ `from=None`、終端側端が無ければ
          `to=None`)。`smallOnly*` は `sizeBand == "S"` の要素のみに絞った
          集計。`censoredN` はその区間に `firstStartAt` で割り当てられた
          `censored` 要素数。`medianLeadTimeHours` / `smallOnlyMedianLeadTimeHours`
          は `negativeInterval == True` の要素を除外して計算する (該当区間の
          有効要素が 0 件なら中央値は `null`)。`n` / `smallOnlyN` /
          `medianSizeLines` は `negativeInterval` を除外せず区間内の全
          `mainSeries` (該当帯) 要素で計算する (`weeklyCohorts` と同じ規約)。
          `boundaries` が `None` または空のとき `intervalStats == []`。
        - `dataQuality` (dict): `{"negativeIntervalCount": int, "redraftPrCount":
          int, "notStartedClosedIssues": int, "multipleReadyPrIssues": int}`。
          `negativeIntervalCount` は `mainSeries` 中で `readyAt < firstStartAt`
          (`negativeInterval == True`) だった件数。`redraftPrCount` は
          `prSeries` 中で `redraftCount > 0` だった PR 件数。
          `notStartedClosedIssues` は `state == "CLOSED"` かつ着手マーカーが
          無かった (`first_start is None`) issue の件数 (`since` フィルタは
          適用しない)。`multipleReadyPrIssues` は `mainSeries` の (b) 経路
          (OPEN issue で qualifying completion PR をリンクして
          `mainSeries` に入った issue) において、qualifying completion PR
          が複数存在したため `readyAt` 最小の PR を採用した issue の件数
          (選択された PR の状態や `completionBasis` は問わずカウントする)。

    境界:
        - `since` の inclusive filter は `firstStartAt` (UTC date 部分)
          `>= since` で判定し、`mainSeries` / `censored` / `auxiliarySeries` /
          `weeklyCohorts` に適用する (`weeklyCohorts` はフィルタ後の
          `mainSeries` から算出されるため間接的に適用される)。
        - 数値の丸めはすべて時間 (hours) 単位・小数第 2 位まで
          (Python 組み込み `round`、round half-even)。
        - `n = 0` の集計オブジェクトでも、そのキー自体は出力から省略しない
          (値を `null` にする。例: 該当週の全件が `negativeInterval` のときの
          `medianLeadTimeHours`)。
        - 本関数は `issues` / `prs` の必須フィールド欠落を検証しない
          (JSONL の読み込み・検証は `main` の責務。本関数は事前に検証済みの
          データが渡されることを前提とする)。
    """
    raise NotImplementedError("Phase A: 実装は Phase B (issue #288) で行う")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI 引数を定義し parse する (CLI 契約はモジュール docstring を参照)。

    定義する引数:

    - `--issues <path>` (必須, `Path`): issues.jsonl のパス。
    - `--prs <path>` (必須, `Path`): prs.jsonl のパス。
    - `--claim-patterns-file <path>` (必須, `Path`, dest=`claim_patterns_file`):
      claim 判定パターン patterns.json のパス。
    - `--as-of <str>` (必須, dest=`as_of`): UTC の ISO8601 文字列
      (例 `2026-07-17T04:00:00Z`)。文字列のまま返す
      (`datetime` への parse は `main` が行い、不正形式は exit code `2` に変換する)。
    - `--since <str>` (省略可, 既定 `None`): `YYYY-MM-DD`。文字列のまま返す。
    - `--boundaries-file <path>` (省略可, `Path`, 既定 `None`, dest=`boundaries_file`):
      boundaries.json のパス。

    Args:
        argv: コマンドライン引数のリスト。`None` のとき argparse は
            `sys.argv[1:]` を使う。

    Returns:
        argparse.Namespace: `issues`, `prs`, `claim_patterns_file`, `as_of`,
        `since`, `boundaries_file` の各属性を持つ。`issues` / `prs` /
        `claim_patterns_file` / `boundaries_file` は `pathlib.Path`
        (`boundaries_file` は未指定なら `None`)。`as_of` / `since` は `str`
        (`since` は未指定なら `None`)。

    Raises:
        SystemExit: 必須引数 (`--issues` / `--prs` / `--claim-patterns-file` /
            `--as-of`) が欠落した場合や未知の引数が渡された場合、argparse の
            既定動作により (通常 exit code `2` で) 送出される。

    境界:
        本関数は引数の定義と構文解析のみを担当する。ファイル存在確認や
        `--as-of` / `--since` の意味的な妥当性検証 (実際に parse できるか) は
        行わない (`main` の責務)。
    """
    parser = argparse.ArgumentParser(
        prog="compute_leadtime.py",
        description=(
            "GitHub issue/PR タイムラインから AI タスクのリードタイムを集計し、"
            "結果 JSON を stdout に出力する。"
        ),
    )
    parser.add_argument(
        "--issues",
        required=True,
        type=Path,
        help="issues.jsonl (OPEN + CLOSED 全件、1 行 1 issue) のパス",
    )
    parser.add_argument(
        "--prs",
        required=True,
        type=Path,
        help="prs.jsonl (OPEN + MERGED PR、1 行 1 PR) のパス",
    )
    parser.add_argument(
        "--claim-patterns-file",
        required=True,
        type=Path,
        help="claim 判定パターン (patterns.json) のパス",
    )
    parser.add_argument(
        "--as-of",
        required=True,
        help="集計基準時刻 (UTC, ISO8601, 例 2026-07-17T04:00:00Z)",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="集計対象の開始日 (YYYY-MM-DD、省略時は全期間)",
    )
    parser.add_argument(
        "--boundaries-file",
        default=None,
        type=Path,
        help="intervalStats の区間境界 (boundaries.json) のパス (省略可)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI エントリポイント。

    Args:
        argv: コマンドライン引数のリスト (`sys.argv[1:]` 相当)。`None` のとき
            `parse_args` は `sys.argv[1:]` を使う。

    Returns:
        int: exit code。
            - `0`: 成功 (集計対象が 0 件の空データも含む)。結果 JSON を
              stdout に書き出す。
            - `2`: 入力エラー (`--issues` / `--prs` のファイル不存在・JSONL
              parse 失敗・必須フィールド欠落・`--as-of` / `--since` の形式不正・
              `--prs` の行に `closingIssuesReferences.totalCount >
              len(closingIssuesReferences.nodes)` が 1 件でも存在する場合)。
            - `3`: `--claim-patterns-file` の契約違反
              (`load_claim_patterns` が `ClaimPatternsError` を送出した場合)。

    副作用:
        - `parse_args` が返す各パスのファイルを読み込む
          (`--issues` / `--prs` / `--claim-patterns-file` / 任意で `--boundaries-file`)。
        - 成功時、`compute` が返した dict を `json.dumps` して stdout に
          書き出す (stdout に出力するのはこの結果 JSON のみ)。
        - 診断メッセージ (エラー理由等) はすべて stderr に書き出す。
        - 上記以外の I/O (ネットワーク・git・他ファイルの書き込み等) は行わない。

    境界:
        いずれの exit code でも部分データのまま処理を継続しない (fail-closed)。
        `--as-of` / `--since` の解析失敗、必須フィールド欠落は exit code `2`、
        claim patterns file の契約違反は exit code `3` として明確に区別する。
    """
    raise NotImplementedError("Phase A: 実装は Phase B (issue #288) で行う")


if __name__ == "__main__":
    sys.exit(main())
