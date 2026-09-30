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
// の起動より後に更新されている、または無くなっていれば、その broker を「古い」と判定して止める。
// 止めた後に wrapper が companion を起動すると、companion は現行のバイナリで新しい broker を起動する。
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
// 2. 環境変数 `CODEX_COMPANION_APP_SERVER_ENDPOINT` に空でない値が設定されていれば何もせず終える
//    (companion はその endpoint に直接接続し、broker の記録を参照しないため。空文字列は
//    companion と同じく未設定として扱う)
// 3. `loadBrokerSession(cwd)` が null (記録なし) なら何もせず終える
// 4. 記録の `pid` のプロセスが生きていなければ何もせず終える
// 5. `ps -A -o pid= -o ppid= -o args=` を 1 回実行し、その出力で記録の `pid` のプロセスが
//    companion の broker であること (引数列に basename が `app-server-broker.mjs` のトークンが
//    あること) を確かめる (`isCompanionBrokerProcess`)。broker でなければ、pid の再利用や記録の
//    改ざんで無関係なプロセスを指しているとみなし、シグナルを送らずに fail-open する
// 6. 同じ ps 出力から、broker から codex の app-server のプロセスだけを通ってたどれる子孫を
//    集め (`findAppServerExecutables`)、codex の実行ファイルのトークンを集める。codex の app-server
//    とみなすのは、basename が `codex` のトークンが引数列の先頭 (`codex app-server`) かインタープ
//    リタの直後 (`node /x/bin/codex app-server`) にあり、その次が `app-server` のプロセスだけ。
//    codex が job の中で実行するツールのコマンド (例えば `grep app-server file` や
//    `bash -lc node /x/codex app-server`) と、job のコマンドの配下にある `codex app-server` は
//    数えない。トークンは絶対パスとは限らない
//    (companion は app-server をコマンド名 `codex` で起動するため、ネイティブバイナリの
//    インストールでは `codex app-server` となり、トークンは `codex` だけになる)
// 7. `ps -o etime= -p <pid>` の `[[dd-]hh:]mm:ss` を秒に直し (`parseElapsedSeconds`)、現在時刻から
//    引いて broker の起動時刻を求める。GNU と BSD の ps の両方にある項目だけを使う
// 8. 各トークンを `resolveExecutablePath(token, env.PATH)` で実行ファイルのパスに解決し、
//    `fs.statSync` (symlink の先を見る) の `mtimeMs` と `ctimeMs` の大きい方を求める (現在時刻より
//    後の mtime は使わず ctime だけを見る。未来の mtime で毎回古いと判定しないため)。stat が
//    `ENOENT` で失敗した (実行ファイルが無くなった) 場合は、その実行ファイルを broker の起動より後に
//    更新されたものとみなす (時刻を `Infinity` とする)。1 つでも broker の起動時刻より後であれば
//    古いと判定する (`isBrokerStale`)。ctime も使うのは、展開時に tarball の mtime を残す配布方法
//    でも更新を検出するため。古くなければ何もせず終える。PATH で解決できないトークンが 1 つでも
//    あるか、`ENOENT` 以外の理由で stat に失敗した場合は fail-open する
// 9. 古い broker には `sendBrokerShutdown(endpoint)` を送る。5 秒以内に pid が終了しなければ
//    SIGTERM を送り、さらに 5 秒待つ
// 10. 終了を確認できたら、`loadBrokerSession(cwd)` で記録を読み直す。同じ workspace で並行して
//    動く companion が、その間に新しい broker を起動して記録を書き直していることがあるため。
//    - 記録の `pid` と `endpoint` が止めた broker のものと一致する場合だけ後片付けをする。
//      `teardownBrokerSession` が関数として export されていれば、記録の `endpoint` / `pidFile` /
//      `logFile` / `sessionDir` / `pid` と `killProcess: null` (プロセスには触れない) で呼び、
//      ソケット・pid ファイル・ログファイルと空の session ディレクトリを消す。続けて
//      `clearBrokerSession(cwd)` で記録を消す。`teardownBrokerSession` は必須ではなく、無ければ
//      この後片付けを省く (警告は出さない)
//    - 一致しない場合と記録が無い場合は、記録にも後片付けにも触れない
//    - どちらの場合も、broker を止めたことを知らせる 1 行を stderr に出す
//
// ## fail-open の方針
//
// 次の場合は stderr に警告を 1 行出し、broker に触れずに (停止を試みた後の場合はそれ以上何もせず)
// 終える。wrapper はその後、従来どおり companion を起動する。
//
// - `lib/broker-lifecycle.mjs` の import に失敗した、または 3 つの関数のいずれかが存在しない
// - `ps` が非ゼロで終了した、または出力を解釈できない
// - 記録の `pid` のプロセスが companion の broker でない
// - 生きている broker の子孫に codex の `app-server` のプロセスが見つからない
// - `resolveExecutablePath` で解決できないトークンがある、または実行ファイルの stat が `ENOENT`
//   以外の理由 (権限など) で失敗した
// - 停止を試みた後も broker の pid が残っている
//
// `runStaleBrokerGuard` は例外を外に投げない。
//
// ## 既知の制約
//
// - コマンド名だけのトークン (`codex`) は guard 自身の `PATH` で解決する。codex を複数の場所に
//   インストールしていて、broker を起動した環境と guard を実行する環境で `PATH` の順序が違うと、
//   broker が実際に使っているものとは別の実行ファイルの時刻を見る
// - 引数列を空白で区切ってトークンにするため、実行ファイルのパスに空白が含まれると codex の
//   app-server を特定できず、警告を出して続行する


