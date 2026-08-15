"""
Data model for the real estate pipeline's DVF+ transactions.

Defines the structured representation of a single DVF+ "mutation"
(recorded property sale), as parsed from the Cerema DVF+ open-data API.
"""

from dataclasses import dataclass
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
    price: float  # Sale price in euros, from valeurfonc
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
