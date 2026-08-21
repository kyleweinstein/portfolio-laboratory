from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from datetime import timedelta
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import Field

from .adapters import (
    FakeWebullAdapter,
    OfficialWebullAdapter,
    ReadOnlyWebullAdapter,
    WebullAdapterError,
)
from .config import Settings
from .migrate import run_migrations
from .models import (
    BackfillResult,
    BrokerageAccount,
    CapabilityReport,
    CashActivity,
    DashboardState,
    DataIssue,
    HealthReport,
    NormalizedModel,
    OrderRecord,
    PortfolioState,
    ServiceStatus,
    StatementAnchor,
    SyncResult,
    utc_now,
)
from .performance import calculate_performance
from .repository import (
    PortfolioRepository,
    PostgresRepository,
    RepositoryConfigurationError,
    RepositoryError,
)
from .sync import SyncService


class AccountSelection(NormalizedModel):
    account_id: str = Field(min_length=1, max_length=128)


class AccountTarget(NormalizedModel):
    account_id: str | None = Field(default=None, min_length=1, max_length=128)


class BackfillRequest(AccountTarget):
    days: int = Field(default=730, ge=1, le=3650)


class ActivityList(NormalizedModel):
    account_id: str
    activities: tuple[CashActivity, ...]


class OrderList(NormalizedModel):
    account_id: str
    orders: tuple[OrderRecord, ...]


def _build_adapter(settings: Settings) -> ReadOnlyWebullAdapter:
    if settings.adapter_kind == "fake":
        return FakeWebullAdapter()
    if settings.adapter_kind != "official":
        raise RuntimeError("WEBULL_ADAPTER must be 'official' or 'fake'.")
    return OfficialWebullAdapter(
        app_key=settings.webull_app_key,
        app_secret=settings.webull_app_secret,
        region=settings.webull_region,
        endpoint=settings.webull_endpoint,
        token_dir=settings.webull_token_dir,
    )


