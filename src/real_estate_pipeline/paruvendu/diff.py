"""Diff logic for ParuVendu listings.

Classifies each freshly scraped listing against the current Supabase state
as new, unchanged, modified, or delisted. Pure functions only — no DB reads
or writes happen here; the caller is responsible for fetching `existing`
and persisting the result.
"""

from __future__ import annotations

import pandas as pd

# Tunable constants
DELISTING_CONFIRMATION_MISSES = 2
SCRAPE_INTERVAL = pd.Timedelta(days=1)
MIN_EXPECTED_RATIO = 0.5  # placeholder threshold for the run-level sanity gate


class ScrapeAnomalyError(Exception):
    """Raised when a scrape run looks broken rather than genuinely smaller."""


def check_run_sanity(
    scraped: pd.DataFrame,
    expected_count: int,
    min_ratio: float = MIN_EXPECTED_RATIO,
) -> None:
    """Abort-guard against classifying a broken scrape run as mass delisting.

    Call this BEFORE classify_listings. If it raises, skip classification
    entirely for this run — log the anomaly and let the next scheduled run
    try again, rather than touching last_seen_at or delisted_at.

    expected_count is caller-supplied (e.g. a rolling average of recent
    successful runs for this commune). If expected_count <= 0 (no history
    yet, e.g. very first run), the check is skipped.
    """
    if expected_count <= 0:
        return
    ratio = len(scraped) / expected_count
    if ratio < min_ratio:
        raise ScrapeAnomalyError(
            f"Scraped {len(scraped)} listings, expected ~{expected_count} "
            f"(ratio {ratio:.2f} < {min_ratio}). Aborting diff for this run."
        )


def classify_listings(
    scraped: pd.DataFrame,
    existing: pd.DataFrame,
    now: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Classify listings as new, unchanged, modified, or delisted.

    Parameters
    ----------
    scraped : today's parsed listings for the commune(s) in this run.
        Must contain 'id', 'price'.
    existing : current `listings` rows for the same commune(s).
        Must contain 'id', 'price', 'last_seen_at'.
    now : this run's timestamp. Defaults to pd.Timestamp.now(tz="UTC").
        Exposed as a parameter so tests can inject a fixed value instead
        of depending on wall-clock time.

    Returns
    -------
    DataFrame combining:
      - every row from `scraped`, tagged 'new' / 'unchanged' / 'modified'
      - any `existing` rows confirmed delisted this run, tagged 'delisted'
    Common columns: 'id', 'diff_status', 'old_price' (populated only for
    'modified' rows).
    """
    if now is None:
        now = pd.Timestamp.now(tz="UTC")

    scraped = scraped.reindex(columns=["id", "price"]).copy() if scraped.empty else scraped.copy()
    existing = (
        existing.reindex(columns=["id", "price", "last_seen_at"]).copy()
        if existing.empty
        else existing.copy()
    )
    existing["last_seen_at"] = pd.to_datetime(existing["last_seen_at"], utc=True)
    existing_by_id = existing.set_index("id")

    # --- new / unchanged / modified ---
    def _classify_scraped_row(row: pd.Series) -> pd.Series:
        listing_id = row["id"]
        if listing_id not in existing_by_id.index:
            return pd.Series({"diff_status": "new", "old_price": pd.NA})
        old_price = existing_by_id.at[listing_id, "price"]
        if row["price"] == old_price:
            return pd.Series({"diff_status": "unchanged", "old_price": pd.NA})
        return pd.Series({"diff_status": "modified", "old_price": old_price})

    if scraped.empty:
        scraped["diff_status"] = pd.Series(dtype="object")
        scraped["old_price"] = pd.Series(dtype="object")
    else:
        scraped[["diff_status", "old_price"]] = scraped.apply(_classify_scraped_row, axis=1)

    # --- delisted: existing rows absent from today's scrape, confirmed via
    # elapsed time rather than a run counter ---
    missing_ids = existing_by_id.index.difference(pd.Index(scraped["id"]))
    missing = existing_by_id.loc[missing_ids]

    confirmation_threshold = now - ((DELISTING_CONFIRMATION_MISSES - 0.5) * SCRAPE_INTERVAL)
    confirmed_delisted = missing[missing["last_seen_at"] < confirmation_threshold]

    delisted_rows = confirmed_delisted.reset_index()[["id", "price"]]
    delisted_rows["diff_status"] = "delisted"
    delisted_rows["old_price"] = pd.NA

    result_cols = ["id", "price", "diff_status", "old_price"]
    scraped_result = scraped.loc[:, result_cols]
    delisted_result = delisted_rows.loc[:, result_cols]
    combined = pd.concat([scraped_result, delisted_result], ignore_index=True)
    assert isinstance(
        combined, pd.DataFrame
    )  # narrows for pyright; concat of two DataFrames is always a DataFrame
    return combined
