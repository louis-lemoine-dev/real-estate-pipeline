"""Statistics on the stored ParuVendu listings (roadmap task 4.0.2).

The asking-price side of the dashboard: for each commune and property type, how
many listings are live, what is the median asking price per m2 (with the
spread), and how many listings have dropped their price. Like the sold-price
side (market.py), the median is never hidden because of a small sample: a
`low_sample` flag says so and the caller decides how to show it.

Listings carry free text, DVF carries codes, so two small tables turn one into
the other: the commune from the location text ("Toulon (83)") and the property
type from its label ("Appartement"). They are the same communes and the same
two segments as the sold-price statistics, so a row of this module can be put
next to `comparable_stats` for the same commune and segment.

"Live" means "not marked gone". That is not the same as "still for sale": only
about 140 results of a search are reachable and the list shifts between runs, so
a listing can leave the visible window without being sold (see docs/decisions.md).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import pandas as pd
from sqlalchemy import MetaData, Table, select
from sqlalchemy.engine import Connection, Engine

from real_estate_pipeline.analysis.market import DEFAULT_MIN_SAMPLE
from real_estate_pipeline.common.db import run_in_transaction
from real_estate_pipeline.paruvendu.runner import load_stored_listings

# The twelve communes of dvf/orchestrator.py's DEFAULT_COMMUNE_CODES, by name.
# A test checks that the two lists cover exactly the same codes.
COMMUNE_NAMES: dict[str, str] = {
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

# ParuVendu's label -> the sold-price segment (market.SEGMENTS). A villa is a
# house for DVF's "single house" type. Any other label (building, parking, land,
# barn...) has no segment: it is counted but gets no price statistics.
PROPERTY_TYPE_SEGMENTS: dict[str, str] = {
    "Appartement": "apartment",
    "Maison": "house",
    "Villa": "house",
}

SUMMARY_COLUMNS = [
    "commune_code",
    "segment",
    "live_count",
    "with_surface_count",
    "median_price_per_m2",
    "q25_price_per_m2",
    "q75_price_per_m2",
    "low_sample",
    "listings_with_price_drop",
    "price_drop_events",
]

# The department of the twelve communes. Location text always ends in "(83)".
_DEPARTMENT = "83"
_LOCATION_PATTERN = re.compile(r"(?P<name>.+?)\s*\((?P<department>\d{2,3})\)")


def _normalize(text: str) -> str:
    """Lower case, no accents, hyphens and runs of spaces become one space."""
    without_accents = "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    )
    return re.sub(r"[\s\-]+", " ", without_accents.lower()).strip()


_CODES_BY_NAME = {_normalize(name): code for code, name in COMMUNE_NAMES.items()}


def commune_code_from_location(location: str | None) -> str | None:
    """The commune code of a listing's location text, or None.

    The text must look like "Toulon (83)": a commune name, then the department.
    Accents, capitals and hyphens do not matter. A department other than 83, a
    commune outside the twelve, a missing text or any other shape gives None.
    """
    if not location:
        return None
    match = _LOCATION_PATTERN.fullmatch(location.strip())
    if match is None or match["department"] != _DEPARTMENT:
        return None
    return _CODES_BY_NAME.get(_normalize(match["name"]))


def match_listings(listings: pd.DataFrame) -> pd.DataFrame:
    """Add commune_code, segment and price_per_m2 to a frame of stored listings.

    commune_code and segment are missing (NaN) where the listing cannot be matched. The
    surface comes out of Postgres as a Decimal: it is converted to a plain
    float. price_per_m2 is price divided by surface, and missing (NaN) where
    there is no surface or it is zero.
    """
    matched = listings.copy()
    matched["commune_code"] = matched["location"].map(commune_code_from_location)
    matched["segment"] = matched["property_type"].map(PROPERTY_TYPE_SEGMENTS.get)
    surface = matched["surface_m2"].astype("float64")
    matched["price_per_m2"] = matched["price"].astype("float64") / surface.where(surface > 0)
    return matched


def summarize_listings(
    matched: pd.DataFrame,
    events: pd.DataFrame,
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> pd.DataFrame:
    """One row per commune and segment, from LIVE listings (pure pandas).

    `matched` is what `match_listings` returns for the live listings; rows with
    no commune or no segment form no group and are left out of the table. `events` holds the
    logged price changes (columns listing_id and delta_eur); a price drop is an
    event with a negative amount. Several drops on one listing count as one
    listing with several events, and increases are not counted.

    live_count counts every listing; the price figures use only those with a
    surface (with_surface_count). low_sample is True when with_surface_count is
    below `min_sample`, and the figures are still given.
    """
    drops_per_listing = events.loc[events["delta_eur"] < 0].groupby("listing_id").size()

    rows: list[dict[str, Any]] = []
    groups = matched.groupby(["commune_code", "segment"], sort=True, dropna=True)
    for key, group in groups:
        commune_code, segment = cast(tuple[str, str], key)
        prices = group["price_per_m2"].dropna().to_numpy(dtype="float64")
        count = len(prices)
        events_per_listing = drops_per_listing.reindex(group["id"]).fillna(0)
        rows.append(
            {
                "commune_code": commune_code,
                "segment": segment,
                "live_count": len(group),
                "with_surface_count": count,
                "median_price_per_m2": float(np.median(prices)) if count else None,
                "q25_price_per_m2": float(np.quantile(prices, 0.25)) if count else None,
                "q75_price_per_m2": float(np.quantile(prices, 0.75)) if count else None,
                "low_sample": count < min_sample,
                "listings_with_price_drop": int((events_per_listing > 0).sum()),
                "price_drop_events": int(events_per_listing.sum()),
            }
        )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def load_price_change_events(engine: Engine) -> pd.DataFrame:
    """Every logged price change. An empty table gives an empty frame with the columns."""
    table = Table("price_change_events", MetaData(), autoload_with=engine)

    def _read(connection: Connection) -> list[dict]:
        return [dict(row) for row in connection.execute(select(table)).mappings()]

    rows = run_in_transaction(engine, _read)
    return pd.DataFrame(rows, columns=[column.name for column in table.columns])


@dataclass(frozen=True)
class ListingStats:
    """The asking-price statistics of the live listings.

    by_segment is the table of summarize_listings. live_total counts every live
    listing and unmatched those left out of the table (another type than
    apartment or house, or a commune outside the twelve), so nothing disappears
    silently. as_of is the latest "last seen" time among the live listings, for a
    "data from ..." label (None when there are none).
    """

    by_segment: pd.DataFrame
    live_total: int
    unmatched: int
    as_of: pd.Timestamp | None


def listing_stats(engine: Engine, min_sample: int = DEFAULT_MIN_SAMPLE) -> ListingStats:
    """The asking-price statistics of the listings stored in the database.

    Reads the stored listings and the logged price changes (nothing is written).
    Listings marked gone are left out. See the module docstring for what
    "live" does and does not mean.
    """
    stored = load_stored_listings(engine)
    live = stored.loc[stored["delisted_at"].isna()]
    table = summarize_listings(match_listings(live), load_price_change_events(engine), min_sample)

    live_total = len(live)
    matched_count = sum(int(count) for count in table["live_count"])
    last_seen = pd.to_datetime(live["last_seen_at"], utc=True).max() if live_total else None
    return ListingStats(
        by_segment=table,
        live_total=live_total,
        unmatched=live_total - matched_count,
        as_of=last_seen,
    )
