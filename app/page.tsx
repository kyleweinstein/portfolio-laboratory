"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type ChangeEvent } from "react";
import DiversificationMap from "./diversification-map";
import { parsePortfolioCsv } from "./portfolio-csv";
import WebullDashboard from "./webull-dashboard";
import type { WebullSource } from "./webull-client";
import {
  ALL_PROXY_SYMBOLS,
  FACTOR_OPTIONS,
  SECTOR_OPTIONS,
  STYLE_OPTIONS,
  dayToLabel,
  packSeries,
  type AnalysisResult,
  type CategoryView,
  type HoldingInput,
  type PairInsight,
  type RawSeries,
} from "./analytics";
import type { AnalyticsWorkerRequest, AnalyticsWorkerResponse } from "./analytics.worker";

type Holding = HoldingInput;
type ChartSeries = { name: string; returns: Float64Array; color: string };
type RadarDatum = { label: string; value: number };
type Progress = { phase: "idle" | "fetching" | "analyzing" | "optimizing" | "ready" | "error"; message: string; current?: number; total?: number };
type WorkerPending = { resolve: (message: AnalyticsWorkerResponse) => void; reject: (error: Error) => void };
type WorkerCommand = AnalyticsWorkerRequest extends infer Request
  ? Request extends AnalyticsWorkerRequest ? Omit<Request, "requestId"> : never
  : never;

const DEFAULT_HOLDINGS: Holding[] = [
  { symbol: "SPY", weight: 35 }, { symbol: "QQQ", weight: 25 }, { symbol: "IEF", weight: 20 },
  { symbol: "GLD", weight: 10 }, { symbol: "VNQ", weight: 10 },
];
const COLORS = ["#FF3B00", "#2E5CC8", "#1B6B45", "#7B3FB5", "#9A6A00", "#111111"];
const TABLE_PAGE_SIZE = 25;
const CLIENT_CACHE_TTL_MS = 60 * 60 * 1000;
const marketCache = new Map<string, { series: RawSeries; fetchedAt: number }>();
const PORTFOLIO_SOURCE_EVENT = "portfolio-source-change";

function portfolioSourceSnapshot(): WebullSource {
  const requested = new URLSearchParams(window.location.search).get("source");
  return requested === "webull" ? "webull" : "manual";
}

function subscribePortfolioSource(listener: () => void) {
  window.addEventListener("popstate", listener);
  window.addEventListener(PORTFOLIO_SOURCE_EVENT, listener);
  return () => {
    window.removeEventListener("popstate", listener);
    window.removeEventListener(PORTFOLIO_SOURCE_EVENT, listener);
  };
}

function cachedSeries(years: number, symbol: string) {
  const cached = marketCache.get(`${years}:${symbol}`);
  return cached && Date.now() - cached.fetchedAt < CLIENT_CACHE_TTL_MS ? cached.series : null;
}

const pct = (value: number, digits = 1) => Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : "—";
const pp = (value: number, digits = 1) => Number.isFinite(value) ? `${(value * 100).toFixed(digits)} pp` : "—";
const num = (value: number, digits = 2) => Number.isFinite(value) ? value.toFixed(digits) : "—";

function radarDataFromView(view: CategoryView, options: readonly string[]): RadarDatum[] {
  const ranked = view.rows.map(row => ({ label: row.name, value: row.exposure })).sort((a, b) => b.value - a.value);
  const visible = ranked.length > 6
    ? [...ranked.slice(0, 5), { label: "Other", value: ranked.slice(5).reduce((sum, item) => sum + item.value, 0) }]
    : ranked;
  for (const option of options) {
    if (visible.length >= 3) break;
    if (!visible.some(item => item.label === option)) visible.push({ label: option, value: 0 });
  }
  return visible;
}

function classifyCorrelation(correlation: number) {
  if (correlation < 0) return "Anticorrelated";
  if (correlation < .25) return "Low correlation";
  if (correlation < .55) return "Moderate overlap";
  return "High overlap";
}

function classificationConfidence(primary: number, runnerUp: number) {
  const gap = primary - runnerUp;
  if (primary >= .8 && gap >= .08) return "High";
  if (primary >= .6 && gap >= .03) return "Moderate";
  return "Low";
}

function snapshotKey(holdings: Holding[], benchmark: string, years: number, riskFreeRate: number, rebalanceBand: number, driftDays: number) {
  return JSON.stringify({
    holdings: holdings.filter(holding => holding.symbol.trim()).map(holding => ({ symbol: holding.symbol.trim().toUpperCase(), weight: Number(holding.weight) || 0 })),
    benchmark: benchmark.trim().toUpperCase(), years, riskFreeRate, rebalanceBand, driftDays,
  });
}

