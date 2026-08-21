from datetime import UTC, datetime

from webull_service.scheduler import is_scheduled_sync_window


def test_scheduler_runs_during_market_hours_and_after_close() -> None:
    assert is_scheduled_sync_window(datetime(2026, 8, 20, 13, 30, tzinfo=UTC))
    assert is_scheduled_sync_window(datetime(2026, 8, 20, 20, 15, tzinfo=UTC))


def test_scheduler_skips_nights_and_weekends() -> None:
    assert not is_scheduled_sync_window(datetime(2026, 8, 20, 22, 0, tzinfo=UTC))
    assert not is_scheduled_sync_window(datetime(2026, 8, 22, 15, 0, tzinfo=UTC))
