"""Tests for src/real_estate_pipeline/paruvendu/update.py."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from unittest.mock import patch

import pandas as pd
import pytest
from sqlalchemy import (
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

from real_estate_pipeline.paruvendu.diff import classify_listings
from real_estate_pipeline.paruvendu.insert import insert_new_listings
from real_estate_pipeline.paruvendu.price_events import compute_price_deltas
from real_estate_pipeline.paruvendu.update import (
    UpdateSummary,
    _prepare_listing_update_records,
    update_listings,
)

NOW = cast(pd.Timestamp, pd.Timestamp("2026-08-30T00:00:00Z"))
# Cast: pandas-stubs can type Timestamp arithmetic as Timestamp | NaTType.
NEXT_DAY = cast(pd.Timestamp, NOW + pd.Timedelta(days=1))


def _naive(timestamp: pd.Timestamp) -> datetime:
    """What a timestamptz round-trips to through SQLite's DateTime type."""
    return timestamp.to_pydatetime().replace(tzinfo=None)


# --- _prepare_listing_update_records: pure data-shaping, no DB ---


def _classified(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


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


def test_drops_bookkeeping_and_stored_row_columns():
    """The result must carry the scraped fields and last_seen_at — and
    NOT the diff bookkeeping, nor first_seen_at / delisted_at, which
    belong to the stored row and must never be overwritten by a scrape.
    """
    classified = _classified(
        [
            {
                "id": "L1",
                "price": 150000,
                "url": "https://x/L1",
                "diff_status": "unchanged",
                "old_price": pd.NA,
                "delta_eur": pd.NA,
                "delta_pct": pd.NA,
                "first_seen_at": pd.NaT,
                "last_seen_at": pd.NaT,
                "delisted_at": pd.NaT,
            }
        ]
    )

    records = _prepare_listing_update_records(classified, now=NOW)

    assert set(records[0]) == {"id", "price", "url", "last_seen_at"}


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
    """Real in-memory engine with listings-shaped and
    price_change_events-shaped tables. Narrower than the Postgres schema
    (no ARRAY column), enough to exercise the transaction mechanics.
    """
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "listings",
        metadata,
        Column("id", String, primary_key=True),
        Column("url", String, nullable=False),
        Column("price", Integer, nullable=False),
        Column("description", String, nullable=True),
        Column("dpe", String, nullable=True),
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
    return {
        "id": listing_id,
        "url": f"https://x/{listing_id}",
        "price": price,
        "description": description,
        "dpe": None,
    }


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
        return [dict(r) for r in connection.execute(select(table)).mappings()]


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


def test_non_price_fields_are_refreshed_too(sqlite_engine: Engine):
    """Price-only decides whether an EVENT is logged; the stored row still
    gets every scraped field refreshed so nothing goes stale.
    """
    _run_day(_scrape(_listing("L1", 150000, "near the port")), sqlite_engine, NOW)

    _run_day(_scrape(_listing("L1", 150000, "near the harbor")), sqlite_engine, NEXT_DAY)

    assert _row(sqlite_engine, "L1")["description"] == "near the harbor"
    assert _events(sqlite_engine) == []


def test_first_seen_and_delisted_at_are_never_touched(sqlite_engine: Engine):
    _run_day(_scrape(_listing("L1", 150000)), sqlite_engine, NOW)
    with sqlite_engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE listings SET delisted_at = '2026-08-25 00:00:00.000000' WHERE id = 'L1'"
        )

    _run_day(_scrape(_listing("L1", 145000)), sqlite_engine, NEXT_DAY)

    row = _row(sqlite_engine, "L1")
    assert row["first_seen_at"] == _naive(NOW)
    assert row["delisted_at"] == datetime(2026, 8, 25)


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
