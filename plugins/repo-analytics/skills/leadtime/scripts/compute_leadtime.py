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

- 共通: `repo` は GitHub API が返す canonical な `nameWithOwner` であり、
  issues.jsonl / prs.jsonl / closer / closingIssuesReferences のすべてで
  同一の canonical 形が使われる前提 (`(repo, number)` join のキー)。
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
  `intervalStats` の区間境界。検証規則・正規化後の並び順は次の
  「exit code 契約」セクションを参照。

## 出力 JSON 契約 (stdout, 詳細は各関数 docstring および issue #288 Phase A
契約ドキュメント セクション 5 が正本)

トップレベルキー: `schemaVersion`, `asOf`, `since`, `sizeBands`, `repos`,
`mainSeries`, `censored`, `auxiliarySeries`, `prSeries`, `weeklyCohorts`,
`markerCoverage`, `claimDetection`, `exclusions`, `boundaries`,
`intervalStats`, `dataQuality`。フィールドの意味は `compute()` の docstring を
正本とする。

## exit code 契約

- `0`: 成功 (集計対象が 0 件の空データも含む)。
- `2`: 入力エラー (`--issues` / `--prs` のファイル不存在・JSONL parse 失敗・
  必須フィールド欠落・実際に処理される日時フィールド (`IssueComment.createdAt`
  / `LabeledEvent.createdAt` / `ClosedEvent.createdAt` / PR の `createdAt` /
  `mergedAt` / `ReadyForReviewEvent.createdAt` 等) が tz 情報の無い naive
  datetime 文字列である場合・`--as-of` / `--since` の形式不正・`--prs` の行に
  `closingIssuesReferences.totalCount > len(closingIssuesReferences.nodes)`
  が 1 件でも存在する場合・`--boundaries-file` の検証失敗)。日時 parser は
  `_parse_datetime` に一本化されており、この関数が naive datetime を検出した
  箇所 (`--issues` / `--prs` / `--boundaries-file` / `--as-of` のいずれでも)
  で `ValueError` を送出し、`main` が exit code `2` へ変換する。
  `--boundaries-file` の検証失敗とは、ファイル不存在・JSON parse 失敗・
  形状不正 (トップレベルが `[{"id": str, "label": str, "at": str}, ...]`
  の形を満たさない)・各要素の `at` が ISO8601 UTC の aware datetime として
  parse できない (naive datetime を含む)・`id` / `label` の欠落または
  空文字列・`id` の重複、のいずれかを指す。入力配列の並び順は問わない —
  検証を通過した boundaries は `(at, id)` の辞書順 (`at` 昇順、同一 `at`
  は `id` の辞書順で tie-break) に正規化してから `compute` へ渡す。
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
import json
import re
import statistics
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
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
        `ClosedEvent`)。createdAt が同値の複数 `ClosedEvent` は
        `timelineItems.nodes` 配列内で後方のものを採用する。
    """

    category: str
    linked_pr: dict | None


def _parse_datetime(value: str) -> datetime:
    """UTC の ISO8601 文字列 (`Z` サフィックス許容) を aware `datetime` へ変換する。

    このモジュール内で日時文字列を parse する唯一の関数であり
    (`--issues` / `--prs` / `--claim-patterns-file` の comment・event
    timestamp、`--as-of`、`--boundaries-file` の `at` を含む全経路がこれを
    経由する)、tz 情報の無い (naive) datetime 文字列は `ValueError` を送出する
    (モジュール docstring 「タイムゾーン」節の fail-closed 規則の実装)。
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"aware (timezone 付き) datetime ではありません: {value}")
    return parsed


def _format_datetime(value: datetime) -> str:
    """aware `datetime` を `Z` サフィックス付き UTC ISO8601 文字列へ変換する。"""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _size_band(size_lines: int) -> str:
    """`additions + deletions` の行数から `SIZE_BANDS` の帯 (band) 名を求める。"""
    for band, minimum, maximum in SIZE_BANDS:
        if size_lines >= minimum and (maximum is None or size_lines <= maximum):
            return band
    raise ValueError(
        f"size_lines が SIZE_BANDS のいずれの帯にも属しません: {size_lines}"
    )


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
            - ファイルを UTF-8 として decode できない場合。
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
    try:
        raw_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ClaimPatternsError(
            f"claim patterns file を読み込めません ({path}): {exc}"
        ) from exc
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ClaimPatternsError(
            f"claim patterns file が JSON として parse できません ({path}): {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ClaimPatternsError(
            "claim patterns file のトップレベルは object である必要があります"
        )

    in_progress_label = data.get("inProgressLabel")
    if not isinstance(in_progress_label, str) or not in_progress_label:
        raise ClaimPatternsError("inProgressLabel は非空文字列である必要があります")

    strict = _load_claim_pattern_list(data.get("strict"), "strict")
    loose = _load_claim_pattern_list(data.get("loose"), "loose")
    return ClaimPatterns(
        in_progress_label=in_progress_label, strict=strict, loose=loose
    )


def _load_claim_pattern_list(raw: object, list_name: str) -> list[ClaimPattern]:
    """`load_claim_patterns` が使う、`strict` / `loose` いずれかのリストの検証・変換。"""
    if not isinstance(raw, list):
        raise ClaimPatternsError(f"{list_name} はリストである必要があります")

    seen_ids: set[str] = set()
    patterns: list[ClaimPattern] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ClaimPatternsError(
                f"{list_name} の要素は object である必要があります"
            )
        pattern_id = item.get("id")
        regex = item.get("regex")
        if not isinstance(pattern_id, str) or not pattern_id:
            raise ClaimPatternsError(
                f"{list_name} の id は非空文字列である必要があります"
            )
        if not isinstance(regex, str) or not regex:
            raise ClaimPatternsError(
                f"{list_name} の regex は非空文字列である必要があります"
            )
        if pattern_id in seen_ids:
            raise ClaimPatternsError(
                f"{list_name} 内で id が重複しています: {pattern_id}"
            )
        seen_ids.add(pattern_id)
        try:
            re.compile(regex)
        except re.error as exc:
            raise ClaimPatternsError(
                f"{list_name} の regex が compile できません (id={pattern_id}): {exc}"
            ) from exc
        patterns.append(ClaimPattern(id=pattern_id, regex_template=regex))
    return patterns


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
    issue_number_escaped = re.escape(str(issue["number"]))
    strict_matchers = [
        (
            pattern.id,
            re.compile(
                _substitute_issue_number(pattern.regex_template, issue_number_escaped),
                re.MULTILINE,
            ),
        )
        for pattern in patterns.strict
    ]
    loose_matchers = [
        re.compile(
            _substitute_issue_number(pattern.regex_template, issue_number_escaped),
            re.MULTILINE,
        )
        for pattern in patterns.loose
    ]

    candidates: list[tuple[datetime, dict]] = []
    loose_only_matches: list[dict] = []

    for node in issue["timelineItems"]["nodes"]:
        typename = node.get("__typename")
        if typename == "IssueComment":
            body = node.get("body", "")
            matched_pattern_id = None
            for pattern_id, matcher in strict_matchers:
                if matcher.search(body):
                    matched_pattern_id = pattern_id
                    break
            if matched_pattern_id is not None:
                created_at = _parse_datetime(node["createdAt"])
                candidates.append(
                    (
                        created_at,
                        {
                            "kind": "comment",
                            "at": created_at,
                            "patternId": matched_pattern_id,
                        },
                    )
                )
            elif any(matcher.search(body) for matcher in loose_matchers):
                loose_only_matches.append(
                    {"commentCreatedAt": _parse_datetime(node["createdAt"])}
                )
        elif typename == "LabeledEvent":
            label = node.get("label") or {}
            if label.get("name") == patterns.in_progress_label:
                created_at = _parse_datetime(node["createdAt"])
                candidates.append(
                    (created_at, {"kind": "label", "at": created_at, "patternId": None})
                )
        # UnlabeledEvent / ClosedEvent / ReopenedEvent は着手候補に影響しない。

    if not candidates:
        return StartResolution(
            first_start=None,
            last_start=None,
            strict_sources=[],
            loose_only_matches=loose_only_matches,
        )

    candidate_ats = [at for at, _source in candidates]
    strict_sources = [source for _at, source in candidates]
    return StartResolution(
        first_start=min(candidate_ats),
        last_start=max(candidate_ats),
        strict_sources=strict_sources,
        loose_only_matches=loose_only_matches,
    )


