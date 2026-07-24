# Portfolio Laboratory

Portfolio Laboratory is an interactive portfolio-risk and allocation dashboard built around free historical market data. Users can edit holdings and weights, select a one-, three-, or five-year lookback, choose a benchmark and risk-free rate, and compare the current allocation with long-only optimization scenarios.

## What it calculates

- Portfolio and holding annualized return from average daily log return
- Annualized volatility from sample daily volatility × √252
- Sharpe ratio using the user-supplied annual risk-free rate
- Maximum peak-to-trough drawdown
- One-day historical value at risk and conditional value at risk at 95%
- Holding and portfolio beta relative to the selected benchmark
- Pairwise Pearson correlations of aligned daily log returns
- Growth of $1 based on aligned daily return history
- Long-only minimum-volatility and maximum-Sharpe scenarios, with a 60% per-holding cap

All return series use only dates present for every holding and the benchmark. Portfolio weights are normalized to 100% for calculations.

## Data source and assumptions

The app retrieves adjusted daily closing prices from Yahoo Finance's public chart endpoint through a server-side route. No API key is required. The dashboard never substitutes synthetic values when the source is unavailable.

Adjusted-close history may be delayed, corrected, incomplete, or unavailable for some symbols. It can differ from executable prices and does not model taxes, fees, slippage, intraday moves, liquidity, corporate-action timing, or currency conversion. Securities with short histories reduce the common analysis window.

Historical covariance and average returns are unstable estimates. Optimization results are scenario outputs, not recommendations, and may change substantially with the lookback, benchmark, risk-free rate, asset set, or constraints.

## Intended use

This is an educational analytics tool, not investment, tax, accounting, or legal advice. Verify source data and methodology with a qualified provider before making financial decisions.
