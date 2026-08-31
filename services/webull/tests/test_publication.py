from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from webull_service.adapters import FakeWebullAdapter
from webull_service.config import Settings
from webull_service.main import create_app
from webull_service.models import (
    BalanceSnapshot,
    BrokerageAccount,
    CashSource,
    PerformancePeriod,
    PerformanceReport,
    PortfolioState,
    PositionSnapshot,
    PublicationConfig,
    PublicationConfigurationRequest,
    PublishedHoldingKind,
    StatementAnchor,
    utc_now,
)
from webull_service.publication import (
    PublicationValidationError,
    build_publication_detail,
    validate_publication_title,
)
from webull_service.repository import MemoryRepository, RepositoryError


def _portfolio(
    *,
    equity: str = "100",
    cash: str = "-25",
    position_value: str = "125",
    cash_source: CashSource = CashSource.EXPLICIT,
) -> PortfolioState:
    account = BrokerageAccount(account_id="margin-account")
    as_of = datetime(2026, 8, 28, 20, tzinfo=UTC)
    return PortfolioState(
        account=account,
        balance=BalanceSnapshot(
            account_id=account.account_id,
            as_of=as_of,
            equity=Decimal(equity),
            cash=Decimal(cash),
            market_value=Decimal(position_value),
            cash_source=cash_source,
        ),
        positions=(
            PositionSnapshot(
                account_id=account.account_id,
                external_position_id="aapl",
                symbol="AAPL",
                quantity=Decimal(5),
                last_price=Decimal(25),
                market_value=Decimal(position_value),
                average_cost=Decimal(20),
                cost_basis=Decimal(100),
            ),
        ),
        snapshot_id="00000000-0000-0000-0000-000000000001",
    )


def _config(*, enabled: bool = True) -> PublicationConfig:
    return PublicationConfig(
        publication_id="00000000-0000-0000-0000-000000000002",
        account_handle="00000000-0000-0000-0000-000000000003",
        account_id="margin-account",
        provider="webull",
        slug="margin-book",
        title="Margin Book",
        benchmark_symbol="SPY",
        enabled=enabled,
        has_published_revision=False,
        updated_at=utc_now(),
    )


def _performance(start: datetime) -> PerformanceReport:
    end = start + timedelta(days=1)
    return PerformanceReport(
        account_id="margin-account",
        start=start,
        end=end,
        periods=(
            PerformancePeriod(
                start=start,
                end=end,
                beginning_value=Decimal(100),
                ending_value=Decimal(110),
                net_external_flow=Decimal(0),
                modified_dietz_return=0.1,
            ),
        ),
        time_weighted_return=0.1,
        money_weighted_return=None,
        net_external_flow=Decimal(0),
        beginning_value=Decimal(100),
        ending_value=Decimal(110),
        quality="verified",
    )


@pytest.mark.parametrize(
    "title",
    (
        "Individual 4321",
        "M1 •••4321",
        "Balance 10,000",
        "$10 Growth",
        "Growth 43-21",
    ),
)
def test_publication_titles_reject_account_and_value_identifiers(title: str) -> None:
    with pytest.raises(PublicationValidationError):
        validate_publication_title(title, "plaid_m1:synthetic-private-4321")


@pytest.mark.parametrize("title", ("Community Growth", "M1 Core", "Strategy 60/40"))
def test_publication_titles_allow_safe_strategy_names(title: str) -> None:
    assert validate_publication_title(title, "plaid_m1:synthetic-private-4321") == title


def test_publication_titles_reject_full_all_letter_private_identifier() -> None:
    with pytest.raises(PublicationValidationError):
        validate_publication_title(
            "Synthetic Private Account", "plaid_m1:synthetic-private-account"
        )


def test_repository_rejects_private_title_before_configuration() -> None:
    repository = MemoryRepository()
    account = BrokerageAccount(account_id="plaid_m1:synthetic-private-4321")
    repository.accounts[account.account_id] = account

    with pytest.raises(PublicationValidationError):
        repository.configure_publication(
            PublicationConfigurationRequest(
                account_id=account.account_id,
                slug="private-title",
                title="Individual 4321",
            )
        )


