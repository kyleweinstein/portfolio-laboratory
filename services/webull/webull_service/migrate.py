from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg

from .config import Settings


def migration_directory() -> Path:
    return Path(__file__).resolve().parents[1] / "migrations"


def run_migrations(database_url: str, directory: Path | None = None) -> list[str]:
    if not database_url:
        raise RuntimeError("DATABASE_URL is required to run Webull service migrations.")
    root = directory or migration_directory()
    applied_now: list[str] = []
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    migration_name TEXT PRIMARY KEY,
                    sha256 CHAR(64) NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        for path in sorted(root.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT sha256 FROM schema_migrations WHERE migration_name = %s",
                    (path.name,),
                )
                existing = cursor.fetchone()
                if existing:
                    if existing[0] != checksum:
                        raise RuntimeError(
                            f"Applied migration {path.name} has changed."
                        )
                    continue
                cursor.execute(sql, prepare=False)
                cursor.execute(
                    "INSERT INTO schema_migrations (migration_name, sha256) VALUES (%s, %s)",
                    (path.name, checksum),
                )
                applied_now.append(path.name)
    return applied_now


def main() -> None:
    settings = Settings.from_env()
    applied = run_migrations(settings.database_url)
    print(f"Applied {len(applied)} Webull service migration(s).")


if __name__ == "__main__":
    main()
