"""Update path for listings that are still present in today's scrape.

Takes the output of classify_listings() + compute_price_deltas() and, for
every listing seen again today — whether its price changed or not —
refreshes its stored row and its last_seen_at date. For listings whose
price changed, the price-change event is written first, in the same
transaction, because overwriting listings.price destroys the old price.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import MetaData, Table, bindparam
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from real_estate_pipeline.common.db import run_in_transaction
from real_estate_pipeline.paruvendu.price_events import write_price_change_events

logger = logging.getLogger(__name__)

# Columns that exist on classify_listings' output but must never be copied
# onto the listings row: the diff bookkeeping, plus the columns that
# belong to the *stored* row rather than to today's scrape.
# - first_seen_at: set once at insert, never touched again.
# - delisted_at: owned by the delisting logic, not by the scrape.
# - last_seen_at: set explicitly to this run's timestamp instead.
_NON_UPDATABLE_COLUMNS = (
    "diff_status",
    "old_price",
    "delta_eur",
    "delta_pct",
    "first_seen_at",
    "last_seen_at",
    "delisted_at",
)


@dataclass(frozen=True)
class UpdateSummary:
    """What an update_listings run actually did."""

    listings_updated: int
    price_events_logged: int


def _prepare_listing_update_records(classified: pd.DataFrame, now: pd.Timestamp) -> list[dict]:
    """Build the row-dicts to write, from classify_listings' output.

    Pure data transform, no DB access — kept separate from
    update_listings() so it's directly testable without a database.

    - Keeps rows classified 'unchanged' or 'modified': every listing seen
      again today. ('new' rows go through the insert path; 'delisted' rows
      aren't in today's scrape at all.)
    - Drops the bookkeeping columns (see _NON_UPDATABLE_COLUMNS).
    - Sets last_seen_at to `now`, one shared timestamp for the whole run.
    - Converts NaN to None so a field the scraper didn't find becomes a
      real SQL NULL (same reasoning as the insert path).
    """
    seen_again = classified.loc[classified["diff_status"].isin(["unchanged", "modified"])]
    if seen_again.empty:
        return []

    updates = seen_again.drop(
        columns=[c for c in _NON_UPDATABLE_COLUMNS if c in seen_again.columns]
    )
    prepared = updates.astype(object).where(pd.notna(updates), None)
    records = prepared.to_dict(orient="records")

    now_native = now.to_pydatetime()
    for record in records:
        record["last_seen_at"] = now_native

    return records


def update_listings(
    classified: pd.DataFrame,
    engine: Engine,
    now: pd.Timestamp | None = None,
) -> UpdateSummary:
    """Refresh every listing seen again today, logging price changes first.

    Everything happens in ONE transaction, in this order:
      1. For listings whose price changed, write the price-change event
         (captures the old price before it is overwritten).
      2. Update every re-seen listing: all scraped fields plus
         last_seen_at. first_seen_at and delisted_at are left alone.
    If either step fails, neither is kept — no event without its update,
    and no update without its event.

    Unchanged listings are updated too, on purpose: refreshing
    last_seen_at is what stops a long-unchanged listing from looking
    long-gone the first day it is missing from a scrape.

    Retries on transient connection errors (see common.db.run_in_transaction).
    Raises immediately on IntegrityError, logged with context first.

    Returns an UpdateSummary; both counts are 0 (and no transaction is
    opened) if there was nothing to do.
    """
    if now is None:
        now = pd.Timestamp.now(tz="UTC")

    records = _prepare_listing_update_records(classified, now)
    if not records:
        return UpdateSummary(listings_updated=0, price_events_logged=0)

    listings_table = Table("listings", MetaData(), autoload_with=engine)

    # Bind-parameter names get a "b_" prefix: SQLAlchemy reserves bare
    # column names as parameter names inside UPDATE ... SET.
    value_columns = [column for column in records[0] if column != "id"]
    update_statement = (
        listings_table.update()
        .where(listings_table.c.id == bindparam("b_id"))
        .values({column: bindparam(f"b_{column}") for column in value_columns})
    )
    parameters = [
        {"b_id": record["id"], **{f"b_{c}": record[c] for c in value_columns}} for record in records
    ]

    def _apply(connection: Connection) -> int:
        events_logged = write_price_change_events(connection, classified)
        connection.execute(update_statement, parameters)
        return events_logged

    try:
        events_logged = run_in_transaction(engine, _apply)
    except IntegrityError:
        logger.error(
            "Integrity error updating listings (ids=%s). Transaction rolled "
            "back, no price-change events kept. Aborting, not retrying.",
            [r["id"] for r in records],
        )
        raise

    return UpdateSummary(listings_updated=len(records), price_events_logged=events_logged)