import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

const STDERR_PREFIX = "[stale-broker-guard] ";
const ENDPOINT_ENV = "CODEX_COMPANION_APP_SERVER_ENDPOINT";
const REQUIRED_LIFECYCLE_FUNCTIONS = [
  "loadBrokerSession",
  "sendBrokerShutdown",
  "clearBrokerSession",
];
const APP_SERVER_TOKEN = "app-server";
const CODEX_EXECUTABLE_NAME = "codex";
const BROKER_SCRIPT_NAME = "app-server-broker.mjs";
const SHUTDOWN_WAIT_MS = 5000;
const TERM_WAIT_MS = 5000;
const EXIT_POLL_INTERVAL_MS = 100;
// `ps -A` の出力は process 数に比例して長くなるため、既定の上限 (1 MiB) より大きく取る。
const PS_MAX_BUFFER_BYTES = 16 * 1024 * 1024;

// ---------------------------------------------------------------------------
// 判定に使う純粋関数
// ---------------------------------------------------------------------------

/**
 * `ps -o etime=` の出力 (`[[dd-]hh:]mm:ss`、前後の空白・改行を許容) を秒数にする。
 *
 * @param {string} text
 * @returns {number | null} 解釈できなければ null
 */
export const parseElapsedSeconds = (text) => {
  if (typeof text !== "string") {
    return null;
  }
  const match = text.trim().match(/^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$/);
  if (match === null) {
    return null;
  }
  const [, days = "0", hours = "0", minutes, seconds] = match;
  return (
    Number(days) * 86400 +
    Number(hours) * 3600 +
    Number(minutes) * 60 +
    Number(seconds)
  );
};

/**
 * `ps -A -o pid= -o ppid= -o args=` の 1 行を { pid, ppid, args } にする。解釈できなければ null。
 *
 * @param {string} line
 * @returns {{ pid: number, ppid: number, args: string } | null}
 */
const parsePsLine = (line) => {
  const match = line.trim().match(/^(\d+)\s+(\d+)(?:\s+(.*))?$/);
  if (match === null) {
    return null;
  }
  return { pid: Number(match[1]), ppid: Number(match[2]), args: match[3] ?? "" };
};

