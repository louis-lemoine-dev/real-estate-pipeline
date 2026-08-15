"""
Orchestration layer for DVF+ transaction fetching.

Wires the fetch layer (dvf/client.py) and parse layer (dvf/parser.py)
together into a single entry point, across one or more communes.
"""

import logging
import time

from real_estate_pipeline.dvf.client import fetch_mutations
from real_estate_pipeline.dvf.models import Transaction
from real_estate_pipeline.dvf.parser import parse_transactions

logger = logging.getLogger(__name__)

RATE_LIMIT_SECONDS = 1.5  # courtesy delay between communes

# Métropole Toulon Provence Méditerranée (TPM), EPCI 248300543,
# per INSEE as of Aug 2026. If TPM's membership changes, update here —
# see the "Future: nationwide commune resolution" Notion task for a
# dynamic alternative (geo.api.gouv.fr) if this becomes worth automating.
DEFAULT_COMMUNE_CODES = [
    "83034",  # Carqueiranne
    "83047",  # La Crau
    "83062",  # La Garde
    "83069",  # Hyères
    "83090",  # Ollioules
    "83098",  # Le Pradet
    "83103",  # Le Revest-les-Eaux
    "83126",  # La Seyne-sur-Mer
    "83129",  # Six-Fours-les-Plages
    "83137",  # Toulon
    "83144",  # La Valette-du-Var
    "83153",  # Saint-Mandrier-sur-Mer
]


def fetch_transactions(
    commune_codes: list[str] | None = None,
    max_pages_per_commune: int = 10,
) -> list[Transaction]:
    """
    Fetch and parse DVF+ transactions across one or more communes.

    Args:
        commune_codes: INSEE codes to fetch. If None, defaults to
            every commune in the Métropole Toulon Provence
            Méditerranée (see DEFAULT_COMMUNE_CODES).
        max_pages_per_commune: Safety cap on pages fetched per
            commune, passed through to fetch_mutations().

    Returns:
        Parsed Transactions across all communes, deduplicated by
        id_mutation.
    """
    codes = commune_codes if commune_codes is not None else DEFAULT_COMMUNE_CODES

    seen_ids: set[int] = set()
    all_transactions: list[Transaction] = []

    for i, code in enumerate(codes):
        logger.info("Fetching commune %s (%d/%d)", code, i + 1, len(codes))

        raw = fetch_mutations(code, max_pages=max_pages_per_commune)
        transactions = parse_transactions(raw)

        new_count = 0
        for transaction in transactions:
            if transaction.id_mutation not in seen_ids:
                seen_ids.add(transaction.id_mutation)
                all_transactions.append(transaction)
                new_count += 1

        logger.info(
            "Commune %s: %d fetched, %d new, %d duplicates",
            code,
            len(transactions),
            new_count,
            len(transactions) - new_count,
        )

        if i < len(codes) - 1:
            time.sleep(RATE_LIMIT_SECONDS)

    return all_transactions
