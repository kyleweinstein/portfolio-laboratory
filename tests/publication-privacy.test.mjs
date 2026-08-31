import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  assertViewerSafeShape,
  configureManagedPublication,
  forwardVerifiedPlaidWebhook,
  loadProviderAccounts,
  loadPublishedPortfolioCards,
  normalizePortfolioCards,
  normalizePortfolioDetail,
  publishManagedPublication,
} from "../app/publication-server.ts";

const SERVICE_ENV = {
  PORTFOLIO_SERVICE_URL: "https://broker.internal/",
  PORTFOLIO_INTERNAL_TOKEN: "internal-token-at-least-sixteen-bytes",
  PORTFOLIO_OWNER_GITHUB_ID: "65232147",
};

const PRIVATE_MARKERS = [
  "acct-raw-1234",
  "external-position-99",
  "9876.54",
  "42.125",
  "11450.00",
  "deposit-private-1",
];

const rawServicePayload = {
  data: {
    slug: "leveraged-growth",
    title: "Leveraged Growth",
    provider: "plaid_m1",
    accountId: "acct-raw-1234",
    ytdReturnPercent: 12.5,
    performanceThrough: "2026-08-28",
    quality: "statement_reconciled",
    balance: "9876.54",
    performance: [
      { date: "2026-01-02", returnPercent: 0, benchmarkReturnPercent: 0, portfolioValue: "9876.54" },
      { date: "2026-08-28", returnPercent: 12.5, benchmarkReturnPercent: 8.1, externalCashFlow: "11450.00" },
    ],
    holdingsAsOf: "2026-08-28T20:00:00Z",
    grossExposurePercent: 125,
    netExposurePercent: 100,
    analyticsSleevePercent: 125,
    holdings: [
      {
        kind: "security",
        symbol: "AAPL",
        name: "Apple Inc.",
        weightPercent: 70,
        costBasisPerShare: 182.3345,
        returnPercent: 14.2,
        quantity: "42.125",
        marketValue: "11450.00",
        externalPositionId: "external-position-99",
        quality: "broker_reported",
      },
      {
        kind: "cash_margin",
        name: "Cash / Margin",
        weightPercent: -25,
        cashValue: "-2500",
        quality: "broker_reported",
      },
      {
        kind: "other",
        name: "Other assets / liabilities",
        weightPercent: 55,
        quality: "estimated",
      },
    ],
    activities: [{ id: "deposit-private-1", amount: "11450.00" }],
    analytics: {
      risk: { annualReturnPercent: 10.4, annualVolatilityPercent: 18.2, sharpeRatio: 0.52 },
      optimizedAllocation: [{ symbol: "AAPL", weightPercent: 60 }],
    },
  },
};

test("viewer detail reconstruction keeps signed weights and per-share basis but drops every private amount and identifier", () => {
  const detail = normalizePortfolioDetail(rawServicePayload);
  assert.ok(detail);
  assert.equal(detail.holdings[1].kind, "cash_margin");
  assert.equal(detail.holdings[1].weightPercent, -25);
  assert.equal(detail.holdings[1].costBasisPerShare, null);
  assert.equal(detail.holdings[0].costBasisPerShare, 182.3345);
  assert.equal(detail.grossExposurePercent, 125);
  const json = JSON.stringify(detail);
  for (const marker of PRIVATE_MARKERS) assert.doesNotMatch(json, new RegExp(escapeRegex(marker)));
  assert.doesNotMatch(json, /accountId|quantity|marketValue|cashValue|portfolioValue|externalCashFlow|activities/i);
  assert.match(json, /costBasisPerShare/);
  assert.doesNotThrow(() => assertViewerSafeShape(detail));
});

