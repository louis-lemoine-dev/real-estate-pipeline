"""
Tests for parser.py's HTML extraction and parsing functions.

Uses real, saved HTML fixtures (tests/fixtures/) captured from live
ParuVendu.fr listing cards, rather than hand-written synthetic HTML -
several bugs this task caught (multi-word property types, 2-part
titles with no room count) only surfaced against real markup, so
fixtures are real snippets, not idealized fakes.
"""

from pathlib import Path

from bs4 import BeautifulSoup, Tag

from real_estate_pipeline.paruvendu.parser import (
    extract_amenities,
    extract_amenity_details,
    extract_description,
    extract_id,
    extract_location,
    extract_price,
    extract_type_rooms_surface,
    extract_url,
    parse_listing_card,
    parse_page,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_card(filename: str) -> Tag:
    """Load a fixture HTML file and return its blocAnnonce Tag."""
    html = (FIXTURES_DIR / filename).read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    card = soup.find("div", class_="blocAnnonce")
    assert card is not None, f"Could not find blocAnnonce div in {filename}"
    return card


# --- extract_id / extract_url ---


def test_extract_id_returns_real_id():
    card = load_card("card_apartment.html")

    result = extract_id(card)

    assert result == "1294127896"


def test_extract_url_returns_absolute_url():
    card = load_card("card_apartment.html")

    result = extract_url(card)

    assert result == "https://www.paruvendu.fr/immobilier/vente/appartement/1294127896A1KIVHAP000"


# --- extract_type_rooms_surface ---


def test_extract_type_rooms_surface_apartment():
    card = load_card("card_apartment.html")

    property_type, rooms, surface_m2 = extract_type_rooms_surface(card)

    assert property_type == "Appartement"
    assert rooms == 5
    assert surface_m2 == 78.0


def test_extract_type_rooms_surface_immeuble_has_no_rooms():
    """
    Regression test: 'Immeuble' titles only have type + surface (no
    room count), a 2-part title instead of the usual 3. A fixed-position
    split previously misread the surface value as the room count.
    """
    card = load_card("card_immeuble.html")

    property_type, rooms, surface_m2 = extract_type_rooms_surface(card)

    assert property_type == "Immeuble"
    assert rooms is None
    assert surface_m2 == 500.0


def test_extract_type_rooms_surface_multiword_type():
    """
    Regression test: multi-word property types (e.g. "Remise / Grange")
    were previously truncated to just the first word by a regex that
    assumed a single-token type.
    """
    card = load_card("card_remise_grange.html")

    property_type, rooms, surface_m2 = extract_type_rooms_surface(card)

    assert property_type == "Remise / Grange"
    assert surface_m2 == 172.0


# --- extract_price ---


def test_extract_price_apartment_has_asterisk():
    card = load_card("card_apartment.html")

    price, has_asterisk = extract_price(card)

    assert price == 185000
    assert has_asterisk is True


def test_extract_price_maison_no_asterisk():
    card = load_card("card_maison.html")

    price, has_asterisk = extract_price(card)

    assert price == 629160
    assert has_asterisk is False


def test_extract_price_missing_returns_none():
    card = load_card("card_missing_price.html")

    price, has_asterisk = extract_price(card)

    assert price is None
    assert has_asterisk is False


# --- extract_amenities / extract_amenity_details ---


def test_extract_amenities_apartment():
    card = load_card("card_apartment.html")

    result = extract_amenities(card)

    assert result == ["5 pièces", "4 chambres", "Garage", "Balcon", "DPE : D"]


def test_extract_amenity_details_apartment():
    card = load_card("card_apartment.html")
    amenities = extract_amenities(card)

    chambres, dpe, terrain_m2, has_garage, has_ascenseur, has_balcon = extract_amenity_details(
        amenities
    )

    assert chambres == 4
    assert dpe == "D"
    assert terrain_m2 is None
    assert has_garage is True
    assert has_ascenseur is False
    assert has_balcon is True


def test_extract_amenity_details_maison_has_terrain():
    card = load_card("card_maison.html")
    amenities = extract_amenities(card)

    chambres, dpe, terrain_m2, has_garage, has_ascenseur, has_balcon = extract_amenity_details(
        amenities
    )

    assert chambres == 3
    assert dpe == "A"
    assert terrain_m2 == 105.0
    assert has_garage is False
    assert has_ascenseur is False
    assert has_balcon is False


# --- extract_location ---


def test_extract_location_immeuble_no_leftover_surface():
    """
    Regression test: when surface_m2 was None (as it incorrectly was,
    pre-fix, for Immeuble cards), the location-stripping step was
    skipped entirely, leaving "500 m 2 Toulon (83)" instead of
    "Toulon (83)".
    """
    card = load_card("card_immeuble.html")
    property_type, _, surface_m2 = extract_type_rooms_surface(card)

    result = extract_location(card, property_type, surface_m2)

    assert result == "Toulon (83)"


def test_extract_location_remise_grange_no_leftover_type():
    """
    Regression test: a multi-word property type previously left part
    of the type string stuck to the front of the location.
    """
    card = load_card("card_remise_grange.html")
    property_type, _, surface_m2 = extract_type_rooms_surface(card)

    result = extract_location(card, property_type, surface_m2)

    assert result == "Toulon (83)"


# --- extract_description ---


def test_extract_description_apartment_present():
    card = load_card("card_apartment.html")

    result = extract_description(card)

    assert result is not None
    assert "siblas" in result.lower()


# --- parse_listing_card ---


def test_parse_listing_card_apartment_returns_complete_listing():
    card = load_card("card_apartment.html")

    listing = parse_listing_card(card)

    assert listing is not None
    assert listing.id == "1294127896"
    assert listing.property_type == "Appartement"
    assert listing.rooms == 5
    assert listing.surface_m2 == 78.0
    assert listing.price == 185000
    assert listing.has_asterisk is True
    assert listing.location == "Toulon (83)"
    assert listing.chambres == 4
    assert listing.dpe == "D"
    assert listing.has_garage is True
    assert listing.has_balcon is True
    assert listing.has_ascenseur is False


def test_parse_listing_card_missing_price_returns_none():
    card = load_card("card_missing_price.html")

    result = parse_listing_card(card)

    assert result is None


# --- parse_page ---


def test_parse_page_returns_multiple_listings():
    apartment_html = (FIXTURES_DIR / "card_apartment.html").read_text(encoding="utf-8")
    maison_html = (FIXTURES_DIR / "card_maison.html").read_text(encoding="utf-8")

    combined_html = f"<html><body>{apartment_html}{maison_html}</body></html>"

    listings = parse_page(combined_html)

    assert len(listings) == 2
    ids = {listing.id for listing in listings}
    assert ids == {"1294127896", "1294230780"}
