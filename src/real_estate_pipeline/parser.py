"""
Parser layer for the real estate pipeline.

Pure functions that convert raw HTML (from scraper.py's fetch layer)
into structured Listing objects. No network calls or I/O — these
functions only transform data that's already been fetched.
"""

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from real_estate_pipeline.models import Listing

logger = logging.getLogger(__name__)


def is_lazyload_bloc(card: Tag) -> bool:
    """
    Check whether a blocAnnonce card is a JS-populated recommendation
    widget rather than a real listing.

    These cards carry an extra "lazyload_bloc" class and arrive empty
    in the raw server HTML (no price, no real data) — see 2.1.2.4
    investigation notes. They must be filtered out before any parsing
    is attempted, or they'll produce crashes or garbage records.
    """
    card_classes = card.get("class", [])
    return "lazyload_bloc" in card_classes


def extract_id(card: Tag) -> str | None:
    """
    Extract the listing's unique ID from the card's data-id attribute.

    Returns None if the attribute is missing, which signals to the
    caller (parse_listing_card) that this card can't be used.
    """
    listing_id = card.get("data-id")
    return listing_id if listing_id else None


def extract_url(card: Tag) -> str | None:
    """
    Extract the full listing URL from the <a> tag inside the card's <h3>.

    The href is relative, so it's joined with ParuVendu's base URL to
    produce a full, usable link. Returns None if the <h3><a> structure
    isn't found.
    """
    heading = card.find("h3")
    if heading is None:
        return None

    link = heading.find("a")
    if link is None or not link.get("href"):
        return None

    return urljoin("https://www.paruvendu.fr", link["href"])


def extract_type_rooms_surface(card: Tag) -> tuple[str | None, int | None, float | None]:
    """
    Extract property type, room count, and surface area.

    Primary source: the title attribute on the <h3><a> tag, which
    already contains clean text like "Maison - 4 piece(s) - 92 m2".
    This avoids parsing the multi-node <span> (surface has a <sup>2</sup>
    for the m2 unit).

    Returns (property_type, rooms, surface_m2), with individual fields
    as None if that piece couldn't be parsed out.
    """
    heading = card.find("h3")
    link = heading.find("a") if heading else None
    title_text = link.get("title") if link else None

    if not title_text:
        # TODO: fall back to parsing the <span> with type/surface/location
        # text if we ever encounter a card where `title` is missing.
        # Not yet needed - title has held on every real card seen so far.
        return None, None, None

    return _parse_title_text(title_text)


def _parse_title_text(title_text: str) -> tuple[str | None, int | None, float | None]:
    """
    Split a title string like "Appartement - 5 piece(s) - 78 m2" into
    its three component parts.

    Parts are identified by content, not position - some property
    types (e.g. "Immeuble") only have a type + surface, with no room
    count, so a fixed-position split ("part 2 is always rooms") would
    misread the surface value as a room count.
    """
    parts = [p.strip() for p in re.split(r"\s*-\s*", title_text)]

    property_type = parts[0] if parts and parts[0] else None

    rooms = None
    surface_m2 = None

    for part in parts[1:]:
        part_lower = part.lower()

        if "pièce" in part_lower or "piece" in part_lower:
            digits = "".join(c for c in part if c in "0123456789")
            if digits:
                rooms = int(digits)
        elif "m²" in part or re.search(r"\bm\s*2\b", part_lower):
            digits = "".join(c for c in part if c in "0123456789.")
            if digits:
                surface_m2 = float(digits)

    return property_type, rooms, surface_m2


def extract_price(card: Tag) -> tuple[int | None, bool]:
    """
    Extract the listing price and whether it carries the apartment-only
    asterisk marker.

    Price sits in a class-less <div> inside div.encoded-lnk, so a
    structural selector is used rather than a class-based one. The
    raw text (e.g. "629 160 €") is cleaned down to digits only.

    Returns (price, has_asterisk). price is None if it couldn't be
    parsed - a listing without a price is unusable and will cause
    parse_listing_card to reject the whole card.
    """
    price_container = card.select_one("div.encoded-lnk div")
    if price_container is None:
        return None, False

    price_text = price_container.get_text()
    has_asterisk = price_container.find("span", class_="f10") is not None

    digits = "".join(c for c in price_text if c.isdigit())
    price = int(digits) if digits else None

    return price, has_asterisk


def extract_amenities(card: Tag) -> list[str]:
    """
    Extract room count, amenity, and feature tags (e.g. "5 pieces",
    "4 chambres", "Garage", "Balcon").

    These are a mix of <li> and <span> elements sharing one identical
    class string, but that class is also reused elsewhere on the card
    (badges, buttons, and the hidden DPE energy-rating tooltip) - so
    the search is scoped to the specific container div holding just
    these tags, and anything nested inside the DPE tooltip is skipped,
    since it's hidden breakdown data, not a real amenity tag.
    """
    container = card.find(
        "div", class_="flex flex-wrap gap-x-3 gap-y-2 my-1 items-center font-medium"
    )
    if container is None:
        return []

    tag_class = (
        "text-xs text-grey-600 py-1 px-2 border-1 border-grey-50 rounded-xl bg-grey-50 font-normal"
    )
    tags = container.find_all(class_=tag_class.split())

    results = []
    for tag in tags:
        if tag.find_parent(class_="tooltip_dpe") is not None:
            continue

        text = tag.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            results.append(text)

    return results


