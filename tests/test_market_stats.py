"""Tests for src/real_estate_pipeline/analysis/market.py."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

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
)
from sqlalchemy.engine import Engine

from real_estate_pipeline.analysis.market import (
    MIN_PRICE,
    SALES_COLUMNS,
    SEGMENTS,
    _sales_frame,
    fetch_market_sales,
)
from real_estate_pipeline.dvf.cleaning import clean_transactions
from real_estate_pipeline.dvf.load import load_transactions
from real_estate_pipeline.dvf.models import Transaction

TOULON = "83137"
HYERES = "83069"


def _transaction(id_mutation: int, **overrides: Any) -> Transaction:
    """A resale apartment in Toulon: 120,000 euros for 60 m2 (2,000 per m2)."""
    base = Transaction(
        id_mutation=id_mutation,
        date=date(2025, 3, 14),
        year=2025,
        price=120000.0,
        is_vefa=False,
        nature_mutation_code=1,
        nature_mutation_label="Vente",
        property_type_code="121",
        property_type_label="UN APPARTEMENT",
        surface_bati=60.0,
        surface_terrain=0.0,
        rooms=3,
        commune_code=TOULON,
    )
    return replace(base, **overrides)


@pytest.fixture
def sqlite_engine() -> Engine:
    """In-memory `transactions` table shaped like the real one (stand-ins:
    Integer for the bigint key, Float for numeric)."""
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


def _store(engine: Engine, *transactions: Transaction) -> None:
    """Through the real cleaning and loading code, so stored rows look like
    production ones."""
    load_transactions(clean_transactions(list(transactions)), engine)


def _ids(frame: pd.DataFrame) -> list[int]:
    return [int(i) for i in frame["id_mutation"]]


# --- segments and the pure frame builder: no database ---


def test_two_segments_single_apartments_and_single_houses():
    assert {name: s.property_type_code for name, s in SEGMENTS.items()} == {
        "apartment": "121",
        "house": "111",
    }


def test_sales_frame_turns_decimal_into_float_and_computes_price_per_m2():
    """Postgres numeric comes back as Decimal; SQLite never shows that."""
    rows = [
        {
            "id_mutation": 7,
            "date": date(2025, 1, 2),
            "price": Decimal("250000.00"),
            "surface_bati": Decimal("62.50"),
        }
    ]

    frame = _sales_frame(rows)

    assert list(frame.columns) == SALES_COLUMNS
    assert frame["price"].dtype == "float64"
    assert frame["surface_bati"].dtype == "float64"
    assert frame["price_per_m2"].iloc[0] == 4000.0
    assert frame["date"].iloc[0] == pd.Timestamp("2025-01-02")


def test_sales_frame_of_nothing_keeps_its_columns_and_dtypes():
    frame = _sales_frame([])

    assert list(frame.columns) == SALES_COLUMNS
    assert frame.empty
    assert frame["price_per_m2"].dtype == "float64"
    assert frame["id_mutation"].dtype == "int64"


# --- fetch_market_sales: real query behaviour, against in-memory SQLite ---


def test_returns_a_plain_resale_apartment_with_its_price_per_m2(sqlite_engine: Engine):
    _store(sqlite_engine, _transaction(1))

    sales = fetch_market_sales(sqlite_engine, TOULON, "apartment")

    assert _ids(sales) == [1]
    assert sales["price"].iloc[0] == 120000.0
    assert sales["surface_bati"].iloc[0] == 60.0
    assert sales["price_per_m2"].iloc[0] == 2000.0


def test_resale_segment_leaves_out_new_build(sqlite_engine: Engine):
    _store(
        sqlite_engine,
        _transaction(1),
        _transaction(2, is_vefa=True, nature_mutation_code=2, nature_mutation_label="VEFA"),
    )

    assert _ids(fetch_market_sales(sqlite_engine, TOULON, "apartment")) == [1]


def test_a_row_flagged_new_build_by_only_one_of_the_two_fields_is_left_out(
    sqlite_engine: Engine,
):
    """is_vefa and nature code 2 agree on all real data. If they ever disagree
    the row is left out rather than counted as resale on a guess."""
    _store(
        sqlite_engine,
        _transaction(1),
        _transaction(2, is_vefa=True, nature_mutation_code=1),
        _transaction(3, is_vefa=False, nature_mutation_code=2, nature_mutation_label="VEFA"),
    )

    assert _ids(fetch_market_sales(sqlite_engine, TOULON, "apartment")) == [1]


@pytest.mark.parametrize(
    ("code", "label"),
    [(3, "Expropriation"), (4, "Vente terrain à bâtir"), (5, "Adjudication"), (6, "Echange")],
)
def test_non_ordinary_sales_are_never_counted(sqlite_engine: Engine, code: int, label: str):
    _store(
        sqlite_engine,
        _transaction(1),
        _transaction(2, nature_mutation_code=code, nature_mutation_label=label),
        _transaction(3, is_vefa=True, nature_mutation_code=code, nature_mutation_label=label),
    )

    assert _ids(fetch_market_sales(sqlite_engine, TOULON, "apartment")) == [1]


def test_price_floor_is_inclusive_and_missing_prices_are_left_out(sqlite_engine: Engine):
    # Literal prices, not built from MIN_PRICE: a test that follows the
    # constant would not notice if the constant itself were changed.
    assert MIN_PRICE == 2
    _store(
        sqlite_engine,
        _transaction(1, price=1.0),
        _transaction(2, price=2.0),
        _transaction(3, price=None),
        _transaction(4, price=0.0),
    )

    assert _ids(fetch_market_sales(sqlite_engine, TOULON, "apartment")) == [2]


def test_bare_land_with_no_built_surface_is_left_out(sqlite_engine: Engine):
    _store(sqlite_engine, _transaction(1), _transaction(2, surface_bati=0.0))

    assert _ids(fetch_market_sales(sqlite_engine, TOULON, "apartment")) == [1]


def test_only_the_segments_property_type_is_returned(sqlite_engine: Engine):
    _store(
        sqlite_engine,
        _transaction(1),
        _transaction(2, property_type_code="111", property_type_label="UNE MAISON"),
        _transaction(3, property_type_code="122", property_type_label="DEUX APPARTEMENTS"),
    )

    assert _ids(fetch_market_sales(sqlite_engine, TOULON, "apartment")) == [1]
    assert _ids(fetch_market_sales(sqlite_engine, TOULON, "house")) == [2]


def test_only_the_requested_commune_is_returned(sqlite_engine: Engine):
    _store(sqlite_engine, _transaction(1), _transaction(2, commune_code=HYERES))

    assert _ids(fetch_market_sales(sqlite_engine, TOULON, "apartment")) == [1]
    assert _ids(fetch_market_sales(sqlite_engine, HYERES, "apartment")) == [2]


def test_date_range_is_inclusive_at_both_ends(sqlite_engine: Engine):
    _store(
        sqlite_engine,
        _transaction(1, date=date(2024, 12, 31), year=2024),
        _transaction(2, date=date(2025, 1, 1)),
        _transaction(3, date=date(2025, 6, 30)),
        _transaction(4, date=date(2025, 12, 31)),
        _transaction(5, date=date(2026, 1, 1), year=2026),
    )

    sales = fetch_market_sales(
        sqlite_engine, TOULON, "apartment", start=date(2025, 1, 1), end=date(2025, 12, 31)
    )

    assert _ids(sales) == [2, 3, 4]


def test_open_ended_ranges(sqlite_engine: Engine):
    _store(
        sqlite_engine,
        _transaction(1, date=date(2024, 5, 1), year=2024),
        _transaction(2, date=date(2025, 5, 1)),
    )

    assert _ids(fetch_market_sales(sqlite_engine, TOULON, "apartment")) == [1, 2]
    assert _ids(fetch_market_sales(sqlite_engine, TOULON, "apartment", start=date(2025, 1, 1))) == [
        2
    ]
    assert _ids(fetch_market_sales(sqlite_engine, TOULON, "apartment", end=date(2024, 12, 31))) == [
        1
    ]


def test_sales_come_back_oldest_first(sqlite_engine: Engine):
    _store(
        sqlite_engine,
        _transaction(1, date=date(2025, 9, 1)),
        _transaction(2, date=date(2023, 2, 1), year=2023),
        _transaction(3, date=date(2023, 2, 1), year=2023),
    )

    assert _ids(fetch_market_sales(sqlite_engine, TOULON, "apartment")) == [2, 3, 1]


def test_nothing_matching_gives_an_empty_frame_with_the_right_columns(sqlite_engine: Engine):
    _store(sqlite_engine, _transaction(1))

    sales = fetch_market_sales(sqlite_engine, TOULON, "house")

    assert sales.empty
    assert list(sales.columns) == SALES_COLUMNS


def test_a_commune_outside_the_twelve_is_refused(sqlite_engine: Engine):
    with pytest.raises(ValueError, match="Unknown commune code '83999'"):
        fetch_market_sales(sqlite_engine, "83999", "apartment")


def test_an_unknown_segment_is_refused(sqlite_engine: Engine):
    with pytest.raises(ValueError, match="Unknown segment 'castle'"):
        fetch_market_sales(sqlite_engine, TOULON, "castle")
