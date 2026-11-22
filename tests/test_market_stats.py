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
    DEFAULT_MIN_SAMPLE,
    MIN_PRICE,
    SALES_COLUMNS,
    SEGMENTS,
    TREND_COLUMNS,
    ComparableStats,
    _sales_frame,
    _window_start,
    comparable_stats,
    fetch_market_sales,
    latest_sale_date,
    median_price_per_m2_by_period,
    price_trend,
    summarize_comparables,
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


# --- median_price_per_m2_by_period: pure pandas, no database ---


def _sales(*sales: tuple[date, float]) -> pd.DataFrame:
    """A sales frame from (date, price per m2) pairs, each on 100 m2."""
    rows = [
        {"id_mutation": i, "date": d, "price": ppm2 * 100.0, "surface_bati": 100.0}
        for i, (d, ppm2) in enumerate(sales, start=1)
    ]
    return _sales_frame(rows)


def _n_sales(n: int, year: int = 2025, ppm2: float = 3000.0) -> pd.DataFrame:
    """n sales in one year, all at the same price per m2."""
    return _sales(*[(date(year, 1, 1 + i % 28), ppm2) for i in range(n)])


def test_default_threshold_is_thirty_sales():
    assert DEFAULT_MIN_SAMPLE == 30


def test_yearly_median_and_count():
    sales = _sales(
        (date(2023, 2, 1), 2000.0),
        (date(2023, 6, 1), 3000.0),
        (date(2023, 9, 1), 4000.0),
        (date(2024, 3, 1), 5000.0),
    )

    result = median_price_per_m2_by_period(sales, "year")

    assert list(result.columns) == TREND_COLUMNS
    assert result["period_start"].tolist() == [
        pd.Timestamp("2023-01-01"),
        pd.Timestamp("2024-01-01"),
    ]
    assert result["median_price_per_m2"].tolist() == [3000.0, 5000.0]
    assert result["sale_count"].tolist() == [3, 1]


def test_it_is_a_median_not_a_mean():
    sales = _sales(
        (date(2023, 1, 1), 2000.0),
        (date(2023, 1, 2), 2100.0),
        (date(2023, 1, 3), 2200.0),
        (date(2023, 1, 4), 50000.0),  # one exceptional sale
    )

    result = median_price_per_m2_by_period(sales, "year")

    # The mean would be 14,075. With an even count the median is the midpoint
    # of the two middle values, as Postgres' percentile_cont(0.5) gives.
    assert result["median_price_per_m2"].tolist() == [2150.0]


def test_quarters_split_on_the_quarter_boundary():
    sales = _sales(
        (date(2025, 3, 31), 1000.0),
        (date(2025, 4, 1), 2000.0),
        (date(2025, 12, 31), 3000.0),
        (date(2026, 1, 1), 4000.0),
    )

    result = median_price_per_m2_by_period(sales, "quarter")

    by_start = dict(zip(result["period_start"], result["sale_count"], strict=True))
    assert by_start[pd.Timestamp("2025-01-01")] == 1  # 31 March is still Q1
    assert by_start[pd.Timestamp("2025-04-01")] == 1  # 1 April starts Q2
    assert by_start[pd.Timestamp("2025-10-01")] == 1  # 31 December is still Q4
    assert by_start[pd.Timestamp("2026-01-01")] == 1  # 1 January starts the next year


def test_years_split_on_the_new_year():
    sales = _sales((date(2024, 12, 31), 1000.0), (date(2025, 1, 1), 2000.0))

    result = median_price_per_m2_by_period(sales, "year")

    assert result["median_price_per_m2"].tolist() == [1000.0, 2000.0]


def test_default_period_is_yearly():
    sales = _sales((date(2025, 1, 1), 1000.0), (date(2025, 8, 1), 3000.0))

    result = median_price_per_m2_by_period(sales)

    assert result["median_price_per_m2"].tolist() == [2000.0]


def test_input_order_does_not_matter():
    shuffled = _sales(
        (date(2024, 5, 1), 3000.0), (date(2024, 2, 1), 1000.0), (date(2023, 5, 1), 500.0)
    )

    result = median_price_per_m2_by_period(shuffled, "year")

    assert result["period_start"].tolist() == [
        pd.Timestamp("2023-01-01"),
        pd.Timestamp("2024-01-01"),
    ]
    assert result["median_price_per_m2"].tolist() == [500.0, 2000.0]


