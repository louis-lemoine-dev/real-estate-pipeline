"""
Apply a SQL migration file to the Supabase database.

Usage:
    poetry run python scripts/apply_migration.py db/migrations/0001_initial_schema.sql
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()


def apply_migration(migration_path: Path) -> None:
    sql = migration_path.read_text()

    # Split into individual statements rather than sending the whole file
    # as one execute() call - safer and driver-agnostic, since not every
    # DBAPI driver reliably supports multiple ;-separated statements in
    # a single call.
    statements = [s.strip() for s in sql.split(";") if s.strip()]

    database_url = os.environ["DATABASE_URL"]
    engine = create_engine(database_url)

    with engine.begin() as connection:
        # engine.begin() wraps everything in one transaction: if any
        # statement fails, all of it rolls back rather than leaving
        # the schema half-applied.
        for statement in statements:
            connection.execute(text(statement))

    print(f"Applied migration: {migration_path.name} ({len(statements)} statements)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: poetry run python scripts/apply_migration.py <path-to-migration.sql>")
        sys.exit(1)

    apply_migration(Path(sys.argv[1]))
