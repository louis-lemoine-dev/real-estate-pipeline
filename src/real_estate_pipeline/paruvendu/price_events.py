"""Price-change delta computation for ParuVendu listings.

Extends classify_listings' output with the absolute (€) and relative (%)
price delta for rows classified 'modified' — the numbers that get written
into `price_change_events` in 3.4.2.
"""

from __future__ import annotations

import pandas as pd


def compute_price_deltas(classified: pd.DataFrame) -> pd.DataFrame:
    """Add delta_eur and delta_pct columns to classify_listings' output.

    Only rows classified 'modified' get real values — new/unchanged/
    delisted rows have no old_price to diff against, so both columns are
    None for them.

    delta_eur = price - old_price (matches price_change_events.delta_eur,
        an integer column, since ParuVendu prices are always whole euros).
    delta_pct = (price - old_price) / old_price * 100 (matches
        price_change_events.delta_pct, numeric). A positive value is a
        price increase, negative is a decrease — the sign itself carries
        the meaning, no special-casing either direction.

    Guards against division by zero: if old_price is 0 (shouldn't happen
    in practice — price is NOT NULL in the schema — but not guaranteed to
    never happen), delta_pct is None rather than raising or producing inf,
    since a percentage change from zero is mathematically undefined.
    delta_eur is still computed in that case, since it doesn't involve
    division.

    No rounding applied — full precision is stored; rounding for display
    is a presentation concern, not a storage one.
    """
    result = classified.copy()
    is_modified = result["diff_status"] == "modified"

    delta_eur = pd.Series(pd.NA, index=result.index, dtype="object")
    delta_pct = pd.Series(pd.NA, index=result.index, dtype="object")

    modified_rows = result.loc[is_modified]
    delta_eur.loc[is_modified] = modified_rows["price"] - modified_rows["old_price"]

    nonzero_old_price = is_modified & (result["old_price"] != 0)
    nonzero_rows = result.loc[nonzero_old_price]
    delta_pct.loc[nonzero_old_price] = (
        (nonzero_rows["price"] - nonzero_rows["old_price"]) / nonzero_rows["old_price"] * 100
    )

    result["delta_eur"] = delta_eur
    result["delta_pct"] = delta_pct
    return result