def _substitute_issue_number(regex_template: str, issue_number_escaped: str) -> str:
    """claim パターンの `{issue_number}` プレースホルダを実際の issue 番号へ置換する。"""
    return regex_template.replace("{issue_number}", issue_number_escaped)


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
    ready_ats: list[datetime] = []
    redraft_count = 0
    for node in pr["timelineItems"]["nodes"]:
        typename = node.get("__typename")
        if typename == "ReadyForReviewEvent":
            ready_ats.append(_parse_datetime(node["createdAt"]))
        elif typename == "ConvertToDraftEvent":
            redraft_count += 1

    via_draft = len(ready_ats) > 0

    if pr["state"] == "OPEN" and pr["isDraft"]:
        ready_at = None
    elif ready_ats:
        ready_at = max(ready_ats)
    else:
        ready_at = _parse_datetime(pr["createdAt"])

    return ReadyResolution(
        ready_at=ready_at, redraft_count=redraft_count, via_draft=via_draft
    )


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
    if issue["state"] != "CLOSED":
        return CloseLinkage(category="open", linked_pr=None)

    if issue.get("stateReason") == "NOT_PLANNED":
        return CloseLinkage(category="not_planned", linked_pr=None)

    closed_events_with_index = [
        (index, node)
        for index, node in enumerate(issue["timelineItems"]["nodes"])
        if node.get("__typename") == "ClosedEvent"
    ]
    if not closed_events_with_index:
        return CloseLinkage(category="manual", linked_pr=None)

    # (createdAt, index) の全順序で選ぶ。createdAt が同値の場合は
    # timelineItems.nodes 配列内で後方の要素を優先する (CloseLinkage の
    # docstring 「境界」参照)。
    _, final_event = max(
        closed_events_with_index,
        key=lambda item: (_parse_datetime(item[1]["createdAt"]), item[0]),
    )
    closer = final_event.get("closer")
    if closer is None:
        return CloseLinkage(category="manual", linked_pr=None)

    closer_type = closer.get("__typename")
    if closer_type == "PullRequest":
        pr_repo = closer["repository"]["nameWithOwner"]
        pr_number = closer["number"]
        if closer["merged"]:
            if (pr_repo, pr_number) in prs_by_key:
                return CloseLinkage(
                    category="merged_pr",
                    linked_pr={"repo": pr_repo, "number": pr_number},
                )
            return CloseLinkage(category="merged_pr_external", linked_pr=None)
        return CloseLinkage(category="unmerged_pr", linked_pr=None)

    if closer_type == "Commit":
        return CloseLinkage(category="commit", linked_pr=None)

    # 未知の closer 種別 (契約上は PullRequest / Commit のみを想定): manual 扱いの
    # best-effort フォールバック (closer 情報以外に手掛かりが無いのと同じ状況)。
    return CloseLinkage(category="manual", linked_pr=None)


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
            `None` または空リストのとき出力の `boundaries` は `[]`、
            `intervalStats` も `[]`。非空のときは `(at, id)` の辞書順に
            正規化してから出力の `boundaries` キーと `intervalStats` の
            区間分割の両方に使う (`compute` 内で正規化を一度だけ行う)。

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
        - `mainSeries` / `censored` / `auxiliarySeries` の対象決定は、
          着手マーカーあり (`first_start is not None`) の issue に対して
          次の分類パイプラインを適用する (マーカーの無い issue はいずれの
          系列にも含めない)。

          1. `exclusions.timelineOverflow` に列挙される issue (自身の
             `timelineItems` が不完全) はこの時点で除外し、以降の判定を
             行わない。
          2. `issue["stateReason"] == "NOT_PLANNED"` の issue は、closer の
             種別や qualifying PR 候補の有無を問わず、明示的な「対応しない」
             判断として無条件に `auxiliarySeries.notPlanned` に固定する。
          3. (1)(2) のいずれにも該当しない issue について、qualifying PR
             候補集合を次の 2 種の和集合として構築する。

             - 最終 `ClosedEvent` の closer が `merged == True` の
               `PullRequest` であり、かつその PR が `prs_by_key` に存在する
               もの (`resolve_close_linkage` の `category == "merged_pr"` で
               特定される PR と同じもの)。この経路もモジュール docstring
               「完了根拠 (qualifying completion) の一般規則」の qualifying
               要件 (`resolve_ready(...).ready_at` が非 `None`) を満たす
               ことを要求する — issue timeline 側の closer 情報
               (`merged == True`) と `--prs` 側で個別収集した PR snapshot は
               収集時刻が異なりうるため、snapshot 側が issue timeline より
               古い (stale) ままだと `state == "OPEN"` かつ
               `isDraft == True` (ready 未到達) でありうる。この場合
               fail-closed に候補から除外し
               `exclusions.mergedCloserPrNotQualifying` に列挙する
               (`pr_overflow_set` による除外とは独立に判定する)。
             - `closingIssuesReferences` の逆引きでこの issue を指す PR の
               うち、qualifying completion PR (モジュール docstring 「完了
               根拠 (qualifying completion) の一般規則」を参照。`state ==
               "MERGED"` の PR、または現在 `state == "OPEN"` かつ
               `isDraft == False` の PR。`resolve_ready(...).ready_at` が
               非 `None` であることと同値) であるもの。

             候補には overflow PR (`exclusions.prTimelineOverflow`) を含めない
             (timeline 不完全な PR の ready 時刻は信頼できないため)。
          4. 候補が 1 件以上あれば、issue の `state` (OPEN/CLOSED) を問わず
             `readyAt` が最も早い候補 PR を採用し `mainSeries` に編入する。
             候補が複数あった場合、この issue は `since` フィルタ適用後も
             `mainSeries` に残っていれば `dataQuality.multipleReadyPrIssues`
             に計上する (選択 PR の状態や `completionBasis` は問わずカウント
             する。計上方法の詳細は `dataQuality` の docstring を参照)。
             `completionBasis` は
             選択した PR の**実状態**で決める — 選択 PR が `state ==
             "MERGED"` なら `completionBasis = "merged"` とし `mergedAt` /
             `phaseHours.readyToMerge` を実データで埋める (issue 自体が
             `state == "OPEN"` のままでも、選択 PR が MERGED であればこの
             ケースになりうる)。選択 PR が `state == "OPEN"` かつ
             `isDraft == False` なら `completionBasis = "ready_unmerged"` で
             `mergedAt = None` / `phaseHours.readyToMerge = None` とする。
          5. 候補が無いが、overflow PR が無ければ qualifying だったことを
             snapshot state (`MERGED`、または `OPEN` かつ non-draft) または
             issue timeline の merged closer から証明できる場合は、ready
             時刻だけが不明な除外として `exclusions.prReadyTimeUnknown` に
             PR との対応関係を列挙する。同じ issue を当該 overflow PR の
             `exclusions.prTimelineOverflow[].linkedIssues` にも列挙し、
             `mainSeries` / `censored` / `auxiliarySeries` には入れない。
          6. 候補もステップ 5 の completion 証明も無く、issue が `state ==
             "OPEN"` なら `censored` に入れる。これにより `censored` の
             経過時間は常に「着手→ready」の下限値として解釈できる。
          7. 候補もステップ 5 の completion 証明も無く、issue が `state ==
             "CLOSED"` なら、
             `resolve_close_linkage(issue, prs_by_key).category` に応じて
             `auxiliarySeries` の該当キー (`"manual"` → `manualClose`,
             `"commit"` → `commitClose`, `"unmerged_pr"` → `unmergedPr`,
             `"merged_pr_external"` → `externalMergedClose`) へ振り分ける
             (`category == "merged_pr"` はこの時点では起こりえない —
             候補集合の 1 つ目の情報源と同じ判定であり、候補が無いことは
             `category != "merged_pr"` を含意するため)。

          境界: ステップ 3 の 1 つ目の情報源 (最終 `ClosedEvent` の merged
          closer) が `exclusions.prTimelineOverflow` の overflow PR と
          一致する場合、または収集時点の PR snapshot が非 qualifying
          (stale、`exclusions.mergedCloserPrNotQualifying` に列挙) の場合、
          その PR は候補にならない (ステップ 3 で除外)。2 つ目の情報源
          (`closingIssuesReferences` 逆引き) にも qualifying candidate が
          見つからなければ、`resolve_close_linkage` を呼び直すと `category ==
          "merged_pr"` に見えることがあるが、ステップ 7 は `"manual"` /
          `"commit"` / `"unmerged_pr"` / `"merged_pr_external"` の
          4 category しか `auxiliarySeries` に対応付けないため、この issue
          はどの `auxiliarySeries` にも該当せず、除外されたまま
          (`mainSeries` にも `censored` にも `auxiliarySeries` にも入らない)
          となる。overflow が候補不成立の唯一の理由なら、除外された issue は
          overflow PR 側の `exclusions.prTimelineOverflow[].linkedIssues` に
          列挙される。別の qualifying PR で `mainSeries` に入った issue、
          着手マーカー無し、NOT_PLANNED など overflow が分類除外の原因では
          ない issue は列挙しない。非 qualifying (stale snapshot) による
          除外の場合は
          `exclusions.mergedCloserPrNotQualifying` に列挙される。

          `mainSeries` の各要素は `{"repo", "issue", "firstStartAt",
          "lastStartAt", "startWeek", "pr", "prRepo", "prCreatedAt", "readyAt",
          "mergedAt", "completionBasis", "leadTimeHours", "phaseHours":
          {"startToPrCreated", "prCreatedToReady", "readyToMerge"},
          "sizeBand", "sizeLines", "viaDraft", "redraftCount",
          "negativeInterval"}`。`repo` は issue 側の canonical repo
          (nameWithOwner)、`prRepo` は選択された qualifying completion PR の
          canonical repo であり、全レコード必須 (`None` を許容しない)。
          両者は同じ値になることが多いが、`closingIssuesReferences` 逆引きで
          見つかる cross-repo closing reference (issue と別リポジトリの PR が
          その issue を close する場合) では `prRepo != repo` になりうる。
          `completionBasis` は `"merged" |
          "ready_unmerged"`。`completionBasis == "ready_unmerged"` の要素は
          `mergedAt` が `None`、`phaseHours.readyToMerge` が `None` (ready
          から先の区間が未確定のため)。`leadTimeHours` は `firstStartAt` →
          `readyAt` の経過時間 (`lastStartAt` ではない。複数着手の解釈が
          必要な場合に備えて `lastStartAt` 自体は保持する)。丸め前の値では
          `startToPrCreated + prCreatedToReady` が `leadTimeHours` に、
          それに `readyToMerge` を加えた値が着手→merge の総時間に一致する。
          出力値は各フィールドを独立に小数第 2 位へ丸めるため、丸め後の
          `startToPrCreated + prCreatedToReady` は `leadTimeHours` と最大
          ±0.01 時間ずれうる (許容誤差であり、Phase B 実装は丸め後の等式を
          強制しない。`readyToMerge` は両辺に同じ丸め済み値が現れるため
          誤差上限に影響しない)。`negativeInterval` は、丸め前の値で
          `startToPrCreated` / `prCreatedToReady` / `readyToMerge`
          (non-null なもののみ判定対象) のいずれか、または `leadTimeHours`
          が負であれば `True` (複数の区間が同時に負でもフラグは 1 つの
          ままであり、負だった区間ごとの内訳は持たない)。中央値計算からの
          除外は metric 単位で行う (`dataQuality` / `weeklyCohorts` /
          `intervalStats` の各項目 docstring を参照。「レコード全体を全
          中央値から除外する」規則ではない)。`since` フィルタ適用対象。
        - `censored` (list[dict]): 上記パイプラインのステップ 6 で
          `censored` に入れられた issue の一覧。各要素は `{"repo", "issue",
          "firstStartAt", "startWeek", "elapsedHoursLowerBound"}`。
          `elapsedHoursLowerBound` は `as_of - firstStartAt` (時間)。
          `since` フィルタ適用対象。
        - `auxiliarySeries` (dict): 上記パイプラインのステップ 2
          (`notPlanned`) およびステップ 7 で振り分けられた issue を
          category 別にまとめた、次の 5 キー固定 shape の dict —
          `{"manualClose": [...], "commitClose": [...], "notPlanned": [...],
          "unmergedPr": [...], "externalMergedClose": [...]}`。該当 issue が
          0 件のカテゴリでもキー自体は省略せず空リストを出力する。各要素は
          少なくとも `repo` / `issue` / `firstStartAt` / `lastStartAt` /
          `startWeek` を含む (確定契約)。category 固有の追加フィールド
          (例: `unmergedPr` の PR 番号) は Phase B 実装時に後方互換な追加
          として拡張しうる。`externalMergedClose` (`category ==
          "merged_pr_external"`) は、最終クローズの closer が `merged ==
          true` の `PullRequest` だが `prs_by_key` に存在しない (収集範囲外
          リポジトリの merged PR 等) ために ready 到達時刻を計測できず、
          かつステップ 3 の `closingIssuesReferences` 逆引きでも qualifying
          candidate が見つからなかった issue を列挙する。この分離により、
          収集範囲外リポジトリの merged PR で close された issue は
          `mainSeries` にも `censored` にも入らない。`since` フィルタ適用
          対象 (mainSeries と揃える)。
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
          とする。`medianLeadTimeHours` および `bySizeBand` 内の
          `medianLeadTimeHours` は、丸め前の `leadTimeHours` が負の要素を
          除外して計算する (metric 単位の除外 — `phaseHours` のいずれかが
          負でも `leadTimeHours` 自体が非負なら計算対象に含める。該当週の
          対象要素が 0 件なら中央値は `null`)。`phaseMedians` の各 phase
          (`startToPrCreated` / `prCreatedToReady` / `readyToMerge`) の
          中央値は、それぞれ自身の丸め前の値が負の要素のみを除外して
          計算する (他の phase や `leadTimeHours` が負でも、その phase
          自身が非負なら含める)。`phaseMedians.readyToMerge` はさらに
          `completionBasis == "ready_unmerged"` (= `phaseHours.readyToMerge
          is None`) の要素を除外して計算する (該当週に merge 済みかつ
          `readyToMerge` が非負の要素が 1 件も無ければ `null`)。
          `bySizeBand` は当該週に 1 件以上存在した帯のみをキーとして
          含める (0 件の帯は省略する疎な dict)。
          `censoredN` はその週に割り当てられた `censored` 要素数。
          `viaDraftRate` / `medianSizeLines` は `negativeInterval` を
          除外せず `n` 全体で計算する (`n == 0` の週は前述のとおり
          `None`)。中央値は `statistics.median` に準拠 (偶数個は中央 2 値の
          算術平均)。`medianLeadTimeHours` 等の中央値は完了タスクのみから
          計算される記述的統計であり、censor-aware 推定 (Kaplan–Meier 等)
          は行わない。解釈は `censoredN` と併せて行う。
        - `markerCoverage` (list[dict]): repo × 月 (`issue["closedAt"]` を
          `YYYY-MM` に truncate、UTC) 別の `{"repo", "month", "closedIssues",
          "withMarker", "unknownTimeline", "coverage"}`。`closedIssues` は
          その repo・月にクローズした issue のうち timeline が完全
          (`exclusions.timelineOverflow` に含まれない) だった数 (=
          `coverage` の分母)、`withMarker` はそのうち着手マーカーがあった数、
          `unknownTimeline` は timeline 不完全 (`exclusions.timelineOverflow`)
          により除外されたその repo・月のクローズ issue 数。`coverage` は
          `closedIssues > 0` のとき `withMarker / closedIssues`、
          `closedIssues == 0` のとき `None` (「マーカーが 1 件も無かった
          (観測済み 0%)」と「timeline 不完全等により観測不能」を区別する。
          timeline が完全な既知 issue が存在し着手マーカーが 0 件のときは
          従来どおり `0.0` になる)。overflow issue しか存在しない repo × 月
          でも行を省略せず出力する (`closedIssues: 0`、`unknownTimeline` は
          その件数、`coverage: None`)。`since` によるフィルタは適用しない
          (全期間。比較可能な coverage 期間の判定材料とするため意図的に
          除外する)。
        - `claimDetection` (dict): `{"strictIssues": int, "looseOnlyIssues":
          list[dict]}`。`strictIssues` は `strict_sources` が 1 件以上ある
          (= `first_start is not None`) issue の件数 (issue 単位で重複排除)。
          `looseOnlyIssues` は `loose_only_matches` を issue 横断でフラットに
          列挙した `{"repo", "issue", "commentCreatedAt"}` のリスト
          (comment 単位。同一 issue に複数の loose-only comment があれば
          複数エントリになる。当該 issue が strict でも判定済みかどうかは問わない)。
          `exclusions.timelineOverflow` に列挙される issue (timeline 不完全)
          は、部分的な timeline から着手判定を行うと誤判定になりうるため、
          `strictIssues` の分母にも `looseOnlyIssues` の列挙対象にも含めない。
        - `exclusions` (dict): `{"timelineOverflow": list[dict],
          "prTimelineOverflow": list[dict], "prReadyTimeUnknown": list[dict],
          "mergedCloserPrNotQualifying": list[dict]}`。
          `timelineOverflow` は `timelineItems.totalCount > len(timelineItems.nodes)`
          だった issue を `{"repo", "issue", "totalCount", "fetched"}` で列挙する
          (`fetched == len(nodes)`)。これらの issue は `mainSeries` /
          `censored` / `auxiliarySeries` のいずれにも含めない。
          `prTimelineOverflow` は `timelineItems.totalCount > len(timelineItems.nodes)`
          だった **PR** を `{"repo", "pr", "totalCount", "fetched", "linkedIssues"}`
          で列挙する (`fetched == len(nodes)`。`linkedIssues` はこの除外により
          `mainSeries` の候補から除外された issue を `{"repo": str, "issue":
          int}` の複合キーで列挙したリスト — `closingIssuesReferences` は
          他 repo の issue を指しうるため issue 番号だけでは一意に特定
          できない。`(repo, issue)` の組で重複排除し、`repo` の辞書順→
          `issue` の昇順で決定的に並べる。該当なしなら空リスト)。
          PR 自体の overflow 判定は `state` (OPEN / MERGED) を問わず同様に
          適用する。`linkedIssues` は最終分類後に確定し、別の qualifying PR
          で `mainSeries` に入った issue や候補パイプライン外の issue は
          含めない。overflow した PR は `prSeries` から除外し、qualifying PR
          候補 (`mainSeries` 対象決定パイプラインのステップ 3 参照) としても
          採用しない (timeline 不完全な PR の ready 時刻は信頼できないため)。
          最終 `ClosedEvent` の closer が overflow PR と一致する issue も、
          同じ理由でステップ 3 の候補集合の情報源として使わない (`mainSeries`
          に入らず、`auxiliarySeries` のいずれのカテゴリにも再分類しない。
          詳細は `mainSeries` docstring の「境界」を参照)。除外された issue は
          `{repo, issue}` として当該 PR の `linkedIssues` に列挙する。
          `prReadyTimeUnknown` は、着手済み issue について completion は証明
          できるが overflow により ready 時刻を特定できず、他の qualifying
          candidate も無い対応関係を `{"repo": str, "issue": int,
          "prRepo": str, "pr": int}` で列挙する。`repo` / `issue` は issue
          側、`prRepo` / `pr` は overflow PR 側の複合キー。同じ issue に
          該当 PR が複数あれば PR ごとに 1 要素とし、辞書順で決定的に並べる。
          これらは completion 済みのため `censored` には含めない。
          `mergedCloserPrNotQualifying` は、`mainSeries` 対象決定パイプライン
          ステップ 3 の 1 つ目の情報源 (最終 `ClosedEvent` の merged closer)
          で特定された PR が、overflow ではないが qualifying completion PR
          の要件 (`resolve_ready(...).ready_at` が非 `None`) を満たさない
          (= 収集時点の PR snapshot が issue timeline より古い、stale) ため
          候補から除外された issue を `{"repo": str, "issue": int, "prRepo":
          str, "pr": int, "snapshotState": str, "snapshotIsDraft": bool}` で
          列挙する。`repo` / `issue` は issue 側の複合キー、`prRepo` / `pr` は
          除外された候補 PR 側の複合キー、`snapshotState` / `snapshotIsDraft`
          は `--prs` 側で収集した当該 PR snapshot の `state` / `isDraft` を
          そのまま転記したもの (fail-closed 判定の根拠を示す)。
          `prTimelineOverflow` と同じ規約で `since` によるフィルタは適用せず
          (全期間)、列挙順は `issues` の入力順に対応する issue 走査順とする
          (repo・issue 番号等でのソートは行わない)。この判定は 2 つ目の情報源
          (`closingIssuesReferences` 逆引き) に qualifying candidate が別途
          見つかるかどうかとは独立に行う — stale な merged closer が除外
          されても、逆引きで別の qualifying PR が見つかればその issue は
          通常どおり `mainSeries` に編入されうる (この場合も
          `exclusions.mergedCloserPrNotQualifying` へのエントリ追加は行う)。
        - `boundaries` (list[dict]): 引数 `boundaries` を `(at, id)` の辞書順
          (`at` 昇順、同一 `at` は `id` の辞書順で tie-break) に正規化した
          `[{"id": str, "label": str, "at": str}]` をそのまま echo したもの
          (`at` は UTC `Z` サフィックス付き ISO8601 文字列に変換する)。
          `boundaries` 引数が `None` または空リストのとき `[]`。正規化は
          `_normalize_boundaries()` を `compute` 内で一度だけ適用し、この
          `boundaries` キーと次の `intervalStats` の区間境界の両方に同じ
          正規化結果を使う (二重に正規化しない・順序がずれない)。
        - `intervalStats` (list[dict]): 上記で正規化した `boundaries` を用い、
          `[開始, b1)`, `[b1, b2)`, ..., `[bn, 終端]` の
          各区間に `mainSeries` と `censored` を `firstStartAt` で割り当てた
          集計。各要素は `{"from": str | None, "to": str | None, "label", "n",
          "medianLeadTimeHours", "medianSizeLines", "smallOnlyMedianLeadTimeHours",
          "smallOnlyN", "censoredN"}`。`from` / `to` は区間端の
          `boundaries[].id` (開始側端が無ければ `from=None`、終端側端が無ければ
          `to=None`)。`label` は区間端の `boundaries[].label` から導出する:
          先頭区間 (`from=None`) は `〜 <to の label>`、内部区間は
          `<from の label> 〜 <to の label>`、末尾区間 (`to=None`) は
          `<from の label> 〜`。`smallOnly*` は `sizeBand == "S"` の要素のみに絞った
          集計。`censoredN` はその区間に `firstStartAt` で割り当てられた
          `censored` 要素数。`medianLeadTimeHours` / `smallOnlyMedianLeadTimeHours`
          は、丸め前の `leadTimeHours` が負の要素を除外して計算する
          (metric 単位の除外。`smallOnlyMedianLeadTimeHours` は `sizeBand ==
          "S"` に絞ったうえで同じ規則を適用する。該当区間の対象要素が
          0 件なら中央値は `null`)。`n` / `smallOnlyN` /
          `medianSizeLines` は `negativeInterval` を除外せず区間内の全
          `mainSeries` (該当帯) 要素で計算する (`weeklyCohorts` と同じ規約)。
          `boundaries` が `None` または空のとき `intervalStats == []`。
        - `dataQuality` (dict): `{"negativeIntervalCount": int, "redraftPrCount":
          int, "notStartedClosedIssues": int, "multipleReadyPrIssues": int}`。
          `negativeIntervalCount` は `mainSeries` 中で `negativeInterval ==
          True` だったレコード件数 (複数区間が同時に負でも 1 件として数える。
          `negativeInterval` の判定規則は `mainSeries` 要素の docstring を
          参照)。`redraftPrCount` は `prSeries` 中で `redraftCount > 0` だった
          PR 件数。`notStartedClosedIssues` は `state == "CLOSED"` かつ
          着手マーカーが無かった (`first_start is None`) issue の件数
          (`since` フィルタは適用しない。`exclusions.timelineOverflow` の
          issue は timeline 不完全から「着手マーカー無し」と断定できない
          ため分母から除外する)。`multipleReadyPrIssues` は、`since` フィルタ
          適用後の `mainSeries` に残った issue のうち、`mainSeries` 対象決定
          パイプラインのステップ 3 で構築した qualifying PR 候補集合が複数件
          だった (= `readyAt` 最小の PR を採用した) ものの件数 (issue の
          `state` が OPEN/CLOSED いずれでも、また選択された PR の状態や
          `completionBasis` によらずカウントする)。

    境界:
        - `since` の inclusive filter は `firstStartAt` (UTC date 部分)
          `>= since` で判定し、`mainSeries` / `censored` / `auxiliarySeries` /
          `weeklyCohorts` に適用する (`weeklyCohorts` はフィルタ後の
          `mainSeries` から算出されるため間接的に適用される。
          `dataQuality.multipleReadyPrIssues` も同様にフィルタ後の
          `mainSeries` から算出されるため間接的に適用される)。
        - 数値の丸めはすべて時間 (hours) 単位・小数第 2 位まで
          (Python 組み込み `round`、round half-even)。丸めは最終出力時に
          1 回だけ適用する (中央値は丸め済みの出力値ではなく未丸めの raw 値
          から計算し、その中央値自体を最後に 1 回だけ丸める)。
        - `n = 0` の集計オブジェクトでも、そのキー自体は出力から省略しない
          (値を `null` にする。例: 該当週の全件が `negativeInterval` のときの
          `medianLeadTimeHours`)。
        - 本関数は `issues` / `prs` の必須フィールド欠落を検証しない
          (JSONL の読み込み・検証は `main` の責務。本関数は事前に検証済みの
          データが渡されることを前提とする)。
    """
    since_ok = _make_since_filter(since)

    prs_by_key = {(pr["repo"], pr["number"]): pr for pr in prs}

    pr_overflow_set: set[tuple[str, int]] = set()
    pr_overflow_info: dict[tuple[str, int], dict] = {}
    pr_ready_info: dict[tuple[str, int], ReadyResolution] = {}
    closing_ref_index: dict[tuple[str, int], list[tuple[str, int]]] = defaultdict(list)
    overflow_closing_ref_index: dict[
        tuple[str, int], list[tuple[str, int]]
    ] = defaultdict(list)
    # overflow で continue しなかった PR のうち ready 到達済み (qualifying) の
    # ものだけを集めた集合。merged-closer 経路 (下の issue 走査ループ) と
    # closingIssuesReferences 逆引き経路 (`closing_ref_index` への登録) の
    # 両方がこの同じ判定を参照するため、qualifying 要件がずれない。
    qualifying_pr_keys: set[tuple[str, int]] = set()

    for pr in prs:
        pr_key = (pr["repo"], pr["number"])
        total_count = pr["timelineItems"]["totalCount"]
        fetched = len(pr["timelineItems"]["nodes"])
        if total_count > fetched:
            pr_overflow_set.add(pr_key)
            pr_overflow_info[pr_key] = {"totalCount": total_count, "fetched": fetched}
            for ref in pr["closingIssuesReferences"]["nodes"]:
                ref_key = (ref["repository"]["nameWithOwner"], ref["number"])
                overflow_closing_ref_index[ref_key].append(pr_key)
            continue

        ready_resolution = resolve_ready(pr)
        pr_ready_info[pr_key] = ready_resolution
        if ready_resolution.ready_at is not None:
            qualifying_pr_keys.add(pr_key)
            for ref in pr["closingIssuesReferences"]["nodes"]:
                ref_key = (ref["repository"]["nameWithOwner"], ref["number"])
                closing_ref_index[ref_key].append(pr_key)

    pr_series = _build_pr_series(prs, pr_overflow_set, pr_ready_info, since)

    timeline_overflow_out: list[dict] = []
    merged_closer_not_qualifying_out: list[dict] = []
    pr_timeline_linked_issues: dict[
        tuple[str, int], set[tuple[str, int]]
    ] = defaultdict(set)
    pr_ready_time_unknown_out: list[dict] = []
    coverage_counts: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"closedIssues": 0, "withMarker": 0, "unknownTimeline": 0}
    )
    strict_issue_count = 0
    loose_only_entries: list[dict] = []
    not_started_closed_issues = 0

    main_records_all: list[_MainSeriesRecord] = []
    censored_all: list[tuple[datetime, dict]] = []
    aux_all: dict[str, list[tuple[datetime, dict]]] = {
        "manualClose": [],
        "commitClose": [],
        "notPlanned": [],
        "unmergedPr": [],
        "externalMergedClose": [],
    }
    category_to_aux_key = {
        "manual": "manualClose",
        "commit": "commitClose",
        "unmerged_pr": "unmergedPr",
        "merged_pr_external": "externalMergedClose",
    }

    for issue in issues:
        total_count = issue["timelineItems"]["totalCount"]
        nodes = issue["timelineItems"]["nodes"]
        fetched = len(nodes)
        overflow = total_count > fetched

        coverage_bucket = None
        if issue["state"] == "CLOSED" and issue.get("closedAt"):
            month = _parse_datetime(issue["closedAt"]).strftime("%Y-%m")
            coverage_bucket = coverage_counts[(issue["repo"], month)]
            if overflow:
                coverage_bucket["unknownTimeline"] += 1

        if overflow:
            timeline_overflow_out.append(
                {
                    "repo": issue["repo"],
                    "issue": issue["number"],
                    "totalCount": total_count,
                    "fetched": fetched,
                }
            )
            continue

        if coverage_bucket is not None:
            coverage_bucket["closedIssues"] += 1

        start_resolution = resolve_start(issue, patterns)

        if start_resolution.first_start is not None:
            strict_issue_count += 1
            if coverage_bucket is not None:
                coverage_bucket["withMarker"] += 1

        for match in start_resolution.loose_only_matches:
            loose_only_entries.append(
                {
                    "repo": issue["repo"],
                    "issue": issue["number"],
                    "commentCreatedAt": _format_datetime(match["commentCreatedAt"]),
                }
            )

        if issue["state"] == "CLOSED" and start_resolution.first_start is None:
            not_started_closed_issues += 1

        if start_resolution.first_start is None:
            continue

        first_start = start_resolution.first_start
        last_start = start_resolution.last_start
        start_week = first_start.date().strftime("%G-W%V")

        if issue.get("stateReason") == "NOT_PLANNED":
            aux_all["notPlanned"].append(
                (first_start, _aux_entry(issue, first_start, last_start, start_week))
            )
            continue

        linkage = resolve_close_linkage(issue, prs_by_key)

        candidates: set[tuple[str, int]] = set()
        completion_proven_overflow_candidates: set[tuple[str, int]] = set()
        if linkage.category == "merged_pr":
            assert linkage.linked_pr is not None
            candidate_key = (linkage.linked_pr["repo"], linkage.linked_pr["number"])
            if candidate_key in qualifying_pr_keys:
                candidates.add(candidate_key)
            elif candidate_key in pr_overflow_set:
                # issue timeline の merged closer 自体が completion を証明する。
                # overflow で ready 時刻だけを信頼できない候補として保持する。
                completion_proven_overflow_candidates.add(candidate_key)
            else:
                # 非 overflow かつ非 qualifying = 収集時点の PR snapshot が
                # issue timeline より古い (stale)。fail-closed に候補から
                # 除外し exclusions.mergedCloserPrNotQualifying に列挙する。
                stale_pr = prs_by_key[candidate_key]
                merged_closer_not_qualifying_out.append(
                    {
                        "repo": issue["repo"],
                        "issue": issue["number"],
                        "prRepo": stale_pr["repo"],
                        "pr": stale_pr["number"],
                        "snapshotState": stale_pr["state"],
                        "snapshotIsDraft": stale_pr["isDraft"],
                    }
                )
        for candidate_key in closing_ref_index.get(
            (issue["repo"], issue["number"]), []
        ):
            candidates.add(candidate_key)
        for candidate_key in overflow_closing_ref_index.get(
            (issue["repo"], issue["number"]), []
        ):
            overflow_pr = prs_by_key[candidate_key]
            if overflow_pr["state"] == "MERGED" or (
                overflow_pr["state"] == "OPEN" and not overflow_pr["isDraft"]
            ):
                # MERGED または OPEN non-draft なら ready 到達済みであることは
                # snapshot state から証明できるが、不完全 timeline から readyAt
                # 自体は特定できない。
                completion_proven_overflow_candidates.add(candidate_key)

        if candidates:
            chosen_key = min(
                candidates,
                key=lambda key: (pr_ready_info[key].ready_at, key[0], key[1]),
            )
            record = _build_main_record(
                issue,
                first_start,
                last_start,
                start_week,
                prs_by_key[chosen_key],
                pr_ready_info[chosen_key],
                len(candidates) > 1,
            )
            main_records_all.append(record)
        elif completion_proven_overflow_candidates:
            issue_key = (issue["repo"], issue["number"])
            for candidate_key in sorted(completion_proven_overflow_candidates):
                pr_timeline_linked_issues[candidate_key].add(issue_key)
                pr_ready_time_unknown_out.append(
                    {
                        "repo": issue["repo"],
                        "issue": issue["number"],
                        "prRepo": candidate_key[0],
                        "pr": candidate_key[1],
                    }
                )
        elif issue["state"] == "OPEN":
            censored_all.append(
                (
                    first_start,
                    {
                        "repo": issue["repo"],
                        "issue": issue["number"],
                        "firstStartAt": _format_datetime(first_start),
                        "startWeek": start_week,
                        "elapsedHoursLowerBound": round(_hours(first_start, as_of), 2),
                    },
                )
            )
        elif issue["state"] == "CLOSED":
            aux_key = category_to_aux_key.get(linkage.category)
            if aux_key is not None:
                aux_all[aux_key].append(
                    (
                        first_start,
                        _aux_entry(issue, first_start, last_start, start_week),
                    )
                )
            # category == "merged_pr" はここに到達しない限り起こらない
            # (候補集合の 1 つ目の情報源と同じ判定であり、候補が無いことは
            # category != "merged_pr" を含意する)。overflow または非
            # qualifying (stale snapshot) の除外により到達した場合は、
            # どの系列にも含めず除外されたままにする
            # (mainSeries docstring の「境界」参照)。

    pr_timeline_overflow_out = _build_pr_timeline_overflow(
        prs,
        pr_overflow_set,
        pr_overflow_info,
        pr_timeline_linked_issues,
    )
    pr_ready_time_unknown_out.sort(
        key=lambda entry: (
            entry["repo"],
            entry["issue"],
            entry["prRepo"],
            entry["pr"],
        )
    )

    main_records = [
        record for record in main_records_all if since_ok(record.raw_first_start)
    ]
    censored_out = [entry for at, entry in censored_all if since_ok(at)]
    aux_out = {
        key: [entry for at, entry in value if since_ok(at)]
        for key, value in aux_all.items()
    }

    repos_out = _build_repos(issues, prs)
    marker_coverage_out = _build_marker_coverage(coverage_counts)
    weekly_cohorts_out = _build_weekly_cohorts(main_records, censored_out)
    normalized_boundaries = _normalize_boundaries(boundaries)
    boundaries_out = [
        {
            "id": boundary["id"],
            "label": boundary["label"],
            "at": _format_datetime(boundary["at"]),
        }
        for boundary in normalized_boundaries
    ]
    interval_stats_out = _build_interval_stats(
        normalized_boundaries, main_records, censored_out
    )

    negative_interval_count = sum(
        1 for record in main_records if record.output["negativeInterval"]
    )
    redraft_pr_count = sum(1 for entry in pr_series if entry["redraftCount"] > 0)
    multiple_ready_pr_issues = sum(
        1 for record in main_records if record.multiple_candidates
    )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "asOf": _format_datetime(as_of),
        "since": since.isoformat() if since is not None else None,
        "sizeBands": [
            {"band": band, "min": minimum, "max": maximum}
            for band, minimum, maximum in SIZE_BANDS
        ],
        "repos": repos_out,
        "mainSeries": [record.output for record in main_records],
        "censored": censored_out,
        "auxiliarySeries": aux_out,
        "prSeries": pr_series,
        "weeklyCohorts": weekly_cohorts_out,
        "markerCoverage": marker_coverage_out,
        "claimDetection": {
            "strictIssues": strict_issue_count,
            "looseOnlyIssues": loose_only_entries,
        },
        "exclusions": {
            "timelineOverflow": timeline_overflow_out,
            "prTimelineOverflow": pr_timeline_overflow_out,
            "prReadyTimeUnknown": pr_ready_time_unknown_out,
            "mergedCloserPrNotQualifying": merged_closer_not_qualifying_out,
        },
        "boundaries": boundaries_out,
        "intervalStats": interval_stats_out,
        "dataQuality": {
            "negativeIntervalCount": negative_interval_count,
            "redraftPrCount": redraft_pr_count,
            "notStartedClosedIssues": not_started_closed_issues,
            "multipleReadyPrIssues": multiple_ready_pr_issues,
        },
    }


def _make_since_filter(since: date | None):
    """`since` inclusive filter (`firstStartAt` の UTC date `>= since`) の判定関数を返す。"""

    def _predicate(first_start: datetime) -> bool:
        return since is None or first_start.date() >= since

    return _predicate


def _hours(start: datetime, end: datetime) -> float:
    """`start` から `end` までの経過時間を時間単位 (丸め前) で返す。"""
    return (end - start).total_seconds() / 3600


def _median_rounded(values: list[float]) -> float | None:
    """`values` の中央値を小数第 2 位に丸めて返す (空リストなら `None`)。"""
    if not values:
        return None
    return round(statistics.median(values), 2)


def _aux_entry(
    issue: dict, first_start: datetime, last_start: datetime, start_week: str
) -> dict:
    """`auxiliarySeries` 各カテゴリ共通の確定契約フィールドを持つ要素を組み立てる。"""
    return {
        "repo": issue["repo"],
        "issue": issue["number"],
        "firstStartAt": _format_datetime(first_start),
        "lastStartAt": _format_datetime(last_start),
        "startWeek": start_week,
    }


@dataclass(frozen=True)
class _MainSeriesRecord:
    """`mainSeries` 1 要素の出力 dict と、集計に必要な丸め前 (raw) の値を束ねる内部表現。

    `multiple_candidates` は `dataQuality.multipleReadyPrIssues` を `since`
    フィルタ後の `mainSeries` から算出するための内部専用フラグであり、公開
    出力 (`output`、`mainSeries` の要素 dict) には含めない。
    """

    output: dict
    raw_first_start: datetime
    raw_lead_time_hours: float
    raw_start_to_pr_created: float
    raw_pr_created_to_ready: float
    raw_ready_to_merge: float | None
    multiple_candidates: bool


def _build_main_record(
    issue: dict,
    first_start: datetime,
    last_start: datetime,
    start_week: str,
    pr: dict,
    ready_resolution: ReadyResolution,
    multiple_candidates: bool,
) -> _MainSeriesRecord:
    """選択された qualifying completion PR から `mainSeries` 1 要素を組み立てる。

    Args:
        multiple_candidates: 呼び出し側が構築した qualifying PR 候補集合が
            複数件だったかどうか (`len(candidates) > 1`)。公開出力には
            含めず、`dataQuality.multipleReadyPrIssues` を `since` フィルタ
            後の `mainSeries` から算出するための内部専用フラグとして
            `_MainSeriesRecord.multiple_candidates` に転記する。
    """
    pr_created_at = _parse_datetime(pr["createdAt"])
    ready_at = ready_resolution.ready_at
    assert ready_at is not None

    size_lines = pr["additions"] + pr["deletions"]
    size_band = _size_band(size_lines)

    raw_start_to_pr_created = _hours(first_start, pr_created_at)
    raw_pr_created_to_ready = _hours(pr_created_at, ready_at)
    raw_lead_time = _hours(first_start, ready_at)

    if pr["state"] == "MERGED":
        completion_basis = "merged"
        merged_at = _parse_datetime(pr["mergedAt"])
        raw_ready_to_merge = _hours(ready_at, merged_at)
    else:
        completion_basis = "ready_unmerged"
        merged_at = None
        raw_ready_to_merge = None

    negative_interval = any(
        value is not None and value < 0
        for value in (
            raw_start_to_pr_created,
            raw_pr_created_to_ready,
            raw_ready_to_merge,
            raw_lead_time,
        )
    )

    output = {
        "repo": issue["repo"],
        "issue": issue["number"],
        "firstStartAt": _format_datetime(first_start),
        "lastStartAt": _format_datetime(last_start),
        "startWeek": start_week,
        "pr": pr["number"],
        "prRepo": pr["repo"],
        "prCreatedAt": _format_datetime(pr_created_at),
        "readyAt": _format_datetime(ready_at),
        "mergedAt": _format_datetime(merged_at) if merged_at is not None else None,
        "completionBasis": completion_basis,
        "leadTimeHours": round(raw_lead_time, 2),
        "phaseHours": {
            "startToPrCreated": round(raw_start_to_pr_created, 2),
            "prCreatedToReady": round(raw_pr_created_to_ready, 2),
            "readyToMerge": round(raw_ready_to_merge, 2)
            if raw_ready_to_merge is not None
            else None,
        },
        "sizeBand": size_band,
        "sizeLines": size_lines,
        "viaDraft": ready_resolution.via_draft,
        "redraftCount": ready_resolution.redraft_count,
        "negativeInterval": negative_interval,
    }
    return _MainSeriesRecord(
        output=output,
        raw_first_start=first_start,
        raw_lead_time_hours=raw_lead_time,
        raw_start_to_pr_created=raw_start_to_pr_created,
        raw_pr_created_to_ready=raw_pr_created_to_ready,
        raw_ready_to_merge=raw_ready_to_merge,
        multiple_candidates=multiple_candidates,
    )


def _build_pr_series(
    prs: list[dict],
    pr_overflow_set: set[tuple[str, int]],
    pr_ready_info: dict[tuple[str, int], ReadyResolution],
    since: date | None,
) -> list[dict]:
    """ready 到達済み PR (overflow を除く) から `prSeries` を組み立てる。"""
    entries = []
    for pr in prs:
        pr_key = (pr["repo"], pr["number"])
        if pr_key in pr_overflow_set:
            continue
        ready_resolution = pr_ready_info[pr_key]
        if ready_resolution.ready_at is None:
            continue

        created_at = _parse_datetime(pr["createdAt"])
        if since is not None and created_at.date() < since:
            continue

        merged_at = _parse_datetime(pr["mergedAt"]) if pr["state"] == "MERGED" else None
        size_lines = pr["additions"] + pr["deletions"]

        entries.append(
            {
                "repo": pr["repo"],
                "pr": pr["number"],
                "state": pr["state"],
                "createdAt": _format_datetime(created_at),
                "readyAt": _format_datetime(ready_resolution.ready_at),
                "mergedAt": _format_datetime(merged_at)
                if merged_at is not None
                else None,
                "createdToReadyHours": round(
                    _hours(created_at, ready_resolution.ready_at), 2
                ),
                "readyToMergeHours": (
                    round(_hours(ready_resolution.ready_at, merged_at), 2)
                    if merged_at is not None
                    else None
                ),
                "sizeBand": _size_band(size_lines),
                "sizeLines": size_lines,
                "viaDraft": ready_resolution.via_draft,
                "redraftCount": ready_resolution.redraft_count,
            }
        )
    return entries


def _build_pr_timeline_overflow(
    prs: list[dict],
    pr_overflow_set: set[tuple[str, int]],
    pr_overflow_info: dict[tuple[str, int], dict],
    linked_issues: dict[tuple[str, int], set[tuple[str, int]]],
) -> list[dict]:
    """timeline 不完全だった PR と、実際に候補除外された issue を組み立てる。"""
    entries = []
    for pr in prs:
        pr_key = (pr["repo"], pr["number"])
        if pr_key not in pr_overflow_set:
            continue
        info = pr_overflow_info[pr_key]
        linked = sorted(linked_issues.get(pr_key, set()))
        entries.append(
            {
                "repo": pr["repo"],
                "pr": pr["number"],
                "totalCount": info["totalCount"],
                "fetched": info["fetched"],
                "linkedIssues": [
                    {"repo": repo, "issue": number} for repo, number in linked
                ],
            }
        )
    return entries


def _build_repos(issues: list[dict], prs: list[dict]) -> list[dict]:
    """repo 別の issue/PR 集計 (`repos`、`since` フィルタ非適用) を組み立てる。"""
    repo_names = sorted(
        {issue["repo"] for issue in issues} | {pr["repo"] for pr in prs}
    )
    entries = []
    for repo in repo_names:
        repo_issues = [issue for issue in issues if issue["repo"] == repo]
        repo_prs = [pr for pr in prs if pr["repo"] == repo]
        entries.append(
            {
                "repo": repo,
                "issues": len(repo_issues),
                "closedIssues": sum(
                    1 for issue in repo_issues if issue["state"] == "CLOSED"
                ),
                "mergedPrs": sum(1 for pr in repo_prs if pr["state"] == "MERGED"),
                "openReadyPrs": sum(
                    1 for pr in repo_prs if pr["state"] == "OPEN" and not pr["isDraft"]
                ),
            }
        )
    return entries


def _build_marker_coverage(coverage_counts: dict[tuple[str, str], dict]) -> list[dict]:
    """repo × 月別の着手マーカー観測状況 (`markerCoverage`) を組み立てる。"""
    entries = []
    for (repo, month), counts in sorted(coverage_counts.items()):
        closed_issues = counts["closedIssues"]
        coverage = (counts["withMarker"] / closed_issues) if closed_issues > 0 else None
        entries.append(
            {
                "repo": repo,
                "month": month,
                "closedIssues": closed_issues,
                "withMarker": counts["withMarker"],
                "unknownTimeline": counts["unknownTimeline"],
                "coverage": coverage,
            }
        )
    return entries


def _build_weekly_cohorts(
    main_records: list[_MainSeriesRecord], censored_out: list[dict]
) -> list[dict]:
    """`startWeek` 別の週次集計 (`weeklyCohorts`) を組み立てる。"""
    weeks = {record.output["startWeek"] for record in main_records}
    weeks |= {entry["startWeek"] for entry in censored_out}

    entries = []
    for week in sorted(weeks):
        week_records = [
            record for record in main_records if record.output["startWeek"] == week
        ]
        week_censored = [entry for entry in censored_out if entry["startWeek"] == week]
        n = len(week_records)

        if n == 0:
            entries.append(
                {
                    "week": week,
                    "n": 0,
                    "medianLeadTimeHours": None,
                    "phaseMedians": {
                        "startToPrCreated": None,
                        "prCreatedToReady": None,
                        "readyToMerge": None,
                    },
                    "bySizeBand": {},
                    "censoredN": len(week_censored),
                    "viaDraftRate": None,
                    "medianSizeLines": None,
                }
            )
            continue

        median_lead_time = _median_rounded(
            [
                record.raw_lead_time_hours
                for record in week_records
                if record.raw_lead_time_hours >= 0
            ]
        )
        phase_medians = {
            "startToPrCreated": _median_rounded(
                [
                    record.raw_start_to_pr_created
                    for record in week_records
                    if record.raw_start_to_pr_created >= 0
                ]
            ),
            "prCreatedToReady": _median_rounded(
                [
                    record.raw_pr_created_to_ready
                    for record in week_records
                    if record.raw_pr_created_to_ready >= 0
                ]
            ),
            "readyToMerge": _median_rounded(
                [
                    record.raw_ready_to_merge
                    for record in week_records
                    if record.raw_ready_to_merge is not None
                    and record.raw_ready_to_merge >= 0
                ]
            ),
        }
        by_size_band = {}
        for band in sorted({record.output["sizeBand"] for record in week_records}):
            band_records = [
                record for record in week_records if record.output["sizeBand"] == band
            ]
            by_size_band[band] = {
                "n": len(band_records),
                "medianLeadTimeHours": _median_rounded(
                    [
                        record.raw_lead_time_hours
                        for record in band_records
                        if record.raw_lead_time_hours >= 0
                    ]
                ),
            }
        via_draft_rate = (
            sum(1 for record in week_records if record.output["viaDraft"]) / n
        )
        median_size_lines = statistics.median(
            [record.output["sizeLines"] for record in week_records]
        )

        entries.append(
            {
                "week": week,
                "n": n,
                "medianLeadTimeHours": median_lead_time,
                "phaseMedians": phase_medians,
                "bySizeBand": by_size_band,
                "censoredN": len(week_censored),
                "viaDraftRate": via_draft_rate,
                "medianSizeLines": median_size_lines,
            }
        )
    return entries


def _normalize_boundaries(boundaries: list[dict] | None) -> list[dict]:
    """`boundaries` を `(at, id)` の辞書順に正規化する。"""
    if not boundaries:
        return []
    return sorted(boundaries, key=lambda boundary: (boundary["at"], boundary["id"]))


def _build_interval_stats(
    sorted_boundaries: list[dict],
    main_records: list[_MainSeriesRecord],
    censored_out: list[dict],
) -> list[dict]:
    """正規化済み `boundaries` (呼び出し側で `_normalize_boundaries` 適用済み。
    `compute` は `boundaries` 出力キーと本関数とで同じ正規化結果を共有する)
    から `intervalStats` (区間別集計) を組み立てる。
    """
    if not sorted_boundaries:
        return []

    boundary_ats = [boundary["at"] for boundary in sorted_boundaries]

    def _interval_index(at: datetime) -> int:
        return bisect_right(boundary_ats, at)

    interval_count = len(sorted_boundaries) + 1
    main_buckets: list[list[_MainSeriesRecord]] = [[] for _ in range(interval_count)]
    for record in main_records:
        main_buckets[_interval_index(record.raw_first_start)].append(record)

    censored_buckets: list[list[dict]] = [[] for _ in range(interval_count)]
    for entry in censored_out:
        first_start = _parse_datetime(entry["firstStartAt"])
        censored_buckets[_interval_index(first_start)].append(entry)

    entries = []
    for index in range(interval_count):
        from_id = sorted_boundaries[index - 1]["id"] if index > 0 else None
        to_id = (
            sorted_boundaries[index]["id"] if index < len(sorted_boundaries) else None
        )
        if from_id is None:
            label = f"〜 {sorted_boundaries[0]['label']}"
        elif to_id is None:
            label = f"{sorted_boundaries[-1]['label']} 〜"
        else:
            label = f"{sorted_boundaries[index - 1]['label']} 〜 {sorted_boundaries[index]['label']}"

        bucket_records = main_buckets[index]
        small_only_records = [
            record for record in bucket_records if record.output["sizeBand"] == "S"
        ]

        entries.append(
            {
                "from": from_id,
                "to": to_id,
                "label": label,
                "n": len(bucket_records),
                "medianLeadTimeHours": _median_rounded(
                    [
                        record.raw_lead_time_hours
                        for record in bucket_records
                        if record.raw_lead_time_hours >= 0
                    ]
                ),
                "medianSizeLines": (
                    statistics.median(
                        [record.output["sizeLines"] for record in bucket_records]
                    )
                    if bucket_records
                    else None
                ),
                "smallOnlyMedianLeadTimeHours": _median_rounded(
                    [
                        record.raw_lead_time_hours
                        for record in small_only_records
                        if record.raw_lead_time_hours >= 0
                    ]
                ),
                "smallOnlyN": len(small_only_records),
                "censoredN": len(censored_buckets[index]),
            }
        )
    return entries


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
              len(closingIssuesReferences.nodes)` が 1 件でも存在する場合・
              `--boundaries-file` の検証失敗。詳細はモジュール docstring
              「exit code 契約」セクションを参照)。
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
    args = parse_args(argv)

    try:
        patterns = load_claim_patterns(args.claim_patterns_file)
    except ClaimPatternsError as exc:
        print(f"claim patterns file の契約に違反しています: {exc}", file=sys.stderr)
        return 3

    try:
        as_of = _parse_datetime(args.as_of)
    except ValueError as exc:
        print(f"--as-of の形式が不正です: {exc}", file=sys.stderr)
        return 2

    since: date | None = None
    if args.since is not None:
        try:
            since = date.fromisoformat(args.since)
        except ValueError as exc:
            print(f"--since の形式が不正です: {exc}", file=sys.stderr)
            return 2

    boundaries: list[dict] | None = None
    if args.boundaries_file is not None:
        try:
            boundaries = _load_boundaries_file(args.boundaries_file)
        except ValueError as exc:
            print(f"--boundaries-file の検証に失敗しました: {exc}", file=sys.stderr)
            return 2

    try:
        issues = _read_jsonl(args.issues)
        prs = _read_jsonl(args.prs)
        _validate_closing_issues_complete(prs)
    except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        print(f"入力データの読み込みに失敗しました: {exc}", file=sys.stderr)
        return 2

    try:
        result = compute(issues, prs, patterns, as_of, since, boundaries)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"入力データの処理中にエラーが発生しました: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result))
    return 0


def _read_jsonl(path: Path) -> list[dict]:
    """JSONL ファイルを 1 行ずつ `json.loads` して dict のリストへ変換する (空行は無視)。"""
    with path.open("r", encoding="utf-8") as jsonl_file:
        lines = jsonl_file.readlines()
    rows = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        rows.append(json.loads(stripped))
    return rows


def _validate_closing_issues_complete(prs: list[dict]) -> None:
    """`--prs` の各行が `closingIssuesReferences` を完全に取得済みであることを検証する。"""
    for pr in prs:
        refs = pr["closingIssuesReferences"]
        if refs["totalCount"] > len(refs["nodes"]):
            raise ValueError(
                "closingIssuesReferences のページングが未完了です "
                f"(repo={pr.get('repo')}, pr={pr.get('number')})"
            )


def _load_boundaries_file(path: Path) -> list[dict]:
    """`--boundaries-file` を読み込み検証する。戻り値の `at` は aware `datetime`。"""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"boundaries file を読み込めません ({path}): {exc}") from exc
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"boundaries file が JSON として parse できません ({path}): {exc}"
        ) from exc
    if not isinstance(data, list):
        raise ValueError("boundaries file のトップレベルはリストである必要があります")

    seen_ids: set[str] = set()
    boundaries = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("boundaries の要素は object である必要があります")
        boundary_id = item.get("id")
        label = item.get("label")
        at_raw = item.get("at")
        if not isinstance(boundary_id, str) or not boundary_id:
            raise ValueError("boundaries の id は非空文字列である必要があります")
        if not isinstance(label, str) or not label:
            raise ValueError("boundaries の label は非空文字列である必要があります")
        if not isinstance(at_raw, str):
            raise ValueError("boundaries の at は文字列である必要があります")
        try:
            at = _parse_datetime(at_raw)
        except ValueError as exc:
            raise ValueError(f"boundaries の at が不正です ({at_raw}): {exc}") from exc
        if boundary_id in seen_ids:
            raise ValueError(f"boundaries の id が重複しています: {boundary_id}")
        seen_ids.add(boundary_id)
        boundaries.append({"id": boundary_id, "label": label, "at": at})
    return boundaries


if __name__ == "__main__":
    sys.exit(main())
