import pandas as pd

from real_estate_pipeline.dvf.models import Transaction


def to_dataframe(transactions: list[Transaction]) -> pd.DataFrame:
    """Convert a list of Transaction objects into a raw DataFrame, one row per mutation.

    No derived columns or flags are added here — this is a straight field-for-field
    conversion. See add_price_per_m2, add_single_unit_flag, and
    flag_market_transactions for the analytical columns.
    """
    return pd.DataFrame([vars(t) for t in transactions])


def add_price_per_m2(df: pd.DataFrame) -> pd.DataFrame:
    """Add a price_per_m2 column: price / surface_bati.

    NaN where surface_bati == 0 (bare land — 'price per m2 of building' doesn't
    apply to a transaction with no building on it).
    """
    df = df.copy()
    df["price_per_m2"] = df["price"] / df["surface_bati"].replace(0, pd.NA)
    return df


def add_single_unit_flag(df: pd.DataFrame) -> pd.DataFrame:
    """Add is_single_residential_unit: True when the mutation unambiguously
    represents exactly one residential unit (mirrors the same condition
    dvf/parser.py's _derive_rooms() uses to populate rooms vs. return None).
    """
    df = df.copy()
    df["is_single_residential_unit"] = df["rooms"].notna()
    return df


def flag_market_transactions(df: pd.DataFrame, min_price: float = 2) -> pd.DataFrame:
    """Add is_market_transaction: True when price is at or above min_price.

    Default min_price=2 excludes mutations at price <= 1 (0 or 1 euro), per
    Cerema's own documented guidance on low/zero-value mutations (resales
    between subsidiaries, transfers between local authorities and HLM
    organizations), kept in DVF for transaction-count completeness but
    recommended excluded from price analysis. Source: Cerema Datafoncier,
    'valeurfonc' field documentation —
    https://doc-datafoncier.cerema.fr/doc/dv3f/mutation/valeurfonc

    min_price is an inclusive floor: price == min_price passes.

    Does not drop rows — flag only, consistent with is_single_residential_unit.
    """
    df = df.copy()
    df["is_market_transaction"] = df["price"] >= min_price
    return df


def clean_transactions(transactions: list[Transaction], min_price: float = 2) -> pd.DataFrame:
    """Build the cleaned DVF DataFrame: convert transactions and add all
    derived/flag columns (price_per_m2, is_single_residential_unit,
    is_market_transaction).

    No rows are dropped — every flag/derived column is additive, so callers
    can filter or group on them as needed rather than losing rows here.
    """
    df = to_dataframe(transactions)
    df = add_price_per_m2(df)
    df = add_single_unit_flag(df)
    df = flag_market_transactions(df, min_price=min_price)
    return df
