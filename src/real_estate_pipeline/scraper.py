"""
Fetch layer for the ParuVendu.fr scraper — raw HTTP GET only, no parsing.
"""

import requests

from real_estate_pipeline.robots import USER_AGENT


def create_session() -> requests.Session:
    """
    Create a requests.Session for use across a single scraping run.

    Using a shared Session (rather than independent requests.get() calls)
    means cookies are preserved between requests — the same way a real
    browser behaves. Without this, ParuVendu's server treats every
    request as a brand-new visitor with no continuity, which was
    causing listing order to shift unpredictably between our page 1
    and page 2 fetches.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def fetch_page(session: requests.Session, base_url: str, page_number: int = 1) -> str:
    """
    Fetch the raw HTML of a ParuVendu.fr listings search page.

    ParuVendu paginates via a `p` query parameter (e.g. `?p=2`). Page 1
    is the base URL with no `p` param at all — that's what the site's
    own "page 1" link produces, so we mirror that rather than appending
    `?p=1` ourselves.

    Args:
        session: A requests.Session (see create_session()), shared
            across every page fetch in a single scraping run so
            cookies persist between requests.
        base_url: The search page URL without any page parameter,
            e.g. "https://www.paruvendu.fr/immobilier/vente/toulon/"
        page_number: Which page of results to fetch (1-indexed).

    Returns:
        The raw HTML of the page as a string.

    Raises:
        requests.HTTPError: if the request fails (bad status code).
    """
    url = base_url if page_number == 1 else f"{base_url}?p={page_number}"

    response = session.get(url, timeout=10)
    response.raise_for_status()

    return response.text
