import assert from "node:assert/strict";
import test from "node:test";
import {
  normalizeServiceDashboard,
  webullStatusResponse,
} from "../app/webull-server.ts";
import {
  GITHUB_SESSION_COOKIE,
  createGitHubSession,
  getGitHubAuthConfig,
} from "../app/github-auth.ts";

const serviceDashboard = {
  portfolio: {
    account: { accountId: "account-1234", accountType: "CASH", currency: "USD" },
    balance: {
      accountId: "account-1234",
      asOf: "2026-08-20T20:10:00Z",
      currency: "USD",
      equity: "10000",
      cash: "-2500",
      marketValue: "12500",
      dayProfitLoss: "75",
      unrealizedProfitLoss: "900",
    },
    positions: [
      { externalPositionId: "position-aapl", symbol: "AAPL", instrumentType: "EQUITY", currency: "USD", quantity: "20", lastPrice: "250", averageCost: "220", marketValue: "5000", costBasis: "4400", unrealizedProfitLoss: "600" },
      { externalPositionId: "position-spy", symbol: "SPY", instrumentType: "ETF", currency: "USD", quantity: "10", lastPrice: "700", averageCost: "670", marketValue: "7000", costBasis: "6700", unrealizedProfitLoss: "300" },
      { externalPositionId: "position-option", symbol: "AAPL260918C00300000", instrumentType: "OPTION", currency: "USD", quantity: "1", lastPrice: "500", averageCost: "400", marketValue: "500" },
    ],
  },
  performance: {
    start: "2026-08-18T20:10:00Z",
    end: "2026-08-20T20:10:00Z",
    timeWeightedReturn: 0.03,
    moneyWeightedReturn: 0.12,
    netExternalFlow: "1000",
    beginningValue: "13000",
    endingValue: "15000",
    periods: [
      { start: "2026-08-18T20:10:00Z", end: "2026-08-19T20:10:00Z", beginningValue: "13000", endingValue: "14000", netExternalFlow: "500", modifiedDietzReturn: 0.02 },
      { start: "2026-08-19T20:10:00Z", end: "2026-08-20T20:10:00Z", beginningValue: "14000", endingValue: "15000", netExternalFlow: "500", modifiedDietzReturn: 0.00980392156862745 },
    ],
  },
  recentActivities: [{ externalActivityId: "deposit-1", activityType: "DEPOSIT", occurredAt: "2026-08-19T14:00:00Z", amount: "500", currency: "USD", status: "COMPLETED", description: "ACH deposit", isExternalFlow: true }],
  issues: [{ code: "PERFORMANCE_HISTORY_BUILDING", severity: "info", message: "History is still short." }],
};

test("private service payload maps to the redacted browser dashboard contract", () => {
  const dashboard = normalizeServiceDashboard(
    serviceDashboard,
    "2026-08-20T20:10:00Z",
    "SPY",
    {
      symbol: "SPY",
      dates: ["2026-08-18", "2026-08-19", "2026-08-20"],
      prices: [100, 101, 102],
      source: "fixture",
    },
  );

  assert.ok(dashboard);
  assert.equal(dashboard.holdingsReady, true);
  assert.equal(dashboard.performanceReady, true);
  assert.equal(dashboard.analyticsCoverage, 1.2);
  assert.equal(dashboard.metrics.timeWeightedReturn.source, "Portfolio Lab computed");
  assert.ok(Math.abs(dashboard.metrics.benchmarkReturn.value - 0.02) < 1e-12);
  assert.ok(Math.abs(dashboard.metrics.excessReturn.value - ((1.03 / 1.02) - 1)) < 1e-12);
  assert.equal(dashboard.metrics.grossExposure.value, 1.25);
  assert.equal(dashboard.metrics.netExposure.value, 1);
  assert.equal(dashboard.metrics.cashMarginWeight.value, -0.25);
  assert.equal(dashboard.holdings.filter(item => item.eligibleForAnalysis).length, 2);
  assert.equal(dashboard.holdings.find(item => item.kind === "cash_margin").weight, -0.25);
  assert.equal(dashboard.holdings.find(item => item.symbol === "AAPL").costBasisPerShare, 220);
  assert.ok(Math.abs(dashboard.holdings.find(item => item.symbol === "AAPL").returnPercent - (250 / 220 - 1)) < 1e-12);
  assert.ok(Math.abs(dashboard.holdings.reduce((sum, item) => sum + item.weight, 0) - 1) < 1e-12);
  assert.equal(dashboard.exclusions.length, 1);
  assert.equal(dashboard.chart.length, 3);
  assert.ok(Math.abs(dashboard.chart.at(-1).benchmarkReturn - 0.02) < 1e-12);

  const serialized = JSON.stringify(dashboard);
  for (const forbidden of [
    "accountId", "maskedIdentifier", "quantity", "marketValue", "netAccountValue",
    "cashBalance", "dayProfitLoss", "unrealizedProfitLoss", "investmentGain",
    "netContributions", "portfolioValue", "externalCashFlow", "activities",
  ]) {
    assert.doesNotMatch(serialized, new RegExp(`\\"${forbidden}\\"`, "i"));
  }
});

