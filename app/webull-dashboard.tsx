"use client";

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { HoldingInput } from "./analytics";
import {
  WebullApiError,
  backfillWebull,
  buildEligibleWebullHoldings,
  connectWebull,
  finiteNumber,
  getWebullStatus,
  isWebullHoldingEligible,
  selectWebullAccount,
  syncWebull,
  webullLoginUrl,
  type WebullAccount,
  type WebullActivity,
  type WebullChartPoint,
  type WebullDashboardData,
  type WebullHolding,
  type WebullMetric,
  type WebullProvenance,
  type WebullSource,
  type WebullStatus,
  type WebullVerification,
} from "./webull-client";

type LoadingAction = "connect" | "sync" | "backfill" | "select" | null;
type LoadFailure = { kind: "disabled" | "unauthorized" | "error"; message: string };
type ChartMode = "growth" | "value";

export type WebullDashboardProps = {
  source?: WebullSource;
  defaultSource?: WebullSource;
  onSourceChange?: (source: WebullSource) => void;
  onAnalyzeCurrentHoldings: (holdings: HoldingInput[]) => void;
  manualContent?: ReactNode;
  className?: string;
};

const DATE_FORMAT = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });
const DATE_TIME_FORMAT = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function displayDate(value: string | null | undefined, includeTime = false): string {
  const parsed = parseDate(value);
  if (!parsed) return "Not available";
  return includeTime ? DATE_TIME_FORMAT.format(parsed) : DATE_FORMAT.format(parsed);
}

const VERIFICATION_STAGE_LABELS: Record<WebullVerification["stage"], string> = {
  starting: "Starting protected verification",
  verifying_access: "Verifying Webull access",
  discovering_accounts: "Loading available accounts",
  syncing_account: "Syncing the selected account",
  finalizing: "Finalizing connection status",
  complete: "Verification complete",
};

const VERIFICATION_STATE_LABELS: Record<WebullVerification["state"], string> = {
  running: "Verification running",
  succeeded: "Verification succeeded",
  failed: "Verification failed",
  timed_out: "Verification timed out",
};

function VerificationStatusCard({ verification, compact = false }: { verification: WebullVerification; compact?: boolean }) {
  return (
    <div className={`webull-verification-card${compact ? " webull-verification-card-compact" : ""}`} data-state={verification.state} role={verification.state === "running" ? "status" : undefined} aria-live={verification.state === "running" ? "polite" : undefined}>
      <div className="webull-verification-heading">
        <span className="webull-eyebrow">{VERIFICATION_STATE_LABELS[verification.state]}</span>
        <strong>{VERIFICATION_STAGE_LABELS[verification.stage]}</strong>
      </div>
      <dl className="webull-verification-times">
        <div><dt>Started</dt><dd><time dateTime={verification.startedAt}>{displayDate(verification.startedAt, true)}</time></dd></div>
        <div><dt>Last update</dt><dd><time dateTime={verification.updatedAt}>{displayDate(verification.updatedAt, true)}</time></dd></div>
        {verification.completedAt ? <div><dt>Completed</dt><dd><time dateTime={verification.completedAt}>{displayDate(verification.completedAt, true)}</time></dd></div> : null}
      </dl>
      {verification.error ? (
        <div className="webull-verification-error" role="alert">
          <strong>{verification.error.message}</strong>
          <small>Error code: {verification.error.code}</small>
        </div>
      ) : null}
    </div>
  );
}

function displayNumber(value: unknown, maximumFractionDigits = 2): string {
  const number = finiteNumber(value as number | string | null | undefined);
  return number === null ? "—" : new Intl.NumberFormat(undefined, { maximumFractionDigits }).format(number);
}

function displayCurrency(value: unknown, currency = "USD", compact = false): string {
  const number = finiteNumber(value as number | string | null | undefined);
  if (number === null) return "—";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      notation: compact ? "compact" : "standard",
      maximumFractionDigits: compact ? 1 : 2,
    }).format(number);
  } catch {
    return `${currency} ${displayNumber(number)}`;
  }
}

function displayPercent(value: unknown, digits = 2): string {
  const number = finiteNumber(value as number | string | null | undefined);
  return number === null ? "—" : `${(number * 100).toFixed(digits)}%`;
}

function displayMetric(metric: WebullMetric | null | undefined, fallbackUnit: "currency" | "percent", currency: string): string {
  if (!metric) return "—";
  const unit = metric.unit || fallbackUnit;
  if (unit === "percent") return displayPercent(metric.value);
  if (unit === "currency") return displayCurrency(metric.value, metric.currency || currency);
  return displayNumber(metric.value);
}

function qualityLabel(value: string | null | undefined): string {
  if (!value) return "Unavailable";
  return value.charAt(0).toUpperCase() + value.slice(1).toLowerCase();
}

function ProvenanceLabel({ provenance, fallbackThrough }: { provenance?: WebullProvenance | null; fallbackThrough?: string | null }) {
  const parts = [
    provenance?.source || "Webull",
    provenance?.quality ? qualityLabel(provenance.quality) : null,
    provenance?.dataThrough || provenance?.asOf || fallbackThrough
      ? `Through ${displayDate(provenance?.dataThrough || provenance?.asOf || fallbackThrough)}`
      : null,
  ].filter(Boolean);
  return (
    <small className="webull-provenance" title={provenance?.methodology || provenance?.note || undefined}>
      {parts.join(" · ") || "Provenance unavailable"}
    </small>
  );
}

function MetricCard({ label, metric, fallbackUnit, currency, detail, fallbackThrough }: {
  label: string;
  metric?: WebullMetric | null;
  fallbackUnit: "currency" | "percent";
  currency: string;
  detail?: ReactNode;
  fallbackThrough?: string | null;
}) {
  return (
    <div className="webull-metric">
      <span className="webull-metric-label">{label}</span>
      <strong>{displayMetric(metric, fallbackUnit, currency)}</strong>
      {detail ? <div className="webull-metric-detail">{detail}</div> : null}
      <ProvenanceLabel provenance={metric} fallbackThrough={fallbackThrough} />
    </div>
  );
}

function accountLabel(account: WebullAccount): string {
  return [account.label, account.maskedIdentifier, account.accountType].filter(Boolean).join(" · ");
}

function stateFailure(error: unknown): LoadFailure {
  if (error instanceof WebullApiError) {
    if (error.status === 404 || error.code === "NOT_CONFIGURED") return { kind: "disabled", message: error.message };
    if (error.status === 401) return { kind: "unauthorized", message: error.message };
    return { kind: "error", message: error.message };
  }
  return { kind: "error", message: error instanceof Error ? error.message : "Unable to load the connected portfolio." };
}

function tabId(base: string, source: WebullSource) {
  return `${base}-${source}-tab`;
}