test("service PortfolioDetail card envelope and analytical sleeve normalize correctly", () => {
  const detail = normalizePortfolioDetail({
    card: {
      slug: "m1-core",
      title: "M1 Core",
      provider: "plaid_m1",
      ytdReturnPercent: "7.25",
      performanceThrough: "2026-08-28T20:00:00Z",
      quality: "portfolio_lab_computed",
      performancePoints: [
        { at: "2026-01-02T21:00:00Z", returnPercent: 0, benchmarkReturnPercent: 0, quality: "portfolio_lab_computed" },
        { at: "2026-08-28T20:00:00Z", returnPercent: 7.25, benchmarkReturnPercent: 6.1, quality: "portfolio_lab_computed" },
      ],
    },
    holdingsAsOf: "2026-08-28T20:00:00Z",
    grossExposurePercent: 125,
    netExposurePercent: 100,
    analyticalSleevePercent: 125,
    holdings: [{ kind: "cash_margin", name: "Cash / Margin", weightPercent: -25, quality: "broker_reported" }],
    benchmarkSymbol: "SPY",
    analytics: null,
  });
  assert.ok(detail);
  assert.equal(detail.slug, "m1-core");
  assert.equal(detail.performance.length, 2);
  assert.equal(detail.performance.at(-1).date, "2026-08-28");
  assert.equal(detail.analyticsSleevePercent, 125);
  assert.equal(detail.holdings[0].weightPercent, -25);
});

