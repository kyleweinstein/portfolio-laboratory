from __future__ import annotations

import hmac
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from .adapters import (
    BrokerAdapterError,
    FakeWebullAdapter,
    OfficialWebullAdapter,
    PlaidClient,
    PlaidHttpClient,
    PlaidM1InvestmentsAdapter,
    ReadOnlyWebullAdapter,
    WebullAdapterError,
    application_read_only_gate_enabled,
    verify_plaid_webhook,
)
from .analytics import (
    MarketDataClient,
    PublishedAnalyticsError,
    YahooMarketDataClient,
    add_benchmark_performance,
    generate_published_analytics,
    load_benchmark_series,
)
from .config import Settings
from .migrate import run_migrations
from .models import (
    BackfillResult,
    BrokerageAccount,
    BrokerageAccountReferenceList,
    BrokerProvider,
    CapabilityReport,
    CashActivity,
    DashboardState,
    DataIssue,
    DiscordSessionRequest,
    DiscordSessionWriteRequest,
    HealthReport,
    ManagedPublicationList,
    NormalizedModel,
    OrderRecord,
    PlaidExchangeRequest,
    PlaidLinkTokenResponse,
    PlaidSignedWebhook,
    PlaidWebhookEvent,
    PortfolioDetail,
    PortfolioState,
    ProviderCapabilities,
    ProviderCapabilityList,
    PublicationBuildRequest,
    PublicationConfig,
    PublicationConfigurationRequest,
    PublishedPortfolioList,
    ServiceNextAction,
    ServiceStatus,
    SyncResult,
    ValuationPoint,
    VerificationAttempt,
    VerificationStage,
    VerificationState,
    WebhookAcknowledgement,
    utc_now,
)
from .performance import calculate_performance
from .publication import PublicationValidationError, build_publication_detail
from .repository import (
    PortfolioRepository,
    PostgresRepository,
    RepositoryConfigurationError,
    RepositoryError,
)
from .scheduler import (
    is_after_close_publication_window,
    is_automatic_publication_window,
    is_reconciled_after_close_snapshot,
)
from .statement_import import (
    MAX_ENCRYPTED_BUNDLE_BYTES,
    StatementImportError,
    StatementImportReceipt,
    import_statement_bundle,
)
from .sync import SyncService


