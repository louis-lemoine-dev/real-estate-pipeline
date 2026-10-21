"""Tests for src/real_estate_pipeline/paruvendu/update.py."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from unittest.mock import patch

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
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from real_estate_pipeline.paruvendu.delist import mark_delisted_listings
from real_estate_pipeline.paruvendu.diff import classify_listings
from real_estate_pipeline.paruvendu.insert import insert_new_listings
from real_estate_pipeline.paruvendu.models import LISTING_COLUMNS
from real_estate_pipeline.paruvendu.price_events import compute_price_deltas
from real_estate_pipeline.paruvendu.update import (
    UpdateSummary,
    _prepare_listing_update_records,
    update_listings,
)

NOW = cast(pd.Timestamp, pd.Timestamp("2026-08-30T00:00:00Z"))
# Cast: pandas-stubs can type Timestamp arithmetic as Timestamp | NaTType.
NEXT_DAY = cast(pd.Timestamp, NOW + pd.Timedelta(days=1))
DAY_3 = cast(pd.Timestamp, NOW + pd.Timedelta(days=2))
DAY_4 = cast(pd.Timestamp, NOW + pd.Timedelta(days=3))
DAY_5 = cast(pd.Timestamp, NOW + pd.Timedelta(days=4))


def _naive(timestamp: pd.Timestamp) -> datetime:
    """What a timestamptz round-trips to through SQLite's DateTime type."""
    return timestamp.to_pydatetime().replace(tzinfo=None)


# --- _prepare_listing_update_records: pure data-shaping, no DB ---


def _full_listing_row(row: dict) -> dict:
    """Complete a partial test row into a full Listing row.

    The write paths store exactly the Listing columns, so tests only spell
    out the fields they care about and the rest get neutral defaults.
    Fails loudly if a Listing field is ever added without a default here.
    """
    defaults = {
        "id": "L?",
        "url": f"https://x/{row['id']}",
        "property_type": "Appartement",
        "rooms": 2,
        "surface_m2": 40.0,
        "chambres": 1,
        "dpe": "D",
        "terrain_m2": None,
        "has_garage": False,
        "has_ascenseur": False,
        "has_balcon": False,
        "price": 100000,
        "has_asterisk": False,
        "location": "Toulon",
        "amenities": [],
        "description": "desc",
    }
    unhandled = set(LISTING_COLUMNS) - set(defaults)
    assert not unhandled, f"add test defaults for new Listing field(s): {unhandled}"
    return {**defaults, **row}


