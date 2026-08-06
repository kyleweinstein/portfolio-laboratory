export const STYLE_OPTIONS = [
  "Large Growth", "Large Blend", "Large Value", "Mid Cap", "Small Cap",
  "International Developed", "Emerging Markets", "Fixed Income", "Real Assets",
] as const;
export const SECTOR_OPTIONS = [
  "Technology", "Communication Services", "Consumer Discretionary", "Consumer Staples",
  "Energy", "Financials", "Health Care", "Industrials", "Materials", "Real Estate",
  "Utilities", "Diversified Equity", "Fixed Income", "Commodities",
] as const;
export const FACTOR_OPTIONS = [
  "Market", "Value", "Momentum", "Quality", "Low Volatility", "Size",
  "Duration", "Inflation", "Real Assets",
] as const;

export type StyleName = typeof STYLE_OPTIONS[number];
export type SectorName = typeof SECTOR_OPTIONS[number];
export type FactorName = typeof FACTOR_OPTIONS[number];
export type HoldingInput = { symbol: string; weight: number };
export type RawSeries = { dates: string[]; prices: number[] };
export type PackedSeries = { symbol: string; days: Int32Array; prices: Float64Array };
export type Stat = { annualReturn: number; volatility: number; sharpe: number; maxDrawdown: number; var95: number; cvar95: number; beta?: number };
export type PairInsight = { a: number; b: number; correlation: number; spreadVolatility: number; rebalancePotential: number };
export type Classification<T extends string> = { name: T; correlation: number; runnerUp: T | null; runnerUpCorrelation: number };
export type HoldingClassification = {
  symbol: string;
  style: Classification<StyleName>;
  sector: Classification<SectorName>;
  factor: Classification<FactorName>;
};
export type CategoryRow = { name: string; exposure: number; suggestions: { name: string; correlation: number }[] };
export type CategoryView = {
  rows: CategoryRow[];
  additions: { name: string; exposure: number; correlation: number; score: number }[];
  covered: number;
  total: number;
};
export type RebalanceBucket = {
  id: number;
  indices: number[];
  targetMix: number[];
  driftedMix: number[];
  deltas: number[];
  drift: number;
  averageCorrelation: number;
  rebalancePotential: number;
  triggered: boolean;
};
export type AnalysisInput = {
  revision: number;
  snapshotKey: string;
  holdings: HoldingInput[];
  benchmark: string;
  riskFreeRate: number;
  rebalanceBand: number;
  driftDays: number;
};
export type AnalysisResult = {
  revision: number;
  snapshotKey: string;
  symbols: string[];
  alignedDays: Int32Array;
  assetReturns: Float64Array[];
  benchmarkReturns: Float64Array;
  portfolioReturns: Float64Array;
  targetWeights: number[];
  driftedWeights: number[];
  driftWindow: number;
  portfolio: Stat;
  holdings: Stat[];
  correlationPacked: Float32Array;
  correlationSize: number;
  clusterOrder: number[];
  pairs: PairInsight[];
  buckets: RebalanceBucket[];
  classifications: HoldingClassification[];
  styleView: CategoryView;
  sectorView: CategoryView;
  factorView: CategoryView;
  observationCount: number;
};

export const STYLE_PROXIES: Record<StyleName, string> = {
  "Large Growth": "VUG", "Large Blend": "SPY", "Large Value": "VTV", "Mid Cap": "VO",
  "Small Cap": "VB", "International Developed": "VEA", "Emerging Markets": "VWO",
  "Fixed Income": "IEF", "Real Assets": "GLD",
};
export const SECTOR_PROXIES: Record<SectorName, string> = {
  "Technology": "XLK", "Communication Services": "XLC", "Consumer Discretionary": "XLY",
  "Consumer Staples": "XLP", "Energy": "XLE", "Financials": "XLF", "Health Care": "XLV",
  "Industrials": "XLI", "Materials": "XLB", "Real Estate": "XLRE", "Utilities": "XLU",
  "Diversified Equity": "SPY", "Fixed Income": "IEF", "Commodities": "GLD",
};
export const FACTOR_PROXIES: Record<FactorName, string> = {
  "Market": "SPY", "Value": "VLUE", "Momentum": "MTUM", "Quality": "QUAL",
  "Low Volatility": "USMV", "Size": "VB", "Duration": "IEF", "Inflation": "TIP", "Real Assets": "GLD",
};
export const ALL_PROXY_SYMBOLS = [...new Set([
  ...Object.values(STYLE_PROXIES), ...Object.values(SECTOR_PROXIES), ...Object.values(FACTOR_PROXIES),
])];

