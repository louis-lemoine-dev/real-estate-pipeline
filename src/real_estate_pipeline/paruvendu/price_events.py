"""Price-change delta computation and event logging for ParuVendu listings.

Extends classify_listings' output with the absolute (€) and relative (%)
price delta for rows classified 'modified' (3.4.1), and writes one
append-only event row per detected change into `price_change_events`
(3.4.2) — the only place old_price is ever persisted, since 3.3.3's
update path overwrites listings.price right after.
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


def compute_price_deltas(classified: pd.DataFrame) -> pd.DataFrame:
    """Add delta_eur and delta_pct columns to classify_listings' output.

    Only rows classified 'modified' get real values — new/unchanged/
    delisted rows have no old_price to diff against, so both columns are
    None for them.

    delta_eur = price - old_price (matches price_change_events.delta_eur,
        an integer column, since ParuVendu prices are always whole euros).
    delta_pct = (price - old_price) / old_price * 100 (matches
        price_change_events.delta_pct, numeric). A positive value is a
        price increase, negative is a decrease — the sign itself carries
        the meaning, no special-casing either direction.

    Guards against division by zero: if old_price is 0 (shouldn't happen
    in practice — price is NOT NULL in the schema — but not guaranteed to
    never happen), delta_pct is None rather than raising or producing inf,
    since a percentage change from zero is mathematically undefined.
    delta_eur is still computed in that case, since it doesn't involve
    division.

    No rounding applied — full precision is stored; rounding for display
    is a presentation concern, not a storage one.
    """
    result = classified.copy()
    is_modified = result["diff_status"] == "modified"

    delta_eur = pd.Series(pd.NA, index=result.index, dtype="object")
    delta_pct = pd.Series(pd.NA, index=result.index, dtype="object")

    modified_rows = result.loc[is_modified]
    delta_eur.loc[is_modified] = modified_rows["price"] - modified_rows["old_price"]

    nonzero_old_price = is_modified & (result["old_price"] != 0)
    nonzero_rows = result.loc[nonzero_old_price]
    delta_pct.loc[nonzero_old_price] = (
        (nonzero_rows["price"] - nonzero_rows["old_price"]) / nonzero_rows["old_price"] * 100
    )

    result["delta_eur"] = delta_eur
    result["delta_pct"] = delta_pct
    return result


def _prepare_price_change_event_records(classified: pd.DataFrame) -> list[dict]:
    """Build the list of row-dicts to insert into price_change_events.

    Pure data transform, no DB access — kept separate from
    insert_price_change_events() so it's directly testable without a
    database, same pattern as insert.py's _prepare_new_listing_records.

    - Filters to rows classified 'modified' (the only ones with a real
      old_price/delta to log).
    - Maps to price_change_events' actual columns: listing_id, old_price,
      new_price, delta_eur, delta_pct. `id` and `detected_at` are left
      out — both are DB-side defaults (identity PK, now()).
    - Converts NaN/NA to None, same reasoning as insert.py: a float64
      column can't hold None on its own, silently keeping NaN instead
      unless object dtype is forced first.
    """
    modified_rows = classified.loc[classified["diff_status"] == "modified"]
    if modified_rows.empty:
        return []

    events = pd.DataFrame(
        {
            "listing_id": modified_rows["id"],
            "old_price": modified_rows["old_price"],
            "new_price": modified_rows["price"],
            "delta_eur": modified_rows["delta_eur"],
            "delta_pct": modified_rows["delta_pct"],
        }
    )

    prepared = events.astype(object).where(pd.notna(events), None)
    return prepared.to_dict(orient="records")


def insert_price_change_events(classified: pd.DataFrame, engine: Engine) -> int:
    """Insert one event row per row classified 'modified' into
    price_change_events.

    Same transactional + retry pattern as insert.py's insert_new_listings:
    one batched INSERT wrapped in a single transaction (all-or-nothing),
    retried on OperationalError (transient connection issues), raised
    immediately with no retry on IntegrityError.

    Purely additive — this table is never updated or deleted from, so
    there's no update-vs-insert branch to consider here.

    Returns the number of events inserted (0 if there was nothing to do —
    no transaction is opened in that case).
    """
    records = _prepare_price_change_event_records(classified)
    if not records:
        return 0

    metadata = MetaData()
    events_table = Table("price_change_events", metadata, autoload_with=engine)

    last_error: OperationalError | None = None
    for attempt in range(DB_MAX_RETRIES):
        try:
            with engine.begin() as connection:
                connection.execute(events_table.insert(), records)
            return len(records)
        except IntegrityError:
            logger.error(
                "Integrity error inserting price-change events "
                "(listing_ids=%s). Aborting, not retrying.",
                [r["listing_id"] for r in records],
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
