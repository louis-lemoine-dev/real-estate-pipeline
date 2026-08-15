"""
Tests for paruvendu/cleaning.py, using the same real HTML fixtures and
parsing pattern as test_parser.py (tests/fixtures/), parsed into Listing
objects via parser.py — same real-data-over-synthetic philosophy, with
one deliberate exception: none of the saved fixtures have surface_m2
missing, so that case uses a synthetic Listing (see
test_add_price_per_m2_missing_surface_is_na below).
"""

from pathlib import Path

import pandas as pd
import pytest
from bs4 import BeautifulSoup, Tag

from real_estate_pipeline.paruvendu.cleaning import (
    add_price_per_m2,
    clean_listings,
    to_dataframe,
)
from real_estate_pipeline.paruvendu.models import Listing
from real_estate_pipeline.paruvendu.parser import parse_listing_card

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_card(filename: str) -> Tag:
    """Load a fixture HTML file and return its blocAnnonce Tag."""
    html = (FIXTURES_DIR / filename).read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    card = soup.find("div", class_="blocAnnonce")
    assert card is not None, f"Could not find blocAnnonce div in {filename}"
    return card


@pytest.fixture(scope="module")
def listings() -> list[Listing]:
    apartment = parse_listing_card(load_card("card_apartment.html"))
    maison = parse_listing_card(load_card("card_maison.html"))
    assert apartment is not None
    assert maison is not None
    return [apartment, maison]


@pytest.fixture(scope="module")
def raw_df(listings) -> pd.DataFrame:
    return to_dataframe(listings)


def test_to_dataframe_has_one_row_per_listing(listings, raw_df):
    assert len(raw_df) == len(listings)
    assert set(raw_df["id"]) == {listing.id for listing in listings}


def test_add_price_per_m2_apartment(raw_df):
    df = add_price_per_m2(raw_df)
    row = df[df["id"] == "1294127896"].iloc[0]

    # card_apartment.html: price=185000, surface_m2=78.0
    assert row["price_per_m2"] == pytest.approx(185000 / 78.0)


def test_add_price_per_m2_missing_surface_is_na():
    """Synthetic case: none of the saved fixtures have surface_m2=None
    (land-only listings would, but none are saved yet). Swap in a real
    fixture here if one turns up later."""
    land_listing = Listing(
        id="999999",
        url="https://www.paruvendu.fr/immobilier/vente/terrain/999999",
        property_type="Terrain",
        rooms=None,
        surface_m2=None,
        chambres=None,
        dpe=None,
        terrain_m2=500.0,
        has_garage=False,
        has_ascenseur=False,
        has_balcon=False,
        price=120000,
        has_asterisk=False,
        location="Toulon (83)",
        amenities=[],
        description=None,
    )
    df = to_dataframe([land_listing])
    df = add_price_per_m2(df)

    assert pd.isna(df["price_per_m2"].iloc[0])


def test_clean_listings_adds_price_per_m2(listings):
    df = clean_listings(listings)

    assert "price_per_m2" in df.columns
    assert len(df) == len(listings)
