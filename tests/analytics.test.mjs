import assert from "node:assert/strict";
import test from "node:test";
import {
  analyzePortfolio,
  correlationAt,
  covariance,
  MIN_OPTIMIZER_HOLDINGS,
  moments,
  OPTIMIZER_MAX_WEIGHT,
  optimizerMaxWeightForHoldingCount,
  optimizePortfolio,
  packSeries,
  projectCappedSimplex,
} from "../app/analytics.ts";

function pricesFromReturns(returns) {
  const prices = [100];
  for (const value of returns) prices.push(prices.at(-1) * Math.exp(value));
  return prices;
}

function rawSeries(returns) {
  const start = Date.UTC(2025, 0, 1);
  const prices = pricesFromReturns(returns);
  return {
    dates: prices.map((_, index) => new Date(start + index * 86_400_000).toISOString().slice(0, 10)),
    prices,
  };
}

const alternating = Array.from({ length: 90 }, (_, index) => index % 2 ? -.006 : .01);
const opposite = alternating.map(value => -value);

test("stable moments and covariance match known samples", () => {
  assert.equal(moments([1, 2, 3]).mean, 2);
  assert.equal(moments([1, 2, 3]).variance, 1);
  assert.equal(covariance([1, 2, 3], [2, 4, 6]), 2);
});

test("packed analysis produces symmetric perfect negative correlation and spread volatility", () => {
  const series = new Map([
    ["AAA", packSeries("AAA", rawSeries(alternating))],
    ["BBB", packSeries("BBB", rawSeries(opposite))],
  ]);
  const result = analyzePortfolio({
    revision: 1,
    snapshotKey: "fixture",
    holdings: [{ symbol: "AAA", weight: 50 }, { symbol: "BBB", weight: 50 }],
    benchmark: "AAA",
    riskFreeRate: .04,
    rebalanceBand: 2.5,
    driftDays: 63,
  }, series);

  assert.equal(result.observationCount, 90);
  assert.ok(Math.abs(correlationAt(result.correlationPacked, 2, 0, 1) + 1) < 1e-6);
  assert.equal(correlationAt(result.correlationPacked, 2, 1, 0), correlationAt(result.correlationPacked, 2, 0, 1));
  assert.equal(correlationAt(result.correlationPacked, 2, 0, 0), 1);
  const expectedSpread = Math.sqrt(moments(alternating.map((value, index) => value - opposite[index])).variance * 252);
  assert.ok(Math.abs(result.pairs[0].spreadVolatility - expectedSpread) < 1e-10);
  assert.deepEqual([...result.clusterOrder].sort(), [0, 1]);
});

test("capped-simplex projection and optimizer respect allocation constraints", () => {
  const projected = projectCappedSimplex([2, -1, .2], .6);
  assert.ok(Math.abs(projected.reduce((sum, value) => sum + value, 0) - 1) < 1e-9);
  assert.ok(projected.every(value => value >= 0 && value <= .6 + 1e-9));
  assert.equal(MIN_OPTIMIZER_HOLDINGS, 2);
  assert.equal(optimizerMaxWeightForHoldingCount(2), 1 / 3);
  assert.equal(optimizerMaxWeightForHoldingCount(4), .2);
  assert.equal(optimizerMaxWeightForHoldingCount(5), 1 / 6);
  assert.equal(optimizerMaxWeightForHoldingCount(8), OPTIMIZER_MAX_WEIGHT);

  const smallSeries = new Map([
    ["AAA", packSeries("AAA", rawSeries(alternating))],
    ["BBB", packSeries("BBB", rawSeries(opposite))],
  ]);
  const smallResult = analyzePortfolio({
    revision: 2,
    snapshotKey: "small-optimizer",
    holdings: [{ symbol: "AAA", weight: 50 }, { symbol: "BBB", weight: 50 }],
    benchmark: "AAA",
    riskFreeRate: .04,
    rebalanceBand: 2.5,
    driftDays: 63,
  }, smallSeries);
  const smallWeights = optimizePortfolio(smallResult, "maxsharpe", .04);
  assert.equal(smallWeights.length, 3);
  assert.ok(Math.abs(smallWeights.reduce((sum, value) => sum + value, 0) - 1) < 1e-8);
  assert.ok(smallWeights.every(value => value <= 1 / 3 + 1e-8));

  const symbols = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"];
  const series = new Map(symbols.map((symbol, symbolIndex) => {
    const returns = alternating.map((value, index) => (
      value * (1 + symbolIndex * .04)
      + ((index + symbolIndex) % 9 === 0 ? .0002 * (symbolIndex + 1) : 0)
    ));
    return [symbol, packSeries(symbol, rawSeries(returns))];
  }));
  const result = analyzePortfolio({
    revision: 2,
    snapshotKey: "optimizer",
    holdings: symbols.map(symbol => ({ symbol, weight: 12.5 })),
    benchmark: "AAA",
    riskFreeRate: .04,
    rebalanceBand: 2.5,
    driftDays: 63,
  }, series);
  for (const objective of ["minvol", "maxsharpe"]) {
    const weights = optimizePortfolio(result, objective, .04);
    assert.equal(weights.length, symbols.length + 1);
    assert.ok(Math.abs(weights.reduce((sum, value) => sum + value, 0) - 1) < 1e-8);
    assert.ok(weights.every(value => value >= -1e-10 && value <= OPTIMIZER_MAX_WEIGHT + 1e-8));
  }
});
