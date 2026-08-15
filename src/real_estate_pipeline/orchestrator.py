"""
Orchestration layer for the ParuVendu.fr scraper.

Combines the fetch layer (scraper.py) and parse layer (parser.py) into
a single entry point: check permission once, fetch + parse each page
in sequence with a polite delay between requests, and return all
listings collected across the run.
"""

import logging
import time

from real_estate_pipeline.models import Listing
from real_estate_pipeline.parser import parse_page
from real_estate_pipeline.robots import is_scraping_allowed
from real_estate_pipeline.scraper import create_session, fetch_page

logger = logging.getLogger(__name__)

RATE_LIMIT_SECONDS = 1.5


def scrape_listings(base_url: str, max_pages: int) -> list[Listing]:
    """
    Scrape and parse listings from a ParuVendu.fr search across
    multiple pages.

    Checks robots.txt permission once, before any requests are made -
    not per page, since the answer doesn't change page to page within
    a single run. Raises PermissionError if scraping isn't allowed,
    rather than silently returning an empty list, since a robots.txt
    block is a distinct condition from "no listings found" and
    shouldn't be mistaken for one.

    A fixed delay is applied between page fetches to avoid hammering
    the server with rapid-fire requests.

    Listings can appear on more than one page (the same ID showing up
    identically across pages, likely due to the site's own pagination
    behavior) - duplicates are removed by ID, keeping the first
    occurrence seen.

    Args:
        base_url: The search page URL without any page parameter.
        max_pages: How many pages to fetch (1-indexed, e.g. 3 fetches
            pages 1, 2, and 3).

    Returns:
        All unique Listings parsed across every fetched page, combined
        into one list, with duplicate IDs removed.

    Raises:
        PermissionError: if robots.txt disallows scraping base_url.
    """
    if not is_scraping_allowed(base_url):
        raise PermissionError(f"Scraping not allowed by robots.txt: {base_url}")

    session = create_session()
    seen_ids: set[str] = set()
    all_listings: list[Listing] = []

    for page_number in range(1, max_pages + 1):
        html = fetch_page(session, base_url, page_number)
        listings = parse_page(html)

        new_listings = [listing for listing in listings if listing.id not in seen_ids]
        seen_ids.update(listing.id for listing in new_listings)
        all_listings.extend(new_listings)

        logger.info(
            "Page %d: parsed %d listings (%d new, %d duplicate)",
            page_number,
            len(listings),
            len(new_listings),
            len(listings) - len(new_listings),
        )

        if page_number < max_pages:
            time.sleep(RATE_LIMIT_SECONDS)

    return all_listings