def extract_amenity_details(
    amenities: list[str],
) -> tuple[int | None, str | None, float | None, bool, bool, bool]:
    """
    Parse the raw amenity tag list into structured fields: bedroom
    count, DPE energy rating, terrain surface, and boolean flags for
    Garage/Ascenseur/Balcon.

    The raw amenities list (from extract_amenities) is kept as-is on
    the Listing as a catch-all, but these specific values are common
    enough and useful enough on their own to deserve dedicated fields
    rather than requiring downstream code to re-parse strings.

    Returns (chambres, dpe, terrain_m2, has_garage, has_ascenseur, has_balcon).
    chambres, dpe, and terrain_m2 are None if no matching tag is found -
    "not stated" is treated as unknown, not as zero.
    """
    chambres = None
    dpe = None
    terrain_m2 = None
    has_garage = False
    has_ascenseur = False
    has_balcon = False

    for tag in amenities:
        chambres_match = re.match(r"^(\d+)\s*chambres?$", tag)
        if chambres_match:
            chambres = int(chambres_match.group(1))
            continue

        dpe_match = re.match(r"^DPE\s*:\s*(\w+)$", tag)
        if dpe_match:
            dpe = dpe_match.group(1)
            continue

        terrain_match = re.match(r"^Terrain\s+([\d\s]+)\s*m\s*2$", tag)
        if terrain_match:
            digits = terrain_match.group(1).replace(" ", "")
            terrain_m2 = float(digits) if digits else None
            continue

        if tag == "Garage":
            has_garage = True
        elif tag == "Ascenseur":
            has_ascenseur = True
        elif tag == "Balcon":
            has_balcon = True

    return chambres, dpe, terrain_m2, has_garage, has_ascenseur, has_balcon


def extract_location(card: Tag, property_type: str | None, surface_m2: float | None) -> str | None:
    """
    Extract the location text (e.g. "Toulon (83)") from the card.

    The source <span> contains type, surface, and location concatenated
    together (e.g. "Appartement 78 m2 Toulon (83)" or, for multi-word
    types, "Remise / Grange 172 m2 Toulon (83)"). Rather than guessing
    a "<type> <digits> m2" pattern with a regex, this strips the exact
    property_type and surface_m2 values already extracted from the
    title attribute (extract_type_rooms_surface) - avoiding the
    assumption that type is a single word or that surface is always
    present.
    """
    span = card.find("h3").find("span") if card.find("h3") else None
    if span is None:
        return None

    full_text = span.get_text(separator=" ", strip=True)
    full_text = re.sub(r"\s+", " ", full_text).strip()

    remaining = full_text

    if property_type and remaining.startswith(property_type):
        remaining = remaining[len(property_type) :].strip()

    if surface_m2 is not None:
        match = re.match(r"^\d[\d.,]*\s*m\s*2\s*", remaining)
        if match:
            remaining = remaining[match.end() :]

    return remaining.strip() or None


def extract_description(card: Tag) -> str | None:
    """
    Extract the listing's description text, if present.

    This is a bonus field found during HTML inspection (2.1.2.3) - not
    every listing necessarily has one, so None is a valid, expected
    result, not an error.
    """
    paragraph = card.select_one("p.text-sm.text-justify.line-clamp-5.min-h-19")
    if paragraph is None:
        return None

    text = paragraph.get_text(strip=True)
    return text or None


def parse_listing_card(card: Tag) -> Listing | None:
    """
    Parse a single blocAnnonce card into a Listing.

    Returns None if any required field (id, url, price) is missing -
    a listing without these isn't usable data. A warning is logged
    when this happens, since it's unexpected and worth investigating.

    Note: an earlier version also filtered out cards with a
    "lazyload_bloc" CSS class, based on a 2.1.2.4 finding that
    overlapping duplicate cards between pages carried that class with
    no price. Real testing against a full page (30 cards) showed
    lazyload_bloc is present on the majority of genuine, fully-priced
    listings too - it's unrelated to data validity, likely a generic
    image-lazy-loading class. The actual signal for "unusable card"
    is a missing price, already handled by the check below.
    """
    listing_id = extract_id(card)
    url = extract_url(card)
    property_type, rooms, surface_m2 = extract_type_rooms_surface(card)
    location = extract_location(card, property_type, surface_m2)
    price, has_asterisk = extract_price(card)
    amenities = extract_amenities(card)
    chambres, dpe, terrain_m2, has_garage, has_ascenseur, has_balcon = extract_amenity_details(
        amenities
    )
    description = extract_description(card)

    if listing_id is None or url is None or price is None:
        logger.warning(
            "Skipping card - missing required field(s): id=%s, url=%s, price=%s",
            listing_id,
            url,
            price,
        )
        return None

    return Listing(
        id=listing_id,
        url=url,
        property_type=property_type,
        rooms=rooms,
        surface_m2=surface_m2,
        chambres=chambres,
        dpe=dpe,
        terrain_m2=terrain_m2,
        has_garage=has_garage,
        has_ascenseur=has_ascenseur,
        has_balcon=has_balcon,
        price=price,
        has_asterisk=has_asterisk,
        location=location,
        amenities=amenities,
        description=description,
    )


def parse_page(html: str) -> list[Listing]:
    """
    Parse a full ParuVendu.fr search results page into a list of Listings.

    Finds every blocAnnonce card in the page, parses each one via
    parse_listing_card, and filters out any that come back as None
    (lazyload_bloc widgets, or cards missing a required field).
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("div", class_="blocAnnonce")

    listings = []
    for card in cards:
        listing = parse_listing_card(card)
        if listing is not None:
            listings.append(listing)

    return listings
