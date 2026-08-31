from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from threading import RLock
from time import monotonic
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .models import (
    PortfolioDetail,
    PortfolioState,
    PublishedAllocation,
    PublishedAnalytics,
    PublishedClassification,
    PublishedCorrelationMap,
    PublishedDirectionComparison,
    PublishedDirectionLane,
    PublishedPairInsight,
    PublishedPerformancePoint,
    PublishedRebalanceBucket,
    PublishedRiskStatistics,
)

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.^=/-]{1,30}$")
_ELIGIBLE_ANALYTICS_TYPES = {"EQUITY", "ETF", "STOCK"}
_TRADING_DAYS = 252
_MINIMUM_ALIGNED_PRICES = 61
_MAX_PAIR_INSIGHTS = 20

STYLE_PROXIES = {
    "Large Growth": "VUG",
    "Large Blend": "SPY",
    "Large Value": "VTV",
    "Mid Cap": "VO",
    "Small Cap": "VB",
    "International Developed": "VEA",
    "Emerging Markets": "VWO",
    "Fixed Income": "IEF",
    "Real Assets": "GLD",
}
SECTOR_PROXIES = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Diversified Equity": "SPY",
    "Fixed Income": "IEF",
    "Commodities": "GLD",
}
FACTOR_PROXIES = {
    "Market": "SPY",
    "Value": "VLUE",
    "Momentum": "MTUM",
    "Quality": "QUAL",
    "Low Volatility": "USMV",
    "Size": "VB",
    "Duration": "IEF",
    "Inflation": "TIP",
    "Real Assets": "GLD",
}
ALL_PROXY_SYMBOLS = tuple(
    sorted(
        {*STYLE_PROXIES.values(), *SECTOR_PROXIES.values(), *FACTOR_PROXIES.values()}
    )
)


class PublishedAnalyticsError(RuntimeError):
    """Current-holdings analytics could not be built from public market data."""


@dataclass(frozen=True, slots=True)
class MarketSeries:
    symbol: str
    dates: tuple[str, ...]
    prices: tuple[float, ...]
    source: str = "adjusted_close"


class MarketDataClient(Protocol):
    def load_series(self, symbol: str, years: int) -> MarketSeries: ...


@dataclass(slots=True)
class YahooMarketDataClient:
    """Bounded adjusted-close client for the existing Portfolio Lab data source."""

    timeout_seconds: float = 15
    cache_ttl_seconds: int = 3600
    _cache: dict[tuple[str, int], tuple[float, MarketSeries]] = field(
        default_factory=dict, init=False
    )
    _lock: RLock = field(default_factory=RLock, init=False)

    def load_series(self, symbol: str, years: int) -> MarketSeries:
        normalized = symbol.strip().upper()
        if not _SYMBOL_PATTERN.fullmatch(normalized):
            raise PublishedAnalyticsError("A market symbol is invalid.")
        if years not in {1, 3, 5}:
            raise PublishedAnalyticsError("Analytics history must be 1, 3, or 5 years.")
        cache_key = (normalized, years)
        now = monotonic()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None and cached[0] > now:
                return cached[1]

        period_2 = int(datetime.now(UTC).timestamp())
        period_1 = period_2 - years * 366 * 24 * 60 * 60
        query = urlencode(
            {
                "period1": period_1,
                "period2": period_2,
                "interval": "1d",
                "events": "history",
                "includeAdjustedClose": "true",
            }
        )
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{quote(normalized, safe='')}?{query}"
        )
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Portfolio-Laboratory/1.0",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(5_000_001)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise PublishedAnalyticsError(
                f"{normalized}: adjusted-close history is unavailable."
            ) from exc
        if len(body) > 5_000_000:
            raise PublishedAnalyticsError(
                f"{normalized}: adjusted-close response exceeded the size limit."
            )
        try:
            payload = json.loads(body)
            result = payload["chart"]["result"][0]
            timestamps = result.get("timestamp") or []
            indicators = result.get("indicators") or {}
            adjusted_blocks = indicators.get("adjclose") or []
            quote_blocks = indicators.get("quote") or []
            prices = (
                (adjusted_blocks[0].get("adjclose") or [])
                if adjusted_blocks
                else (quote_blocks[0].get("close") or [])
            )
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise PublishedAnalyticsError(
                f"{normalized}: adjusted-close response was invalid."
            ) from exc

        by_date: dict[str, float] = {}
        for index, stamp in enumerate(timestamps):
            if index >= len(prices):
                break
            price = prices[index]
            if not isinstance(stamp, (int, float)) or not isinstance(
                price, (int, float)
            ):
                continue
            numeric_price = float(price)
            if not math.isfinite(numeric_price) or numeric_price <= 0:
                continue
            date = datetime.fromtimestamp(float(stamp), UTC).date().isoformat()
            by_date[date] = numeric_price
        if len(by_date) < 60:
            raise PublishedAnalyticsError(
                f"{normalized}: adjusted-close history is insufficient."
            )
        dates = tuple(sorted(by_date))
        value = MarketSeries(
            symbol=normalized,
            dates=dates,
            prices=tuple(by_date[date] for date in dates),
            source="Yahoo Finance chart API",
        )
        with self._lock:
            self._cache[cache_key] = (now + self.cache_ttl_seconds, value)
        return value


