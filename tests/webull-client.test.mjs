import assert from "node:assert/strict";
import test from "node:test";
import {
  buildEligibleWebullHoldings,
  getWebullStatus,
  normalizeWebullStatus,
  syncWebull,
  webullLoginUrl,
} from "../app/webull-client.ts";

test("Webull status normalizes wrapped responses without inventing connection state", () => {
  const status = normalizeWebullStatus({
    data: {
      data: {
        enabled: true,
        authenticated: true,
        connected: true,
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
          status: "error",
          startedAt: "2026-08-20T19:58:00Z",
          completedAt: "2026-08-20T20:01:00Z",
          cashActivitiesComplete: false,
          message: "Webull cash activities are unavailable.",
        },
        nextAction: "wait",
        csrfToken: "csrf-token-for-test-only-1234567890",
        accounts: [{ accountRef: "wbr_aaaaaaaaaaaaaaaaaaaaaaaa", label: "Individual" }],
        selectedAccountRef: "wbr_aaaaaaaaaaaaaaaaaaaaaaaa",
        dashboard: { quality: "verified", holdings: [] },
        issues: [{ issueId: "SYNC_INCOMPLETE", severity: "warning", title: "Sync incomplete", message: "Retry the account sync." }],
      },
    },
  });

  assert.equal(status.connected, true);
  assert.equal(status.verificationInProgress, true);
  assert.equal(status.verification?.stage, "verifying_access");
  assert.equal(status.verification?.startedAt, "2026-08-20T20:00:00Z");
  assert.equal(status.lastSyncAttempt?.status, "error");
  assert.equal(status.lastSyncAttempt?.cashActivitiesComplete, false);
  assert.equal(status.nextAction, "wait");
  assert.equal(status.issues[0].issueId, "SYNC_INCOMPLETE");
  assert.equal(status.accounts[0].accountRef, "wbr_aaaaaaaaaaaaaaaaaaaaaaaa");
  assert.equal(status.dashboard?.quality, "verified");
});

test("Webull status rejects unknown verification values and never exposes attempts while signed out", () => {
  const invalid = normalizeWebullStatus({
    enabled: true,
    authenticated: true,
    connected: false,
    verificationInProgress: false,
    verification: {
      state: "pending",
      stage: "guessing",
      startedAt: "yesterday",
      updatedAt: "eventually",
      completedAt: null,
      error: { code: "UPSTREAM", message: "Do not trust this malformed record." },
    },
    nextAction: "open_trading",
    accounts: [],
    selectedAccountRef: null,
    dashboard: null,
  });
  assert.equal(invalid.verification, null);
  assert.equal(invalid.nextAction, "start_verification");

  const signedOut = normalizeWebullStatus({
    enabled: true,
    authenticated: false,
    connected: false,
    verificationInProgress: false,
    verification: {
      state: "failed",
      stage: "verifying_access",
      startedAt: "2026-08-20T20:00:00Z",
      updatedAt: "2026-08-20T20:05:00Z",
      completedAt: "2026-08-20T20:05:00Z",
      error: { code: "PRIVATE", message: "Private server detail" },
    },
    lastSyncAttempt: {
      status: "error",
      startedAt: "2026-08-20T20:00:00Z",
      completedAt: "2026-08-20T20:05:00Z",
      cashActivitiesComplete: false,
      message: "Private sync detail",
    },
    nextAction: "retry_verification",
    accounts: [],
    selectedAccountRef: null,
    dashboard: null,
    issues: [{ issueId: "PRIVATE", severity: "error", title: "Private issue" }],
  });
  assert.equal(signedOut.verification, null);
  assert.equal(signedOut.lastSyncAttempt, null);
  assert.equal(signedOut.nextAction, "sign_in");
  assert.deepEqual(signedOut.issues, []);
});

test("Webull status rejects malformed durable sync attempts", () => {
  const status = normalizeWebullStatus({
    enabled: true,
    authenticated: true,
    connected: true,
    verificationInProgress: false,
    verification: null,
    lastSyncAttempt: {
      status: "running",
      startedAt: "not-a-time",
      completedAt: "2026-08-20T20:05:00Z",
      cashActivitiesComplete: "yes",
      message: "Malformed",
    },
    nextAction: "sync_account",
    accounts: [{ accountRef: "wbr_aaaaaaaaaaaaaaaaaaaaaaaa" }],
    selectedAccountRef: "wbr_aaaaaaaaaaaaaaaaaaaaaaaa",
    dashboard: null,
    issues: [],
  });
  assert.equal(status.lastSyncAttempt, null);
});

test("eligible Webull positions create one normalized long-only analytics sleeve", () => {
  const holdings = buildEligibleWebullHoldings([
    { symbol: " aapl ", instrumentType: "EQUITY", weight: "0.60" },
    { symbol: "AAPL", instrumentType: "STOCK", weight: 0.15 },
    { symbol: "SPY", instrumentType: "ETF", weight: 0.25 },
    { kind: "cash_margin", symbol: "USD", instrumentType: "CASH", weight: -0.25 },
    { symbol: "TSLA", instrumentType: "EQUITY", weight: -0.05 },
    { symbol: "BTC", instrumentType: "CRYPTO", weight: 0.50, eligibleForAnalysis: false },
  ]);

  assert.deepEqual(holdings, [
    { symbol: "AAPL", weight: 75 },
    { symbol: "SPY", weight: 25 },
  ]);
  assert.equal(holdings.reduce((sum, holding) => sum + holding.weight, 0), 100);
});

test("Webull login URL accepts only same-origin relative return paths", () => {
  assert.equal(webullLoginUrl("/"), "/api/webull/auth/login?return_to=/");
  assert.equal(webullLoginUrl("/?source=webull"), "/api/webull/auth/login?return_to=%2F%3Fsource%3Dwebull");
  assert.equal(webullLoginUrl("/portfolio?source=webull"), "/api/webull/auth/login?return_to=%2Fportfolio%3Fsource%3Dwebull");
  assert.equal(webullLoginUrl("https://attacker.example"), "/api/webull/auth/login?return_to=/");
  assert.equal(webullLoginUrl("//attacker.example"), "/api/webull/auth/login?return_to=/");
});

test("authenticated Webull mutations send the session CSRF token", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (input, init = {}) => {
    requests.push({ input: String(input), init });
    if (String(input).endsWith("/status")) {
      return Response.json({
        enabled: true,
        authenticated: true,
        connected: false,
        verificationInProgress: false,
        csrfToken: "csrf-token-for-test-only-1234567890",
        accounts: [],
        selectedAccountRef: null,
        dashboard: null,
      });
    }
    return Response.json({ message: "Sync accepted" });
  };

  try {
    await getWebullStatus();
    await syncWebull(null);
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(requests[1].input, "/api/webull/sync");
  const headers = new Headers(requests[1].init.headers);
  assert.equal(headers.get("x-portfolio-csrf"), "csrf-token-for-test-only-1234567890");
});
