"""Load path for cleaned DVF transactions into the `transactions` table.

Takes the cleaned DataFrame for ONE commune (clean_transactions' output) and
upserts it by id_mutation: new mutations are inserted, mutations whose stored
row differs are updated, identical ones are left alone. Nothing is ever
deleted.

The upsert is done as read, compare, write inside one transaction, not as a
Postgres-only ON CONFLICT statement: the counts (inserted / updated /
unchanged) are then exact, and the exact same code runs on the in-memory
SQLite the tests use, so the tests protect the statements that run in
production.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

import pandas as pd
from sqlalchemy import MetaData, Table, bindparam, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from real_estate_pipeline.common.db import run_in_transaction
from real_estate_pipeline.dvf.models import (
    NULLABLE_TRANSACTION_COLUMNS,
    TRANSACTION_COLUMNS,
    require_transaction_columns,
)

logger = logging.getLogger(__name__)

# 13 columns x 1,000 rows = 13,000 bind parameters, far below Postgres's
# limit of 65,535 per statement.
DEFAULT_BATCH_SIZE = 1000

# Money and surfaces are compared after rounding to this many decimals, so a
# Decimal read from Postgres numeric and a float held by pandas compare equal.
COMPARISON_DECIMALS = 6

# How many ids an error message names before it just counts the rest.
_IDS_IN_MESSAGES = 5

_VALUE_COLUMNS = tuple(column for column in TRANSACTION_COLUMNS if column != "id_mutation")
_NOT_NULL_COLUMNS = tuple(
    column for column in TRANSACTION_COLUMNS if column not in NULLABLE_TRANSACTION_COLUMNS
)


@dataclass(frozen=True)
class LoadSummary:
    """What a load_transactions run actually did."""

    inserted: int
    updated: int
    unchanged: int


# --- Converting pandas values into the plain Python values Postgres expects ---


def _to_int(value: Any) -> int:
    as_int = int(value)
    if as_int != value:
        raise ValueError(f"{value!r} is not a whole number")
    return as_int


def _to_date(value: Any) -> date:
    # A pandas Timestamp is a datetime, which is a date: check datetime first.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError(f"{value!r} is not a date")


# One converter per Transaction column. A test fails if a field is added to
# Transaction without a converter here.
_CONVERTERS: dict[str, Callable[[Any], Any]] = {
    "id_mutation": _to_int,
    "date": _to_date,
    "year": _to_int,
    "price": float,
    "is_vefa": bool,
    "nature_mutation_code": _to_int,
    "nature_mutation_label": str,
    "property_type_code": str,
    "property_type_label": str,
    "surface_bati": float,
    "surface_terrain": float,
    "rooms": _to_int,
    "commune_code": str,
}


def _ids_for_message(ids: list[Any]) -> str:
    shown = ", ".join(str(i) for i in ids[:_IDS_IN_MESSAGES])
    extra = len(ids) - _IDS_IN_MESSAGES
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def _prepare_transaction_records(cleaned: pd.DataFrame) -> list[dict[str, Any]]:
    """Build the row-dicts to write, from clean_transactions' output.

    Pure data transform, no DB access: directly testable without a database.

    - Keeps exactly the Transaction columns (an allow-list): the three derived
      columns the cleaning step adds can never reach the table.
    - Refuses (ValueError) a frame missing a column, with a duplicate
      id_mutation, or with a null in a NOT NULL column.
    - Converts NaN to None so price and rooms become a real SQL NULL.
      Object dtype is forced first: .where() alone leaves NaN in a float64
      column (the 3.3.2 lesson).
    - Converts every value to a plain Python int / float / bool / date / str,
      so no numpy type depends on the driver's adaptation rules. rooms comes
      out as an int, not 3.0.
    """
    if cleaned.empty:
        return []

    require_transaction_columns(cleaned.columns, where="load_transactions")
    selected = cleaned.loc[:, list(TRANSACTION_COLUMNS)]

    duplicated = selected.loc[selected["id_mutation"].duplicated(keep=False), "id_mutation"]
    if not duplicated.empty:
        raise ValueError(
            "duplicate id_mutation in the input: "
            f"{_ids_for_message(sorted(set(duplicated.tolist())))}"
        )

    null_mask = selected.loc[:, list(_NOT_NULL_COLUMNS)].isna()
    for column in _NOT_NULL_COLUMNS:
        if null_mask[column].any():
            bad_ids = selected.loc[null_mask[column], "id_mutation"].tolist()
            raise ValueError(
                f"null in NOT NULL column '{column}' for id_mutation: {_ids_for_message(bad_ids)}"
            )

    prepared = selected.astype(object).where(pd.notna(selected), None)
    records = cast(list[dict[str, Any]], prepared.to_dict(orient="records"))

    for record in records:
        for column, convert in _CONVERTERS.items():
            value = record[column]
            if value is None:
                continue
            try:
                record[column] = convert(value)
            except ValueError as error:
                raise ValueError(
                    f"column '{column}', id_mutation {record['id_mutation']}: {error}"
                ) from error

    return records


# --- Deciding what each incoming row needs ---


def _normalize(value: Any) -> Any:
    """Make a stored value and an incoming value comparable.

    Postgres returns numeric as Decimal while pandas holds floats: both
    become a float rounded to COMPARISON_DECIMALS. A missing value (None or
    NaN) becomes None. Without this, every reload would flag every row as
    changed. SQLite cannot produce a Decimal, so a pure test covers this.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Decimal | float):
        number = float(value)
        return None if math.isnan(number) else round(number, COMPARISON_DECIMALS)
    return value