def test_signed_margin_weights_reconcile_and_expose_only_per_share_basis() -> None:
    detail = build_publication_detail(_config(), _portfolio(), None)

    security, cash = detail.holdings
    assert security.kind is PublishedHoldingKind.SECURITY
    assert security.weight_percent == Decimal(125)
    assert security.cost_basis_per_share == Decimal(20)
    assert security.return_percent == Decimal(25)
    assert cash.kind is PublishedHoldingKind.CASH_MARGIN
    assert cash.weight_percent == Decimal(-25)
    assert cash.cost_basis_per_share is None
    assert detail.gross_exposure_percent == Decimal(125)
    assert detail.net_exposure_percent == Decimal(125)
    assert sum(item.weight_percent or Decimal(0) for item in detail.holdings) == 100


def test_unexplained_residual_is_not_disguised_as_cash() -> None:
    detail = build_publication_detail(
        _config(),
        _portfolio(equity="100", cash="10", position_value="80"),
        None,
    )

    other = detail.holdings[-1]
    assert other.kind is PublishedHoldingKind.OTHER
    assert other.weight_percent == Decimal(10)
    assert other.cost_basis_per_share is None
    assert sum(item.weight_percent or Decimal(0) for item in detail.holdings) == 100


@pytest.mark.parametrize("equity", ["0", "-10"])
def test_nonpositive_nav_marks_every_weight_and_exposure_unavailable(
    equity: str,
) -> None:
    detail = build_publication_detail(
        _config(),
        _portfolio(equity=equity, cash="0", position_value="25"),
        None,
    )

    assert all(item.weight_percent is None for item in detail.holdings)
    assert detail.gross_exposure_percent is None
    assert detail.net_exposure_percent is None
    assert detail.analytical_sleeve_percent is None


def test_public_analytics_rejects_private_fields_and_dollar_formatted_text() -> None:
    with pytest.raises(PublicationValidationError, match="forbidden"):
        build_publication_detail(
            _config(), _portfolio(), None, analytics={"accountBalance": 100}
        )
    with pytest.raises(PublicationValidationError, match="Dollar-formatted"):
        build_publication_detail(
            _config(), _portfolio(), None, analytics={"note": "$100"}
        )


def test_ytd_requires_a_year_start_anchor() -> None:
    anchored = build_publication_detail(
        _config(),
        _portfolio(),
        _performance(datetime(2026, 1, 1, tzinfo=UTC)),
    )
    partial = build_publication_detail(
        _config(),
        _portfolio(),
        _performance(datetime(2026, 2, 1, tzinfo=UTC)),
    )

    assert anchored.card.ytd_return_percent == Decimal("10.0")
    assert partial.card.ytd_return_percent is None


def test_memory_publication_commit_preserves_last_good_revision_on_failure() -> None:
    repository = MemoryRepository()
    portfolio = _portfolio()
    repository.accounts[portfolio.account.account_id] = portfolio.account
    repository.snapshots[portfolio.account.account_id] = [portfolio]
    config = repository.configure_publication(
        PublicationConfigurationRequest(
            account_id=portfolio.account.account_id,
            slug="margin-book",
            title="Margin Book",
            enabled=True,
        )
    )
    detail = build_publication_detail(config, portfolio, None)
    repository.commit_publication(
        config.publication_id, detail, source_snapshot_id=portfolio.snapshot_id
    )
    same_day = repository.commit_publication(
        config.publication_id,
        detail.model_copy(update={"analytics": {"risk": 1}}),
        source_snapshot_id=portfolio.snapshot_id,
    )

    assert same_day == detail
    assert repository.get_published_portfolio("margin-book") == detail

    with pytest.raises(RepositoryError, match="source snapshot"):
        repository.commit_publication(
            config.publication_id,
            detail.model_copy(update={"analytics": {"risk": 1}}),
            source_snapshot_id="00000000-0000-0000-0000-000000000099",
        )

    assert repository.get_published_portfolio("margin-book") == detail


