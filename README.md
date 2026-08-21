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

## Owner-only Webull connection

The Railway deployment can add a private, application-level read-only Webull
account view while
leaving every manual portfolio feature public. The browser talks only to
authenticated Vinext routes. Those routes proxy a private FastAPI service backed
by PostgreSQL; Webull credentials, access tokens, and full account payloads never
enter the browser bundle or repository.

The connected view preserves source boundaries:

- Current net liquidation value, cash, market value, day P&L, positions, basis,
  and unrealized P&L are displayed as Webull-reported values.
- Historical returns are Portfolio Lab calculations from atomic post-close Webull
  snapshots and external cash activities. They are labeled estimated until the
  period has complete, reconciled coverage.
- Orders are never interpreted as deposits or withdrawals. Cash, options, crypto,
  shorts, and other unsupported assets remain in account value but are listed as
  excluded from the long-only analytics sleeve.
- `Analyze current holdings` copies only eligible positive stocks and ETFs into
  the editable manual draft and never starts analysis automatically.

The feature is off unless `WEBULL_INTEGRATION_ENABLED=true`. Activation also
requires a GitHub OAuth app, an allowlisted numeric owner ID, a private internal
token, Railway Postgres, the approved Webull App Key/Secret, a persistent SDK
token volume, and confirmation that the broker credential excludes trading
permissions. See
[`services/webull/README.md`](services/webull/README.md) and the checked-in
`.env.example` files for the complete runtime contract. No trading methods are
implemented.

The owner dashboard exposes a durable verification record rather than a
transient spinner. It reports the current stage, timestamps, terminal error or
success, and the exact next action after reloads, service restarts, or duplicate
clicks. Broker access remains unavailable—and the Verify action remains
hidden—until the private read-only permission gate is explicitly confirmed.
