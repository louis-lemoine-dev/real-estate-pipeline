"""
Load DVF+ transactions into the Supabase database, one commune at a time.

Usage:
    poetry run python scripts/load_dvf.py                  # all twelve communes
    poetry run python scripts/load_dvf.py 83103 83098      # only these communes

Safe to re-run: rows already stored and unchanged are left alone. A commune
that fails is reported and the run continues with the next one; the exit code
is 1 if any commune failed. A full load fetches about 1,260 pages and takes
45 to 90 minutes.
"""

import logging
import sys

from real_estate_pipeline.common.db import get_engine
from real_estate_pipeline.dvf.orchestrator import DEFAULT_COMMUNE_CODES
from real_estate_pipeline.dvf.runner import (
    format_report,
    has_failures,
    load_communes,
    validate_commune_codes,
)


def main(arguments: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    try:
        codes = validate_commune_codes(arguments or DEFAULT_COMMUNE_CODES)
    except ValueError as error:
        print(error)
        print("Usage: poetry run python scripts/load_dvf.py [commune_code ...]")
        return 1

    results = load_communes(codes, get_engine())

    print()
    print(format_report(results))
    return 1 if has_failures(results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