test("no performance history stays explicitly partial and unavailable", () => {
  const dashboard = normalizeServiceDashboard(
    { ...serviceDashboard, performance: null },
    "2026-08-20T20:10:00Z",
  );
  assert.ok(dashboard);
  assert.equal(dashboard.performanceReady, false);
  assert.equal(dashboard.quality, "partial");
  assert.equal(dashboard.metrics.timeWeightedReturn.value, null);
  assert.deepEqual(dashboard.chart, []);
});

test("authenticated status forwards only a validated verification attempt and next action", async () => {
  const env = {
    WEBULL_INTEGRATION_ENABLED: "true",
    WEBULL_SERVICE_URL: "https://webull.internal/",
    WEBULL_INTERNAL_TOKEN: "test-internal-token-long-enough",
    GITHUB_CLIENT_ID: "client-id",
    GITHUB_CLIENT_SECRET: "client-secret",
    GITHUB_SESSION_SECRET: "test-session-secret-that-is-longer-than-thirty-two-bytes",
    GITHUB_OWNER_IDS: "12345",
  };
  const { cookieValue } = await createGitHubSession(
    { id: "12345", login: "portfolio-owner" },
    getGitHubAuthConfig(env),
  );
  const request = new Request("https://portfolio.example/api/webull/status", {
    headers: { cookie: `${GITHUB_SESSION_COOKIE}=${cookieValue}` },
  });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => Response.json({
    connected: false,
    verificationInProgress: true,
    verification: {
      state: "running",
      stage: "verifying_access",
      startedAt: "2026-08-20T20:00:00Z",
      updatedAt: "2026-08-20T20:04:00Z",
      completedAt: null,
      error: null,
    },
    lastSyncAttempt: {
      status: "running",
      startedAt: "invalid",
      completedAt: "2026-08-20T20:04:00Z",
      cashActivitiesComplete: "yes",
      message: "Malformed",
    },
    nextAction: "wait",
    accounts: [],
    selectedAccountId: null,
    lastSyncedAt: null,
    dashboard: null,
  });

  try {
    const response = await webullStatusResponse(request, env);
    assert.equal(response.status, 200);
    const status = await response.json();
    assert.equal(status.verificationInProgress, true);
    assert.equal(status.verification.state, "running");
    assert.equal(status.verification.stage, "verifying_access");
    assert.equal(status.verification.startedAt, "2026-08-20T20:00:00Z");
    assert.equal(status.lastSyncAttempt, null);
    assert.equal(status.nextAction, "wait");
    assert.equal(status.selectedAccountRef, null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("connected status preserves the last sync failure and issues without inventing a dashboard", async () => {
  const env = {
    WEBULL_INTEGRATION_ENABLED: "true",
    WEBULL_SERVICE_URL: "https://webull.internal/",
    WEBULL_INTERNAL_TOKEN: "test-internal-token-long-enough",
    GITHUB_CLIENT_ID: "client-id",
    GITHUB_CLIENT_SECRET: "client-secret",
    GITHUB_SESSION_SECRET: "test-session-secret-that-is-longer-than-thirty-two-bytes",
    GITHUB_OWNER_IDS: "12345",
  };
  const { cookieValue } = await createGitHubSession(
    { id: "12345", login: "portfolio-owner" },
    getGitHubAuthConfig(env),
  );
  const request = new Request("https://portfolio.example/api/webull/status", {
    headers: { cookie: `${GITHUB_SESSION_COOKIE}=${cookieValue}` },
  });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => Response.json({
    connected: true,
    verificationInProgress: false,
    verification: null,
    lastSyncAttempt: {
      status: "error",
      startedAt: "2026-08-20T20:00:00Z",
      completedAt: "2026-08-20T20:01:00Z",
      cashActivitiesComplete: false,
      message: "Webull cash activities are unavailable.",
    },
    nextAction: "sync_account",
    accounts: [{ accountId: "account-1234", accountType: "CASH", status: "ACTIVE", currency: "USD" }],
    selectedAccountId: "account-1234",
    lastSyncedAt: null,
    dashboard: {
      portfolio: null,
      performance: null,
      recentActivities: [],
      issues: [{ code: "NO_PORTFOLIO_SNAPSHOT", severity: "warning", message: "Run the first read-only sync." }],
    },
  });

  try {
    const response = await webullStatusResponse(request, env);
    assert.equal(response.status, 200);
    const status = await response.json();
    assert.equal(status.connected, true);
    assert.equal(status.dashboard, null);
    assert.match(status.accounts[0].accountRef, /^wbr_[A-Za-z0-9_-]{24,64}$/);
    assert.equal(status.selectedAccountRef, status.accounts[0].accountRef);
    assert.doesNotMatch(JSON.stringify(status), /account-1234|accountId|selectedAccountId/);
    assert.deepEqual(status.lastSyncAttempt, {
      status: "error",
      startedAt: "2026-08-20T20:00:00Z",
      completedAt: "2026-08-20T20:01:00Z",
      cashActivitiesComplete: false,
      message: "Webull cash activities are unavailable.",
    });
    assert.equal(status.issues[0].issueId, "NO_PORTFOLIO_SNAPSHOT");
    assert.equal(status.issues[0].message, "Run the first read-only sync.");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("signed-out status exposes only the sign-in action and no private attempt", async () => {
  const response = await webullStatusResponse(
    new Request("https://portfolio.example/api/webull/status"),
    { WEBULL_INTEGRATION_ENABLED: "true" },
  );
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    enabled: true,
    authenticated: false,
    connected: false,
    verificationInProgress: false,
    verification: null,
    lastSyncAttempt: null,
    nextAction: "sign_in",
    accounts: [],
    selectedAccountRef: null,
    dashboard: null,
    issues: [],
  });
});

test("status transport failures stay errors and preserve safe FastAPI details", async () => {
  const env = {
    WEBULL_INTEGRATION_ENABLED: "true",
    WEBULL_SERVICE_URL: "https://webull.internal/",
    WEBULL_INTERNAL_TOKEN: "test-internal-token-long-enough",
    GITHUB_CLIENT_ID: "client-id",
    GITHUB_CLIENT_SECRET: "client-secret",
    GITHUB_SESSION_SECRET: "test-session-secret-that-is-longer-than-thirty-two-bytes",
    GITHUB_OWNER_IDS: "12345",
  };
  const { cookieValue, session } = await createGitHubSession(
    { id: "12345", login: "portfolio-owner" },
    getGitHubAuthConfig(env),
  );
  const request = new Request("https://portfolio.example/api/webull/status", {
    headers: { cookie: `${GITHUB_SESSION_COOKIE}=${cookieValue}` },
  });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => Response.json(
    { detail: "The selected account is unavailable." },
    { status: 422 },
  );

  try {
    const response = await webullStatusResponse(request, env);
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), {
      enabled: true,
      authenticated: true,
      connected: false,
      verificationInProgress: false,
      verification: null,
      lastSyncAttempt: null,
      nextAction: "start_verification",
      accounts: [],
      selectedAccountRef: null,
      dashboard: null,
      issues: [],
      csrfToken: session.csrfToken,
      error: "The selected account is unavailable.",
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
