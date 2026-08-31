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


def test_multibroker_migration_is_additive_and_separates_public_projection() -> None:
    sql = (
        Path(__file__).parents[1] / "migrations" / "0004_multibroker_publications.sql"
    ).read_text(encoding="utf-8")

    assert "provider IN ('webull', 'plaid_m1', 'schwab')" in sql
    assert "internal_account_id UUID" in sql
    assert "broker_connection_credentials" in sql
    assert "PGP_SYM" not in sql  # encryption happens in parameterized repository SQL
    assert "REVOKE ALL ON broker_connection_credentials FROM PUBLIC" in sql
    assert "CREATE TABLE IF NOT EXISTS published_portfolios" in sql
    assert "CREATE TABLE IF NOT EXISTS publication_revisions" in sql
    assert "CREATE TABLE IF NOT EXISTS published_holdings" in sql
    assert "CREATE TABLE IF NOT EXISTS published_performance_points" in sql
    assert "kind IN ('security', 'cash_margin', 'other')" in sql
    public_tail = sql.split("CREATE TABLE IF NOT EXISTS published_holdings", 1)[1]
    assert "quantity" not in public_tail
    assert "market_value" not in public_tail
    assert "publication_revisions_one_published_idx" in sql


def test_cash_activity_coverage_migration_records_explicit_bounds() -> None:
    sql = (
        Path(__file__).parents[1]
        / "migrations"
        / "0005_cash_activity_coverage_bounds.sql"
    ).read_text(encoding="utf-8")

    assert "cash_activity_coverage_start TIMESTAMPTZ" in sql
    assert "cash_activity_coverage_end TIMESTAMPTZ" in sql
    assert "cash_activity_coverage_bounds_check" in sql
    assert "cash_activity_coverage_start <= cash_activity_coverage_end" in sql


def test_discord_session_migration_keeps_oauth_material_server_side() -> None:
    sql = (
        Path(__file__).parents[1] / "migrations" / "0007_discord_viewer_sessions.sql"
    ).read_text(encoding="utf-8")

    assert "session_id_hash CHAR(64) PRIMARY KEY" in sql
    assert "encrypted_access_token BYTEA" in sql
    assert "encrypted_refresh_token BYTEA" in sql
    assert "REVOKE ALL ON discord_viewer_sessions FROM PUBLIC" in sql


def test_provider_refresh_webhooks_have_a_durable_invalidation_record() -> None:
    sql = (
        Path(__file__).parents[1] / "migrations" / "0008_provider_refresh_requests.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS provider_refresh_requests" in sql
    assert "provider TEXT PRIMARY KEY" in sql
    assert "processed_at TIMESTAMPTZ" in sql
    assert "WHERE processed_at IS NULL" in sql


def test_statement_import_migration_is_owner_scoped_atomic_and_private() -> None:
    sql = (
        Path(__file__).parents[1] / "migrations" / "0009_statement_imports.sql"
    ).read_text(encoding="utf-8")

    assert "owner_github_id" in sql
    assert 'account_handle VARCHAR(64) COLLATE "C"' in sql
    assert "brokerage_accounts_owner_handle_idx" in sql
    assert "CREATE TABLE IF NOT EXISTS statement_import_batches" in sql
    assert "batch_id UUID PRIMARY KEY" in sql
    assert "payload_sha256 CHAR(64)" in sql
    assert "CREATE TABLE IF NOT EXISTS statement_import_sources" in sql
    assert "source_content_sha256 CHAR(64)" in sql
    assert "CREATE TABLE IF NOT EXISTS statement_external_flows" in sql
    assert "valuation_status IN ('reported', 'unavailable')" in sql
    assert "REVOKE ALL ON statement_import_batches FROM PUBLIC" in sql
    assert "raw_pdf" not in sql.lower()
    assert "extracted_text" not in sql.lower()
