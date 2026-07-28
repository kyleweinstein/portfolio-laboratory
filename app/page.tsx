"use client";

import { useEffect, useMemo, useRef, useState } from "react";

type Holding = { symbol: string; weight: number; style: string; sector: string; factor: string };
type Series = { dates: string[]; prices: number[] };
type Stat = { annualReturn: number; volatility: number; sharpe: number; maxDrawdown: number; var95: number; cvar95: number; beta?: number };
type PairInsight = { a: number; b: number; correlation: number; spreadVolatility: number; rebalancePotential: number };
type ChartSeries = { name: string; returns: number[]; color: string };
type RadarDatum = { label: string; value: number };

const DEFAULT_HOLDINGS: Holding[] = [
  { symbol: "SPY", weight: 35, style: "Blend", sector: "Diversified", factor: "Market" },
  { symbol: "QQQ", weight: 25, style: "Growth", sector: "Technology", factor: "Momentum" },
  { symbol: "IEF", weight: 20, style: "Defensive", sector: "Fixed income", factor: "Duration" },
  { symbol: "GLD", weight: 10, style: "Defensive", sector: "Commodities", factor: "Inflation" },
  { symbol: "VNQ", weight: 10, style: "Income", sector: "Real estate", factor: "Real assets" },
];
const COLORS = ["#FF3B00", "#2E5CC8", "#1B6B45", "#7B3FB5", "#9A6A00", "#111111"];
const STYLE_OPTIONS = ["Blend", "Growth", "Value", "Defensive", "Income"];
const SECTOR_OPTIONS = ["Diversified", "Technology", "Fixed income", "Commodities", "Real estate", "Healthcare", "Financials", "Industrials", "Consumer", "Energy", "Utilities", "Other"];
const FACTOR_OPTIONS = ["Market", "Momentum", "Value", "Quality", "Low volatility", "Size", "Duration", "Inflation", "Real assets"];

const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
const sampleStd = (xs: number[]) => Math.sqrt(xs.reduce((a, x) => a + (x - mean(xs)) ** 2, 0) / Math.max(1, xs.length - 1));
const covariance = (a: number[], b: number[]) => a.reduce((s, x, i) => s + (x - mean(a)) * (b[i] - mean(b)), 0) / Math.max(1, a.length - 1);
const pct = (n: number, digits = 1) => Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : "—";
const pp = (n: number, digits = 1) => Number.isFinite(n) ? `${(n * 100).toFixed(digits)} pp` : "—";
const num = (n: number, digits = 2) => Number.isFinite(n) ? n.toFixed(digits) : "—";
const normalizeWeights = (values: number[]) => {
  const clean = values.map(value => Math.max(0, Number(value) || 0));
  const total = clean.reduce((a, b) => a + b, 0);
  return total > 0 ? clean.map(value => value / total) : clean.map(() => 1 / Math.max(clean.length, 1));
};

function buildExposureData(holdings: Holding[], weights: number[], field: "style" | "sector" | "factor", options: string[]): RadarDatum[] {
  const totals = new Map<string, number>();
  holdings.forEach((holding, index) => totals.set(holding[field], (totals.get(holding[field]) || 0) + (weights[index] || 0)));
  const ranked = [...totals].map(([label, value]) => ({ label, value })).filter(item => item.value > 0).sort((a, b) => b.value - a.value);
  const visible = ranked.length > 6
    ? [...ranked.slice(0, 5), { label: "Other", value: ranked.slice(5).reduce((sum, item) => sum + item.value, 0) }]
    : ranked;
  for (const option of options) {
    if (visible.length >= 3) break;
    if (!visible.some(item => item.label === option)) visible.push({ label: option, value: 0 });
  }
  return visible;
}

function fetchSeries(symbol: string, years: number): Promise<Series> {
  const url = `/api/market?symbol=${encodeURIComponent(symbol)}&years=${years}`;
  return fetch(url).then(async (res) => {
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json?.error || `${symbol}: data request failed (${res.status})`);
    const pairs = (json?.dates || []).map((date: string, i: number) => [date, json?.prices?.[i]] as const)
      .filter((pair: readonly [string, number]) => Number.isFinite(pair[1]) && pair[1] > 0);
    if (pairs.length < 60) throw new Error(`${symbol}: insufficient price history`);
    return { dates: pairs.map(pair => pair[0]), prices: pairs.map(pair => pair[1]) };
  });
}