function panelId(base: string, source: WebullSource) {
  return `${base}-${source}-panel`;
}

type ChartGeometry = {
  portfolioPath: string;
  benchmarkPath: string;
  cashFlowXs: number[];
  yTicks: { y: number; value: number }[];
  xTicks: { x: number; label: string }[];
  pointCount: number;
  firstValue: number | null;
  lastValue: number | null;
  benchmarkName: string;
};

function makePath(points: { x: number; y: number }[]): string {
  return points.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
}

function chartGeometry(points: readonly WebullChartPoint[], mode: ChartMode, benchmarkName: string): ChartGeometry | null {
  const width = 800;
  const height = 300;
  const left = 64;
  const right = 22;
  const top = 20;
  const bottom = 42;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const ordered = points
    .map(point => ({ ...point, parsedDate: parseDate(point.date) }))
    .filter(point => point.parsedDate)
    .sort((leftPoint, rightPoint) => leftPoint.parsedDate!.getTime() - rightPoint.parsedDate!.getTime());
  if (ordered.length < 2) return null;

  const portfolioValues = ordered.map(point => finiteNumber(mode === "growth" ? point.portfolioGrowth : point.portfolioValue));
  const benchmarkValues = mode === "growth" ? ordered.map(point => finiteNumber(point.benchmarkGrowth)) : ordered.map(() => null);
  const domainValues = [...portfolioValues, ...benchmarkValues].filter((value): value is number => value !== null);
  if (domainValues.length < 2) return null;
  let min = Math.min(...domainValues);
  let max = Math.max(...domainValues);
  if (min === max) {
    const padding = Math.max(Math.abs(min) * 0.02, 1);
    min -= padding;
    max += padding;
  } else {
    const padding = (max - min) * 0.08;
    min -= padding;
    max += padding;
  }

  const x = (index: number) => left + (index / Math.max(1, ordered.length - 1)) * plotWidth;
  const y = (value: number) => top + ((max - value) / (max - min)) * plotHeight;
  const portfolioPoints = portfolioValues.flatMap((value, index) => value === null ? [] : [{ x: x(index), y: y(value) }]);
  const benchmarkPoints = benchmarkValues.flatMap((value, index) => value === null ? [] : [{ x: x(index), y: y(value) }]);
  const cashFlowXs = ordered.flatMap((point, index) => {
    const flow = finiteNumber(point.externalCashFlow);
    return flow === null || flow === 0 ? [] : [x(index)];
  });
  const yTicks = Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4;
    const value = max - ratio * (max - min);
    return { y: top + ratio * plotHeight, value };
  });
  const xIndices = [...new Set([0, Math.floor((ordered.length - 1) / 2), ordered.length - 1])];
  const xTicks = xIndices.map(index => ({ x: x(index), label: displayDate(ordered[index].date) }));
  const validPortfolio = portfolioValues.filter((value): value is number => value !== null);

  return {
    portfolioPath: makePath(portfolioPoints),
    benchmarkPath: makePath(benchmarkPoints),
    cashFlowXs,
    yTicks,
    xTicks,
    pointCount: validPortfolio.length,
    firstValue: validPortfolio[0] ?? null,
    lastValue: validPortfolio[validPortfolio.length - 1] ?? null,
    benchmarkName,
  };
}

function PerformanceChart({ dashboard }: { dashboard: WebullDashboardData }) {
  const [mode, setMode] = useState<ChartMode>("growth");
  const benchmarkName = dashboard.metrics?.benchmarkSymbol || "Benchmark";
  const growthGeometry = useMemo(() => chartGeometry(dashboard.chart || [], "growth", benchmarkName), [dashboard.chart, benchmarkName]);
  const valueGeometry = useMemo(() => chartGeometry(dashboard.chart || [], "value", benchmarkName), [dashboard.chart, benchmarkName]);
  const geometry = mode === "growth" ? growthGeometry : valueGeometry;
  const currency = dashboard.currency || "USD";

  return (
    <section className="webull-card webull-chart-card" aria-labelledby="webull-performance-heading">
      <div className="webull-card-heading">
        <div>
          <span className="webull-eyebrow">Performance record</span>
          <h3 id="webull-performance-heading">{mode === "growth" ? "Growth of $100" : "Portfolio value"}</h3>
        </div>
        <div className="webull-chart-tabs" role="group" aria-label="Chart view">
          <button type="button" className={mode === "growth" ? "webull-chart-tab webull-chart-tab-active" : "webull-chart-tab"} aria-pressed={mode === "growth"} disabled={!growthGeometry} onClick={() => setMode("growth")}>Growth of $100</button>
          <button type="button" className={mode === "value" ? "webull-chart-tab webull-chart-tab-active" : "webull-chart-tab"} aria-pressed={mode === "value"} disabled={!valueGeometry} onClick={() => setMode("value")}>Portfolio value</button>
        </div>
      </div>
      {geometry ? (
        <>
          <div className="webull-chart-legend" aria-hidden="true">
            <span><i className="webull-chart-key-portfolio" />Portfolio</span>
            {mode === "growth" && geometry.benchmarkPath ? <span><i className="webull-chart-key-benchmark" />{geometry.benchmarkName}</span> : null}
            {mode === "value" && geometry.cashFlowXs.length ? <span><i className="webull-chart-key-flow" />External cash flow</span> : null}
          </div>
          <div className="webull-chart-shell">
            <svg viewBox="0 0 800 300" role="img" aria-label={`${mode === "growth" ? "Growth of 100 dollars" : "Portfolio value"} from ${geometry.pointCount} dated observations. First value ${geometry.firstValue ?? "unavailable"}; last value ${geometry.lastValue ?? "unavailable"}.`}>
              <title>{mode === "growth" ? `Portfolio growth compared with ${geometry.benchmarkName}` : "Portfolio value with external cash-flow dates"}</title>
              {geometry.yTicks.map(tick => (
                <g key={tick.y}>
                  <line className="webull-chart-grid" x1="64" x2="778" y1={tick.y} y2={tick.y} />
                  <text className="webull-chart-axis-label" x="56" y={tick.y + 4} textAnchor="end">{mode === "growth" ? displayCurrency(tick.value, currency, true) : displayCurrency(tick.value, currency, true)}</text>
                </g>
              ))}
              {geometry.xTicks.map(tick => (
                <text className="webull-chart-axis-label" key={`${tick.x}-${tick.label}`} x={tick.x} y="286" textAnchor={tick.x < 100 ? "start" : tick.x > 730 ? "end" : "middle"}>{tick.label}</text>
              ))}
              {mode === "value" ? geometry.cashFlowXs.map((flowX, index) => <line className="webull-chart-flow" key={`${flowX}-${index}`} x1={flowX} x2={flowX} y1="20" y2="258" />) : null}
              {geometry.benchmarkPath ? <path className="webull-chart-benchmark" d={geometry.benchmarkPath} /> : null}
              <path className="webull-chart-portfolio" d={geometry.portfolioPath} />
            </svg>
          </div>
          <ProvenanceLabel provenance={dashboard} fallbackThrough={dashboard.dataThrough} />
        </>
      ) : (
        <div className="webull-empty-inline" role="status">This chart will appear after the API returns at least two reconciled observations for this view.</div>
      )}
    </section>
  );
}

