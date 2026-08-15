"""
Utilities for checking whether a given URL can be scraped, per robots.txt.
"""

from urllib.parse import urlparse

import requests
from protego import Protego

# Identify ourselves honestly on every request — some sites block requests
# with no User-Agent, or with generic default ones (like Python's own
# urllib default), and it's good etiquette to be identifiable as a script.
USER_AGENT = "real-estate-pipeline-bot/0.1 (personal learning project)"


def is_scraping_allowed(url: str) -> bool:
    """
    Check whether our scraper is allowed to fetch `url`, according to
    that site's own robots.txt rules for our User-Agent.

    The robots.txt location is derived from `url`'s own domain — every
    site's robots.txt lives at its domain root by convention (part of
    the Robots Exclusion Standard), so this works for any site we point
    it at, not just one hardcoded target.

    Uses Protego (the same robots.txt engine Scrapy uses internally)
    instead of the standard library's urllib.robotparser, which does not
    support wildcard (*) patterns in Disallow rules — a real limitation
    that caused it to silently ignore most of PAP.fr's actual rules.

    We fetch robots.txt ourselves via `requests` (with our own honest
    User-Agent) rather than any built-in fetcher, since some sites (like
    PAP.fr) reject requests carrying generic default User-Agent strings.

    Returns True if allowed, False if disallowed.
    """
    parsed_url = urlparse(url)
    robots_url = f"{parsed_url.scheme}://{parsed_url.netloc}/robots.txt"

    response = requests.get(
        robots_url,
        headers={"User-Agent": USER_AGENT},
        timeout=10,
    )
    response.raise_for_status()

    parser = Protego.parse(response.text)

    return parser.can_fetch(url, USER_AGENT)
