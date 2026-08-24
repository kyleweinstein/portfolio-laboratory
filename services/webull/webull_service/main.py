from __future__ import annotations

import hmac
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import Field

from .adapters import (
    FakeWebullAdapter,
    OfficialWebullAdapter,
    ReadOnlyWebullAdapter,
    WebullAdapterError,
    application_read_only_gate_enabled,
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
    ServiceNextAction,
    ServiceStatus,
    StatementAnchor,
    SyncResult,
    VerificationAttempt,
    VerificationStage,
    VerificationState,
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

    def live_read_only_access_enabled() -> bool:
        if runtime_settings.adapter_kind == "fake":
            return True
        return application_read_only_gate_enabled(
            configured=runtime_settings.webull_read_only_adapter_enabled,
            adapter=runtime_adapter,
        )

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
            webull_read_only_adapter_enabled=live_read_only_access_enabled(),
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

    def require_read_only_adapter_enabled() -> None:
        if (
            runtime_settings.adapter_kind == "official"
            and not live_read_only_access_enabled()
        ):
            raise HTTPException(
                status_code=503,
                detail=(
                    "WEBULL_READ_ONLY_ADAPTER_ENABLED must be true before live "
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

    def next_action_for(
        *,
        connected: bool,
        verification: VerificationAttempt | None,
        portfolio: PortfolioState | None,
    ) -> ServiceNextAction:
        if (
            runtime_settings.adapter_kind == "official"
            and not live_read_only_access_enabled()
        ):
            return ServiceNextAction.CONFIGURE
        if verification and verification.state is VerificationState.RUNNING:
            return ServiceNextAction.WAIT
        if (
            not connected
            and verification
            and verification.state
            in {VerificationState.FAILED, VerificationState.TIMED_OUT}
        ):
            return ServiceNextAction.RETRY_VERIFICATION
        if connected:
            return (
                ServiceNextAction.VIEW_PORTFOLIO
                if portfolio is not None
                else ServiceNextAction.SYNC_ACCOUNT
            )
        return ServiceNextAction.START_VERIFICATION

    def status_response() -> ServiceStatus:
        state = runtime_repository.get_connection_state()
        verification = runtime_repository.get_verification_attempt()
        verification_in_progress = bool(
            verification and verification.state is VerificationState.RUNNING
        )
        if not state.connected or not state.selected_account_id:
            return ServiceStatus(
                connected=state.connected,
                verification_in_progress=verification_in_progress,
                verification=verification,
                next_action=next_action_for(
                    connected=state.connected,
                    verification=verification,
                    portfolio=None,
                ),
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
            verification_in_progress=verification_in_progress,
            verification=verification,
            next_action=next_action_for(
                connected=True,
                verification=verification,
                portfolio=portfolio,
            ),
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
            require_read_only_adapter_enabled()
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
        require_read_only_adapter_enabled()
        attempt, created = runtime_repository.begin_verification_attempt()
        if not created:
            return status_response()
        try:
            runtime_repository.advance_verification_attempt(
                attempt.attempt_id, VerificationStage.VERIFYING_ACCESS
            )
            accounts = runtime_adapter.list_accounts()
            runtime_repository.advance_verification_attempt(
                attempt.attempt_id, VerificationStage.DISCOVERING_ACCOUNTS
            )
            state = runtime_repository.connect_accounts(accounts)
            if state.selected_account_id:
                runtime_repository.advance_verification_attempt(
                    attempt.attempt_id, VerificationStage.SYNCING_ACCOUNT
                )
                sync_service.sync_account(state.selected_account_id, accounts=accounts)
            runtime_repository.advance_verification_attempt(
                attempt.attempt_id, VerificationStage.FINALIZING
            )
            runtime_repository.finish_verification_attempt(
                attempt.attempt_id, VerificationState.SUCCEEDED
            )
        except Exception as exc:
            if isinstance(exc, WebullAdapterError):
                error_code = "webull_access_failed"
                error_message = str(exc)[:500]
            else:
                error_code = "verification_failed"
                error_message = "Webull verification could not be completed."
            with suppress(Exception):
                runtime_repository.finish_verification_attempt(
                    attempt.attempt_id,
                    VerificationState.FAILED,
                    error_code=error_code,
                    error_message=error_message,
                )
            raise
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
        require_read_only_adapter_enabled()
        account_id = selected_account(payload.account_id if payload else None)
        return sync_service.sync_account(account_id)

    @router.post("/scheduled-sync", response_model=tuple[SyncResult, ...])
    def scheduled_sync() -> tuple[SyncResult, ...]:
        require_read_only_adapter_enabled()
        return tuple(sync_service.sync_all())

    @router.post("/backfill", response_model=BackfillResult)
    def backfill(payload: BackfillRequest | None = None) -> BackfillResult:
        require_read_only_adapter_enabled()
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