const DAY_MS = 86_400_000;
export const dayToLabel = (day: number) => new Date(day * DAY_MS).toISOString().slice(0, 10);
export const packSeries = (symbol: string, series: RawSeries): PackedSeries => ({
  symbol,
  days: Int32Array.from(series.dates.map(date => Math.floor(Date.parse(`${date}T00:00:00Z`) / DAY_MS))),
  prices: Float64Array.from(series.prices),
});

export function normalizeWeights(values: number[]) {
  const clean = values.map(value => Math.max(0, Number(value) || 0));
  const total = clean.reduce((sum, value) => sum + value, 0);
  return total > 0 ? clean.map(value => value / total) : clean.map(() => 1 / Math.max(clean.length, 1));
}

export function moments(values: ArrayLike<number>) {
  let mean = 0;
  let m2 = 0;
  for (let index = 0; index < values.length; index++) {
    const delta = values[index] - mean;
    mean += delta / (index + 1);
    m2 += delta * (values[index] - mean);
  }
  const variance = values.length > 1 ? m2 / (values.length - 1) : 0;
  return { mean, variance, std: Math.sqrt(Math.max(0, variance)), m2 };
}

export function covariance(left: ArrayLike<number>, right: ArrayLike<number>) {
  const length = Math.min(left.length, right.length);
  let meanLeft = 0;
  let meanRight = 0;
  let coMoment = 0;
  for (let index = 0; index < length; index++) {
    const deltaLeft = left[index] - meanLeft;
    meanLeft += deltaLeft / (index + 1);
    meanRight += (right[index] - meanRight) / (index + 1);
    coMoment += deltaLeft * (right[index] - meanRight);
  }
  return length > 1 ? coMoment / (length - 1) : 0;
}

export function packedIndex(size: number, row: number, column: number) {
  const a = Math.min(row, column);
  const b = Math.max(row, column);
  return a * size - (a * (a - 1)) / 2 + (b - a);
}

export function correlationAt(packed: ArrayLike<number>, size: number, row: number, column: number) {
  return packed[packedIndex(size, row, column)];
}

function alignedReturns(series: Map<string, PackedSeries>, symbols: string[]) {
  const packed = symbols.map(symbol => {
    const value = series.get(symbol);
    if (!value) throw new Error(`${symbol}: price history is not loaded`);
    return value;
  });
  const priceMaps = packed.map(item => new Map(Array.from(item.days, (day, index) => [day, item.prices[index]])));
  const days = Array.from(packed[0].days).filter(day => priceMaps.every(map => map.has(day))).sort((a, b) => a - b);
  if (days.length < 61) throw new Error("The holdings do not share enough aligned price history.");
  const returns = priceMaps.map(map => Float64Array.from(days.slice(1), (day, index) =>
    Math.log((map.get(day) as number) / (map.get(days[index]) as number))));
  return { days: Int32Array.from(days.slice(1)), returns };
}

function calculateStats(returns: Float64Array, riskFreeRate: number, benchmark?: Float64Array): Stat {
  const stats = moments(returns);
  const annualReturn = Math.exp(stats.mean * 252) - 1;
  const volatility = stats.std * Math.sqrt(252);
  const sorted = Array.from(returns).sort((a, b) => a - b);
  const cut = Math.max(0, Math.floor(sorted.length * .05) - 1);
  let wealth = 1;
  let peak = 1;
  let maxDrawdown = 0;
  for (const value of returns) {
    wealth *= Math.exp(value);
    peak = Math.max(peak, wealth);
    maxDrawdown = Math.min(maxDrawdown, wealth / peak - 1);
  }
  return {
    annualReturn,
    volatility,
    sharpe: volatility ? (annualReturn - riskFreeRate) / volatility : NaN,
    maxDrawdown,
    var95: -sorted[cut],
    cvar95: -moments(sorted.slice(0, cut + 1)).mean,
    beta: benchmark ? covariance(returns, benchmark) / Math.max(moments(benchmark).variance, 1e-16) : undefined,
  };
}

