import type {
  PortfolioCard,
  PortfolioDetail,
  PublicationQuality,
  PublishedDirectionComparison,
  PublishedPerformancePoint,
} from "./publication-server";
import Link from "next/link";
import PublishedDiversificationMap from "./published-diversification-map";
import { publicationChartSegments } from "./publication-chart";

export function PortfolioMasthead({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return <>
    <nav className="portfolio-nav" aria-label="Portfolio Lab">
      <Link href="/">Manual lab</Link>
      <Link href="/portfolios">Tracked portfolios</Link>
      <Link href="/manage">Manage</Link>
      <span>Discord member · <a href="/api/discord/auth/logout?return_to=%2F">Sign out</a></span>
    </nav>
    <header className="publication-header">
      <div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>
    </header>
  </>;
}

export function PortfolioCards({ portfolios }: { portfolios: PortfolioCard[] }) {
  if (!portfolios.length) {
    return <section className="notice"><b>No portfolios are published yet.</b> The owner can publish a reconciled after-market-close snapshot from the management page.</section>;
  }
  return <section className="published-card-grid" aria-label="Published portfolios">
    {portfolios.map(portfolio => <Link className="published-portfolio-card" href={`/portfolios/${portfolio.slug}`} key={portfolio.slug}>
      <div className="published-card-heading"><div><span className="eyebrow">{portfolio.provider}</span><h2>{portfolio.title}</h2></div><QualityBadge quality={portfolio.quality}/></div>
      <PerformanceChart points={portfolio.performance} title={`${portfolio.title} percentage return`}/>
      <div className="published-card-summary"><div><span>YTD performance</span><strong className={tone(portfolio.ytdReturnPercent)}>{formatPercent(portfolio.ytdReturnPercent)}</strong></div><div><span>Performance through</span><b>{formatDate(portfolio.performanceThrough)}</b></div></div>
      <span className="published-card-link">View holdings and risk →</span>
    </Link>)}
  </section>;
}

export function PortfolioDetailView({ portfolio }: { portfolio: PortfolioDetail }) {
  const risk = portfolio.analytics.risk;
  return <>
    <section className="published-detail-summary card">
      <div><span className="eyebrow">{portfolio.provider}</span><h2>Actual account performance</h2><p className="muted">Cash-flow-adjusted account results. Current-holdings analytics below are a separate fixed-sleeve model.</p></div>
      <div className="published-ytd"><span>YTD performance</span><strong className={tone(portfolio.ytdReturnPercent)}>{formatPercent(portfolio.ytdReturnPercent)}</strong><small>Through {formatDate(portfolio.performanceThrough)}</small></div>
    </section>
    <section className="performance-card card">
      <div className="section-title"><div><span className="eyebrow">Percentage performance</span><h2>Portfolio versus {portfolio.benchmarkSymbol ?? "benchmark"}</h2></div><QualityBadge quality={portfolio.quality}/></div>
      <PerformanceChart points={portfolio.performance} title={`${portfolio.title} and benchmark percentage returns`} showBenchmark showDataTable/>
      <p className="note">Performance values are percentage returns only. Account values, contributions, and currency profit or loss are never published.</p>
    </section>
    <section className="published-exposure-grid">
      <PublishedMetric label="Gross exposure" value={formatPercent(portfolio.grossExposurePercent)} />
      <PublishedMetric label="Net exposure" value={formatPercent(portfolio.netExposurePercent)} />
      <PublishedMetric label="Analytics sleeve" value={formatPercent(portfolio.analyticsSleevePercent)} />
    </section>
    <section className="card published-holdings">
      <div className="section-title"><div><span className="eyebrow">Signed allocation</span><h2>Holdings</h2></div><span className="pill">As of {formatDate(portfolio.holdingsAsOf)}</span></div>
      <p className="chart-intro">Weights reconcile to net account value. Cash / Margin is negative when the portfolio is borrowing on margin.</p>
      <div className="table-wrap"><table><thead><tr><th>Holding</th><th>Weight</th><th>Average cost / share</th><th>Return</th><th>Source</th></tr></thead><tbody>
        {portfolio.holdings.map((holding, index) => <tr className={holding.weightPercent !== null && holding.weightPercent < 0 ? "negative-holding" : ""} key={`${holding.kind}-${holding.symbol ?? index}`}>
          <td><strong>{holding.symbol ?? holding.name}</strong>{holding.symbol && holding.name !== holding.symbol ? <small>{holding.name}</small> : null}</td>
          <td>{formatPercent(holding.weightPercent)}</td>
          <td>{holding.kind === "security" ? formatCostBasis(holding.costBasisPerShare) : "N/A"}</td>
          <td className={tone(holding.returnPercent)}>{holding.kind === "security" ? formatPercent(holding.returnPercent) : "N/A"}</td>
          <td><QualityBadge quality={holding.quality}/></td>
        </tr>)}
      </tbody></table></div>
    </section>
    {risk && <section className="published-risk-section">
      <div className="section-title"><div><span className="eyebrow">Current-holdings model</span><h2>Risk statistics</h2></div><span className="pill">Read-only sleeve analytics</span></div>
      <div className="published-risk-grid">
        <PublishedMetric label="Annualized return" value={formatPercent(risk.annualReturnPercent)}/>
        <PublishedMetric label="Annualized volatility" value={formatPercent(risk.annualVolatilityPercent)}/>
        <PublishedMetric label="Sharpe ratio" value={formatNumber(risk.sharpeRatio)}/>
        <PublishedMetric label="Maximum drawdown" value={formatPercent(risk.maximumDrawdownPercent)}/>
        <PublishedMetric label="Historical VaR (95%)" value={formatPercent(risk.valueAtRisk95Percent)}/>
        <PublishedMetric label="Beta" value={formatNumber(risk.beta)}/>
      </div>
    </section>}
    {portfolio.analytics.directionComparison && <section className="card publication-analytics-card"><span className="eyebrow">Direction comparison</span><h2>Daily up / down pattern</h2><DirectionComparison data={portfolio.analytics.directionComparison}/></section>}
    {portfolio.analytics.correlationMap && <section className="card publication-analytics-card"><span className="eyebrow">Diversification</span><h2>Correlation map</h2><p className="chart-intro">Search or zoom to inspect dense portfolios, drag with a pointer or touch to pan, and use the keyboard or accessible table for exact correlations.</p><PublishedDiversificationMap data={portfolio.analytics.correlationMap}/></section>}
    {portfolio.analytics.pairInsights.length > 0 && <section className="card publication-analytics-card"><span className="eyebrow">Low-correlation opportunities</span><h2>Pair insights</h2><div className="published-pair-list">{portfolio.analytics.pairInsights.slice(0, 8).map(pair => <div key={`${pair.leftSymbol}-${pair.rightSymbol}`}><strong>{pair.leftSymbol} / {pair.rightSymbol}</strong><dl><div><dt>Correlation</dt><dd>{formatNumber(pair.correlation)}</dd></div><div><dt>Spread volatility</dt><dd>{formatPercent(pair.spreadVolatilityPercent)}</dd></div><div><dt>Rebalance potential</dt><dd>{formatPercent(pair.rebalancePotentialPercent)}</dd></div></dl></div>)}</div></section>}
    {portfolio.analytics.classifications.length > 0 && <section className="card publication-analytics-card"><span className="eyebrow">Composition</span><h2>Style / sector / factor</h2><div className="table-wrap"><table><thead><tr><th>Holding</th><th>Style</th><th>Sector / sleeve</th><th>Factor</th><th>Confidence</th></tr></thead><tbody>{portfolio.analytics.classifications.map(item => <tr key={item.symbol}><td><strong>{item.symbol}</strong></td><td>{item.style ?? "Unavailable"}</td><td>{item.sector ?? "Unavailable"}</td><td>{item.factor ?? "Unavailable"}</td><td>{item.confidence}</td></tr>)}</tbody></table></div></section>}
    {portfolio.analytics.rebalanceBuckets.length > 0 && <section className="card publication-analytics-card"><span className="eyebrow">Rebalance model</span><h2>Sleeve buckets</h2><div className="published-buckets">{portfolio.analytics.rebalanceBuckets.map((bucket, index) => <article className={bucket.triggered ? "triggered" : ""} key={`${bucket.symbols.join("-")}-${index}`}><div><strong>{bucket.symbols.join(" + ")}</strong><span>{bucket.triggered ? "Band crossed" : "Within band"}</span></div><b>{formatPercent(bucket.driftPercent)} drift</b><div>{bucket.symbols.map((symbol, symbolIndex) => <p key={symbol}><strong>{symbol}</strong><span>Target {formatPercent(bucket.targetWeightsPercent[symbolIndex])}</span><span>Price-implied {formatPercent(bucket.priceImpliedWeightsPercent[symbolIndex])}</span></p>)}</div></article>)}</div></section>}
    {portfolio.analytics.optimizedAllocation.length > 0 && <section className="card publication-analytics-card"><span className="eyebrow">Stored scenario</span><h2>Optimized sleeve allocation</h2><p className="chart-intro">This is a read-only scenario generated from the latest analyzed holdings, not a trade instruction.</p><div className="published-allocation-list">{portfolio.analytics.optimizedAllocation.map(item => <div key={item.symbol}><strong>{item.symbol}</strong><span><i style={{ width: `${Math.max(0, Math.min(100, item.weightPercent))}%` }}/></span><b>{formatPercent(item.weightPercent)}</b></div>)}</div></section>}
  </>;
}

function PerformanceChart({
  points,
  title,
  showBenchmark = false,
  showDataTable = false,
}: {
  points: PublishedPerformancePoint[];
  title: string;
  showBenchmark?: boolean;
  showDataTable?: boolean;
}) {
  if (points.length < 2) return <div className="published-chart-empty">Performance history is not yet available.</div>;
  const width = 760;
  const height = 240;
  const padding = 22;
  const values = points.flatMap(point => [point.returnPercent, ...(showBenchmark && point.benchmarkReturnPercent !== null ? [point.benchmarkReturnPercent] : [])]);
  const minimum = Math.min(0, ...values);
  const maximum = Math.max(0, ...values);
  const range = Math.max(1, maximum - minimum);
  const portfolioSegments = publicationChartSegments(
    points,
    "returnPercent",
    { width, height, padding, minimum, maximum },
  );
  const benchmarkSegments = showBenchmark
    ? publicationChartSegments(
        points,
        "benchmarkReturnPercent",
        { width, height, padding, minimum, maximum },
      )
    : [];
  const zeroY = padding + ((maximum - 0) / range) * (height - padding * 2);
  const firstDate = formatDate(points[0]?.date ?? null);
  const lastDate = formatDate(points.at(-1)?.date ?? null);
  return <div className="published-chart">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}. Latest portfolio return ${formatPercent(points.at(-1)?.returnPercent ?? null)}${showBenchmark ? `; latest benchmark return ${formatPercent(points.at(-1)?.benchmarkReturnPercent ?? null)}` : ""}. Data from ${firstDate} through ${lastDate}.`}>
      <line x1={padding} x2={width - padding} y1={zeroY} y2={zeroY} className="published-zero-line"/>
      {benchmarkSegments.map((segment, index) => <polyline points={segment} className="published-benchmark-line" key={`benchmark-${index}`}/>)}
      {portfolioSegments.map((segment, index) => <polyline points={segment} className="published-return-line" key={`portfolio-${index}`}/>)}
    </svg>
    {showBenchmark && <div className="published-chart-key"><span><i className="portfolio"/>Portfolio</span><span><i className="benchmark"/>Benchmark</span></div>}
    {showDataTable && <div className="sr-only">
      <table>
        <caption>{title}. Percentage returns from {firstDate} through {lastDate}.</caption>
        <thead><tr><th scope="col">Date</th><th scope="col">Portfolio return</th>{showBenchmark && <th scope="col">Benchmark return</th>}</tr></thead>
        <tbody>{points.map(point => <tr key={point.date}><th scope="row">{formatDate(point.date)}</th><td>{formatPercent(point.returnPercent)}</td>{showBenchmark && <td>{formatPercent(point.benchmarkReturnPercent)}</td>}</tr>)}</tbody>
      </table>
    </div>}
  </div>;
}

function DirectionComparison({ data }: { data: PublishedDirectionComparison }) {
  return <div className="published-directions" role="img" aria-label={`Up and down comparison for ${data.lanes.map(lane => lane.symbol).join(", ")}.`}>
    {data.lanes.map(lane => <div key={lane.symbol}><strong>{lane.symbol}</strong><span>{lane.directions.map((direction, index) => <i className={direction > 0 ? "up" : direction < 0 ? "down" : "flat"} style={{ width: `${100 / lane.directions.length}%` }} key={index}/>)}</span><b>{formatPercent(lane.upSharePercent)} up</b></div>)}
  </div>;
}

function PublishedMetric({ label, value }: { label: string; value: string }) {
  return <div className="published-metric"><span>{label}</span><strong>{value}</strong></div>;
}

function QualityBadge({ quality }: { quality: PublicationQuality }) {
  const label: Record<PublicationQuality, string> = {
    broker_reported: "Broker reported",
    statement_reconciled: "Statement reconciled",
    computed: "Portfolio Lab computed",
    estimated: "Estimated",
    unavailable: "Unavailable",
  };
  return <span className={`quality-badge ${quality}`}>{label[quality]}</span>;
}

function tone(value: number | null): string {
  return value === null ? "" : value < 0 ? "negative-value" : value > 0 ? "positive-value" : "";
}

function formatPercent(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "Unavailable"
    : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "Unavailable"
    : value.toFixed(2);
}

function formatCostBasis(value: number | null): string {
  return value === null || !Number.isFinite(value)
    ? "Unavailable"
    : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 }).format(value);
}

function formatDate(value: string | null): string {
  if (!value) return "Unavailable";
  const date = new Date(`${value}T00:00:00Z`);
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(date);
}
