from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from webull_service.adapters import FakeWebullAdapter
from webull_service.analytics import (
    MarketSeries,
    PublishedAnalyticsError,
    add_benchmark_performance,
    calculate_published_analytics,
)
from webull_service.config import Settings
from webull_service.main import create_app
from webull_service.models import (
    PortfolioCard,
    PortfolioDetail,
    PublicationQuality,
    PublishedPerformancePoint,
)
from webull_service.repository import MemoryRepository


def _series(
    symbol: str,
    returns: list[float],
    *,
    start: datetime | None = None,
) -> MarketSeries:
    start = start or datetime(2026, 1, 1, tzinfo=UTC)
    dates = [
        (start + timedelta(days=index)).date().isoformat()
        for index in range(len(returns) + 1)
    ]
    prices = [100.0]
    for value in returns:
        prices.append(prices[-1] * math.exp(value))
    return MarketSeries(symbol=symbol, dates=tuple(dates), prices=tuple(prices))


def _fixture_series() -> dict[str, MarketSeries]:
    first = [0.012 if index % 4 in {0, 1} else -0.008 for index in range(100)]
    second = [value * 0.8 for value in first]
    inverse = [-value for value in first]
    benchmark = [
        value * 0.6 + (0.001 if index % 3 == 0 else -0.0005)
        for index, value in enumerate(first)
    ]
    return {
        "AAA": _series("AAA", first),
        "BBB": _series("BBB", second),
        "CCC": _series("CCC", inverse),
        "SPY": _series("SPY", benchmark),
        "VUG": _series("VUG", first),
        "XLK": _series("XLK", first),
        "MTUM": _series("MTUM", first),
    }


def test_published_analytics_match_manual_math_and_public_contract() -> None:
    result = calculate_published_analytics(
        ("AAA", "BBB", "CCC"),
        (0.5, 0.3, 0.2),
        "SPY",
        _fixture_series(),
    )

    assert result.observation_count == 100
    assert result.risk is not None
    assert result.risk.annual_volatility_percent is not None
    assert result.risk.annual_volatility_percent > 0
    assert result.risk.maximum_drawdown_percent is not None
    assert result.risk.maximum_drawdown_percent <= 0
    assert result.correlation_map is not None
    assert len(result.correlation_map.packed_correlations) == 6
    assert result.correlation_map.packed_correlations[2] == pytest.approx(-1)
    assert result.pair_insights[0].left_symbol == "AAA"
    assert result.pair_insights[0].right_symbol == "CCC"
    assert sum(
        item.weight_percent for item in result.optimized_allocation
    ) == pytest.approx(100)
    assert max(item.weight_percent for item in result.optimized_allocation) <= 60.000001
    assert all(item.weight_percent >= 0 for item in result.optimized_allocation)
    assert result.direction_comparison is not None
    assert len(result.direction_comparison.lanes) == 4
    assert result.direction_comparison.lanes[0].symbol == "PORTFOLIO"
    assert all(
        direction in {-1, 0, 1}
        for lane in result.direction_comparison.lanes
        for direction in lane.directions
    )
    assert result.classifications[0].style == "Large Growth"
    assert result.classifications[0].sector == "Technology"
    assert result.classifications[0].factor == "Momentum"
    # The best proxies are exact matches, but the SPY runner-up is close enough
    # that the intentionally conservative aggregate confidence remains low.
    assert result.classifications[0].confidence == "low"
    assert result.rebalance_buckets

    public = result.model_dump(mode="json", by_alias=True)
    serialized = str(public).lower()
    for forbidden in (
        "accountid",
        "quantity",
        "marketvalue",
        "netliquidation",
        "cashflow",
        "portfoliovalue",
        "totalcostbasis",
        "profitlossamount",
    ):
        assert forbidden not in serialized


def test_constant_series_stays_finite_and_insufficient_alignment_fails() -> None:
    fixture = _fixture_series()
    fixture["FLAT"] = _series("FLAT", [0.0] * 100)
    result = calculate_published_analytics(("AAA", "FLAT"), (0.5, 0.5), "SPY", fixture)

    assert result.correlation_map is not None
    assert result.correlation_map.packed_correlations == pytest.approx((1, 0, 0))
    assert all(
        math.isfinite(value) for value in result.correlation_map.packed_correlations
    )

    fixture["SPY"] = _series("SPY", [0.001] * 30)
    with pytest.raises(PublishedAnalyticsError, match="enough aligned history"):
        calculate_published_analytics(("AAA", "FLAT"), (0.5, 0.5), "SPY", fixture)


def test_benchmark_returns_require_exact_published_dates() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=2)
    detail = PortfolioDetail(
        card=PortfolioCard(
            slug="benchmark-book",
            title="Benchmark Book",
            provider="webull",
            ytd_return_percent=Decimal(5),
            performance_through=end,
            quality=PublicationQuality.PORTFOLIO_LAB_COMPUTED,
            performance_points=(
                PublishedPerformancePoint(
                    at=start,
                    return_percent=Decimal(0),
                    quality=PublicationQuality.PORTFOLIO_LAB_COMPUTED,
                ),
                PublishedPerformancePoint(
                    at=end,
                    return_percent=Decimal(5),
                    quality=PublicationQuality.PORTFOLIO_LAB_COMPUTED,
                ),
            ),
        ),
        holdings_as_of=end,
        holdings=(),
        gross_exposure_percent=None,
        net_exposure_percent=None,
        analytical_sleeve_percent=None,
    )
    exact = MarketSeries(
        symbol="SPY",
        dates=("2026-01-01", "2026-01-02", "2026-01-03"),
        prices=(100.0, 105.0, 110.0),
    )
    enriched = add_benchmark_performance(detail, exact)
    assert enriched.card.performance_points[0].benchmark_return_percent == 0
    assert enriched.card.performance_points[1].benchmark_return_percent == Decimal(10)

    missing_anchor = MarketSeries(
        symbol="SPY",
        dates=("2026-01-01", "2026-01-02"),
        prices=(100.0, 105.0),
    )
    withheld = add_benchmark_performance(detail, missing_anchor)
    assert all(
        point.benchmark_return_percent is None
        for point in withheld.card.performance_points
    )


