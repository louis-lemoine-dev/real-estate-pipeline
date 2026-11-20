"""One run of the ParuVendu pipeline: scrape, compare with the database, write.

This chains the pieces that already exist and were tested one by one:

    scrape -> clean -> read the stored listings -> sanity check -> classify
    (new / unchanged / modified / gone) -> price deltas -> insert the new ->
    update the re-seen -> mark the confirmed-gone

A run is all-or-nothing up to the moment of the first write: the site is
scraped completely and checked before anything is written, so a failed page
or a collapsed scrape never reaches the delisting logic. The three writes
each have their own transaction. A failure between them is safe to follow
with a re-run: listings already inserted then come back as "unchanged".

With dry_run=True the run reads the site and the database but writes nothing,
and reports what it WOULD do.

Scheduling (a daily run) is not here: that belongs to Phase 7.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd
from sqlalchemy import MetaData, Table, select
from sqlalchemy.engine import Connection, Engine

from real_estate_pipeline.common.db import run_in_transaction
from real_estate_pipeline.paruvendu.cleaning import clean_listings
from real_estate_pipeline.paruvendu.delist import _prepare_delisted_ids, mark_delisted_listings
from real_estate_pipeline.paruvendu.diff import (
    ScrapeAnomalyError,
    check_run_sanity,
    classify_listings,
)
from real_estate_pipeline.paruvendu.insert import insert_new_listings
from real_estate_pipeline.paruvendu.models import Listing
from real_estate_pipeline.paruvendu.orchestrator import scrape_listings
from real_estate_pipeline.paruvendu.price_events import compute_price_deltas
from real_estate_pipeline.paruvendu.update import update_listings

logger = logging.getLogger(__name__)

# The only search URL the project has: Toulon. Page 1 is this URL, page N is
# this URL with ?p=N (see paruvendu/scraper.py).
DEFAULT_BASE_URL = "https://www.paruvendu.fr/immobilier/vente/toulon/"

# One page every 1.5 seconds, so this is at most about 5 minutes of requests.
# It guards against a typo such as 10000, not against a real need.
MAX_PAGES_LIMIT = 200


@dataclass(frozen=True)
class ScrapeRunResult:
    """What one run found and did. In a dry run the "written" numbers are 0."""

    dry_run: bool
    scraped: int  # listings found on the site
    new: int
    unchanged: int
    modified: int  # price changed
    gone: int  # confirmed gone this run and not yet marked
    inserted: int = 0
    updated: int = 0
    price_events_logged: int = 0
    reinstated: int = 0  # marked gone before, seen again now
    marked_gone: int = 0


def _count(mask: Any) -> int:
    """How many rows of a True/False series are True (typed loosely: pandas-stubs
    cannot tell that comparing a column gives a Series)."""
    return int(mask.to_numpy().sum())


def validate_max_pages(max_pages: int) -> int:
    """Return max_pages, or raise ValueError if it is not between 1 and the limit."""
    if not 1 <= max_pages <= MAX_PAGES_LIMIT:
        raise ValueError(f"max pages must be between 1 and {MAX_PAGES_LIMIT}, got {max_pages}")
    return max_pages


def load_stored_listings(engine: Engine) -> pd.DataFrame:
    """Every stored listing, INCLUDING the ones already marked as gone.

    The marked ones must be there: otherwise a listing that comes back would
    look new and its insert would fail on the existing id. Read with a
    SQLAlchemy select, never raw SQL with a literal % through pandas under
    psycopg. An empty table gives an empty frame that still has the columns.
    """
    table = Table("listings", MetaData(), autoload_with=engine)

    def _read(connection: Connection) -> list[dict]:
        return [dict(row) for row in connection.execute(select(table)).mappings()]

    rows = run_in_transaction(engine, _read)
    return pd.DataFrame(rows, columns=[column.name for column in table.columns])


def run_scrape(
    engine: Engine,
    base_url: str,
    max_pages: int,
    dry_run: bool = False,
    scrape: Callable[[str, int], list[Listing]] = scrape_listings,
    now: pd.Timestamp | None = None,
) -> ScrapeRunResult:
    """Scrape the site and bring the database up to date (or report, if dry_run).

    `scrape` can be replaced so tests never touch the real site. `now` is one
    shared timestamp for every step of the run.

    Raises, before anything is written:
        ValueError: max_pages outside 1 to MAX_PAGES_LIMIT.
        ScrapeAnomalyError: the scrape found nothing at all, or found less than
            half of the listings currently stored as live (a broken scrape
            would otherwise look like a mass removal).
        Anything the scrape itself raises (a failed page, robots.txt refusal).
    """
    validate_max_pages(max_pages)
    if now is None:
        now = pd.Timestamp.now(tz="UTC")

    listings = scrape(base_url, max_pages)
    if not listings:
        raise ScrapeAnomalyError(
            "The scrape found no listings at all (blocked page, changed layout "
            "or wrong URL?). Nothing was written."
        )

    cleaned = clean_listings(listings)
    existing = load_stored_listings(engine)

    # Expected count: the listings currently stored as live. With none stored
    # (the very first run) the check is skipped by design.
    live_count = _count(existing["delisted_at"].isna())
    check_run_sanity(cleaned, expected_count=live_count)

    classified = compute_price_deltas(classify_listings(cleaned, existing, now=now))
    status = classified["diff_status"]
    new = _count(status == "new")
    unchanged = _count(status == "unchanged")
    modified = _count(status == "modified")
    gone = len(_prepare_delisted_ids(classified))

    marked_ids = existing.loc[existing["delisted_at"].notna(), "id"].tolist()
    seen_again = classified["diff_status"].isin(["unchanged", "modified"])
    would_reinstate = _count(seen_again & classified["id"].isin(marked_ids))

    logger.info(
        "Compared with the database: %d new, %d unchanged, %d price changed, %d gone",
        new,
        unchanged,
        modified,
        gone,
    )

    if dry_run:
        return ScrapeRunResult(
            dry_run=True,
            scraped=len(cleaned),
            new=new,
            unchanged=unchanged,
            modified=modified,
            gone=gone,
            reinstated=would_reinstate,
        )

    inserted = insert_new_listings(classified, engine, now=now)
    summary = update_listings(classified, engine, now=now)
    marked_gone = mark_delisted_listings(classified, engine, now=now)
    return ScrapeRunResult(
        dry_run=False,
        scraped=len(cleaned),
        new=new,
        unchanged=unchanged,
        modified=modified,
        gone=gone,
        inserted=inserted,
        updated=summary.listings_updated,
        price_events_logged=summary.price_events_logged,
        reinstated=summary.listings_reinstated,
        marked_gone=marked_gone,
    )


def format_report(result: ScrapeRunResult) -> str:
    """The run in plain words, ending with what was (or would be) written."""
    lines = [
        "DRY RUN: nothing was written to the database."
        if result.dry_run
        else "REAL RUN: changes were written to the database.",
        f"Listings found on the site: {result.scraped}",
        f"Compared with the database: {result.new} new | {result.unchanged} unchanged | "
        f"{result.modified} price changed | {result.gone} confirmed gone",
    ]
    if result.dry_run:
        lines.append(
            f"Would write: {result.new} inserted, {result.unchanged + result.modified} updated "
            f"({result.modified} price changes logged, {result.reinstated} coming back), "
            f"{result.gone} marked as gone"
        )
    else:
        lines.append(
            f"Written: {result.inserted} inserted, {result.updated} updated "
            f"({result.price_events_logged} price changes logged, {result.reinstated} came back), "
            f"{result.marked_gone} marked as gone"
        )
    return "\n".join(lines)