function alignedReturns(series: Record<string, Series>, symbols: string[]) {
  const maps = symbols.map(symbol => new Map(series[symbol].dates.map((date, i) => [date, series[symbol].prices[i]])));
  const dates = [...maps[0].keys()].filter(date => maps.every(map => map.has(date))).sort();
  const returns = symbols.map((_, j) => dates.slice(1).map((date, i) => Math.log((maps[j].get(date) as number) / (maps[j].get(dates[i]) as number))));
  return { dates: dates.slice(1), returns };
}

function calculateStats(returns: number[], riskFreeRate: number, benchmark?: number[]): Stat {
  const annualReturn = Math.exp(mean(returns) * 252) - 1;
  const volatility = sampleStd(returns) * Math.sqrt(252);
  const sorted = [...returns].sort((a, b) => a - b);
  const cut = Math.max(0, Math.floor(sorted.length * .05) - 1);
  let wealth = 1, peak = 1, maxDrawdown = 0;
  returns.forEach(value => {
    wealth *= Math.exp(value);
    peak = Math.max(peak, wealth);
    maxDrawdown = Math.min(maxDrawdown, wealth / peak - 1);
  });
  return {
    annualReturn,
    volatility,
    sharpe: volatility ? (annualReturn - riskFreeRate) / volatility : NaN,
    maxDrawdown,
    var95: -sorted[cut],
    cvar95: -mean(sorted.slice(0, cut + 1)),
    beta: benchmark ? covariance(returns, benchmark) / (sampleStd(benchmark) ** 2) : undefined,
  };
}

function projectCapped(weights: number[], cap: number) {
  let projected = weights.map(value => Math.max(0, value));
  for (let pass = 0; pass < 12; pass++) {
    const total = projected.reduce((a, b) => a + b, 0) || 1;
    projected = projected.map(value => value / total);
    const excess = projected.reduce((sum, value) => sum + Math.max(0, value - cap), 0);
    projected = projected.map(value => Math.min(cap, value));
    if (excess < 1e-10) break;
    const room = projected.reduce((sum, value) => sum + Math.max(0, cap - value), 0);
    projected = projected.map(value => value < cap ? value + excess * (cap - value) / Math.max(room, 1e-12) : value);
  }
  return projected;
}

function classifyCorrelation(correlation: number) {
  if (correlation < 0) return "Anticorrelated";
  if (correlation < .25) return "Low correlation";
  if (correlation < .55) return "Moderate overlap";
  return "High overlap";
}

