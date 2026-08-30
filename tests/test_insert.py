"""Tests for src/real_estate_pipeline/paruvendu/insert.py."""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

import pandas as pd
import pytest
from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError

from real_estate_pipeline.common.db import DB_MAX_RETRIES
from real_estate_pipeline.paruvendu.insert import (
    _prepare_new_listing_records,
    insert_new_listings,
)

NOW = cast(pd.Timestamp, pd.Timestamp("2026-08-30T00:00:00Z"))


def _classified(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


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


def test_drops_diff_status_and_old_price_columns():
    classified = _classified(
        [{"id": "L1", "price": 100000, "diff_status": "new", "old_price": pd.NA}]
    )

    records = _prepare_new_listing_records(classified, now=NOW)

    assert "diff_status" not in records[0]
    assert "old_price" not in records[0]


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
    """A real (in-memory) engine with a `listings`-shaped table, so the
    transaction/retry/error-handling behavior is exercised for real
    rather than mocked away. Narrower than the actual Postgres schema
    (no ARRAY column, since SQLite has no native array type) — sufficient
    for testing insert mechanics, not a schema-fidelity test.
    """
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "listings",
        metadata,
        Column("id", String, primary_key=True),
        Column("url", String, nullable=False),
        Column("price", Integer, nullable=False),
        Column("dpe", String, nullable=True),
        Column("has_garage", Boolean, nullable=False, server_default="0"),
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
        patch("real_estate_pipeline.paruvendu.insert.time.sleep") as mock_sleep,
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
        patch("real_estate_pipeline.paruvendu.insert.time.sleep") as mock_sleep,
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
        patch("real_estate_pipeline.paruvendu.insert.time.sleep") as mock_sleep,
        pytest.raises(OperationalError),
    ):
        insert_new_listings(classified, sqlite_engine, now=NOW)

    assert mock_sleep.call_count == DB_MAX_RETRIES - 1
