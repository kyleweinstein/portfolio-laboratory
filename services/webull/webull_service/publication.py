from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from math import isfinite
from typing import Any

from .models import (
    CashSource,
    PerformanceReport,
    PortfolioCard,
    PortfolioDetail,
    PortfolioState,
    PublicationConfig,
    PublicationQuality,
    PublishedAnalytics,
    PublishedHolding,
    PublishedHoldingKind,
    PublishedPerformancePoint,
)

_ONE_HUNDRED = Decimal(100)
_ELIGIBLE_ANALYTICS_TYPES = {"EQUITY", "ETF", "STOCK"}
_TITLE_CURRENCY_RE = re.compile(r"[$\u20ac\u00a3\u00a5]")
_TITLE_LONG_NUMBER_RE = re.compile(r"\d{4,}")
_TITLE_GROUPED_NUMBER_RE = re.compile(r"\d{1,3}(?:[ ,]\d{3})+(?:\.\d+)?")
_TITLE_MASKED_IDENTIFIER_RE = re.compile(r"(?:[\u2022*xX#]{2,})\s*[A-Za-z0-9]{3,}")
_TITLE_VALUE_WORD_RE = re.compile(
    r"\b(?:account|acct|balance|cash|equity|nav|nlv|value|profit|loss|p&l)\b",
    re.IGNORECASE,
)
_FORBIDDEN_ANALYTICS_KEYS = {
    "account",
    "accountid",
    "accountnumber",
    "accountsuffix",
    "amount",
    "balance",
    "buyingpower",
    "cash",
    "cashflow",
    "contribution",
    "costbasis",
    "dollar",
    "equity",
    "marketvalue",
    "netliquidation",
    "portfoliovalue",
    "currentvalue",
    "currentprice",
    "lastprice",
    "price",
    "profitlossamount",
    "quantity",
    "shares",
    "totalcostbasis",
}


class PublicationValidationError(ValueError):
    """A private or unreconciled payload cannot enter the public projection."""


def validate_publication_title(
    title: str, account_identifier: str | None = None
) -> str:
    """Reject owner labels that could disclose an account suffix or dollar value."""

    normalized = " ".join(title.split())
    if not normalized or len(normalized) > 120:
        raise PublicationValidationError("Publication title is invalid.")
    if (
        _TITLE_CURRENCY_RE.search(normalized)
        or _TITLE_LONG_NUMBER_RE.search(normalized)
        or _TITLE_GROUPED_NUMBER_RE.search(normalized)
        or _TITLE_MASKED_IDENTIFIER_RE.search(normalized)
        or (_TITLE_VALUE_WORD_RE.search(normalized) and re.search(r"\d", normalized))
    ):
        raise PublicationValidationError(
            "Publication title may not contain account or value identifiers."
        )

    if account_identifier:
        title_token = "".join(
            character.lower() for character in normalized if character.isalnum()
        )
        private_identifier = account_identifier.rsplit(":", 1)[-1]
        account_token = "".join(
            character.lower() for character in private_identifier if character.isalnum()
        )
        private_tokens = {
            match.group(0) for match in re.finditer(r"\d{4,}", private_identifier)
        }
        private_tokens.update(token[-4:] for token in tuple(private_tokens))
        if len(account_token) >= 8:
            private_tokens.add(account_token)
            if any(character.isdigit() for character in account_token):
                private_tokens.add(account_token[-4:])
        if any(token and token in title_token for token in private_tokens):
            raise PublicationValidationError(
                "Publication title may not contain account or value identifiers."
            )
    return normalized


def validate_public_analytics(
    value: PublishedAnalytics | dict[str, Any] | None,
) -> None:
    normalized = (
        value.model_dump(mode="json", by_alias=True)
        if isinstance(value, PublishedAnalytics)
        else value
    )
    _validate_analytics(normalized)


