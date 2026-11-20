"""Tests for src/real_estate_pipeline/paruvendu/runner.py.

The scrape is always a fake: no test touches the real site. The database is
an in-memory SQLite one shaped like the real `listings` table.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

import pandas as pd
import pytest
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    select,
    update,
)
from sqlalchemy.engine import Engine

from real_estate_pipeline.paruvendu.diff import ScrapeAnomalyError
from real_estate_pipeline.paruvendu.models import Listing
from real_estate_pipeline.paruvendu.runner import (
    MAX_PAGES_LIMIT,
    ScrapeRunResult,
    format_report,
    load_stored_listings,
    run_scrape,
    validate_max_pages,
)

URL = "https://example.test/search/"
NOW = cast(pd.Timestamp, pd.Timestamp("2026-08-30T00:00:00Z"))
# Cast: pandas-stubs can type Timestamp arithmetic as Timestamp | NaTType.
NEXT_DAY = cast(pd.Timestamp, NOW + pd.Timedelta(days=1))
DAY_3 = cast(pd.Timestamp, NOW + pd.Timedelta(days=2))
DAY_4 = cast(pd.Timestamp, NOW + pd.Timedelta(days=3))
DAY_5 = cast(pd.Timestamp, NOW + pd.Timedelta(days=4))


def _naive(timestamp: pd.Timestamp) -> datetime:
    """What a timestamptz round-trips to through SQLite's DateTime type."""
    return timestamp.to_pydatetime().replace(tzinfo=None)


def _listing(listing_id: str, price: int = 100000) -> Listing:
    return Listing(
        id=listing_id,
        url=f"https://x/{listing_id}",
        property_type="Appartement",
        rooms=2,
        surface_m2=40.0,
        chambres=1,
        dpe="D",
        terrain_m2=None,
        has_garage=False,
        has_ascenseur=False,
        has_balcon=False,
        price=price,
        has_asterisk=False,
        location="Toulon (83)",
        amenities=[],
        description="desc",
    )


class _FakeSite:
    """A fake scrape that returns fixed listings and remembers how it was called."""

    def __init__(self, listings: tuple[Listing, ...]) -> None:
        self.listings = listings
        self.calls: list[tuple[str, int]] = []

    def __call__(self, base_url: str, max_pages: int) -> list[Listing]:
        self.calls.append((base_url, max_pages))
        return list(self.listings)


def _site(*listings: Listing) -> _FakeSite:
    return _FakeSite(listings)


@pytest.fixture
def sqlite_engine() -> Engine:
    """In-memory `listings` and `price_change_events` tables shaped like the
    real ones. The only stand-in: `amenities` is JSON (SQLite has no arrays).
    """
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "listings",
        metadata,
        Column("id", String, primary_key=True),
        Column("url", String, nullable=False),
        Column("property_type", String, nullable=True),
        Column("rooms", Integer, nullable=True),
        Column("surface_m2", Float, nullable=True),
        Column("chambres", Integer, nullable=True),
        Column("dpe", String, nullable=True),
        Column("terrain_m2", Float, nullable=True),
        Column("has_garage", Boolean, nullable=False, server_default="0"),
        Column("has_ascenseur", Boolean, nullable=False, server_default="0"),
        Column("has_balcon", Boolean, nullable=False, server_default="0"),
        Column("price", Integer, nullable=False),
        Column("has_asterisk", Boolean, nullable=False, server_default="0"),
        Column("location", String, nullable=True),
        Column("amenities", JSON, nullable=True),
        Column("description", String, nullable=True),
        Column("first_seen_at", DateTime, nullable=False),
        Column("last_seen_at", DateTime, nullable=False),
        Column("delisted_at", DateTime, nullable=True),
    )
    Table(
        "price_change_events",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("listing_id", String, nullable=False),
        Column("old_price", Integer, nullable=False),
        Column("new_price", Integer, nullable=False),
        Column("delta_eur", Integer, nullable=False),
        Column("delta_pct", Float, nullable=True),
    )
    metadata.create_all(engine)
    return engine


