import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const connectRoute = readFileSync(new URL("../app/api/webull/connect/route.ts", import.meta.url), "utf8");
const syncRoute = readFileSync(new URL("../app/api/webull/sync/route.ts", import.meta.url), "utf8");
const backfillRoute = readFileSync(new URL("../app/api/webull/backfill/route.ts", import.meta.url), "utf8");
const accountRoute = readFileSync(new URL("../app/api/webull/accounts/select/route.ts", import.meta.url), "utf8");
const dashboard = readFileSync(new URL("../app/webull-dashboard.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");

test("only explicit Webull connect receives the first-approval timeout", () => {
  const [postHandler, deleteHandler = ""] = connectRoute.split("export async function DELETE");
  assert.match(postHandler, /proxyWebullJson\("\/connect"[\s\S]*timeoutMs:\s*330_000/);
  assert.doesNotMatch(deleteHandler, /timeoutMs:\s*330_000/);
  for (const route of [syncRoute, backfillRoute, accountRoute]) {
    assert.doesNotMatch(route, /timeoutMs:\s*330_000/);
  }
});

test("OAuth returns to the Webull source and the URL preserves source selection", () => {
  assert.match(dashboard, /webullLoginUrl\("\/\?source=webull"\)/);
  assert.match(page, /URLSearchParams\(window\.location\.search\)[\s\S]*requested === "webull"/);
  assert.match(page, /url\.searchParams\.set\("source", "webull"\)/);
});

test("disconnected UI explains the approval wait and prevents duplicate verification", () => {
  assert.match(dashboard, /First-time Webull approval can take up to five minutes\./);
  assert.match(dashboard, /repeated starts are blocked/i);
  assert.match(dashboard, /disabled=\{Boolean\(action\) \|\| Boolean\(status\.verificationInProgress\)\}/);
  assert.match(dashboard, /You may leave this page[\s\S]*show the current status when you return\./i);
  assert.match(dashboard, /status\?\.verificationInProgress[\s\S]*window\.setTimeout\(poll, 3_000\)/);
});
