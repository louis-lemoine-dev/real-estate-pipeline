"""Tests for src/real_estate_pipeline/paruvendu/delist.py."""

from __future__ import annotations

from datetime import datetime
from typing import cast

import pandas as pd
import pytest
from sqlalchemy import Column, DateTime, MetaData, String, Table, create_engine, select
from sqlalchemy.engine import Engine

from real_estate_pipeline.paruvendu.delist import (
    _prepare_delisted_ids,
    mark_delisted_listings,
)

NOW = cast(pd.Timestamp, pd.Timestamp("2026-08-30T00:00:00Z"))
NOW_NAIVE = datetime(2026, 8, 30)
EARLIER = datetime(2026, 8, 25)


def _classified(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# --- _prepare_delisted_ids: pure data-shaping, no DB ---


def test_selects_only_delisted_rows():
    classified = _classified(
        [
            {"id": "L1", "diff_status": "delisted"},
            {"id": "L2", "diff_status": "unchanged"},
            {"id": "L3", "diff_status": "new"},
            {"id": "L4", "diff_status": "modified"},
        ]
    )

    assert _prepare_delisted_ids(classified) == ["L1"]


def test_already_marked_listings_are_left_out():
    """classify_listings keeps tagging a gone listing 'delisted' on every
    later run; the recorded date must never be moved forward.
    """
    classified = _classified(
        [
            {"id": "L1", "diff_status": "delisted", "delisted_at": pd.Timestamp("2026-08-25")},
            {"id": "L2", "diff_status": "delisted", "delisted_at": pd.NaT},
        ]
    )

    assert _prepare_delisted_ids(classified) == ["L2"]


def test_without_a_delisted_at_column_nothing_is_excluded():
    classified = _classified([{"id": "L1", "diff_status": "delisted"}])

    assert _prepare_delisted_ids(classified) == ["L1"]


def test_nothing_delisted_returns_empty_list():
    classified = _classified([{"id": "L1", "diff_status": "unchanged"}])

    assert _prepare_delisted_ids(classified) == []


# --- mark_delisted_listings: real DB behavior, against in-memory SQLite ---


@pytest.fixture
def sqlite_engine() -> Engine:
    """A minimal real table: this step only reads and writes id and
    delisted_at, so nothing else is needed to exercise it for real.
    """
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "listings",
        metadata,
        Column("id", String, primary_key=True),
        Column("delisted_at", DateTime, nullable=True),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO listings (id, delisted_at) VALUES "
            "('L1', NULL), ('L2', NULL), ('L3', '2026-08-25 00:00:00.000000')"
        )
    return engine


def _delisted_at(engine: Engine, listing_id: str) -> datetime | None:
    table = Table("listings", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        return connection.execute(
            select(table.c.delisted_at).where(table.c.id == listing_id)
        ).scalar_one()


def test_marks_only_the_confirmed_gone_listing(sqlite_engine: Engine):
    classified = _classified(
        [
            {"id": "L1", "diff_status": "delisted"},
            {"id": "L2", "diff_status": "unchanged"},
        ]
    )

    count = mark_delisted_listings(classified, sqlite_engine, now=NOW)

    assert count == 1
    assert _delisted_at(sqlite_engine, "L1") == NOW_NAIVE
    assert _delisted_at(sqlite_engine, "L2") is None


def test_filtering_stops_an_already_marked_listing_being_touched(sqlite_engine: Engine):
    classified = _classified(
        [{"id": "L3", "diff_status": "delisted", "delisted_at": pd.Timestamp("2026-08-25")}]
    )

    count = mark_delisted_listings(classified, sqlite_engine, now=NOW)

    assert count == 0
    assert _delisted_at(sqlite_engine, "L3") == EARLIER


def test_database_guard_protects_the_date_even_if_the_caller_passes_marked_rows(
    sqlite_engine: Engine,
):
    """Second line of defence: the frame carries no delisted_at column, so
    the Python-side filter cannot help, yet the UPDATE itself must still
    refuse to overwrite an existing date.
    """
    classified = _classified([{"id": "L3", "diff_status": "delisted"}])

    mark_delisted_listings(classified, sqlite_engine, now=NOW)

    assert _delisted_at(sqlite_engine, "L3") == EARLIER


def test_nothing_to_mark_opens_no_transaction():
    """With nothing to do the function must not touch the database at all:
    this engine has no `listings` table, so any access would raise.
    """
    engine = create_engine("sqlite:///:memory:")
    classified = _classified([{"id": "L1", "diff_status": "unchanged"}])

    assert mark_delisted_listings(classified, engine, now=NOW) == 0