def generate_published_analytics(
    portfolio: PortfolioState,
    benchmark_symbol: str,
    market_data: MarketDataClient,
    *,
    years: int = 3,
    risk_free_rate: float = 0.04,
    rebalance_band_percent: float = 2.5,
    drift_days: int = 63,
    concurrency: int = 6,
) -> PublishedAnalytics:
    """Load public history and calculate a server-owned analytical sleeve.

    Only positive stock/ETF positions enter this model. Private account values
    are used only to derive relative sleeve weights and never enter the result.
    Required holding/benchmark failures abort publication; proxy failures only
    make the affected classifications unavailable.
    """

    values_by_symbol: dict[str, float] = {}
    for position in portfolio.positions:
        symbol = position.symbol.strip().upper()
        if (
            position.instrument_type not in _ELIGIBLE_ANALYTICS_TYPES
            or position.market_value <= 0
            or not _SYMBOL_PATTERN.fullmatch(symbol)
        ):
            continue
        values_by_symbol[symbol] = values_by_symbol.get(symbol, 0.0) + float(
            position.market_value
        )
    if not values_by_symbol:
        return PublishedAnalytics(
            analytics_as_of=portfolio.balance.as_of,
            observation_count=0,
        )

    symbols = tuple(sorted(values_by_symbol))
    weights = _normalize([values_by_symbol[symbol] for symbol in symbols])
    benchmark = benchmark_symbol.strip().upper()
    if not _SYMBOL_PATTERN.fullmatch(benchmark):
        raise PublishedAnalyticsError("The configured benchmark symbol is invalid.")

    required_symbols = tuple(dict.fromkeys((*symbols, benchmark)))
    required, failures = _load_many(
        market_data, required_symbols, years, concurrency=concurrency
    )
    if failures:
        failed = ", ".join(sorted(failures))
        raise PublishedAnalyticsError(
            f"Required adjusted-close history is unavailable for: {failed}."
        )

    optional_symbols = tuple(
        symbol for symbol in ALL_PROXY_SYMBOLS if symbol not in required
    )
    optional, _ = _load_many(
        market_data, optional_symbols, years, concurrency=concurrency
    )
    series = {**required, **optional}
    return calculate_published_analytics(
        symbols,
        weights,
        benchmark,
        series,
        risk_free_rate=risk_free_rate,
        rebalance_band_percent=rebalance_band_percent,
        drift_days=drift_days,
    )


def load_benchmark_series(
    market_data: MarketDataClient,
    benchmark_symbol: str,
    years: int,
) -> MarketSeries | None:
    """Best-effort benchmark history; callers withhold missing exact dates."""

    benchmark = benchmark_symbol.strip().upper()
    if not _SYMBOL_PATTERN.fullmatch(benchmark):
        return None
    try:
        return _validated_series(market_data.load_series(benchmark, years), benchmark)
    except (
        PublishedAnalyticsError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        OSError,
    ):
        return None


