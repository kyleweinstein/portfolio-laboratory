from pathlib import Path


def test_postgres_schema_enforces_snapshot_and_history_identity() -> None:
    sql = (Path(__file__).parents[1] / "migrations" / "0001_initial.sql").read_text(
        encoding="utf-8"
    )
    assert "PRIMARY KEY (snapshot_id, external_position_id)" in sql
    assert "PRIMARY KEY (account_id, external_activity_id)" in sql
    assert "PRIMARY KEY (account_id, external_order_id)" in sql
    assert "ON DELETE CASCADE" in sql