@pytest.mark.parametrize(("n", "expected_low"), [(29, True), (30, False), (31, False)])
def test_low_sample_flag_switches_at_the_threshold(n: int, expected_low: bool):
    result = median_price_per_m2_by_period(_n_sales(n), "year")

    assert result["sale_count"].tolist() == [n]
    assert result["low_sample"].tolist() == [expected_low]


def test_the_median_is_still_given_when_the_sample_is_low():
    """Louis's rule: always show the count, never remove the value."""
    result = median_price_per_m2_by_period(_n_sales(2, ppm2=2500.0), "year")

    assert result["low_sample"].tolist() == [True]
    assert result["median_price_per_m2"].tolist() == [2500.0]


def test_the_threshold_is_a_parameter():
    sales = _n_sales(5)

    assert median_price_per_m2_by_period(sales, "year", min_sample=5)["low_sample"].tolist() == [
        False
    ]
    assert median_price_per_m2_by_period(sales, "year", min_sample=6)["low_sample"].tolist() == [
        True
    ]


def test_a_period_with_no_sales_is_kept_with_no_median_and_count_zero():
    sales = _sales((date(2020, 5, 1), 1000.0), (date(2022, 5, 1), 3000.0))

    result = median_price_per_m2_by_period(sales, "year")

    assert result["period_start"].tolist() == [
        pd.Timestamp("2020-01-01"),
        pd.Timestamp("2021-01-01"),
        pd.Timestamp("2022-01-01"),
    ]
    assert result["sale_count"].tolist() == [1, 0, 1]
    assert pd.isna(result["median_price_per_m2"].iloc[1])
    assert result["low_sample"].tolist() == [True, True, True]  # 1, 0 and 1 are all below 30


def test_no_sales_gives_an_empty_frame_with_the_right_columns():
    result = median_price_per_m2_by_period(_sales_frame([]), "year")

    assert result.empty
    assert list(result.columns) == TREND_COLUMNS
    assert result["sale_count"].dtype == "int64"
    assert result["median_price_per_m2"].dtype == "float64"
    assert result["low_sample"].dtype == "bool"


def test_an_unknown_period_is_refused():
    with pytest.raises(ValueError, match="Unknown period 'month'"):
        median_price_per_m2_by_period(_n_sales(3), "month")


# --- price_trend: fetch and summarise together, against in-memory SQLite ---


def test_price_trend_uses_only_market_sales_of_the_commune_and_segment(sqlite_engine: Engine):
    _store(
        sqlite_engine,
        _transaction(1, price=120000.0),  # 2,000 per m2
        _transaction(2, price=180000.0),  # 3,000 per m2
        _transaction(3, price=600000.0, is_vefa=True, nature_mutation_code=2),  # new-build
        _transaction(4, price=900000.0, commune_code=HYERES),  # another commune
        _transaction(5, price=900000.0, property_type_code="111"),  # a house
        _transaction(6, price=1.0),  # below the price floor
    )

    result = price_trend(sqlite_engine, TOULON, "apartment")

    assert result["sale_count"].tolist() == [2]
    assert result["median_price_per_m2"].tolist() == [2500.0]


def test_price_trend_passes_period_threshold_and_dates_through(sqlite_engine: Engine):
    _store(
        sqlite_engine,
        _transaction(1, date=date(2024, 2, 1), year=2024),
        _transaction(2, date=date(2025, 2, 1)),
        _transaction(3, date=date(2025, 8, 1)),
        _transaction(4, date=date(2026, 2, 1), year=2026),
    )

    result = price_trend(
        sqlite_engine,
        TOULON,
        "apartment",
        period="quarter",
        min_sample=1,
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
    )

    # Quarters Q1 to Q3 of 2025 only; the 2024 and 2026 sales are out of range.
    assert result["period_start"].tolist() == [
        pd.Timestamp("2025-01-01"),
        pd.Timestamp("2025-04-01"),
        pd.Timestamp("2025-07-01"),
    ]
    assert result["sale_count"].tolist() == [1, 0, 1]
    # With a threshold of 1 only the empty quarter is low; the default of 30
    # would flag all three, so this proves min_sample is passed through.
    assert result["low_sample"].tolist() == [False, True, False]


# --- _window_start: the window arithmetic, no database ---