def add_benchmark_performance(
    detail: PortfolioDetail,
    benchmark: MarketSeries | None,
) -> PortfolioDetail:
    """Attach adjusted-close benchmark returns only on exact portfolio dates.

    If any published account-performance date lacks a corresponding adjusted
    close, every benchmark value remains unavailable. This avoids silently
    shifting weekend, holiday, or incomplete-history anchors.
    """

    points = detail.card.performance_points
    if benchmark is None or not points:
        return detail
    price_by_date = dict(zip(benchmark.dates, benchmark.prices, strict=True))
    point_dates = tuple(point.at.astimezone(UTC).date().isoformat() for point in points)
    if any(date not in price_by_date for date in point_dates):
        return detail
    starting_price = price_by_date[point_dates[0]]
    if not math.isfinite(starting_price) or starting_price <= 0:
        return detail
    starting_decimal = Decimal(str(starting_price))
    enriched = tuple(
        PublishedPerformancePoint(
            at=point.at,
            return_percent=point.return_percent,
            benchmark_return_percent=(
                Decimal(str(price_by_date[date])) / starting_decimal - Decimal(1)
            )
            * Decimal(100),
            quality=point.quality,
        )
        for point, date in zip(points, point_dates, strict=True)
    )
    return detail.model_copy(
        update={"card": detail.card.model_copy(update={"performance_points": enriched})}
    )


