"""Insert path for newly-scraped ParuVendu listings.

Takes the output of classify_listings() and persists rows tagged 'new'
into the `listings` table — the only write path in the diff-aware
ingestion flow that this module is responsible for.
"""

from __future__ import annotations

import logging
import time

import pandas as pd
from sqlalchemy import MetaData, Table
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError

from real_estate_pipeline.common.db import DB_MAX_RETRIES, DB_RETRY_BACKOFF_SECONDS

logger = logging.getLogger(__name__)


def _prepare_new_listing_records(classified: pd.DataFrame, now: pd.Timestamp) -> list[dict]:
    """Build the list of row-dicts to insert, from classify_listings' output.

    Pure data transform, no DB access — kept separate from
    insert_new_listings() so it's directly testable without a database.

    - Filters to rows classified 'new'.
    - Drops diff_status/old_price (not real `listings` columns).
    - Sets first_seen_at = last_seen_at = `now`, a single shared timestamp
      for the whole batch, not one generated per row.
    - Converts NaN to None so nullable columns get a real SQL NULL
      instead of a NaN silently landing in the insert.
    """
    new_rows = classified.loc[classified["diff_status"] == "new"].drop(
        columns=["diff_status", "old_price"]
    )
    if new_rows.empty:
        return []

    new_rows = new_rows.copy()

    # .where() alone doesn't reliably turn NaN into None: a float64 column
    # can't hold None, so pandas silently keeps NaN rather than converting
    # it — force object dtype first so None can actually land.
    prepared = new_rows.astype(object).where(pd.notna(new_rows), None)
    records = prepared.to_dict(orient="records")

    # Set first_seen_at/last_seen_at directly on the output dicts, after
    # to_dict() — assigning through a DataFrame column doesn't work here,
    # since pandas re-wraps any datetime-like column back into its own
    # Timestamp type internally regardless of what native type is first
    # assigned. A plain datetime.datetime is what every DBAPI driver is
    # guaranteed to accept as a bound parameter; a pandas Timestamp isn't
    # universally guaranteed to be.
    now_native = now.to_pydatetime()
    for record in records:
        record["first_seen_at"] = now_native
        record["last_seen_at"] = now_native

    return records


def insert_new_listings(
    classified: pd.DataFrame,
    engine: Engine,
    now: pd.Timestamp | None = None,
) -> int:
    """Insert rows classified 'new' (from classify_listings) into `listings`.

    One batched INSERT wrapped in a single transaction — all-or-nothing,
    matching scripts/apply_migration.py's existing transactional pattern.

    Retries with backoff on OperationalError (transient connection issues)
    up to DB_MAX_RETRIES times. Raises immediately, no retry, on
    IntegrityError (e.g. a 'new'-classified row that turns out to already
    exist) — retrying an identical malformed batch would just fail again,
    identically, so it's logged and re-raised instead of masked.

    Returns the number of rows inserted (0 if there was nothing to do —
    no transaction is opened in that case).
    """
    if now is None:
        now = pd.Timestamp.now(tz="UTC")

    records = _prepare_new_listing_records(classified, now)
    if not records:
        return 0

    metadata = MetaData()
    listings_table = Table("listings", metadata, autoload_with=engine)

    last_error: OperationalError | None = None
    for attempt in range(DB_MAX_RETRIES):
        try:
            with engine.begin() as connection:
                connection.execute(listings_table.insert(), records)
            return len(records)
        except IntegrityError:
            logger.error(
                "Integrity error inserting new listings batch (ids=%s) — "
                "a 'new'-classified row likely already exists. Aborting, "
                "not retrying.",
                [r["id"] for r in records],
            )
            raise
        except OperationalError as error:
            last_error = error
            if attempt < DB_MAX_RETRIES - 1:
                wait = DB_RETRY_BACKOFF_SECONDS * (2**attempt)
                logger.warning(
                    "DB insert failed (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1,
                    DB_MAX_RETRIES,
                    error,
                    wait,
                )
                time.sleep(wait)

    raise (
        last_error
        if last_error is not None
        else RuntimeError("Insert failed with no captured exception — this shouldn't happen")
    )
