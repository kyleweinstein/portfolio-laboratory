from __future__ import annotations

import os
from dataclasses import dataclass


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
    adapter_kind: str = "official"
    cash_activity_lookback_days: int = 45
    sync_interval_seconds: int = 900
    auto_migrate: bool = True

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
            adapter_kind=os.getenv("WEBULL_ADAPTER", "official").strip().lower(),
            cash_activity_lookback_days=_integer("CASH_ACTIVITY_LOOKBACK_DAYS", 45, 1),
            sync_interval_seconds=_integer("SYNC_INTERVAL_SECONDS", 900, 60),
            auto_migrate=_boolean("AUTO_MIGRATE", True),
        )

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url)

    @property
    def webull_credentials_configured(self) -> bool:
        return bool(self.webull_app_key and self.webull_app_secret)
