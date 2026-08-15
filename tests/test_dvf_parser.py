"""
Tests for dvf/parser.py, using real DVF+ mutation records saved as
fixtures (tests/fixtures/dvf_mutations.json) rather than hand-written
synthetic data — same approach as test_parser.py for ParuVendu, since
real API responses are what actually exercise edge cases (e.g. the
multi-commune fixture below only makes sense because it's a real,
messy record, not something we'd think to construct by hand).
"""

import json
from datetime import date
from pathlib import Path

import pytest

from real_estate_pipeline.dvf.parser import parse_transaction, parse_transactions

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "dvf_mutations.json"


@pytest.fixture(scope="module")
def mutations() -> dict:
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_single_apartment(mutations):
    transaction = parse_transaction(mutations["single_apartment"])

    assert transaction.id_mutation == 1298
    assert transaction.date == date(2014, 2, 25)
    assert transaction.year == 2014
    assert transaction.price == 124200.0
    assert transaction.is_vefa is False
    assert transaction.property_type_code == "121"
    assert transaction.property_type_label == "UN APPARTEMENT"
    assert transaction.surface_bati == 68.0
    assert transaction.surface_terrain == 0.0
    assert transaction.commune_code == "83137"
    assert transaction.rooms == 4
    assert transaction.nature_mutation_code == 1
    assert transaction.nature_mutation_label == "Vente"


def test_single_house(mutations):
    transaction = parse_transaction(mutations["single_house"])

    assert transaction.property_type_label == "UNE MAISON"
    assert transaction.surface_bati == 130.0
    # Bucket "5pp" means "5 rooms or more", not exactly 5
    assert transaction.rooms == 5


def test_mixed_multi_unit_has_no_room_count(mutations):
    """
    A mutation bundling multiple units (here: 2 apartments + 1 house)
    has no single sensible room count — multiple nbapt*pp/nbmai*pp
    buckets are non-zero simultaneously, so rooms must be None rather
    than guessing.
    """
    transaction = parse_transaction(mutations["mixed_multi_unit"])

    assert transaction.property_type_label == "BATI MIXTE - LOGEMENTS"
    assert transaction.surface_bati == 343.0
    assert transaction.rooms is None


def test_multi_commune_mutation(mutations):
    """
    A mutation spanning two communes (nbcomm=2). Despite having a
    single non-zero room bucket (nbapt3pp=1), rooms must still be
    None here because nblocmut=2 — the bucket pattern alone isn't
    sufficient evidence of a single residential unit.

    commune_code takes l_codinsee[0], the "primary" commune, which
    is 83126 even though this mutation was originally fetched by
    querying commune 83129 — a known, documented limitation.
    """
    transaction = parse_transaction(mutations["multi_commune"])

    assert transaction.commune_code == "83126"
    assert transaction.rooms is None


def test_bare_land_has_no_building_surface(mutations):
    transaction = parse_transaction(mutations["bare_land"])

    assert transaction.property_type_label == "TERRAIN NON BATIS INDETERMINE"
    assert transaction.surface_bati == 0.0
    assert transaction.surface_terrain == 4835.0
    assert transaction.rooms is None


def test_vefa_sale(mutations):
    transaction = parse_transaction(mutations["vefa_sale"])

    assert transaction.is_vefa is True
    assert transaction.rooms == 1
    assert transaction.nature_mutation_code == 2
    assert transaction.nature_mutation_label == "Vente en l'état futur d'achèvement"


def test_parse_transactions_returns_all_in_order(mutations):
    raw_list = list(mutations.values())
    transactions = parse_transactions(raw_list)

    assert len(transactions) == len(raw_list)
    assert [t.id_mutation for t in transactions] == [raw["idmutation"] for raw in raw_list]