function App() {
  const [holdings, setHoldings] = useState<Holding[]>(DEFAULT_HOLDINGS);
  const portfolioSource = useSyncExternalStore(
    subscribePortfolioSource,
    portfolioSourceSnapshot,
    () => "manual",
  );
  const [benchmark, setBenchmark] = useState("SPY");
  const [years, setYears] = useState(3);
  const [riskFreeRate, setRiskFreeRate] = useState(.04);
  const [rebalanceBand, setRebalanceBand] = useState(2.5);
  const [driftDays, setDriftDays] = useState(63);
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [analyzedBand, setAnalyzedBand] = useState(rebalanceBand);
  const [analyzedRiskFree, setAnalyzedRiskFree] = useState(riskFreeRate);
  const [progress, setProgress] = useState<Progress>({ phase: "idle", message: "Ready when you are" });
  const [error, setError] = useState("");
  const [optimized, setOptimized] = useState<number[] | null>(null);
  const [pendingOptimization, setPendingOptimization] = useState<"minvol" | "maxsharpe" | null>(null);
  const [selectedSeries, setSelectedSeries] = useState<string[]>(["Portfolio", "SPY", "QQQ"]);
  const [seriesSearch, setSeriesSearch] = useState("");
  const [classificationSearch, setClassificationSearch] = useState("");
  const [classificationPage, setClassificationPage] = useState(0);
  const [riskSearch, setRiskSearch] = useState("");
  const [riskPage, setRiskPage] = useState(0);
  const [bucketVisible, setBucketVisible] = useState(12);
  const [expandedBucket, setExpandedBucket] = useState<number | null>(null);
  const [focusedPair, setFocusedPair] = useState<PairInsight | null>(null);
  const [importNotice, setImportNotice] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const csvInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const workerRef = useRef<Worker | null>(null);
  const workerYearsRef = useRef<number | null>(null);
  const loadedSymbolsRef = useRef(new Set<string>());
  const pendingWorkerRef = useRef(new Map<number, WorkerPending>());
  const requestIdRef = useRef(0);
  const revisionRef = useRef(0);

  function changePortfolioSource(nextSource: WebullSource) {
    const url = new URL(window.location.href);
    if (nextSource === "webull") url.searchParams.set("source", "webull");
    else url.searchParams.delete("source");
    window.history.replaceState(
      window.history.state,
      "",
      `${url.pathname}${url.search}${url.hash}`,
    );
    window.dispatchEvent(new Event(PORTFOLIO_SOURCE_EVENT));
  }

  const activeHoldings = useMemo(() => holdings
    .map(holding => ({ ...holding, symbol: holding.symbol.trim().toUpperCase() }))
    .filter(holding => Boolean(holding.symbol)), [holdings]);
  const draftKey = useMemo(() => snapshotKey(holdings, benchmark, years, riskFreeRate, rebalanceBand, driftDays), [holdings, benchmark, years, riskFreeRate, rebalanceBand, driftDays]);
  const dirty = Boolean(analysis && analysis.snapshotKey !== draftKey);
  const busy = ["fetching", "analyzing", "optimizing"].includes(progress.phase);
  const allocationTotal = activeHoldings.reduce((sum, holding) => sum + (Number(holding.weight) || 0), 0);
  const resultSymbols = analysis?.symbols || [];
  const selectableSeries = ["Portfolio", ...resultSymbols];
  const validSelected = selectedSeries.filter(name => selectableSeries.includes(name));
  const effectiveSelectedSeries = (validSelected.length >= 2 ? validSelected : selectableSeries.slice(0, Math.min(3, selectableSeries.length))).slice(0, 6);

  function stopWorker() {
    workerRef.current?.terminate();
    workerRef.current = null;
    loadedSymbolsRef.current.clear();
    for (const pending of pendingWorkerRef.current.values()) pending.reject(new Error("The previous analysis was superseded."));
    pendingWorkerRef.current.clear();
  }

  function ensureWorker() {
    if (workerRef.current) return workerRef.current;
    const worker = new Worker(new URL("./analytics.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (event: MessageEvent<AnalyticsWorkerResponse>) => {
      const message = event.data;
      if (message.type === "PROGRESS") {
        setProgress({ phase: message.phase, message: message.message });
        return;
      }
      const pending = pendingWorkerRef.current.get(message.requestId);
      if (!pending) return;
      pendingWorkerRef.current.delete(message.requestId);
      if (message.type === "ERROR") pending.reject(new Error(message.message));
      else pending.resolve(message);
    };
    worker.onerror = () => {
      for (const pending of pendingWorkerRef.current.values()) pending.reject(new Error("The analytical worker stopped unexpectedly."));
      pendingWorkerRef.current.clear();
    };
    workerRef.current = worker;
    return worker;
  }

  function sendWorker(message: WorkerCommand, transfer: Transferable[] = []) {
    const requestId = ++requestIdRef.current;
    const request = { ...message, requestId } as AnalyticsWorkerRequest;
    return new Promise<AnalyticsWorkerResponse>((resolve, reject) => {
      pendingWorkerRef.current.set(requestId, { resolve, reject });
      ensureWorker().postMessage(request, transfer);
    });
  }

  useEffect(() => () => {
    abortRef.current?.abort();
    stopWorker();
  }, []);

  async function fetchBatch(symbols: string[], historyYears: number, signal: AbortSignal, revision: number) {
    const errors: { symbol: string; message: string }[] = [];
    for (let start = 0; start < symbols.length; start += 150) {
      const chunk = symbols.slice(start, start + 150);
      setProgress({ phase: "fetching", message: "Fetching market history", current: Math.min(start, symbols.length), total: symbols.length });
      const response = await fetch("/api/market/batch", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ symbols: chunk, years: historyYears }), signal,
      });
      const payload = await response.json().catch(() => ({})) as {
        error?: string;
        dataBySymbol?: Record<string, RawSeries>;
        errors?: { symbol: string; message: string }[];
      };
      if (!response.ok) throw new Error(payload.error || `Market data request failed (${response.status}).`);
      for (const [symbol, series] of Object.entries(payload.dataBySymbol || {})) {
        marketCache.set(`${historyYears}:${symbol}`, { series, fetchedAt: Date.now() });
        loadedSymbolsRef.current.delete(symbol);
      }
      errors.push(...(payload.errors || []));
      if (revision !== revisionRef.current) throw new Error("The analysis was superseded.");
    }
    setProgress({ phase: "fetching", message: "Market history ready", current: symbols.length, total: symbols.length });
    return errors;
  }

  async function analyze() {
    const revision = ++revisionRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setError("");
    setOptimized(null);
    try {
      if (!activeHoldings.length) throw new Error("Add at least one holding before analyzing.");
      if (new Set(activeHoldings.map(holding => holding.symbol)).size !== activeHoldings.length) throw new Error("Each holding symbol must be unique.");
      if (!/^[A-Z0-9.^=-]{1,15}$/.test(benchmark.trim().toUpperCase())) throw new Error("Enter a valid benchmark symbol.");
      const coreSymbols = [...new Set([...activeHoldings.map(holding => holding.symbol), benchmark.trim().toUpperCase()])];
      const neededSymbols = [...new Set([...coreSymbols, ...ALL_PROXY_SYMBOLS])];
      const missing = neededSymbols.filter(symbol => !cachedSeries(years, symbol));
      setProgress({ phase: "fetching", message: "Checking market history", current: 0, total: missing.length });
      const fetchErrors = missing.length ? await fetchBatch(missing, years, controller.signal, revision) : [];
      const coreErrors = fetchErrors.filter(item => coreSymbols.includes(item.symbol));
      const unavailableCore = coreSymbols.filter(symbol => !cachedSeries(years, symbol));
      if (coreErrors.length || unavailableCore.length) {
        const messages = [...coreErrors.map(item => item.message), ...unavailableCore.filter(symbol => !coreErrors.some(item => item.symbol === symbol)).map(symbol => `${symbol}: price history is unavailable`)];
        throw new Error(messages.join("; "));
      }

      if (workerYearsRef.current !== years) {
        stopWorker();
        workerYearsRef.current = years;
      }
      const toLoad = neededSymbols.filter(symbol => cachedSeries(years, symbol) && !loadedSymbolsRef.current.has(symbol));
      if (toLoad.length) {
        const packed = toLoad.map(symbol => packSeries(symbol, cachedSeries(years, symbol)!));
        const transfer = packed.flatMap(item => [item.days.buffer, item.prices.buffer] as Transferable[]);
        await sendWorker({ type: "LOAD_DATA", revision, series: packed }, transfer);
        toLoad.forEach(symbol => loadedSymbolsRef.current.add(symbol));
      }
      setProgress({ phase: "analyzing", message: "Computing aligned returns and correlations" });
      const response = await sendWorker({
        type: "ANALYZE",
        revision,
        input: {
          revision,
          snapshotKey: draftKey,
          holdings: activeHoldings,
          benchmark: benchmark.trim().toUpperCase(),
          riskFreeRate,
          rebalanceBand,
          driftDays,
        },
      });
      if (response.type !== "RESULT" || response.kind !== "analysis" || revision !== revisionRef.current) return;
      setAnalysis(response.result);
      setAnalyzedBand(rebalanceBand);
      setAnalyzedRiskFree(riskFreeRate);
      setSelectedSeries(["Portfolio", ...response.result.symbols.slice(0, 2)]);
      setClassificationPage(0);
      setRiskPage(0);
      setBucketVisible(12);
      setExpandedBucket(null);
      const proxyFailures = fetchErrors.filter(item => !coreSymbols.includes(item.symbol)).length;
      setProgress({ phase: "ready", message: proxyFailures ? `Ready · ${proxyFailures} classification proxies unavailable` : "Ready" });
    } catch (caught) {
      if (controller.signal.aborted) return;
      const message = caught instanceof Error ? caught.message : "Unable to complete the analysis.";
      setError(message);
      setProgress({ phase: "error", message: "Analysis failed" });
    }
  }

  async function optimize(objective: "minvol" | "maxsharpe") {
    if (!analysis || dirty || busy) return;
    setPendingOptimization(objective);
    setError("");
    setProgress({ phase: "optimizing", message: "Searching the constrained allocation" });
    try {
      const response = await sendWorker({ type: "OPTIMIZE", revision: analysis.revision, objective, riskFreeRate: analyzedRiskFree });
      if (response.type !== "RESULT" || response.kind !== "optimization") return;
      setOptimized(response.result);
      setProgress({ phase: "ready", message: "Optimization ready" });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Optimization could not be completed.");
      setProgress({ phase: "error", message: "Optimization failed" });
    } finally {
      setPendingOptimization(null);
    }
  }

  async function importPortfolio(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".csv")) { setImportNotice({ type: "error", message: "Choose a .csv file." }); return; }
    if (file.size > 2_000_000) { setImportNotice({ type: "error", message: "Choose a CSV smaller than 2 MB." }); return; }
    try {
      const imported = parsePortfolioCsv(await file.text());
      const duplicateText = imported.mergedRows ? ` ${imported.mergedRows} duplicate row${imported.mergedRows === 1 ? " was" : "s were"} merged.` : "";
      const ignoredText = imported.ignoredRows ? ` ${imported.ignoredRows} zero-value or cash row${imported.ignoredRows === 1 ? " was" : "s were"} ignored.` : "";
      setHoldings(imported.holdings);
      setOptimized(null);
      setImportNotice({
        type: "success",
        message: `Imported ${imported.holdings.length} holding${imported.holdings.length === 1 ? "" : "s"} from ${file.name}. Weights were calculated from ${imported.basis} and normalized to 100%.${duplicateText}${ignoredText} Select Analyze portfolio when the draft is ready.`,
      });
    } catch (caught) {
      setImportNotice({ type: "error", message: caught instanceof Error ? caught.message : "The portfolio CSV could not be imported." });
    }
  }

  function updateHolding(index: number, field: keyof Holding, value: string) {
    setHoldings(previous => previous.map((holding, holdingIndex) => holdingIndex === index
      ? { ...holding, [field]: field === "weight" ? Number(value) : value.toUpperCase() }
      : holding));
    setOptimized(null);
  }

  function applyOptimized() {
    if (!optimized || !analysis) return;
    const bySymbol = new Map(analysis.symbols.map((symbol, index) => [symbol, optimized[index]]));
    setHoldings(previous => previous.map(holding => {
      const weight = bySymbol.get(holding.symbol.trim().toUpperCase());
      return weight === undefined ? holding : { ...holding, weight: +(weight * 100).toFixed(2) };
    }));
  }

  function comparePair(left: number, right: number) {
    if (!analysis) return;
    setSelectedSeries([analysis.symbols[left], analysis.symbols[right]]);
    document.getElementById("direction-comparison")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function toggleSeries(name: string) {
    setSelectedSeries(previous => {
      const valid = previous.filter(value => selectableSeries.includes(value));
      if (valid.includes(name)) return valid.length > 2 ? valid.filter(value => value !== name) : valid;
      return valid.length >= 6 ? valid : [...valid, name];
    });
  }

  function useWebullHoldings(nextHoldings: Holding[]) {
    if (!nextHoldings.length) {
      setImportNotice({ type: "error", message: "Webull did not return any eligible long-only stock or ETF positions." });
      return;
    }
    setHoldings(nextHoldings);
    setOptimized(null);
    changePortfolioSource("manual");
    setImportNotice({
      type: "success",
      message: `${nextHoldings.length} eligible Webull holding${nextHoldings.length === 1 ? "" : "s"} copied into the draft. Select Analyze portfolio when you are ready.`,
    });
  }

  const chartSeries: ChartSeries[] = analysis ? effectiveSelectedSeries.flatMap(name => {
    if (name === "Portfolio") return [{ name, returns: analysis.portfolioReturns, color: "#111111" }];
    const index = analysis.symbols.indexOf(name);
    return index >= 0 ? [{ name, returns: analysis.assetReturns[index], color: COLORS[index % COLORS.length] }] : [];
  }) : [];
  const chartDates = analysis ? Array.from(analysis.alignedDays, dayToLabel) : [];
  const classificationMatches = analysis?.classifications.filter(item => item.symbol.includes(classificationSearch.trim().toUpperCase())) || [];
  const classificationPages = Math.max(1, Math.ceil(classificationMatches.length / TABLE_PAGE_SIZE));
  const visibleClassifications = classificationMatches.slice(classificationPage * TABLE_PAGE_SIZE, classificationPage * TABLE_PAGE_SIZE + TABLE_PAGE_SIZE);
  const riskIndices = analysis?.symbols.map((symbol, index) => ({ symbol, index })).filter(item => item.symbol.includes(riskSearch.trim().toUpperCase())) || [];
  const riskPages = Math.max(1, Math.ceil(riskIndices.length / TABLE_PAGE_SIZE));
  const visibleRisk = riskIndices.slice(riskPage * TABLE_PAGE_SIZE, riskPage * TABLE_PAGE_SIZE + TABLE_PAGE_SIZE);
  const sortedBuckets = analysis ? [...analysis.buckets].sort((left, right) => Number(right.triggered) - Number(left.triggered) || right.drift - left.drift) : [];
  const visibleBuckets = sortedBuckets.slice(0, bucketVisible);
  const directionOptions = selectableSeries.filter(name => name.toLowerCase().includes(seriesSearch.toLowerCase()));
  const analyzeLabel = progress.phase === "fetching"
    ? progress.total ? `Fetching ${progress.current || 0}/${progress.total}` : "Fetching market data"
    : progress.phase === "analyzing" ? "Analyzing portfolio…" : "Analyze portfolio";

  return <main>
    <header>
      <div><span className="eyebrow">THE SEER&apos;S</span><h1>PORTFOLIO LAB</h1><p>Design, stress-check, pair, and rebalance a long-only portfolio using adjusted daily closes.</p></div>
      {portfolioSource === "manual" && <div className="masthead-action"><button className="primary" onClick={analyze} disabled={busy}>{analyzeLabel}</button>{dirty && <span className="stale-badge">Changes not analyzed</span>}</div>}
    </header>

    <WebullDashboard source={portfolioSource} onSourceChange={changePortfolioSource} onAnalyzeCurrentHoldings={useWebullHoldings}/>

    {portfolioSource === "manual" && <>
    <section className="control card"><div className="controls">
      <label>History<select value={years} onChange={event => { setYears(+event.target.value); setOptimized(null); }}><option value={1}>1 year</option><option value={3}>3 years</option><option value={5}>5 years</option></select></label>
      <label>Benchmark<input value={benchmark} onChange={event => { setBenchmark(event.target.value.toUpperCase()); setOptimized(null); }} maxLength={12}/></label>
      <label>Risk-free rate<input type="number" step="0.1" value={(riskFreeRate * 100).toFixed(1)} onChange={event => { setRiskFreeRate(+event.target.value / 100); setOptimized(null); }}/><small>annual %</small></label>
      <label>Rebalance band<input type="number" min="0.1" step="0.5" value={rebalanceBand} onChange={event => { setRebalanceBand(Math.max(.1, +event.target.value)); setOptimized(null); }}/><small>within-bucket percentage points</small></label>
      <label>Drift window<select value={driftDays} onChange={event => { setDriftDays(+event.target.value); setOptimized(null); }}><option value={21}>1 month</option><option value={63}>3 months</option><option value={126}>6 months</option><option value={252}>1 year</option></select><small>trading-day approximation</small></label>
      <div className="source"><b>Source</b><span>Yahoo Finance public chart data</span><small>Adjusted daily close; no key required</small></div>
    </div></section>

    <section className="card allocation-card">
      <div className="section-title"><div><span className="eyebrow">Allocation ledger</span><h2>Portfolio weights</h2></div><div className="allocation-totals"><span className={Math.abs(allocationTotal - 100) < .01 ? "pill ok" : "pill warn"}>Allocation {allocationTotal.toFixed(1)}%</span><span className="pill">{activeHoldings.length} holdings</span></div></div>
      {activeHoldings.length > 100 && <div className="scale-warning" role="status"><b>Optimized for up to 100 holdings.</b> This portfolio will continue on a best-effort basis; analysis and first-load market data may take longer.</div>}
      <div className="holdings-scroll"><div className="holding-head"><span>Symbol</span><span>Weight</span><span/></div><div className="holdings">{holdings.map((holding, index) => <div className="holding" key={index}>
        <input aria-label={`Symbol ${index + 1}`} value={holding.symbol} onChange={event => updateHolding(index, "symbol", event.target.value)} placeholder="Ticker"/>
        <label className="weight-field"><span className="sr-only">Weight for {holding.symbol || `row ${index + 1}`}</span><input aria-label={`Weight ${holding.symbol}`} type="number" min="0" step="0.1" value={holding.weight} onChange={event => updateHolding(index, "weight", event.target.value)}/><i>%</i></label>
        <button className="icon" aria-label={`Remove ${holding.symbol}`} onClick={() => { setHoldings(previous => previous.filter((_, item) => item !== index)); setOptimized(null); }}>×</button>
      </div>)}</div></div>
      <div className="allocation-actions"><input ref={csvInputRef} className="sr-only" type="file" accept=".csv,text/csv" onChange={importPortfolio}/><button className="secondary" disabled={busy} onClick={() => csvInputRef.current?.click()}>Import portfolio CSV</button><button className="secondary" onClick={() => setHoldings(previous => [...previous, { symbol: "", weight: 0 }])}>+ Add holding</button></div>
      {importNotice && <p className={`import-notice ${importNotice.type}`} role={importNotice.type === "error" ? "alert" : "status"}>{importNotice.message}</p>}
      <p className="note">CSV import accepts a Symbol column plus Value or Weight. The file is parsed only in your browser; imported symbols are used for market-data requests. Style, sector, and factor are inferred automatically from each holding’s return relationship to representative ETFs.</p>
    </section>

    <section className="card optimizer-card">
      <div><span className="eyebrow">Portfolio design</span><h2>Constraint-aware optimizer</h2><p className="muted">Long-only; each holding capped at 60%. Optimization uses the latest analyzed snapshot and runs away from the interface.</p></div>
      <div className="optimizer-actions"><div className="action-row"><button className="secondary" disabled={!analysis || dirty || busy || analysis.symbols.length < 2} onClick={() => optimize("minvol")}>{pendingOptimization === "minvol" ? "Optimizing…" : "Minimum volatility"}</button><button className="secondary" disabled={!analysis || dirty || busy || analysis.symbols.length < 2} onClick={() => optimize("maxsharpe")}>{pendingOptimization === "maxsharpe" ? "Optimizing…" : "Maximum Sharpe"}</button></div>
        {dirty && analysis && <p className="note">Analyze the draft before optimizing; the current result belongs to the prior snapshot.</p>}
        {optimized && analysis && <div className="recommend"><b>Suggested target allocation</b><div>{analysis.symbols.map((symbol, index) => <span key={symbol}>{symbol} <strong>{pct(optimized[index])}</strong></span>)}</div><button className="link" onClick={applyOptimized}>Apply as draft weights →</button></div>}
      </div>
      <p className="note">Optimization and rebalancing are scenario tools, not recommendations. Results are sensitive to the window, expected returns, constraints, taxes, and trading costs.</p>
    </section>

    {error && <section className="notice error" role="alert"><b>{progress.phase === "error" ? progress.message : "Analysis unavailable"}.</b> {error} The last successful analysis remains visible when available.</section>}
    {!analysis && !error && <section className="notice"><b>{progress.message}.</b> Edit the draft holdings, then select Analyze portfolio. Expensive analytics run only on that explicit snapshot.</section>}
    {analysis && dirty && <section className="notice stale"><b>Changes not analyzed.</b> The results below are preserved from the previous successful snapshot.</section>}
    {analysis && analysis.observationCount / analysis.symbols.length < 5 && <section className="notice statistical"><b>Limited observations per holding.</b> This analysis has {analysis.observationCount} aligned daily observations for {analysis.symbols.length} holdings ({(analysis.observationCount / analysis.symbols.length).toFixed(1)}×). Correlation estimates and optimized weights may be unstable.</section>}

    {analysis && <>
      <section className="metric-grid"><Metric label="Annualized return" value={pct(analysis.portfolio.annualReturn)}/><Metric label="Annualized volatility" value={pct(analysis.portfolio.volatility)}/><Metric label="Sharpe ratio" value={num(analysis.portfolio.sharpe)}/><Metric label="Maximum drawdown" value={pct(analysis.portfolio.maxDrawdown)}/><Metric label="Historical VaR (95%)" value={pct(analysis.portfolio.var95)}/><Metric label="Beta vs. benchmark" value={num(analysis.portfolio.beta!)}/></section>

      <section className="card composition-card">
        <div className="section-title"><div><span className="eyebrow">Composition & counterweights</span><h2>Style / sector / factor radar</h2></div><span className="pill">Automatic classification</span></div>
        <p className="chart-intro">Each holding is assigned to its closest style, sector/sleeve, and primary-factor proxy using realized daily-return correlation. The radar plots share a 0–100% scale.</p>
        <div className="table-tools"><label>Find holding<input value={classificationSearch} onChange={event => { setClassificationSearch(event.target.value.toUpperCase()); setClassificationPage(0); }} placeholder="Ticker"/></label><span>{classificationMatches.length} results</span></div>
        <div className="classification-table" role="table" aria-label="Automatically inferred holding classifications"><div className="classification-row classification-header" role="row"><span role="columnheader">Holding</span><span role="columnheader">Inferred style</span><span role="columnheader">Inferred sector / sleeve</span><span role="columnheader">Inferred factor</span><span role="columnheader">Confidence</span></div>
          {visibleClassifications.map(item => {
            const confidences = [classificationConfidence(item.style.correlation, item.style.runnerUpCorrelation), classificationConfidence(item.sector.correlation, item.sector.runnerUpCorrelation), classificationConfidence(item.factor.correlation, item.factor.runnerUpCorrelation)];
            const confidence = confidences.includes("Low") ? "Low" : confidences.includes("Moderate") ? "Moderate" : "High";
            return <div className="classification-row" role="row" key={item.symbol}><strong role="cell">{item.symbol}</strong><span role="cell"><b>{item.style.name}</b><small>ρ {num(item.style.correlation)}</small></span><span role="cell"><b>{item.sector.name}</b><small>ρ {num(item.sector.correlation)}</small></span><span role="cell"><b>{item.factor.name}</b><small>ρ {num(item.factor.correlation)}</small></span><span role="cell"><em className={`confidence ${confidence === "Low" ? "low" : ""}`}>{confidence}</em><small>lowest of three</small></span></div>;
          })}
        </div>
        <Pagination page={classificationPage} pages={classificationPages} onPage={setClassificationPage}/>
        <div className="radar-grid"><RadarPlot title="Style" data={radarDataFromView(analysis.styleView, STYLE_OPTIONS)} color="#FF3B00"/><RadarPlot title="Sector" data={radarDataFromView(analysis.sectorView, SECTOR_OPTIONS)} color="#2E5CC8"/><RadarPlot title="Factor" data={radarDataFromView(analysis.factorView, FACTOR_OPTIONS)} color="#7B3FB5"/></div>
        <div className="balance-summary"><div><span>Style additions</span><strong>{analysis.styleView.additions.length ? analysis.styleView.additions.map(item => item.name).join(" · ") : "No clear addition"}</strong></div><div><span>Sector / sleeve additions</span><strong>{analysis.sectorView.additions.length ? analysis.sectorView.additions.map(item => item.name).join(" · ") : "No clear addition"}</strong></div><div><span>Factor additions</span><strong>{analysis.factorView.additions.length ? analysis.factorView.additions.map(item => item.name).join(" · ") : "No clear addition"}</strong></div></div>
        <p className="note">Classifications are best-fit historical inferences, not issuer classifications or factor-regression estimates. Addition scores combine low allocation exposure with low correlation to the portfolio.</p>
      </section>

      <section className="card performance-card" id="direction-comparison">
        <div className="section-title"><div><span className="eyebrow">Directional co-movement</span><h2>Up / down direction comparison</h2></div><span className="pill">{effectiveSelectedSeries.length} of 6 lanes</span></div>
        <p className="chart-intro">Each selected asset gets an equal-height lane. Return magnitude is deliberately removed so aligned and opposing moves remain visible.</p>
        <div className="series-picker"><label>Find series<input value={seriesSearch} onChange={event => setSeriesSearch(event.target.value.toUpperCase())} placeholder="Portfolio or ticker"/></label><span>Select two to six lanes</span></div>
        <div className="overlay-controls">{directionOptions.slice(0, 30).map(name => { const active = effectiveSelectedSeries.includes(name); const resultIndex = analysis.symbols.indexOf(name); const color = name === "Portfolio" ? "#111111" : COLORS[resultIndex % COLORS.length]; return <label className={active ? "series-toggle active" : "series-toggle"} key={name}><input type="checkbox" checked={active} onChange={() => toggleSeries(name)} disabled={(active && effectiveSelectedSeries.length <= 2) || (!active && effectiveSelectedSeries.length >= 6)}/><i style={{ background: color }}/>{name}</label>; })}</div>
        {directionOptions.length > 30 && <p className="note">Showing the first 30 matches. Refine the search to find another holding.</p>}
        <DirectionChart dates={chartDates} series={chartSeries}/>
      </section>

      <section className="diversification-layout">
        <DiversificationMap key={focusedPair ? `${focusedPair.a}:${focusedPair.b}` : "map"} symbols={analysis.symbols} correlation={analysis.correlationPacked} clusterOrder={analysis.clusterOrder} pairs={analysis.pairs} onCompare={comparePair} focusedPair={focusedPair}/>
        <aside className="card pair-opportunities"><span className="eyebrow">Pairing laboratory</span><h2>Lowest-correlation opportunities</h2><p className="chart-intro">Ranked by correlation, then relative-motion potential.</p><div className="pair-list">{analysis.pairs.slice(0, 8).map(pair => <div className="pair-row" key={`${pair.a}-${pair.b}`}><div><strong>{analysis.symbols[pair.a]} / {analysis.symbols[pair.b]}</strong><span className={`correlation-tag ${pair.correlation < 0 ? "negative" : ""}`}>{classifyCorrelation(pair.correlation)}</span></div><dl><div><dt>Corr.</dt><dd>{num(pair.correlation)}</dd></div><div><dt>Spread vol.</dt><dd>{pct(pair.spreadVolatility)}</dd></div><div><dt>Potential*</dt><dd>{pct(pair.rebalancePotential)}</dd></div></dl><div className="pair-actions"><button className="link" onClick={() => setFocusedPair(pair)}>Inspect map</button><button className="link" onClick={() => comparePair(pair.a, pair.b)}>Compare ↑</button></div></div>)}</div><p className="note">*Annualized spread volatility × (1 − correlation) ÷ 2. It measures relative motion, not expected profit.</p></aside>
      </section>

      <section className="card bucket-card"><div className="section-title"><div><span className="eyebrow">Drift control</span><h2>Suggested rebalance buckets</h2></div><span className="pill">{analysis.buckets.filter(bucket => bucket.triggered).length} triggered</span></div><p className="chart-intro">Triggered buckets appear first. Expand one bucket at a time to inspect its price-implied drift and potential trades.</p>
        <div className="bucket-list">{visibleBuckets.map(bucket => { const expanded = expandedBucket === bucket.id; return <article className={bucket.triggered ? "bucket triggered" : "bucket"} key={bucket.id}><button className="bucket-summary" aria-expanded={expanded} onClick={() => setExpandedBucket(expanded ? null : bucket.id)}><div><span className="bucket-number">Bucket {String(bucket.id).padStart(2, "0")}</span><h3>{bucket.indices.map(index => analysis.symbols[index]).join(" / ")}</h3></div><span className={bucket.triggered ? "pill warn" : "pill ok"}>{bucket.triggered ? "Rebalance" : "Inside band"}</span></button>{expanded && <div className="bucket-detail"><div className="bucket-metrics"><div><span>Avg. correlation</span><strong>{num(bucket.averageCorrelation)}</strong></div><div><span>Max mix drift</span><strong>{pp(bucket.drift)}</strong></div><div><span>Band</span><strong>{analyzedBand.toFixed(1)} pp</strong></div><div><span>Potential*</span><strong>{pct(bucket.rebalancePotential)}</strong></div></div><div className="bucket-allocations">{bucket.indices.map((index, item) => <div key={analysis.symbols[index]}><b>{analysis.symbols[index]}</b><span>Target {pct(bucket.targetMix[item])}</span><span>Drifted {pct(bucket.driftedMix[item])}</span><span className={Math.abs(bucket.deltas[item]) > .0005 ? "trade" : ""}>{bucket.deltas[item] > .0005 ? `Add ${pp(bucket.deltas[item])}` : bucket.deltas[item] < -.0005 ? `Trim ${pp(-bucket.deltas[item])}` : "No trade"}</span></div>)}</div><button className="link" onClick={() => setSelectedSeries(bucket.indices.slice(0, 6).map(index => analysis.symbols[index]))}>Compare directions ↑</button></div>}</article>; })}</div>
        {bucketVisible < sortedBuckets.length && <button className="secondary show-more" onClick={() => setBucketVisible(value => value + 12)}>Show 12 more buckets</button>}
        <p className="note">Drifted weights assume the portfolio began at target {analysis.driftWindow} aligned trading days ago and then received no trades or cash flows.</p>
      </section>

      <section className="card"><div className="section-title"><div><span className="eyebrow">Holding-level risk</span><h2>Comparable statistics</h2></div><span className="pill">25 rows per page</span></div><div className="table-tools"><label>Find holding<input value={riskSearch} onChange={event => { setRiskSearch(event.target.value.toUpperCase()); setRiskPage(0); }} placeholder="Ticker"/></label><span>{riskIndices.length} results</span></div><div className="table-wrap"><table><thead><tr><th>Holding</th><th>Weight</th><th>Drifted</th><th>Ann. return</th><th>Volatility</th><th>Sharpe</th><th>Max drawdown</th><th>VaR 95%</th><th>Beta</th></tr></thead><tbody>{visibleRisk.map(({ symbol, index }) => { const stats = analysis.holdings[index]; return <tr key={symbol}><td><i style={{ background: COLORS[index % COLORS.length] }}/> {symbol}</td><td>{pct(analysis.targetWeights[index])}</td><td>{pct(analysis.driftedWeights[index])}</td><td>{pct(stats.annualReturn)}</td><td>{pct(stats.volatility)}</td><td>{num(stats.sharpe)}</td><td>{pct(stats.maxDrawdown)}</td><td>{pct(stats.var95)}</td><td>{num(stats.beta!)}</td></tr>; })}</tbody></table></div><Pagination page={riskPage} pages={riskPages} onPage={setRiskPage}/></section>

      <section className="method"><div><span className="eyebrow">Methodology</span><h2>Pairing and rebalance logic</h2><p>Returns are aligned daily log changes in adjusted close. Pair rankings use Pearson correlation and annualized spread volatility. The packed correlation matrix is computed once from standardized return vectors and reused by every dependent view.</p></div><div><span className="eyebrow">Important limitations</span><h2>Volatility harvesting is not guaranteed</h2><p>Rebalancing can add, reduce, or have no effect on return. Any benefit depends on recurring relative movement, mean reversion, thresholds, costs, taxes, and future correlations.</p></div></section>
    </>}
    </>}
    <footer>Educational analytics only — not investment, tax, or legal advice. Verify prices, assumptions, and rebalance instructions before trading.</footer>
  </main>;
}

