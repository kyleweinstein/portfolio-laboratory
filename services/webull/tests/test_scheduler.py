from datetime import UTC, datetime

from webull_service.scheduler import (
    is_after_close_publication_window,
    is_automatic_publication_window,
    is_reconciled_after_close_snapshot,
    is_scheduled_sync_window,
)


def test_scheduler_runs_during_market_hours_and_after_close() -> None:
    assert is_scheduled_sync_window(datetime(2026, 8, 20, 13, 30, tzinfo=UTC))
    assert is_scheduled_sync_window(datetime(2026, 8, 20, 20, 15, tzinfo=UTC))


def test_scheduler_skips_nights_and_weekends() -> None:
    assert not is_scheduled_sync_window(datetime(2026, 8, 20, 22, 15, tzinfo=UTC))
    assert not is_scheduled_sync_window(datetime(2026, 8, 22, 15, 0, tzinfo=UTC))


def test_publication_window_is_after_close_only() -> None:
    assert is_after_close_publication_window(datetime(2026, 8, 21, 20, 15, tzinfo=UTC))
    assert not is_after_close_publication_window(
        datetime(2026, 8, 21, 20, 5, tzinfo=UTC)
    )
    assert not is_after_close_publication_window(
        datetime(2026, 8, 21, 18, 0, tzinfo=UTC)
    )


def test_publication_requires_a_same_date_reconciled_close() -> None:
    now = datetime(2026, 8, 21, 20, 30, tzinfo=UTC)

    assert is_reconciled_after_close_snapshot(
        datetime(2026, 8, 21, 20, 0, tzinfo=UTC), now
    )
    assert not is_reconciled_after_close_snapshot(
        datetime(2026, 8, 21, 19, 59, tzinfo=UTC), now
    )
    assert not is_reconciled_after_close_snapshot(
        datetime(2026, 8, 20, 20, 0, tzinfo=UTC), now
    )


def test_automatic_publication_waits_for_late_provider_retries() -> None:
    assert not is_automatic_publication_window(
        datetime(2026, 8, 21, 21, 45, tzinfo=UTC)
    )
    assert is_automatic_publication_window(datetime(2026, 8, 21, 22, 0, tzinfo=UTC))
