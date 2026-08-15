"""
Cleaning/normalization layer for ParuVendu.fr listings.

Mirrors dvf/cleaning.py's composable, additive pattern: each function
takes and returns a DataFrame, adding derived columns without dropping
rows. Simpler than the DVF module by design — Listings don't carry
DVF's multi-unit-mutation or nature-of-sale ambiguity, so there's less
to clean.
"""

import pandas as pd

from real_estate_pipeline.paruvendu.models import Listing


def to_dataframe(listings: list[Listing]) -> pd.DataFrame:
    """Convert a list of Listing objects into a raw DataFrame, one row per listing.

    No derived columns are added here — see add_price_per_m2 for that.
    """
    return pd.DataFrame([vars(listing) for listing in listings])


def add_price_per_m2(df: pd.DataFrame) -> pd.DataFrame:
    """Add a price_per_m2 column: price / surface_m2.

    NaN where surface_m2 is missing (e.g. land-only listings, which
    report terrain_m2 instead — price_per_m2 doesn't apply to them,
    same reasoning as DVF's bare-land handling in dvf/cleaning.py).
    """
    df = df.copy()
    df["price_per_m2"] = df["price"] / df["surface_m2"].replace(0, pd.NA)
    return df


def clean_listings(listings: list[Listing]) -> pd.DataFrame:
    """Build the cleaned ParuVendu DataFrame: convert listings and add
    derived columns (currently just price_per_m2).

    No rows are dropped.
    """
    df = to_dataframe(listings)
    df = add_price_per_m2(df)
    return df
