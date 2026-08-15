"""
Parser layer for the DVF+ open-data transactions.

Pure functions that convert raw JSON dicts (from dvf/client.py's fetch
layer) into structured Transaction objects. No network calls or I/O —
these functions only transform data that's already been fetched.
"""

from datetime import date

from real_estate_pipeline.dvf.models import Transaction

_ROOM_BUCKET_FIELDS = [
    "nbapt1pp",
    "nbapt2pp",
    "nbapt3pp",
    "nbapt4pp",
    "nbapt5pp",
    "nbmai1pp",
    "nbmai2pp",
    "nbmai3pp",
    "nbmai4pp",
    "nbmai5pp",
]


def _derive_rooms(raw: dict) -> int | None:
    """
    Derive a single room count from DVF+'s per-bucket unit counts,
    only when the mutation unambiguously represents one residential
    unit: exactly one bucket (nbapt1pp...nbmai5pp) equal to 1, all
    others 0, and nblocmut == 1 confirming a single locau overall.

    Returns None for multi-unit mutations, non-residential mutations
    (bare land, commercial), or any other ambiguous case — rather
    than guess, we prefer an honest gap in the data.
    """
    non_zero = [(field, raw[field]) for field in _ROOM_BUCKET_FIELDS if raw.get(field, 0) != 0]

    if len(non_zero) != 1 or non_zero[0][1] != 1:
        return None
    if raw.get("nblocmut") != 1:
        return None

    field_name, _ = non_zero[0]
    # e.g. "nbapt4pp" or "nbmai5pp" — the digit before "pp" is the bucket
    return int(field_name[-3])


def parse_transaction(raw: dict) -> Transaction:
    """
    Convert one raw DVF+ mutation dict (from the API's `results` list)
    into a Transaction.

    Args:
        raw: A single mutation dict, as returned by
            dvf/client.py's fetch_mutations().

    Returns:
        A parsed Transaction.
    """
    return Transaction(
        id_mutation=raw["idmutation"],
        date=date.fromisoformat(raw["datemut"]),
        year=raw["anneemut"],
        price=float(raw["valeurfonc"]),
        is_vefa=raw["vefa"],
        nature_mutation_code=raw["idnatmut"],
        nature_mutation_label=raw["libnatmut"],
        property_type_code=raw["codtypbien"],
        property_type_label=raw["libtypbien"],
        surface_bati=float(raw["sbati"]),
        surface_terrain=float(raw["sterr"]),
        rooms=_derive_rooms(raw),
        commune_code=raw["l_codinsee"][0],
    )


def parse_transactions(raw_list: list[dict]) -> list[Transaction]:
    """
    Convert a list of raw DVF+ mutation dicts into Transactions.

    Args:
        raw_list: The full list of mutation dicts, as returned by
            dvf/client.py's fetch_mutations().

    Returns:
        A list of parsed Transactions, same order as input.
    """
    return [parse_transaction(raw) for raw in raw_list]
