"""
Scrape the ParuVendu listings (Toulon) and store them in the Supabase database.

Usage:
    poetry run python scripts/scrape_paruvendu.py --max-pages 5 --dry-run   # look, write nothing
    poetry run python scripts/scrape_paruvendu.py --max-pages 5             # real run

--max-pages is required on purpose: the site is scraped page by page, one page
every 1.5 seconds, and there is no "last page" detection, so you choose how
deep to go (1 to 200).

A dry run reads the site and the database but writes nothing, and reports what
a real run would do. A real run writes to the database. Re-running is safe:
listings already stored come back as "unchanged". If anything fails, the exit
code is 1 and the message says what happened.
"""

import argparse
import logging
import sys

from real_estate_pipeline.common.db import get_engine
from real_estate_pipeline.paruvendu.runner import (
    DEFAULT_BASE_URL,
    MAX_PAGES_LIMIT,
    format_report,
    run_scrape,
    validate_max_pages,
)


def main(arguments: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    parser = argparse.ArgumentParser(description="Scrape ParuVendu listings into Supabase.")
    parser.add_argument(
        "--max-pages",
        type=int,
        required=True,
        help=f"how many result pages to read (1 to {MAX_PAGES_LIMIT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read the site and the database, write nothing, and report",
    )
    parser.add_argument("--url", default=DEFAULT_BASE_URL, help="search URL (default: Toulon)")
    args = parser.parse_args(arguments)

    try:
        validate_max_pages(args.max_pages)
    except ValueError as error:
        print(error)
        return 1

    try:
        result = run_scrape(get_engine(), args.url, args.max_pages, dry_run=args.dry_run)
    except Exception as error:
        logging.getLogger(__name__).exception("Run failed")
        print()
        print(f"FAILED: {type(error).__name__}: {error}")
        print("Re-running is safe: listings already stored come back as unchanged.")
        return 1

    print()
    print(format_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
