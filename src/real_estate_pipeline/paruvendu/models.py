"""
Data models for the real estate pipeline.

Defines the structured representation of a single property listing,
as parsed from a ParuVendu.fr search result card.
"""

from collections.abc import Iterable
from dataclasses import dataclass, fields


@dataclass
class Listing:
    """
    A single property listing scraped from ParuVendu.fr.

    Fields are populated by parser.py from the raw HTML of one
    `div.blocAnnonce` card. Optional fields may be None if the
    corresponding data wasn't present on the card (e.g. some listings
    have no description).
    """

    # --- Identity ---
    id: str  # ParuVendu's own listing ID, from data-id
    url: str  # Full absolute URL to the listing page

    # --- Property details ---
    property_type: str | None  # e.g. "Appartement", "Maison"
    rooms: int | None  # Number of rooms, if stated
    surface_m2: float | None  # Living surface in square meters
    chambres: int | None  # Number of bedrooms, if stated
    dpe: str | None  # Energy rating letter (A-G), if stated
    terrain_m2: (
        float | None
    )  # Land/plot surface in square meters, if stated (None if no terrain tag)
    has_garage: bool  # Whether a "Garage" amenity tag is present
    has_ascenseur: bool  # Whether an "Ascenseur" (elevator) amenity tag is present
    has_balcon: bool  # Whether a "Balcon" amenity tag is present

    # --- Price ---
    price: int  # Price in euros
    has_asterisk: bool  # Whether the price carries the apartment-only "*" marker (meaning TBD)

    # --- Location & extras ---
    location: str | None  # Raw location text as shown on the card
    amenities: list[str]  # Raw amenity/tag list, e.g. ["Garage", "Balcon"] - kept as a catch-all
    description: str | None  # Optional description text, if present


# Every column a scrape must provide: one per Listing field, in field order.
# The single definition of "what the scraper hands over". It is used to
# validate scraped data on the way in (diff.classify_listings) and to choose
# exactly which columns the write paths store (insert.py, update.py).
LISTING_COLUMNS: tuple[str, ...] = tuple(field.name for field in fields(Listing))


def require_listing_columns(available: Iterable[str], where: str) -> None:
    """Raise ValueError naming every Listing column missing from `available`.

    Stopping loudly is deliberate: writing a scrape that lacks a column
    would overwrite the stored value with NULL (or fail on a NOT NULL
    column), and by then the scraped and stored data have been merged, so
    "the scraper omitted it" can no longer be told apart from "the scraper
    found nothing". The check therefore has to run where the scraped data
    is still separate.
    """
    present = set(available)
    missing = [column for column in LISTING_COLUMNS if column not in present]
    if missing:
        raise ValueError(
            f"{where}: scraped data is missing Listing column(s) {missing}. "
            "Refusing to continue: writing without them would overwrite the "
            "stored values with NULL."
        )