type ReturnSeries = { days: Int32Array; values: Float64Array };
function returnSeries(series: PackedSeries): ReturnSeries {
  return {
    days: series.days.slice(1),
    values: Float64Array.from(series.prices.slice(1), (price, index) => Math.log(price / series.prices[index])),
  };
}

function pairedCorrelation(left: ReturnSeries, right: ReturnSeries) {
  const rightMap = new Map(Array.from(right.days, (day, index) => [day, right.values[index]]));
  let count = 0;
  let meanLeft = 0;
  let meanRight = 0;
  let m2Left = 0;
  let m2Right = 0;
  let coMoment = 0;
  for (let index = 0; index < left.days.length; index++) {
    const rightValue = rightMap.get(left.days[index]);
    if (!Number.isFinite(rightValue)) continue;
    count++;
    const leftValue = left.values[index];
    const deltaLeft = leftValue - meanLeft;
    const deltaRight = (rightValue as number) - meanRight;
    meanLeft += deltaLeft / count;
    meanRight += deltaRight / count;
    m2Left += deltaLeft * (leftValue - meanLeft);
    m2Right += deltaRight * ((rightValue as number) - meanRight);
    coMoment += deltaLeft * ((rightValue as number) - meanRight);
  }
  return count >= 60 && m2Left > 0 && m2Right > 0 ? coMoment / Math.sqrt(m2Left * m2Right) : NaN;
}

function clusterOrder(symbols: string[], correlation: Float32Array) {
  if (symbols.length < 2) return symbols.map((_, index) => index);
  type Cluster = { id: number; size: number; order: number[]; stable: string };
  const clusters = new Map<number, Cluster>(symbols.map((symbol, index) => [index, { id: index, size: 1, order: [index], stable: symbol }]));
  const distances = new Map<string, number>();
  const distanceKey = (left: number, right: number) => left < right ? `${left}:${right}` : `${right}:${left}`;
  const getDistance = (left: number, right: number) => distances.get(distanceKey(left, right)) ?? 1;
  for (let left = 0; left < symbols.length; left++) {
    for (let right = left + 1; right < symbols.length; right++) {
      const value = correlationAt(correlation, symbols.length, left, right);
      distances.set(distanceKey(left, right), Number.isFinite(value) ? 1 - value : 1);
    }
  }

  let nextId = symbols.length;
  let chain: number[] = [];
  while (clusters.size > 1) {
    if (!chain.length || !clusters.has(chain.at(-1)!)) {
      chain = [[...clusters.values()].sort((a, b) => a.stable.localeCompare(b.stable))[0].id];
    }
    const current = chain.at(-1)!;
    const nearest = [...clusters.values()]
      .filter(cluster => cluster.id !== current)
      .sort((left, right) => getDistance(current, left.id) - getDistance(current, right.id) || left.stable.localeCompare(right.stable))[0].id;
    if (chain.length > 1 && nearest === chain.at(-2)) {
      const left = clusters.get(current)!;
      const right = clusters.get(nearest)!;
      const forward = getDistance(left.order.at(-1)!, right.order[0]);
      const reverse = getDistance(right.order.at(-1)!, left.order[0]);
      const order = forward < reverse || (forward === reverse && left.stable < right.stable)
        ? [...left.order, ...right.order]
        : [...right.order, ...left.order];
      const merged: Cluster = { id: nextId++, size: left.size + right.size, order, stable: left.stable < right.stable ? left.stable : right.stable };
      for (const other of clusters.values()) {
        if (other.id === left.id || other.id === right.id) continue;
        const average = (getDistance(left.id, other.id) * left.size + getDistance(right.id, other.id) * right.size) / merged.size;
        distances.set(distanceKey(merged.id, other.id), average);
      }
      clusters.delete(left.id);
      clusters.delete(right.id);
      clusters.set(merged.id, merged);
      chain = [];
    } else {
      chain.push(nearest);
    }
  }
  return [...clusters.values()][0].order;
}

