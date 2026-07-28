import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server renders the portfolio dashboard shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>Portfolio Laboratory<\/title>/i);
  assert.match(html, /Clear allocation decisions/);
  assert.match(html, /Yahoo Finance public chart data/);
  assert.match(html, /Minimum volatility/);
  assert.match(html, /Maximum Sharpe/);
  assert.match(html, /not investment, tax, or legal advice/i);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/i);
});

test("client analysis includes pairing, overlays, and rebalance controls", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(source, /Multi-asset performance overlay/);
  assert.match(source, /Lowest-correlation opportunities/);
  assert.match(source, /Suggested rebalance buckets/);
  assert.match(source, /Style and sector balance/);
  assert.match(source, /Lowest-correlation counterweights/);
  assert.match(source, /STYLE_PROXIES/);
  assert.match(source, /SECTOR_PROXIES/);
  assert.match(source, /underweight \* diversification/);
  assert.match(source, /Volatility harvesting is not guaranteed/);
  assert.match(source, /rebalancePotential: spreadVolatility \* \(1 - correlation\[a\]\[b\]\) \/ 2/);
  assert.match(source, /triggered: drift >= rebalanceBand \/ 100/);
});

test("market route rejects unsafe symbols before source access", async () => {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-market`);
  const { default: worker } = await import(workerUrl.href);
  const response = await worker.fetch(
    new Request("http://localhost/api/market?symbol=../secret&years=3"),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "Enter a valid market symbol." });
});