@pytest.mark.parametrize(
    ("window_end", "months", "expected"),
    [
        (date(2025, 12, 31), 12, date(2025, 1, 1)),  # a full calendar year
        (date(2025, 12, 31), 6, date(2025, 7, 1)),
        (date(2025, 3, 31), 1, date(2025, 3, 1)),  # one month ending on the 31st
        (date(2025, 2, 28), 12, date(2024, 2, 29)),  # a leap day inside the window
        (date(2024, 2, 29), 12, date(2023, 3, 1)),  # ending on a leap day
        (date(2025, 1, 15), 3, date(2024, 10, 16)),  # crosses a year boundary
    ],
)
def test_window_start(window_end: date, months: int, expected: date):
    assert _window_start(window_end, months) == expected


# --- summarize_comparables: pure pandas, no database ---

START = date(2025, 1, 1)
END = date(2025, 12, 31)


def _summary(sales: pd.DataFrame, **kwargs) -> ComparableStats:
    return summarize_comparables(sales, TOULON, "apartment", START, END, **kwargs)


def test_median_and_quartiles_interpolate_linearly():
    """1, 2, 3, 4 gives 2.5 for the median and 1.75 and 3.25 for the quartiles
    (what Postgres' percentile function gives), not the nearest values."""
    sales = _sales(*[(date(2025, 3, day), float(day)) for day in (1, 2, 3, 4)])

    stats = _summary(sales)

    assert stats.sale_count == 4
    assert stats.median_price_per_m2 == 2.5
    assert stats.q25_price_per_m2 == 1.75
    assert stats.q75_price_per_m2 == 3.25


def test_the_median_ignores_an_exceptional_sale():
    sales = _sales(*[(date(2025, 3, day), p) for day, p in enumerate([2000, 2100, 2200, 90000], 1)])

    assert _summary(sales).median_price_per_m2 == 2150.0


def test_the_summary_carries_the_commune_segment_and_window():
    stats = _summary(_n_sales(3))

    assert (stats.commune_code, stats.segment) == (TOULON, "apartment")
    assert (stats.window_start, stats.window_end) == (START, END)


@pytest.mark.parametrize(("n", "expected_low"), [(29, True), (30, False)])
def test_comparables_low_sample_flag_switches_at_the_threshold(n: int, expected_low: bool):
    assert _summary(_n_sales(n)).low_sample is expected_low


def test_comparables_still_give_the_figures_when_the_sample_is_low():
    stats = _summary(_n_sales(2, ppm2=2500.0))

    assert stats.low_sample is True
    assert stats.median_price_per_m2 == 2500.0
    assert stats.q25_price_per_m2 == 2500.0
    assert stats.q75_price_per_m2 == 2500.0


def test_comparables_threshold_is_a_parameter():
    assert _summary(_n_sales(5), min_sample=5).low_sample is False
    assert _summary(_n_sales(5), min_sample=6).low_sample is True


def test_no_sales_gives_count_zero_and_no_figures():
    stats = _summary(_sales_frame([]))

    assert stats.sale_count == 0
    assert stats.median_price_per_m2 is None
    assert stats.q25_price_per_m2 is None
    assert stats.q75_price_per_m2 is None
    assert stats.low_sample is True


def test_the_results_are_plain_python_numbers():
    stats = _summary(_n_sales(3))

    assert type(stats.sale_count) is int
    assert type(stats.median_price_per_m2) is float
    assert type(stats.q25_price_per_m2) is float
    assert type(stats.q75_price_per_m2) is float


# --- latest_sale_date and comparable_stats: against in-memory SQLite ---


def test_the_latest_sale_date_is_the_latest_over_the_whole_table(sqlite_engine: Engine):
    _store(
        sqlite_engine,
        _transaction(1, date=date(2024, 5, 1), year=2024),
        _transaction(2, date=date(2025, 11, 30), commune_code=HYERES),
        _transaction(3, date=date(2025, 6, 1)),
    )

    assert latest_sale_date(sqlite_engine) == date(2025, 11, 30)


def test_an_empty_table_has_no_latest_sale_date(sqlite_engine: Engine):
    with pytest.raises(ValueError, match="no sale"):
        latest_sale_date(sqlite_engine)


def test_the_window_includes_both_ends_and_nothing_beyond(sqlite_engine: Engine):
    _store(
        sqlite_engine,
        _transaction(1, date=date(2024, 12, 31), year=2024, price=60000.0),  # day before
        _transaction(2, date=date(2025, 1, 1), price=120000.0),  # first day
        _transaction(3, date=date(2025, 12, 31), price=180000.0),  # last day
        _transaction(4, date=date(2026, 1, 1), year=2026, price=600000.0),  # day after
    )

    stats = comparable_stats(sqlite_engine, TOULON, "apartment", as_of=date(2025, 12, 31))

    assert (stats.window_start, stats.window_end) == (date(2025, 1, 1), date(2025, 12, 31))
    assert stats.sale_count == 2
    assert stats.median_price_per_m2 == 2500.0  # 2,000 and 3,000 per m2