def build_publication_detail(
    config: PublicationConfig,
    portfolio: PortfolioState,
    performance: PerformanceReport | None,
    *,
    analytics: PublishedAnalytics | dict[str, Any] | None = None,
    tolerance_bps: int = 5,
) -> PortfolioDetail:
    """Build the only payload permitted to cross the follower boundary.

    Private values are used transiently to derive percentages. The resulting
    Pydantic model contains no quantities, account identifiers, balances,
    position values, or dollar P&L fields.
    """

    if config.account_id != portfolio.account.account_id:
        raise PublicationValidationError("Publication account does not match snapshot.")
    if config.provider is not portfolio.account.provider:
        raise PublicationValidationError(
            "Publication provider does not match snapshot."
        )
    validate_publication_title(config.title, config.account_id)
    validate_public_analytics(analytics)

    balance = portfolio.balance
    if any(position.currency != balance.currency for position in portfolio.positions):
        raise PublicationValidationError(
            "All published holdings must share the account snapshot currency."
        )

    equity = balance.equity
    weights_available = equity > 0
    holdings: list[PublishedHolding] = []
    security_value = sum(
        (position.market_value for position in portfolio.positions), Decimal(0)
    )
    eligible_value = sum(
        (
            position.market_value
            for position in portfolio.positions
            if position.instrument_type in _ELIGIBLE_ANALYTICS_TYPES
            and position.market_value > 0
        ),
        Decimal(0),
    )

    for position in sorted(portfolio.positions, key=lambda item: item.symbol):
        average_cost = position.average_cost
        holding_return = (
            (position.last_price - average_cost) / average_cost * _ONE_HUNDRED
            if average_cost is not None and average_cost > 0
            else None
        )
        holdings.append(
            PublishedHolding(
                kind=PublishedHoldingKind.SECURITY,
                symbol=position.symbol,
                name=position.symbol,
                weight_percent=(
                    position.market_value / equity * _ONE_HUNDRED
                    if weights_available
                    else None
                ),
                cost_basis_per_share=average_cost,
                return_percent=holding_return,
                quality=PublicationQuality.BROKER_REPORTED,
            )
        )

    cash_quality = (
        PublicationQuality.BROKER_REPORTED
        if balance.cash_source is CashSource.EXPLICIT
        else PublicationQuality.ESTIMATED
        if balance.cash_source is CashSource.DERIVED_RESIDUAL
        else PublicationQuality.UNAVAILABLE
    )
    holdings.append(
        PublishedHolding(
            kind=PublishedHoldingKind.CASH_MARGIN,
            symbol="USD",
            name="Cash / Margin",
            weight_percent=(
                balance.cash / equity * _ONE_HUNDRED if weights_available else None
            ),
            cost_basis_per_share=None,
            return_percent=None,
            quality=cash_quality,
        )
    )

    residual = equity - security_value - balance.cash
    tolerance = abs(equity) * Decimal(max(0, tolerance_bps)) / Decimal(10000)
    if abs(residual) > tolerance:
        holdings.append(
            PublishedHolding(
                kind=PublishedHoldingKind.OTHER,
                symbol=None,
                name="Other assets / liabilities",
                weight_percent=(
                    residual / equity * _ONE_HUNDRED if weights_available else None
                ),
                cost_basis_per_share=None,
                return_percent=None,
                quality=PublicationQuality.ESTIMATED,
            )
        )

    if weights_available:
        total_weight = sum(
            (holding.weight_percent or Decimal(0) for holding in holdings),
            Decimal(0),
        )
        allowed_error = Decimal(max(0, tolerance_bps)) / Decimal(100)
        if abs(total_weight - _ONE_HUNDRED) > allowed_error:
            raise PublicationValidationError(
                "Signed holding weights do not reconcile to 100%."
            )

    quality = _performance_quality(performance)
    points = _performance_points(performance, quality)
    ytd = _ytd_return_percent(performance)
    gross_exposure = (
        sum((abs(item.market_value) for item in portfolio.positions), Decimal(0))
        / equity
        * _ONE_HUNDRED
        if weights_available
        else None
    )
    net_exposure = security_value / equity * _ONE_HUNDRED if weights_available else None
    analytical_sleeve = (
        eligible_value / equity * _ONE_HUNDRED if weights_available else None
    )

    return PortfolioDetail(
        card=PortfolioCard(
            slug=config.slug,
            title=config.title,
            provider=config.provider,
            ytd_return_percent=ytd,
            performance_through=performance.end if performance else None,
            quality=quality,
            performance_points=points,
        ),
        holdings_as_of=balance.as_of,
        holdings=tuple(holdings),
        gross_exposure_percent=gross_exposure,
        net_exposure_percent=net_exposure,
        analytical_sleeve_percent=analytical_sleeve,
        benchmark_symbol=config.benchmark_symbol,
        analytics=analytics,
    )


