"""Tests for src/real_estate_pipeline/dvf/load.py."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError

from real_estate_pipeline.dvf.cleaning import clean_transactions
from real_estate_pipeline.dvf.load import (
    _CONVERTERS,
    LoadSummary,
    _classify,
    _normalize,
    _prepare_transaction_records,
    load_transactions,
)
from real_estate_pipeline.dvf.models import TRANSACTION_COLUMNS, Transaction

# Kept so a test can wrap the real classifier and make it fail on demand.
_REAL_CLASSIFY = _classify


def _transaction(id_mutation: int, **overrides: Any) -> Transaction:
    """A complete Transaction; tests only spell out what they care about."""
    base = Transaction(
        id_mutation=id_mutation,
        date=date(2025, 3, 14),
        year=2025,
        price=250000.0,
        is_vefa=False,
        nature_mutation_code=1,
        nature_mutation_label="Vente",
        property_type_code="1",
        property_type_label="UN APPARTEMENT",
        surface_bati=60.0,
        surface_terrain=0.0,
        rooms=3,
        commune_code="83103",
    )
    return replace(base, **overrides)


def _cleaned(*transactions: Transaction) -> pd.DataFrame:
    """Through the real cleaning step, so dtypes match production: 16 columns,
    rooms a float column as soon as one value is missing, and so on.
    """
    return clean_transactions(list(transactions))


# --- _prepare_transaction_records: pure data-shaping, no DB ---


def test_every_transaction_column_has_a_converter():
    """Fails loudly if a field is added to Transaction without a converter."""
    assert set(_CONVERTERS) == set(TRANSACTION_COLUMNS)


def test_keeps_exactly_the_transaction_columns():
    """An allow-list: the three derived columns of the cleaned frame
    (price_per_m2, is_single_residential_unit, is_market_transaction) never
    reach the table.
    """
    cleaned = _cleaned(_transaction(1))
    assert {"price_per_m2", "is_single_residential_unit", "is_market_transaction"} <= set(
        cleaned.columns
    )

    records = _prepare_transaction_records(cleaned)

    assert set(records[0]) == set(TRANSACTION_COLUMNS)


def test_empty_frame_returns_no_records():
    assert _prepare_transaction_records(pd.DataFrame()) == []


def test_missing_column_raises_naming_it():
    cleaned = _cleaned(_transaction(1)).drop(columns=["surface_bati", "commune_code"])

    with pytest.raises(ValueError, match=r"surface_bati.*commune_code"):
        _prepare_transaction_records(cleaned)


def test_duplicate_id_mutation_raises():
    cleaned = _cleaned(_transaction(7), _transaction(8), _transaction(7, price=1.0))

    with pytest.raises(ValueError, match=r"duplicate id_mutation.*7"):
        _prepare_transaction_records(cleaned)


def test_null_in_a_not_null_column_raises_naming_column_and_id():
    cleaned = _cleaned(_transaction(1), _transaction(2))
    cleaned.loc[1, "surface_bati"] = float("nan")

    with pytest.raises(ValueError, match=r"surface_bati.*2"):
        _prepare_transaction_records(cleaned)


def test_nan_becomes_none_for_price_and_rooms():
    """The 3.3.2 lesson, on the pure side: SQLite binds NaN as NULL on its
    own, so only this test (not a DB test) proves NaN cannot reach Postgres.
    """
    cleaned = _cleaned(_transaction(1, price=None, rooms=None), _transaction(2))
    assert cleaned["price"].dtype == "float64"  # NaN, the case that leaks

    records = _prepare_transaction_records(cleaned)

    assert records[0]["price"] is None
    assert records[0]["rooms"] is None
    assert records[1]["price"] == 250000.0
    assert records[1]["rooms"] == 3


def test_records_hold_plain_python_types():
    """rooms is 3, not 3.0 (the column is float64 once one row has no rooms),
    and nothing is a numpy type.
    """
    cleaned = _cleaned(_transaction(1), _transaction(2, rooms=None))

    record = _prepare_transaction_records(cleaned)[0]

    assert type(record["id_mutation"]) is int
    assert type(record["year"]) is int
    assert type(record["nature_mutation_code"]) is int
    assert type(record["rooms"]) is int
    assert type(record["price"]) is float
    assert type(record["surface_bati"]) is float
    assert type(record["surface_terrain"]) is float
    assert type(record["is_vefa"]) is bool
    assert type(record["date"]) is date
    assert type(record["commune_code"]) is str


def test_non_whole_rooms_is_refused_not_truncated():
    cleaned = _cleaned(_transaction(1), _transaction(2, rooms=None))
    cleaned.loc[0, "rooms"] = 2.5

    with pytest.raises(ValueError, match=r"rooms.*id_mutation 1"):
        _prepare_transaction_records(cleaned)


# --- _normalize and _classify: pure comparison logic, no DB ---


def test_normalize_makes_decimal_float_and_missing_values_comparable():
    assert _normalize(Decimal("250000.00")) == _normalize(250000.0)
    assert _normalize(Decimal("60.1234567")) == _normalize(60.1234567)
    # Representation noise far below a cent or a square millimetre is not a change.
    assert _normalize(60.0000001) == _normalize(Decimal("60.0"))
    assert _normalize(float("nan")) is None
    assert _normalize(None) is None
    assert _normalize(True) is True


def test_normalize_still_tells_a_real_difference_apart():
    assert _normalize(Decimal("60.000001")) != _normalize(60.0)
    assert _normalize(Decimal("250000.00")) != _normalize(250000.5)
    assert _normalize(None) != _normalize(0.0)


def test_classify_splits_new_changed_and_unchanged():
    """Stored values come back as Decimal, as Postgres numeric would."""
    records = _prepare_transaction_records(
        _cleaned(_transaction(1), _transaction(2, price=260000.0), _transaction(3))
    )
    stored = {
        1: {**records[0], "price": Decimal("250000.00"), "surface_bati": Decimal("60")},
        2: {**records[1], "price": Decimal("250000.00")},  # the incoming price differs
    }

    new, changed, unchanged = _classify(records, stored)

    assert [r["id_mutation"] for r in new] == [3]
    assert [r["id_mutation"] for r in changed] == [2]
    assert [r["id_mutation"] for r in unchanged] == [1]


# --- load_transactions: real DB behaviour, against in-memory SQLite ---


@pytest.fixture
def sqlite_engine() -> Engine:
    """A real in-memory engine with a `transactions` table shaped like the
    real one: the 13 columns, price and rooms nullable, everything else NOT
    NULL. Stand-ins: SQLite has no BigInteger primary key, so Integer; money
    and surfaces are Float, not numeric.
    """
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "transactions",
        metadata,
        Column("id_mutation", Integer, primary_key=True, autoincrement=False),
        Column("date", Date, nullable=False),
        Column("year", Integer, nullable=False),
        Column("price", Float, nullable=True),
        Column("is_vefa", Boolean, nullable=False),
        Column("nature_mutation_code", Integer, nullable=False),
        Column("nature_mutation_label", String, nullable=False),
        Column("property_type_code", String, nullable=False),
        Column("property_type_label", String, nullable=False),
        Column("surface_bati", Float, nullable=False),
        Column("surface_terrain", Float, nullable=False),
        Column("rooms", Integer, nullable=True),
        Column("commune_code", String, nullable=False),
    )
    metadata.create_all(engine)
    return engine


def _stored(engine: Engine) -> list[dict]:
    table = Table("transactions", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        rows = connection.execute(select(table).order_by(table.c.id_mutation)).mappings()
        return [dict(row) for row in rows]


def test_first_load_inserts_every_row(sqlite_engine: Engine):
    cleaned = _cleaned(_transaction(1), _transaction(2), _transaction(3))

    summary = load_transactions(cleaned, sqlite_engine)

    assert summary == LoadSummary(inserted=3, updated=0, unchanged=0)
    assert [r["id_mutation"] for r in _stored(sqlite_engine)] == [1, 2, 3]


def test_identical_reload_changes_nothing(sqlite_engine: Engine):
    cleaned = _cleaned(_transaction(1), _transaction(2), _transaction(3))
    load_transactions(cleaned, sqlite_engine)
    after_first = _stored(sqlite_engine)

    summary = load_transactions(cleaned, sqlite_engine)

    assert summary == LoadSummary(inserted=0, updated=0, unchanged=3)
    assert _stored(sqlite_engine) == after_first


def test_mixed_load_inserts_only_the_new_rows(sqlite_engine: Engine):
    load_transactions(_cleaned(_transaction(1), _transaction(2)), sqlite_engine)

    summary = load_transactions(
        _cleaned(_transaction(1), _transaction(2), _transaction(3)), sqlite_engine
    )

    assert summary == LoadSummary(inserted=1, updated=0, unchanged=2)
    assert [r["id_mutation"] for r in _stored(sqlite_engine)] == [1, 2, 3]


def test_revised_row_is_updated_and_the_others_are_not(sqlite_engine: Engine):
    load_transactions(_cleaned(_transaction(1), _transaction(2), _transaction(3)), sqlite_engine)
    before = {r["id_mutation"]: r for r in _stored(sqlite_engine)}

    summary = load_transactions(
        _cleaned(_transaction(1), _transaction(2, price=260000.0), _transaction(3)),
        sqlite_engine,
    )

    assert summary == LoadSummary(inserted=0, updated=1, unchanged=2)
    after = {r["id_mutation"]: r for r in _stored(sqlite_engine)}
    assert after[2]["price"] == 260000.0
    assert after[1] == before[1]
    assert after[3] == before[3]


@pytest.mark.parametrize(
    ("column", "new_value"),
    [
        ("date", date(2025, 4, 1)),
        ("year", 2024),
        ("price", None),
        ("is_vefa", True),
        ("nature_mutation_code", 2),
        ("nature_mutation_label", "VEFA"),
        ("property_type_code", "2"),
        ("property_type_label", "UNE MAISON"),
        ("surface_bati", 75.0),
        ("surface_terrain", 120.0),
        ("rooms", 4),
        ("commune_code", "83137"),
    ],
)
def test_a_change_in_any_single_column_is_detected_and_stored(
    sqlite_engine: Engine, column: str, new_value: Any
):
    """The comparison must look at every column, not just the price: a
    revised mutation can change anywhere, and each must reach the table.
    """
    load_transactions(_cleaned(_transaction(1), _transaction(2)), sqlite_engine)

    revised = _cleaned(_transaction(1), _transaction(2, **{column: new_value}))
    summary = load_transactions(revised, sqlite_engine)

    assert summary == LoadSummary(inserted=0, updated=1, unchanged=1)
    assert _stored(sqlite_engine)[1][column] == new_value


def test_null_price_is_stored_as_null_and_reloads_unchanged(sqlite_engine: Engine):
    cleaned = _cleaned(_transaction(1, price=None, rooms=None), _transaction(2))

    load_transactions(cleaned, sqlite_engine)

    first = _stored(sqlite_engine)[0]
    assert first["price"] is None
    assert first["rooms"] is None
    # None on both sides must compare equal, not look like a change.
    assert load_transactions(cleaned, sqlite_engine) == LoadSummary(0, 0, 2)


def test_several_batches_give_the_right_totals(sqlite_engine: Engine):
    transactions = [_transaction(i) for i in range(1, 6)]  # 5 rows -> batches of 2, 2, 1
    assert load_transactions(_cleaned(*transactions), sqlite_engine, batch_size=2) == LoadSummary(
        inserted=5, updated=0, unchanged=0
    )

    transactions[4] = _transaction(5, price=1.0)  # revised row lives in the last batch
    summary = load_transactions(_cleaned(*transactions), sqlite_engine, batch_size=2)

    assert summary == LoadSummary(inserted=0, updated=1, unchanged=4)
    assert len(_stored(sqlite_engine)) == 5


def test_failure_in_a_later_batch_rolls_back_the_earlier_batches(sqlite_engine: Engine):
    """Batch 1 is really inserted, then batch 2 fails: nothing may remain,
    and since IntegrityError is a data problem it must not be retried.
    """
    calls = {"n": 0}

    def fail_on_second_batch(records, stored):
        calls["n"] += 1
        if calls["n"] == 2:
            raise IntegrityError("simulated", {}, Exception("constraint failed"))
        return _REAL_CLASSIFY(records, stored)

    cleaned = _cleaned(*[_transaction(i) for i in range(1, 5)])

    with (
        patch("real_estate_pipeline.dvf.load._classify", side_effect=fail_on_second_batch),
        patch("real_estate_pipeline.common.db.time.sleep") as mock_sleep,
        pytest.raises(IntegrityError),
    ):
        load_transactions(cleaned, sqlite_engine, batch_size=2)

    assert _stored(sqlite_engine) == []
    assert calls["n"] == 2  # no second attempt
    mock_sleep.assert_not_called()


def test_a_retried_attempt_does_not_double_count(sqlite_engine: Engine):
    """A transient connection error in batch 2 rolls batch 1 back and the
    whole attempt is rerun: the summary must count each row once.
    """
    calls = {"n": 0}

    def flaky_second_call(records, stored):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OperationalError("simulated drop", {}, Exception("connection reset"))
        return _REAL_CLASSIFY(records, stored)

    cleaned = _cleaned(*[_transaction(i) for i in range(1, 4)])

    with (
        patch("real_estate_pipeline.dvf.load._classify", side_effect=flaky_second_call),
        patch("real_estate_pipeline.common.db.time.sleep") as mock_sleep,
    ):
        summary = load_transactions(cleaned, sqlite_engine, batch_size=2)

    assert summary == LoadSummary(inserted=3, updated=0, unchanged=0)
    assert len(_stored(sqlite_engine)) == 3
    mock_sleep.assert_called_once()


def test_rows_absent_from_the_input_are_never_deleted(sqlite_engine: Engine):
    load_transactions(_cleaned(_transaction(1), _transaction(2), _transaction(3)), sqlite_engine)

    summary = load_transactions(_cleaned(_transaction(3)), sqlite_engine)

    assert summary == LoadSummary(inserted=0, updated=0, unchanged=1)
    assert [r["id_mutation"] for r in _stored(sqlite_engine)] == [1, 2, 3]


def test_malformed_input_is_refused_and_nothing_is_written(sqlite_engine: Engine):
    cleaned = _cleaned(_transaction(1), _transaction(2), _transaction(1, price=1.0))

    with pytest.raises(ValueError, match="duplicate"):
        load_transactions(cleaned, sqlite_engine)

    assert _stored(sqlite_engine) == []


def test_empty_input_opens_no_transaction(sqlite_engine: Engine):
    with patch("real_estate_pipeline.dvf.load.run_in_transaction") as mock_run:
        summary = load_transactions(pd.DataFrame(), sqlite_engine)

    assert summary == LoadSummary(inserted=0, updated=0, unchanged=0)
    mock_run.assert_not_called()


def test_batch_size_below_one_is_refused(sqlite_engine: Engine):
    with pytest.raises(ValueError, match="batch_size"):
        load_transactions(_cleaned(_transaction(1)), sqlite_engine, batch_size=0)
