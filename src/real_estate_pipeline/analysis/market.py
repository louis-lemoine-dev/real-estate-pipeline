"""Market statistics over the stored DVF transactions (roadmap step 4.1).

This module decides which stored sales count as "the market" for a commune
and a property segment (resale only: new-build is left out, see below). Later
tasks build the median price per m2 by period (4.1.1) and the comparable
lookup (4.1.3) on top of `fetch_market_sales`, so the dashboard and the AI
insights can never disagree about what counts.

Filtering happens in SQL (it is cheap and keeps the rows small); the
statistics are computed in pandas afterwards. SQLite, which the tests run on,
has no percentile function, so a SQL median could not be tested.

The `transactions` table stores only the 13 source fields, so the rules that
the cleaning layer flags in pandas (`price >= 2`, `price / surface_bati`) are
rebuilt here, in SQL terms. That duplication is accepted in docs/decisions.md.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.engine import Connection, Engine

from real_estate_pipeline.common.db import run_in_transaction
from real_estate_pipeline.dvf.orchestrator import DEFAULT_COMMUNE_CODES

# Same inclusive floor as the cleaning layer's is_market_transaction: Cerema
# recommends leaving mutations at 0 or 1 euro out of price analysis.
MIN_PRICE = 2

# nature_mutation_code 1 is an ordinary sale. The others are not market sales:
# 2 new-build (VEFA), 3 expropriation, 4 land to build, 5 auction, 6 exchange.
# New-build is left out on purpose: since 2019 almost every new-build sale is
# the "no description" type with no built surface, so only about 10 a year have
# a usable price per m2 (against about 500 a year in 2014-2018).
ORDINARY_SALE_CODE = 1


@dataclass(frozen=True)
class Segment:
    """One slice of the resale market: a single property type."""

    name: str
    property_type_code: str


# Single apartments (121) and single houses (111) carry the market: together
# about 87,000 usable sales. We segment on property_type_code, not on `rooms`:
# only 43% of apartment sales have `rooms` filled, so is_single_residential_unit
# would throw most of them away.
SEGMENTS: dict[str, Segment] = {
    segment.name: segment
    for segment in (
        Segment("apartment", "121"),
        Segment("house", "111"),
    )
}

# A median over fewer sales than this is flagged low_sample (never hidden).
DEFAULT_MIN_SAMPLE = 30

# Period name -> pandas period frequency. Yearly is the default for the
# dashboard: quarterly medians are meaningless in the small communes
# (Saint-Mandrier has about 7 house sales a quarter).
_PERIOD_FREQUENCIES = {"year": "Y", "quarter": "Q"}

SALES_COLUMNS = ["id_mutation", "date", "price", "surface_bati", "price_per_m2"]
TREND_COLUMNS = ["period_start", "median_price_per_m2", "sale_count", "low_sample"]


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
    above zero (bare land has none), and it is an ordinary resale: nature code
    1 and not flagged new-build. New-build, auctions, exchanges and the like
    are never counted.

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

    table = Table("transactions", MetaData(), autoload_with=engine)
    statement = (
        select(table.c.id_mutation, table.c.date, table.c.price, table.c.surface_bati)
        .where(table.c.commune_code == commune_code)
        .where(table.c.property_type_code == definition.property_type_code)
        .where(table.c.is_vefa.is_(False))
        .where(table.c.nature_mutation_code == ORDINARY_SALE_CODE)
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


def median_price_per_m2_by_period(
    sales: pd.DataFrame,
    period: str = "year",
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> pd.DataFrame:
    """Median price per m2 for each period, from a frame of market sales.

    `sales` is what `fetch_market_sales` returns. Median rather than mean:
    price per m2 is heavily right-skewed and a few exceptional sales distort
    an average badly (Cerema's own guidance). `period` is "year" or "quarter".

    Returns one row per period, oldest first, with columns period_start (the
    first day of the period), median_price_per_m2, sale_count and low_sample.
    The median is NEVER hidden because of a small sample: low_sample is simply
    True when sale_count is below `min_sample`, and the caller decides how to
    show it. A period with no sales at all has no median to give (NaN) and a
    sale_count of 0; such periods between the first and last sale are kept in
    the result, so a chart does not silently join across a gap. An empty
    input gives an empty frame with the same columns.

    Raises:
        ValueError: unknown period.
    """
    if period not in _PERIOD_FREQUENCIES:
        raise ValueError(f"Unknown period {period!r}. Known: {', '.join(_PERIOD_FREQUENCIES)}")
    if sales.empty:
        return pd.DataFrame(
            {
                "period_start": pd.Series(dtype="datetime64[ns]"),
                "median_price_per_m2": pd.Series(dtype="float64"),
                "sale_count": pd.Series(dtype="int64"),
                "low_sample": pd.Series(dtype="bool"),
            }
        )

    frequency = _PERIOD_FREQUENCIES[period]
    periods = sales["date"].dt.to_period(frequency)
    by_period = sales.groupby(periods)["price_per_m2"]
    every_period = pd.period_range(periods.min(), periods.max(), freq=frequency)
    medians = by_period.median().reindex(every_period)
    counts = by_period.count().reindex(every_period, fill_value=0).astype("int64")
    return pd.DataFrame(
        {
            "period_start": every_period.to_timestamp(),
            "median_price_per_m2": medians.to_numpy(dtype="float64"),
            "sale_count": counts.to_numpy(),
            "low_sample": (counts < min_sample).to_numpy(),
        }
    )


def price_trend(
    engine: Engine,
    commune_code: str,
    segment: str,
    period: str = "year",
    min_sample: int = DEFAULT_MIN_SAMPLE,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """The median price per m2 by period for one commune and one segment.

    Fetches the market sales (see `fetch_market_sales` for what counts) and
    summarises them (see `median_price_per_m2_by_period`).
    """
    sales = fetch_market_sales(engine, commune_code, segment, start, end)
    return median_price_per_m2_by_period(sales, period, min_sample)


@dataclass(frozen=True)
class ComparableStats:
    """The comparable sold prices of one commune and segment over one window.

    The three price figures are euros per m2. They are None only when no sale
    falls in the window (sale_count 0). A small sample never removes a figure:
    low_sample just says the count is below the threshold, and the caller
    decides how to show it.
    """

    commune_code: str
    segment: str
    window_start: date  # first day included
    window_end: date  # last day included
    sale_count: int
    median_price_per_m2: float | None
    q25_price_per_m2: float | None  # a quarter of the sales sold for less
    q75_price_per_m2: float | None  # a quarter of the sales sold for more
    low_sample: bool


def latest_sale_date(engine: Engine) -> date:
    """The date of the latest sale in the whole transactions table.

    Used as the end of the comparable window, because the data lags today
    (the latest sale was on 31 Dec 2025 when the table was loaded) and a window
    counted back from today would be almost empty. It is the latest over ALL
    communes and types, not per commune, so every commune and segment is
    compared over the same window.

    Raises:
        ValueError: the table holds no sale at all.
    """
    table = Table("transactions", MetaData(), autoload_with=engine)

    def _read(connection: Connection) -> Any:
        return connection.execute(select(func.max(table.c.date))).scalar()

    latest = run_in_transaction(engine, _read)
    if latest is None:
        raise ValueError("The transactions table holds no sale, so there is no latest sale date.")
    return latest.date() if isinstance(latest, datetime) else latest


def _window_start(window_end: date, window_months: int) -> date:
    """First day of a window of `window_months` calendar months ending on `window_end`.

    Both ends are included: ending on 31 Dec 2025, 12 months start on 1 Jan 2025.
    It is the same date `window_months` earlier (clipped to the end of a shorter
    month, so 28 Feb minus a year is 28 Feb), plus one day.
    """
    months_since_year_zero = window_end.year * 12 + (window_end.month - 1) - window_months
    year, month_index = divmod(months_since_year_zero, 12)
    month = month_index + 1
    day = min(window_end.day, calendar.monthrange(year, month)[1])
    return date(year, month, day) + timedelta(days=1)


def summarize_comparables(
    sales: pd.DataFrame,
    commune_code: str,
    segment: str,
    window_start: date,
    window_end: date,
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> ComparableStats:
    """Median and quartiles of the price per m2 of `sales` (pure pandas, no database).

    `sales` is what `fetch_market_sales` returns, already limited to the window.
    The quartiles interpolate linearly like the median, which is also what
    Postgres' percentile function does. No sales: count 0, no figures,
    low_sample True.
    """
    prices = sales["price_per_m2"]
    count = len(prices)
    if count == 0:
        median = q25 = q75 = None
    else:
        values = prices.to_numpy(dtype="float64")
        median = float(np.median(values))
        q25 = float(np.quantile(values, 0.25))
        q75 = float(np.quantile(values, 0.75))
    return ComparableStats(
        commune_code=commune_code,
        segment=segment,
        window_start=window_start,
        window_end=window_end,
        sale_count=count,
        median_price_per_m2=median,
        q25_price_per_m2=q25,
        q75_price_per_m2=q75,
        low_sample=count < min_sample,
    )


def comparable_stats(
    engine: Engine,
    commune_code: str,
    segment: str,
    window_months: int = 12,
    as_of: date | None = None,
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> ComparableStats:
    """The comparable sold prices for one commune and one segment.

    This is the one function the dashboard and the later comparison with
    listings share, so they can never disagree about what "comparable" means.

    The window is `window_months` calendar months ending on `as_of` (both ends
    included). `as_of` defaults to the latest sale in the whole table (see
    `latest_sale_date`). The window is never widened automatically: a thin
    commune returns its few sales with low_sample True, so a caller always
    knows exactly which sales were used (see window_start and window_end).
    What counts as a sale is decided by `fetch_market_sales`.

    Raises:
        ValueError: window_months below 1, an unknown commune or segment, or no
            `as_of` given and an empty transactions table.
    """
    if window_months < 1:
        raise ValueError(f"window_months must be at least 1, got {window_months}")
    window_end = as_of if as_of is not None else latest_sale_date(engine)
    window_start = _window_start(window_end, window_months)
    sales = fetch_market_sales(engine, commune_code, segment, window_start, window_end)
    return summarize_comparables(sales, commune_code, segment, window_start, window_end, min_sample)