function Pagination({ page, pages, onPage }: { page: number; pages: number; onPage: (page: number) => void }) {
  if (pages <= 1) return null;
  return <div className="pagination"><button className="secondary compact" disabled={page === 0} onClick={() => onPage(page - 1)}>Previous</button><span>Page {page + 1} of {pages}</span><button className="secondary compact" disabled={page >= pages - 1} onClick={() => onPage(page + 1)}>Next</button></div>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric card"><span>{label}</span><strong>{value}</strong><small>Analyzed snapshot</small></div>;
}

function RadarPlot({ title, data, color }: { title: string; data: RadarDatum[]; color: string }) {
  const size = 320, center = size / 2, radius = 88, count = Math.max(data.length, 3);
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const point = (index: number, scale: number) => { const angle = -Math.PI / 2 + index * Math.PI * 2 / count; return [center + Math.cos(angle) * radius * scale, center + Math.sin(angle) * radius * scale] as const; };
  const polygon = (scale: number) => Array.from({ length: count }, (_, index) => point(index, scale).join(",")).join(" ");
  const exposurePolygon = data.map((item, index) => point(index, Math.min(1, item.value)).join(",")).join(" ");
  const dominant = data.reduce((best, item) => item.value > best.value ? item : best, data[0] || { label: "None", value: 0 });
  return <article className="radar-panel"><div className="radar-heading"><h3>{title}</h3><span>{dominant.label} {pct(dominant.value)}</span></div><svg viewBox={`0 0 ${size} ${size}`} role="img" aria-labelledby={`${slug}-radar-title ${slug}-radar-description`}><title id={`${slug}-radar-title`}>{title} exposure radar</title><desc id={`${slug}-radar-description`}>{data.map(item => `${item.label} ${pct(item.value)}`).join(", ")}. All axes run from zero to one hundred percent.</desc>{[.25, .5, .75, 1].map(scale => <g key={scale}><polygon className="radar-ring" points={polygon(scale)}/><text className="radar-scale" x={center + 4} y={center - radius * scale + 11}>{Math.round(scale * 100)}%</text></g>)}{data.map((item, index) => { const [axisX, axisY] = point(index, 1); const [labelX, labelY] = point(index, 1.34); const anchor = Math.abs(labelX - center) < 8 ? "middle" : labelX > center ? "start" : "end"; return <g key={item.label}><line className="radar-axis" x1={center} y1={center} x2={axisX} y2={axisY}/><text className="radar-label" x={labelX} y={labelY} textAnchor={anchor} dominantBaseline="middle"><tspan x={labelX}>{item.label}</tspan><tspan className="radar-value" x={labelX} dy="12">{pct(item.value, 0)}</tspan></text></g>; })}<polygon className="radar-shape" points={exposurePolygon} style={{ fill: color, stroke: color }}/>{data.map((item, index) => { const [x, y] = point(index, Math.min(1, item.value)); return <circle key={item.label} cx={x} cy={y} r="3.5" style={{ fill: color }}/>; })}</svg></article>;
}

