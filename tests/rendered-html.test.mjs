import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { parsePortfolioCsv } from "../app/portfolio-csv.ts";

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
  assert.match(html, />PORTFOLIO LAB</);
  assert.doesNotMatch(html, /Clear allocation decisions/);
  assert.match(html, /Yahoo Finance public chart data/);
  assert.match(html, />Manual</);
  assert.match(html, />Webull</);
  assert.match(html, /does not overwrite the manual draft/i);
  assert.match(html, /Minimum volatility/);
  assert.match(html, /Maximum Sharpe/);
  assert.match(html, /not investment, tax, or legal advice/i);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/i);
});

test("client analysis uses explicit snapshots, worker analytics, bounded direction lanes, and an adaptive map", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const analytics = await readFile(new URL("../app/analytics.ts", import.meta.url), "utf8");
  const map = await readFile(new URL("../app/diversification-map.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(source, /<h1>PORTFOLIO LAB<\/h1>/);
  assert.match(source, /<span className="eyebrow">THE SEER&apos;S<\/span>/);
  assert.doesNotMatch(source, /<span className="eyebrow">Portfolio laboratory<\/span>/i);
  assert.match(source, /Analyze portfolio/);
  assert.match(source, /<WebullDashboard/);
  assert.match(source, /onAnalyzeCurrentHoldings={useWebullHoldings}/);
  assert.match(source, /Changes not analyzed/);
  assert.match(source, /new Worker\(new URL\("\.\/analytics\.worker\.ts"/);
  assert.match(source, /\/api\/market\/batch/);
  assert.match(source, /Up \/ down direction comparison/);
  assert.match(source, /<DirectionChart/);
  assert.match(source, /fixed ±1 encoding/);
  assert.doesNotMatch(source, /CumulativeChart|Growth of one dollar|Multi-asset performance overlay/);
  assert.match(source, /Lowest-correlation opportunities/);
  assert.match(source, /Suggested rebalance buckets/);
  assert.match(source, /Style \/ sector \/ factor radar/);
  assert.match(source, /<RadarPlot title="Factor"/);
  assert.match(analytics, /STYLE_PROXIES/);
  assert.match(analytics, /SECTOR_PROXIES/);
  assert.match(analytics, /FACTOR_PROXIES/);
  assert.match(analytics, /inferCategory/);
  assert.match(source, /Automatic classification/);
  assert.match(source, /Style, sector, and factor are inferred automatically/);
  assert.match(analytics, /underweight \* diversification/);
  assert.match(source, /Volatility harvesting is not guaranteed/);
  assert.match(analytics, /rebalancePotential: spreadVolatility \* \(1 - correlation\) \/ 2/);
  assert.match(analytics, /triggered: drift >= input\.rebalanceBand \/ 100/);
  assert.match(analytics, /return weight \* Math\.exp\(cumulativeReturn\)/);
  assert.match(source, /valid\.length >= 6/);
  assert.match(map, /<canvas/);
  assert.match(map, /effectiveCell >= 28/);
  assert.match(map, /effectiveCell < 1/);
  assert.match(map, /Clustered/);
  assert.doesNotMatch(source, /className="cell"/);
  assert.doesNotMatch(source, /holding\.current|Current weight|<th>Current<\/th>/);
  assert.match(styles, /\.controls\{display:grid;grid-template-columns:/);
  assert.match(styles, /\.controls>label input,\.controls>label select\{width:100%;min-width:0;height:46px\}/);
});

test("portfolio CSV import derives weights from Value and merges duplicate symbols", () => {
  const csv = [
    "Symbol,Name,Quantity,Avg. Price,Cost Basis,Unrealized Gain ($),Unrealized Gain (%),Value",
    "AAA,\"Alpha, Inc.\",2,25,50,10,20,60",
    "BBB,Beta Corp.,1,30,30,10,33.3,40",
    "AAA,Alpha Inc.,1,25,25,5,20,25",
    "CASH,Cash,1,15,15,0,0,15",
  ].join("\r\n");
  const result = parsePortfolioCsv(csv);

  assert.equal(result.basis, "Value");
  assert.equal(result.importedRows, 3);
  assert.equal(result.mergedRows, 1);
  assert.equal(result.ignoredRows, 1);
  assert.deepEqual(result.holdings, [
    { symbol: "AAA", weight: 68 },
    { symbol: "BBB", weight: 32 },
  ]);
});

test("portfolio CSV import reports malformed rows without replacing the portfolio", () => {
  assert.throws(
    () => parsePortfolioCsv("Symbol,Value\nGOOD,100\nBAD SYMBOL,50"),
    /row 3: invalid Symbol "BAD SYMBOL"/,
  );
  assert.throws(
    () => parsePortfolioCsv("Name,Quantity\nAlpha,3"),
    /Include a Symbol column and either a Value or Weight column/,
  );
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

test("batch market route validates the request before source access", async () => {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-batch`);
  const { default: worker } = await import(workerUrl.href);
  const response = await worker.fetch(
    new Request("http://localhost/api/market/batch", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ symbols: ["../secret"], years: 3 }),
    }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "Provide between 1 and 150 valid market symbols." });
});

test("Webull stays disabled by default without affecting the public manual app", async () => {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-webull-disabled`);
  const { default: worker } = await import(workerUrl.href);
  const response = await worker.fetch(
    new Request("http://localhost/api/webull/status"),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    enabled: false,
    authenticated: false,
    connected: false,
    verificationInProgress: false,
    verification: null,
    lastSyncAttempt: null,
    nextAction: "configure",
    accounts: [],
    selectedAccountId: null,
    dashboard: null,
    issues: [],
  });
});

test("Railway health endpoint is public and cache-safe", async () => {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-health`);
  const { default: worker } = await import(workerUrl.href);
  const response = await worker.fetch(
    new Request("http://localhost/api/health"),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(await response.json(), { status: "ok" });
});
