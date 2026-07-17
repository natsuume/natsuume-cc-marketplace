#!/usr/bin/env node

/** Exit 0 for terminal companion job JSON, 1 for pending, and 2 for invalid JSON. */

import fs from "node:fs";

const [statusFile] = process.argv.slice(2);
if (!statusFile) process.exit(2);

let payload;
try {
  payload = JSON.parse(fs.readFileSync(statusFile, "utf8"));
} catch {
  process.exit(2);
}

const status = payload?.job?.status;
if (["completed", "failed", "cancelled"].includes(status)) process.exit(0);
if (["queued", "running"].includes(status)) process.exit(1);
process.exit(2);
