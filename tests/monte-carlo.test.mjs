import test from 'node:test';
import assert from 'node:assert/strict';
import { simulatePortfolio } from '../app/monte-carlo.ts';
const settings = { years: 1, paths: 100, blockDays: 21, seed: 42, targetPercent: 20, drawdownPercent: 20 };
test('constant positive log returns compound exactly', () => {
  const result = simulatePortfolio(new Float64Array(252).fill(.001), settings);
  const expected = Math.expm1(.001 * 252) * 100;
  assert.ok(Math.abs(result.points.at(-1).p50 - expected) < 1e-10);
  assert.equal(result.points.at(-1).p05, result.points.at(-1).p95);
  assert.equal(result.lossProbability, 0);
  assert.equal(result.targetProbability, 1);
  assert.equal(result.medianDrawdown, 0);
});
test('negative returns include drawdown from starting wealth', () => {
  const result = simulatePortfolio(new Float64Array(252).fill(-.001), settings);
  assert.equal(result.lossProbability, 1);
  assert.equal(result.targetProbability, 0);
  assert.equal(result.drawdownProbability, 1);
  assert.ok(Math.abs(result.medianDrawdown + Math.expm1(-.252) * 100) < 1e-10);
});
test('flat returns are not counted as losses', () => {
  const result = simulatePortfolio(new Float64Array(100), settings);
  assert.equal(result.lossProbability, 0);
  assert.equal(result.medianDrawdown, 0);
  assert.equal(result.points.at(-1).p95, 0);
});
test('seeds reproduce paths and percentiles remain ordered', () => {
  const history = Float64Array.from({ length: 756 }, (_, i) => Math.sin(i * .7) * .02 + .0002);
  const a = simulatePortfolio(history, settings);
  assert.deepEqual(a, simulatePortfolio(history, settings));
  assert.notDeepEqual(a.points, simulatePortfolio(history, { ...settings, seed: 43 }).points);
  assert.equal(a.points.length, 13);
  for (const p of a.points) assert.ok(p.p05 <= p.p25 && p.p25 <= p.p50 && p.p50 <= p.p75 && p.p75 <= p.p95);
  for (const p of [a.lossProbability, a.targetProbability, a.drawdownProbability]) assert.ok(p >= 0 && p <= 1);
});
test('invalid observations and settings are rejected', () => {
  assert.throws(() => simulatePortfolio(new Float64Array(59), settings));
  assert.throws(() => simulatePortfolio(new Float64Array(100).fill(NaN), settings));
  for (const patch of [{ years: 0 }, { paths: 10000 }, { blockDays: 64 }, { seed: -1 }, { targetPercent: NaN }, { drawdownPercent: 0 }]) assert.throws(() => simulatePortfolio(new Float64Array(100), { ...settings, ...patch }));
});