function holdingWeight(holding: WebullHolding, dashboard: WebullDashboardData): number | null {
  const provided = finiteNumber(holding.weight);
  if (provided !== null) return provided;
  const value = finiteNumber(holding.marketValue);
  const nav = finiteNumber(dashboard.metrics?.netAccountValue?.value);
  return value !== null && nav !== null && nav !== 0 ? value / nav : null;
}

function HoldingsTable({ dashboard }: { dashboard: WebullDashboardData }) {
  const holdings = dashboard.holdings || [];
  return (
    <section className="webull-card" aria-labelledby="webull-holdings-heading">
      <div className="webull-card-heading">
        <div>
          <span className="webull-eyebrow">Current account</span>
          <h3 id="webull-holdings-heading">Holdings</h3>
        </div>
        <span className="webull-count">{holdings.length} position{holdings.length === 1 ? "" : "s"}</span>
      </div>
      <div className="webull-table-shell" tabIndex={0} aria-label="Scrollable holdings table">
        <table className="webull-table">
          <caption className="webull-sr-only">Current Webull positions</caption>
          <thead><tr><th scope="col">Holding</th><th scope="col">Quantity</th><th scope="col">Market value</th><th scope="col">Account weight</th><th scope="col">Unrealized P&amp;L</th><th scope="col">Analysis</th></tr></thead>
          <tbody>
            {holdings.length ? holdings.map((holding, index) => {
              const eligible = isWebullHoldingEligible(holding);
              return (
                <tr key={holding.positionId || `${holding.symbol}-${index}`}>
                  <th scope="row"><b>{holding.symbol}</b><small>{holding.name || holding.instrumentType || "Instrument details unavailable"}</small></th>
                  <td>{displayNumber(holding.quantity, 6)}</td>
                  <td>{displayCurrency(holding.marketValue, holding.currency || dashboard.currency || "USD")}</td>
                  <td>{displayPercent(holdingWeight(holding, dashboard))}</td>
                  <td>{displayCurrency(holding.unrealizedProfitLoss, holding.currency || dashboard.currency || "USD")}</td>
                  <td><span className={eligible ? "webull-eligibility webull-eligibility-included" : "webull-eligibility"}>{eligible ? "Included" : "Excluded"}</span></td>
                </tr>
              );
            }) : <tr><td colSpan={6} className="webull-table-empty">No current positions were returned for this account.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ActivitiesTable({ activities, currency }: { activities: readonly WebullActivity[]; currency: string }) {
  return (
    <section className="webull-card" aria-labelledby="webull-activity-heading">
      <div className="webull-card-heading">
        <div><span className="webull-eyebrow">Reconciled record</span><h3 id="webull-activity-heading">Recent activity</h3></div>
        <span className="webull-count">{activities.length} record{activities.length === 1 ? "" : "s"}</span>
      </div>
      <div className="webull-table-shell" tabIndex={0} aria-label="Scrollable activity table">
        <table className="webull-table webull-activity-table">
          <caption className="webull-sr-only">Recent Webull account activity</caption>
          <thead><tr><th scope="col">Date</th><th scope="col">Type</th><th scope="col">Description</th><th scope="col">Amount</th><th scope="col">Status</th><th scope="col">Provenance</th></tr></thead>
          <tbody>
            {activities.length ? activities.map(activity => (
              <tr key={activity.activityId}>
                <td>{displayDate(activity.date)}</td>
                <th scope="row">{activity.type}</th>
                <td>{[activity.symbol, activity.description].filter(Boolean).join(" · ") || "—"}</td>
                <td>{displayCurrency(activity.amount, activity.currency || currency)}</td>
                <td>{activity.status || "Posted"}</td>
                <td><ProvenanceLabel provenance={activity} /></td>
              </tr>
            )) : <tr><td colSpan={6} className="webull-table-empty">No activity records are available for this account.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ExclusionsTable({ dashboard }: { dashboard: WebullDashboardData }) {
  const exclusions = dashboard.exclusions || [];
  if (!exclusions.length) return null;
  return (
    <section className="webull-card" aria-labelledby="webull-exclusions-heading">
      <div className="webull-card-heading">
        <div><span className="webull-eyebrow">Analysis boundary</span><h3 id="webull-exclusions-heading">Excluded assets</h3></div>
        <span className="webull-count">{exclusions.length}</span>
      </div>
      <p className="webull-intro">These assets remain part of account performance when authoritative values are available, but are not sent to the long-only equity analytics engine.</p>
      <div className="webull-table-shell" tabIndex={0} aria-label="Scrollable excluded assets table">
        <table className="webull-table">
          <caption className="webull-sr-only">Assets excluded from Portfolio Lab holdings analysis</caption>
          <thead><tr><th scope="col">Asset</th><th scope="col">Type</th><th scope="col">Market value</th><th scope="col">Reason</th></tr></thead>
          <tbody>{exclusions.map((item, index) => (
            <tr key={`${item.symbol || item.name || "asset"}-${index}`}>
              <th scope="row">{item.symbol || item.name || "Unnamed asset"}</th>
              <td>{item.instrumentType || "—"}</td>
              <td>{displayCurrency(item.marketValue, item.currency || dashboard.currency || "USD")}</td>
              <td>{item.reason}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </section>
  );
}

function SourceStrip({ status, dashboard, action, onSelectAccount, onSync, onBackfill, onReviewIssues }: {
  status: WebullStatus;
  dashboard: WebullDashboardData | null;
  action: LoadingAction;
  onSelectAccount: (accountId: string) => void;
  onSync: () => void;
  onBackfill: () => void;
  onReviewIssues: () => void;
}) {
  const issues = dashboard?.issues || [];
  const selectedAccount = status.accounts.find(account => account.accountId === status.selectedAccountId);
  const quality = dashboard?.quality || (dashboard?.performanceReady ? "verified" : "partial");
  return (
    <div className="webull-source-strip">
      <div className="webull-source-identity">
        <span className="webull-eyebrow">Connected source</span>
        <strong>Webull{selectedAccount ? ` · ${accountLabel(selectedAccount)}` : ""}</strong>
        <small>Last successful sync: {displayDate(dashboard?.lastSuccessfulSyncAt, true)}</small>
      </div>
      {status.accounts.length > 1 ? (
        <label className="webull-field">
          <span>Account</span>
          <select value={status.selectedAccountId || ""} disabled={Boolean(action)} onChange={event => onSelectAccount(event.target.value)}>
            {status.accounts.map(account => <option key={account.accountId} value={account.accountId}>{accountLabel(account)}</option>)}
          </select>
        </label>
      ) : null}
      <div className="webull-source-freshness">
        <span className="webull-eyebrow">Performance through</span>
        <strong>{displayDate(dashboard?.dataThrough)}</strong>
        <span className="webull-quality" data-quality={String(quality).toLowerCase()}>{qualityLabel(quality)}</span>
      </div>
      <div className="webull-source-actions">
        <button type="button" className="webull-button webull-button-primary" disabled={Boolean(action)} onClick={onSync}>{action === "sync" ? "Syncing…" : "Sync now"}</button>
        <button type="button" className="webull-button" disabled={Boolean(action)} onClick={onBackfill}>{action === "backfill" ? "Backfilling…" : "Backfill history"}</button>
        <button type="button" className="webull-button webull-button-text" disabled={!issues.length} onClick={onReviewIssues}>Review issues{issues.length ? ` (${issues.length})` : ""}</button>
      </div>
    </div>
  );
}

function ConnectedDashboard({ status, action, actionError, announcement, onSelectAccount, onSync, onBackfill, onAnalyzeCurrentHoldings }: {
  status: WebullStatus;
  action: LoadingAction;
  actionError: string;
  announcement: string;
  onSelectAccount: (accountId: string) => void;
  onSync: () => void;
  onBackfill: () => void;
  onAnalyzeCurrentHoldings: (holdings: HoldingInput[]) => void;
}) {
  const dashboard = status.dashboard;
  const issuesRef = useRef<HTMLDetailsElement>(null);
  const eligible = useMemo(() => buildEligibleWebullHoldings(dashboard?.holdings || []), [dashboard?.holdings]);

  function reviewIssues() {
    if (!issuesRef.current) return;
    issuesRef.current.open = true;
    issuesRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    issuesRef.current.querySelector("summary")?.focus();
  }

  if (!dashboard) {
    return (
      <>
        {status.verification ? <VerificationStatusCard verification={status.verification} compact /> : null}
        <SourceStrip status={status} dashboard={null} action={action} onSelectAccount={onSelectAccount} onSync={onSync} onBackfill={onBackfill} onReviewIssues={() => undefined} />
        {actionError ? <div className="webull-notice webull-notice-error" role="alert">{actionError}</div> : null}
        <div className="webull-state" role="status">
          <span className="webull-eyebrow">Connected · No account snapshot</span>
          <h2>Your Webull connection is ready.</h2>
          <p>Sync this account to load current balances and positions. Historical performance appears only after the API returns a reconciled record.</p>
          <button type="button" className="webull-button webull-button-primary" disabled={Boolean(action)} onClick={onSync}>{action === "sync" ? "Syncing…" : "Sync account"}</button>
        </div>
      </>
    );
  }

  const metrics = dashboard.metrics || {};
  const currency = dashboard.currency || "USD";
  const period = metrics.periodLabel || "Available period";
  const coverage = finiteNumber(dashboard.analyticsCoverage);
  const issues = dashboard.issues || [];

  function analyzeCurrent() {
    if (!eligible.length) return;
    onAnalyzeCurrentHoldings(eligible);
  }

  return (
    <div className="webull-connected" aria-busy={Boolean(action)}>
      {status.verification ? <VerificationStatusCard verification={status.verification} compact /> : null}
      <SourceStrip status={status} dashboard={dashboard} action={action} onSelectAccount={onSelectAccount} onSync={onSync} onBackfill={onBackfill} onReviewIssues={reviewIssues} />
      {actionError ? <div className="webull-notice webull-notice-error" role="alert">{actionError}</div> : null}
      <p className="webull-sr-only" aria-live="polite">{announcement}</p>

      {!dashboard.performanceReady ? (
        <div className="webull-notice webull-notice-warning" role="status">
          <strong>Performance history is not fully reconciled.</strong>
          <span>Current holdings can still be reviewed. Return metrics remain unavailable or estimated until continuous account values and external cash flows are complete{dashboard.performanceReadyFrom ? ` from ${displayDate(dashboard.performanceReadyFrom)}` : ""}.</span>
        </div>
      ) : null}

      <section aria-label={`Connected account summary for ${period}`}>
        <div className="webull-metric-grid">
          <MetricCard label="Net account value" metric={metrics.netAccountValue} fallbackUnit="currency" currency={currency} fallbackThrough={dashboard.dataThrough} detail={metrics.cashBalance ? <>Cash {displayMetric(metrics.cashBalance, "currency", currency)}</> : null} />
          <MetricCard label="Day P&amp;L" metric={metrics.dayProfitLoss} fallbackUnit="currency" currency={currency} fallbackThrough={dashboard.dataThrough} />
          <MetricCard label="Unrealized P&amp;L" metric={metrics.unrealizedProfitLoss} fallbackUnit="currency" currency={currency} fallbackThrough={dashboard.dataThrough} detail={metrics.marketValue ? <>Market value {displayMetric(metrics.marketValue, "currency", currency)}</> : null} />
        </div>
        <div className="webull-metric-grid">
          <MetricCard label={`Time-weighted return · ${period}`} metric={metrics.timeWeightedReturn} fallbackUnit="percent" currency={currency} fallbackThrough={dashboard.dataThrough} />
          <MetricCard
            label={`${metrics.benchmarkSymbol || "Benchmark"} return · ${period}`}
            metric={metrics.benchmarkReturn}
            fallbackUnit="percent"
            currency={currency}
            fallbackThrough={dashboard.dataThrough}
          />
          <MetricCard label="Geometric excess return" metric={metrics.excessReturn} fallbackUnit="percent" currency={currency} fallbackThrough={dashboard.dataThrough} />
          <MetricCard label="Investment gain" metric={metrics.investmentGain} fallbackUnit="currency" currency={currency} fallbackThrough={dashboard.dataThrough} />
          <MetricCard label="Net contributions" metric={metrics.netContributions} fallbackUnit="currency" currency={currency} fallbackThrough={dashboard.dataThrough} />
          <MetricCard label="Money-weighted return" metric={metrics.moneyWeightedReturn} fallbackUnit="percent" currency={currency} fallbackThrough={dashboard.dataThrough} />
        </div>
      </section>

      <PerformanceChart dashboard={dashboard} />

      <section className="webull-analysis-card" aria-labelledby="webull-analysis-heading">
        <div>
          <span className="webull-eyebrow">Portfolio Lab bridge</span>
          <h3 id="webull-analysis-heading">Analyze current holdings</h3>
          <p>Copy the eligible long-only equity sleeve into the editable draft. This action does not run analysis automatically.</p>
          <small>{coverage === null ? "Coverage unavailable" : `${displayPercent(coverage)} of account NAV covered`} · {eligible.length} eligible holding{eligible.length === 1 ? "" : "s"}</small>
        </div>
        <button type="button" className="webull-button webull-button-primary" disabled={!eligible.length} onClick={analyzeCurrent}>Analyze current holdings</button>
      </section>

      <HoldingsTable dashboard={dashboard} />
      <div className="webull-detail-grid">
        <ActivitiesTable activities={dashboard.activities || []} currency={currency} />
        <ExclusionsTable dashboard={dashboard} />
      </div>

      <details className="webull-issues" ref={issuesRef}>
        <summary>Data quality and reconciliation issues <span>{issues.length}</span></summary>
        {issues.length ? (
          <ul>
            {issues.map(issue => (
              <li key={issue.issueId} data-severity={issue.severity}>
                <strong>{issue.title}</strong>
                {issue.message ? <p>{issue.message}</p> : null}
                <small>{[issue.affectedMetric, issue.date ? displayDate(issue.date) : null].filter(Boolean).join(" · ")}</small>
              </li>
            ))}
          </ul>
        ) : <p>No unresolved issues were returned by the API.</p>}
      </details>
    </div>
  );
}

export default function WebullDashboard({
  source,
  defaultSource = "manual",
  onSourceChange,
  onAnalyzeCurrentHoldings,
  manualContent,
  className = "",
}: WebullDashboardProps) {
  const baseId = useId().replace(/:/g, "");
  const [internalSource, setInternalSource] = useState<WebullSource>(defaultSource);
  const [status, setStatus] = useState<WebullStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<LoadFailure | null>(null);
  const [action, setAction] = useState<LoadingAction>(null);
  const [actionError, setActionError] = useState("");
  const [announcement, setAnnouncement] = useState("");
  const requestRevision = useRef(0);
  const selectedSource = source ?? internalSource;

  const loadStatus = useCallback(async (signal?: AbortSignal, quiet = false) => {
    const revision = ++requestRevision.current;
    if (!quiet) setLoading(true);
    setFailure(null);
    try {
      const nextStatus = await getWebullStatus({ signal });
      if (revision !== requestRevision.current) return null;
      setStatus(nextStatus);
      setFailure(nextStatus.enabled ? nextStatus.authenticated ? null : { kind: "unauthorized", message: "Your Portfolio Lab session is not authorized to view this Webull connection." } : { kind: "disabled", message: "Webull is not configured for this deployment." });
      return nextStatus;
    } catch (error) {
      if (signal?.aborted || revision !== requestRevision.current) return null;
      setFailure(stateFailure(error));
      return null;
    } finally {
      if (revision === requestRevision.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const revision = ++requestRevision.current;
    getWebullStatus({ signal: controller.signal })
      .then((nextStatus) => {
        if (revision !== requestRevision.current) return;
        setStatus(nextStatus);
        setFailure(nextStatus.enabled ? nextStatus.authenticated ? null : { kind: "unauthorized", message: "Your Portfolio Lab session is not authorized to view this Webull connection." } : { kind: "disabled", message: "Webull is not configured for this deployment." });
      })
      .catch((error) => {
        if (controller.signal.aborted || revision !== requestRevision.current) return;
        setFailure(stateFailure(error));
      })
      .finally(() => {
        if (revision === requestRevision.current) setLoading(false);
      });
    return () => {
      controller.abort();
      if (revision === requestRevision.current) requestRevision.current += 1;
    };
  }, []);

  useEffect(() => {
    const verificationRunning = status?.verification?.state === "running" || status?.verificationInProgress;
    if (action !== "connect" && !verificationRunning) return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      await loadStatus(undefined, true);
      if (!cancelled) timer = window.setTimeout(poll, 3_000);
    };
    timer = window.setTimeout(poll, 3_000);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [action, loadStatus, status?.verification?.state, status?.verificationInProgress]);

  function chooseSource(nextSource: WebullSource) {
    if (nextSource === "webull" && failure?.kind === "disabled") return;
    if (source === undefined) setInternalSource(nextSource);
    onSourceChange?.(nextSource);
  }

  async function finishAction(nextAction: Exclude<LoadingAction, null>, work: () => Promise<{ redirectUrl?: string | null; message?: string | null; status?: WebullStatus | null }>) {
    setAction(nextAction);
    setActionError("");
    try {
      const result = await work();
      if (result.status) setStatus(result.status);
      if (result.redirectUrl) {
        window.location.assign(result.redirectUrl);
        return;
      }
      if (!result.status) await loadStatus(undefined, true);
      setAnnouncement(result.message || (nextAction === "sync" ? "Webull sync completed." : nextAction === "backfill" ? "Webull history backfill requested." : "Webull account updated."));
    } catch (error) {
      let refreshedStatus: WebullStatus | null = null;
      if (nextAction === "connect") {
        refreshedStatus = await loadStatus(undefined, true);
        if (error instanceof WebullApiError && error.status === 409 &&
            (refreshedStatus?.verification?.state === "running" || refreshedStatus?.verificationInProgress)) {
          setAnnouncement("Webull verification is already in progress. This page will update automatically.");
          return;
        }
      }
      const nextFailure = stateFailure(error);
      setActionError(nextFailure.message);
      if (nextFailure.kind === "unauthorized") setFailure(nextFailure);
    } finally {
      setAction(null);
    }
  }

  function connect() {
    void finishAction("connect", () => connectWebull());
  }

  function signIn() {
    window.location.assign(webullLoginUrl("/?source=webull"));
  }

  function selectAccount(accountId: string) {
    void finishAction("select", () => selectWebullAccount(accountId));
  }

  function sync() {
    void finishAction("sync", () => syncWebull(status?.selectedAccountId || null));
  }

  function backfill() {
    void finishAction("backfill", () => backfillWebull(status?.selectedAccountId || null));
  }

  const webullUnavailable = failure?.kind === "disabled";
  const verificationInProgress = action === "connect" || status?.verification?.state === "running" || Boolean(status?.verificationInProgress);
  const sourceStatus = loading && !status
    ? "Checking connection..."
    : failure?.kind === "disabled"
      ? "Not configured"
      : status?.nextAction === "configure"
        ? "Configuration required"
        : failure?.kind === "unauthorized" || status?.nextAction === "sign_in"
          ? "Sign-in required"
          : verificationInProgress
            ? "Verifying"
            : status?.connected
              ? "Connected"
              : status?.verification?.state === "failed" || status?.verification?.state === "timed_out"
                ? "Action required"
                : "Not connected";
  const disconnectedHeading = verificationInProgress
    ? "Webull verification is running."
    : status?.verification?.state === "failed"
      ? "Webull verification needs attention."
      : status?.verification?.state === "timed_out"
        ? "Webull verification did not finish."
        : "Verify the configured Webull account.";
  const verificationButtonLabel = verificationInProgress
    ? "Verification in progress"
    : status?.nextAction === "retry_verification"
      ? "Retry Webull verification"
      : "Verify Webull connection";
  const classes = ["webull-dashboard", className].filter(Boolean).join(" ");

  return (
    <section className={classes} aria-label="Portfolio source">
      <style>{WEBULL_STYLES}</style>
      <div className="webull-source-tabs" role="tablist" aria-label="Portfolio source">
        <button id={tabId(baseId, "manual")} type="button" role="tab" aria-selected={selectedSource === "manual"} aria-controls={panelId(baseId, "manual")} tabIndex={selectedSource === "manual" ? 0 : -1} className={selectedSource === "manual" ? "webull-source-tab webull-source-tab-active" : "webull-source-tab"} onClick={() => chooseSource("manual")}>Manual</button>
        <button id={tabId(baseId, "webull")} type="button" role="tab" aria-selected={selectedSource === "webull"} aria-controls={panelId(baseId, "webull")} tabIndex={selectedSource === "webull" ? 0 : -1} aria-disabled={webullUnavailable} title={webullUnavailable ? "Webull is not configured for this deployment." : undefined} className={selectedSource === "webull" ? "webull-source-tab webull-source-tab-active" : "webull-source-tab"} onClick={() => chooseSource("webull")}>Webull</button>
        <span className="webull-source-tab-status" role="status">{sourceStatus}</span>
      </div>

      {selectedSource === "manual" ? (
        <div id={panelId(baseId, "manual")} role="tabpanel" aria-labelledby={tabId(baseId, "manual")} className="webull-manual-panel">
          {manualContent || (
            <div>
              <span className="webull-eyebrow">Manual source active</span>
              <h2>Build the portfolio directly.</h2>
              <p>Continue editing the allocation ledger. Switching to Webull does not overwrite the manual draft or run analysis.</p>
            </div>
          )}
        </div>
      ) : (
        <div id={panelId(baseId, "webull")} role="tabpanel" aria-labelledby={tabId(baseId, "webull")} className="webull-panel">
          {loading && !status ? (
            <div className="webull-state" role="status" aria-live="polite" aria-busy="true"><span className="webull-eyebrow">Webull</span><h2>Loading connected portfolio…</h2><p>Checking connection and account availability.</p><div className="webull-loading-bar" /></div>
          ) : failure?.kind === "disabled" ? (
            <div className="webull-state"><span className="webull-eyebrow">Integration unavailable</span><h2>Webull is not configured.</h2><p>{failure.message}</p></div>
          ) : failure?.kind === "unauthorized" ? (
            <div className="webull-state webull-state-error" role="alert"><span className="webull-eyebrow">Sign-in required</span><h2>Sign in to view your saved Webull status.</h2><p>{failure.message}</p><div className="webull-state-actions"><button type="button" className="webull-button webull-button-primary" onClick={signIn}>Sign in with GitHub</button><button type="button" className="webull-button" onClick={() => void loadStatus()}>Retry access</button></div></div>
          ) : failure?.kind === "error" && !status ? (
            <div className="webull-state webull-state-error" role="alert"><span className="webull-eyebrow">Connection error</span><h2>Webull could not be loaded.</h2><p>{failure.message}</p><button type="button" className="webull-button" onClick={() => void loadStatus()}>Try again</button></div>
          ) : status?.nextAction === "configure" ? (
            <div className="webull-state webull-state-error" role="alert">
              <span className="webull-eyebrow">Configuration required</span>
              <h2>Finish the server-side read-only gate.</h2>
              <p>Portfolio Lab will not verify Webull until the private service confirms that the configured API key cannot place, replace, or cancel trades.</p>
              <p className="webull-approval-note">Confirm the Webull key is limited to Account Infos and Order Query, then enable the private service gate.</p>
            </div>
          ) : status && !status.connected ? (
            <div className="webull-state">
              <span className="webull-eyebrow">Server-side read-only connection</span>
              <h2>{disconnectedHeading}</h2>
              <p>Portfolio Lab will test the Webull API credentials configured on the server and load the accounts, balances, positions, and available history they can access. Credentials are never sent to this browser.</p>
              <p className="webull-approval-note">First-time Webull approval can take up to five minutes. Verification runs as one protected job: repeated starts are blocked, and returning to this tab will resume its status.</p>
              {status.verification ? <VerificationStatusCard verification={status.verification} /> : null}
              {actionError && actionError !== status.verification?.error?.message ? <div className="webull-notice webull-notice-error" role="alert">{actionError}</div> : null}
              <button type="button" className="webull-button webull-button-primary" disabled={Boolean(action) || verificationInProgress || status.nextAction === "wait"} onClick={connect}>{verificationButtonLabel}</button>
              {verificationInProgress ? <p className="webull-approval-progress" role="status" aria-live="polite">Portfolio Lab is checking the saved verification status every few seconds. You may leave this page; the latest stage and result will be here when you return.</p> : null}
            </div>
          ) : status ? (
            <ConnectedDashboard status={status} action={action} actionError={actionError} announcement={announcement} onSelectAccount={selectAccount} onSync={sync} onBackfill={backfill} onAnalyzeCurrentHoldings={onAnalyzeCurrentHoldings} />
          ) : null}
        </div>
      )}
    </section>
  );
}

const WEBULL_STYLES = `
.webull-dashboard{margin:18px 0;color:var(--ink);font-family:var(--sans)}
.webull-dashboard *{box-sizing:border-box}
.webull-source-tabs{display:flex;align-items:stretch;border:2px solid var(--rule);background:var(--paper-alt)}
.webull-source-tab{appearance:none;border:0;border-right:1px solid var(--rule);background:transparent;color:var(--ink-soft);min-width:132px;padding:13px 20px;font:700 .72rem var(--mono);letter-spacing:.08em;text-transform:uppercase;cursor:pointer}
.webull-source-tab:hover{background:var(--paper);color:var(--ink)}
.webull-source-tab:focus-visible,.webull-button:focus-visible,.webull-chart-tab:focus-visible,.webull-field select:focus-visible,.webull-table-shell:focus-visible,.webull-issues summary:focus-visible{outline:3px solid var(--accent);outline-offset:-3px}
.webull-source-tab-active{background:var(--ink);color:var(--paper)}
.webull-source-tab-active:hover{background:var(--ink);color:var(--paper)}
.webull-source-tab[aria-disabled="true"]{opacity:.45;cursor:not-allowed}
.webull-source-tab-status{margin-left:auto;display:grid;place-items:center;padding:0 16px;color:var(--ink-soft);font:700 .62rem var(--mono);text-transform:uppercase;letter-spacing:.05em}
.webull-panel,.webull-manual-panel{border:1px solid var(--rule);border-top:0;padding:20px}
.webull-manual-panel{background:var(--paper-alt)}
.webull-manual-panel h2,.webull-state h2{font-size:1.35rem;margin:5px 0 8px}
.webull-manual-panel p,.webull-state p,.webull-intro{color:var(--ink-soft);font:.94rem/1.6 var(--serif);max-width:74ch}
.webull-eyebrow{display:block;color:var(--accent);font:700 .66rem/1.25 var(--mono);letter-spacing:.12em;text-transform:uppercase}
.webull-state{border:2px solid var(--rule);padding:28px;background:var(--paper-alt)}
.webull-state-error{border-left:5px solid #C32B2B}
.webull-state .webull-button{margin-top:18px}
.webull-state-actions{display:flex;gap:9px;flex-wrap:wrap}.webull-state-actions .webull-button{margin-top:18px}
.webull-approval-note{margin-top:12px!important;padding-left:11px;border-left:3px solid var(--amber);font:.72rem/1.5 var(--mono)!important}
.webull-approval-progress{margin-top:10px!important;color:var(--amber)!important;font:700 .68rem/1.45 var(--mono)!important}
.webull-verification-card{display:grid;gap:13px;margin:18px 0;padding:15px;border:1px solid var(--rule);border-left:5px solid var(--cobalt);background:var(--paper)}
.webull-verification-card[data-state="running"]{border-left-color:var(--amber)}.webull-verification-card[data-state="succeeded"]{border-left-color:var(--emerald)}.webull-verification-card[data-state="failed"],.webull-verification-card[data-state="timed_out"]{border-left-color:#C32B2B}
.webull-verification-card-compact{margin:0 0 14px;background:var(--paper-alt)}
.webull-verification-heading{display:grid;gap:4px}.webull-verification-heading strong{font:800 .9rem/1.3 var(--sans)}
.webull-verification-times{display:flex;gap:12px 24px;flex-wrap:wrap;margin:0}.webull-verification-times div{display:grid;gap:3px}.webull-verification-times dt{color:var(--ink-soft);font:700 .58rem var(--mono);letter-spacing:.06em;text-transform:uppercase}.webull-verification-times dd{margin:0;font:700 .68rem var(--mono)}
.webull-verification-error{display:grid;gap:4px;padding-top:11px;border-top:1px solid var(--rule-light);color:#8D2020}.webull-verification-error strong{font:.78rem/1.45 var(--mono)}.webull-verification-error small{font:.58rem var(--mono);letter-spacing:.04em;text-transform:uppercase}
.webull-loading-bar{height:4px;margin-top:20px;background:linear-gradient(90deg,var(--accent) 0 20%,var(--rule-light) 20% 100%);background-size:200% 100%;animation:webull-loading 1.2s linear infinite}
@keyframes webull-loading{to{background-position:-200% 0}}
@media(prefers-reduced-motion:reduce){.webull-loading-bar{animation:none}.webull-button{transition:none}}
.webull-source-strip{display:grid;grid-template-columns:minmax(230px,1.4fr) minmax(180px,.85fr) minmax(180px,.75fr) auto;gap:16px;align-items:end;border:2px solid var(--rule);background:var(--paper-alt);padding:16px}
.webull-source-identity,.webull-source-freshness{display:grid;gap:4px;min-width:0}
.webull-source-identity strong,.webull-source-freshness strong{font:800 .86rem var(--sans);overflow-wrap:anywhere}
.webull-source-identity small{color:var(--ink-soft);font:.62rem var(--mono)}
.webull-source-freshness{align-content:end}.webull-quality{width:max-content;border:1px solid var(--rule);padding:3px 6px;color:var(--ink-soft);font:700 .58rem var(--mono);letter-spacing:.04em;text-transform:uppercase}
.webull-quality[data-quality="verified"]{border-color:var(--emerald);color:var(--emerald)}
.webull-quality[data-quality="estimated"],.webull-quality[data-quality="partial"],.webull-quality[data-quality="stale"]{border-color:var(--amber);color:var(--amber)}
.webull-field{display:grid;gap:5px;color:var(--ink-soft);font:700 .62rem var(--mono);text-transform:uppercase;letter-spacing:.05em}
.webull-field select{width:100%;min-width:0;background:var(--paper);color:var(--ink);border:1px solid var(--rule);padding:9px;font:700 .72rem var(--mono)}
.webull-source-actions{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}
.webull-button,.webull-chart-tab{appearance:none;border:1px solid var(--rule);border-radius:0;background:var(--paper);color:var(--ink);padding:10px 13px;font:700 .67rem var(--mono);letter-spacing:.05em;text-transform:uppercase;cursor:pointer;transition:background .15s,color .15s,border-color .15s}
.webull-button:hover,.webull-chart-tab:hover{background:var(--ink);color:var(--paper)}
.webull-button-primary{background:var(--accent);border-color:var(--accent);color:var(--paper)}
.webull-button-primary:hover{background:var(--ink);border-color:var(--ink)}
.webull-button-text{border-color:transparent;background:transparent;color:var(--ink-soft);padding-inline:5px}
.webull-button:disabled,.webull-chart-tab:disabled{opacity:.45;cursor:not-allowed}.webull-button:disabled:hover,.webull-chart-tab:disabled:hover{background:var(--paper);color:var(--ink)}
.webull-notice{display:grid;gap:3px;margin:14px 0;padding:12px 14px;border:1px solid var(--rule);border-left:5px solid var(--cobalt);background:var(--paper-alt);color:var(--ink-soft);font:.74rem/1.5 var(--mono)}
.webull-notice strong{color:var(--ink)}.webull-notice-error{border-left-color:#C32B2B}.webull-notice-warning{border-left-color:var(--amber)}
.webull-metric-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border:2px solid var(--rule);margin:18px 0}
.webull-metric{min-width:0;padding:16px;border-right:1px solid var(--rule-light);border-bottom:1px solid var(--rule-light)}
.webull-metric:nth-child(3n){border-right:0}.webull-metric:nth-last-child(-n+3){border-bottom:0}
.webull-metric-label{display:block;color:var(--ink-soft);font:700 .61rem var(--mono);letter-spacing:.05em;text-transform:uppercase}
.webull-metric strong{display:block;margin:8px 0 5px;font:800 clamp(1.15rem,2vw,1.55rem) var(--mono);letter-spacing:-.04em;overflow-wrap:anywhere}
.webull-metric-detail{margin:-2px 0 6px;color:var(--ink);font:700 .65rem var(--mono)}
.webull-provenance{display:block;color:var(--ink-soft);font:.57rem/1.4 var(--mono);text-transform:uppercase;letter-spacing:.03em;white-space:normal}
.webull-card{border:1px solid var(--rule);padding:18px;margin:18px 0;min-width:0}.webull-chart-card{border-width:2px}
.webull-card-heading{display:flex;justify-content:space-between;gap:14px;align-items:flex-start}.webull-card-heading h3,.webull-analysis-card h3{font-size:1.22rem;line-height:1.15;margin:5px 0 0;text-transform:uppercase}
.webull-count{border:1px solid var(--rule);padding:4px 7px;color:var(--ink-soft);font:700 .6rem var(--mono);text-transform:uppercase}
.webull-chart-tabs{display:flex}.webull-chart-tab+.webull-chart-tab{border-left:0}.webull-chart-tab-active{background:var(--ink);color:var(--paper)}
.webull-chart-legend{display:flex;gap:16px;flex-wrap:wrap;margin:17px 0 5px;color:var(--ink-soft);font:.64rem var(--mono)}
.webull-chart-legend span{display:flex;align-items:center;gap:6px}.webull-chart-legend i{display:block;width:18px;height:3px}.webull-chart-key-portfolio{background:var(--ink)}.webull-chart-key-benchmark{background:var(--cobalt)}.webull-chart-key-flow{height:10px!important;width:2px!important;background:var(--accent)}
.webull-chart-shell{overflow-x:auto}.webull-chart-shell svg{display:block;width:100%;min-width:640px;height:auto}
.webull-chart-grid{stroke:var(--rule-light);stroke-width:1;vector-effect:non-scaling-stroke}.webull-chart-axis-label{fill:var(--ink-soft);font:10px var(--mono)}
.webull-chart-portfolio,.webull-chart-benchmark{fill:none;stroke-width:2.5;vector-effect:non-scaling-stroke}.webull-chart-portfolio{stroke:var(--ink)}.webull-chart-benchmark{stroke:var(--cobalt)}.webull-chart-flow{stroke:var(--accent);stroke-width:1;stroke-dasharray:3 3;vector-effect:non-scaling-stroke}
.webull-empty-inline{margin-top:16px;padding:16px;background:var(--paper-alt);color:var(--ink-soft);font:.75rem/1.5 var(--mono)}
.webull-analysis-card{display:grid;grid-template-columns:1fr auto;gap:20px;align-items:center;border:2px solid var(--rule);border-left:6px solid var(--accent);padding:18px;margin:18px 0;background:var(--paper-alt)}
.webull-analysis-card p{margin:7px 0 4px;color:var(--ink-soft);font:.9rem/1.5 var(--serif)}.webull-analysis-card small{color:var(--ink-soft);font:.65rem var(--mono)}
.webull-detail-grid{display:grid;grid-template-columns:1.15fr .85fr;gap:18px}.webull-detail-grid>.webull-card{margin:0}
.webull-table-shell{overflow:auto;margin-top:14px}.webull-table-shell:focus-visible{outline-offset:2px}
.webull-table{width:100%;min-width:760px;border-collapse:collapse;white-space:nowrap;font:.7rem var(--mono)}
.webull-table th,.webull-table td{padding:10px 9px;border-bottom:1px solid var(--rule-light);text-align:left;vertical-align:top}.webull-table thead th{background:var(--paper-alt);border-bottom:2px solid var(--rule);color:var(--ink);font-size:.59rem;letter-spacing:.06em;text-transform:uppercase}
.webull-table tbody th{font-weight:700}.webull-table tbody th b,.webull-table tbody th small{display:block}.webull-table tbody th small{max-width:220px;color:var(--ink-soft);font-size:.56rem;font-weight:400;white-space:normal}
.webull-table tbody tr:last-child th,.webull-table tbody tr:last-child td{border-bottom:2px solid var(--rule)}.webull-table-empty{text-align:center!important;color:var(--ink-soft);padding:24px!important}
.webull-activity-table .webull-provenance{max-width:180px}.webull-eligibility{display:inline-block;border:1px solid var(--amber);padding:3px 5px;color:var(--amber);font-size:.56rem;font-weight:700;text-transform:uppercase}.webull-eligibility-included{border-color:var(--emerald);color:var(--emerald)}
.webull-issues{border:1px solid var(--rule);margin:18px 0;scroll-margin:20px}.webull-issues summary{display:flex;justify-content:space-between;gap:12px;padding:14px 16px;background:var(--paper-alt);font:700 .7rem var(--mono);letter-spacing:.05em;text-transform:uppercase;cursor:pointer}.webull-issues summary span{border:1px solid var(--rule);padding:1px 5px}
.webull-issues>p{padding:16px;color:var(--ink-soft);font:.75rem var(--mono)}.webull-issues ul{list-style:none;margin:0;padding:0}.webull-issues li{padding:13px 16px;border-top:1px solid var(--rule-light);border-left:4px solid var(--cobalt)}.webull-issues li[data-severity="warning"]{border-left-color:var(--amber)}.webull-issues li[data-severity="error"]{border-left-color:#C32B2B}.webull-issues li p{margin:3px 0;color:var(--ink-soft);font:.85rem/1.5 var(--serif)}.webull-issues li small{color:var(--ink-soft);font:.6rem var(--mono)}
.webull-sr-only{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
@media(max-width:1000px){.webull-source-strip{grid-template-columns:1fr 1fr}.webull-source-actions{justify-content:flex-start}.webull-detail-grid{grid-template-columns:1fr}}
@media(max-width:720px){.webull-panel,.webull-manual-panel{padding:14px}.webull-source-strip{grid-template-columns:1fr}.webull-metric-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.webull-metric:nth-child(3n){border-right:1px solid var(--rule-light)}.webull-metric:nth-child(2n){border-right:0}.webull-metric:nth-last-child(-n+3){border-bottom:1px solid var(--rule-light)}.webull-metric:nth-last-child(-n+2){border-bottom:0}.webull-analysis-card{grid-template-columns:1fr}.webull-card-heading{display:grid}.webull-chart-tabs{width:100%}.webull-chart-tab{flex:1}.webull-source-tab{min-width:0;flex:1}.webull-source-tab-status{display:none}}
@media(max-width:440px){.webull-source-tabs{display:grid;grid-template-columns:1fr 1fr}.webull-source-tab{padding-inline:10px}.webull-state{padding:20px}.webull-metric-grid{grid-template-columns:1fr}.webull-metric,.webull-metric:nth-child(2n),.webull-metric:nth-child(3n),.webull-metric:nth-last-child(-n+2){border-right:0;border-bottom:1px solid var(--rule-light)}.webull-metric:last-child{border-bottom:0}.webull-source-actions{display:grid}.webull-source-actions .webull-button{width:100%}.webull-chart-tabs{display:grid}.webull-chart-tab+.webull-chart-tab{border-left:1px solid var(--rule);border-top:0}.webull-analysis-card .webull-button{width:100%}}
`;
