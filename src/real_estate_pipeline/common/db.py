"""Shared database plumbing for real-estate-pipeline.

Centralizes how a database engine is built, and how a write is run with
retries, so every task talking to Supabase does so the same way with the
same reliability settings — instead of each script/task carrying its own
`create_engine(...)` call or its own retry loop.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError

load_dotenv()

logger = logging.getLogger(__name__)

# Tuned for a Postgres connection drop/reconnect (fast), not for a slow
# external API — see the DVF+ client's MAX_RETRIES/RETRY_BACKOFF_SECONDS
# for contrast. Kept separate and not shared with that one: these tune a
# genuinely different failure mode and shouldn't drift together by
# accident just because both are "retry constants".
#
# Centralized here (not left duplicated per module) because a second
# consumer (price_events.py) needed the exact same retry behavior as
# insert.py — the signal we'd said would justify promoting them.
DB_MAX_RETRIES = 3
DB_RETRY_BACKOFF_SECONDS = 1  # doubles each retry: 1s, 2s, 4s


def get_engine() -> Engine:
    """Build a SQLAlchemy engine for the Supabase Postgres database.

    Reads DATABASE_URL from the environment (populated via .env through
    load_dotenv()). Raises KeyError if it's not set — fails loudly rather
    than silently falling back to some default connection.

    pool_pre_ping=True checks each pooled connection with a lightweight
    ping before it's handed out, so a stale or dropped connection (e.g.
    one recycled server-side by Supabase's Session pooler) is caught and
    transparently replaced before a real query runs on it, instead of
    failing mid-operation.
    """
    database_url = os.environ["DATABASE_URL"]
    return create_engine(database_url, pool_pre_ping=True)


def run_in_transaction[T](engine: Engine, operation: Callable[[Connection], T]) -> T:
    """Run `operation(connection)` inside one transaction, retrying on
    transient connection errors.

    The single place the retry-with-backoff policy lives, so every write
    path (new listings, price-change events, listing updates) behaves
    identically instead of each carrying its own copy of the loop.

    - Everything `operation` does commits together, or rolls back together
      if anything raises — all-or-nothing.
    - OperationalError (dropped connection, transient pooler hiccup) is
      retried up to DB_MAX_RETRIES times with exponential backoff. Since
      the failed attempt's transaction was rolled back, `operation` is
      simply run again from scratch — it must therefore be safe to run
      more than once (no side effects outside the database).
    - Any other exception — notably IntegrityError, a data problem that
      would fail identically on every retry — propagates immediately,
      untouched, so the caller can log it with context and decide.

    Returns whatever `operation` returned on the successful attempt.
    """
    last_error: OperationalError | None = None
    for attempt in range(DB_MAX_RETRIES):
        try:
            with engine.begin() as connection:
                return operation(connection)
        except OperationalError as error:
            last_error = error
            if attempt < DB_MAX_RETRIES - 1:
                wait = DB_RETRY_BACKOFF_SECONDS * (2**attempt)
                logger.warning(
                    "DB transaction failed (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1,
                    DB_MAX_RETRIES,
                    error,
                    wait,
                )
                time.sleep(wait)

    raise (
        last_error
        if last_error is not None
        else RuntimeError("Transaction failed with no captured exception — this shouldn't happen")
    )