def _classify(
    records: list[dict[str, Any]], stored: dict[int, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a batch into (new, changed, unchanged) against the stored rows.

    Pure, no DB access. `stored` maps id_mutation to the stored row, for the
    ids of this batch that already exist.
    """
    new: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for record in records:
        existing = stored.get(record["id_mutation"])
        if existing is None:
            new.append(record)
        elif any(_normalize(record[c]) != _normalize(existing[c]) for c in _VALUE_COLUMNS):
            changed.append(record)
        else:
            unchanged.append(record)
    return new, changed, unchanged


def load_transactions(
    cleaned: pd.DataFrame,
    engine: Engine,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> LoadSummary:
    """Upsert one commune's cleaned DVF transactions into `transactions`.

    Everything happens in ONE transaction (all-or-nothing, retried on
    transient connection errors by common.db.run_in_transaction). Per batch of
    `batch_size` rows: read the stored rows for those ids, classify each
    incoming row as new / changed / unchanged, insert the new ones, update the
    changed ones. Rows absent from the input are never touched.

    Raises ValueError, before anything is written, on a malformed input (see
    _prepare_transaction_records). Raises IntegrityError immediately, logged
    with context first, without a retry: a data problem would fail
    identically every time.

    Returns a LoadSummary. All zeros, with no transaction opened, if there was
    nothing to load.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be at least 1, got {batch_size}")

    records = _prepare_transaction_records(cleaned)
    if not records:
        return LoadSummary(inserted=0, updated=0, unchanged=0)

    table = Table("transactions", MetaData(), autoload_with=engine)

    # Bind-parameter names get a "b_" prefix: SQLAlchemy reserves bare column
    # names as parameter names inside UPDATE ... SET.
    update_statement = (
        table.update()
        .where(table.c.id_mutation == bindparam("b_id_mutation"))
        .values({column: bindparam(f"b_{column}") for column in _VALUE_COLUMNS})
    )

    def _apply(connection: Connection) -> LoadSummary:
        # Counters live inside the attempt: a retried attempt starts from zero.
        inserted = updated = unchanged = 0
        for start in range(0, len(records), batch_size):
            batch = records[start : start + batch_size]
            ids = [record["id_mutation"] for record in batch]
            rows = connection.execute(select(table).where(table.c.id_mutation.in_(ids)))
            stored = {row["id_mutation"]: dict(row) for row in rows.mappings()}

            new, changed, same = _classify(batch, stored)
            if new:
                connection.execute(table.insert(), new)
            if changed:
                parameters = [
                    {"b_id_mutation": r["id_mutation"], **{f"b_{c}": r[c] for c in _VALUE_COLUMNS}}
                    for r in changed
                ]
                connection.execute(update_statement, parameters)
            inserted += len(new)
            updated += len(changed)
            unchanged += len(same)
        return LoadSummary(inserted=inserted, updated=updated, unchanged=unchanged)

    try:
        summary = run_in_transaction(engine, _apply)
    except IntegrityError:
        logger.error(
            "Integrity error loading %d transactions (first ids: %s). Transaction "
            "rolled back, nothing kept. Aborting, not retrying.",
            len(records),
            _ids_for_message([r["id_mutation"] for r in records]),
        )
        raise

    logger.info(
        "Transactions loaded: %d inserted, %d updated, %d unchanged",
        summary.inserted,
        summary.updated,
        summary.unchanged,
    )
    return summary
