"""Tests for src/real_estate_pipeline/paruvendu/diff.py."""

from __future__ import annotations

from typing import cast

import pandas as pd
import pytest

from real_estate_pipeline.paruvendu.diff import (
    DELISTING_CONFIRMATION_MISSES,
    ScrapeAnomalyError,
    check_run_sanity,
    classify_listings,
)
from real_estate_pipeline.paruvendu.models import LISTING_COLUMNS

# Cast explicitly: pandas-stubs types pd.Timestamp(...) as Timestamp | NaTType
# in some configurations (a string could theoretically parse to NaT), but this
# literal is known-valid at write time — never actually NaT.
NOW = cast(pd.Timestamp, pd.Timestamp("2026-08-30T00:00:00Z"))


def _existing(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _full_scraped_row(row: dict) -> dict:
    """Complete a partial test row into a full scraped listing.

    classify_listings requires every Listing column, so tests only spell
    out the fields they care about (id, price, ...) and the rest get
    neutral defaults. Fails loudly if a Listing field is ever added
    without a default here, instead of silently testing incomplete data.
    """
    defaults = {
        "id": "L?",
        "url": f"https://x/{row['id']}",
        "property_type": "Appartement",
        "rooms": 2,
        "surface_m2": 40.0,
        "chambres": 1,
        "dpe": "D",
        "terrain_m2": None,
        "has_garage": False,
        "has_ascenseur": False,
        "has_balcon": False,
        "price": 100000,
        "has_asterisk": False,
        "location": "Toulon",
        "amenities": [],
        "description": "desc",
    }
    unhandled = set(LISTING_COLUMNS) - set(defaults)
    assert not unhandled, f"add test defaults for new Listing field(s): {unhandled}"
    return {**defaults, **row}


def _scraped(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([_full_scraped_row(row) for row in rows])


# --- classify_listings: new / unchanged / modified ---


def test_new_listing_not_in_existing():
    existing = _existing([])
    scraped = _scraped([{"id": "L1", "price": 150000}])

    result = classify_listings(scraped, existing, now=NOW)

    row = result.loc[result["id"] == "L1"].iloc[0]
    assert row["diff_status"] == "new"
    assert pd.isna(row["old_price"])


def test_unchanged_listing_same_price():
    existing = _existing(
        [{"id": "L1", "price": 150000, "last_seen_at": NOW - pd.Timedelta(days=1)}]
    )
    scraped = _scraped([{"id": "L1", "price": 150000}])

    result = classify_listings(scraped, existing, now=NOW)

    row = result.loc[result["id"] == "L1"].iloc[0]
    assert row["diff_status"] == "unchanged"
    assert pd.isna(row["old_price"])


def test_modified_listing_price_changed():
    existing = _existing(
        [{"id": "L1", "price": 150000, "last_seen_at": NOW - pd.Timedelta(days=1)}]
    )
    scraped = _scraped([{"id": "L1", "price": 145000}])

    result = classify_listings(scraped, existing, now=NOW)

    row = result.loc[result["id"] == "L1"].iloc[0]
    assert row["diff_status"] == "modified"
    assert row["old_price"] == 150000


def test_non_price_field_change_classified_unchanged():
    """A change to a non-price field (e.g. description) must still be
    classified 'unchanged', since classify_listings is deliberately
    price-only. This is the test that actually proves that design
    decision holds — every other 'unchanged' fixture happened to have
    fully identical rows, which wouldn't catch a bug that flagged ANY
    field difference as 'modified'.
    """
    existing = _existing(
        [
            {
                "id": "L1",
                "price": 150000,
                "last_seen_at": NOW - pd.Timedelta(days=1),
                "description": "Charming studio near the port",
            }
        ]
    )
    scraped = _scraped(
        [
            {
                "id": "L1",
                "price": 150000,  # unchanged
                "description": "Charming studio near the harbor",  # reworded
            }
        ]
    )

    result = classify_listings(scraped, existing, now=NOW)

    row = result.loc[result["id"] == "L1"].iloc[0]
    assert row["diff_status"] == "unchanged"


def test_scrape_missing_listing_columns_raises_naming_them():
    """A scrape that omits columns must be refused before anything is
    classified, naming every missing column. Silently carrying on would
    overwrite the stored values with NULL downstream.
    """
    existing = _existing(
        [{"id": "L1", "price": 150000, "last_seen_at": NOW - pd.Timedelta(days=1)}]
    )
    scraped = _scraped([{"id": "L1", "price": 145000}]).drop(columns=["dpe", "has_ascenseur"])

    with pytest.raises(ValueError, match=r"dpe.*has_ascenseur"):
        classify_listings(scraped, existing, now=NOW)


def test_mixed_batch_classified_independently():
    existing = _existing(
        [
            {"id": "L1", "price": 150000, "last_seen_at": NOW - pd.Timedelta(days=1)},
            {"id": "L2", "price": 200000, "last_seen_at": NOW - pd.Timedelta(days=1)},
        ]
    )
    scraped = _scraped(
        [
            {"id": "L1", "price": 150000},  # unchanged
            {"id": "L2", "price": 190000},  # modified
            {"id": "L3", "price": 99000},  # new
        ]
    )

    result = classify_listings(scraped, existing, now=NOW)
    statuses = dict(zip(result["id"], result["diff_status"], strict=True))

    assert statuses == {"L1": "unchanged", "L2": "modified", "L3": "new"}


def test_original_scraped_columns_survive_classification():
    """A scraped listing's non-price/id columns (url, surface, amenities...)
    must pass through untouched — classify_listings must not trim them.
    """
    existing = _existing([])
    scraped = _scraped(
        [
            {
                "id": "L1",
                "price": 150000,
                "url": "https://www.paruvendu.fr/listing/L1",
                "surface_m2": 42.5,
                "amenities": ["balcony", "parking"],
            }
        ]
    )

    result = classify_listings(scraped, existing, now=NOW)

    row = result.loc[result["id"] == "L1"].iloc[0]
    assert row["url"] == "https://www.paruvendu.fr/listing/L1"
    assert row["surface_m2"] == 42.5
    assert row["amenities"] == ["balcony", "parking"]


def test_delisted_row_carries_existing_columns_not_scraped_columns():
    """A delisted row comes from `existing`, not `scraped` — it should carry
    existing's columns (e.g. last_seen_at) and have NaN for scraped-only
    columns it never had (e.g. url), not raise or get dropped.
    """
    existing = _existing(
        [
            {
                "id": "L1",
                "price": 150000,
                "last_seen_at": NOW - pd.Timedelta(days=2),
            }
        ]
    )
    scraped = _scraped([{"id": "L2", "price": 99000, "url": "https://x/L2"}])

    result = classify_listings(scraped, existing, now=NOW)

    delisted_row = result.loc[result["id"] == "L1"].iloc[0]
    assert delisted_row["diff_status"] == "delisted"
    assert not pd.isna(delisted_row["last_seen_at"])
    assert pd.isna(delisted_row["url"])  # L1 was never in scraped — no url


def test_empty_scraped_batch_does_not_error():
    existing = _existing(
        [{"id": "L1", "price": 150000, "last_seen_at": NOW - pd.Timedelta(days=1)}]
    )
    scraped = _scraped([])
    # not old enough to be confirmed delisted yet
    result = classify_listings(scraped, existing, now=NOW)

    assert (result["diff_status"] == "delisted").sum() == 0


# --- classify_listings: delisted ---


def test_single_miss_not_yet_delisted():
    """One missed scrape (~1 day stale) must not be flagged delisted."""
    existing = _existing(
        [{"id": "L1", "price": 150000, "last_seen_at": NOW - pd.Timedelta(days=1)}]
    )
    scraped = _scraped([])  # L1 absent from today's scrape

    result = classify_listings(scraped, existing, now=NOW)

    assert "L1" not in result["id"].values


def test_two_consecutive_misses_confirms_delisted():
    """~2 days stale (2nd consecutive miss) should be confirmed delisted."""
    existing = _existing(
        [{"id": "L1", "price": 150000, "last_seen_at": NOW - pd.Timedelta(days=2)}]
    )
    scraped = _scraped([])

    result = classify_listings(scraped, existing, now=NOW)

    row = result.loc[result["id"] == "L1"].iloc[0]
    assert row["diff_status"] == "delisted"


def test_delisted_listing_not_confused_with_modified():
    """A delisted listing must not also appear tagged as new/modified."""
    existing = _existing(
        [
            {"id": "L1", "price": 150000, "last_seen_at": NOW - pd.Timedelta(days=2)},
            {"id": "L2", "price": 200000, "last_seen_at": NOW - pd.Timedelta(days=1)},
        ]
    )
    scraped = _scraped([{"id": "L2", "price": 200000}])

    result = classify_listings(scraped, existing, now=NOW)

    assert len(result) == 2
    assert result.loc[result["id"] == "L1", "diff_status"].iloc[0] == "delisted"
    assert result.loc[result["id"] == "L2", "diff_status"].iloc[0] == "unchanged"


def test_reappearing_listing_classified_new_not_delisted():
    """A listing absent long enough to qualify, but present in today's scrape,
    is classified from the scraped side (new/unchanged/modified) — the
    delisted branch only ever looks at ids missing from `scraped`.
    """
    existing = _existing(
        [{"id": "L1", "price": 150000, "last_seen_at": NOW - pd.Timedelta(days=5)}]
    )
    scraped = _scraped([{"id": "L1", "price": 150000}])

    result = classify_listings(scraped, existing, now=NOW)

    row = result.loc[result["id"] == "L1"].iloc[0]
    assert row["diff_status"] == "unchanged"


def test_confirmation_threshold_matches_constant():
    """Sanity check that the module's confirmation constant is what the
    time-based threshold is built from, so a future change to the constant
    doesn't silently drift the threshold below/above 2 misses without a
    test noticing.
    """
    assert DELISTING_CONFIRMATION_MISSES == 2


# --- check_run_sanity ---


def test_run_sanity_passes_for_normal_count():
    scraped = _scraped([{"id": f"L{i}", "price": 100000} for i in range(95)])
    check_run_sanity(scraped, expected_count=100)  # should not raise


def test_run_sanity_raises_for_collapsed_count():
    scraped = _scraped([{"id": "L1", "price": 100000}])
    with pytest.raises(ScrapeAnomalyError):
        check_run_sanity(scraped, expected_count=100)


def test_run_sanity_skipped_when_no_expected_count():
    scraped = _scraped([{"id": "L1", "price": 100000}])
    check_run_sanity(scraped, expected_count=0)  # first-ever run, no raise
