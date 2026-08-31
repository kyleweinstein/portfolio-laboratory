# Portfolio Laboratory

Portfolio Laboratory is an interactive portfolio-risk and allocation dashboard built around free historical market data. Users can edit holdings and weights; select a one-, three-, or five-year lookback; choose a benchmark and risk-free rate; and compare the portfolio with long-only optimization scenarios.

Portfolio CSVs can be imported directly in the browser. The importer accepts `Symbol` plus either `Value` or `Weight`, merges duplicate symbols, converts position values into normalized portfolio weights, and reports malformed rows without replacing the existing portfolio. The CSV file itself is not uploaded or retained; imported ticker symbols are sent through the dashboard’s market-data route when analysis refreshes.

## What it calculates

- Portfolio and holding annualized return from average daily log return
- Annualized volatility from sample daily volatility × √252
- Sharpe ratio using the user-supplied annual risk-free rate
- Maximum peak-to-trough drawdown
- One-day historical value at risk and conditional value at risk at 95%
- Holding and portfolio beta relative to the selected benchmark
- Portfolio composition across automatically inferred style, sector, and primary-factor dimensions, shown as shared-scale radar small multiples
- Pairwise Pearson correlations of aligned daily log returns
- A selectable, equal-height direction chart for two or more holdings that encodes each aligned daily return as up or down while intentionally omitting magnitude
- Ranked pair opportunities using correlation, spread volatility, and a clearly labeled relative-motion heuristic
- Greedy low-correlation rebalance buckets with target/price-implied mix, drift bands, and within-bucket trade instructions
- Long-only minimum-volatility and maximum-Sharpe scenarios, with a 60% per-holding cap

All return series use only dates present for every holding and the benchmark. Portfolio weights are normalized to 100% for calculations.

The direction chart is designed for visual co-movement comparison, not performance measurement. It converts every positive aligned daily return to up and every negative return to down, so a high-performing asset cannot compress the other holdings. Because magnitude is discarded, the pattern is related to—but not identical to—the Pearson return correlations reported elsewhere.

## Pairing and rebalance methodology

Pair opportunities are ranked by Pearson correlation, lowest first. Negative correlation is labeled “anticorrelated”; positive values are never described as anticorrelation. The dashboard also reports annualized volatility of each pair’s return spread and a rebalancing-potential heuristic:

`spread volatility × (1 − correlation) ÷ 2`

The heuristic measures relative motion, not expected return or guaranteed “volatility harvest.”

Holdings are greedily paired from the lowest-correlation unassigned pair. When the portfolio has an odd number of holdings, the remaining holding joins the bucket with which it has the lowest average correlation. Within each bucket, drift is the largest absolute percentage-point gap between price-implied and target mix. Price-implied weights assume the portfolio began at target at the start of the selected drift window and then received no trades or cash flows. A trigger preserves the bucket’s drifted capital and calculates trades that restore the target mix inside that bucket.

## Data source and assumptions

The app retrieves adjusted daily closing prices from Yahoo Finance's public chart endpoint through a server-side route. No API key is required. The dashboard never substitutes synthetic values when the source is unavailable.

Adjusted-close history may be delayed, corrected, incomplete, or unavailable for some symbols. It can differ from executable prices and does not model taxes, fees, slippage, intraday moves, liquidity, corporate-action timing, or currency conversion. Securities with short histories reduce the common analysis window.

Historical covariance and average returns are unstable estimates. Optimization results are scenario outputs, not recommendations, and may change substantially with the lookback, benchmark, risk-free rate, asset set, or constraints.

Style, sector, and factor classifications are best-fit historical inferences based on return correlation with representative free-data ETF proxies. They are not issuer classifications or factor-regression estimates, and low-confidence labels may change with the analysis window. Each holding contributes its full normalized weight to one primary category in each dimension.

Rebalancing can add, reduce, or have no effect on return. Its outcome depends on future relative movement, mean reversion, thresholds, taxes, spreads, fees, and the behavior of trending assets. Correlations can change abruptly, especially during market stress.

## Intended use

This is an educational analytics tool, not investment, tax, accounting, or legal advice. Verify source data and methodology with a qualified provider before making financial decisions.

## Multi-broker publishing

The Railway deployment adds a privacy-first publication layer around the manual
laboratory. GitHub owner authentication protects broker connections, sync,
settings, privacy preview, and publication. Discord OAuth protects the follower
gallery at `/portfolios` and verifies membership in one configured server every
five minutes. `/manage` and follower access are separate authorization
boundaries.

One selected brokerage account becomes one independently titled portfolio card.
Cards show a percentage-only performance chart, YTD return, provider,
performance-through date, and quality. Details show actual cash-flow-adjusted
performance and a stored, read-only version of the manual laboratory's risk,
classification, direction, correlation, rebalance, and optimization outputs.

Follower responses are built from an allowlisted publication projection. They
may contain only:

- percentage return and benchmark series;
- signed allocation weights, including negative `Cash / Margin` when the account
  is borrowing;
- percentage-only gross, net, and analytical-sleeve exposure;
- optional average cost per share and unrealized return percentage; and
- privacy-safe modeled analytics.

Account balances, account identifiers, quantities, current position values, cash
dollar amounts, total cost basis, dollar P&L, contributions, and raw activities
never enter follower JSON, HTML, SVG, ARIA text, browser storage, or caches.
Average cost per share is the sole permitted dollar-denominated display field.
Raw broker values remain private in PostgreSQL because they are required to
derive weights and performance.

Webull remains supported through the approved Retail Trading API. M1 Finance uses
Plaid Investments with Link, encrypted Item tokens, signed webhooks, holdings,
and optional investment transactions. Schwab's provider-neutral OAuth,
throttling, balance, position, and transaction mapping is scaffolded but its live
connector is disabled. None of the supported adapter interfaces expose trading,
transfer, or money-movement operations.

Historical returns are Portfolio Lab calculations from reconciled after-close
account-value snapshots and external cash activities. Monthly M1 statements can
provide private reconciliation anchors through the local encrypted statement
tool. Publication is withheld when cash-flow coverage, snapshot timing, signed
weights, statement totals, or required market history do not reconcile. A failed
sync or analysis leaves the preceding good public revision active.

All new capabilities are fail-closed behind independent feature flags. The Sites
copy remains manual-only; authenticated broker and follower experiences run on
Railway. See [`services/webull/README.md`](services/webull/README.md), the two
checked-in `.env.example` files, and
[`tools/m1-statements/README.md`](tools/m1-statements/README.md) for the private
runtime and operator contracts.