export function analyzePortfolio(input: AnalysisInput, series: Map<string, PackedSeries>): AnalysisResult {
  const symbols = input.holdings.map(holding => holding.symbol);
  if (!symbols.length) throw new Error("Add at least one holding before analyzing.");
  if (new Set(symbols).size !== symbols.length) throw new Error("Each holding symbol must be unique.");
  const aligned = alignedReturns(series, [...symbols, input.benchmark]);
  const assetReturns = aligned.returns.slice(0, symbols.length);
  const benchmarkReturns = aligned.returns.at(-1)!;
  const targetWeights = normalizeWeights(input.holdings.map(holding => holding.weight));
  const driftWindow = Math.min(input.driftDays, aligned.days.length);
  const driftedWeights = normalizeWeights(targetWeights.map((weight, index) => {
    let cumulativeReturn = 0;
    for (let day = assetReturns[index].length - driftWindow; day < assetReturns[index].length; day++) cumulativeReturn += assetReturns[index][day];
    return weight * Math.exp(cumulativeReturn);
  }));
  const portfolioReturns = Float64Array.from(aligned.days, (_, day) =>
    assetReturns.reduce((sum, returns, index) => sum + returns[day] * targetWeights[index], 0));

  const assetMoments = assetReturns.map(returns => moments(returns));
  const standardized = assetReturns.map((returns, index) => {
    const scale = Math.sqrt(assetMoments[index].m2);
    return scale > 0 ? Float64Array.from(returns, value => (value - assetMoments[index].mean) / scale) : new Float64Array(returns.length);
  });
  const correlationPacked = new Float32Array(symbols.length * (symbols.length + 1) / 2);
  const covariancePacked = new Float64Array(correlationPacked.length);
  for (let left = 0; left < symbols.length; left++) {
    for (let right = left; right < symbols.length; right++) {
      let correlation = left === right && assetMoments[left].m2 > 0 ? 1 : 0;
      let covarianceValue = left === right ? assetMoments[left].variance : 0;
      if (left !== right) {
        let correlationSum = 0;
        let covarianceSum = 0;
        for (let day = 0; day < aligned.days.length; day++) {
          correlationSum += standardized[left][day] * standardized[right][day];
          covarianceSum += (assetReturns[left][day] - assetMoments[left].mean) * (assetReturns[right][day] - assetMoments[right].mean);
        }
        correlation = assetMoments[left].m2 > 0 && assetMoments[right].m2 > 0 ? correlationSum : NaN;
        covarianceValue = covarianceSum / Math.max(1, aligned.days.length - 1);
      }
      const index = packedIndex(symbols.length, left, right);
      correlationPacked[index] = correlation;
      covariancePacked[index] = covarianceValue;
    }
  }

  const pairs: PairInsight[] = [];
  for (let left = 0; left < symbols.length; left++) {
    for (let right = left + 1; right < symbols.length; right++) {
      const correlation = correlationAt(correlationPacked, symbols.length, left, right);
      const spreadVariance = Math.max(0, assetMoments[left].variance + assetMoments[right].variance - 2 * covariancePacked[packedIndex(symbols.length, left, right)]);
      const spreadVolatility = Math.sqrt(spreadVariance * 252);
      pairs.push({
        a: left, b: right, correlation, spreadVolatility,
        rebalancePotential: spreadVolatility * (1 - correlation) / 2,
      });
    }
  }
  pairs.sort((left, right) => left.correlation - right.correlation || right.rebalancePotential - left.rebalancePotential);
  const pairLookup = new Map(pairs.map(pair => [`${pair.a}:${pair.b}`, pair]));

  const remaining = new Set(symbols.map((_, index) => index));
  const bucketIndices: number[][] = [];
  for (const pair of pairs) {
    if (!remaining.has(pair.a) || !remaining.has(pair.b)) continue;
    bucketIndices.push([pair.a, pair.b]);
    remaining.delete(pair.a);
    remaining.delete(pair.b);
  }
  if (remaining.size === 1) {
    const last = [...remaining][0];
    if (bucketIndices.length) {
      const best = bucketIndices.map((indices, index) => ({
        index,
        average: indices.reduce((sum, other) => sum + correlationAt(correlationPacked, symbols.length, last, other), 0) / indices.length,
      })).sort((a, b) => a.average - b.average)[0].index;
      bucketIndices[best] = [...bucketIndices[best], last];
    } else bucketIndices.push([last]);
  }
  const buckets: RebalanceBucket[] = bucketIndices.map((indices, bucketIndex) => {
    const targetMix = normalizeWeights(indices.map(index => targetWeights[index]));
    const driftedMix = normalizeWeights(indices.map(index => driftedWeights[index]));
    const bucketCapital = indices.reduce((sum, index) => sum + driftedWeights[index], 0);
    const desiredWeights = targetMix.map(mix => mix * bucketCapital);
    const deltas = desiredWeights.map((desired, index) => desired - driftedWeights[indices[index]]);
    const values: PairInsight[] = [];
    for (let left = 0; left < indices.length; left++) for (let right = left + 1; right < indices.length; right++) {
      const low = Math.min(indices[left], indices[right]);
      const high = Math.max(indices[left], indices[right]);
      const pair = pairLookup.get(`${low}:${high}`);
      if (pair) values.push(pair);
    }
    const drift = Math.max(...driftedMix.map((mix, index) => Math.abs(mix - targetMix[index])));
    return {
      id: bucketIndex + 1, indices, targetMix, driftedMix, deltas, drift,
      averageCorrelation: values.length ? values.reduce((sum, value) => sum + value.correlation, 0) / values.length : NaN,
      rebalancePotential: values.length ? values.reduce((sum, value) => sum + value.rebalancePotential, 0) / values.length : NaN,
      triggered: drift >= input.rebalanceBand / 100,
    };
  });

  const returnsBySymbol = new Map<string, ReturnSeries>();
  for (const [symbol, value] of series) returnsBySymbol.set(symbol, returnSeries(value));
  const pairCorrelationCache = new Map<string, number>();
  const seriesCorrelation = (left: string, right: string) => {
    const key = left < right ? `${left}:${right}` : `${right}:${left}`;
    if (pairCorrelationCache.has(key)) return pairCorrelationCache.get(key)!;
    const a = returnsBySymbol.get(left);
    const b = returnsBySymbol.get(right);
    const value = a && b ? pairedCorrelation(a, b) : NaN;
    pairCorrelationCache.set(key, value);
    return value;
  };
  const portfolioSeries: ReturnSeries = { days: aligned.days, values: portfolioReturns };
  const portfolioCorrelation = (symbol: string) => {
    const proxy = returnsBySymbol.get(symbol);
    return proxy ? pairedCorrelation(portfolioSeries, proxy) : NaN;
  };
  const inferCategory = <T extends string>(asset: string, options: readonly T[], proxies: Record<T, string>): Classification<T> => {
    const ranked = options.map(name => ({ name, correlation: seriesCorrelation(asset, proxies[name]) }))
      .filter(item => Number.isFinite(item.correlation)).sort((a, b) => b.correlation - a.correlation);
    const best = ranked[0] || { name: options[0], correlation: NaN };
    const runnerUp = ranked[1] || null;
    return { name: best.name, correlation: best.correlation, runnerUp: runnerUp?.name || null, runnerUpCorrelation: runnerUp?.correlation ?? NaN };
  };
  const classifications: HoldingClassification[] = symbols.map(symbol => ({
    symbol,
    style: inferCategory(symbol, STYLE_OPTIONS, STYLE_PROXIES),
    sector: inferCategory(symbol, SECTOR_OPTIONS, SECTOR_PROXIES),
    factor: inferCategory(symbol, FACTOR_OPTIONS, FACTOR_PROXIES),
  }));
  const buildCategoryView = <T extends string>(options: readonly T[], proxies: Record<T, string>, field: "style" | "sector" | "factor"): CategoryView => {
    const exposures = new Map<T, number>(options.map(option => [option, 0]));
    classifications.forEach((classification, index) => {
      const category = classification[field].name as T;
      exposures.set(category, (exposures.get(category) || 0) + targetWeights[index]);
    });
    const available = options.filter(option => series.has(proxies[option]));
    const rows = options.filter(option => (exposures.get(option) || 0) > .0005).map(option => ({
      name: option,
      exposure: exposures.get(option) || 0,
      suggestions: available.filter(candidate => candidate !== option)
        .map(candidate => ({ name: candidate, correlation: seriesCorrelation(proxies[option], proxies[candidate]) }))
        .filter(candidate => Number.isFinite(candidate.correlation)).sort((a, b) => a.correlation - b.correlation).slice(0, 3),
    })).sort((a, b) => b.exposure - a.exposure);
    const neutralWeight = 1 / Math.max(available.length, 1);
    const additions = available.map(option => {
      const exposure = exposures.get(option) || 0;
      const correlation = portfolioCorrelation(proxies[option]);
      const underweight = Math.max(0, neutralWeight - exposure) / neutralWeight;
      const diversification = Number.isFinite(correlation) ? (1 - correlation) / 2 : 0;
      return { name: option, exposure, correlation, score: underweight * diversification };
    }).filter(option => option.exposure < neutralWeight * .8 && Number.isFinite(option.correlation))
      .sort((a, b) => b.score - a.score || a.correlation - b.correlation).slice(0, 3);
    return { rows, additions, covered: available.length, total: options.length };
  };

  return {
    revision: input.revision,
    snapshotKey: input.snapshotKey,
    symbols,
    alignedDays: aligned.days,
    assetReturns,
    benchmarkReturns,
    portfolioReturns,
    targetWeights,
    driftedWeights,
    driftWindow,
    portfolio: calculateStats(portfolioReturns, input.riskFreeRate, benchmarkReturns),
    holdings: assetReturns.map(returns => calculateStats(returns, input.riskFreeRate, benchmarkReturns)),
    correlationPacked,
    correlationSize: symbols.length,
    clusterOrder: clusterOrder(symbols, correlationPacked),
    pairs,
    buckets,
    classifications,
    styleView: buildCategoryView(STYLE_OPTIONS, STYLE_PROXIES, "style"),
    sectorView: buildCategoryView(SECTOR_OPTIONS, SECTOR_PROXIES, "sector"),
    factorView: buildCategoryView(FACTOR_OPTIONS, FACTOR_PROXIES, "factor"),
    observationCount: aligned.days.length,
  };
}

