"""
Tests for DVF+ mutations that come back with no price (valeurfonc null).

About 0.1% of real mutations have this: the first full-scale fetch found 8 among ~7,000
rows (Carqueiranne and La Crau). They are ordinary sales, here a house in La Crau, so they
are kept with price = None instead of crashing the parser or being dropped. The record in
tests/fixtures/dvf_null_price_house.json is a real one, fetched through the single-mutation
endpoint, in line with the other DVF fixtures.
"""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from real_estate_pipeline.dvf.cleaning import clean_transactions
from real_estate_pipeline.dvf.models import Transaction
from real_estate_pipeline.dvf.parser import parse_transaction

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "dvf_null_price_house.json"


@pytest.fixture(scope="module")
def raw_mutation() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def null_price_transaction(raw_mutation) -> Transaction:
    return parse_transaction(raw_mutation)


def test_fixture_really_has_no_price(raw_mutation):
    """Guards the fixture itself: swapping in a priced record would silently void these tests."""
    assert "valeurfonc" in raw_mutation
    assert raw_mutation["valeurfonc"] is None


def test_null_price_parses_to_none(null_price_transaction):
    transaction = null_price_transaction

    assert transaction.price is None
    assert transaction.id_mutation == 3629889
    assert transaction.date == date(2017, 6, 22)
    assert transaction.is_vefa is False
    assert transaction.nature_mutation_label == "Vente"
    assert transaction.property_type_label == "UNE MAISON"
    assert transaction.surface_bati == 120.0
    assert transaction.surface_terrain == 1888.0
    assert transaction.commune_code == "83047"


def test_null_price_does_not_change_any_other_field(raw_mutation, null_price_transaction):
    """Same record with a price filled in must parse to the identical Transaction apart
    from price, so no other field (rooms included) depends on the price being present."""
    priced = parse_transaction({**raw_mutation, "valeurfonc": "250000.00"})

    assert priced == replace(null_price_transaction, price=250000.0)


def test_missing_price_key_still_raises(raw_mutation):
    """A null value is a gap in the data; an absent key would mean the API changed shape."""
    without_key = {k: v for k, v in raw_mutation.items() if k != "valeurfonc"}

    with pytest.raises(KeyError, match="valeurfonc"):
        parse_transaction(without_key)


def test_cleaning_keeps_null_price_row_and_flags_it(null_price_transaction):
    df = clean_transactions([null_price_transaction])
    row = df.iloc[0]

    assert len(df) == 1  # nothing is dropped
    assert pd.isna(row["price"])
    assert pd.isna(row["price_per_m2"])
    assert not row["is_market_transaction"]
    assert bool(row["is_single_residential_unit"]) == (null_price_transaction.rooms is not None)


def test_cleaning_mixed_batch_of_priced_and_null_price(null_price_transaction):
    priced = replace(null_price_transaction, id_mutation=1, price=250000.0)

    df = clean_transactions([priced, null_price_transaction])
    priced_row = df[df["id_mutation"] == 1].iloc[0]
    null_row = df[df["id_mutation"] == 3629889].iloc[0]

    assert df["price"].dtype == "float64"
    assert priced_row["price_per_m2"] == pytest.approx(250000.0 / 120.0)
    assert priced_row["is_market_transaction"]
    assert pd.isna(null_row["price_per_m2"])
    assert not null_row["is_market_transaction"]
