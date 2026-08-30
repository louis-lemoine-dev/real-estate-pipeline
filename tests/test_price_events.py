"""Tests for src/real_estate_pipeline/paruvendu/price_events.py."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from real_estate_pipeline.paruvendu.price_events import (
    _prepare_price_change_event_records,
    compute_price_deltas,
    insert_price_change_events,
)


def _classified(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_price_decrease_produces_negative_delta():
    classified = _classified(
        [{"id": "L1", "price": 145000, "diff_status": "modified", "old_price": 150000}]
    )

    result = compute_price_deltas(classified)

    row = result.iloc[0]
    assert row["delta_eur"] == -5000
    # (145000 - 150000) / 150000 * 100 = -3.3333...
    assert abs(row["delta_pct"] - (-3.3333333333333335)) < 1e-9


def test_price_increase_produces_positive_delta():
    classified = _classified(
        [{"id": "L1", "price": 160000, "diff_status": "modified", "old_price": 150000}]
    )

    result = compute_price_deltas(classified)

    row = result.iloc[0]
    assert row["delta_eur"] == 10000
    assert row["delta_pct"] > 0


def test_non_modified_rows_get_no_delta():
    classified = _classified(
        [
            {"id": "L1", "price": 100000, "diff_status": "new", "old_price": pd.NA},
            {
                "id": "L2",
                "price": 200000,
                "diff_status": "unchanged",
                "old_price": pd.NA,
            },
            {"id": "L3", "price": 50000, "diff_status": "delisted", "old_price": pd.NA},
        ]
    )

    result = compute_price_deltas(classified)

    assert bool(result["delta_eur"].isna().all())
    assert bool(result["delta_pct"].isna().all())


def test_zero_old_price_guards_division_but_still_computes_delta_eur():
    """old_price == 0 shouldn't happen in practice (price is NOT NULL in
    the schema), but is guarded anyway: delta_pct must be None (a % change
    from zero is undefined), while delta_eur is still computed since it
    doesn't involve division.
    """
    classified = _classified(
        [{"id": "L1", "price": 50000, "diff_status": "modified", "old_price": 0}]
    )

    result = compute_price_deltas(classified)

    row = result.iloc[0]
    assert row["delta_eur"] == 50000
    assert pd.isna(row["delta_pct"])


def test_delta_pct_not_rounded():
    """Full precision is stored — rounding is a display concern, not
    computed away here.
    """
    classified = _classified(
        [{"id": "L1", "price": 100000, "diff_status": "modified", "old_price": 300000}]
    )

    result = compute_price_deltas(classified)

    row = result.iloc[0]
    # (100000 - 300000) / 300000 * 100 = -66.66666...
    assert abs(row["delta_pct"] - (-66.66666666666667)) < 1e-9


def test_mixed_batch_only_modified_rows_get_deltas():
    classified = _classified(
        [
            {"id": "L1", "price": 145000, "diff_status": "modified", "old_price": 150000},
            {"id": "L2", "price": 200000, "diff_status": "unchanged", "old_price": pd.NA},
            {"id": "L3", "price": 90000, "diff_status": "new", "old_price": pd.NA},
        ]
    )

    result = compute_price_deltas(classified)

    l1 = result.loc[result["id"] == "L1"].iloc[0]
    l2 = result.loc[result["id"] == "L2"].iloc[0]
    l3 = result.loc[result["id"] == "L3"].iloc[0]

    assert l1["delta_eur"] == -5000
    assert pd.isna(l2["delta_eur"])
    assert pd.isna(l3["delta_eur"])


# --- _prepare_price_change_event_records: pure data-shaping, no DB ---


def test_prepare_events_filters_to_modified_rows_only():
    classified = _classified(
        [
            {
                "id": "L1",
                "price": 145000,
                "diff_status": "modified",
                "old_price": 150000,
                "delta_eur": -5000,
                "delta_pct": -3.33,
            },
            {
                "id": "L2",
                "price": 200000,
                "diff_status": "unchanged",
                "old_price": pd.NA,
                "delta_eur": pd.NA,
                "delta_pct": pd.NA,
            },
        ]
    )

    records = _prepare_price_change_event_records(classified)

    assert len(records) == 1
    assert records[0]["listing_id"] == "L1"


def test_prepare_events_maps_columns_correctly():
    classified = _classified(
        [
            {
                "id": "L1",
                "price": 145000,
                "diff_status": "modified",
                "old_price": 150000,
                "delta_eur": -5000,
                "delta_pct": -3.3333333333333335,
            }
        ]
    )

    records = _prepare_price_change_event_records(classified)

    record = records[0]
    assert record["listing_id"] == "L1"
    assert record["old_price"] == 150000
    assert record["new_price"] == 145000
    assert record["delta_eur"] == -5000
    assert abs(record["delta_pct"] - (-3.3333333333333335)) < 1e-9
    assert "id" not in record
    assert "detected_at" not in record


def test_prepare_events_nan_delta_pct_converted_to_none():
    """A guarded zero-old_price case (delta_pct is None from
    compute_price_deltas) must stay None, not become NaN, going into
    the DB insert.
    """
    classified = _classified(
        [
            {
                "id": "L1",
                "price": 50000,
                "diff_status": "modified",
                "old_price": 0,
                "delta_eur": 50000,
                "delta_pct": pd.NA,
            }
        ]
    )

    records = _prepare_price_change_event_records(classified)

    assert records[0]["delta_pct"] is None


def test_prepare_events_no_modified_rows_returns_empty_list():
    classified = _classified(
        [
            {
                "id": "L1",
                "price": 100000,
                "diff_status": "unchanged",
                "old_price": pd.NA,
                "delta_eur": pd.NA,
                "delta_pct": pd.NA,
            }
        ]
    )

    records = _prepare_price_change_event_records(classified)

    assert records == []


# --- insert_price_change_events: real DB behavior, against in-memory SQLite ---


@pytest.fixture
def sqlite_events_engine() -> Engine:
    """A real (in-memory) engine with a price_change_events-shaped table."""
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "price_change_events",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("listing_id", String, nullable=False),
        Column("old_price", Integer, nullable=False),
        Column("new_price", Integer, nullable=False),
        Column("delta_eur", Integer, nullable=False),
        Column("delta_pct", String, nullable=True),
    )
    metadata.create_all(engine)
    return engine


def test_insert_events_returns_zero_when_nothing_to_insert(
    sqlite_events_engine: Engine,
):
    classified = _classified(
        [
            {
                "id": "L1",
                "price": 100000,
                "diff_status": "unchanged",
                "old_price": pd.NA,
                "delta_eur": pd.NA,
                "delta_pct": pd.NA,
            }
        ]
    )

    count = insert_price_change_events(classified, sqlite_events_engine)

    assert count == 0


def test_insert_events_writes_rows_and_returns_count(sqlite_events_engine: Engine):
    classified = _classified(
        [
            {
                "id": "L1",
                "price": 145000,
                "diff_status": "modified",
                "old_price": 150000,
                "delta_eur": -5000,
                "delta_pct": -3.33,
            },
            {
                "id": "L2",
                "price": 210000,
                "diff_status": "modified",
                "old_price": 200000,
                "delta_eur": 10000,
                "delta_pct": 5.0,
            },
        ]
    )

    count = insert_price_change_events(classified, sqlite_events_engine)

    assert count == 2
    with sqlite_events_engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT listing_id, old_price, new_price FROM price_change_events ORDER BY listing_id"
        ).fetchall()
    assert rows == [("L1", 150000, 145000), ("L2", 200000, 210000)]


def test_insert_events_integrity_error_not_retried(
    monkeypatch: pytest.MonkeyPatch, sqlite_events_engine: Engine
):
    classified = _classified(
        [
            {
                "id": "L1",
                "price": 145000,
                "diff_status": "modified",
                "old_price": 150000,
                "delta_eur": -5000,
                "delta_pct": -3.33,
            }
        ]
    )

    def always_integrity_error(*args, **kwargs):
        raise IntegrityError("simulated constraint violation", {}, Exception("bad"))

    monkeypatch.setattr(sqlite_events_engine, "begin", always_integrity_error)
    with (
        patch("real_estate_pipeline.paruvendu.price_events.time.sleep") as mock_sleep,
        pytest.raises(IntegrityError),
    ):
        insert_price_change_events(classified, sqlite_events_engine)

    mock_sleep.assert_not_called()
