"""Market statistics over the stored DVF transactions (roadmap step 4.1).

This module decides which stored sales count as "the market" for a commune
and a property segment. Later tasks build the median price per m2 by period
(4.1.1) and the comparable lookup (4.1.3) on top of `fetch_market_sales`, so
the dashboard and the AI insights can never disagree about what counts.

Filtering happens in SQL (it is cheap and keeps the rows small); the
statistics are computed in pandas afterwards. SQLite, which the tests run on,
has no percentile function, so a SQL median could not be tested.

The `transactions` table stores only the 13 source fields, so the rules that
the cleaning layer flags in pandas (`price >= 2`, `price / surface_bati`) are
rebuilt here, in SQL terms. That duplication is accepted in docs/decisions.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import MetaData, Table, select
from sqlalchemy.engine import Connection, Engine

from real_estate_pipeline.common.db import run_in_transaction
from real_estate_pipeline.dvf.orchestrator import DEFAULT_COMMUNE_CODES

# Same inclusive floor as the cleaning layer's is_market_transaction: Cerema
# recommends leaving mutations at 0 or 1 euro out of price analysis.
MIN_PRICE = 2

# nature_mutation_code values (see dvf/models.py). Only these two are market
# sales; 3 expropriation, 4 land to build, 5 auction and 6 exchange are not.
ORDINARY_SALE_CODE = 1
NEW_BUILD_SALE_CODE = 2  # VEFA: sold before completion, by a developer


@dataclass(frozen=True)
class Segment:
    """One slice of the market: a single property type, resale or new-build.

    Resale and new-build are kept apart because a new-build price includes the
    developer's margin and would drag a resale median up.
    """

    name: str
    property_type_code: str
    new_build: bool


# Single apartments (121) and single houses (111) carry the market: together
# about 87,000 usable sales. We segment on property_type_code, not on `rooms`:
# only 43% of apartment sales have `rooms` filled, so is_single_residential_unit
# would throw most of them away.
SEGMENTS: dict[str, Segment] = {
    segment.name: segment
    for segment in (
        Segment("apartment", "121", new_build=False),
        Segment("house", "111", new_build=False),
        Segment("new_apartment", "121", new_build=True),
        Segment("new_house", "111", new_build=True),
    )
}

SALES_COLUMNS = ["id_mutation", "date", "price", "surface_bati", "price_per_m2"]


def _get_segment(name: str) -> Segment:
    try:
        return SEGMENTS[name]
    except KeyError:
        raise ValueError(f"Unknown segment {name!r}. Known: {', '.join(SEGMENTS)}") from None


def _sales_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Turn query rows into the sales frame (pure, so it can be tested with
    Decimal values, which is what Postgres numeric returns and SQLite never
    does).

    price_per_m2 is price / surface_bati; the query already guarantees a price
    of at least MIN_PRICE and a built surface above zero, so it is never
    infinite or missing.
    """
    frame = pd.DataFrame(rows, columns=["id_mutation", "date", "price", "surface_bati"])
    frame["id_mutation"] = frame["id_mutation"].astype("int64")
    frame["date"] = pd.to_datetime(frame["date"])
    frame["price"] = frame["price"].astype(float)
    frame["surface_bati"] = frame["surface_bati"].astype(float)
    frame["price_per_m2"] = frame["price"] / frame["surface_bati"]
    return frame.reindex(columns=SALES_COLUMNS)


def fetch_market_sales(
    engine: Engine,
    commune_code: str,
    segment: str,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """The market sales of one commune and one segment, oldest first.

    A sale counts when its price is at least MIN_PRICE, its built surface is
    above zero (bare land has none), and it is of the segment's kind: an
    ordinary sale that is not new-build for a resale segment, a new-build
    sale for a new-build one. Auctions, exchanges and the like are never
    counted.

    `start` and `end` are inclusive and optional. `commune_code` must be one
    of the twelve known communes: it is the mutation's primary commune, and
    the few stored rows whose primary commune lies outside the twelve are
    therefore never returned.

    Returns columns id_mutation, date (datetime64), price, surface_bati and
    price_per_m2 (all float except the ids). An empty result is an empty
    frame with the same columns.

    Raises:
        ValueError: unknown commune code or segment name.
    """
    if commune_code not in DEFAULT_COMMUNE_CODES:
        raise ValueError(
            f"Unknown commune code {commune_code!r}. Known: {', '.join(DEFAULT_COMMUNE_CODES)}"
        )
    definition = _get_segment(segment)
    sale_code = NEW_BUILD_SALE_CODE if definition.new_build else ORDINARY_SALE_CODE

    table = Table("transactions", MetaData(), autoload_with=engine)
    statement = (
        select(table.c.id_mutation, table.c.date, table.c.price, table.c.surface_bati)
        .where(table.c.commune_code == commune_code)
        .where(table.c.property_type_code == definition.property_type_code)
        .where(table.c.is_vefa == definition.new_build)
        .where(table.c.nature_mutation_code == sale_code)
        .where(table.c.price >= MIN_PRICE)
        .where(table.c.surface_bati > 0)
        .order_by(table.c.date, table.c.id_mutation)
    )
    if start is not None:
        statement = statement.where(table.c.date >= start)
    if end is not None:
        statement = statement.where(table.c.date <= end)

    def _read(connection: Connection) -> list[dict[str, Any]]:
        return [dict(row) for row in connection.execute(statement).mappings()]

    return _sales_frame(run_in_transaction(engine, _read))
