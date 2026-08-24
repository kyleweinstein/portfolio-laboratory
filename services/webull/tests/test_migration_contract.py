from pathlib import Path


def test_postgres_schema_enforces_snapshot_and_history_identity() -> None:
    sql = (Path(__file__).parents[1] / "migrations" / "0001_initial.sql").read_text(
        encoding="utf-8"
    )
    assert "PRIMARY KEY (snapshot_id, external_position_id)" in sql
    assert "PRIMARY KEY (account_id, external_activity_id)" in sql
    assert "PRIMARY KEY (account_id, external_order_id)" in sql
    assert "ON DELETE CASCADE" in sql


def test_verification_schema_enforces_one_durable_running_attempt() -> None:
    sql = (
        Path(__file__).parents[1] / "migrations" / "0002_verification_attempts.sql"
    ).read_text(encoding="utf-8")
    assert "state IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'TIMED_OUT')" in sql
    assert "lease_expires_at TIMESTAMPTZ NOT NULL" in sql
    assert "CREATE UNIQUE INDEX" in sql
    assert "WHERE state = 'RUNNING'" in sql


def test_sync_attempt_schema_persists_cash_activity_coverage() -> None:
    sql = (
        Path(__file__).parents[1] / "migrations" / "0003_sync_attempt_coverage.sql"
    ).read_text(encoding="utf-8")
    assert "cash_activities_complete BOOLEAN" in sql
    assert "message VARCHAR(500)" in sql
    assert "cash_activity_gap_start TIMESTAMPTZ" in sql
    assert "cash_activity_gap_end TIMESTAMPTZ" in sql
    assert "cash_activity_gap_bounds_check" in sql
    assert "SET message = 'Webull synchronization failed.'" in sql
    assert "SET error_message = NULL" in sql
    assert "LEFT(error_message" not in sql
    assert "sync_runs_latest_idx" in sql

    repository_source = (
        Path(__file__).parents[1] / "webull_service" / "repository.py"
    ).read_text(encoding="utf-8")
    assert "COALESCE(message, error_message)" not in repository_source
    assert "SELECT MAX(last_synced_at)" not in repository_source
