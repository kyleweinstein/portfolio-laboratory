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
  assert.match(dashboard, /disabled=\{Boolean\(action\) \|\| verificationInProgress \|\| status\.nextAction === "wait"\}/);
  assert.match(dashboard, /You may leave this page[\s\S]*latest stage and result will be here when you return\./i);
  assert.match(dashboard, /action !== "connect" && !verificationRunning[\s\S]*window\.setTimeout\(poll, 3_000\)/);
  assert.match(dashboard, /VerificationStatusCard[\s\S]*Started[\s\S]*Last update[\s\S]*Completed/);
  assert.match(dashboard, /nextAction === "connect"[\s\S]*await loadStatus\(undefined, true\)/);
});

test("read-only activation pending cannot start verification", () => {
  assert.match(dashboard, /status\?\.nextAction === "configure"[\s\S]*Read-only protection/);
  assert.match(dashboard, /Portfolio Lab already has the connection details it needs/);
  assert.match(dashboard, /does not implement trading or transfer actions/);
  assert.match(dashboard, /No additional Webull setup is needed\. When activation is complete, return here to verify the connection\./);
  assert.match(dashboard, /status\?\.nextAction === "configure"[\s\S]*\? "Read-only activation pending"/);
  const configureBranch = dashboard
    .split('status?.nextAction === "configure" ? (')[1]
    ?.split(') : status && !status.connected ? (')[0] || "";
  assert.ok(configureBranch);
  assert.doesNotMatch(configureBranch, /onClick=\{connect\}|Verify Webull connection/);
  assert.doesNotMatch(configureBranch, /App Key[^.]+(?:limited|restricted|read-only|query-only)|Account Infos|Order Query|permission scope/i);
});

test("403 mutation failures remain action errors and status text stays encoding-safe", () => {
  assert.match(dashboard, /if \(error\.status === 401\) return \{ kind: "unauthorized"/);
  assert.doesNotMatch(dashboard, /error\.status === 401 \|\| error\.status === 403/);
  assert.match(dashboard, /"Checking connection\.\.\."/);
  assert.doesNotMatch(dashboard, /Checking connectionâ€¦/);
});
