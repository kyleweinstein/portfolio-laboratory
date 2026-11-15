"use client";
import { useEffect, useRef, useState } from "react";
import type { SimulationResult, SimulationSettings } from "./monte-carlo";

const percent = (value: number) => `${value.toFixed(1)}%`;
export default function MonteCarloPanel({ returns, dirty }: { returns: Float64Array; dirty: boolean }) {
  const [settings, setSettings] = useState<SimulationSettings>({ years: 3, paths: 2000, blockDays: 21, seed: 42, targetPercent: 20, drawdownPercent: 20 });
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState("");
  const worker = useRef<Worker | null>(null);
  useEffect(() => () => worker.current?.terminate(), []);
  const cancel = () => { worker.current?.terminate(); worker.current = null; setProgress(null); };
  const update = (field: keyof SimulationSettings, value: number) => { cancel(); setSettings(previous => ({ ...previous, [field]: value })); };
  const run = () => {
    cancel(); setError(""); setProgress(0);
    try {
      const active = new Worker(new URL("./monte-carlo.worker.ts", import.meta.url), { type: "module" });
      worker.current = active;
      active.onmessage = event => {
        if (worker.current !== active) return;
        if (event.data.type === "progress") setProgress(event.data.percent);
        else { if (event.data.type === "result") setResult(event.data.result); else setError(event.data.message); cancel(); }
      };
      active.onerror = () => { if (worker.current === active) { setError("Simulation failed. Try again with fewer paths."); cancel(); } };
      const copy = returns.slice();
      active.postMessage({ returns: copy, settings }, [copy.buffer]);
    } catch { setError("The simulation worker could not start."); cancel(); }
  };
  const changed = result && JSON.stringify(settings) !== JSON.stringify(result.settings);
  const last = result?.points.at(-1);
  const low = Math.min(0, ...(result?.points.map(p => p.p05) ?? [0]));
  const high = Math.max(1, ...(result?.points.map(p => p.p95) ?? [1]));
  const x = (month: number) => 66 + month / (result ? result.settings.years * 12 : 12) * 690;
  const y = (value: number) => 280 - (value - low) / (high - low) * 250;
  const band = (lower: "p05" | "p25", upper: "p95" | "p75") => result ? [...result.points.map(p => `${x(p.month)},${y(p[upper])}`), ...result.points.toReversed().map(p => `${x(p.month)},${y(p[lower])}`)].join(" ") : "";
  return <section className="panel mc-panel" aria-labelledby="mc-title">
    <span className="eyebrow">Modeled scenarios</span><h2 id="mc-title">Monte Carlo simulator</h2>
    <p>Explore possible percentage-return paths by resampling stretches of this analyzed portfolio’s historical daily returns. These are scenarios, not forecasts or actual account performance.</p>
    <div className="mc-controls">
      <label>Horizon<select value={settings.years} onChange={e => update("years", Number(e.target.value))}>{[1, 3, 5, 10].map(n => <option key={n} value={n}>{n} years</option>)}</select></label>
      <label>Paths<select value={settings.paths} onChange={e => update("paths", Number(e.target.value))}>{[1000, 2000, 5000].map(n => <option key={n}>{n}</option>)}</select></label>
      <label>Sampling block<select value={settings.blockDays} onChange={e => update("blockDays", Number(e.target.value))}>{[5, 21, 63].map(n => <option key={n} value={n}>{n} trading days</option>)}</select></label>
      <label>Ending return target %<input type="number" min="-99" max="1000" value={settings.targetPercent} onChange={e => update("targetPercent", Number(e.target.value))}/></label>
      <label>Drawdown threshold %<input type="number" min="1" max="100" value={settings.drawdownPercent} onChange={e => update("drawdownPercent", Number(e.target.value))}/></label>
      <label>Random seed<input type="number" min="0" max="4294967295" step="1" value={settings.seed} onChange={e => update("seed", Number(e.target.value))}/></label>
    </div>
    <button className="primary" disabled={dirty || progress !== null} onClick={run}>Run simulation</button>{progress !== null && <button className="secondary" onClick={cancel}>Cancel simulation</button>}
    <p role="status" aria-live="polite">{progress !== null ? `Simulating ${progress}%` : dirty ? "Analyze your draft before running new scenarios." : changed ? "Settings changed; displayed results are from the previous run." : result ? "Simulation complete." : "Ready to simulate."}</p>
    {error && <p role="alert">{error}</p>}
    {result && last && <>
      <p className="note">{result.settings.paths.toLocaleString()} paths · {result.settings.years}-year horizon · {result.observations} historical daily observations · seed {result.settings.seed}</p>
      {result.settings.years * 252 > result.observations && <p className="note">The projected horizon exceeds the historical sample length. Paths reuse sampled history; they do not add new evidence.</p>}
      <div className="mc-metrics">
        <div><span>Median ending return</span><strong>{percent(last.p50)}</strong></div>
        <div><span>Ending below start</span><strong>{percent(result.lossProbability * 100)}</strong></div>
        <div><span>Ending return ≥ {percent(result.settings.targetPercent)}</span><strong>{percent(result.targetProbability * 100)}</strong></div>
        <div><span>Drawdown ≥ {percent(result.settings.drawdownPercent)} during horizon</span><strong>{percent(result.drawdownProbability * 100)}</strong></div>
      </div>
      <svg className="mc-chart" viewBox="0 0 800 330" role="img" aria-label="Simulated cumulative percentage returns. Outer band: 5th–95th percentile. Inner band: 25th–75th. Line: median. Exact values are in the expandable table below.">
        {Array.from({ length: 5 }, (_, i) => low + (high - low) * i / 4).map((v, i) => <g key={i}><line x1="66" x2="756" y1={y(v)} y2={y(v)} stroke="currentColor" opacity=".15"/><text x="60" y={y(v) + 4} textAnchor="end" fontSize="12">{percent(v)}</text></g>)}
        <polygon points={band("p05", "p95")} fill="#245ac1" opacity=".15"/><polygon points={band("p25", "p75")} fill="#245ac1" opacity=".25"/>
        <line x1="66" x2="756" y1={y(0)} y2={y(0)} stroke="currentColor" strokeDasharray="4 4"/>
        <polyline points={result.points.map(p => `${x(p.month)},${y(p.p50)}`).join(" ")} fill="none" stroke="#245ac1" strokeWidth="3"/>
        <text x="66" y="310" fontSize="13">Start</text><text x="756" y="310" textAnchor="end" fontSize="13">{result.settings.years} years</text>
      </svg>
      <p className="note">Outer band: 5th–95th percentile · Inner band: 25th–75th · Line: median. Bands are pointwise percentiles, not confidence guarantees. Median maximum drawdown: {percent(result.medianDrawdown)}.</p>
      <details><summary>Monthly scenario percentiles</summary><div className="mc-table" tabIndex={0} role="region" aria-label="Monthly scenario percentiles"><table><thead><tr>{["Month", "5th", "25th", "Median", "75th", "95th"].map(v => <th key={v}>{v}</th>)}</tr></thead><tbody>{result.points.map(p => <tr key={p.month}><th scope="row">{p.month}</th>{[p.p05, p.p25, p.p50, p.p75, p.p95].map((v, i) => <td key={i}>{percent(v)}</td>)}</tr>)}</tbody></table></div></details>
    </>}
    <details><summary>Methodology and useful comparisons</summary><p>Compare allocations using the same history, seed, and settings. Explore loss probability, target-return feasibility, and drawdowns over different horizons. Change block length to test sensitivity to persistent good or bad markets.</p><p>Samples contiguous blocks of the analyzed constant-weight portfolio’s daily log returns, preserving cross-asset relationships within that portfolio. Uses 252 trading days per year and 21 per month. Assumes history remains representative; it cannot create shocks absent from the sample. No new contributions, withdrawals, taxes, trading costs, inflation, or margin financing are modeled. Optimizer cash suggestions are not included unless reflected in the analyzed returns. Repeating a seed reproduces the sample.</p></details>
  </section>;
}