class _FakeMarketData:
    def __init__(self) -> None:
        base = [0.006 if index % 3 else -0.004 for index in range(90)]
        benchmark = [0.004 if index % 3 else -0.003 for index in range(90)]
        start = datetime(2026, 7, 1, tzinfo=UTC)
        self.values = {
            "AAPL": _series("AAPL", base, start=start),
            "SPY": _series("SPY", benchmark, start=start),
        }
        self.fail_required = False
        self.calls: list[str] = []

    def load_series(self, symbol: str, years: int) -> MarketSeries:
        assert years == 3
        self.calls.append(symbol)
        if self.fail_required and symbol == "AAPL":
            raise PublishedAnalyticsError("unavailable")
        try:
            return self.values[symbol]
        except KeyError:
            raise PublishedAnalyticsError("optional proxy unavailable") from None


def test_preview_is_non_committing_and_failed_rebuild_preserves_last_good() -> None:
    settings = Settings(
        database_url="",
        internal_api_token="token",
        portfolio_owner_github_id="123",
        webull_app_key="",
        webull_app_secret="",
        adapter_kind="fake",
        auto_migrate=False,
        portfolio_publication_enabled=True,
        auto_publish_after_close_enabled=True,
        published_analytics_enabled=True,
    )
    repository = MemoryRepository()
    market_data = _FakeMarketData()
    adapter = FakeWebullAdapter()
    client = TestClient(
        create_app(
            settings=settings,
            adapter=adapter,
            market_data_client=market_data,
            repository=repository,
            publication_clock=lambda: adapter.as_of + timedelta(hours=2),
        )
    )
    headers = {
        "Authorization": "Bearer token",
        "x-portfolio-owner-github-id": "123",
    }
    assert client.post("/v1/connect", headers=headers).status_code == 200
    adapter.advance(days=1)
    assert client.post("/v1/sync", headers=headers, json={}).status_code == 200
    accounts = client.get("/v1/providers/webull/accounts", headers=headers).json()
    configured = client.put(
        "/v1/publications/configure",
        headers=headers,
        json={
            "accountHandle": accounts["accounts"][0]["accountHandle"],
            "slug": "analytics-book",
            "title": "Analytics Book",
            "enabled": True,
        },
    ).json()
    publication_id = configured["publicationId"]

    preview = client.get(f"/v1/publications/{publication_id}/preview", headers=headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["analytics"]["risk"] is not None
    points = preview.json()["card"]["performancePoints"]
    assert len(points) == 2
    assert float(points[0]["benchmarkReturnPercent"]) == 0
    assert points[1]["benchmarkReturnPercent"] is not None
    assert client.get("/v1/publications", headers=headers).json() == {"portfolios": []}

    injected = client.post(
        f"/v1/publications/{publication_id}/publish",
        headers=headers,
        json={"analytics": {"accountBalance": 100}},
    )
    assert injected.status_code == 422
    published = client.post(
        f"/v1/publications/{publication_id}/publish", headers=headers, json={}
    )
    assert published.status_code == 200, published.text
    analytics_calls = market_data.calls.count("AAPL")

    adapter.advance(days=3)
    assert client.post("/v1/sync", headers=headers, json={}).status_code == 200
    scheduled = client.post("/v1/publications/scheduled-publish", headers=headers)
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()[0]["analytics"]["risk"] is not None
    assert market_data.calls.count("AAPL") > analytics_calls
    regenerated = client.get("/v1/publications/analytics-book", headers=headers).json()

    # A rollout can temporarily disable recalculation without erasing the last
    # stored analytical sleeve on the next after-close revision.
    adapter.advance(days=1)
    preserve_client = TestClient(
        create_app(
            settings=replace(settings, published_analytics_enabled=False),
            adapter=adapter,
            market_data_client=market_data,
            repository=repository,
            publication_clock=lambda: adapter.as_of + timedelta(hours=2),
        )
    )
    assert preserve_client.post("/v1/sync", headers=headers, json={}).status_code == 200
    aapl_calls = market_data.calls.count("AAPL")
    preserved = preserve_client.post(
        "/v1/publications/scheduled-publish", headers=headers
    )
    assert preserved.status_code == 200, preserved.text
    assert preserved.json()[0]["analytics"] == regenerated["analytics"]
    assert market_data.calls.count("AAPL") == aapl_calls
    before = preserve_client.get(
        "/v1/publications/analytics-book", headers=headers
    ).json()

    market_data.fail_required = True
    failed = client.post(
        f"/v1/publications/{publication_id}/publish", headers=headers, json={}
    )
    assert failed.status_code == 503
    after = client.get("/v1/publications/analytics-book", headers=headers).json()
    assert after == before
