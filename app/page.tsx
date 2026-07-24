"use client";

import { useMemo, useState } from "react";

type Holding = { symbol: string; weight: number };
type Series = { dates: string[]; prices: number[] };
type Stat = { annualReturn: number; volatility: number; sharpe: number; maxDrawdown: number; var95: number; cvar95: number; beta?: number };

const DEFAULT_HOLDINGS: Holding[] = [
  { symbol: "SPY", weight: 35 }, { symbol: "QQQ", weight: 25 }, { symbol: "IEF", weight: 20 },
  { symbol: "GLD", weight: 10 }, { symbol: "VNQ", weight: 10 },
];
const COLORS = ["#FF3B00", "#2E5CC8", "#1B6B45", "#7B3FB5", "#9A6A00", "#111111"];

const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
const sampleStd = (xs: number[]) => Math.sqrt(xs.reduce((a, x) => a + (x - mean(xs)) ** 2, 0) / Math.max(1, xs.length - 1));
const covariance = (a: number[], b: number[]) => a.reduce((s, x, i) => s + (x - mean(a)) * (b[i] - mean(b)), 0) / Math.max(1, a.length - 1);
const pct = (n: number, digits = 1) => Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : "—";
const num = (n: number, digits = 2) => Number.isFinite(n) ? n.toFixed(digits) : "—";

function fetchSeries(symbol: string, years: number): Promise<Series> {
  const url = `/api/market?symbol=${encodeURIComponent(symbol)}&years=${years}`;
  return fetch(url).then(async (res) => {
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json?.error || `${symbol}: data request failed (${res.status})`);
    const pairs = (json?.dates || []).map((date: string, i: number) => [date, json?.prices?.[i]] as const)
      .filter((p: readonly [string, number]) => Number.isFinite(p[1]) && p[1] > 0);
    if (pairs.length < 60) throw new Error(`${symbol}: insufficient price history`);
    return { dates: pairs.map(p => p[0]), prices: pairs.map(p => p[1]) };
  });
}

function alignedReturns(series: Record<string, Series>, symbols: string[]) {
  const maps = symbols.map(s => new Map(series[s].dates.map((d, i) => [d, series[s].prices[i]])));
  const dates = [...maps[0].keys()].filter(d => maps.every(m => m.has(d))).sort();
  const returns = symbols.map((_, j) => dates.slice(1).map((d, i) => Math.log((maps[j].get(d) as number) / (maps[j].get(dates[i]) as number))));
  return { dates: dates.slice(1), returns };
}

function calculateStats(r: number[], rf: number, benchmark?: number[]): Stat {
  const annualReturn = Math.exp(mean(r) * 252) - 1;
  const volatility = sampleStd(r) * Math.sqrt(252);
  const sorted = [...r].sort((a, b) => a - b); const cut = Math.max(0, Math.floor(sorted.length * .05) - 1);
  let wealth = 1, peak = 1, maxDrawdown = 0;
  r.forEach(x => { wealth *= Math.exp(x); peak = Math.max(peak, wealth); maxDrawdown = Math.min(maxDrawdown, wealth / peak - 1); });
  return { annualReturn, volatility, sharpe: volatility ? (annualReturn - rf) / volatility : NaN, maxDrawdown, var95: -sorted[cut], cvar95: -mean(sorted.slice(0, cut + 1)), beta: benchmark ? covariance(r, benchmark) / (sampleStd(benchmark) ** 2) : undefined };
}

function projectCapped(weights: number[], cap: number) {
  let projected = weights.map(x => Math.max(0, x));
  for (let pass = 0; pass < 12; pass++) {
    const total = projected.reduce((a, b) => a + b, 0) || 1;
    projected = projected.map(x => x / total);
    const excess = projected.reduce((s, x) => s + Math.max(0, x - cap), 0);
    projected = projected.map(x => Math.min(cap, x));
    if (excess < 1e-10) break;
    const room = projected.reduce((s, x) => s + Math.max(0, cap - x), 0);
    projected = projected.map(x => x < cap ? x + excess * (cap - x) / Math.max(room, 1e-12) : x);
  }
  return projected;
}

