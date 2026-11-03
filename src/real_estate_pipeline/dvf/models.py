"""
Data model for the real estate pipeline's DVF+ transactions.

Defines the structured representation of a single DVF+ "mutation"
(recorded property sale), as parsed from the Cerema DVF+ open-data API.
"""

from collections.abc import Iterable
from dataclasses import dataclass, fields
from datetime import date


@dataclass
class Transaction:
    """
    A single DVF+ mutation (recorded property sale transaction).

    Fields are populated by parser.py from the raw JSON returned by
    dvf/client.py. Named after their real-world meaning rather than
    the API's raw French field names — see parser.py for the mapping.
    """

    # --- Identity ---
    id_mutation: int  # DVF+'s own mutation ID, from idmutation

    # --- Sale details ---
    date: date  # Date of the sale, from datemut
    year: int  # Year of the sale, from anneemut
    price: float | None  # Sale price in euros, from valeurfonc; None when the API gives no value
    is_vefa: bool  # Off-plan sale (vente en l'état futur d'achèvement), from vefa
    nature_mutation_code: int  # Nature of mutation code, from idnatmut
    # (1=Vente, 2=VEFA, 3=Expropriation, 4=Vente terrain à bâtir, 5=Adjudication, 6=Echange)
    nature_mutation_label: str  # Human-readable nature of mutation, from libnatmut (e.g. "Vente")

    # --- Property details ---
    property_type_code: str  # DVF's property type code, from codtypbien
    property_type_label: (
        str  # Human-readable property type, from libtypbien (e.g. "UN APPARTEMENT")
    )
    surface_bati: float  # Built/floor surface in square meters, from sbati (0.0 for bare land)
    surface_terrain: float  # Land/plot surface in square meters, from sterr
    rooms: (
        int | None
    )  # Room count, only when mutation is a single residential unit; capped at 5 ("5+")

    # --- Location ---
    commune_code: str  # INSEE commune code, from l_codinsee[0] — the "primary" commune;
    # for boundary/multi-parcel mutations (nbcomm > 1) this may not
    # match the commune originally queried. See Notion: "Future:
    # investigate multi-commune / multi-parcel DVF mutations"


# The columns of the `transactions` table, defined once from the dataclass so
# they cannot drift from `Transaction`. The loader writes exactly these (the
# derived columns the cleaning step adds are deliberately not stored).
TRANSACTION_COLUMNS: tuple[str, ...] = tuple(field.name for field in fields(Transaction))

# The only columns the `transactions` table lets be NULL (migration 0004 made
# price nullable; rooms is only set for a single residential unit).
NULLABLE_TRANSACTION_COLUMNS: tuple[str, ...] = ("price", "rooms")


def require_transaction_columns(columns: Iterable[str], *, where: str) -> None:
    """Raise ValueError naming every Transaction column missing from `columns`.

    Same role as require_listing_columns on the ParuVendu side: a frame that
    lacks a column must be refused outright, not written with NULLs.
    """
    present = set(columns)
    missing = [column for column in TRANSACTION_COLUMNS if column not in present]
    if missing:
        raise ValueError(f"{where}: missing Transaction column(s): {', '.join(missing)}")
