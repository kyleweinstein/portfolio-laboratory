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
  assert.match(html, /Clear allocation decisions/);
  assert.match(html, /Yahoo Finance public chart data/);
  assert.match(html, /Minimum volatility/);
  assert.match(html, /Maximum Sharpe/);
  assert.match(html, /not investment, tax, or legal advice/i);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/i);
});

test("client analysis includes pairing, equal-scale direction lanes, radar dimensions, and automatic drift controls", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(source, /Up \/ down direction comparison/);
  assert.match(source, /<DirectionChart/);
  assert.match(source, /fixed ±1 encoding/);
  assert.doesNotMatch(source, /CumulativeChart|Growth of one dollar|Multi-asset performance overlay/);
  assert.match(source, /Lowest-correlation opportunities/);
  assert.match(source, /Suggested rebalance buckets/);
  assert.match(source, /Style \/ sector \/ factor radar/);
  assert.match(source, /<RadarPlot title="Factor"/);
  assert.match(source, /STYLE_PROXIES/);
  assert.match(source, /SECTOR_PROXIES/);
  assert.match(source, /FACTOR_PROXIES/);
  assert.match(source, /inferCategory/);
  assert.match(source, /Automatic classification/);
  assert.match(source, /Style, sector, and factor are inferred automatically/);
  assert.match(source, /underweight \* diversification/);
  assert.match(source, /Volatility harvesting is not guaranteed/);
  assert.match(source, /rebalancePotential: spreadVolatility \* \(1 - correlation\[a\]\[b\]\) \/ 2/);
  assert.match(source, /triggered: drift >= rebalanceBand \/ 100/);
  assert.match(source, /weight \* Math\.exp\(cumulativeReturn\)/);
  assert.match(source, /disabled=\{activeHoldings\.length < 2 \|\| pendingOptimization !== null\}/);
  assert.doesNotMatch(source, /holding\.current|Current weight|<th>Current<\/th>/);
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
