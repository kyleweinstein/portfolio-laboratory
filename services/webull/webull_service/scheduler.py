from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from datetime import time as wall_time
from zoneinfo import ZoneInfo

import psycopg

from .adapters import OfficialWebullAdapter, application_read_only_gate_enabled
from .config import Settings
from .migrate import run_migrations
from .repository import PostgresRepository
from .sync import SyncService

_SCHEDULER_LOCK_ID = 7_326_175_112
_MARKET_TIME_ZONE = ZoneInfo("America/New_York")


def is_scheduled_sync_window(now: datetime | None = None) -> bool:
    """Run the Railway cron during the U.S. session and one post-close cycle."""
    current = (now or datetime.now(UTC)).astimezone(_MARKET_TIME_ZONE)
    if current.weekday() >= 5:
        return False
    return wall_time(9, 30) <= current.time().replace(tzinfo=None) <= wall_time(16, 20)


def run_cycle(settings: Settings) -> int:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for scheduled sync.")
    adapter = OfficialWebullAdapter(
        app_key=settings.webull_app_key,
        app_secret=settings.webull_app_secret,
        region=settings.webull_region,
        endpoint=settings.webull_endpoint,
        token_dir=settings.webull_token_dir,
    )
    if not application_read_only_gate_enabled(
        configured=settings.webull_read_only_adapter_enabled,
        adapter=adapter,
    ):
        raise RuntimeError("Application read-only Webull access is not enabled.")
    repository = PostgresRepository(settings.database_url)
    service = SyncService(
        adapter,
        repository,
        cash_activity_lookback_days=settings.cash_activity_lookback_days,
    )
    # A session advisory lock prevents two Railway workers or cron invocations
    # from writing duplicate snapshots for the same scheduled cycle.
    with psycopg.connect(settings.database_url, autocommit=True) as lock_connection:
        with lock_connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (_SCHEDULER_LOCK_ID,))
            acquired = bool(cursor.fetchone()[0])
        if not acquired:
            return 0
        try:
            return len(service.sync_all())
        finally:
            with lock_connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (_SCHEDULER_LOCK_ID,))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run read-only Webull account synchronization."
    )
    parser.add_argument(
        "--loop", action="store_true", help="Repeat at SYNC_INTERVAL_SECONDS."
    )
    args = parser.parse_args()
    settings = Settings.from_env()
    if (
        not settings.database_configured
        or not settings.webull_credentials_configured
        or not settings.webull_read_only_adapter_enabled
    ):
        print(
            "Webull scheduled sync is disabled until its private configuration "
            "and read-only adapter activation are complete."
        )
        return
    if not args.loop and not is_scheduled_sync_window():
        print(
            "Webull scheduled sync skipped outside the configured market-hours window."
        )
        return
    if settings.auto_migrate:
        run_migrations(settings.database_url)
    while True:
        if is_scheduled_sync_window():
            count = run_cycle(settings)
            print(f"Webull scheduled sync completed for {count} account(s).")
        if not args.loop:
            break
        time.sleep(settings.sync_interval_seconds)


if __name__ == "__main__":
    main()
