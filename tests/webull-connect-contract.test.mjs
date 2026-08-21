import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const connectRoute = readFileSync(new URL("../app/api/webull/connect/route.ts", import.meta.url), "utf8");
const syncRoute = readFileSync(new URL("../app/api/webull/sync/route.ts", import.meta.url), "utf8");
const backfillRoute = readFileSync(new URL("../app/api/webull/backfill/route.ts", import.meta.url), "utf8");
const accountRoute = readFileSync(new URL("../app/api/webull/accounts/select/route.ts", import.meta.url), "utf8");
const dashboard = readFileSync(new URL("../app/webull-dashboard.tsx", import.meta.url), "utf8");

test("only explicit Webull connect receives the first-approval timeout", () => {
  const [postHandler, deleteHandler = ""] = connectRoute.split("export async function DELETE");
  assert.match(postHandler, /proxyWebullJson\("\/connect"[\s\S]*timeoutMs:\s*330_000/);
  assert.doesNotMatch(deleteHandler, /timeoutMs:\s*330_000/);
  for (const route of [syncRoute, backfillRoute, accountRoute]) {
    assert.doesNotMatch(route, /timeoutMs:\s*330_000/);
  }
});

test("disconnected UI explains the approval wait and prevents duplicate verification", () => {
  assert.match(dashboard, /First-time Webull approval can take up to five minutes\./);
  assert.match(dashboard, /do not retry while it is running\./i);
  assert.match(dashboard, /disabled=\{Boolean\(action\)\} onClick=\{connect\}/);
  assert.match(dashboard, /verification is in progress[\s\S]*do not refresh or retry\./i);
});
