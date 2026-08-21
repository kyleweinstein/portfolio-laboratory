from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from webull_service.models import CashActivity, ValuationPoint
from webull_service.performance import (
    calculate_performance,
    modified_dietz_return,
    time_weighted_return,
    xirr,
)


def external_flow(at: datetime, amount: str) -> CashActivity:
    return CashActivity(
        account_id="account",
        external_activity_id=f"flow-{at.timestamp()}-{amount}",
        activity_type="DEPOSIT" if Decimal(amount) >= 0 else "WITHDRAWAL",
        occurred_at=at,
        amount=Decimal(amount),
        currency="USD",
        is_external_flow=True,
    )


def test_modified_dietz_weights_intraday_external_flow() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=1)
    result = modified_dietz_return(
        Decimal(100),
        Decimal(165),
        [external_flow(start + timedelta(hours=12), "50")],
        period_start=start,
        period_end=end,
    )
    assert result == pytest.approx(0.12)


def test_twr_compounds_daily_modified_dietz_periods() -> None:
    assert time_weighted_return([0.10, -0.05]) == pytest.approx(0.045)
    assert time_weighted_return([0.10, None]) is None


def test_xirr_solves_irregular_investor_cash_flows() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    result = xirr(
        [(start, Decimal(-1000)), (start + timedelta(days=365), Decimal(1100))]
    )
    assert result == pytest.approx(0.10, abs=0.001)
    assert (
        xirr([(start, Decimal(100)), (start + timedelta(days=1), Decimal(10))]) is None
    )


def test_performance_report_keeps_dividends_out_of_external_flows() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    deposit = external_flow(start + timedelta(hours=12), "50")
    dividend = CashActivity(
        account_id="account",
        external_activity_id="dividend",
        activity_type="DIVIDEND",
        occurred_at=start + timedelta(hours=18),
        amount=Decimal(5),
        currency="USD",
        is_external_flow=False,
    )
    report = calculate_performance(
        "account",
        [
            ValuationPoint(at=start, value=Decimal(100)),
            ValuationPoint(at=start + timedelta(days=1), value=Decimal(165)),
        ],
        [deposit, dividend],
    )
    assert report.net_external_flow == Decimal(50)
    assert report.periods[0].modified_dietz_return == pytest.approx(0.12)
    assert report.time_weighted_return == pytest.approx(0.12)
    assert report.money_weighted_return is not None


def test_statement_anchor_keeps_backfilled_performance_estimated() -> None:
    start = datetime(2026, 1, 1, 20, tzinfo=UTC)
    report = calculate_performance(
        "account",
        [
            ValuationPoint(at=start, value=Decimal(100), source="statement_anchor"),
            ValuationPoint(at=start + timedelta(days=1), value=Decimal(101)),
        ],
        [],
    )
    assert report.quality == "estimated"