def _reconciled_performance_window(
    repository: PortfolioRepository,
    account_id: str,
    valuations: list[ValuationPoint],
    external_flows: list[CashActivity],
) -> tuple[list[ValuationPoint], list[CashActivity], bool]:
    """Return the longest covered valuation prefix without fabricating later history."""

    ordered_valuations = sorted(valuations, key=lambda point: point.at)
    ordered_flows = sorted(external_flows, key=lambda activity: activity.occurred_at)
    for end_index in range(len(ordered_valuations), 0, -1):
        candidate = ordered_valuations[:end_index]
        if repository.cash_activity_coverage_spans(
            account_id,
            start=candidate[0].at,
            end=candidate[-1].at,
        ):
            return (
                candidate,
                [
                    activity
                    for activity in ordered_flows
                    if activity.occurred_at <= candidate[-1].at
                ],
                True,
            )
    return [], [], False


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
    plaid_client: PlaidClient | None = None,
    market_data_client: MarketDataClient | None = None,
    repository: PortfolioRepository | None = None,
    publication_clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    runtime_settings = settings or Settings.from_env()
    runtime_adapter = adapter or _build_adapter(runtime_settings)
    runtime_repository = repository or PostgresRepository(
        runtime_settings.database_url,
        runtime_settings.portfolio_owner_github_id,
    )
    runtime_market_data = market_data_client or YahooMarketDataClient()
    runtime_publication_clock = publication_clock or utc_now
    sync_service = SyncService(
        runtime_adapter,
        runtime_repository,
        cash_activity_lookback_days=runtime_settings.cash_activity_lookback_days,
    )

    def configured_plaid_client() -> PlaidClient:
        if plaid_client is not None:
            return plaid_client
        return PlaidHttpClient(
            client_id=runtime_settings.plaid_client_id,
            secret=runtime_settings.plaid_secret,
            environment=runtime_settings.plaid_environment,
            production_enabled=runtime_settings.plaid_m1_integration_enabled,
        )

    def plaid_access_token() -> str | None:
        if runtime_settings.plaid_access_token:
            return runtime_settings.plaid_access_token
        if not runtime_settings.broker_credential_encryption_key:
            return None
        return runtime_repository.get_provider_access_token(
            BrokerProvider.PLAID_M1.value,
            encryption_key=runtime_settings.broker_credential_encryption_key,
        )

    def plaid_adapter() -> PlaidM1InvestmentsAdapter:
        token = plaid_access_token()
        if not token:
            raise RepositoryConfigurationError(
                "Connect M1 through Plaid before reading investment data."
            )
        return PlaidM1InvestmentsAdapter(
            client=configured_plaid_client(),
            access_token=token,
            expected_institution_id=runtime_settings.plaid_m1_institution_id or None,
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

    @application.exception_handler(BrokerAdapterError)
    async def broker_adapter_error_handler(_, exc: BrokerAdapterError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @application.exception_handler(PublicationValidationError)
    async def publication_validation_error_handler(_, exc: PublicationValidationError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(RepositoryConfigurationError)
    async def repository_configuration_handler(_, exc: RepositoryConfigurationError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @application.exception_handler(RepositoryError)
    async def repository_error_handler(_, exc: RepositoryError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(StatementImportError)
    async def statement_import_error_handler(_, exc: StatementImportError):
        status_code = {
            "STATEMENT_IMPORT_BUNDLE_TOO_LARGE": 413,
            "STATEMENT_IMPORT_KEY_INVALID": 503,
            "STATEMENT_IMPORT_PERSIST_FAILED": 503,
        }.get(exc.code, 422)
        return JSONResponse(
            status_code=status_code,
            content={"code": exc.code, "detail": exc.safe_message},
        )

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

    @router.post("/discord-sessions/write")
    def write_discord_session(payload: DiscordSessionWriteRequest) -> dict[str, bool]:
        runtime_repository.write_discord_session(
            payload.session_id,
            payload.session,
            encryption_key=runtime_settings.discord_session_encryption_key,
        )
        return {"stored": True}

    @router.post("/discord-sessions/read")
    def read_discord_session(payload: DiscordSessionRequest) -> dict[str, object]:
        session = runtime_repository.get_discord_session(
            payload.session_id,
            encryption_key=runtime_settings.discord_session_encryption_key,
        )
        if session is None:
            return {"session": None}
        return {
            "session": {
                "discordId": session.discord_id,
                "username": session.username,
                "displayName": session.display_name,
                "guildId": session.guild_id,
                "accessToken": session.access_token.get_secret_value(),
                "refreshToken": session.refresh_token.get_secret_value(),
                "tokenExpiresAt": session.token_expires_at.isoformat(),
                "membershipCheckedAt": session.membership_checked_at.isoformat(),
                "expiresAt": session.expires_at.isoformat(),
            }
        }

    @router.post("/discord-sessions/delete")
    def delete_discord_session(payload: DiscordSessionRequest) -> dict[str, bool]:
        runtime_repository.delete_discord_session(payload.session_id)
        return {"deleted": True}

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
        cash_activities_complete: bool | None,
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
        if cash_activities_complete is False:
            issues.append(
                DataIssue(
                    code="CASH_ACTIVITY_COVERAGE_INCOMPLETE",
                    severity="warning",
                    message=(
                        "Current holdings are available, but performance is unavailable because "
                        "cash activity coverage is incomplete."
                    ),
                )
            )
        elif performance_period_count == 0:
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
                last_sync_attempt=None,
                dashboard=None,
            )
        account_id = state.selected_account_id
        portfolio = runtime_repository.get_portfolio(account_id)
        last_sync_attempt = runtime_repository.get_latest_sync_attempt(account_id)
        cash_activities_complete = runtime_repository.get_latest_cash_activity_coverage(
            account_id
        )
        valuations, external_flows = runtime_repository.get_performance_inputs(
            account_id
        )
        reconciled_valuations, reconciled_flows, coverage_spans_performance = (
            _reconciled_performance_window(
                runtime_repository,
                account_id,
                list(valuations),
                list(external_flows),
            )
        )
        performance = (
            calculate_performance(
                account_id,
                reconciled_valuations,
                reconciled_flows,
                statement_reconciled=any(
                    point.source == "statement_anchor"
                    for point in reconciled_valuations
                ),
            )
            if coverage_spans_performance
            else None
        )
        effective_cash_activities_complete = (
            True if coverage_spans_performance else cash_activities_complete
        )
        activities = tuple(
            runtime_repository.get_recent_activities(account_id, limit=100)
        )
        capabilities = runtime_adapter.probe(live=False)
        issues = issues_for(
            portfolio,
            len(performance.periods) if performance else 0,
            capabilities,
            effective_cash_activities_complete,
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
            last_sync_attempt=last_sync_attempt,
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

    def provider_capabilities() -> tuple[ProviderCapabilities, ...]:
        webull_configured = (
            runtime_settings.webull_credentials_configured
            or runtime_settings.adapter_kind == "fake"
        )
        stored_plaid_connection = runtime_repository.has_provider_connection(
            BrokerProvider.PLAID_M1.value
        )
        plaid_configured = bool(
            runtime_settings.plaid_credentials_configured
            and (runtime_settings.plaid_access_token or stored_plaid_connection)
        )
        return (
            ProviderCapabilities(
                provider=BrokerProvider.WEBULL,
                enabled=live_read_only_access_enabled(),
                configured=webull_configured,
                accounts=True,
                balances=True,
                positions=True,
                activities=True,
                status=(
                    "ready"
                    if live_read_only_access_enabled() and webull_configured
                    else "configuration_required"
                ),
            ),
            ProviderCapabilities(
                provider=BrokerProvider.PLAID_M1,
                enabled=runtime_settings.plaid_m1_integration_enabled,
                configured=plaid_configured,
                accounts=True,
                balances=True,
                positions=True,
                activities=True,
                status=(
                    "ready"
                    if runtime_settings.plaid_m1_integration_enabled
                    and plaid_configured
                    else "configuration_required"
                ),
                message=(
                    None
                    if runtime_settings.plaid_m1_integration_enabled
                    else "Plaid M1 integration is disabled."
                ),
            ),
            ProviderCapabilities(
                provider=BrokerProvider.SCHWAB,
                enabled=runtime_settings.schwab_integration_enabled,
                configured=False,
                accounts=True,
                balances=True,
                positions=True,
                activities=True,
                status="adapter_deferred",
                message="Schwab is schema-ready; live OAuth and reads remain disabled.",
            ),
        )

    @router.get("/providers", response_model=ProviderCapabilityList)
    def providers() -> ProviderCapabilityList:
        return ProviderCapabilityList(providers=provider_capabilities())

    @router.get("/providers/{provider}/status", response_model=ProviderCapabilities)
    def provider_status(provider: BrokerProvider) -> ProviderCapabilities:
        return next(
            item for item in provider_capabilities() if item.provider == provider
        )

    @router.get(
        "/providers/{provider}/accounts",
        response_model=BrokerageAccountReferenceList,
    )
    def provider_accounts(
        provider: BrokerProvider,
    ) -> BrokerageAccountReferenceList:
        return BrokerageAccountReferenceList(
            accounts=runtime_repository.list_provider_account_references(provider.value)
        )

    @router.post("/providers/plaid/link-token", response_model=PlaidLinkTokenResponse)
    def create_plaid_link_token() -> PlaidLinkTokenResponse:
        if not runtime_settings.plaid_m1_integration_enabled:
            raise HTTPException(
                status_code=503, detail="Plaid M1 integration is disabled."
            )
        if not runtime_settings.plaid_credentials_configured:
            raise HTTPException(
                status_code=503, detail="Plaid server credentials are not configured."
            )
        payload = configured_plaid_client().link_token_create(
            client_user_id=f"github:{runtime_settings.portfolio_owner_github_id}",
            redirect_uri=runtime_settings.plaid_redirect_uri or None,
        )
        token = str(payload.get("link_token") or "")
        expiration_value = str(payload.get("expiration") or "")
        if not token or not expiration_value:
            raise BrokerAdapterError("Plaid Link returned an invalid response.")
        return PlaidLinkTokenResponse(
            link_token=token,
            expiration=datetime.fromisoformat(expiration_value.replace("Z", "+00:00")),
        )

    @router.post(
        "/providers/plaid/update-link-token", response_model=PlaidLinkTokenResponse
    )
    def create_plaid_update_link_token() -> PlaidLinkTokenResponse:
        if not runtime_settings.plaid_m1_integration_enabled:
            raise HTTPException(
                status_code=503, detail="Plaid M1 integration is disabled."
            )
        token = plaid_access_token()
        if not token:
            raise RepositoryConfigurationError(
                "Connect M1 through Plaid before starting update mode."
            )
        payload = configured_plaid_client().link_token_create(
            client_user_id=f"github:{runtime_settings.portfolio_owner_github_id}",
            redirect_uri=runtime_settings.plaid_redirect_uri or None,
            access_token=token,
        )
        link_token = str(payload.get("link_token") or "")
        expiration_value = str(payload.get("expiration") or "")
        if not link_token or not expiration_value:
            raise BrokerAdapterError("Plaid Link returned an invalid response.")
        return PlaidLinkTokenResponse(
            link_token=link_token,
            expiration=datetime.fromisoformat(expiration_value.replace("Z", "+00:00")),
        )

    @router.post("/providers/plaid/exchange", response_model=ProviderCapabilities)
    def exchange_plaid_public_token(
        payload: PlaidExchangeRequest,
    ) -> ProviderCapabilities:
        if not runtime_settings.plaid_m1_integration_enabled:
            raise HTTPException(
                status_code=503, detail="Plaid M1 integration is disabled."
            )
        if not runtime_settings.broker_credential_encryption_key:
            raise HTTPException(
                status_code=503,
                detail="Broker credential encryption is not configured.",
            )
        client = configured_plaid_client()
        exchange = client.exchange_public_token(payload.public_token)
        access_token = str(exchange.get("access_token") or "")
        item_id = str(exchange.get("item_id") or "")
        if not access_token or not item_id:
            raise BrokerAdapterError(
                "Plaid token exchange returned an invalid response."
            )
        item_payload = client.item_get(access_token)
        item = (
            item_payload.get("item")
            if isinstance(item_payload.get("item"), dict)
            else {}
        )
        institution_id = str(item.get("institution_id") or "")
        if not institution_id:
            raise BrokerAdapterError(
                "Plaid did not identify the connected institution."
            )
        institution_payload = client.institution_get_by_id(institution_id)
        institution = (
            institution_payload.get("institution")
            if isinstance(institution_payload.get("institution"), dict)
            else {}
        )
        institution_name = str(institution.get("name") or "")
        if "m1" not in institution_name.lower():
            raise BrokerAdapterError(
                "The connected Plaid institution is not M1 Finance."
            )
        institution_products = {
            str(product).strip().lower()
            for product in institution.get("products", [])
            if isinstance(product, str)
        }
        if "investments" not in institution_products:
            raise BrokerAdapterError(
                "The connected M1 institution does not currently support Plaid Investments."
            )
        if (
            runtime_settings.plaid_m1_institution_id
            and institution_id != runtime_settings.plaid_m1_institution_id
        ):
            raise BrokerAdapterError(
                "The connected Plaid institution is not approved for M1."
            )
        runtime_repository.save_provider_access_token(
            BrokerProvider.PLAID_M1.value,
            item_id=item_id,
            institution_id=institution_id,
            access_token=access_token,
            encryption_key=runtime_settings.broker_credential_encryption_key,
            owner_github_id=runtime_settings.portfolio_owner_github_id,
        )
        return next(
            item
            for item in provider_capabilities()
            if item.provider == BrokerProvider.PLAID_M1
        )

    @router.post("/providers/plaid/sync", response_model=tuple[SyncResult, ...])
    def sync_plaid_m1() -> tuple[SyncResult, ...]:
        if not runtime_settings.plaid_m1_integration_enabled:
            raise HTTPException(
                status_code=503, detail="Plaid M1 integration is disabled."
            )
        adapter = plaid_adapter()
        accounts = adapter.list_accounts()
        provider_sync = SyncService(
            adapter,
            runtime_repository,
            cash_activity_lookback_days=runtime_settings.cash_activity_lookback_days,
        )
        return tuple(
            provider_sync.sync_account(account.account_id, accounts=accounts)
            for account in accounts
        )

    @router.post("/providers/plaid/webhook", response_model=WebhookAcknowledgement)
    def plaid_webhook(payload: PlaidSignedWebhook) -> WebhookAcknowledgement:
        if not runtime_settings.plaid_m1_integration_enabled:
            raise HTTPException(
                status_code=503, detail="Plaid M1 integration is disabled."
            )
        event = PlaidWebhookEvent.model_validate(
            verify_plaid_webhook(
                payload.raw_body,
                payload.signature,
                configured_plaid_client(),
            )
        )
        sync_required = event.webhook_type.upper() in {
            "HOLDINGS",
            "INVESTMENTS_TRANSACTIONS",
        } and event.webhook_code.upper() in {
            "DEFAULT_UPDATE",
            "HISTORICAL_UPDATE",
        }
        if sync_required:
            runtime_repository.request_provider_refresh(BrokerProvider.PLAID_M1.value)
        return WebhookAcknowledgement(accepted=True, sync_required=sync_required)

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
        results: list[SyncResult] = []
        if not runtime_settings.plaid_m1_integration_enabled:
            require_read_only_adapter_enabled()
        if runtime_repository.get_connection_state().connected:
            require_read_only_adapter_enabled()
            results.extend(sync_service.sync_all())
        if (
            runtime_settings.plaid_m1_integration_enabled
            and runtime_settings.plaid_credentials_configured
            and plaid_access_token()
        ):
            adapter = plaid_adapter()
            accounts = adapter.list_accounts()
            provider_sync = SyncService(
                adapter,
                runtime_repository,
                cash_activity_lookback_days=runtime_settings.cash_activity_lookback_days,
            )
            results.extend(
                provider_sync.sync_account(account.account_id, accounts=accounts)
                for account in accounts
            )
            runtime_repository.complete_provider_refresh(BrokerProvider.PLAID_M1.value)
        if (
            runtime_settings.portfolio_publication_enabled
            and runtime_settings.auto_publish_after_close_enabled
            and is_automatic_publication_window(runtime_publication_clock())
        ):
            scheduled_publish()
        return tuple(results)

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

    @router.post("/statement-imports", response_model=StatementImportReceipt)
    async def import_statements(
        request: Request,
        owner_github_id: str = Depends(require_internal_access),
    ) -> StatementImportReceipt:
        if not runtime_settings.m1_statement_import_enabled:
            # Hide the operator-only boundary while its fail-closed gate is off.
            raise HTTPException(status_code=404, detail="Not found.")
        media_type = request.headers.get("content-type", "").split(";", 1)[0]
        if media_type.strip().lower() != "application/octet-stream":
            raise HTTPException(
                status_code=415,
                detail="Statement imports require application/octet-stream.",
            )
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="The statement import content length is invalid.",
                ) from None
            if declared_length < 0:
                raise HTTPException(
                    status_code=400,
                    detail="The statement import content length is invalid.",
                )
            if declared_length > MAX_ENCRYPTED_BUNDLE_BYTES:
                raise StatementImportError("STATEMENT_IMPORT_BUNDLE_TOO_LARGE")
        if not runtime_settings.m1_statement_output_key:
            raise HTTPException(
                status_code=503,
                detail="Statement import encryption is not configured.",
            )

        encrypted_payload = bytearray()
        async for chunk in request.stream():
            if len(encrypted_payload) + len(chunk) > MAX_ENCRYPTED_BUNDLE_BYTES:
                raise StatementImportError("STATEMENT_IMPORT_BUNDLE_TOO_LARGE")
            encrypted_payload.extend(chunk)
        return import_statement_bundle(
            encrypted_payload=bytes(encrypted_payload),
            m1_statement_output_key=runtime_settings.m1_statement_output_key,
            owner_github_id=owner_github_id,
            atomic_commit=runtime_repository.commit_statement_import,
        )

    @router.put("/publications/configure", response_model=PublicationConfig)
    def configure_publication(
        payload: PublicationConfigurationRequest,
    ) -> PublicationConfig:
        if not runtime_settings.portfolio_publication_enabled:
            raise HTTPException(
                status_code=503, detail="Portfolio publication is disabled."
            )
        return runtime_repository.configure_publication(payload)

    @router.get("/publications/manage", response_model=ManagedPublicationList)
    def manage_publications() -> ManagedPublicationList:
        if not runtime_settings.portfolio_publication_enabled:
            return ManagedPublicationList(publications=())
        return runtime_repository.list_managed_publications()

    @router.get("/publications", response_model=PublishedPortfolioList)
    def publications() -> PublishedPortfolioList:
        if not runtime_settings.portfolio_publication_enabled:
            return PublishedPortfolioList(portfolios=())
        return runtime_repository.list_published_portfolios()

    def build_publication_preview(
        publication_id: str,
        *,
        require_publishable: bool = False,
    ) -> tuple[PortfolioDetail, str, bool]:
        if not runtime_settings.portfolio_publication_enabled:
            raise HTTPException(
                status_code=503, detail="Portfolio publication is disabled."
            )
        config = runtime_repository.get_publication_config(publication_id)
        portfolio = runtime_repository.get_portfolio(config.account_id)
        if portfolio is None:
            raise RepositoryError("Sync the account before publishing it.")
        valuations, external_flows = runtime_repository.get_performance_inputs(
            config.account_id
        )
        reconciled_valuations, reconciled_flows, cash_coverage_spans_performance = (
            _reconciled_performance_window(
                runtime_repository,
                config.account_id,
                list(valuations),
                list(external_flows),
            )
        )
        if require_publishable and not cash_coverage_spans_performance:
            raise RepositoryError(
                "Cash activity coverage must span the full performance interval "
                "before publication."
            )
        performance = (
            calculate_performance(
                config.account_id,
                reconciled_valuations,
                reconciled_flows,
                statement_reconciled=any(
                    point.source == "statement_anchor"
                    for point in reconciled_valuations
                ),
            )
            if cash_coverage_spans_performance
            else None
        )
        try:
            analytics = (
                generate_published_analytics(
                    portfolio,
                    config.benchmark_symbol,
                    runtime_market_data,
                    years=runtime_settings.published_analytics_years,
                    risk_free_rate=(
                        runtime_settings.published_analytics_risk_free_rate
                    ),
                    rebalance_band_percent=(
                        runtime_settings.published_analytics_rebalance_band_percent
                    ),
                    drift_days=runtime_settings.published_analytics_drift_days,
                    concurrency=runtime_settings.published_analytics_concurrency,
                )
                if runtime_settings.published_analytics_enabled
                else None
            )
        except PublishedAnalyticsError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if analytics is None and not runtime_settings.published_analytics_enabled:
            try:
                analytics = runtime_repository.get_published_portfolio(
                    config.slug
                ).analytics
            except KeyError:
                pass
        detail = build_publication_detail(
            config,
            portfolio,
            performance,
            analytics=analytics,
            tolerance_bps=runtime_settings.publication_weight_tolerance_bps,
        )
        benchmark = (
            load_benchmark_series(
                runtime_market_data,
                config.benchmark_symbol,
                runtime_settings.published_analytics_years,
            )
            if detail.card.performance_points
            else None
        )
        return (
            add_benchmark_performance(detail, benchmark),
            portfolio.snapshot_id,
            cash_coverage_spans_performance,
        )

    def build_and_commit_publication(publication_id: str) -> PortfolioDetail:
        now = runtime_publication_clock()
        if not is_after_close_publication_window(now):
            raise RepositoryError(
                "Publishing is available only during the after-close reconciliation window."
            )
        detail, source_snapshot_id, cash_coverage_complete = build_publication_preview(
            publication_id, require_publishable=True
        )
        if not cash_coverage_complete:
            raise RepositoryError(
                "Cash activity coverage must span the full performance interval "
                "before publication."
            )
        if not is_reconciled_after_close_snapshot(detail.holdings_as_of, now):
            raise RepositoryError(
                "Publishing requires a same-market-date reconciled after-close snapshot."
            )
        return runtime_repository.commit_publication(
            publication_id,
            detail,
            source_snapshot_id=source_snapshot_id,
        )

    @router.post(
        "/publications/scheduled-publish", response_model=tuple[PortfolioDetail, ...]
    )
    def scheduled_publish() -> tuple[PortfolioDetail, ...]:
        if (
            not runtime_settings.portfolio_publication_enabled
            or not runtime_settings.auto_publish_after_close_enabled
            or not is_automatic_publication_window(runtime_publication_clock())
        ):
            return ()
        return tuple(
            build_and_commit_publication(config.publication_id)
            for config in runtime_repository.list_managed_publications().publications
            if config.enabled
        )

    @router.post(
        "/publications/{publication_id}/publish", response_model=PortfolioDetail
    )
    def publish_portfolio(
        publication_id: str,
        _payload: PublicationBuildRequest | None = None,
    ) -> PortfolioDetail:
        return build_and_commit_publication(publication_id)

    @router.get(
        "/publications/{publication_id}/preview", response_model=PortfolioDetail
    )
    def preview_portfolio(publication_id: str) -> PortfolioDetail:
        detail, _source_snapshot_id, _cash_coverage_complete = (
            build_publication_preview(publication_id)
        )
        return detail

    @router.delete("/publications/{publication_id}", response_model=PublicationConfig)
    def unpublish_portfolio(publication_id: str) -> PublicationConfig:
        if not runtime_settings.portfolio_publication_enabled:
            raise HTTPException(
                status_code=503, detail="Portfolio publication is disabled."
            )
        return runtime_repository.set_publication_enabled(publication_id, enabled=False)

    @router.get("/publications/{slug}", response_model=PortfolioDetail)
    def publication(slug: str) -> PortfolioDetail:
        if not runtime_settings.portfolio_publication_enabled:
            raise KeyError(slug)
        return runtime_repository.get_published_portfolio(slug)

    application.include_router(router)
    return application


app = create_app()
