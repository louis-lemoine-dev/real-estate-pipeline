"""Tests for src/real_estate_pipeline/paruvendu/price_events.py."""

from __future__ import annotations

import pandas as pd

from real_estate_pipeline.paruvendu.price_events import compute_price_deltas


def _classified(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_price_decrease_produces_negative_delta():
    classified = _classified(
        [{"id": "L1", "price": 145000, "diff_status": "modified", "old_price": 150000}]
    )

    result = compute_price_deltas(classified)

    row = result.iloc[0]
    assert row["delta_eur"] == -5000
    # (145000 - 150000) / 150000 * 100 = -3.3333...
    assert abs(row["delta_pct"] - (-3.3333333333333335)) < 1e-9


def test_price_increase_produces_positive_delta():
    classified = _classified(
        [{"id": "L1", "price": 160000, "diff_status": "modified", "old_price": 150000}]
    )

    result = compute_price_deltas(classified)

    row = result.iloc[0]
    assert row["delta_eur"] == 10000
    assert row["delta_pct"] > 0


def test_non_modified_rows_get_no_delta():
    classified = _classified(
        [
            {"id": "L1", "price": 100000, "diff_status": "new", "old_price": pd.NA},
            {
                "id": "L2",
                "price": 200000,
                "diff_status": "unchanged",
                "old_price": pd.NA,
            },
            {"id": "L3", "price": 50000, "diff_status": "delisted", "old_price": pd.NA},
        ]
    )

    result = compute_price_deltas(classified)

    assert bool(result["delta_eur"].isna().all())
    assert bool(result["delta_pct"].isna().all())


def test_zero_old_price_guards_division_but_still_computes_delta_eur():
    """old_price == 0 shouldn't happen in practice (price is NOT NULL in
    the schema), but is guarded anyway: delta_pct must be None (a % change
    from zero is undefined), while delta_eur is still computed since it
    doesn't involve division.
    """
    classified = _classified(
        [{"id": "L1", "price": 50000, "diff_status": "modified", "old_price": 0}]
    )

    result = compute_price_deltas(classified)

    row = result.iloc[0]
    assert row["delta_eur"] == 50000
    assert pd.isna(row["delta_pct"])


def test_delta_pct_not_rounded():
    """Full precision is stored — rounding is a display concern, not
    computed away here.
    """
    classified = _classified(
        [{"id": "L1", "price": 100000, "diff_status": "modified", "old_price": 300000}]
    )

    result = compute_price_deltas(classified)

    row = result.iloc[0]
    # (100000 - 300000) / 300000 * 100 = -66.66666...
    assert abs(row["delta_pct"] - (-66.66666666666667)) < 1e-9


def test_mixed_batch_only_modified_rows_get_deltas():
    classified = _classified(
        [
            {"id": "L1", "price": 145000, "diff_status": "modified", "old_price": 150000},
            {"id": "L2", "price": 200000, "diff_status": "unchanged", "old_price": pd.NA},
            {"id": "L3", "price": 90000, "diff_status": "new", "old_price": pd.NA},
        ]
    )

    result = compute_price_deltas(classified)

    l1 = result.loc[result["id"] == "L1"].iloc[0]
    l2 = result.loc[result["id"] == "L2"].iloc[0]
    l3 = result.loc[result["id"] == "L3"].iloc[0]

    assert l1["delta_eur"] == -5000
    assert pd.isna(l2["delta_eur"])
    assert pd.isna(l3["delta_eur"])
