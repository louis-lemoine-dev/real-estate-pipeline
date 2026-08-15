"""
Tests for dvf/cleaning.py, using the same real DVF+ mutation fixtures as
test_dvf_parser.py (tests/fixtures/dvf_mutations.json), parsed into
Transaction objects first via dvf/parser.py — same real-data-over-synthetic
philosophy, since dvf_parser.py's tests already validate the parsing step,
so this file can trust it and focus purely on cleaning.py's own logic.
"""

import json
from datetime import date as date_
from pathlib import Path

import pandas as pd
import pytest

from real_estate_pipeline.dvf.cleaning import (
    add_price_per_m2,
    add_single_unit_flag,
    clean_transactions,
    flag_market_transactions,
    to_dataframe,
)
from real_estate_pipeline.dvf.models import Transaction
from real_estate_pipeline.dvf.parser import parse_transactions

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "dvf_mutations.json"


@pytest.fixture(scope="module")
def transactions() -> list[Transaction]:
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        raw_mutations = json.load(f)
    return parse_transactions(list(raw_mutations.values()))


@pytest.fixture(scope="module")
def raw_df(transactions) -> pd.DataFrame:
    return to_dataframe(transactions)


def test_to_dataframe_has_one_row_per_transaction(transactions, raw_df):
    assert len(raw_df) == len(transactions)
    assert set(raw_df["id_mutation"]) == {t.id_mutation for t in transactions}


def test_add_price_per_m2_single_apartment(raw_df):
    df = add_price_per_m2(raw_df)
    row = df[df["id_mutation"] == 1298].iloc[0]

    # single_apartment: price=124200.00, surface_bati=68.00
    assert row["price_per_m2"] == pytest.approx(124200.0 / 68.0)


def test_add_price_per_m2_bare_land_is_na(raw_df):
    """bare_land has surface_bati=0 — price_per_m2 must be NA, not inf,
    since dividing by zero would otherwise silently poison any later
    aggregation."""
    df = add_price_per_m2(raw_df)
    row = df[df["id_mutation"] == 5546].iloc[0]

    assert pd.isna(row["price_per_m2"])


def test_add_single_unit_flag(raw_df):
    df = add_single_unit_flag(raw_df)

    single_apartment = df[df["id_mutation"] == 1298].iloc[0]
    mixed_multi_unit = df[df["id_mutation"] == 64130].iloc[0]
    multi_commune = df[df["id_mutation"] == 141626].iloc[0]
    bare_land = df[df["id_mutation"] == 5546].iloc[0]

    assert single_apartment["is_single_residential_unit"]
    # mixed_multi_unit and multi_commune both have rooms=None (see
    # test_dvf_parser.py), so the flag must be False for both
    assert not mixed_multi_unit["is_single_residential_unit"]
    assert not multi_commune["is_single_residential_unit"]
    assert not bare_land["is_single_residential_unit"]


def test_flag_market_transactions_default_includes_real_fixtures(raw_df):
    """None of the saved fixtures are low-value mutations (all real prices
    range from 62k to 1.41M), so this only confirms ordinary transactions
    pass the default floor — it does not exercise the exclusion branch.
    See test_flag_market_transactions_excludes_low_value below for that,
    using a synthetic Transaction since no real low-value fixture exists
    yet in this dataset."""
    df = flag_market_transactions(raw_df)

    assert bool(df["is_market_transaction"].all())


def test_flag_market_transactions_excludes_low_value():
    """Synthetic case: no real €0/€1 mutation exists in our saved fixtures
    yet, but Cerema's own documentation confirms these occur in DVF (see
    flag_market_transactions' docstring). Swap in a real fixture here if
    one turns up later."""
    low_value = Transaction(
        id_mutation=999999,
        date=date_(2014, 1, 1),
        year=2014,
        price=1.0,
        is_vefa=False,
        nature_mutation_code=1,
        nature_mutation_label="Vente",
        property_type_code="121",
        property_type_label="UN APPARTEMENT",
        surface_bati=50.0,
        surface_terrain=0.0,
        rooms=2,
        commune_code="83137",
    )
    df = to_dataframe([low_value])
    df = flag_market_transactions(df)

    assert not df["is_market_transaction"].iloc[0]


def test_clean_transactions_adds_all_columns(transactions):
    df = clean_transactions(transactions)

    for column in (
        "price_per_m2",
        "is_single_residential_unit",
        "is_market_transaction",
    ):
        assert column in df.columns
    assert len(df) == len(transactions)