def create_app(
    *,
    settings: Settings | None = None,
    adapter: ReadOnlyWebullAdapter | None = None,
    repository: PortfolioRepository | None = None,
) -> FastAPI:
    runtime_settings = settings or Settings.from_env()
    runtime_adapter = adapter or _build_adapter(runtime_settings)
    runtime_repository = repository or PostgresRepository(runtime_settings.database_url)
    sync_service = SyncService(
        runtime_adapter,
        runtime_repository,
        cash_activity_lookback_days=runtime_settings.cash_activity_lookback_days,
    )
    verification_lock = Lock()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if runtime_settings.auto_migrate and runtime_settings.database_url:
            run_migrations(runtime_settings.database_url)
        yield

    application = FastAPI(
        title="Portfolio Lab Webull Service",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @application.exception_handler(WebullAdapterError)
    async def adapter_error_handler(_, exc: WebullAdapterError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @application.exception_handler(RepositoryConfigurationError)
    async def repository_configuration_handler(_, exc: RepositoryConfigurationError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @application.exception_handler(RepositoryError)
    async def repository_error_handler(_, exc: RepositoryError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(KeyError)
    async def not_found_handler(_, exc: KeyError):
        account_id = str(exc.args[0]) if exc.args else "unknown"
        return JSONResponse(
            status_code=404, content={"detail": f"Account {account_id} was not found."}
        )

    @application.get("/health", response_model=HealthReport)
    def health() -> HealthReport:
        return HealthReport(
            status="ok",
            database_configured=runtime_settings.database_configured,
            webull_credentials_configured=runtime_settings.webull_credentials_configured
            or runtime_settings.adapter_kind == "fake",
            webull_read_only_scope_confirmed=(
                runtime_settings.webull_read_only_scope_confirmed
                or runtime_settings.adapter_kind == "fake"
            ),
            authentication_configured=bool(
                runtime_settings.internal_api_token
                and runtime_settings.portfolio_owner_github_id
            ),
        )

    def require_internal_access(
        authorization: Annotated[str | None, Header()] = None,
        owner_github_id: Annotated[
            str | None, Header(alias="x-portfolio-owner-github-id")
        ] = None,
    ) -> str:
        expected = runtime_settings.internal_api_token
        if not expected:
            raise HTTPException(
                status_code=503, detail="INTERNAL_API_TOKEN is not configured."
            )
        prefix = "Bearer "
        supplied = (
            authorization[len(prefix) :]
            if authorization and authorization.startswith(prefix)
            else ""
        )
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(
                status_code=401,
                detail="Invalid internal bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not owner_github_id:
            raise HTTPException(
                status_code=403, detail="x-portfolio-owner-github-id is required."
            )
        expected_owner = runtime_settings.portfolio_owner_github_id
        if not expected_owner:
            raise HTTPException(
                status_code=503,
                detail="PORTFOLIO_OWNER_GITHUB_ID is not configured.",
            )
        if not hmac.compare_digest(owner_github_id, expected_owner):
            raise HTTPException(
                status_code=403, detail="Portfolio owner does not match this service."
            )
        return owner_github_id

    router = APIRouter(prefix="/v1", dependencies=[Depends(require_internal_access)])

    def require_read_only_scope_confirmation() -> None:
        if (
            runtime_settings.adapter_kind == "official"
            and not runtime_settings.webull_read_only_scope_confirmed
        ):
            raise HTTPException(
                status_code=503,
                detail=(
                    "WEBULL_READ_ONLY_SCOPE_CONFIRMED must be true before live "
                    "Webull access is enabled."
                ),
            )

    def selected_account(requested: str | None = None) -> str:
        if requested:
            return requested
        state = runtime_repository.get_connection_state()
        if not state.connected or not state.selected_account_id:
            raise RepositoryError("Connect Webull and select an account first.")
        return state.selected_account_id

    def issues_for(
        portfolio: PortfolioState | None,
        performance_period_count: int,
        capabilities: CapabilityReport,
    ) -> tuple[DataIssue, ...]:
        issues: list[DataIssue] = []
        if portfolio is None:
            issues.append(
                DataIssue(
                    code="NO_PORTFOLIO_SNAPSHOT",
                    severity="warning",
                    message="Run the first read-only sync to load balances and positions.",
                )
            )
        else:
            if utc_now() - portfolio.balance.as_of > timedelta(hours=36):
                issues.append(
                    DataIssue(
                        code="STALE_SNAPSHOT",
                        severity="warning",
                        message="The latest Webull account snapshot is more than 36 hours old.",
                    )
                )
            unsupported = sorted(
                {
                    item.instrument_type
                    for item in portfolio.positions
                    if item.instrument_type not in {"EQUITY", "ETF", "STOCK"}
                }
            )
            if unsupported:
                issues.append(
                    DataIssue(
                        code="UNSUPPORTED_ANALYTICS_ASSETS",
                        severity="warning",
                        message=(
                            "These asset types are retained for account totals but excluded from the "
                            f"long-only Portfolio Lab engine: {', '.join(unsupported)}."
                        ),
                    )
                )
        if performance_period_count == 0:
            issues.append(
                DataIssue(
                    code="PERFORMANCE_HISTORY_BUILDING",
                    severity="info",
                    message=(
                        "Daily account performance requires at least two scheduled snapshots or "
                        "validated statement anchors."
                    ),
                )
            )
        if not capabilities.order_history:
            issues.append(
                DataIssue(
                    code="ORDER_HISTORY_UNAVAILABLE",
                    severity="info",
                    message="The installed official SDK does not expose safe read-only order history.",
                )
            )
        return tuple(issues)

    def status_response() -> ServiceStatus:
        state = runtime_repository.get_connection_state()
        if not state.connected or not state.selected_account_id:
            return ServiceStatus(
                connected=state.connected,
                verification_in_progress=verification_lock.locked(),
                accounts=state.accounts,
                selected_account_id=state.selected_account_id,
                last_synced_at=state.last_synced_at,
                dashboard=None,
            )
        account_id = state.selected_account_id
        portfolio = runtime_repository.get_portfolio(account_id)
        valuations, external_flows = runtime_repository.get_performance_inputs(
            account_id
        )
        performance = (
            calculate_performance(account_id, valuations, external_flows)
            if valuations
            else None
        )
        activities = tuple(
            runtime_repository.get_recent_activities(account_id, limit=100)
        )
        capabilities = runtime_adapter.probe(live=False)
        issues = issues_for(
            portfolio, len(performance.periods) if performance else 0, capabilities
        )
        return ServiceStatus(
            connected=True,
            verification_in_progress=verification_lock.locked(),
            accounts=state.accounts,
            selected_account_id=account_id,
            last_synced_at=state.last_synced_at,
            dashboard=DashboardState(
                portfolio=portfolio,
                performance=performance,
                recent_activities=activities,
                issues=issues,
            ),
        )

    @router.get("/capabilities", response_model=CapabilityReport)
    def capabilities(live: bool = Query(default=False)) -> CapabilityReport:
        if live:
            require_read_only_scope_confirmation()
        return runtime_adapter.probe(live=live)

    @router.get("/status", response_model=ServiceStatus)
    def status() -> ServiceStatus:
        return status_response()

    @router.get("/accounts", response_model=tuple[BrokerageAccount, ...])
    def accounts() -> tuple[BrokerageAccount, ...]:
        return runtime_repository.get_connection_state().accounts

    @router.post("/connect", response_model=ServiceStatus)
    def connect() -> ServiceStatus:
        if runtime_repository.get_connection_state().connected:
            return status_response()
        require_read_only_scope_confirmation()
        if not verification_lock.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail="Webull verification is already in progress.",
                headers={"Retry-After": "5"},
            )
        try:
            if not runtime_repository.get_connection_state().connected:
                sync_service.connect(sync_selected=True)
        finally:
            verification_lock.release()
        return status_response()

    @router.delete("/connect", response_model=ServiceStatus)
    def disconnect() -> ServiceStatus:
        runtime_repository.disconnect()
        return status_response()

    @router.post("/accounts/select", response_model=ServiceStatus)
    def select(payload: AccountSelection) -> ServiceStatus:
        runtime_repository.select_account(payload.account_id)
        return status_response()

    @router.post("/sync", response_model=SyncResult)
    def sync(payload: AccountTarget | None = None) -> SyncResult:
        require_read_only_scope_confirmation()
        account_id = selected_account(payload.account_id if payload else None)
        return sync_service.sync_account(account_id)

    @router.post("/scheduled-sync", response_model=tuple[SyncResult, ...])
    def scheduled_sync() -> tuple[SyncResult, ...]:
        require_read_only_scope_confirmation()
        return tuple(sync_service.sync_all())

    @router.post("/backfill", response_model=BackfillResult)
    def backfill(payload: BackfillRequest | None = None) -> BackfillResult:
        require_read_only_scope_confirmation()
        request = payload or BackfillRequest()
        account_id = selected_account(request.account_id)
        return sync_service.backfill(account_id, days=request.days)

    @router.get("/portfolio", response_model=PortfolioState | None)
    def portfolio(
        account_id: str | None = Query(default=None, alias="accountId"),
    ) -> PortfolioState | None:
        return runtime_repository.get_portfolio(selected_account(account_id))

    @router.get("/activities", response_model=ActivityList)
    def activities(
        account_id: str | None = Query(default=None, alias="accountId"),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> ActivityList:
        selected = selected_account(account_id)
        return ActivityList(
            account_id=selected,
            activities=tuple(
                runtime_repository.get_recent_activities(selected, limit=limit)
            ),
        )

    @router.get("/orders", response_model=OrderList)
    def orders(
        account_id: str | None = Query(default=None, alias="accountId"),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> OrderList:
        selected = selected_account(account_id)
        return OrderList(
            account_id=selected,
            orders=tuple(runtime_repository.get_recent_orders(selected, limit=limit)),
        )

    @router.get("/issues", response_model=tuple[DataIssue, ...])
    def issues() -> tuple[DataIssue, ...]:
        current = status_response()
        return current.dashboard.issues if current.dashboard else ()

    @router.post("/statement-anchors", response_model=StatementAnchor)
    def import_statement_anchor(anchor: StatementAnchor) -> StatementAnchor:
        # Contract accepts already-extracted, user-verified totals only. It never
        # accepts, stores or parses a PDF statement.
        return runtime_repository.import_statement_anchor(anchor)

    application.include_router(router)
    return application


app = create_app()