function App() {
  const [holdings, setHoldings] = useState<Holding[]>(DEFAULT_HOLDINGS);
  const [benchmark, setBenchmark] = useState("SPY");
  const [years, setYears] = useState(3);
  const [riskFreeRate, setRiskFreeRate] = useState(.04);
  const [rebalanceBand, setRebalanceBand] = useState(2.5);
  const [driftDays, setDriftDays] = useState(63);
  const [data, setData] = useState<Record<string, Series>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [optimized, setOptimized] = useState<number[] | null>(null);
  const [pendingOptimization, setPendingOptimization] = useState<"minvol" | "maxsharpe" | null>(null);
  const [selectedSeries, setSelectedSeries] = useState<string[]>(["Portfolio", "SPY", "QQQ"]);
  const didAutoLoad = useRef(false);

  const activeHoldings = holdings
    .map((holding, rowIndex) => ({ ...holding, rowIndex, symbol: holding.symbol.trim().toUpperCase() }))
    .filter(holding => Boolean(holding.symbol));
  const symbols = activeHoldings.map(holding => holding.symbol);
  const allocationTotal = activeHoldings.reduce((sum, holding) => sum + (Number(holding.weight) || 0), 0);
  const allocationWeights = normalizeWeights(activeHoldings.map(holding => holding.weight));
  const styleExposure = buildExposureData(activeHoldings, allocationWeights, "style", STYLE_OPTIONS);
  const sectorExposure = buildExposureData(activeHoldings, allocationWeights, "sector", SECTOR_OPTIONS);
  const factorExposure = buildExposureData(activeHoldings, allocationWeights, "factor", FACTOR_OPTIONS);

  useEffect(() => {
    const valid = new Set(["Portfolio", ...symbols]);
    setSelectedSeries(previous => {
      const kept = previous.filter(name => valid.has(name));
      if (kept.length >= 2) return kept;
      return ["Portfolio", ...symbols.slice(0, 2)].slice(0, Math.min(3, symbols.length + 1));
    });
  }, [symbols.join("|")]);

  const calculation = useMemo(() => {
    if (!symbols.length || !symbols.every(symbol => data[symbol]) || !data[benchmark]) return null;
    const aligned = alignedReturns(data, [...symbols, benchmark]);
    if (aligned.dates.length < 60) return null;
    const assetReturns = aligned.returns.slice(0, symbols.length);
    const benchmarkReturns = aligned.returns.at(-1)!;
    const targetWeights = normalizeWeights(activeHoldings.map(holding => holding.weight));
    const driftWindow = Math.min(driftDays, aligned.dates.length);
    const driftedWeights = normalizeWeights(targetWeights.map((weight, index) => {
      const cumulativeReturn = assetReturns[index].slice(-driftWindow).reduce((sum, value) => sum + value, 0);
      return weight * Math.exp(cumulativeReturn);
    }));
    const portfolioReturns = aligned.dates.map((_, i) => assetReturns.reduce((sum, returns, j) => sum + returns[i] * targetWeights[j], 0));
    const correlation = assetReturns.map(a => assetReturns.map(b => covariance(a, b) / (sampleStd(a) * sampleStd(b))));
    const pairs: PairInsight[] = [];
    for (let a = 0; a < symbols.length; a++) {
      for (let b = a + 1; b < symbols.length; b++) {
        const relativeReturns = assetReturns[a].map((value, i) => value - assetReturns[b][i]);
        const spreadVolatility = sampleStd(relativeReturns) * Math.sqrt(252);
        pairs.push({
          a,
          b,
          correlation: correlation[a][b],
          spreadVolatility,
          rebalancePotential: spreadVolatility * (1 - correlation[a][b]) / 2,
        });
      }
    }
    pairs.sort((left, right) => left.correlation - right.correlation || right.rebalancePotential - left.rebalancePotential);

    const remaining = new Set(symbols.map((_, index) => index));
    const bucketIndices: number[][] = [];
    while (remaining.size >= 2) {
      const candidate = pairs.find(pair => remaining.has(pair.a) && remaining.has(pair.b));
      if (!candidate) break;
      bucketIndices.push([candidate.a, candidate.b]);
      remaining.delete(candidate.a);
      remaining.delete(candidate.b);
    }
    if (remaining.size === 1) {
      const last = [...remaining][0];
      if (bucketIndices.length) {
        const bestBucket = bucketIndices
          .map((indices, index) => ({ index, average: mean(indices.map(other => correlation[last][other])) }))
          .sort((a, b) => a.average - b.average)[0].index;
        bucketIndices[bestBucket] = [...bucketIndices[bestBucket], last];
      } else {
        bucketIndices.push([last]);
      }
    }

    const buckets = bucketIndices.map((indices, bucketIndex) => {
      const targetMix = normalizeWeights(indices.map(index => targetWeights[index]));
      const driftedMix = normalizeWeights(indices.map(index => driftedWeights[index]));
      const bucketCapital = indices.reduce((sum, index) => sum + driftedWeights[index], 0);
      const desiredWeights = targetMix.map(mix => mix * bucketCapital);
      const deltas = desiredWeights.map((desired, i) => desired - driftedWeights[indices[i]]);
      const drift = Math.max(...driftedMix.map((mix, i) => Math.abs(mix - targetMix[i])));
      const bucketPairs = pairs.filter(pair => indices.includes(pair.a) && indices.includes(pair.b));
      return {
        id: bucketIndex + 1,
        indices,
        targetMix,
        driftedMix,
        deltas,
        drift,
        averageCorrelation: bucketPairs.length ? mean(bucketPairs.map(pair => pair.correlation)) : NaN,
        rebalancePotential: bucketPairs.length ? mean(bucketPairs.map(pair => pair.rebalancePotential)) : NaN,
        triggered: drift >= rebalanceBand / 100,
      };
    });

    return {
      aligned,
      assetReturns,
      benchmarkReturns,
      portfolioReturns,
      driftedWeights,
      targetWeights,
      driftWindow,
      portfolio: calculateStats(portfolioReturns, riskFreeRate, benchmarkReturns),
      holdings: assetReturns.map(returns => calculateStats(returns, riskFreeRate, benchmarkReturns)),
      correlation,
      pairs,
      buckets,
    };
  }, [data, symbols.join("|"), holdings, benchmark, riskFreeRate, rebalanceBand, driftDays]);

  async function refresh() {
    setLoading(true);
    setError("");
    setOptimized(null);
    try {
      if (new Set(symbols).size !== symbols.length) throw new Error("Each holding symbol must be unique.");
      const unique = [...new Set([...symbols, benchmark.toUpperCase()])];
      const results = await Promise.all(unique.map(async symbol => [symbol, await fetchSeries(symbol, years)] as const));
      setData(Object.fromEntries(results));
    } catch (caught) {
      setData({});
      setPendingOptimization(null);
      setError(caught instanceof Error ? caught.message : "Unable to retrieve price history.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (didAutoLoad.current) return;
    didAutoLoad.current = true;
    void refresh();
  }, []);

  function updateHolding(index: number, field: keyof Holding, value: string) {
    setHoldings(previous => previous.map((holding, i) => i === index
      ? { ...holding, [field]: field === "weight" ? Number(value) : field === "symbol" ? value.toUpperCase() : value }
      : holding));
  }

  function runOptimization(objective: "minvol" | "maxsharpe") {
    if (!calculation) return;
    const cap = .6;
    let weights = calculation.targetWeights.map(value => Math.max(.001, value));
    const score = (candidate: number[]) => {
      const returns = calculation.aligned.dates.map((_, i) => calculation.assetReturns.reduce((sum, asset, j) => sum + asset[i] * candidate[j], 0));
      const stats = calculateStats(returns, riskFreeRate);
      return objective === "minvol" ? -stats.volatility : stats.sharpe;
    };
    for (let step = 0; step < 250; step++) {
      const epsilon = .0001;
      const gradient = weights.map((_, j) => {
        const high = projectCapped(weights.map((value, k) => value + (k === j ? epsilon : 0)), cap);
        const low = projectCapped(weights.map((value, k) => value - (k === j ? epsilon : 0)), cap);
        return (score(high) - score(low)) / (2 * epsilon);
      });
      const rate = objective === "minvol" ? .02 : .006;
      weights = projectCapped(weights.map((value, j) => value + rate * gradient[j]), cap);
    }
    setOptimized(weights);
  }

  function optimize(objective: "minvol" | "maxsharpe") {
    if (calculation) {
      runOptimization(objective);
      return;
    }
    setPendingOptimization(objective);
    void refresh();
  }

  useEffect(() => {
    if (!pendingOptimization || !calculation) return;
    runOptimization(pendingOptimization);
    setPendingOptimization(null);
  }, [calculation, pendingOptimization]);

  function applyOptimized() {
    if (optimized) {
      setHoldings(previous => {
        let activeIndex = 0;
        return previous.map(holding => holding.symbol.trim()
          ? { ...holding, weight: +(optimized[activeIndex++] * 100).toFixed(2) }
          : holding);
      });
    }
  }

  function comparePair(a: number, b: number) {
    setSelectedSeries([symbols[a], symbols[b]]);
    document.getElementById("performance-overlay")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function toggleSeries(name: string) {
    setSelectedSeries(previous => previous.includes(name)
      ? (previous.length > 2 ? previous.filter(value => value !== name) : previous)
      : [...previous, name]);
  }

  const chartSeries: ChartSeries[] = calculation
    ? selectedSeries.flatMap(name => {
        if (name === "Portfolio") return [{ name, returns: calculation.portfolioReturns, color: "#111111" }];
        const index = symbols.indexOf(name);
        return index >= 0 ? [{ name, returns: calculation.assetReturns[index], color: COLORS[index % COLORS.length] }] : [];
      })
    : [];

  return <main>
    <header>
      <div><span className="eyebrow">Portfolio laboratory</span><h1>Clear allocation decisions, grounded in return history.</h1><p>Design, stress-check, pair, and rebalance a long-only portfolio using adjusted daily closes.</p></div>
      <button className="primary" onClick={refresh} disabled={loading}>{loading ? "Loading market data…" : "Refresh analysis"}</button>
    </header>

    <section className="control card"><div className="controls">
      <label>History<select value={years} onChange={event => setYears(+event.target.value)}><option value={1}>1 year</option><option value={3}>3 years</option><option value={5}>5 years</option></select></label>
      <label>Benchmark<input value={benchmark} onChange={event => setBenchmark(event.target.value.toUpperCase())} maxLength={12}/></label>
      <label>Risk-free rate<input type="number" step="0.1" value={(riskFreeRate * 100).toFixed(1)} onChange={event => setRiskFreeRate(+event.target.value / 100)}/><small>annual %</small></label>
      <label>Rebalance band<input type="number" min="0.1" step="0.5" value={rebalanceBand} onChange={event => setRebalanceBand(Math.max(.1, +event.target.value))}/><small>within-bucket percentage points</small></label>
      <label>Drift window<select value={driftDays} onChange={event => setDriftDays(+event.target.value)}><option value={21}>1 month</option><option value={63}>3 months</option><option value={126}>6 months</option><option value={252}>1 year</option></select><small>trading-day approximation</small></label>
      <div className="source"><b>Source</b><span>Yahoo Finance public chart data</span><small>Adjusted daily close; no key required</small></div>
    </div></section>

    <section className="card allocation-card">
      <div className="section-title">
        <div><span className="eyebrow">Allocation ledger</span><h2>Weights and analytical dimensions</h2></div>
        <span className={Math.abs(allocationTotal - 100) < .01 ? "pill ok" : "pill warn"}>Allocation {allocationTotal.toFixed(1)}%</span>
      </div>
      <div className="holdings-scroll">
        <div className="holding-head"><span>Symbol</span><span>Weight</span><span>Style</span><span>Sector</span><span>Primary factor</span><span></span></div>
        <div className="holdings">{holdings.map((holding, i) => <div className="holding" key={i}>
          <input aria-label={`Symbol ${i + 1}`} value={holding.symbol} onChange={event => updateHolding(i, "symbol", event.target.value)} placeholder="Ticker"/>
          <label className="weight-field"><span className="sr-only">Weight for {holding.symbol || `row ${i + 1}`}</span><input aria-label={`Weight ${holding.symbol}`} type="number" min="0" step="0.1" value={holding.weight} onChange={event => updateHolding(i, "weight", event.target.value)}/><i>%</i></label>
          <select aria-label={`Style ${holding.symbol}`} value={holding.style} onChange={event => updateHolding(i, "style", event.target.value)}>{STYLE_OPTIONS.map(option => <option key={option}>{option}</option>)}</select>
          <select aria-label={`Sector ${holding.symbol}`} value={holding.sector} onChange={event => updateHolding(i, "sector", event.target.value)}>{SECTOR_OPTIONS.map(option => <option key={option}>{option}</option>)}</select>
          <select aria-label={`Primary factor ${holding.symbol}`} value={holding.factor} onChange={event => updateHolding(i, "factor", event.target.value)}>{FACTOR_OPTIONS.map(option => <option key={option}>{option}</option>)}</select>
          <button className="icon" aria-label={`Remove ${holding.symbol}`} onClick={() => setHoldings(previous => previous.filter((_, j) => j !== i))}>×</button>
        </div>)}</div>
      </div>
      <button className="secondary" onClick={() => setHoldings(previous => [...previous, { symbol: "", weight: 0, style: "Blend", sector: "Diversified", factor: "Market" }])}>+ Add holding</button>
      <p className="note">Weights are normalized to 100% for calculations. Style, sector, and factor are editable analytical tags; verify the classifications for your use case.</p>
    </section>

    <section className="card optimizer-card">
      <div><span className="eyebrow">Portfolio design</span><h2>Constraint-aware optimizer</h2><p className="muted">Long-only; each holding capped at 60%. The optimizer loads market history automatically and can also start a refresh when data is not ready.</p></div>
      <div className="optimizer-actions">
        <div className="action-row"><button className="secondary" disabled={loading || activeHoldings.length < 2} onClick={() => optimize("minvol")}>{pendingOptimization === "minvol" ? "Preparing..." : "Minimum volatility"}</button><button className="secondary" disabled={loading || activeHoldings.length < 2} onClick={() => optimize("maxsharpe")}>{pendingOptimization === "maxsharpe" ? "Preparing..." : "Maximum Sharpe"}</button></div>
        {optimized && <div className="recommend"><b>Suggested target allocation</b><div>{symbols.map((symbol, i) => <span key={symbol}>{symbol} <strong>{pct(optimized[i])}</strong></span>)}</div><button className="link" onClick={applyOptimized}>Apply as weights →</button></div>}
      </div>
      <p className="note">Optimization and rebalancing are scenario tools, not recommendations. Results are sensitive to the window, expected returns, constraints, taxes, and trading costs.</p>
    </section>

    <section className="card exposure-card">
      <div className="section-title"><div><span className="eyebrow">Portfolio composition</span><h2>Style / sector / factor radar</h2></div><span className="pill">Shared 0-100% scale</span></div>
      <p className="chart-intro">Three small multiples keep unlike dimensions separate while making concentration easy to compare. Each plot uses the normalized portfolio weights above.</p>
      <div className="radar-grid">
        <RadarPlot title="Style" data={styleExposure} color="#FF3B00"/>
        <RadarPlot title="Sector" data={sectorExposure} color="#2E5CC8"/>
        <RadarPlot title="Factor" data={factorExposure} color="#7B3FB5"/>
      </div>
      <p className="note">Each holding has one primary tag per dimension, so these are exposure summaries rather than a multi-factor regression. When more than six categories are present, smaller categories are combined as Other.</p>
    </section>

    {error && <section className="notice error"><b>Market data unavailable.</b> {error} Check the ticker and network access, then refresh. No statistics are shown from substituted or synthetic prices.</section>}
    {!calculation && !error && <section className="notice"><b>{loading ? "Loading market history." : "Ready when you are."}</b> {loading ? "Risk, pairing, and rebalance signals will appear automatically." : "Edit the holdings, then refresh to calculate from live historical data."}</section>}

    {calculation && <>
      <section className="metric-grid">
        <Metric label="Annualized return" value={pct(calculation.portfolio.annualReturn)}/>
        <Metric label="Annualized volatility" value={pct(calculation.portfolio.volatility)}/>
        <Metric label="Sharpe ratio" value={num(calculation.portfolio.sharpe)}/>
        <Metric label="Maximum drawdown" value={pct(calculation.portfolio.maxDrawdown)}/>
        <Metric label="Historical VaR (95%)" value={pct(calculation.portfolio.var95)}/>
        <Metric label="Beta vs. benchmark" value={num(calculation.portfolio.beta!)}/>
      </section>

      <section className="card performance-card" id="performance-overlay">
        <div className="section-title"><div><span className="eyebrow">Relative path of wealth</span><h2>Multi-asset performance overlay</h2></div><span className="pill">{selectedSeries.length} series</span></div>
        <p className="chart-intro">Compare at least two normalized paths to see when paired holdings diverged and converged. Click a pairing opportunity below to load it here.</p>
        <div className="overlay-controls">{["Portfolio", ...symbols].map((name, i) => {
          const active = selectedSeries.includes(name);
          const color = name === "Portfolio" ? "#111111" : COLORS[(i - 1) % COLORS.length];
          return <label className={active ? "series-toggle active" : "series-toggle"} key={name}><input type="checkbox" checked={active} onChange={() => toggleSeries(name)} disabled={active && selectedSeries.length <= 2}/><i style={{ background: color }}/>{name}</label>;
        })}</div>
        <CumulativeChart dates={calculation.aligned.dates} series={chartSeries}/>
      </section>

      <section className="grid two pairing-grid">
        <div className="card">
          <span className="eyebrow">Pairing laboratory</span><h2>Lowest-correlation opportunities</h2>
          <p className="chart-intro">Ranked first by correlation, then by a relative-motion heuristic. Negative values are genuinely anticorrelated; low positive values are diversifiers, not anticorrelated assets.</p>
          <div className="pair-list">{calculation.pairs.slice(0, 8).map(pair => <div className="pair-row" key={`${pair.a}-${pair.b}`}>
            <div><strong>{symbols[pair.a]} / {symbols[pair.b]}</strong><span className={`correlation-tag ${pair.correlation < 0 ? "negative" : ""}`}>{classifyCorrelation(pair.correlation)}</span></div>
            <dl><div><dt>Corr.</dt><dd>{num(pair.correlation)}</dd></div><div><dt>Spread vol.</dt><dd>{pct(pair.spreadVolatility)}</dd></div><div><dt>Rebalance potential*</dt><dd>{pct(pair.rebalancePotential)}</dd></div></dl>
            <button className="link" onClick={() => comparePair(pair.a, pair.b)}>Compare paths ↑</button>
          </div>)}</div>
          <p className="note">*Heuristic = annualized volatility of the pair’s return spread × (1 − correlation) ÷ 2. It measures relative motion, not expected profit.</p>
        </div>
        <div className="card">
          <span className="eyebrow">Diversification map</span><h2>Correlation of daily returns</h2>
          <div className="matrix" style={{ gridTemplateColumns: `42px repeat(${symbols.length}, minmax(42px, 1fr))` }}>
            {["", ...symbols].map((symbol, i) => <span key={`h${i}`} className="matrix-label">{symbol}</span>)}
            {calculation.correlation.flatMap((row, i) => [
              <span key={`r${i}`} className="matrix-label">{symbols[i]}</span>,
              ...row.map((value, j) => <span key={`${i}-${j}`} className="cell" style={{ background: value < 0 ? `rgba(255,59,0,${.12 + .7 * Math.abs(value)})` : `rgba(46,92,200,${.08 + .78 * Math.abs(value)})`, color: Math.abs(value) > .6 ? "#FAFAF7" : "#111111" }} title={`${symbols[i]} / ${symbols[j]}: ${value.toFixed(2)}`}>{value.toFixed(2)}</span>),
            ])}
          </div>
          <p className="note">Orange cells are negative; blue cells are positive. Correlation can change abruptly and does not capture tail dependence.</p>
        </div>
      </section>

      <section className="card bucket-card">
        <div className="section-title"><div><span className="eyebrow">Drift control</span><h2>Suggested rebalance buckets</h2></div><span className="pill">{calculation.buckets.filter(bucket => bucket.triggered).length} triggered</span></div>
        <p className="chart-intro">Holdings are greedily grouped by the lowest available correlation. Each trigger preserves that bucket’s drifted capital and restores its target mix; it does not force the whole portfolio back to target.</p>
        <div className="bucket-list">{calculation.buckets.map(bucket => <article className={bucket.triggered ? "bucket triggered" : "bucket"} key={bucket.id}>
          <div className="bucket-header"><div><span className="bucket-number">Bucket {String(bucket.id).padStart(2, "0")}</span><h3>{bucket.indices.map(index => symbols[index]).join(" / ")}</h3></div><span className={bucket.triggered ? "pill warn" : "pill ok"}>{bucket.triggered ? "Rebalance" : "Inside band"}</span></div>
          <div className="bucket-metrics"><div><span>Avg. correlation</span><strong>{num(bucket.averageCorrelation)}</strong></div><div><span>Max mix drift</span><strong>{pp(bucket.drift)}</strong></div><div><span>Band</span><strong>{rebalanceBand.toFixed(1)} pp</strong></div><div><span>Potential*</span><strong>{pct(bucket.rebalancePotential)}</strong></div></div>
          <div className="bucket-allocations">{bucket.indices.map((index, i) => <div key={symbols[index]}><b>{symbols[index]}</b><span>Target mix {pct(bucket.targetMix[i])}</span><span>Drifted mix {pct(bucket.driftedMix[i])}</span><span className={Math.abs(bucket.deltas[i]) > .0005 ? "trade" : ""}>{bucket.deltas[i] > .0005 ? `Add ${pp(bucket.deltas[i])}` : bucket.deltas[i] < -.0005 ? `Trim ${pp(-bucket.deltas[i])}` : "No trade"}</span></div>)}</div>
          <button className="link" onClick={() => setSelectedSeries(bucket.indices.map(index => symbols[index]))}>Overlay this bucket ↑</button>
        </article>)}</div>
        <p className="note">Drifted weights assume the portfolio began at its target weights {calculation.driftWindow} aligned trading days ago and then received no trades or cash flows. A triggered instruction is expressed in total-portfolio percentage points; translate it into dollars using the portfolio value at trade time.</p>
      </section>

      <section className="card">
        <span className="eyebrow">Holding-level risk</span><h2>Comparable statistics</h2>
        <div className="table-wrap"><table><thead><tr><th>Holding</th><th>Weight</th><th>Drifted</th><th>Ann. return</th><th>Volatility</th><th>Sharpe</th><th>Max drawdown</th><th>VaR 95%</th><th>Beta</th></tr></thead><tbody>{symbols.map((symbol, i) => {
          const stats = calculation.holdings[i];
          return <tr key={symbol}><td><i style={{ background: COLORS[i % COLORS.length] }}/> {symbol}</td><td>{pct(calculation.targetWeights[i])}</td><td>{pct(calculation.driftedWeights[i])}</td><td>{pct(stats.annualReturn)}</td><td>{pct(stats.volatility)}</td><td>{num(stats.sharpe)}</td><td>{pct(stats.maxDrawdown)}</td><td>{pct(stats.var95)}</td><td>{num(stats.beta!)}</td></tr>;
        })}</tbody></table></div>
      </section>

      <section className="method">
        <div><span className="eyebrow">Methodology</span><h2>Pairing and rebalance logic</h2><p>Returns are aligned daily log changes in adjusted close. Pair rankings use Pearson correlation and annualized spread volatility. Buckets are formed greedily from the lowest-correlation unassigned pair; an odd holding joins the bucket with which it has the lowest average correlation. Drift is the largest absolute gap between the price-implied and target mix inside each bucket.</p></div>
        <div><span className="eyebrow">Important limitations</span><h2>Volatility harvesting is not guaranteed</h2><p>Rebalancing can add, reduce, or have no effect on return. Any benefit depends on recurring relative movement, mean reversion, thresholds, costs, taxes, and future correlations. Trending assets can make repeated rebalancing harmful. Free public prices may also be delayed, corrected, or incomplete.</p></div>
      </section>
    </>}

    <footer>Educational analytics only — not investment, tax, or legal advice. Verify prices, assumptions, and rebalance instructions before trading.</footer>
  </main>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric card"><span>{label}</span><strong>{value}</strong><small>Selected window</small></div>;
}

function RadarPlot({ title, data, color }: { title: string; data: RadarDatum[]; color: string }) {
  const size = 320;
  const center = size / 2;
  const radius = 88;
  const count = Math.max(data.length, 3);
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const point = (index: number, scale: number) => {
    const angle = -Math.PI / 2 + index * Math.PI * 2 / count;
    return [center + Math.cos(angle) * radius * scale, center + Math.sin(angle) * radius * scale] as const;
  };
  const polygon = (scale: number) => Array.from({ length: count }, (_, index) => point(index, scale).join(",")).join(" ");
  const exposurePolygon = data.map((item, index) => point(index, Math.min(1, item.value)).join(",")).join(" ");
  const dominant = data.reduce((best, item) => item.value > best.value ? item : best, data[0] || { label: "None", value: 0 });

  return <article className="radar-panel">
    <div className="radar-heading"><h3>{title}</h3><span>{dominant.label} {pct(dominant.value)}</span></div>
    <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-labelledby={`${slug}-radar-title ${slug}-radar-description`}>
      <title id={`${slug}-radar-title`}>{title} exposure radar</title>
      <desc id={`${slug}-radar-description`}>{data.map(item => `${item.label} ${pct(item.value)}`).join(", ")}. All axes run from zero to one hundred percent.</desc>
      {[.25, .5, .75, 1].map(scale => <g key={scale}><polygon className="radar-ring" points={polygon(scale)}/><text className="radar-scale" x={center + 4} y={center - radius * scale + 11}>{Math.round(scale * 100)}%</text></g>)}
      {data.map((item, index) => {
        const [axisX, axisY] = point(index, 1);
        const [labelX, labelY] = point(index, 1.34);
        const anchor = Math.abs(labelX - center) < 8 ? "middle" : labelX > center ? "start" : "end";
        return <g key={item.label}>
          <line className="radar-axis" x1={center} y1={center} x2={axisX} y2={axisY}/>
          <text className="radar-label" x={labelX} y={labelY} textAnchor={anchor} dominantBaseline="middle"><tspan x={labelX}>{item.label}</tspan><tspan className="radar-value" x={labelX} dy="12">{pct(item.value, 0)}</tspan></text>
        </g>;
      })}
      <polygon className="radar-shape" points={exposurePolygon} style={{ fill: color, stroke: color }}/>
      {data.map((item, index) => {
        const [x, y] = point(index, Math.min(1, item.value));
        return <circle key={item.label} cx={x} cy={y} r="3.5" style={{ fill: color }}/>;
      })}
    </svg>
  </article>;
}

function CumulativeChart({ dates, series }: { dates: string[]; series: ChartSeries[] }) {
  const paths = series.map(item => {
    let wealth = 1;
    return item.returns.map(value => (wealth *= Math.exp(value)));
  });
  const observedMin = Math.min(...paths.flat());
  const observedMax = Math.max(...paths.flat());
  const padding = Math.max((observedMax - observedMin) * .08, .02);
  const domainMin = Math.max(0, observedMin - padding);
  const domainMax = observedMax + padding;
  const left = 62, right = 900, top = 20, bottom = 270;
  const x = (index: number, length: number) => left + (index / Math.max(length - 1, 1)) * (right - left);
  const y = (value: number) => bottom - ((value - domainMin) / (domainMax - domainMin || 1)) * (bottom - top);
  const points = (path: number[]) => path.map((value, i) => `${x(i, path.length)},${y(value)}`).join(" ");
  const yTicks = [domainMax, (domainMax + domainMin) / 2, domainMin];
  const dateIndexes = [0, Math.floor((dates.length - 1) / 2), dates.length - 1];
  return <div className="chart">
    <svg viewBox="0 0 1000 320" preserveAspectRatio="xMidYMid meet" role="img" aria-labelledby="performance-title performance-description">
      <title id="performance-title">Normalized performance comparison</title>
      <desc id="performance-description">Growth of one dollar for {series.map(item => item.name).join(", ")} from {dates[0]} through {dates.at(-1)}.</desc>
      {yTicks.map(value => <g key={value}><line x1={left} y1={y(value)} x2={right} y2={y(value)}/><text x={left - 10} y={y(value) + 4} textAnchor="end">${value.toFixed(2)}</text></g>)}
      {dateIndexes.map(index => <text key={index} x={x(index, dates.length)} y={bottom + 25} textAnchor={index === 0 ? "start" : index === dates.length - 1 ? "end" : "middle"}>{dates[index]}</text>)}
      <line className="axis" x1={left} y1={top} x2={left} y2={bottom}/><line className="axis" x1={left} y1={bottom} x2={right} y2={bottom}/>
      {paths.map((path, i) => <polyline key={series[i].name} points={points(path)} style={{ stroke: series[i].color }}/>)}
    </svg>
    <div className="legend">{series.map((item, i) => <span key={item.name}><i style={{ background: item.color }}/>{item.name} <b>${paths[i].at(-1)?.toFixed(2)}</b></span>)}</div>
    <small>{dates[0]} — {dates.at(-1)} · each series normalized to $1.00 · adjusted close</small>
  </div>;
}

export default App;
