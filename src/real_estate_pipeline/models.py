"""
Data models for the real estate pipeline.

Defines the structured representation of a single property listing,
as parsed from a ParuVendu.fr search result card.
"""

from dataclasses import dataclass


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
