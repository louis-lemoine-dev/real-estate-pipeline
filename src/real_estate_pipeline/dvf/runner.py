"""Load the DVF+ transactions of several communes into Supabase, one at a time.

For each commune: fetch (dvf/orchestrator.py), clean, then upsert with
load_transactions() in that commune's own transaction. A commune that fails
is recorded and the run moves on to the next one, so a failure costs only
itself, and a re-run is safe: communes already loaded come back unchanged.

The runner only accepts the communes of DEFAULT_COMMUNE_CODES, which catches
a mistyped code before any slow request is made. The orchestrator itself
stays open to any commune.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from sqlalchemy.engine import Engine

from real_estate_pipeline.dvf.cleaning import clean_transactions
from real_estate_pipeline.dvf.load import LoadSummary, load_transactions
from real_estate_pipeline.dvf.models import Transaction
from real_estate_pipeline.dvf.orchestrator import (
    DEFAULT_COMMUNE_CODES,
    RATE_LIMIT_SECONDS,
    fetch_transactions,
)

logger = logging.getLogger(__name__)

# Longest error text kept for a failed commune. The real HTTP status of a
# failed request comes first in the message, so it survives the cut.
MAX_ERROR_CHARS = 500

Status = Literal["loaded", "skipped", "failed"]


@dataclass(frozen=True)
class CommuneResult:
    """What happened to one commune in a run."""

    code: str
    status: Status
    fetched: int = 0
    summary: LoadSummary | None = None
    error: str | None = None


def validate_commune_codes(codes: Sequence[str]) -> list[str]:
    """Return the codes as a list, or raise ValueError naming the unknown ones."""
    unknown = [code for code in codes if code not in DEFAULT_COMMUNE_CODES]
    if unknown:
        raise ValueError(
            f"unknown commune code(s): {', '.join(unknown)}. "
            f"Known codes: {', '.join(DEFAULT_COMMUNE_CODES)}"
        )
    return list(codes)


def _short(error: Exception) -> str:
    text = f"{type(error).__name__}: {error}"
    if len(text) <= MAX_ERROR_CHARS:
        return text
    return text[: MAX_ERROR_CHARS - 3] + "..."


def _load_one(
    code: str,
    engine: Engine,
    fetch: Callable[[list[str]], list[Transaction]],
) -> CommuneResult:
    """Fetch, clean and load one commune. Any Exception is recorded, not raised.

    KeyboardInterrupt and SystemExit are not Exceptions, so Ctrl-C still stops
    the whole run.
    """
    try:
        transactions = fetch([code])
        if not transactions:
            # clean_transactions([]) would crash on the missing price column.
            logger.warning("Commune %s: nothing fetched, skipped", code)
            return CommuneResult(code=code, status="skipped")
        summary = load_transactions(clean_transactions(transactions), engine)
    except Exception as error:
        logger.exception("Commune %s failed", code)
        return CommuneResult(code=code, status="failed", error=_short(error))

    logger.info(
        "Commune %s: %d fetched, %d inserted, %d updated, %d unchanged",
        code,
        len(transactions),
        summary.inserted,
        summary.updated,
        summary.unchanged,
    )
    return CommuneResult(code=code, status="loaded", fetched=len(transactions), summary=summary)


def load_communes(
    codes: Sequence[str],
    engine: Engine,
    fetch: Callable[[list[str]], list[Transaction]] = fetch_transactions,
    pause_seconds: float = RATE_LIMIT_SECONDS,
) -> list[CommuneResult]:
    """Load each commune in turn, each in its own transaction.

    Raises ValueError, before anything is fetched, if a code is not one of the
    twelve known communes. After that no commune-level failure is raised: it
    is recorded in the returned results and the run continues.

    `fetch` receives a one-element list (`[code]`), so the orchestrator never
    sleeps inside a call: the courtesy pause between communes happens here.
    """
    to_load = validate_commune_codes(codes)

    results: list[CommuneResult] = []
    for index, code in enumerate(to_load):
        logger.info("Commune %s (%d/%d)", code, index + 1, len(to_load))
        results.append(_load_one(code, engine, fetch))
        if index < len(to_load) - 1:
            time.sleep(pause_seconds)
    return results


def has_failures(results: Sequence[CommuneResult]) -> bool:
    return any(result.status == "failed" for result in results)


def format_report(results: Sequence[CommuneResult]) -> str:
    """One line per commune, a totals line, and how to re-run the failures."""
    lines: list[str] = []
    for result in results:
        if result.status == "skipped":
            lines.append(f"{result.code}  SKIPPED  nothing fetched")
        elif result.status == "failed":
            lines.append(f"{result.code}  FAILED   {result.error}")
        elif result.summary is not None:
            summary = result.summary
            lines.append(
                f"{result.code}  loaded   {result.fetched} fetched | {summary.inserted} inserted, "
                f"{summary.updated} updated, {summary.unchanged} unchanged"
            )

    summaries = [result.summary for result in results if result.summary is not None]
    count = {status: sum(r.status == status for r in results) for status in get_args(Status)}
    lines.append(
        f"Total: {count['loaded']} loaded, {count['skipped']} skipped, {count['failed']} failed | "
        f"{sum(r.fetched for r in results)} fetched, "
        f"{sum(s.inserted for s in summaries)} inserted, "
        f"{sum(s.updated for s in summaries)} updated, "
        f"{sum(s.unchanged for s in summaries)} unchanged"
    )

    failed_codes = [result.code for result in results if result.status == "failed"]
    if failed_codes:
        lines.append(
            "Re-run the failed communes with: "
            f"poetry run python scripts/load_dvf.py {' '.join(failed_codes)}"
        )
    return "\n".join(lines)