def test_publication_api_returns_allowlisted_follower_projection() -> None:
    settings = Settings(
        database_url="",
        internal_api_token="token",
        portfolio_owner_github_id="123",
        webull_app_key="",
        webull_app_secret="",
        adapter_kind="fake",
        auto_migrate=False,
        portfolio_publication_enabled=True,
        published_analytics_enabled=False,
    )
    repository = MemoryRepository()
    adapter = FakeWebullAdapter()
    publication_time = [adapter.as_of - timedelta(hours=1)]
    client = TestClient(
        create_app(
            settings=settings,
            adapter=adapter,
            repository=repository,
            publication_clock=lambda: publication_time[0],
        )
    )
    headers = {
        "Authorization": "Bearer token",
        "x-portfolio-owner-github-id": "123",
    }
    assert client.post("/v1/connect", headers=headers).status_code == 200
    accounts = client.get("/v1/providers/webull/accounts", headers=headers)
    assert accounts.status_code == 200
    account_handle = accounts.json()["accounts"][0]["accountHandle"]
    assert "accountId" not in accounts.text
    configured = client.put(
        "/v1/publications/configure",
        headers=headers,
        json={
            "accountHandle": account_handle,
            "slug": "public-book",
            "title": "Public Book",
            "enabled": True,
        },
    )
    assert configured.status_code == 200, configured.text
    assert "accountId" not in configured.text
    publication_id = configured.json()["publicationId"]
    before_close = client.post(
        f"/v1/publications/{publication_id}/publish", headers=headers, json={}
    )
    assert before_close.status_code == 409
    assert client.get("/v1/publications", headers=headers).json() == {"portfolios": []}

    publication_time[0] = adapter.as_of + timedelta(minutes=30)
    published = client.post(
        f"/v1/publications/{publication_id}/publish", headers=headers, json={}
    )
    assert published.status_code == 200, published.text

    listing = client.get("/v1/publications", headers=headers)
    detail = client.get("/v1/publications/public-book", headers=headers)
    assert listing.status_code == detail.status_code == 200
    serialized = detail.text.lower()
    for forbidden in (
        "accountid",
        "quantity",
        "marketvalue",
        "netliquidation",
        "dayprofitloss",
        "unrealizedprofitloss",
        '"costbasis"',
    ):
        assert forbidden not in serialized
    assert "costbasispershare" in serialized
    assert detail.json()["holdings"][-1]["kind"] == "cash_margin"


def test_publication_blocks_when_statement_history_predates_cash_coverage() -> None:
    settings = Settings(
        database_url="",
        internal_api_token="token",
        portfolio_owner_github_id="123",
        webull_app_key="",
        webull_app_secret="",
        adapter_kind="fake",
        auto_migrate=False,
        portfolio_publication_enabled=True,
    )
    repository = MemoryRepository()
    adapter = FakeWebullAdapter()
    client = TestClient(
        create_app(
            settings=settings,
            adapter=adapter,
            repository=repository,
            publication_clock=lambda: adapter.as_of + timedelta(minutes=30),
        )
    )
    headers = {
        "Authorization": "Bearer token",
        "x-portfolio-owner-github-id": "123",
    }
    assert client.post("/v1/connect", headers=headers).status_code == 200
    repository.import_statement_anchor(
        StatementAnchor(
            account_id=adapter.account.account_id,
            external_statement_id="older-than-coverage",
            statement_date=adapter.as_of - timedelta(days=90),
            ending_equity=Decimal(14000),
        )
    )
    account_handle = client.get(
        "/v1/providers/webull/accounts", headers=headers
    ).json()["accounts"][0]["accountHandle"]
    configured = client.put(
        "/v1/publications/configure",
        headers=headers,
        json={
            "accountHandle": account_handle,
            "slug": "coverage-book",
            "title": "Coverage Book",
            "enabled": True,
        },
    )

    response = client.post(
        f"/v1/publications/{configured.json()['publicationId']}/publish",
        headers=headers,
        json={},
    )

    assert response.status_code == 409
    assert "full performance interval" in response.json()["detail"]
    assert repository.list_published_portfolios().portfolios == ()