/**
 * @param {string} psOutput
 * @returns {{ pid: number, ppid: number, args: string }[]} 解釈できた行だけ (出現順)
 */
const parsePsOutput = (psOutput) =>
  psOutput
    .split("\n")
    .map(parsePsLine)
    .filter((processInfo) => processInfo !== null);

/**
 * rootPid の子孫のうち、rootPid から canEnter を満たすプロセスだけを通ってたどれるものの pid の
 * 集合 (rootPid 自身は含めない)。canEnter を満たさないプロセスとその配下はたどらない。
 *
 * @param {{ pid: number, ppid: number }[]} processes
 * @param {number} rootPid
 * @param {(pid: number) => boolean} canEnter
 * @returns {Set<number>}
 */
const collectDescendantPids = (processes, rootPid, canEnter) => {
  const childrenByParent = new Map();
  for (const { pid, ppid } of processes) {
    if (!childrenByParent.has(ppid)) {
      childrenByParent.set(ppid, []);
    }
    childrenByParent.get(ppid).push(pid);
  }

  const descendants = new Set();
  const pending = [rootPid];
  while (pending.length > 0) {
    const parent = pending.shift();
    for (const child of childrenByParent.get(parent) ?? []) {
      if (child === rootPid || descendants.has(child) || !canEnter(child)) {
        continue;
      }
      descendants.add(child);
      pending.push(child);
    }
  }
  return descendants;
};

/**
 * 引数列を空白で区切ったトークンの配列。
 *
 * @param {string} args
 * @returns {string[]}
 */
const splitArgs = (args) => args.split(/\s+/).filter((token) => token !== "");

/**
 * 引数列が codex の app-server のものなら、その codex のトークンを 1 つ含む配列。そうでなければ
 * 空配列。basename が `codex` のトークンが先頭にあり次が `app-server` の場合 (`codex app-server`)
 * と、2 番目にあり次が `app-server` の場合 (`node /x/bin/codex app-server`) だけを対象にする。
 * `app-server` が先頭トークンの引数列と、組が 3 番目以降にある引数列からは何も取らない。
 *
 * @param {string} args
 * @returns {string[]}
 */
const codexTokensBeforeAppServer = (args) => {
  const tokens = splitArgs(args);
  // codex が実行ファイルそのもの (`codex app-server`) か、インタープリタの直後
  // (`node /x/bin/codex app-server`) にある場合だけを app-server とみなす。シェルのコマンド
  // 文字列の途中にある組 (`bash -lc node /x/codex app-server`) は数えない。
  for (const codexIndex of [0, 1]) {
    if (
      tokens[codexIndex + 1] === APP_SERVER_TOKEN &&
      path.basename(tokens[codexIndex] ?? "") === CODEX_EXECUTABLE_NAME
    ) {
      return [tokens[codexIndex]];
    }
  }
  return [];
};

/**
 * `ps -A -o pid= -o ppid= -o args=` の出力から brokerPid の子孫プロセス (broker 自身は含めない) を
 * たどり、codex の app-server のプロセス (`codexTokensBeforeAppServer` が空でないもの) から
 * codex の実行ファイルのトークンを集める。
 *
 * たどるのは、broker から codex の app-server のプロセス (そのようなトークンを持つプロセス) だけを
 * 通って到達できるものに限る。codex の app-server が job の中で実行したコマンド (bash や python3
 * 等) の配下にある `codex app-server` は、broker が起動したものではないので数えない。
 *
 * トークンは加工せずに返す。絶対パスとは限らず、`codex` のようなコマンド名だけのこともある
 * (パスへの解決は `resolveExecutablePath` が行う)。
 *
 * - basename が `codex` でないトークン (codex が job の中で実行するツールのコマンド、例えば
 *   `grep app-server file` の `grep`) は無視する
 * - `app-server` が先頭トークンのプロセスは除く
 * - `app-server-broker.mjs` のような部分一致は対象外
 * - 解釈できない行 (空行、pid / ppid が数値でない行等) は無視する
 *
 * @param {string} psOutput
 * @param {number} brokerPid
 * @returns {string[]} 重複なし、出現順
 */
