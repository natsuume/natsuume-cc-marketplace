// stale-broker-guard.mjs
//
// codex CLI の更新より前に起動した codex companion の常駐 broker を検出して止める guard。
//
// ## 目的
//
// codex companion (`codex-companion.mjs`) は workspace ごとに常駐 broker
// (`app-server-broker.mjs`) を起動し、その記録 (`broker.json`) に接続できる限り同じ broker を
// 再利用する。broker は起動時の `codex app-server` を子孫プロセスとして抱え続けるため、codex CLI
// を更新しても、更新前に起動した broker は古いバイナリのまま review / task を実行する。古い
// バイナリは新しい model を拒否することがあり、その場合 review が失敗する。
//
// broker は `initialize` を自分で処理し固定の userAgent を返すため、broker 経由で app-server の
// バージョンを知る方法は無い。そこで本 guard は、broker 配下の app-server の実行ファイルが broker
// の起動より後に更新されていれば、その broker を「古い」と判定して止める。止めた後に wrapper が
// companion を起動すると、companion は現行のバイナリで新しい broker を起動する。
//
// wrapper (pre-push / pre-merge の codex review wrapper、cross-model-advisor の job / advisor
// wrapper) は companion の `review` / `adversarial-review` / `task` を起動する直前に本 guard を
// 1 回実行する。同じ broker で別の job が実行中でも止める (その job の中断は許容する)。
//
// ## CLI の入出力契約
//
//   node stale-broker-guard.mjs <codex-companion.mjs の絶対パス>
//
// - cwd: `process.cwd()` を workspace として使う。wrapper は companion を起動するときと同じ cwd で
//   本 guard を実行する
// - env: `process.env` を使う
// - 終了コード: 常に 0。wrapper は guard の結果で review を止めない
// - stdout: 何も書かない (companion の stdout を呼び出し側へそのまま渡す wrapper や、ファイルへ
//   書き出す wrapper があるため)
// - stderr: 各行を `[stale-broker-guard] ` で始める。警告は `[stale-broker-guard] warning: ` で
//   始める。古い broker を止めたときは、更新前の codex で動いていた broker を止めた旨と
//   `pid=<broker の pid>` を含む 1 行を出す。何もしなかった場合は何も出さない
//
// ## 判定手順
//
// 1. companion と同じディレクトリの `lib/broker-lifecycle.mjs` を動的 import する。import の
//    直後に `loadBrokerSession` / `sendBrokerShutdown` / `clearBrokerSession` の 3 つが関数として
//    存在することを確認する (環境変数の確認や broker の記録の読み込みより前に行う)
// 2. 環境変数 `CODEX_COMPANION_APP_SERVER_ENDPOINT` が設定されていれば何もせず終える
//    (companion はその endpoint に直接接続し、broker の記録を参照しないため)
// 3. `loadBrokerSession(cwd)` が null (記録なし) なら何もせず終える
// 4. 記録の `pid` のプロセスが生きていなければ何もせず終える
// 5. `ps -A -o pid=,ppid=,args=` の出力から broker の子孫プロセスを再帰的にたどり
//    (`findAppServerExecutables`)、引数列の `app-server` トークンの直前のトークンを app-server の
//    実行ファイルのパスとして集める
// 6. `ps -o etime= -p <pid>` の `[[dd-]hh:]mm:ss` を秒に直し (`parseElapsedSeconds`)、現在時刻から
//    引いて broker の起動時刻を求める。GNU と BSD の ps の両方にある項目だけを使う
// 7. 各実行ファイルの `fs.statSync` の `mtimeMs` と `ctimeMs` の大きい方を求め、1 つでも broker の
//    起動時刻より後であれば古いと判定する (`isBrokerStale`)。ctime も使うのは、展開時に tarball の
//    mtime を残す配布方法でも更新を検出するため。古くなければ何もせず終える
// 8. 古い broker には `sendBrokerShutdown(endpoint)` を送る。5 秒以内に pid が終了しなければ
//    SIGTERM を送り、さらに 5 秒待つ
// 9. 終了を確認できたら `clearBrokerSession(cwd)` で記録を消し、stderr に 1 行出す
//
// ## fail-open の方針
//
// 次の場合は stderr に警告を 1 行出し、broker に触れずに (停止を試みた後の場合はそれ以上何もせず)
// 終える。wrapper はその後、従来どおり companion を起動する。
//
// - `lib/broker-lifecycle.mjs` の import に失敗した、または 3 つの関数のいずれかが存在しない
// - `ps` が非ゼロで終了した、または出力を解釈できない
// - 生きている broker の子孫に `app-server` のプロセスが見つからない、または実行ファイルのパスが
//   存在しない
// - 停止を試みた後も broker の pid が残っている
//
// `runStaleBrokerGuard` は例外を外に投げない。

import fs from "node:fs";
import process from "node:process";
import { pathToFileURL } from "node:url";

/**
 * `ps -o etime=` の出力 (`[[dd-]hh:]mm:ss`、前後の空白・改行を許容) を秒数にする。
 *
 * @param {string} text
 * @returns {number | null} 解釈できなければ null
 */
export const parseElapsedSeconds = (text) => {
  throw new Error("not implemented");
};

/**
 * `ps -A -o pid=,ppid=,args=` の出力から brokerPid の子孫プロセス (子・孫・それ以下。broker
 * 自身は含めない) をたどり、引数列を空白で区切ったトークンのうち `app-server` と完全一致する
 * トークンの直前のトークンを実行ファイルのパスとして集める。
 *
 * - `app-server` が先頭トークンのプロセスは除く
 * - `app-server-broker.mjs` のような部分一致は対象外
 * - 解釈できない行 (空行、pid / ppid が数値でない行等) は無視する
 *
 * @param {string} psOutput
 * @param {number} brokerPid
 * @returns {string[]} 重複なし、出現順
 */
export const findAppServerExecutables = (psOutput, brokerPid) => {
  throw new Error("not implemented");
};

/**
 * 実行ファイルの更新時刻のどれか 1 つでも broker の起動時刻より後なら true。
 * 等しい場合と空配列は false。
 *
 * @param {number} brokerStartMs broker の起動時刻 (epoch ミリ秒)
 * @param {number[]} executableTimesMs 各実行ファイルの `max(mtimeMs, ctimeMs)`
 * @returns {boolean}
 */
export const isBrokerStale = (brokerStartMs, executableTimesMs) => {
  throw new Error("not implemented");
};

/**
 * guard 全体の処理 (ファイル先頭の判定手順)。例外を外に投げない。
 *
 * @param {{ companionPath: string, cwd: string, env: NodeJS.ProcessEnv }} options
 * @returns {Promise<void>}
 */
export const runStaleBrokerGuard = async ({ companionPath, cwd, env }) => {
  throw new Error("not implemented");
};

const isInvokedAsCli = () => {
  const entryPath = process.argv[1];
  if (!entryPath) {
    return false;
  }
  try {
    return pathToFileURL(fs.realpathSync(entryPath)).href === import.meta.url;
  } catch {
    return false;
  }
};

if (isInvokedAsCli()) {
  process.exitCode = 0;
}
