import assert from "node:assert/strict";
import test from "node:test";
import { normalizeServiceDashboard } from "../app/webull-server.ts";

const serviceDashboard = {
  portfolio: {
    account: { accountId: "account-1234", accountType: "CASH", currency: "USD" },
    balance: {
      accountId: "account-1234",
      asOf: "2026-08-20T20:10:00Z",
      currency: "USD",
      equity: "15000",
      cash: "3000",
      marketValue: "12000",
      dayProfitLoss: "75",
      unrealizedProfitLoss: "900",
    },
    positions: [
      { externalPositionId: "position-aapl", symbol: "AAPL", instrumentType: "EQUITY", currency: "USD", quantity: "20", marketValue: "5000", costBasis: "4400", unrealizedProfitLoss: "600" },
      { externalPositionId: "position-spy", symbol: "SPY", instrumentType: "ETF", currency: "USD", quantity: "10", marketValue: "7000", costBasis: "6700", unrealizedProfitLoss: "300" },
      { externalPositionId: "position-option", symbol: "AAPL260918C00300000", instrumentType: "OPTION", currency: "USD", quantity: "1", marketValue: "500" },
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
  assert.equal(dashboard.accountId, "account-1234");
  assert.equal(dashboard.holdingsReady, true);
  assert.equal(dashboard.performanceReady, true);
  assert.equal(dashboard.analyticsCoverage, 0.8);
  assert.equal(dashboard.metrics.netAccountValue.value, 15000);
  assert.equal(dashboard.metrics.netAccountValue.source, "Webull reported");
  assert.equal(dashboard.metrics.dayProfitLoss.value, 75);
  assert.equal(dashboard.metrics.timeWeightedReturn.source, "Portfolio Lab computed");
  assert.ok(Math.abs(dashboard.metrics.benchmarkReturn.value - 0.02) < 1e-12);
  assert.ok(Math.abs(dashboard.metrics.excessReturn.value - ((1.03 / 1.02) - 1)) < 1e-12);
  assert.equal(dashboard.metrics.investmentGain.value, 1000);
  assert.equal(dashboard.holdings.filter(item => item.eligibleForAnalysis).length, 2);
  assert.equal(dashboard.exclusions.length, 1);
  assert.equal(dashboard.activities[0].activityId, "deposit-1");
  assert.equal(dashboard.chart.length, 3);
  assert.equal(dashboard.chart.at(-1).benchmarkGrowth, 102);
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