export function projectCappedSimplex(values: number[], cap = .6) {
  if (!values.length) return [];
  if (values.length * cap < 1) return values.map(() => 1 / values.length);
  let low = Math.min(...values.map(value => value - cap));
  let high = Math.max(...values);
  for (let iteration = 0; iteration < 70; iteration++) {
    const lambda = (low + high) / 2;
    const total = values.reduce((sum, value) => sum + Math.min(cap, Math.max(0, value - lambda)), 0);
    if (total > 1) low = lambda;
    else high = lambda;
  }
  const lambda = (low + high) / 2;
  return values.map(value => Math.min(cap, Math.max(0, value - lambda)));
}

export function optimizePortfolio(result: AnalysisResult, objective: "minvol" | "maxsharpe", riskFreeRate: number) {
  const count = result.assetReturns.length;
  if (count < 2) throw new Error("Optimization requires at least two holdings.");
  const means = result.assetReturns.map(returns => moments(returns).mean);
  const covarianceMatrix = result.assetReturns.map((left, row) => result.assetReturns.map((right, column) =>
    row === column ? moments(left).variance : covariance(left, right)));
  let weights = projectCappedSimplex(result.targetWeights, .6);
  const evaluate = (candidate: number[]) => {
    const meanDaily = candidate.reduce((sum, weight, index) => sum + weight * means[index], 0);
    const sigmaWeights = covarianceMatrix.map(row => row.reduce((sum, value, index) => sum + value * candidate[index], 0));
    const variance = Math.max(1e-16, candidate.reduce((sum, weight, index) => sum + weight * sigmaWeights[index], 0));
    const volatility = Math.sqrt(variance * 252);
    const annualReturn = Math.exp(meanDaily * 252) - 1;
    if (objective === "minvol") {
      return { score: -volatility, gradient: sigmaWeights.map(value => -value / Math.sqrt(variance)) };
    }
    const excess = annualReturn - riskFreeRate;
    const returnScale = Math.exp(meanDaily * 252) * 252;
    return {
      score: excess / volatility,
      gradient: means.map((mean, index) => returnScale * mean / volatility - excess * 252 * sigmaWeights[index] / (volatility ** 3)),
    };
  };

  for (let iteration = 0; iteration < 500; iteration++) {
    const current = evaluate(weights);
    const gradientScale = Math.max(...current.gradient.map(Math.abs), 1e-12);
    const direction = current.gradient.map(value => value / gradientScale);
    let step = objective === "minvol" ? .08 : .04;
    let candidate = weights;
    let accepted = false;
    for (let lineSearch = 0; lineSearch < 18; lineSearch++) {
      candidate = projectCappedSimplex(weights.map((weight, index) => weight + step * direction[index]), .6);
      if (evaluate(candidate).score >= current.score - 1e-12) {
        accepted = true;
        break;
      }
      step /= 2;
    }
    if (!accepted) break;
    const movement = Math.max(...candidate.map((value, index) => Math.abs(value - weights[index])));
    weights = candidate;
    if (movement < 1e-8) break;
  }
  return weights;
}