def calculate_published_analytics(
    symbols: Sequence[str],
    weights: Sequence[float],
    benchmark_symbol: str,
    series: dict[str, MarketSeries],
    *,
    risk_free_rate: float = 0.04,
    rebalance_band_percent: float = 2.5,
    drift_days: int = 63,
) -> PublishedAnalytics:
    """Pure current-holdings analysis used by production and numerical tests."""

    normalized_symbols = tuple(symbol.upper() for symbol in symbols)
    if not normalized_symbols or len(set(normalized_symbols)) != len(
        normalized_symbols
    ):
        raise PublishedAnalyticsError("Analytics holdings must be unique and nonempty.")
    if len(normalized_symbols) != len(weights):
        raise PublishedAnalyticsError("Analytics holdings and weights must align.")
    target_weights = _normalize(weights)
    aligned_dates, asset_returns, benchmark_returns = _aligned_returns(
        series, (*normalized_symbols, benchmark_symbol.upper())
    )
    portfolio_returns = tuple(
        sum(
            asset_returns[asset_index][day_index] * target_weights[asset_index]
            for asset_index in range(len(normalized_symbols))
        )
        for day_index in range(len(aligned_dates))
    )

    moments = [_moments(values) for values in asset_returns]
    packed: list[float] = []
    covariance_matrix = [[0.0 for _ in normalized_symbols] for _ in normalized_symbols]
    pairs: list[tuple[int, int, float, float, float]] = []
    for left in range(len(normalized_symbols)):
        for right in range(left, len(normalized_symbols)):
            covariance = _covariance(asset_returns[left], asset_returns[right])
            covariance_matrix[left][right] = covariance
            covariance_matrix[right][left] = covariance
            if left == right:
                correlation = 1.0 if moments[left][1] > 0 else 0.0
            else:
                denominator = math.sqrt(max(0.0, moments[left][1] * moments[right][1]))
                correlation = covariance / denominator if denominator > 0 else 0.0
                correlation = max(-1.0, min(1.0, correlation))
                spread_variance = max(
                    0.0,
                    moments[left][1] + moments[right][1] - 2 * covariance,
                )
                spread_volatility = math.sqrt(spread_variance * _TRADING_DAYS)
                rebalance_potential = spread_volatility * (1 - correlation) / 2
                pairs.append(
                    (
                        left,
                        right,
                        correlation,
                        spread_volatility,
                        rebalance_potential,
                    )
                )
            packed.append(correlation)

    pairs.sort(key=lambda item: (item[2], -item[4], normalized_symbols[item[0]]))
    pair_insights = tuple(
        PublishedPairInsight(
            left_symbol=normalized_symbols[left],
            right_symbol=normalized_symbols[right],
            correlation=correlation,
            spread_volatility_percent=spread_volatility * 100,
            rebalance_potential_percent=rebalance_potential * 100,
        )
        for left, right, correlation, spread_volatility, rebalance_potential in pairs[
            :_MAX_PAIR_INSIGHTS
        ]
    )

    drift_window = min(max(1, drift_days), len(aligned_dates))
    price_implied = _normalize(
        [
            target_weights[index] * math.exp(sum(asset_returns[index][-drift_window:]))
            for index in range(len(normalized_symbols))
        ]
    )
    bucket_indices = _rebalance_bucket_indices(len(normalized_symbols), pairs, packed)
    rebalance_buckets = tuple(
        _published_bucket(
            indices,
            normalized_symbols,
            target_weights,
            price_implied,
            rebalance_band_percent,
        )
        for indices in bucket_indices
    )

    optimized = _optimize_max_sharpe(
        target_weights,
        asset_returns,
        covariance_matrix,
        risk_free_rate,
    )
    optimized_allocation = tuple(
        PublishedAllocation(symbol=symbol, weight_percent=weight * 100)
        for symbol, weight in zip(normalized_symbols, optimized, strict=True)
    )

    classifications = tuple(_classify(symbol, series) for symbol in normalized_symbols)
    lane_indices = sorted(
        range(len(normalized_symbols)),
        key=lambda index: (-target_weights[index], normalized_symbols[index]),
    )[:5]
    direction_lanes = [
        _direction_lane("PORTFOLIO", portfolio_returns),
        *(
            _direction_lane(normalized_symbols[index], asset_returns[index])
            for index in lane_indices
        ),
    ]
    direction_comparison = (
        PublishedDirectionComparison(
            dates=aligned_dates,
            lanes=tuple(direction_lanes),
        )
        if len(direction_lanes) >= 2
        else None
    )
    analytics_as_of = datetime.strptime(aligned_dates[-1], "%Y-%m-%d").replace(
        tzinfo=UTC
    )
    return PublishedAnalytics(
        risk=_risk_statistics(portfolio_returns, risk_free_rate, benchmark_returns),
        classifications=classifications,
        pair_insights=pair_insights,
        optimized_allocation=optimized_allocation,
        correlation_map=(
            PublishedCorrelationMap(
                symbols=normalized_symbols,
                packed_correlations=tuple(packed),
            )
            if len(normalized_symbols) >= 2
            else None
        ),
        direction_comparison=direction_comparison,
        rebalance_buckets=rebalance_buckets,
        analytics_as_of=analytics_as_of,
        observation_count=len(aligned_dates),
    )


def _load_many(
    client: MarketDataClient,
    symbols: Sequence[str],
    years: int,
    *,
    concurrency: int,
) -> tuple[dict[str, MarketSeries], set[str]]:
    if not symbols:
        return {}, set()
    results: dict[str, MarketSeries] = {}
    failures: set[str] = set()
    with ThreadPoolExecutor(max_workers=min(max(1, concurrency), len(symbols))) as pool:
        future_symbols = {
            pool.submit(client.load_series, symbol, years): symbol for symbol in symbols
        }
        for future in as_completed(future_symbols):
            symbol = future_symbols[future]
            try:
                results[symbol] = _validated_series(future.result(), symbol)
            except (
                PublishedAnalyticsError,
                RuntimeError,
                ValueError,
                TypeError,
                KeyError,
                OSError,
            ):
                failures.add(symbol)
    return results, failures


def _validated_series(value: MarketSeries, expected_symbol: str) -> MarketSeries:
    if value.symbol.upper() != expected_symbol or len(value.dates) != len(value.prices):
        raise PublishedAnalyticsError("Market history did not match its symbol.")
    by_date: dict[str, float] = {}
    for date, raw_price in zip(value.dates, value.prices, strict=True):
        try:
            datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError as exc:
            raise PublishedAnalyticsError(
                "Market history included an invalid date."
            ) from exc
        price = float(raw_price)
        if math.isfinite(price) and price > 0:
            by_date[date] = price
    if len(by_date) < 60:
        raise PublishedAnalyticsError("Market history is insufficient.")
    dates = tuple(sorted(by_date))
    return MarketSeries(
        symbol=expected_symbol,
        dates=dates,
        prices=tuple(by_date[date] for date in dates),
        source=value.source,
    )


