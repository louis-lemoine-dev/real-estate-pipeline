"""
Fetch layer for the DVF+ open-data API (Cerema) — raw HTTP GET only,
no parsing into our own data model.
"""

import time

import requests

from real_estate_pipeline.common.robots import USER_AGENT

BASE_URL = "https://apidf-preprod.cerema.fr/dvf_opendata/mutations/"

# The Cerema API is still labeled beta/preprod and has been observed
# to hang transiently (see 2.1.3.2). Retry a few times with a short
# backoff before giving up on a single page request.
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2  # doubles each retry: 2s, 4s, 8s

# Courtesy delay between successive page requests during pagination.
RATE_LIMIT_SECONDS = 1.5


def _get_with_retry(url: str, params: dict | None = None) -> dict:
    """
    GET a URL and return its parsed JSON body, retrying on timeout or
    connection errors with exponential backoff.

    Args:
        url: The URL to request.
        params: Optional query parameters.

    Returns:
        The parsed JSON response body.

    Raises:
        requests.RequestException: if all retry attempts are exhausted.
    """
    headers = {"User-Agent": USER_AGENT}
    last_error: requests.RequestException | None = None

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as error:
            last_error = error
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF_SECONDS * (2**attempt)
                time.sleep(wait)

    raise last_error


def fetch_mutations(code_insee: str, max_pages: int = 10) -> list[dict]:
    """
    Fetch all DVF+ mutation records for a commune, paginating through
    the API's results. Returns raw dicts (one per mutation), unparsed —
    turning these into our own Transaction model is dvf/parser.py's job.

    Args:
        code_insee: The commune's INSEE code, e.g. "83137" for Toulon.
        max_pages: Safety cap on how many pages to follow via `next`,
            so a first run can't accidentally pull an entire commune's
            full history (tens of thousands of records) unbounded.

    Returns:
        A list of raw mutation dicts, combined across all fetched pages.
    """
    all_results: list[dict] = []
    url: str | None = BASE_URL
    params: dict | None = {"code_insee": code_insee}

    for page_number in range(1, max_pages + 1):
        data = _get_with_retry(url, params=params)
        all_results.extend(data.get("results", []))

        next_url = data.get("next")
        if not next_url:
            break

        # The API returns `next` as http://, not https:// — normalize
        # it rather than following it as-is.
        url = next_url.replace("http://", "https://", 1)
        params = None  # next_url already has all query params baked in

        if page_number < max_pages:
            time.sleep(RATE_LIMIT_SECONDS)

    return all_results