def _performance_quality(
    performance: PerformanceReport | None,
) -> PublicationQuality:
    if performance is None or not performance.periods:
        return PublicationQuality.UNAVAILABLE
    if performance.quality == "verified":
        return PublicationQuality.PORTFOLIO_LAB_COMPUTED
    return PublicationQuality.ESTIMATED


def _performance_points(
    performance: PerformanceReport | None,
    quality: PublicationQuality,
) -> tuple[PublishedPerformancePoint, ...]:
    if performance is None or performance.start is None or not performance.periods:
        return ()
    compounded = Decimal(1)
    points = [
        PublishedPerformancePoint(
            at=performance.start,
            return_percent=Decimal(0),
            benchmark_return_percent=None,
            quality=quality,
        )
    ]
    for period in performance.periods:
        period_return = period.modified_dietz_return
        if period_return is None or not isfinite(period_return):
            return ()
        compounded *= Decimal(str(1 + period_return))
        points.append(
            PublishedPerformancePoint(
                at=period.end,
                return_percent=(compounded - Decimal(1)) * _ONE_HUNDRED,
                benchmark_return_percent=None,
                quality=quality,
            )
        )
    return tuple(points)


def _ytd_return_percent(performance: PerformanceReport | None) -> Decimal | None:
    if performance is None or performance.end is None or not performance.periods:
        return None
    year_start = datetime(performance.end.year, 1, 1, tzinfo=UTC)
    if performance.start is None or performance.start > year_start:
        return None
    compounded = Decimal(1)
    included = False
    for period in performance.periods:
        if period.end <= year_start:
            continue
        value = period.modified_dietz_return
        if value is None or not isfinite(value):
            return None
        compounded *= Decimal(str(1 + value))
        included = True
    return (compounded - Decimal(1)) * _ONE_HUNDRED if included else None


def _validate_analytics(value: Any, path: tuple[str, ...] = ()) -> None:
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if "$" in value:
            raise PublicationValidationError(
                f"Dollar-formatted analytics text is forbidden at {'.'.join(path) or 'analytics'}."
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_analytics(item, (*path, str(index)))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = "".join(
                character for character in key.lower() if character.isalnum()
            )
            private_compound = (
                normalized.startswith("account")
                or normalized.endswith("amount")
                or any(
                    term in normalized
                    for term in (
                        "buyingpower",
                        "cashflow",
                        "marketvalue",
                        "netliquidation",
                        "portfoliovalue",
                        "currentvalue",
                        "currentprice",
                        "lastprice",
                        "totalcostbasis",
                    )
                )
            )
            if normalized in _FORBIDDEN_ANALYTICS_KEYS or private_compound:
                raise PublicationValidationError(
                    f"Private analytics field is forbidden: {'.'.join((*path, key))}."
                )
            _validate_analytics(item, (*path, key))
        return
    raise PublicationValidationError(
        f"Unsupported analytics value at {'.'.join(path) or 'analytics'}."
    )