function DirectionChart({ dates, series }: { dates: string[]; series: ChartSeries[] }) {
  const left = 112, right = 910, top = 16, rowHeight = 60, axisY = top + series.length * rowHeight, height = axisY + 42;
  const x = (index: number) => left + (index / Math.max(dates.length - 1, 1)) * (right - left);
  const strokeWidth = Math.max(.8, Math.min(2.2, (right - left) / Math.max(dates.length, 1) * .8));
  const directionPath = (returns: Float64Array, direction: "up" | "down", baseline: number) => Array.from(returns, (value, index) => { if ((direction === "up" && value <= 0) || (direction === "down" && value >= 0)) return ""; return `M${x(index).toFixed(2)},${baseline}V${baseline + (direction === "up" ? -18 : 18)}`; }).join(" ");
  const dateIndexes = [0, Math.floor((dates.length - 1) / 2), dates.length - 1];
  return <div className="direction-chart"><div className="direction-scroll"><svg viewBox={`0 0 1000 ${height}`} preserveAspectRatio="xMidYMid meet" role="img" aria-labelledby="direction-title direction-description"><title id="direction-title">Daily up and down direction comparison</title><desc id="direction-description">Equal-height lanes for {series.map(item => item.name).join(", ")} from {dates[0]} through {dates.at(-1)}.</desc>{dateIndexes.map(index => <line className="date-guide" key={`guide-${index}`} x1={x(index)} y1={top} x2={x(index)} y2={axisY}/>)}{series.map((item, index) => { const baseline = top + index * rowHeight + rowHeight / 2; const positiveDays = item.returns.filter(value => value > 0).length; const upShare = positiveDays / Math.max(item.returns.length, 1); return <g key={item.name}><line className="lane-baseline" x1={left} y1={baseline} x2={right} y2={baseline}/><rect x="18" y={baseline - 5} width="10" height="10" style={{ fill: item.color }}/><text className="lane-label" x="36" y={baseline + 4}>{item.name}</text><text className="lane-share" x="978" y={baseline + 4} textAnchor="end">↑ {pct(upShare, 0)}</text><path className="direction-up" d={directionPath(item.returns, "up", baseline)} style={{ strokeWidth }}/><path className="direction-down" d={directionPath(item.returns, "down", baseline)} style={{ strokeWidth }}/></g>; })}{dateIndexes.map(index => <text key={`date-${index}`} x={x(index)} y={axisY + 25} textAnchor={index === 0 ? "start" : index === dates.length - 1 ? "end" : "middle"}>{dates[index]}</text>)}</svg></div><div className="direction-key"><span><i className="up"/>↑ Up day</span><span><i className="down"/>↓ Down day</span><span>Right label = share of up days</span></div><small>{dates[0]} — {dates.at(-1)} · daily adjusted-close direction · fixed ±1 encoding · magnitude intentionally omitted</small></div>;
}

export default App;