def _row(engine: Engine, listing_id: str) -> dict:
    table = Table("listings", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        result = connection.execute(select(table).where(table.c.id == listing_id))
        return dict(result.mappings().one())


def _all_rows(engine: Engine, table_name: str) -> list[dict]:
    table = Table(table_name, MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(table)).mappings()]


def _mark_gone(engine: Engine, *listing_ids: str) -> None:
    """Directly mark listings as gone, to build a state the runner cannot
    reach in one step (its safety check refuses a scrape that shrinks fast)."""
    table = Table("listings", MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(
            update(table).where(table.c.id.in_(listing_ids)).values(delisted_at=_naive(NOW))
        )


def _run(engine: Engine, site, now: pd.Timestamp = NOW, **kwargs) -> ScrapeRunResult:
    return run_scrape(engine, URL, 3, scrape=site, now=now, **kwargs)


# --- validate_max_pages ---


@pytest.mark.parametrize("pages", [0, -1, MAX_PAGES_LIMIT + 1])
def test_max_pages_outside_the_range_is_refused(pages: int):
    with pytest.raises(ValueError, match="between 1 and"):
        validate_max_pages(pages)


@pytest.mark.parametrize("pages", [1, 5, MAX_PAGES_LIMIT])
def test_max_pages_inside_the_range_is_accepted(pages: int):
    assert validate_max_pages(pages) == pages


def test_a_bad_max_pages_stops_the_run_before_any_scraping(sqlite_engine: Engine):
    site = _site(_listing("L1"))

    with pytest.raises(ValueError):
        run_scrape(sqlite_engine, URL, 0, scrape=site, now=NOW)

    assert site.calls == []


# --- load_stored_listings ---


def test_an_empty_table_gives_an_empty_frame_that_still_has_the_columns(sqlite_engine: Engine):
    stored = load_stored_listings(sqlite_engine)

    assert stored.empty
    assert {"id", "price", "last_seen_at", "delisted_at"} <= set(stored.columns)


def test_stored_listings_include_the_ones_already_marked_gone(sqlite_engine: Engine):
    _run(sqlite_engine, _site(_listing("L1"), _listing("L2"), _listing("L3")))
    _mark_gone(sqlite_engine, "L2")

    stored = load_stored_listings(sqlite_engine)

    assert sorted(stored["id"]) == ["L1", "L2", "L3"]
    assert stored.loc[stored["id"] == "L2", "delisted_at"].notna().all()
    assert stored.loc[stored["id"] == "L1", "delisted_at"].isna().all()


# --- the first run ---


def test_the_first_run_inserts_everything(sqlite_engine: Engine):
    site = _site(_listing("L1", 150000), _listing("L2", 200000), _listing("L3", 90000))

    result = _run(sqlite_engine, site)

    assert result == ScrapeRunResult(
        dry_run=False,
        scraped=3,
        new=3,
        unchanged=0,
        modified=0,
        gone=0,
        inserted=3,
        updated=0,
        price_events_logged=0,
        reinstated=0,
        marked_gone=0,
    )
    assert sorted(r["id"] for r in _all_rows(sqlite_engine, "listings")) == ["L1", "L2", "L3"]


def test_the_scrape_is_called_with_the_url_and_the_page_count(sqlite_engine: Engine):
    site = _site(_listing("L1"))

    run_scrape(sqlite_engine, URL, 7, scrape=site, now=NOW)

    assert site.calls == [(URL, 7)]


def test_one_timestamp_is_used_for_every_write_of_a_run(sqlite_engine: Engine):
    _run(sqlite_engine, _site(_listing("L1"), _listing("L2"), _listing("L3")))

    for row in _all_rows(sqlite_engine, "listings"):
        assert row["first_seen_at"] == _naive(NOW)
        assert row["last_seen_at"] == _naive(NOW)


# --- later runs ---


def test_an_identical_second_run_changes_only_the_last_seen_date(sqlite_engine: Engine):
    site = _site(_listing("L1", 150000), _listing("L2", 200000))
    _run(sqlite_engine, site)

    result = _run(sqlite_engine, site, now=NEXT_DAY)

    assert (result.new, result.unchanged, result.modified, result.gone) == (0, 2, 0, 0)
    assert (result.inserted, result.updated, result.price_events_logged) == (0, 2, 0)
    assert len(_all_rows(sqlite_engine, "listings")) == 2
    assert _all_rows(sqlite_engine, "price_change_events") == []
    row = _row(sqlite_engine, "L1")
    assert row["first_seen_at"] == _naive(NOW)
    assert row["last_seen_at"] == _naive(NEXT_DAY)


def test_a_price_change_is_logged_and_stored(sqlite_engine: Engine):
    _run(sqlite_engine, _site(_listing("L1", 150000), _listing("L2", 200000)))

    result = _run(sqlite_engine, _site(_listing("L1", 140000), _listing("L2", 200000)), NEXT_DAY)

    assert (result.unchanged, result.modified, result.price_events_logged) == (1, 1, 1)
    events = _all_rows(sqlite_engine, "price_change_events")
    assert [(e["listing_id"], e["old_price"], e["new_price"]) for e in events] == [
        ("L1", 150000, 140000)
    ]
    assert _row(sqlite_engine, "L1")["price"] == 140000


def test_a_missing_listing_is_marked_gone_only_after_the_second_miss(sqlite_engine: Engine):
    everyone = _site(_listing("L1"), _listing("L2"), _listing("L3"))
    without_l1 = _site(_listing("L2"), _listing("L3"))
    _run(sqlite_engine, everyone)

    first_miss = _run(sqlite_engine, without_l1, NEXT_DAY)
    assert (first_miss.gone, first_miss.marked_gone) == (0, 0)
    assert _row(sqlite_engine, "L1")["delisted_at"] is None

    second_miss = _run(sqlite_engine, without_l1, DAY_3)
    assert (second_miss.gone, second_miss.marked_gone) == (1, 1)
    assert _row(sqlite_engine, "L1")["delisted_at"] == _naive(DAY_3)

    still_gone = _run(sqlite_engine, without_l1, DAY_4)
    assert (still_gone.gone, still_gone.marked_gone) == (0, 0)
    assert _row(sqlite_engine, "L1")["delisted_at"] == _naive(DAY_3)  # the date does not move

    back = _run(sqlite_engine, everyone, DAY_5)
    assert back.reinstated == 1
    assert _row(sqlite_engine, "L1")["delisted_at"] is None


# --- the safety checks, before anything is written ---


def test_a_scrape_that_collapses_is_refused_and_nothing_is_written(sqlite_engine: Engine):
    _run(sqlite_engine, _site(*(_listing(f"L{i}") for i in range(1, 5))))

    with pytest.raises(ScrapeAnomalyError):
        _run(sqlite_engine, _site(_listing("L1", 1)), NEXT_DAY)  # 1 of 4 stored is below half

    assert len(_all_rows(sqlite_engine, "listings")) == 4
    assert all(r["last_seen_at"] == _naive(NOW) for r in _all_rows(sqlite_engine, "listings"))
    assert _row(sqlite_engine, "L1")["price"] == 100000
    assert _all_rows(sqlite_engine, "price_change_events") == []


def test_the_expected_count_is_the_live_listings_not_the_gone_ones(sqlite_engine: Engine):
    _run(sqlite_engine, _site(*(_listing(f"L{i}") for i in range(1, 5))))
    _mark_gone(sqlite_engine, "L2", "L3", "L4")  # only L1 is still live

    result = _run(sqlite_engine, _site(_listing("L1")), NEXT_DAY)  # 1 of 1 live: fine

    assert result.unchanged == 1


def test_a_scrape_that_finds_nothing_is_refused_even_on_the_first_run(sqlite_engine: Engine):
    with pytest.raises(ScrapeAnomalyError, match="no listings at all"):
        _run(sqlite_engine, _site())

    assert _all_rows(sqlite_engine, "listings") == []


def test_a_failed_scrape_writes_nothing(sqlite_engine: Engine):
    _run(sqlite_engine, _site(_listing("L1"), _listing("L2")))

    def broken(base_url: str, max_pages: int) -> list[Listing]:
        raise RuntimeError("page 3 failed")

    with pytest.raises(RuntimeError, match="page 3 failed"):
        run_scrape(sqlite_engine, URL, 3, scrape=broken, now=NEXT_DAY)

    assert _row(sqlite_engine, "L1")["last_seen_at"] == _naive(NOW)


# --- dry run ---


def test_a_dry_run_on_an_empty_database_writes_nothing_but_reports(sqlite_engine: Engine):
    site = _site(_listing("L1"), _listing("L2"), _listing("L3"))

    result = _run(sqlite_engine, site, dry_run=True)

    assert result.dry_run is True
    assert (result.scraped, result.new) == (3, 3)
    assert (result.inserted, result.updated, result.marked_gone) == (0, 0, 0)
    assert _all_rows(sqlite_engine, "listings") == []


def test_a_dry_run_on_a_filled_database_changes_nothing(sqlite_engine: Engine):
    _run(sqlite_engine, _site(*(_listing(f"L{i}", 100000) for i in range(1, 5))))
    _mark_gone(sqlite_engine, "L4")
    before = _all_rows(sqlite_engine, "listings")
    # L1 price drops, L2 and L3 stay, L4 (marked gone) is back, one new listing.
    site = _site(
        _listing("L1", 90000), _listing("L2"), _listing("L3"), _listing("L4"), _listing("L5")
    )

    result = _run(sqlite_engine, site, now=DAY_3, dry_run=True)

    assert (result.new, result.unchanged, result.modified, result.reinstated) == (1, 3, 1, 1)
    assert (result.inserted, result.updated, result.price_events_logged) == (0, 0, 0)
    assert _all_rows(sqlite_engine, "listings") == before
    assert _all_rows(sqlite_engine, "price_change_events") == []


def test_a_dry_run_still_refuses_a_collapsed_scrape(sqlite_engine: Engine):
    _run(sqlite_engine, _site(*(_listing(f"L{i}") for i in range(1, 5))))

    with pytest.raises(ScrapeAnomalyError):
        _run(sqlite_engine, _site(_listing("L1")), NEXT_DAY, dry_run=True)


# --- the report ---


def test_the_real_run_report_says_what_was_written():
    result = ScrapeRunResult(
        dry_run=False,
        scraped=10,
        new=4,
        unchanged=5,
        modified=1,
        gone=2,
        inserted=4,
        updated=6,
        price_events_logged=1,
        reinstated=3,
        marked_gone=2,
    )

    assert format_report(result).splitlines() == [
        "REAL RUN: changes were written to the database.",
        "Listings found on the site: 10",
        "Compared with the database: 4 new | 5 unchanged | 1 price changed | 2 confirmed gone",
        "Written: 4 inserted, 6 updated (1 price changes logged, 3 came back), 2 marked as gone",
    ]


def test_the_dry_run_report_says_nothing_was_written():
    result = ScrapeRunResult(
        dry_run=True, scraped=10, new=4, unchanged=5, modified=1, gone=2, reinstated=3
    )

    assert format_report(result).splitlines() == [
        "DRY RUN: nothing was written to the database.",
        "Listings found on the site: 10",
        "Compared with the database: 4 new | 5 unchanged | 1 price changed | 2 confirmed gone",
        "Would write: 4 inserted, 6 updated (1 price changes logged, 3 coming back), "
        "2 marked as gone",
    ]
