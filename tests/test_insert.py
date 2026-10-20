"""Tests for src/real_estate_pipeline/paruvendu/insert.py."""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

import pandas as pd
import pytest
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError

from real_estate_pipeline.common.db import DB_MAX_RETRIES
from real_estate_pipeline.paruvendu.insert import (
    _prepare_new_listing_records,
    insert_new_listings,
)
from real_estate_pipeline.paruvendu.models import LISTING_COLUMNS

NOW = cast(pd.Timestamp, pd.Timestamp("2026-08-30T00:00:00Z"))


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


# --- _prepare_new_listing_records: pure data-shaping, no DB ---


def test_filters_to_new_rows_only():
    classified = _classified(
        [
            {"id": "L1", "price": 100000, "diff_status": "new", "old_price": pd.NA},
            {
                "id": "L2",
                "price": 200000,
                "diff_status": "unchanged",
                "old_price": pd.NA,
            },
            {"id": "L3", "price": 90000, "diff_status": "modified", "old_price": 95000},
        ]
    )

    records = _prepare_new_listing_records(classified, now=NOW)

    assert [r["id"] for r in records] == ["L1"]


def test_keeps_exactly_the_listing_columns_plus_seen_timestamps():
    """An allow-list, not a drop-list: neither the diff bookkeeping nor any
    column that only exists on the stored side (delisted_at, a stale
    first_seen_at, anything else) can reach the INSERT.
    """
    classified = _classified([{"id": "L1", "diff_status": "new", "old_price": pd.NA}])
    classified["delta_eur"] = pd.NA
    classified["delisted_at"] = pd.NaT
    classified["first_seen_at"] = pd.NaT
    classified["some_db_only_column"] = "leak"

    records = _prepare_new_listing_records(classified, now=NOW)

    assert set(records[0]) == set(LISTING_COLUMNS) | {"first_seen_at", "last_seen_at"}
    assert records[0]["first_seen_at"] == NOW


def test_new_rows_missing_a_listing_column_raise_naming_it():
    classified = _classified([{"id": "L1", "diff_status": "new", "old_price": pd.NA}]).drop(
        columns=["dpe"]
    )

    with pytest.raises(ValueError, match="dpe"):
        _prepare_new_listing_records(classified, now=NOW)


def test_sets_first_and_last_seen_at_to_shared_now():
    classified = _classified(
        [
            {"id": "L1", "price": 100000, "diff_status": "new", "old_price": pd.NA},
            {"id": "L2", "price": 200000, "diff_status": "new", "old_price": pd.NA},
        ]
    )

    records = _prepare_new_listing_records(classified, now=NOW)

    for record in records:
        assert record["first_seen_at"] == NOW
        assert record["last_seen_at"] == NOW


def test_nan_converted_to_none_for_nullable_columns():
    classified = _classified(
        [
            {
                "id": "L1",
                "price": 100000,
                "diff_status": "new",
                "old_price": pd.NA,
                "dpe": float("nan"),
            }
        ]
    )

    records = _prepare_new_listing_records(classified, now=NOW)

    assert records[0]["dpe"] is None


def test_no_new_rows_returns_empty_list():
    classified = _classified(
        [{"id": "L1", "price": 100000, "diff_status": "unchanged", "old_price": pd.NA}]
    )

    records = _prepare_new_listing_records(classified, now=NOW)

    assert records == []


# --- insert_new_listings: real DB behavior, against in-memory SQLite ---


@pytest.fixture
def sqlite_engine() -> Engine:
    """A real (in-memory) engine with a `listings` table shaped like the
    real one: all 16 Listing columns plus the two timestamps, with the
    NOT NULL boolean flags carrying a server-side default as in Postgres.
    The only stand-in is `amenities`: SQLite has no native array type, so
    it is JSON here. Transactions, retries and error handling are
    exercised for real rather than mocked away.
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
        Column("first_seen_at", String, nullable=False),
        Column("last_seen_at", String, nullable=False),
    )
    metadata.create_all(engine)
    return engine


def test_insert_new_listings_returns_zero_when_nothing_to_insert(
    sqlite_engine: Engine,
):
    classified = _classified(
        [{"id": "L1", "price": 100000, "diff_status": "unchanged", "old_price": pd.NA}]
    )

    count = insert_new_listings(classified, sqlite_engine, now=NOW)

    assert count == 0


def test_insert_new_listings_writes_rows_and_returns_count(sqlite_engine: Engine):
    classified = _classified(
        [
            {
                "id": "L1",
                "url": "https://x/L1",
                "price": 100000,
                "diff_status": "new",
                "old_price": pd.NA,
            },
            {
                "id": "L2",
                "url": "https://x/L2",
                "price": 200000,
                "diff_status": "new",
                "old_price": pd.NA,
            },
        ]
    )

    count = insert_new_listings(classified, sqlite_engine, now=NOW)

    assert count == 2
    with sqlite_engine.connect() as connection:
        rows = connection.exec_driver_sql("SELECT id, price FROM listings ORDER BY id").fetchall()
    assert rows == [("L1", 100000), ("L2", 200000)]


def test_integrity_error_raised_immediately_not_retried(sqlite_engine: Engine):
    """A duplicate id (PK conflict) must raise IntegrityError right away,
    with no retry attempts — retrying identical bad data can't succeed.
    """
    classified = _classified(
        [
            {
                "id": "L1",
                "url": "https://x/L1",
                "price": 100000,
                "diff_status": "new",
                "old_price": pd.NA,
            }
        ]
    )
    insert_new_listings(classified, sqlite_engine, now=NOW)  # first insert succeeds

    with (
        patch("real_estate_pipeline.common.db.time.sleep") as mock_sleep,
        pytest.raises(IntegrityError),
    ):
        insert_new_listings(classified, sqlite_engine, now=NOW)  # duplicate id

    mock_sleep.assert_not_called()


def test_operational_error_retries_then_succeeds(sqlite_engine: Engine):
    """A transient OperationalError should be retried, and a subsequent
    successful attempt should return normally.
    """
    classified = _classified(
        [
            {
                "id": "L1",
                "url": "https://x/L1",
                "price": 100000,
                "diff_status": "new",
                "old_price": pd.NA,
            }
        ]
    )

    real_begin = sqlite_engine.begin
    call_count = {"n": 0}

    def flaky_begin(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OperationalError("simulated drop", {}, Exception("connection reset"))
        return real_begin(*args, **kwargs)

    with (
        patch.object(sqlite_engine, "begin", side_effect=flaky_begin),
        patch("real_estate_pipeline.common.db.time.sleep") as mock_sleep,
    ):
        count = insert_new_listings(classified, sqlite_engine, now=NOW)

    assert count == 1
    mock_sleep.assert_called_once()


def test_operational_error_exhausts_retries_and_raises(sqlite_engine: Engine):
    classified = _classified(
        [
            {
                "id": "L1",
                "url": "https://x/L1",
                "price": 100000,
                "diff_status": "new",
                "old_price": pd.NA,
            }
        ]
    )

    def always_fails(*args, **kwargs):
        raise OperationalError("simulated drop", {}, Exception("connection reset"))

    with (
        patch.object(sqlite_engine, "begin", side_effect=always_fails),
        patch("real_estate_pipeline.common.db.time.sleep") as mock_sleep,
        pytest.raises(OperationalError),
    ):
        insert_new_listings(classified, sqlite_engine, now=NOW)

    assert mock_sleep.call_count == DB_MAX_RETRIES - 1