def _aligned_returns(
    series: dict[str, MarketSeries], symbols: Sequence[str]
) -> tuple[tuple[str, ...], tuple[tuple[float, ...], ...], tuple[float, ...]]:
    try:
        selected = [series[symbol] for symbol in symbols]
    except KeyError as exc:
        raise PublishedAnalyticsError(
            f"{exc.args[0]}: price history is not loaded."
        ) from exc
    price_maps = [dict(zip(item.dates, item.prices, strict=True)) for item in selected]
    common_dates = tuple(
        sorted(set(price_maps[0]).intersection(*(set(item) for item in price_maps[1:])))
    )
    if len(common_dates) < _MINIMUM_ALIGNED_PRICES:
        raise PublishedAnalyticsError(
            "The holdings and benchmark do not share enough aligned history."
        )
    return_dates = common_dates[1:]
    returns = tuple(
        tuple(
            math.log(price_map[current] / price_map[previous])
            for previous, current in pairwise(common_dates)
        )
        for price_map in price_maps
    )
    return return_dates, returns[:-1], returns[-1]


def _normalize(values: Sequence[float]) -> tuple[float, ...]:
    clean = tuple(
        max(0.0, float(value)) if math.isfinite(float(value)) else 0.0
        for value in values
    )
    total = sum(clean)
    if total > 0:
        return tuple(value / total for value in clean)
    return tuple(1 / max(1, len(clean)) for _ in clean)


def _moments(values: Sequence[float]) -> tuple[float, float]:
    mean = 0.0
    m2 = 0.0
    for count, value in enumerate(values, 1):
        delta = value - mean
        mean += delta / count
        m2 += delta * (value - mean)
    variance = m2 / (len(values) - 1) if len(values) > 1 else 0.0
    return mean, max(0.0, variance)


def _covariance(left: Sequence[float], right: Sequence[float]) -> float:
    length = min(len(left), len(right))
    mean_left = 0.0
    mean_right = 0.0
    co_moment = 0.0
    for index in range(length):
        delta_left = left[index] - mean_left
        mean_left += delta_left / (index + 1)
        mean_right += (right[index] - mean_right) / (index + 1)
        co_moment += delta_left * (right[index] - mean_right)
    return co_moment / (length - 1) if length > 1 else 0.0


def _risk_statistics(
    returns: Sequence[float],
    risk_free_rate: float,
    benchmark: Sequence[float],
) -> PublishedRiskStatistics:
    mean, variance = _moments(returns)
    annual_return = math.exp(mean * _TRADING_DAYS) - 1
    volatility = math.sqrt(variance * _TRADING_DAYS)
    ordered = sorted(returns)
    cut = max(0, math.floor(len(ordered) * 0.05) - 1)
    tail = ordered[: cut + 1]
    wealth = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    for value in returns:
        wealth *= math.exp(value)
        peak = max(peak, wealth)
        maximum_drawdown = min(maximum_drawdown, wealth / peak - 1)
    benchmark_variance = _moments(benchmark)[1]
    beta = (
        _covariance(returns, benchmark) / benchmark_variance
        if benchmark_variance > 1e-16
        else None
    )
    return PublishedRiskStatistics(
        annual_return_percent=annual_return * 100,
        annual_volatility_percent=volatility * 100,
        sharpe_ratio=(annual_return - risk_free_rate) / volatility
        if volatility > 0
        else None,
        maximum_drawdown_percent=maximum_drawdown * 100,
        value_at_risk_95_percent=-ordered[cut] * 100,
        conditional_value_at_risk_95_percent=-_moments(tail)[0] * 100,
        beta=beta,
    )