def test_the_default_end_is_the_latest_sale_in_the_whole_table(sqlite_engine: Engine):
    """The latest sale belongs to another commune and another segment, yet it
    sets the window for Toulon apartments: every commune and segment is
    compared over the same window."""
    _store(
        sqlite_engine,
        _transaction(1, date=date(2024, 12, 30), year=2024),  # outside a year ending 31 Dec 2025
        _transaction(2, date=date(2025, 3, 1)),
        _transaction(
            3,
            date=date(2025, 12, 31),
            commune_code=HYERES,
            property_type_code="111",
            property_type_label="UNE MAISON",
        ),
    )

    stats = comparable_stats(sqlite_engine, TOULON, "apartment")

    assert stats.window_end == date(2025, 12, 31)
    assert stats.window_start == date(2025, 1, 1)
    assert stats.sale_count == 1


def test_as_of_overrides_the_default_end(sqlite_engine: Engine):
    _store(
        sqlite_engine,
        _transaction(1, date=date(2024, 6, 1), year=2024),
        _transaction(2, date=date(2025, 6, 1)),
    )

    stats = comparable_stats(sqlite_engine, TOULON, "apartment", as_of=date(2024, 12, 31))

    assert stats.window_end == date(2024, 12, 31)
    assert stats.sale_count == 1


def test_window_months_is_used(sqlite_engine: Engine):
    _store(
        sqlite_engine,
        _transaction(1, date=date(2025, 2, 1)),
        _transaction(2, date=date(2025, 10, 1)),
    )

    year = comparable_stats(sqlite_engine, TOULON, "apartment", as_of=date(2025, 12, 31))
    half = comparable_stats(
        sqlite_engine, TOULON, "apartment", window_months=6, as_of=date(2025, 12, 31)
    )

    assert (year.sale_count, half.sale_count) == (2, 1)
    assert half.window_start == date(2025, 7, 1)


def test_min_sample_is_passed_through(sqlite_engine: Engine):
    _store(sqlite_engine, _transaction(1), _transaction(2), _transaction(3))

    default = comparable_stats(sqlite_engine, TOULON, "apartment")
    custom = comparable_stats(sqlite_engine, TOULON, "apartment", min_sample=3)

    assert (default.low_sample, custom.low_sample) == (True, False)


def test_only_resale_sales_of_the_right_commune_and_segment_count(sqlite_engine: Engine):
    _store(
        sqlite_engine,
        _transaction(1),  # counts
        _transaction(2, commune_code=HYERES),
        _transaction(3, property_type_code="111", property_type_label="UNE MAISON"),
        _transaction(4, is_vefa=True, nature_mutation_code=2),
        _transaction(5, price=1.0),
        _transaction(6, surface_bati=0.0),
    )

    assert comparable_stats(sqlite_engine, TOULON, "apartment").sale_count == 1


def test_a_window_with_no_sales_gives_count_zero(sqlite_engine: Engine):
    _store(sqlite_engine, _transaction(1, date=date(2020, 1, 1), year=2020))

    stats = comparable_stats(sqlite_engine, TOULON, "apartment", as_of=date(2025, 12, 31))

    assert stats.sale_count == 0
    assert stats.median_price_per_m2 is None


def test_no_end_and_an_empty_table_is_refused_but_an_explicit_end_is_fine(sqlite_engine: Engine):
    with pytest.raises(ValueError, match="no sale"):
        comparable_stats(sqlite_engine, TOULON, "apartment")

    stats = comparable_stats(sqlite_engine, TOULON, "apartment", as_of=date(2025, 12, 31))
    assert stats.sale_count == 0


@pytest.mark.parametrize("months", [0, -3])
def test_a_window_of_less_than_one_month_is_refused(sqlite_engine: Engine, months: int):
    with pytest.raises(ValueError, match="window_months must be at least 1"):
        comparable_stats(sqlite_engine, TOULON, "apartment", window_months=months)


def test_comparable_stats_refuses_an_unknown_commune_or_segment(sqlite_engine: Engine):
    _store(sqlite_engine, _transaction(1))

    with pytest.raises(ValueError, match="Unknown commune code"):
        comparable_stats(sqlite_engine, "83999", "apartment")
    with pytest.raises(ValueError, match="Unknown segment"):
        comparable_stats(sqlite_engine, TOULON, "castle")
