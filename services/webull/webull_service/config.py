from __future__ import annotations

import os
from dataclasses import dataclass, field


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _integer(name: str, default: int, minimum: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return max(minimum, int(value))


def _number(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    return min(maximum, max(minimum, float(value)))


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    internal_api_token: str
    portfolio_owner_github_id: str
    webull_app_key: str
    webull_app_secret: str
    webull_region: str = "us"
    webull_endpoint: str = "api.webull.com"
    webull_token_dir: str | None = None
    webull_read_only_adapter_enabled: bool = False
    adapter_kind: str = "official"
    cash_activity_lookback_days: int = 45
    sync_interval_seconds: int = 900
    auto_migrate: bool = True
    plaid_m1_integration_enabled: bool = False
    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_environment: str = "sandbox"
    plaid_access_token: str = ""
    plaid_m1_institution_id: str = ""
    plaid_redirect_uri: str = ""
    schwab_integration_enabled: bool = False
    publication_weight_tolerance_bps: int = 5
    broker_credential_encryption_key: str = ""
    discord_session_encryption_key: str = ""
    portfolio_publication_enabled: bool = False
    auto_publish_after_close_enabled: bool = False
    published_analytics_enabled: bool = True
    published_analytics_years: int = 3
    published_analytics_risk_free_rate: float = 0.04
    published_analytics_rebalance_band_percent: float = 2.5
    published_analytics_drift_days: int = 63
    published_analytics_concurrency: int = 6
    m1_statement_import_enabled: bool = False
    m1_statement_output_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.getenv("DATABASE_URL", "").strip(),
            internal_api_token=os.getenv("INTERNAL_API_TOKEN", "").strip(),
            portfolio_owner_github_id=os.getenv(
                "PORTFOLIO_OWNER_GITHUB_ID", ""
            ).strip(),
            webull_app_key=os.getenv("WEBULL_APP_KEY", "").strip(),
            webull_app_secret=os.getenv("WEBULL_APP_SECRET", "").strip(),
            webull_region=os.getenv("WEBULL_REGION", "us").strip().lower(),
            webull_endpoint=os.getenv("WEBULL_API_ENDPOINT", "api.webull.com").strip(),
            webull_token_dir=os.getenv("WEBULL_OPENAPI_TOKEN_DIR") or None,
            # The legacy broker-scope assertion is intentionally not accepted:
            # this flag activates only Portfolio Lab's application boundary.
            webull_read_only_adapter_enabled=_boolean(
                "WEBULL_READ_ONLY_ADAPTER_ENABLED", False
            ),
            adapter_kind=os.getenv("WEBULL_ADAPTER", "official").strip().lower(),
            cash_activity_lookback_days=_integer("CASH_ACTIVITY_LOOKBACK_DAYS", 45, 1),
            sync_interval_seconds=_integer("SYNC_INTERVAL_SECONDS", 900, 60),
            auto_migrate=_boolean("AUTO_MIGRATE", True),
            plaid_m1_integration_enabled=_boolean(
                "PLAID_M1_INTEGRATION_ENABLED", False
            ),
            plaid_client_id=os.getenv("PLAID_CLIENT_ID", "").strip(),
            plaid_secret=os.getenv("PLAID_SECRET", "").strip(),
            plaid_environment=os.getenv("PLAID_ENV", "sandbox").strip().lower(),
            plaid_access_token=os.getenv("PLAID_M1_ACCESS_TOKEN", "").strip(),
            plaid_m1_institution_id=os.getenv("PLAID_M1_INSTITUTION_ID", "").strip(),
            plaid_redirect_uri=os.getenv("PLAID_REDIRECT_URI", "").strip(),
            schwab_integration_enabled=_boolean("SCHWAB_INTEGRATION_ENABLED", False),
            publication_weight_tolerance_bps=_integer(
                "PUBLICATION_WEIGHT_TOLERANCE_BPS", 5, 0
            ),
            broker_credential_encryption_key=os.getenv(
                "BROKER_CREDENTIAL_ENCRYPTION_KEY", ""
            ).strip(),
            discord_session_encryption_key=os.getenv(
                "DISCORD_SESSION_ENCRYPTION_KEY", ""
            ).strip(),
            portfolio_publication_enabled=_boolean(
                "PORTFOLIO_PUBLICATION_ENABLED", False
            ),
            auto_publish_after_close_enabled=_boolean(
                "AUTO_PUBLISH_AFTER_CLOSE_ENABLED", False
            ),
            published_analytics_enabled=_boolean("PUBLISHED_ANALYTICS_ENABLED", True),
            published_analytics_years=(
                int(os.getenv("PUBLISHED_ANALYTICS_YEARS", "3"))
                if int(os.getenv("PUBLISHED_ANALYTICS_YEARS", "3")) in {1, 3, 5}
                else 3
            ),
            published_analytics_risk_free_rate=_number(
                "PUBLISHED_ANALYTICS_RISK_FREE_RATE", 0.04, -1.0, 1.0
            ),
            published_analytics_rebalance_band_percent=_number(
                "PUBLISHED_ANALYTICS_REBALANCE_BAND_PERCENT", 2.5, 0.0, 100.0
            ),
            published_analytics_drift_days=_integer(
                "PUBLISHED_ANALYTICS_DRIFT_DAYS", 63, 1
            ),
            published_analytics_concurrency=min(
                12,
                _integer("PUBLISHED_ANALYTICS_CONCURRENCY", 6, 1),
            ),
            m1_statement_import_enabled=_boolean("M1_STATEMENT_IMPORT_ENABLED", False),
            m1_statement_output_key=os.getenv("M1_STATEMENT_OUTPUT_KEY", "").strip(),
        )

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url)

    @property
    def webull_credentials_configured(self) -> bool:
        return bool(self.webull_app_key and self.webull_app_secret)

    @property
    def plaid_credentials_configured(self) -> bool:
        return bool(self.plaid_client_id and self.plaid_secret)

    @property
    def plaid_m1_configured(self) -> bool:
        return bool(self.plaid_credentials_configured and self.plaid_access_token)