test("server-to-server publication reads include private service identity without leaking it", async () => {
  const originalFetch = globalThis.fetch;
  let capturedHeaders;
  globalThis.fetch = async (_input, init) => {
    capturedHeaders = new Headers(init?.headers);
    return Response.json({ portfolios: [] });
  };
  try {
    const cards = await loadPublishedPortfolioCards({
      WEBULL_SERVICE_URL: "https://broker.internal/",
      WEBULL_INTERNAL_TOKEN: "internal-token-long-enough",
      GITHUB_OWNER_IDS: "65232147",
    });
    assert.deepEqual(cards, []);
    assert.equal(capturedHeaders.get("x-portfolio-owner-github-id"), "65232147");
    assert.equal(capturedHeaders.get("authorization"), "Bearer internal-token-long-enough");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Plaid webhook proxy forwards the exact signed raw body only to the private service", async () => {
  const originalFetch = globalThis.fetch;
  let forwarded;
  globalThis.fetch = async (input, init) => {
    forwarded = {
      url: String(input),
      body: JSON.parse(String(init?.body)),
      headers: new Headers(init?.headers),
    };
    return Response.json({ accepted: true, syncRequired: true });
  };
  try {
    const rawBody = '{"webhook_type":"HOLDINGS", "webhook_code":"DEFAULT_UPDATE"}';
    assert.deepEqual(
      await forwardVerifiedPlaidWebhook(rawBody, "signed.jwt.value-at-least-sixteen", SERVICE_ENV),
      { accepted: true, syncRequired: true },
    );
    assert.equal(forwarded.url, "https://broker.internal/v1/providers/plaid/webhook");
    assert.deepEqual(forwarded.body, {
      rawBody,
      signature: "signed.jwt.value-at-least-sixteen",
    });
    assert.equal(forwarded.headers.get("x-portfolio-owner-github-id"), "65232147");
    assert.equal(forwarded.headers.get("authorization"), "Bearer internal-token-at-least-sixteen-bytes");
  } finally {
    globalThis.fetch = originalFetch;
  }
  const route = await readFile(new URL("../app/api/webhooks/plaid/route.ts", import.meta.url), "utf8");
  assert.match(route, /request\.headers\.get\("plaid-verification"\)/);
  assert.match(route, /await request\.text\(\)/);
  assert.doesNotMatch(route, /request\.json\(\)/);
});

test("cards cannot use labels as a dollar or account-identifier side channel", () => {
  assert.deepEqual(normalizePortfolioCards({ publications: [
    { slug: "safe", title: "Account #12345678", provider: "webull", ytdReturnPercent: 1, quality: "computed" },
    { slug: "unsafe", title: "$50,000 Growth", provider: "webull", ytdReturnPercent: 1, quality: "computed" },
    { slug: "suffix", title: "Individual 4321", provider: "webull", ytdReturnPercent: 1, quality: "computed" },
    { slug: "masked", title: "M1 •••4321", provider: "plaid_m1", ytdReturnPercent: 1, quality: "computed" },
    { slug: "balance", title: "Balance 10,000", provider: "plaid_m1", ytdReturnPercent: 1, quality: "computed" },
    { slug: "community-growth", title: "Community Growth", provider: "webull", ytdReturnPercent: 1, quality: "computed" },
    { slug: "strategy-60-40", title: "Strategy 60/40", provider: "webull", ytdReturnPercent: 1, quality: "computed" },
  ] }).map(card => card.slug), ["community-growth", "strategy-60-40"]);
});

test("recursive privacy guard rejects forbidden fields at any JSON depth", () => {
  for (const value of [
    { accountId: "private" },
    { nested: [{ quantity: 2 }] },
    { analytics: { netLiquidationValue: 100 } },
    { chart: { external_cash_flow: 10 } },
    { metrics: { dayProfitLoss: 10 } },
    { metrics: { total_market_value_usd: 10 } },
    { source: { accountMaskedIdentifier: "***1234" } },
    { snapshot: { portfolioValue: 10 } },
    { snapshot: { investmentGain: 10 } },
    { holding: { costBasisPerShareUsd: 10 } },
    { nested: { brokerExternalPositionId: "private" } },
    { history: { transactions: [] } },
  ]) {
    assert.throws(() => assertViewerSafeShape(value), /Forbidden viewer field/);
  }
  assert.doesNotThrow(() => assertViewerSafeShape({
    holding: { weightPercent: -25, costBasisPerShare: 10.25 },
    risk: { valueAtRisk95Percent: -2.1 },
    performance: { benchmarkReturnPercent: 8.5 },
  }));
});

test("published HTML, SVG, and ARIA templates consume only privacy-safe percentage DTO fields", async () => {
  const ui = await readFile(new URL("../app/publication-ui.tsx", import.meta.url), "utf8");
  const map = await readFile(new URL("../app/published-diversification-map.tsx", import.meta.url), "utf8");
  const viewerRoute = await readFile(new URL("../app/api/portfolios/[slug]/route.ts", import.meta.url), "utf8");
  assert.match(ui, /formatCostBasis\(holding\.costBasisPerShare\)/);
  assert.match(ui, /Account values, contributions, and currency profit or loss are never published/);
  assert.doesNotMatch(ui, /holding\.(?:quantity|marketValue|cashValue|accountId|profitLoss)/);
  assert.doesNotMatch(map, /marketValue|portfolioValue|accountId|quantity|costBasis/);
  assert.match(map, /aria-label={`Correlation map for \$\{data\.symbols\.length\} holdings\./);
  assert.match(ui, /aria-label={`\$\{title\}\. Latest portfolio return/);
  assert.match(viewerRoute, /authorizeDiscordViewerRequest\(request\)/);
  assert.match(viewerRoute, /viewerJsonResponse/);
});

test("follower pages are Discord-gated and owner mutations require GitHub session plus CSRF", async () => {
  const portfolioPage = await readFile(new URL("../app/portfolios/page.tsx", import.meta.url), "utf8");
  const detailPage = await readFile(new URL("../app/portfolios/[slug]/page.tsx", import.meta.url), "utf8");
  const managePage = await readFile(new URL("../app/manage/page.tsx", import.meta.url), "utf8");
  const ownerGuard = await readFile(new URL("../app/owner-publication-server.ts", import.meta.url), "utf8");
  const publicationMutation = await readFile(new URL("../app/api/manage/publications/[publicationId]/route.ts", import.meta.url), "utf8");
  const plaidExchange = await readFile(new URL("../app/api/manage/providers/plaid/exchange/route.ts", import.meta.url), "utf8");
  assert.match(portfolioPage, /requireDiscordViewer\("\/portfolios"\)/);
  assert.match(detailPage, /requireDiscordViewer\(`\/portfolios\/\$\{slug\}`\)/);
  assert.match(managePage, /requirePortfolioOwner\("\/manage"\)/);
  assert.match(ownerGuard, /readGitHubSession\(request\)/);
  assert.match(ownerGuard, /validateMutationRequest\(request, session\)/);
  assert.match(publicationMutation, /authorizePublicationOwner\(request, true\)/g);
  assert.match(plaidExchange, /authorizePublicationOwner\(request, true\)/);
  assert.doesNotMatch(managePage, /accountId|accountNumber|maskedIdentifier/);
});

test("owner management auth is neutral, feature-flag independent, and operator guidance exposes no fake upload controls", async () => {
  const ownerLogin = await readFile(new URL("../app/api/owner/auth/login/route.ts", import.meta.url), "utf8");
  const ownerCallback = await readFile(new URL("../app/api/owner/auth/callback/route.ts", import.meta.url), "utf8");
  const webullLogin = await readFile(new URL("../app/api/webull/auth/login/route.ts", import.meta.url), "utf8");
  const webullCallback = await readFile(new URL("../app/api/webull/auth/callback/route.ts", import.meta.url), "utf8");
  const pageGuard = await readFile(new URL("../app/discord-page-auth.ts", import.meta.url), "utf8");
  const ownerGuard = await readFile(new URL("../app/owner-publication-server.ts", import.meta.url), "utf8");
  const managePage = await readFile(new URL("../app/manage/page.tsx", import.meta.url), "utf8");

  assert.match(ownerLogin, /resolveGitHubOwnerCallbackUrl/);
  assert.match(ownerCallback, /createGitHubSession/);
  assert.doesNotMatch(ownerLogin + webullLogin + webullCallback, /isWebullIntegrationEnabled|Webull integration is disabled/);
  assert.match(pageGuard, /\/api\/owner\/auth\/login/);
  assert.match(ownerGuard, /\/api\/owner\/auth\/login/);
  assert.match(managePage, /encrypted normalized bundle/i);
  assert.match(managePage, /Privacy preview/);
  assert.match(managePage, /commits it atomically/);
  assert.doesNotMatch(managePage, /Backend contract pending/);
  assert.doesNotMatch(managePage, /<button[^>]+disabled|<input[^>]+type=["']file["']/i);
});

test("owner publication transport uses opaque account handles and agreed private service routes", async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init = {}) => {
    calls.push({
      url: String(input),
      method: init.method ?? "GET",
      body: init.body ?? null,
      authorization: new Headers(init.headers).get("authorization"),
    });
    if (String(input).endsWith("/v1/providers/plaid_m1/accounts")) {
      return Response.json({ accounts: [{ accountHandle: "handle_m1_1", provider: "plaid_m1", accountType: "MARGIN", currency: "USD", lastSyncedAt: "2026-08-28T21:00:00Z", accountId: "must-not-pass" }] });
    }
    return Response.json({ ok: true });
  };
  try {
    assert.deepEqual(await loadProviderAccounts("plaid_m1", SERVICE_ENV), [{
      accountHandle: "handle_m1_1",
      provider: "M1 Finance",
      accountType: "MARGIN",
      currency: "USD",
      lastSyncedAt: "2026-08-28T21:00:00Z",
    }]);
    await configureManagedPublication({
      accountHandle: "handle_m1_1",
      slug: "m1-growth",
      title: "M1 Growth",
      benchmarkSymbol: "SPY",
      enabled: true,
    }, SERVICE_ENV);
    await publishManagedPublication("publication_1", SERVICE_ENV);
    assert.equal(calls[0].url, "https://broker.internal/v1/providers/plaid_m1/accounts");
    assert.equal(calls[1].url, "https://broker.internal/v1/publications/configure");
    assert.equal(calls[1].method, "PUT");
    assert.deepEqual(JSON.parse(calls[1].body), {
      accountHandle: "handle_m1_1",
      slug: "m1-growth",
      title: "M1 Growth",
      benchmarkSymbol: "SPY",
      enabled: true,
    });
    assert.doesNotMatch(calls[1].body, /accountId/);
    assert.equal(calls[2].url, "https://broker.internal/v1/publications/publication_1/publish");
    assert.equal(calls[2].method, "POST");
    assert.ok(calls.every(call => call.authorization === `Bearer ${SERVICE_ENV.PORTFOLIO_INTERNAL_TOKEN}`));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