export const findAppServerExecutables = (psOutput, brokerPid) => {
  const processes = parsePsOutput(psOutput);
  const codexTokensByPid = new Map(
    processes.map(({ pid, args }) => [pid, codexTokensBeforeAppServer(args)]),
  );
  const chainPids = collectDescendantPids(
    processes,
    brokerPid,
    (pid) => (codexTokensByPid.get(pid) ?? []).length > 0,
  );
  const executables = [];
  for (const { pid } of processes) {
    if (!chainPids.has(pid)) {
      continue;
    }
    for (const token of codexTokensByPid.get(pid)) {
      if (!executables.includes(token)) {
        executables.push(token);
      }
    }
  }
  return executables;
};

/**
 * `ps -A -o pid= -o ppid= -o args=` の出力で、pid のプロセスが companion の broker かどうか。
 * pid の行の引数列に、basename が `app-server-broker.mjs` のトークンがあれば true。
 * pid の行が無い場合と、`my-app-server-broker.mjs` のような部分一致は false。
 *
 * @param {string} psOutput
 * @param {number} pid
 * @returns {boolean}
 */
export const isCompanionBrokerProcess = (psOutput, pid) => {
  const processInfo = parsePsOutput(psOutput).find(
    (candidate) => candidate.pid === pid,
  );
  if (processInfo === undefined) {
    return false;
  }
  return splitArgs(processInfo.args).some(
    (token) => path.basename(token) === BROKER_SCRIPT_NAME,
  );
};

/**
 * candidate が存在する通常ファイル (symlink は解決した先で判定) で実行権限を持つか。
 *
 * @param {string} candidate
 * @returns {boolean}
 */
const isExecutableFile = (candidate) => {
  try {
    if (!fs.statSync(candidate).isFile()) {
      return false;
    }
    fs.accessSync(candidate, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
};

/**
 * `findAppServerExecutables` が返したトークンを実行ファイルのパスに解決する。
 *
 * - token に `/` が含まれる: 絶対パスならそのまま返し、相対パスなら null
 * - token に `/` が含まれない: pathEnv (`PATH` の値。区切りは `path.delimiter`) の各ディレクトリを
 *   先頭から順に見て、`<dir>/<token>` が存在する通常ファイル (symlink は解決した先で判定) で
 *   実行権限があれば、その `<dir>/<token>` を返す (symlink の先のパスではない)。見つからなければ
 *   null
 * - pathEnv が空文字列や undefined なら null
 *
 * @param {string} token
 * @param {string | undefined} pathEnv
 * @returns {string | null}
 */
export const resolveExecutablePath = (token, pathEnv) => {
  if (typeof token !== "string" || token === "") {
    return null;
  }
  if (token.includes("/")) {
    return path.isAbsolute(token) ? token : null;
  }
  if (typeof pathEnv !== "string" || pathEnv === "") {
    return null;
  }
  for (const directory of pathEnv.split(path.delimiter)) {
    if (directory === "") {
      continue;
    }
    const candidate = path.join(directory, token);
    if (isExecutableFile(candidate)) {
      return candidate;
    }
  }
  return null;
};

/**
 * 実行ファイルの更新時刻のどれか 1 つでも broker の起動時刻より後なら true。
 * 等しい場合と空配列は false。
 *
 * @param {number} brokerStartMs broker の起動時刻 (epoch ミリ秒)
 * @param {number[]} executableTimesMs 各実行ファイルの `max(mtimeMs, ctimeMs)`
 * @returns {boolean}
 */
export const isBrokerStale = (brokerStartMs, executableTimesMs) =>
  executableTimesMs.some((timeMs) => timeMs > brokerStartMs);

// ---------------------------------------------------------------------------
// 外部とのやり取り (stderr・プロセス・ファイル)
// ---------------------------------------------------------------------------

/**
 * guard の失敗を表す。message は警告 1 行として stderr に出す。
 */
class GuardWarning extends Error {}

/**
 * stderr に 1 行書く。プロセスを終える直前でも出力が欠けないよう同期書き込みにする。
 * 改行を含む message は 1 行にまとめる。
 *
 * @param {string} message
 */
const writeStderrLine = (message) => {
  const singleLine = message.replace(/\s*\n\s*/g, " ").trim();
  try {
    fs.writeSync(2, `${STDERR_PREFIX}${singleLine}\n`);
  } catch {
    // stderr が閉じていても guard の結果は変えない。
  }
};

const writeWarning = (message) => {
  writeStderrLine(`warning: ${message}`);
};

const describeError = (error) =>
  error instanceof Error ? error.message : String(error);

const sleep = (ms) =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

/**
 * pid のプロセスが存在するか (権限が無くても存在すれば true)。
 *
 * @param {number} pid
 * @returns {boolean}
 */
const isProcessAlive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
};

/**
 * pid のプロセスが終了するまで最大 timeoutMs 待つ。終了していれば true。
 *
 * @param {number} pid
 * @param {number} timeoutMs
 * @returns {Promise<boolean>}
 */
const waitForProcessExit = async (pid, timeoutMs) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!isProcessAlive(pid)) {
      return true;
    }
    await sleep(EXIT_POLL_INTERVAL_MS);
  }
  return !isProcessAlive(pid);
};