function App() {
  const [holdings, setHoldings] = useState<Holding[]>(DEFAULT_HOLDINGS);
  const [benchmark, setBenchmark] = useState("SPY"); const [years, setYears] = useState(3); const [rf, setRf] = useState(0.04);
  const [data, setData] = useState<Record<string, Series>>({}); const [error, setError] = useState(""); const [loading, setLoading] = useState(false); const [optimized, setOptimized] = useState<number[] | null>(null);
  const symbols = holdings.map(h => h.symbol.trim().toUpperCase()).filter(Boolean);
  const weightTotal = holdings.reduce((s, h) => s + (Number(h.weight) || 0), 0);
  const calculation = useMemo(() => {
    if (!symbols.every(s => data[s]) || !data[benchmark]) return null;
    const allSymbols = [...symbols, benchmark]; const aligned = alignedReturns(data, allSymbols);
    if (aligned.dates.length < 60) return null;
    const assetReturns = aligned.returns.slice(0, symbols.length), benchmarkReturns = aligned.returns.at(-1)!;
    const w = holdings.map(h => Math.max(0, Number(h.weight) || 0) / Math.max(weightTotal, 1));
    const portfolioReturns = aligned.dates.map((_, i) => assetReturns.reduce((s, r, j) => s + r[i] * w[j], 0));
    return { aligned, assetReturns, benchmarkReturns, portfolioReturns, weights: w, portfolio: calculateStats(portfolioReturns, rf, benchmarkReturns), holdings: assetReturns.map(r => calculateStats(r, rf, benchmarkReturns)), correlation: assetReturns.map(a => assetReturns.map(b => covariance(a, b) / (sampleStd(a) * sampleStd(b)))) };
  }, [data, symbols.join("|"), holdings, benchmark, rf, weightTotal]);

  async function refresh() {
    setLoading(true); setError(""); setOptimized(null);
    try { const unique = [...new Set([...symbols, benchmark.toUpperCase()])]; const results = await Promise.all(unique.map(async s => [s, await fetchSeries(s, years)] as const)); setData(Object.fromEntries(results)); }
    catch (e) { setData({}); setError(e instanceof Error ? e.message : "Unable to retrieve price history."); }
    finally { setLoading(false); }
  }
  function updateHolding(i: number, field: keyof Holding, value: string) { setHoldings(h => h.map((x, j) => j === i ? { ...x, [field]: field === "weight" ? Number(value) : value.toUpperCase() } : x)); }
  function optimize(objective: "minvol" | "maxsharpe") {
    if (!calculation) return; const n = symbols.length; const cap = .6; let w = calculation.weights.map(x => Math.max(.001, x));
    const score = (v: number[]) => { const r = calculation.aligned.dates.map((_, i) => calculation.assetReturns.reduce((s, a, j) => s + a[i] * v[j], 0)); const st = calculateStats(r, rf); return objective === "minvol" ? -st.volatility : st.sharpe; };
    for (let step = 0; step < 250; step++) {
      const eps = .0001;
      const grad = w.map((_, j) => {
        const hi = projectCapped(w.map((x, k) => x + (k === j ? eps : 0)), cap);
        const lo = projectCapped(w.map((x, k) => x - (k === j ? eps : 0)), cap);
        return (score(hi) - score(lo)) / (2 * eps);
      });
      const rate = objective === "minvol" ? .02 : .006;
      w = projectCapped(w.map((x, j) => x + rate * grad[j]), cap);
    }
    setOptimized(w);
  }
  function applyOptimized() { if (optimized) setHoldings(h => h.map((x, i) => ({ ...x, weight: +(optimized[i] * 100).toFixed(2) }))); }
  return <main>
    <header><div><span className="eyebrow">Portfolio laboratory</span><h1>Clear allocation decisions, grounded in return history.</h1><p>Design, stress-check, and rebalance a long-only portfolio using adjusted daily closes.</p></div><button className="primary" onClick={refresh} disabled={loading}>{loading ? "Loading market data…" : "Refresh analysis"}</button></header>
    <section className="control card"><div className="controls"><label>History<select value={years} onChange={e => setYears(+e.target.value)}><option value={1}>1 year</option><option value={3}>3 years</option><option value={5}>5 years</option></select></label><label>Benchmark<input value={benchmark} onChange={e => setBenchmark(e.target.value.toUpperCase())} maxLength={12}/></label><label>Risk-free rate<input type="number" step="0.1" value={(rf*100).toFixed(1)} onChange={e => setRf(+e.target.value/100)}/><small>annual %</small></label><div className="source"><b>Source</b><span>Yahoo Finance public chart data</span><small>Adjusted daily close; no key required</small></div></div></section>
    <section className="grid two"><div className="card"><div className="section-title"><div><span className="eyebrow">Current allocation</span><h2>Holdings & weights</h2></div><span className={Math.abs(weightTotal-100)<.01 ? "pill ok" : "pill warn"}>{weightTotal.toFixed(1)}% total</span></div><div className="holdings">{holdings.map((h,i)=><div className="holding" key={i}><input aria-label={`Symbol ${i+1}`} value={h.symbol} onChange={e=>updateHolding(i,"symbol",e.target.value)} placeholder="Ticker"/><input aria-label={`Weight ${h.symbol}`} type="number" min="0" step="0.1" value={h.weight} onChange={e=>updateHolding(i,"weight",e.target.value)}/><span>%</span><button className="icon" aria-label={`Remove ${h.symbol}`} onClick={()=>setHoldings(x=>x.filter((_,j)=>j!==i))}>×</button></div>)}</div><button className="secondary" onClick={()=>setHoldings(h=>[...h,{symbol:"",weight:0}])}>+ Add holding</button></div>
    <div className="card"><span className="eyebrow">Portfolio design</span><h2>Constraint-aware optimizer</h2><p className="muted">Long-only; each holding capped at 60%. Uses the same historical return window as the dashboard.</p><div className="action-row"><button className="secondary" disabled={!calculation} onClick={()=>optimize("minvol")}>Minimum volatility</button><button className="secondary" disabled={!calculation} onClick={()=>optimize("maxsharpe")}>Maximum Sharpe</button></div>{optimized && <div className="recommend"><b>Suggested allocation</b><div>{symbols.map((s,i)=><span key={s}>{s} <strong>{pct(optimized[i])}</strong></span>)}</div><button className="link" onClick={applyOptimized}>Apply to holdings →</button></div>}<p className="note">Optimization is sensitive to the chosen window and expected-return estimates; use it as a scenario tool, not a recommendation.</p></div></section>
    {error && <section className="notice error"><b>Market data unavailable.</b> {error} Check the ticker and network access, then refresh. No statistics are shown from substituted or synthetic prices.</section>}
    {!calculation && !error && <section className="notice"><b>Ready when you are.</b> Set your holdings and click “Refresh analysis” to calculate risk statistics from live historical data.</section>}
    {calculation && <><section className="metric-grid"><Metric label="Annualized return" value={pct(calculation.portfolio.annualReturn)}/><Metric label="Annualized volatility" value={pct(calculation.portfolio.volatility)}/><Metric label="Sharpe ratio" value={num(calculation.portfolio.sharpe)}/><Metric label="Maximum drawdown" value={pct(calculation.portfolio.maxDrawdown)}/><Metric label="Historical VaR (95%)" value={pct(calculation.portfolio.var95)}/><Metric label="Beta vs. benchmark" value={num(calculation.portfolio.beta!)} /></section>
    <section className="grid two"><div className="card"><span className="eyebrow">Path of wealth</span><h2>Growth of $1</h2><CumulativeChart dates={calculation.aligned.dates} returns={[calculation.portfolioReturns, ...calculation.assetReturns]} names={["Portfolio",...symbols]}/></div><div className="card"><span className="eyebrow">Diversification</span><h2>Correlation of daily returns</h2><div className="matrix" style={{gridTemplateColumns:`42px repeat(${symbols.length}, minmax(42px, 1fr))`}}>{["",...symbols].map((s,i)=><span key={`h${i}`} className="matrix-label">{s}</span>)}{calculation.correlation.flatMap((row,i)=>[<span key={`r${i}`} className="matrix-label">{symbols[i]}</span>, ...row.map((v,j)=><span key={`${i}-${j}`} className="cell" style={{background:`rgba(46,92,200,${.08+.78*Math.abs(v)})`,color:Math.abs(v)>.6?"#FAFAF7":"#111111"}} title={`${symbols[i]} / ${symbols[j]}: ${v.toFixed(2)}`}>{v.toFixed(2)}</span>)])}</div><p className="note">Darker cells indicate stronger relationships, positive or negative. Correlation does not measure causation or tail dependence.</p></div></section>
    <section className="card"><span className="eyebrow">Holding-level risk</span><h2>Comparable statistics</h2><div className="table-wrap"><table><thead><tr><th>Holding</th><th>Weight</th><th>Ann. return</th><th>Volatility</th><th>Sharpe</th><th>Max drawdown</th><th>VaR 95%</th><th>Beta</th></tr></thead><tbody>{symbols.map((s,i)=>{const st=calculation.holdings[i];return <tr key={s}><td><i style={{background:COLORS[i%COLORS.length]}}/> {s}</td><td>{pct(calculation.weights[i])}</td><td>{pct(st.annualReturn)}</td><td>{pct(st.volatility)}</td><td>{num(st.sharpe)}</td><td>{pct(st.maxDrawdown)}</td><td>{pct(st.var95)}</td><td>{num(st.beta!)}</td></tr>})}</tbody></table></div></section>
    <section className="method"><div><span className="eyebrow">Methodology</span><h2>What the dashboard calculates</h2><p>Returns are log changes in adjusted close, aligned only on dates available for every selected holding and benchmark. Annualized return is geometric from the average daily log return; volatility uses sample daily standard deviation × √252. VaR and CVaR use the historical 5% left tail of daily returns.</p></div><div><span className="eyebrow">Important limitations</span><h2>Use with appropriate caution</h2><p>Free public data can be delayed, corrected, incomplete, or differ from execution prices. Adjusted close may not model taxes, fees, slippage, corporate-action timing, intraday risk, or securities with limited history. Historical results do not predict future returns.</p></div></section></>}
    <footer>Educational analytics only — not investment, tax, or legal advice. Verify prices and methodology with a qualified provider before trading.</footer>
  </main>;
}

function Metric({label,value}:{label:string;value:string}) { return <div className="metric card"><span>{label}</span><strong>{value}</strong><small>Current window</small></div>; }
function CumulativeChart({dates,returns,names}:{dates:string[];returns:number[][];names:string[]}) { const paths=returns.map(r=>{let w=1;return r.map(x=>(w*=Math.exp(x)));}); const min=Math.min(...paths.flat()), max=Math.max(...paths.flat()); const pts=(a:number[])=>a.map((v,i)=>`${(i/(a.length-1))*100},${94-((v-min)/(max-min||1))*82}`).join(" "); return <div className="chart"><svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Cumulative return chart"><line x1="0" y1="94" x2="100" y2="94"/><line x1="0" y1="53" x2="100" y2="53"/><line x1="0" y1="12" x2="100" y2="12"/>{paths.map((p,i)=><polyline key={i} points={pts(p)} style={{stroke:COLORS[i%COLORS.length]}}/>)}</svg><div className="legend">{names.map((n,i)=><span key={n}><i style={{background:COLORS[i%COLORS.length]}}/>{n}</span>)}</div><small>{dates[0]} — {dates.at(-1)} · normalized to $1.00</small></div>; }
export default App;
