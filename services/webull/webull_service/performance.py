from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from math import exp, inf, isfinite, log1p

from .models import CashActivity, PerformancePeriod, PerformanceReport, ValuationPoint


def modified_dietz_return(
    beginning_value: Decimal,
    ending_value: Decimal,
    flows: Iterable[CashActivity],
    *,
    period_start: datetime,
    period_end: datetime,
) -> float | None:
    """Return for a period using time-weighted external cash flows.

    Cash activity amounts use the account perspective: deposits are positive and
    withdrawals are negative. Only activities marked as external flows belong in
    this calculation; dividends and interest remain investment performance.
    """

    duration = (period_end - period_start).total_seconds()
    if duration <= 0:
        raise ValueError("The Modified Dietz period must have positive duration.")

    net_flow = Decimal(0)
    weighted_flow = Decimal(0)
    for flow in flows:
        if not flow.is_external_flow or not (
            period_start < flow.occurred_at <= period_end
        ):
            continue
        remaining = max(
            0.0, min(1.0, (period_end - flow.occurred_at).total_seconds() / duration)
        )
        net_flow += flow.amount
        weighted_flow += flow.amount * Decimal(str(remaining))

    denominator = beginning_value + weighted_flow
    if denominator == 0:
        return None
    return float((ending_value - beginning_value - net_flow) / denominator)


def time_weighted_return(period_returns: Iterable[float | None]) -> float | None:
    values = list(period_returns)
    if not values or any(value is None or not isfinite(value) for value in values):
        return None
    compounded = 1.0
    for value in values:
        compounded *= 1.0 + float(value)
    return compounded - 1.0


def xnpv(rate: float, cash_flows: Iterable[tuple[datetime, Decimal]]) -> float:
    if rate <= -1:
        raise ValueError("XNPV requires a rate greater than -100%.")
    flows = sorted(cash_flows, key=lambda item: item[0])
    if not flows:
        raise ValueError("XNPV requires at least one cash flow.")
    origin = flows[0][0]
    total = 0.0
    for at, amount in flows:
        years = ((at - origin).total_seconds()) / 31_557_600.0
        exponent = log1p(rate) * years
        if exponent > 709:
            discounted = 0.0
        elif exponent < -709:
            discounted = inf if amount > 0 else -inf
        else:
            discounted = float(amount) / exp(exponent)
        total += discounted
    return total


def xirr(
    cash_flows: Iterable[tuple[datetime, Decimal]], *, tolerance: float = 1e-10
) -> float | None:
    """Annualized irregular IRR for investor-perspective signed cash flows."""

    combined: dict[datetime, Decimal] = {}
    for at, amount in cash_flows:
        combined[at] = combined.get(at, Decimal(0)) + amount
    flows = sorted((at, amount) for at, amount in combined.items() if amount != 0)
    if (
        len(flows) < 2
        or not any(amount < 0 for _, amount in flows)
        or not any(amount > 0 for _, amount in flows)
    ):
        return None

    grid = [
        -0.999999,
        -0.99,
        -0.9,
        -0.75,
        -0.5,
        -0.25,
        0.0,
        0.1,
        0.25,
        0.5,
        1.0,
        2.0,
        5.0,
        10.0,
        25.0,
        100.0,
        1_000.0,
        1_000_000.0,
        1_000_000_000_000.0,
        1.0e24,
        1.0e48,
        1.0e96,
    ]
    previous_rate = grid[0]
    previous_value = xnpv(previous_rate, flows)
    if abs(previous_value) <= tolerance:
        return previous_rate
    bracket: tuple[float, float] | None = None
    for rate in grid[1:]:
        value = xnpv(rate, flows)
        if abs(value) <= tolerance:
            return rate
        if (previous_value < 0 < value) or (value < 0 < previous_value):
            bracket = (previous_rate, rate)
            break
        previous_rate, previous_value = rate, value
    if bracket is None:
        return None

    low, high = bracket
    low_value = xnpv(low, flows)
    for _ in range(240):
        middle = (low + high) / 2.0
        value = xnpv(middle, flows)
        if abs(value) <= tolerance or high - low <= tolerance:
            return middle
        if (low_value < 0 < value) or (value < 0 < low_value):
            high = middle
        else:
            low, low_value = middle, value
    return (low + high) / 2.0


def calculate_performance(
    account_id: str,
    valuations: Iterable[ValuationPoint],
    activities: Iterable[CashActivity],
) -> PerformanceReport:
    points_by_time = {point.at: point for point in valuations}
    points = sorted(points_by_time.values(), key=lambda point: point.at)
    all_activities = sorted(activities, key=lambda item: item.occurred_at)
    if not points:
        return PerformanceReport(
            account_id=account_id,
            start=None,
            end=None,
            periods=(),
            time_weighted_return=None,
            money_weighted_return=None,
            net_external_flow=Decimal(0),
            beginning_value=None,
            ending_value=None,
            quality="unavailable",
        )

    periods: list[PerformancePeriod] = []
    for beginning, ending in pairwise(points):
        period_flows = [
            flow
            for flow in all_activities
            if flow.is_external_flow and beginning.at < flow.occurred_at <= ending.at
        ]
        net_flow = sum((flow.amount for flow in period_flows), Decimal(0))
        periods.append(
            PerformancePeriod(
                start=beginning.at,
                end=ending.at,
                beginning_value=beginning.value,
                ending_value=ending.value,
                net_external_flow=net_flow,
                modified_dietz_return=modified_dietz_return(
                    beginning.value,
                    ending.value,
                    period_flows,
                    period_start=beginning.at,
                    period_end=ending.at,
                ),
            )
        )

    first, last = points[0], points[-1]
    external = [
        flow
        for flow in all_activities
        if flow.is_external_flow and first.at < flow.occurred_at <= last.at
    ]
    investor_flows: list[tuple[datetime, Decimal]] = [(first.at, -first.value)]
    investor_flows.extend((flow.occurred_at, -flow.amount) for flow in external)
    if last.at > first.at:
        investor_flows.append((last.at, last.value))

    return PerformanceReport(
        account_id=account_id,
        start=first.at,
        end=last.at,
        periods=tuple(periods),
        time_weighted_return=time_weighted_return(
            period.modified_dietz_return for period in periods
        ),
        money_weighted_return=xirr(investor_flows) if last.at > first.at else None,
        net_external_flow=sum((flow.amount for flow in external), Decimal(0)),
        beginning_value=first.value,
        ending_value=last.value,
        quality=(
            "verified"
            if periods and all(point.source == "webull_snapshot" for point in points)
            else "estimated"
            if periods
            else "unavailable"
        ),
    )
