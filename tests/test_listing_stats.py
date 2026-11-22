"""Tests for src/real_estate_pipeline/analysis/listings.py."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

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
)
from sqlalchemy.engine import Engine

from real_estate_pipeline.analysis.listings import (
    COMMUNE_NAMES,
    PROPERTY_TYPE_SEGMENTS,
    SUMMARY_COLUMNS,
    commune_code_from_location,
    listing_stats,
    match_listings,
    summarize_listings,
)
from real_estate_pipeline.analysis.market import SEGMENTS
from real_estate_pipeline.dvf.orchestrator import DEFAULT_COMMUNE_CODES

TOULON = "83137"
LE_REVEST = "83103"


# --- the two lookup tables ---


def test_the_name_table_covers_exactly_the_twelve_communes():
    assert set(COMMUNE_NAMES) == set(DEFAULT_COMMUNE_CODES)


def test_the_commune_names_are_the_expected_ones():
    """Spelled out, so a typo in a name cannot hide behind a test that reads
    the same table."""
    assert COMMUNE_NAMES == {
        "83034": "Carqueiranne",
        "83047": "La Crau",
        "83062": "La Garde",
        "83069": "Hyères",
        "83090": "Ollioules",
        "83098": "Le Pradet",
        "83103": "Le Revest-les-Eaux",
        "83126": "La Seyne-sur-Mer",
        "83129": "Six-Fours-les-Plages",
        "83137": "Toulon",
        "83144": "La Valette-du-Var",
        "83153": "Saint-Mandrier-sur-Mer",
    }


def test_the_property_type_labels_are_the_expected_ones():
    assert PROPERTY_TYPE_SEGMENTS == {
        "Appartement": "apartment",
        "Maison": "house",
        "Villa": "house",
    }


def test_every_mapped_segment_is_a_sold_price_segment():
    assert set(PROPERTY_TYPE_SEGMENTS.values()) <= set(SEGMENTS)


@pytest.mark.parametrize(("code", "name"), list(COMMUNE_NAMES.items()))
def test_each_commune_is_found_from_its_location_text(code: str, name: str):
    assert commune_code_from_location(f"{name} (83)") == code


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("TOULON (83)", TOULON),  # capitals do not matter
        ("toulon (83)", TOULON),
        ("Hyeres (83)", "83069"),  # nor accents
        ("Hyères(83)", "83069"),  # nor the space before the bracket
        ("La Seyne sur Mer (83)", "83126"),  # nor hyphens
        ("  Toulon (83)  ", TOULON),  # nor spaces around
        ("Le Revest-les-Eaux (83)", LE_REVEST),
    ],
)
def test_location_text_variants(text: str, code: str):
    assert commune_code_from_location(text) == code


@pytest.mark.parametrize(
    "text",
    [
        "Toulon (13)",  # another department
        "Marseille (83)",  # a commune outside the twelve
        "Toulon",  # no department
        "Toulon (83) centre",  # not the expected shape
        "(83)",  # no name
        "",
        None,
    ],
)
def test_location_text_that_cannot_be_matched(text: str | None):
    assert commune_code_from_location(text) is None


# --- match_listings ---


def _listings(*rows: dict) -> pd.DataFrame:
    """Stored-listing rows with neutral defaults: a 50 m2 apartment in Toulon."""
    defaults = {
        "property_type": "Appartement",
        "location": "Toulon (83)",
        "surface_m2": Decimal("50"),
        "price": 100000,
    }
    return pd.DataFrame([{"id": f"L{i}", **defaults, **row} for i, row in enumerate(rows, start=1)])


def _at(price_per_m2: float, **row) -> dict:
    """A 50 m2 listing whose price per m2 is the given value."""
    return {"price": int(price_per_m2 * 50), **row}


def test_property_types_map_to_segments():
    listings = _listings(
        {"property_type": "Appartement"},
        {"property_type": "Maison"},
        {"property_type": "Villa"},
        {"property_type": "Immeuble"},
        {"property_type": "Parking / Garage"},
        {"property_type": None},
    )

    segments = match_listings(listings)["segment"]

    assert segments.iloc[:3].tolist() == ["apartment", "house", "house"]
    assert segments.iloc[3:].isna().all()  # building, parking and no label: no segment


def test_the_commune_code_is_added():
    listings = _listings({}, {"location": "Le Revest-les-Eaux (83)"}, {"location": "Nice (06)"})

    codes = match_listings(listings)["commune_code"]

    assert codes.iloc[:2].tolist() == [TOULON, LE_REVEST]
    assert pd.isna(codes.iloc[2])


def test_price_per_m2_from_a_decimal_surface():
    """Postgres returns the surface as a Decimal; it must become a plain float."""
    listings = _listings({"price": 150000, "surface_m2": Decimal("62.50")})

    price_per_m2 = match_listings(listings)["price_per_m2"]

    assert price_per_m2.dtype == "float64"
    assert price_per_m2.iloc[0] == 2400.0


def test_no_price_per_m2_without_a_usable_surface():
    listings = _listings({"surface_m2": None}, {"surface_m2": Decimal("0")}, {})

    price_per_m2 = match_listings(listings)["price_per_m2"]

    assert pd.isna(price_per_m2.iloc[0])
    assert pd.isna(price_per_m2.iloc[1])
    assert price_per_m2.iloc[2] == 2000.0


def test_matching_does_not_change_the_input():
    listings = _listings({})

    match_listings(listings)

    assert list(listings.columns) == ["id", "property_type", "location", "surface_m2", "price"]


# --- summarize_listings: pure pandas, no database ---


def _no_events() -> pd.DataFrame:
    return pd.DataFrame({"listing_id": [], "delta_eur": []})


def _events(*pairs: tuple[str, int]) -> pd.DataFrame:
    return pd.DataFrame(pairs, columns=["listing_id", "delta_eur"])


def _summary(listings: pd.DataFrame, events: pd.DataFrame | None = None, **kwargs):
    events = _no_events() if events is None else events
    return summarize_listings(match_listings(listings), events, **kwargs)


def test_median_and_quartiles_interpolate_linearly():
    """1,000 to 4,000 euros/m2 give 2,500 and the quartiles 1,750 and 3,250."""
    listings = _listings(*[_at(ppm2) for ppm2 in (1000.0, 2000.0, 3000.0, 4000.0)])

    row = _summary(listings).iloc[0]

    assert list(_summary(listings).columns) == SUMMARY_COLUMNS
    assert (row["commune_code"], row["segment"]) == (TOULON, "apartment")
    assert row["median_price_per_m2"] == 2500.0
    assert row["q25_price_per_m2"] == 1750.0
    assert row["q75_price_per_m2"] == 3250.0


def test_the_median_is_not_pulled_by_one_exceptional_listing():
    listings = _listings(*[_at(ppm2) for ppm2 in (2000.0, 2100.0, 2200.0, 90000.0)])

    assert _summary(listings).iloc[0]["median_price_per_m2"] == 2150.0


def test_listings_without_a_surface_count_as_live_but_not_in_the_price_figures():
    listings = _listings(_at(2000.0), _at(4000.0), {"surface_m2": None})

    row = _summary(listings).iloc[0]

    assert (row["live_count"], row["with_surface_count"]) == (3, 2)
    assert row["median_price_per_m2"] == 3000.0


@pytest.mark.parametrize(("n", "expected_low"), [(29, True), (30, False)])
def test_low_sample_switches_at_the_threshold(n: int, expected_low: bool):
    listings = _listings(*[_at(3000.0) for _ in range(n)])

    assert bool(_summary(listings).iloc[0]["low_sample"]) is expected_low


def test_the_threshold_is_a_parameter_and_the_median_is_still_given():
    listings = _listings(*[_at(3000.0) for _ in range(5)])

    assert bool(_summary(listings, min_sample=5).iloc[0]["low_sample"]) is False
    low = _summary(listings, min_sample=6).iloc[0]
    assert bool(low["low_sample"]) is True
    assert low["median_price_per_m2"] == 3000.0


def test_a_group_where_no_listing_has_a_surface_has_no_figures():
    listings = _listings({"surface_m2": None}, {"surface_m2": None})

    row = _summary(listings).iloc[0]

    assert (row["live_count"], row["with_surface_count"]) == (2, 0)
    assert row["median_price_per_m2"] is None
    assert row["q25_price_per_m2"] is None
    assert row["q75_price_per_m2"] is None
    assert bool(row["low_sample"]) is True


def test_one_row_per_commune_and_segment_and_unmatched_rows_are_left_out():
    listings = _listings(
        {},
        {},
        {"property_type": "Maison"},
        {"property_type": "Villa"},
        {"location": "Le Revest-les-Eaux (83)", "property_type": "Maison"},
        {"property_type": "Immeuble"},  # no segment
        {"location": "Nice (06)"},  # no commune
    )

    table = _summary(listings)

    assert list(zip(table["commune_code"], table["segment"], table["live_count"], strict=True)) == [
        (LE_REVEST, "house", 1),
        (TOULON, "apartment", 2),
        (TOULON, "house", 2),
    ]


def test_price_drops_count_listings_and_events_and_ignore_increases():
    listings = _listings(_at(2000.0), _at(2000.0), _at(2000.0), _at(2000.0))
    events = _events(
        ("L1", -5000),  # two drops on the same listing
        ("L1", -3000),
        ("L2", 2000),  # an increase does not count
        ("L3", -1000),
    )

    row = _summary(listings, events).iloc[0]

    assert row["listings_with_price_drop"] == 2  # L1 and L3
    assert row["price_drop_events"] == 3


def test_price_drops_of_another_group_or_an_unknown_listing_are_not_counted():
    listings = _listings(_at(2000.0), {"property_type": "Maison"})
    events = _events(("L2", -1000), ("GONE", -2000))  # L2 is a house; GONE is not a live listing

    table = _summary(listings, events)
    apartment = table.loc[table["segment"] == "apartment"].iloc[0]
    house = table.loc[table["segment"] == "house"].iloc[0]

    assert (apartment["listings_with_price_drop"], apartment["price_drop_events"]) == (0, 0)
    assert (house["listings_with_price_drop"], house["price_drop_events"]) == (1, 1)


def test_nothing_to_summarize_gives_an_empty_table_with_the_columns():
    table = _summary(_listings({"location": "Nice (06)"}))

    assert table.empty
    assert list(table.columns) == SUMMARY_COLUMNS


# --- listing_stats: real query behaviour, against in-memory SQLite ---

NOW = datetime(2026, 11, 22, 12, 0, 0)
EARLIER = datetime(2026, 11, 20, 9, 0, 0)


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


def _store(engine: Engine, *rows: dict) -> None:
    """Insert listings straight into the table; each row spells out only what it needs."""
    table = Table("listings", MetaData(), autoload_with=engine)
    defaults = {
        "property_type": "Appartement",
        "location": "Toulon (83)",
        "surface_m2": 50.0,
        "price": 100000,
        "amenities": [],
        "first_seen_at": EARLIER,
        "last_seen_at": NOW,
        "delisted_at": None,
    }
    records = [
        {"id": f"L{i}", "url": f"https://x/L{i}", **defaults, **row}
        for i, row in enumerate(rows, start=1)
    ]
    with engine.begin() as connection:
        connection.execute(table.insert(), records)


def _store_drop(engine: Engine, listing_id: str, old_price: int, new_price: int) -> None:
    table = Table("price_change_events", MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(
            table.insert(),
            {
                "listing_id": listing_id,
                "old_price": old_price,
                "new_price": new_price,
                "delta_eur": new_price - old_price,
                "delta_pct": (new_price - old_price) / old_price * 100,
            },
        )


def test_listing_stats_leaves_out_listings_marked_gone_and_accounts_for_every_live_one(
    sqlite_engine: Engine,
):
    _store(
        sqlite_engine,
        {"price": 100000},  # L1 apartment, Toulon
        {"price": 150000},  # L2 apartment, Toulon
        {"surface_m2": None},  # L3 apartment, Toulon, no surface
        {"delisted_at": EARLIER},  # L4 marked gone
        {"property_type": "Maison", "location": "Le Revest-les-Eaux (83)"},  # L5
        {"property_type": "Villa"},  # L6 counts as a house in Toulon
        {"property_type": "Immeuble"},  # L7 unmatched type
        {"location": "Marseille (13)"},  # L8 unmatched commune
    )

    stats = listing_stats(sqlite_engine)

    assert stats.live_total == 7
    assert stats.unmatched == 2
    table = stats.by_segment
    assert list(zip(table["commune_code"], table["segment"], table["live_count"], strict=True)) == [
        (LE_REVEST, "house", 1),
        (TOULON, "apartment", 3),
        (TOULON, "house", 1),
    ]
    apartments = table.loc[table["segment"] == "apartment"].iloc[0]
    assert apartments["with_surface_count"] == 2
    assert apartments["median_price_per_m2"] == 2500.0  # 2,000 and 3,000 per m2


def test_as_of_is_the_latest_last_seen_among_the_live_listings(sqlite_engine: Engine):
    _store(
        sqlite_engine,
        {"last_seen_at": EARLIER},
        {"last_seen_at": NOW},
        {"last_seen_at": datetime(2026, 12, 25), "delisted_at": EARLIER},  # gone: ignored
    )

    stats = listing_stats(sqlite_engine)

    assert stats.as_of == pd.Timestamp(NOW, tz="UTC")


def test_price_drops_come_from_the_events_of_live_listings_only(sqlite_engine: Engine):
    _store(sqlite_engine, {}, {}, {"delisted_at": EARLIER})
    _store_drop(sqlite_engine, "L1", 100000, 95000)
    _store_drop(sqlite_engine, "L1", 95000, 90000)
    _store_drop(sqlite_engine, "L2", 100000, 105000)  # an increase
    _store_drop(sqlite_engine, "L3", 100000, 80000)  # a listing that is gone

    row = listing_stats(sqlite_engine).by_segment.iloc[0]

    assert row["listings_with_price_drop"] == 1
    assert row["price_drop_events"] == 2


def test_min_sample_is_passed_through(sqlite_engine: Engine):
    _store(sqlite_engine, {}, {})

    default = listing_stats(sqlite_engine).by_segment.iloc[0]
    custom = listing_stats(sqlite_engine, min_sample=2).by_segment.iloc[0]

    assert (bool(default["low_sample"]), bool(custom["low_sample"])) == (True, False)


def test_an_empty_database_gives_an_empty_table_and_zeros(sqlite_engine: Engine):
    stats = listing_stats(sqlite_engine)

    assert stats.by_segment.empty
    assert list(stats.by_segment.columns) == SUMMARY_COLUMNS
    assert (stats.live_total, stats.unmatched) == (0, 0)
    assert stats.as_of is None
