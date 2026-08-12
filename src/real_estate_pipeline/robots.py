"""
Utilities for checking whether a given URL can be scraped, per robots.txt.
"""

import requests
from protego import Protego

# PAP.fr's robots.txt location
PAP_ROBOTS_URL = "https://www.pap.fr/robots.txt"

# Identify ourselves honestly on every request — some sites block requests
# with no User-Agent, or with generic default ones (like Python's own
# urllib default), and it's good etiquette to be identifiable as a script.
USER_AGENT = "real-estate-pipeline-bot/0.1 (personal learning project)"


def is_scraping_allowed(url: str) -> bool:
    """
    Check whether our scraper is allowed to fetch `url`, according to
    PAP.fr's robots.txt rules for our User-Agent.

    Uses Protego (the same robots.txt engine Scrapy uses internally)
    instead of the standard library's urllib.robotparser, which does not
    support wildcard (*) patterns in Disallow rules — a real limitation
    that caused it to silently ignore most of PAP.fr's actual rules.

    We fetch robots.txt ourselves via `requests` (with our own honest
    User-Agent) rather than any built-in fetcher, since PAP.fr's server
    rejects requests carrying generic default User-Agent strings.

    Returns True if allowed, False if disallowed.
    """
    response = requests.get(
        PAP_ROBOTS_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=10,
    )
    response.raise_for_status()

    parser = Protego.parse(response.text)

    return parser.can_fetch(url, USER_AGENT)
