export type SimulationSettings = { years: number; paths: number; blockDays: number; seed: number; targetPercent: number; drawdownPercent: number };
export type SimulationPoint = { month: number; p05: number; p25: number; p50: number; p75: number; p95: number };
export type SimulationResult = { points: SimulationPoint[]; lossProbability: number; targetProbability: number; drawdownProbability: number; medianDrawdown: number; settings: SimulationSettings; observations: number };

function quantile(sorted: number[], p: number) {
  const index = (sorted.length - 1) * p, lower = Math.floor(index);
  return sorted[lower] + (sorted[Math.ceil(index)] - sorted[lower]) * (index - lower);
}

/** Moving-block bootstrap of the analyzer's constant-weight daily log returns. */
export function simulatePortfolio(returns: Float64Array, settings: SimulationSettings, progress: (percent: number) => void = () => {}): SimulationResult {
  const { years, paths, blockDays, seed, targetPercent, drawdownPercent } = settings;
  if (!Number.isInteger(years) || years < 1 || years > 10 || !Number.isInteger(paths) || paths < 100 || paths > 5000 || !Number.isInteger(blockDays) || blockDays < 1 || blockDays > 63 || !Number.isInteger(seed) || seed < 0 || seed > 4294967295 || !Number.isFinite(targetPercent) || targetPercent <= -100 || targetPercent > 1000 || !Number.isFinite(drawdownPercent) || drawdownPercent <= 0 || drawdownPercent > 100) throw new Error("Check the simulation settings.");
  if (returns.length < Math.max(60, blockDays) || !returns.every(Number.isFinite)) throw new Error("At least 60 finite daily observations and a full sampling block are required.");
  let state = seed >>> 0;
  const random = () => {
    state = (state + 0x6D2B79F5) >>> 0;
    let value = Math.imul(state ^ (state >>> 15), state | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
  const months = Array.from({ length: years * 12 + 1 }, () => [] as number[]);
  const drawdowns: number[] = [];
  let losses = 0, targets = 0, breaches = 0;
  for (let path = 0; path < paths; path++) {
    let cumulative = 0, peak = 0, worst = 0, start = 0;
    months[0].push(0);
    for (let day = 0; day < years * 252; day++) {
      if (day % blockDays === 0) start = Math.floor(random() * (returns.length - blockDays + 1));
      cumulative += returns[start + day % blockDays];
      peak = Math.max(peak, cumulative);
      worst = Math.max(worst, -Math.expm1(cumulative - peak));
      if ((day + 1) % 21 === 0) {
        const value = Math.expm1(cumulative) * 100;
        if (!Number.isFinite(value)) throw new Error("Simulated returns exceeded numerical limits; use a shorter horizon.");
        months[(day + 1) / 21].push(value);
      }
    }
    const terminal = Math.expm1(cumulative) * 100;
    losses += Number(terminal < 0);
    targets += Number(terminal >= targetPercent);
    breaches += Number(worst * 100 >= drawdownPercent);
    drawdowns.push(worst * 100);
    if ((path + 1) % 100 === 0) progress(Math.round((path + 1) / paths * 100));
  }
  drawdowns.sort((a, b) => a - b);
  return {
    points: months.map((values, month) => {
      values.sort((a, b) => a - b);
      return { month, p05: quantile(values, .05), p25: quantile(values, .25), p50: quantile(values, .5), p75: quantile(values, .75), p95: quantile(values, .95) };
    }),
    lossProbability: losses / paths, targetProbability: targets / paths, drawdownProbability: breaches / paths,
    medianDrawdown: quantile(drawdowns, .5), settings: { ...settings }, observations: returns.length,
  };
}