def _packed_index(size: int, row: int, column: int) -> int:
    left = min(row, column)
    right = max(row, column)
    return left * size - left * (left - 1) // 2 + (right - left)


def _rebalance_bucket_indices(
    size: int,
    pairs: Sequence[tuple[int, int, float, float, float]],
    packed: Sequence[float],
) -> tuple[tuple[int, ...], ...]:
    remaining = set(range(size))
    buckets: list[tuple[int, ...]] = []
    for left, right, *_ in pairs:
        if left not in remaining or right not in remaining:
            continue
        buckets.append((left, right))
        remaining.remove(left)
        remaining.remove(right)
    if len(remaining) == 1:
        final = remaining.pop()
        if buckets:
            best_index = min(
                range(len(buckets)),
                key=lambda bucket_index: (
                    sum(
                        packed[_packed_index(size, final, other)]
                        for other in buckets[bucket_index]
                    )
                    / len(buckets[bucket_index])
                ),
            )
            buckets[best_index] = (*buckets[best_index], final)
        else:
            buckets.append((final,))
    return tuple(buckets)


def _published_bucket(
    indices: Sequence[int],
    symbols: Sequence[str],
    target_weights: Sequence[float],
    price_implied_weights: Sequence[float],
    rebalance_band_percent: float,
) -> PublishedRebalanceBucket:
    target_mix = _normalize([target_weights[index] for index in indices])
    implied_mix = _normalize([price_implied_weights[index] for index in indices])
    drift = max(
        abs(implied_mix[index] - target_mix[index]) for index in range(len(indices))
    )
    return PublishedRebalanceBucket(
        symbols=tuple(symbols[index] for index in indices),
        target_weights_percent=tuple(value * 100 for value in target_mix),
        price_implied_weights_percent=tuple(value * 100 for value in implied_mix),
        drift_percent=drift * 100,
        triggered=drift * 100 >= rebalance_band_percent,
    )


def _return_series(value: MarketSeries) -> tuple[tuple[str, ...], tuple[float, ...]]:
    return (
        value.dates[1:],
        tuple(
            math.log(current / previous) for previous, current in pairwise(value.prices)
        ),
    )


def _paired_correlation(left: MarketSeries, right: MarketSeries) -> float | None:
    left_dates, left_values = _return_series(left)
    right_dates, right_values = _return_series(right)
    right_map = dict(zip(right_dates, right_values, strict=True))
    aligned_left: list[float] = []
    aligned_right: list[float] = []
    for date, value in zip(left_dates, left_values, strict=True):
        if date in right_map:
            aligned_left.append(value)
            aligned_right.append(right_map[date])
    if len(aligned_left) < 60:
        return None
    left_variance = _moments(aligned_left)[1]
    right_variance = _moments(aligned_right)[1]
    denominator = math.sqrt(left_variance * right_variance)
    if denominator <= 0:
        return None
    return max(
        -1.0,
        min(1.0, _covariance(aligned_left, aligned_right) / denominator),
    )


def _rank_category(
    symbol: str,
    proxies: dict[str, str],
    series: dict[str, MarketSeries],
) -> tuple[str | None, str]:
    asset = series.get(symbol)
    if asset is None:
        return None, "unavailable"
    ranked: list[tuple[float, str]] = []
    for label, proxy_symbol in proxies.items():
        proxy = series.get(proxy_symbol)
        if proxy is None:
            continue
        correlation = _paired_correlation(asset, proxy)
        if correlation is not None and math.isfinite(correlation):
            ranked.append((correlation, label))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if not ranked:
        return None, "unavailable"
    best_correlation, label = ranked[0]
    margin = best_correlation - ranked[1][0] if len(ranked) > 1 else 1.0
    if best_correlation >= 0.75 and margin >= 0.10:
        confidence = "high"
    elif best_correlation >= 0.55 and margin >= 0.05:
        confidence = "moderate"
    else:
        confidence = "low"
    return label, confidence


