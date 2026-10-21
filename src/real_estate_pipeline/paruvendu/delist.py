"""Recording that a listing has gone from the site.

classify_listings() decides which stored listings are confirmed gone (absent
from today's scrape AND missing long enough to be a second consecutive miss;
a broken scrape never gets this far, check_run_sanity() stops it first). This
module acts on that decision by writing the date into listings.delisted_at.

Nothing is ever deleted: a delisted listing stays in the table, marked, so
that later stages (dashboard, AI analysis) can skip it.
"""

from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import MetaData, Table, bindparam
from sqlalchemy.engine import Connection, Engine

from real_estate_pipeline.common.db import run_in_transaction

logger = logging.getLogger(__name__)


def _prepare_delisted_ids(classified: pd.DataFrame) -> list[str]:
    """Ids of listings that are confirmed gone and not yet marked.

    Pure data transform, no DB access.

    classify_listings keeps tagging a gone listing 'delisted' on every
    later run (it is still absent), so listings that already carry a
    delisted_at are left out here: the date records when we first
    concluded the listing was gone, and must never be moved forward.
    If the frame has no delisted_at column at all, nothing is excluded
    (the database guard in mark_delisted_listings still protects the date).
    """
    gone = classified.loc[classified["diff_status"] == "delisted"]
    if "delisted_at" in gone.columns:
        gone = gone.loc[gone["delisted_at"].isna()]
    return [str(listing_id) for listing_id in gone["id"]]


def mark_delisted_listings(
    classified: pd.DataFrame,
    engine: Engine,
    now: pd.Timestamp | None = None,
) -> int:
    """Write delisted_at = `now` on listings confirmed gone and not yet marked.

    `now` is the moment this run concluded the listing was gone, not the
    moment it actually vanished from the site (which is a day or two
    earlier, since a listing must be missing twice in a row to be
    confirmed). One shared timestamp for the whole run.

    Only the first conclusion is recorded: the UPDATE itself carries
    `delisted_at IS NULL`, so an existing date can never be overwritten,
    even if a caller hands over rows that are already marked. Runs in one
    transaction with the standard retry policy (common.db.run_in_transaction).

    Returns the number of listings it was asked to mark (0, with no
    transaction opened, if there was nothing to do).
    """
    if now is None:
        now = pd.Timestamp.now(tz="UTC")

    ids = _prepare_delisted_ids(classified)
    if not ids:
        return 0

    listings_table = Table("listings", MetaData(), autoload_with=engine)
    statement = (
        listings_table.update()
        .where(listings_table.c.id == bindparam("b_id"))
        .where(listings_table.c.delisted_at.is_(None))
        .values(delisted_at=bindparam("b_delisted_at"))
    )
    now_native = now.to_pydatetime()
    parameters = [{"b_id": listing_id, "b_delisted_at": now_native} for listing_id in ids]

    def _apply(connection: Connection) -> None:
        connection.execute(statement, parameters)

    run_in_transaction(engine, _apply)

    logger.info("Marked %d listing(s) as delisted: %s", len(ids), ids)
    return len(ids)