/**
 * `ps` を shell を経由せずに実行し、stdout を返す。失敗したら GuardWarning を投げる。
 *
 * `-ww` を付けて列の幅の制限をなくす。procps の ps は環境変数 `COLUMNS` があると、出力が
 * パイプでも args の列をその幅で切るため。
 *
 * @param {string[]} args
 * @returns {string}
 */
const runPs = (args) => {
  const result = spawnSync("ps", ["-ww", ...args], {
    encoding: "utf8",
    maxBuffer: PS_MAX_BUFFER_BYTES,
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (result.error) {
    throw new GuardWarning(
      `ps ${args.join(" ")} を実行できません: ${describeError(result.error)}`,
    );
  }
  if (result.status !== 0) {
    throw new GuardWarning(
      `ps ${args.join(" ")} が終了コード ${result.status} で失敗しました`,
    );
  }
  return result.stdout;
};

/**
 * companion と同じディレクトリの `lib/broker-lifecycle.mjs` を読み込み、必須の関数が揃って
 * いることを確認する。
 *
 * @param {string} companionPath
 * @returns {Promise<Record<string, unknown>>}
 */
const importBrokerLifecycle = async (companionPath) => {
  if (typeof companionPath !== "string" || companionPath === "") {
    throw new GuardWarning("codex-companion.mjs のパスが指定されていません");
  }
  const lifecyclePath = path.join(
    path.dirname(companionPath),
    "lib",
    "broker-lifecycle.mjs",
  );
  let lifecycle;
  try {
    lifecycle = await import(pathToFileURL(lifecyclePath).href);
  } catch (error) {
    throw new GuardWarning(
      `${lifecyclePath} を読み込めません: ${describeError(error)}`,
    );
  }
  const missing = REQUIRED_LIFECYCLE_FUNCTIONS.filter(
    (name) => typeof lifecycle[name] !== "function",
  );
  if (missing.length > 0) {
    throw new GuardWarning(
      `${lifecyclePath} に関数 ${missing.join(" / ")} がありません`,
    );
  }
  return lifecycle;
};

/**
 * 全プロセスの一覧 (`ps -A -o pid= -o ppid= -o args=` の出力) を取る。
 *
 * @returns {string}
 */
const readProcessTable = () => {
  const psOutput = runPs(["-A", "-o", "pid=", "-o", "ppid=", "-o", "args="]);
  if (parsePsOutput(psOutput).length === 0) {
    throw new GuardWarning("ps -A の出力を解釈できません");
  }
  return psOutput;
};

/**
 * 記録の pid が companion の broker であることを確かめる。broker でなければ、無関係な
 * プロセスにシグナルを送らないよう GuardWarning を投げる。
 *
 * @param {string} psOutput
 * @param {number} brokerPid
 */
const ensureCompanionBroker = (psOutput, brokerPid) => {
  if (!isCompanionBrokerProcess(psOutput, brokerPid)) {
    throw new GuardWarning(
      `記録の pid=${brokerPid} のプロセスは companion の broker (${BROKER_SCRIPT_NAME}) ではありません`,
    );
  }
};

/**
 * broker 配下の codex の app-server の実行ファイルのパスを集める。
 *
 * @param {string} psOutput
 * @param {number} brokerPid
 * @param {string | undefined} pathEnv
 * @returns {string[]}
 */
const collectAppServerExecutablePaths = (psOutput, brokerPid, pathEnv) => {
  const tokens = findAppServerExecutables(psOutput, brokerPid);
  if (tokens.length === 0) {
    throw new GuardWarning(
      `broker (pid=${brokerPid}) の子孫に codex の app-server のプロセスが見つかりません`,
    );
  }
  return tokens.map((token) => {
    const resolved = resolveExecutablePath(token, pathEnv);
    if (resolved === null) {
      throw new GuardWarning(
        `app-server の実行ファイル ${token} のパスを解決できません`,
      );
    }
    return resolved;
  });
};

/**
 * 実行ファイルの `max(mtimeMs, ctimeMs)` (symlink の先を見る)。現在時刻より後の mtime は
 * 使わず ctime だけを見る (未来の mtime を持つファイルで、起動し直した broker まで毎回古いと
 * 判定しないため)。実行ファイルが無くなっていれば (`ENOENT`)、broker の起動より後に更新された
 * ものとして `Infinity` を返す。
 *
 * @param {string} executablePath
 * @returns {number}
 */
const readExecutableUpdateTimeMs = (executablePath) => {
  try {
    const stats = fs.statSync(executablePath);
    const mtimeMs = stats.mtimeMs > Date.now() ? 0 : stats.mtimeMs;
    return Math.max(mtimeMs, stats.ctimeMs);
  } catch (error) {
    if (error?.code === "ENOENT") {
      return Number.POSITIVE_INFINITY;
    }
    throw new GuardWarning(
      `app-server の実行ファイル ${executablePath} を参照できません: ${describeError(error)}`,
    );
  }
};

/**
 * `ps -o etime=` から broker の起動時刻 (epoch ミリ秒) を求める。
 *
 * @param {number} brokerPid
 * @returns {number}
 */
const readBrokerStartMs = (brokerPid) => {
  const elapsedText = runPs(["-o", "etime=", "-p", String(brokerPid)]);
  const elapsedSeconds = parseElapsedSeconds(elapsedText);
  if (elapsedSeconds === null) {
    throw new GuardWarning(
      `ps -o etime= の出力を解釈できません: ${JSON.stringify(elapsedText)}`,
    );
  }
  return Date.now() - elapsedSeconds * 1000;
};

/**
 * broker に shutdown を送り、終了しなければ SIGTERM を送る。終了を確認できなければ
 * GuardWarning を投げる。
 *
 * @param {Record<string, Function>} lifecycle
 * @param {{ pid: number, endpoint?: unknown }} session
 */
const stopBroker = async (lifecycle, session) => {
  // shutdown 要求の完了は待たず、pid の終了で判定する (要求が応答を返さない場合も
  // 待ち続けないため)。要求の失敗は SIGTERM で補う。
  Promise.resolve()
    .then(() => lifecycle.sendBrokerShutdown(session.endpoint))
    .catch(() => {});
  if (await waitForProcessExit(session.pid, SHUTDOWN_WAIT_MS)) {
    return;
  }
  try {
    process.kill(session.pid, "SIGTERM");
  } catch {
    // 直前に終了した場合は次の確認で分かる。
  }
  if (!(await waitForProcessExit(session.pid, TERM_WAIT_MS))) {
    throw new GuardWarning(
      `broker (pid=${session.pid}) を止めようとしましたが、プロセスが残っています`,
    );
  }
};

/**
 * 記録が止めた broker のものと一致すれば、後片付けをして記録を消す。一致しない場合と記録が
 * 無い場合は何もしない (並行する companion が新しい broker を記録していることがあるため)。
 *
 * @param {Record<string, Function>} lifecycle
 * @param {string} cwd
 * @param {{ pid: number, endpoint?: unknown }} stoppedSession
 */
const clearStoppedBrokerRecord = (lifecycle, cwd, stoppedSession) => {
  const current = lifecycle.loadBrokerSession(cwd);
  if (
    current === null ||
    typeof current !== "object" ||
    current.pid !== stoppedSession.pid ||
    current.endpoint !== stoppedSession.endpoint
  ) {
    return;
  }
  if (typeof lifecycle.teardownBrokerSession === "function") {
    lifecycle.teardownBrokerSession({
      endpoint: current.endpoint ?? null,
      pidFile: current.pidFile ?? null,
      logFile: current.logFile ?? null,
      sessionDir: current.sessionDir ?? null,
      pid: current.pid ?? null,
      killProcess: null,
    });
  }
  lifecycle.clearBrokerSession(cwd);
};

// ---------------------------------------------------------------------------
// guard 全体の処理
// ---------------------------------------------------------------------------

/**
 * guard 全体の処理 (ファイル先頭の判定手順)。例外を外に投げない。
 *
 * @param {{ companionPath: string, cwd: string, env: NodeJS.ProcessEnv }} options
 * @returns {Promise<void>}
 */
export const runStaleBrokerGuard = async ({ companionPath, cwd, env }) => {
  try {
    const lifecycle = await importBrokerLifecycle(companionPath);
    if (typeof env[ENDPOINT_ENV] === "string" && env[ENDPOINT_ENV] !== "") {
      return;
    }

    const session = lifecycle.loadBrokerSession(cwd);
    const brokerPid = session?.pid;
    if (!Number.isInteger(brokerPid) || brokerPid <= 0) {
      return;
    }
    if (!isProcessAlive(brokerPid)) {
      return;
    }

    const psOutput = readProcessTable();
    ensureCompanionBroker(psOutput, brokerPid);
    const executablePaths = collectAppServerExecutablePaths(
      psOutput,
      brokerPid,
      env.PATH,
    );
    const brokerStartMs = readBrokerStartMs(brokerPid);
    const updateTimesMs = executablePaths.map(readExecutableUpdateTimeMs);
    if (!isBrokerStale(brokerStartMs, updateTimesMs)) {
      return;
    }

    await stopBroker(lifecycle, session);
    let cleanupError = null;
    try {
      clearStoppedBrokerRecord(lifecycle, cwd, session);
    } catch (error) {
      cleanupError = error;
    }
    writeStderrLine(
      `codex CLI の更新前に起動した companion の broker を止めました (pid=${brokerPid})`,
    );
    if (cleanupError !== null) {
      writeWarning(`broker の記録を片付けられません: ${describeError(cleanupError)}`);
    }
  } catch (error) {
    writeWarning(
      error instanceof GuardWarning
        ? error.message
        : `予期しないエラーで guard を中断しました: ${describeError(error)}`,
    );
  }
};

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

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
  await runStaleBrokerGuard({
    companionPath: process.argv[2],
    cwd: process.cwd(),
    env: process.env,
  });
  // shutdown 要求の socket などが残っていても待たずに終える。終了コードは常に 0。
  process.exit(0);
}