def _classified(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([_full_listing_row(row) for row in rows])


def test_keeps_only_unchanged_and_modified_rows():
    classified = _classified(
        [
            {"id": "L1", "price": 1, "diff_status": "new"},
            {"id": "L2", "price": 2, "diff_status": "unchanged"},
            {"id": "L3", "price": 3, "diff_status": "modified"},
            {"id": "L4", "price": 4, "diff_status": "delisted"},
        ]
    )

    records = _prepare_listing_update_records(classified, now=NOW)

    assert [r["id"] for r in records] == ["L2", "L3"]


def test_keeps_exactly_the_listing_columns_plus_seen_markers():
    """An allow-list, not a drop-list: the result carries the scraped
    fields, last_seen_at, and delisted_at cleared to None (seen today means
    not delisted) — and NOT the diff bookkeeping, nor first_seen_at, nor
    any other column that only exists on the stored side.
    """
    classified = _classified([{"id": "L1", "diff_status": "unchanged", "old_price": pd.NA}])
    classified["delta_eur"] = pd.NA
    classified["delta_pct"] = pd.NA
    classified["first_seen_at"] = pd.NaT
    classified["last_seen_at"] = pd.NaT
    classified["delisted_at"] = pd.Timestamp("2026-08-25")  # a stale mark from the stored side
    classified["some_db_only_column"] = "leak"

    records = _prepare_listing_update_records(classified, now=NOW)

    assert set(records[0]) == set(LISTING_COLUMNS) | {"last_seen_at", "delisted_at"}
    assert records[0]["last_seen_at"] == NOW
    assert records[0]["delisted_at"] is None


def test_rows_missing_a_listing_column_raise_naming_it():
    classified = _classified([{"id": "L1", "diff_status": "unchanged", "old_price": pd.NA}]).drop(
        columns=["dpe"]
    )

    with pytest.raises(ValueError, match="dpe"):
        _prepare_listing_update_records(classified, now=NOW)


def test_sets_last_seen_at_to_shared_native_now():
    classified = _classified(
        [
            {"id": "L1", "price": 1, "diff_status": "unchanged"},
            {"id": "L2", "price": 2, "diff_status": "modified"},
        ]
    )

    records = _prepare_listing_update_records(classified, now=NOW)

    for record in records:
        assert record["last_seen_at"] == NOW
        assert type(record["last_seen_at"]) is datetime


def test_nan_converted_to_none():
    classified = _classified(
        [
            {
                "id": "L1",
                "price": 1,
                "diff_status": "unchanged",
                "dpe": float("nan"),
            }
        ]
    )

    records = _prepare_listing_update_records(classified, now=NOW)

    assert records[0]["dpe"] is None


def test_nothing_to_update_returns_empty_list():
    classified = _classified([{"id": "L1", "price": 1, "diff_status": "new"}])

    assert _prepare_listing_update_records(classified, now=NOW) == []


# --- update_listings: real DB behavior, against in-memory SQLite ---


@pytest.fixture
def sqlite_engine() -> Engine:
    """Real in-memory engine with a `listings` table shaped like the real
    one (all 16 Listing columns plus the three bookkeeping dates, NOT NULL
    flags with a server default as in Postgres) and a
    price_change_events-shaped table. The only stand-in is `amenities`:
    SQLite has no native array type, so it is JSON here.
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


def _scrape(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _listing(listing_id: str, price: int, description: str = "desc") -> dict:
    return _full_listing_row({"id": listing_id, "price": price, "description": description})


def _run_day(scraped: pd.DataFrame, engine: Engine, now: pd.Timestamp):
    """One full ingestion day through the real chain: read the stored
    state, classify, compute deltas, insert new, update re-seen.
    """
    existing = pd.read_sql("SELECT * FROM listings", engine)
    classified = compute_price_deltas(classify_listings(scraped, existing, now=now))
    inserted = insert_new_listings(classified, engine, now=now)
    summary = update_listings(classified, engine, now=now)
    return inserted, summary


def _row(engine: Engine, listing_id: str) -> dict:
    table = Table("listings", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        result = connection.execute(select(table).where(table.c.id == listing_id))
        return dict(result.mappings().one())


def _events(engine: Engine) -> list[dict]:
    table = Table("price_change_events", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        rows = connection.execute(select(table).order_by(table.c.id)).mappings()
        return [dict(r) for r in rows]


def test_unchanged_listing_gets_fresh_last_seen_and_no_event(sqlite_engine: Engine):
    _run_day(_scrape(_listing("L1", 150000)), sqlite_engine, NOW)

    _, summary = _run_day(_scrape(_listing("L1", 150000)), sqlite_engine, NEXT_DAY)

    assert summary == UpdateSummary(listings_updated=1, price_events_logged=0)
    assert _row(sqlite_engine, "L1")["last_seen_at"] == _naive(NEXT_DAY)
    assert _events(sqlite_engine) == []


def test_modified_listing_updates_price_and_logs_event(sqlite_engine: Engine):
    _run_day(_scrape(_listing("L1", 150000)), sqlite_engine, NOW)

    _, summary = _run_day(_scrape(_listing("L1", 145000)), sqlite_engine, NEXT_DAY)

    assert summary == UpdateSummary(listings_updated=1, price_events_logged=1)
    assert _row(sqlite_engine, "L1")["price"] == 145000
    events = _events(sqlite_engine)
    assert len(events) == 1
    assert events[0]["listing_id"] == "L1"
    assert events[0]["old_price"] == 150000
    assert events[0]["new_price"] == 145000
    assert events[0]["delta_eur"] == -5000
    # (145000 - 150000) / 150000 * 100, stored end to end
    assert abs(events[0]["delta_pct"] - (-3.3333333333333335)) < 1e-9


def test_non_price_fields_are_refreshed_too(sqlite_engine: Engine):
    """Price-only decides whether an EVENT is logged; the stored row still
    gets every scraped field refreshed so nothing goes stale.
    """
    _run_day(_scrape(_listing("L1", 150000, "near the port")), sqlite_engine, NOW)

    _run_day(_scrape(_listing("L1", 150000, "near the harbor")), sqlite_engine, NEXT_DAY)

    assert _row(sqlite_engine, "L1")["description"] == "near the harbor"
    assert _events(sqlite_engine) == []


def test_first_seen_is_never_touched_and_a_returning_listing_is_reinstated(
    sqlite_engine: Engine,
):
    """A listing seen again is live: any delisted_at mark is cleared, and
    the run reports it. first_seen_at stays exactly as first recorded.
    """
    _run_day(_scrape(_listing("L1", 150000)), sqlite_engine, NOW)
    with sqlite_engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE listings SET delisted_at = '2026-08-25 00:00:00.000000' WHERE id = 'L1'"
        )

    _, summary = _run_day(_scrape(_listing("L1", 145000)), sqlite_engine, NEXT_DAY)

    row = _row(sqlite_engine, "L1")
    assert row["first_seen_at"] == _naive(NOW)
    assert row["delisted_at"] is None
    assert summary.listings_reinstated == 1


def test_mixed_batch_summary_counts(sqlite_engine: Engine):
    _run_day(
        _scrape(_listing("L1", 100), _listing("L2", 200), _listing("L3", 300)),
        sqlite_engine,
        NOW,
    )

    inserted, summary = _run_day(
        _scrape(
            _listing("L1", 100),  # unchanged
            _listing("L2", 190),  # modified
            _listing("L4", 400),  # new
        ),
        sqlite_engine,
        NEXT_DAY,
    )

    assert inserted == 1
    assert summary == UpdateSummary(listings_updated=2, price_events_logged=1)


def test_nothing_to_update_returns_zeros(sqlite_engine: Engine):
    classified = _classified([{"id": "L1", "price": 1, "diff_status": "new"}])

    summary = update_listings(classified, sqlite_engine, now=NOW)

    assert summary == UpdateSummary(listings_updated=0, price_events_logged=0)


def test_rerun_on_identical_data_adds_no_history_and_changes_no_content(
    sqlite_engine: Engine,
):
    """The roadmap's idempotency claim, tested for real: running the same
    scrape again must not log any price-change event, nor alter any stored
    content. Only last_seen_at moves.
    """
    scrape = _scrape(_listing("L1", 150000), _listing("L2", 200000))
    _run_day(scrape, sqlite_engine, NOW)
    after_first = {i: _row(sqlite_engine, i) for i in ("L1", "L2")}

    inserted, summary = _run_day(scrape, sqlite_engine, NEXT_DAY)

    assert inserted == 0
    assert summary.price_events_logged == 0
    assert _events(sqlite_engine) == []
    for listing_id, before in after_first.items():
        after = _row(sqlite_engine, listing_id)
        assert after["last_seen_at"] == _naive(NEXT_DAY)
        for column in ("url", "price", "description", "first_seen_at"):
            assert after[column] == before[column]


def test_failed_update_rolls_back_the_price_event_too(sqlite_engine: Engine):
    """Atomicity: the event is written BEFORE the update, so if the update
    then fails, the event must disappear with it — otherwise the next run
    would re-detect the same change and log a duplicate.
    """
    _run_day(_scrape(_listing("L1", 150000)), sqlite_engine, NOW)
    broken = _listing("L1", 145000)
    broken["url"] = None  # NOT NULL column -> the UPDATE will fail

    with (
        patch("real_estate_pipeline.common.db.time.sleep") as mock_sleep,
        pytest.raises(IntegrityError),
    ):
        _run_day(_scrape(broken), sqlite_engine, NEXT_DAY)

    assert _events(sqlite_engine) == []
    assert _row(sqlite_engine, "L1")["price"] == 150000
    mock_sleep.assert_not_called()


def test_incomplete_scrape_is_refused_and_nothing_is_written(sqlite_engine: Engine):
    """The incident the manual Supabase check exposed, closed: a scrape that
    omits columns the table has must be refused outright — not allowed to
    blank the stored values (or trip a NOT NULL) halfway through a write.
    """
    _run_day(_scrape(_listing("L1", 150000)), sqlite_engine, NOW)
    before = _row(sqlite_engine, "L1")
    incomplete = _scrape(_listing("L1", 145000)).drop(columns=["dpe", "has_ascenseur"])

    with pytest.raises(ValueError, match=r"dpe.*has_ascenseur"):
        _run_day(incomplete, sqlite_engine, NEXT_DAY)

    assert _row(sqlite_engine, "L1") == before  # stored row untouched, dpe included
    assert _events(sqlite_engine) == []


# --- price-change history, end to end (3.4.3) ---


def _prices_and_events(prices_by_day: list[tuple[pd.Timestamp, int]], engine: Engine):
    for day, price in prices_by_day:
        _run_day(_scrape(_listing("L1", price)), engine, day)
    return _events(engine)


def test_price_increase_logs_one_event_with_positive_delta(sqlite_engine: Engine):
    _run_day(_scrape(_listing("L1", 150000)), sqlite_engine, NOW)

    _, summary = _run_day(_scrape(_listing("L1", 160000)), sqlite_engine, NEXT_DAY)

    assert summary == UpdateSummary(listings_updated=1, price_events_logged=1)
    assert _row(sqlite_engine, "L1")["price"] == 160000
    events = _events(sqlite_engine)
    assert len(events) == 1
    assert (events[0]["old_price"], events[0]["new_price"]) == (150000, 160000)
    assert events[0]["delta_eur"] == 10000
    assert abs(events[0]["delta_pct"] - 6.666666666666667) < 1e-9


def test_two_successive_drops_log_two_distinct_events_in_order(sqlite_engine: Engine):
    """The case V2's repeated-drops signal will depend on: one listing, two
    price drops on consecutive days, must leave two separate events whose
    prices chain (the second starts where the first ended) — not one
    overwritten row, and not a lost first drop.
    """
    events = _prices_and_events([(NOW, 150000), (NEXT_DAY, 145000), (DAY_3, 140000)], sqlite_engine)

    assert len(events) == 2
    assert events[0]["id"] != events[1]["id"]
    assert [(e["old_price"], e["new_price"]) for e in events] == [
        (150000, 145000),
        (145000, 140000),
    ]
    assert [e["delta_eur"] for e in events] == [-5000, -5000]
    assert {e["listing_id"] for e in events} == {"L1"}
    assert _row(sqlite_engine, "L1")["price"] == 140000


def test_flat_day_between_two_drops_adds_no_extra_event(sqlite_engine: Engine):
    """A day where the price simply holds must not log anything, so the
    history only ever contains real changes.
    """
    events = _prices_and_events(
        [(NOW, 150000), (NEXT_DAY, 145000), (DAY_3, 145000), (DAY_4, 140000)],
        sqlite_engine,
    )

    assert [(e["old_price"], e["new_price"]) for e in events] == [
        (150000, 145000),
        (145000, 140000),
    ]


# --- delisting lifecycle, end to end ---


def _run_day_with_delisting(scraped: pd.DataFrame, engine: Engine, now: pd.Timestamp):
    """Like _run_day, plus the step that records listings confirmed gone."""
    existing = pd.read_sql("SELECT * FROM listings", engine)
    classified = compute_price_deltas(classify_listings(scraped, existing, now=now))
    insert_new_listings(classified, engine, now=now)
    summary = update_listings(classified, engine, now=now)
    marked = mark_delisted_listings(classified, engine, now=now)
    return summary, marked


def test_delisting_lifecycle_mark_wait_keep_then_clear(sqlite_engine: Engine):
    """The whole story of one listing that disappears and comes back:
    first miss -> nothing; second miss -> marked with that day's date;
    still gone later -> the date does NOT move; reappears -> mark cleared.
    L2 stays on the site throughout, so every scrape is realistic.
    """
    both = _scrape(_listing("L1", 150000), _listing("L2", 200000))
    only_l2 = _scrape(_listing("L2", 200000))

    _run_day_with_delisting(both, sqlite_engine, NOW)
    assert _row(sqlite_engine, "L1")["delisted_at"] is None

    # Day 2: L1 missing for the first time -> not enough to conclude.
    _, marked = _run_day_with_delisting(only_l2, sqlite_engine, NEXT_DAY)
    assert marked == 0
    assert _row(sqlite_engine, "L1")["delisted_at"] is None

    # Day 3: second consecutive miss -> marked, with the day we concluded it.
    _, marked = _run_day_with_delisting(only_l2, sqlite_engine, DAY_3)
    assert marked == 1
    assert _row(sqlite_engine, "L1")["delisted_at"] == _naive(DAY_3)
    assert _row(sqlite_engine, "L2")["delisted_at"] is None

    # Day 4: still gone -> already marked, the original date is kept.
    _, marked = _run_day_with_delisting(only_l2, sqlite_engine, DAY_4)
    assert marked == 0
    assert _row(sqlite_engine, "L1")["delisted_at"] == _naive(DAY_3)

    # Day 5: it is back -> live again, mark cleared, and the run says so.
    summary, marked = _run_day_with_delisting(both, sqlite_engine, DAY_5)
    assert marked == 0
    assert summary.listings_reinstated == 1
    assert _row(sqlite_engine, "L1")["delisted_at"] is None
    assert _row(sqlite_engine, "L1")["last_seen_at"] == _naive(DAY_5)