def _classify(symbol: str, series: dict[str, MarketSeries]) -> PublishedClassification:
    style, style_confidence = _rank_category(symbol, STYLE_PROXIES, series)
    sector, sector_confidence = _rank_category(symbol, SECTOR_PROXIES, series)
    factor, factor_confidence = _rank_category(symbol, FACTOR_PROXIES, series)
    levels = {"unavailable": 0, "low": 1, "moderate": 2, "high": 3}
    confidence = min(
        (style_confidence, sector_confidence, factor_confidence),
        key=levels.__getitem__,
    )
    return PublishedClassification(
        symbol=symbol,
        style=style,
        sector=sector,
        factor=factor,
        confidence=confidence,  # type: ignore[arg-type]
    )


def _direction_lane(symbol: str, values: Sequence[float]) -> PublishedDirectionLane:
    directions = tuple(1 if value > 0 else -1 if value < 0 else 0 for value in values)
    return PublishedDirectionLane(
        symbol=symbol,
        up_share_percent=sum(value > 0 for value in values) / len(values) * 100,
        directions=directions,
    )


def _project_capped_simplex(
    values: Sequence[float], cap: float = 0.6
) -> tuple[float, ...]:
    if not values:
        return ()
    if len(values) * cap < 1:
        return tuple(1 / len(values) for _ in values)
    low = min(value - cap for value in values)
    high = max(values)
    for _ in range(70):
        threshold = (low + high) / 2
        total = sum(min(cap, max(0.0, value - threshold)) for value in values)
        if total > 1:
            low = threshold
        else:
            high = threshold
    threshold = (low + high) / 2
    projected = tuple(min(cap, max(0.0, value - threshold)) for value in values)
    total = sum(projected)
    return tuple(value / total for value in projected)


def _optimize_max_sharpe(
    target_weights: Sequence[float],
    asset_returns: Sequence[Sequence[float]],
    covariance_matrix: Sequence[Sequence[float]],
    risk_free_rate: float,
) -> tuple[float, ...]:
    if len(target_weights) < 2:
        return tuple(target_weights)
    means = tuple(_moments(values)[0] for values in asset_returns)
    weights = _project_capped_simplex(target_weights)

    def evaluate(candidate: Sequence[float]) -> tuple[float, tuple[float, ...]]:
        mean_daily = sum(
            candidate[index] * means[index] for index in range(len(candidate))
        )
        sigma_weights = tuple(
            sum(row[index] * candidate[index] for index in range(len(candidate)))
            for row in covariance_matrix
        )
        variance = max(
            1e-16,
            sum(
                candidate[index] * sigma_weights[index]
                for index in range(len(candidate))
            ),
        )
        volatility = math.sqrt(variance * _TRADING_DAYS)
        annual_return = math.exp(mean_daily * _TRADING_DAYS) - 1
        excess = annual_return - risk_free_rate
        return_scale = math.exp(mean_daily * _TRADING_DAYS) * _TRADING_DAYS
        gradient = tuple(
            return_scale * mean / volatility
            - excess * _TRADING_DAYS * sigma_weights[index] / (volatility**3)
            for index, mean in enumerate(means)
        )
        return excess / volatility, gradient

    for _ in range(500):
        current_score, gradient = evaluate(weights)
        gradient_scale = max(max(map(abs, gradient)), 1e-12)
        direction = tuple(value / gradient_scale for value in gradient)
        step = 0.04
        candidate = weights
        accepted = False
        for _ in range(18):
            candidate = _project_capped_simplex(
                [
                    weights[index] + step * direction[index]
                    for index in range(len(weights))
                ]
            )
            if evaluate(candidate)[0] >= current_score - 1e-12:
                accepted = True
                break
            step /= 2
        if not accepted:
            break
        movement = max(
            abs(candidate[index] - weights[index]) for index in range(len(weights))
        )
        weights = candidate
        if movement < 1e-8:
            break
    return weights


def analytics_json(value: PublishedAnalytics | None) -> dict[str, Any] | None:
    """Canonical JSONB value used for idempotence and repository persistence."""

    return (
        value.model_dump(mode="json", by_alias=True, exclude_none=False)
        if value is not None
        else None
    )
