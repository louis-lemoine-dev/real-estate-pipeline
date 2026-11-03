"""
Fetch layer for the DVF+ open-data API (Cerema) — raw HTTP GET only,
no parsing into our own data model.
"""

import logging
import math
import time

import requests

from real_estate_pipeline.common.robots import USER_AGENT

logger = logging.getLogger(__name__)

BASE_URL = "https://apidf-preprod.cerema.fr/dvf_opendata/mutations/"

# The Cerema API is still labeled beta/preprod and has been observed
# to hang transiently (see 2.1.3.2). Retry a few times with a short
# backoff before giving up on a single page request.
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2  # doubles each retry: 2s, 4s, 8s

# Courtesy delay between successive page requests during pagination.
RATE_LIMIT_SECONDS = 1.5

# Observed on the real API: the FIRST request for a large commune can take a minute or more,
# sometimes ending in an HTTP error after about 60 s and sometimes in a client-side timeout, and
# it can fail several times before it succeeds; once it has, later pages answer in about half a
# second. So the first request gets a long timeout and more attempts, while later pages keep the
# short timeout and the normal number of attempts (the occasional slow page is absorbed by those
# retries).
REQUEST_TIMEOUT_SECONDS = 15
FIRST_REQUEST_TIMEOUT_SECONDS = 65
FIRST_REQUEST_MAX_ATTEMPTS = 5

# When max_pages is not given, the page ceiling is the expected page count (from the API's own
# `count`) plus this margin. It only guards against a runaway pagination loop.
PAGE_CEILING_MARGIN = 2

# Log progress every this many pages, so a long fetch (Toulon is ~470 pages) is not silent.
PROGRESS_LOG_EVERY_PAGES = 10


class IncompleteFetchError(RuntimeError):
    """A commune's mutations could not be fetched completely."""


def _get_with_retry(
    url: str,
    params: dict | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    max_attempts: int = MAX_RETRIES,
) -> dict:
    """
    GET a URL and return its parsed JSON body, retrying on timeout or
    connection errors with exponential backoff.

    Args:
        url: The URL to request.
        params: Optional query parameters.
        timeout: Read timeout in seconds for each attempt.
        max_attempts: How many times to try before giving up.

    Returns:
        The parsed JSON response body.

    Raises:
        requests.RequestException: if all retry attempts are exhausted.
    """
    headers = {"User-Agent": USER_AGENT}
    last_error: requests.RequestException | None = None

    for attempt in range(max_attempts):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as error:
            last_error = error
            if attempt < max_attempts - 1:
                wait = RETRY_BACKOFF_SECONDS * (2**attempt)
                logger.warning(
                    "Request failed (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1,
                    max_attempts,
                    error,
                    wait,
                )
                time.sleep(wait)

    raise (
        last_error
        if last_error is not None
        else RuntimeError("Request failed with no captured exception — this shouldn't happen")
    )


def fetch_mutations(
    code_insee: str,
    max_pages: int | None = None,
    allow_partial: bool = False,
) -> list[dict]:
    """
    Fetch all DVF+ mutation records for a commune, paginating through
    the API's results. Returns raw dicts (one per mutation), unparsed —
    turning these into our own Transaction model is dvf/parser.py's job.

    Requests fields="all" so the response includes the per-bucket room
    count fields (nbapt1pp...nbmai5pp) needed to derive Transaction.rooms
    — the default (unspecified fields) response omits these entirely.

    The result is complete or the call fails. The API reports how many
    mutations the commune has (`count`); the walk raises
    IncompleteFetchError if it stops at the page ceiling with pages still
    pending, or ends with a different number of distinct mutations.
    Pagination order is not stable across calls, so a short result is a
    real possibility, not just a theoretical one.

    Args:
        code_insee: The commune's INSEE code, e.g. "83137" for Toulon.
        max_pages: Page ceiling. None (the default) derives it from the
            API's `count` plus a small margin, so a whole commune is always
            fetched. An explicit number is a hard ceiling: reaching it with
            pages still pending raises, unless allow_partial is set.
        allow_partial: Sampling mode, for scratch work and fixture capture.
            When the ceiling is reached, log a warning and return what was
            fetched instead of raising.

    Returns:
        A list of raw mutation dicts, combined across all fetched pages and
        deduplicated by idmutation.

    Raises:
        IncompleteFetchError: if the response has no `count`, the page
            ceiling is reached with pages pending (and allow_partial is
            False), or the distinct mutations fetched do not match `count`.
        requests.RequestException: if a page request exhausts its retries.
    """
    first_page = _get_with_retry(
        BASE_URL,
        params={"code_insee": code_insee, "fields": "all"},
        timeout=FIRST_REQUEST_TIMEOUT_SECONDS,
        max_attempts=FIRST_REQUEST_MAX_ATTEMPTS,
    )
    if "count" not in first_page:
        raise IncompleteFetchError(
            f"Commune {code_insee}: the response has no 'count' field, so completeness "
            "cannot be checked (has the API changed shape?)"
        )
    expected_count = int(first_page["count"])
    first_page_size = len(first_page.get("results", []))

    if max_pages is not None:
        page_limit = max_pages
    elif first_page_size:
        page_limit = math.ceil(expected_count / first_page_size) + PAGE_CEILING_MARGIN
    else:
        page_limit = PAGE_CEILING_MARGIN
    logger.info(
        "Commune %s: %d mutations expected (page limit %d)", code_insee, expected_count, page_limit
    )

    rows_by_id: dict[int, dict] = {}
    rows_seen = 0
    data = first_page
    page_number = 1

    while True:
        for row in data.get("results", []):
            rows_seen += 1
            rows_by_id.setdefault(row["idmutation"], row)

        next_url = data.get("next")
        if not next_url:
            break

        if page_number >= page_limit:
            message = (
                f"Commune {code_insee}: stopped at the page ceiling ({page_limit} pages) "
                f"with pages still pending; fetched {len(rows_by_id)} of {expected_count} "
                "mutations"
            )
            if allow_partial:
                logger.warning("%s (partial result allowed)", message)
                return list(rows_by_id.values())
            raise IncompleteFetchError(message)

        time.sleep(RATE_LIMIT_SECONDS)
        url = str(next_url).replace("http://", "https://", 1)
        data = _get_with_retry(url, params=None, timeout=REQUEST_TIMEOUT_SECONDS)
        page_number += 1

        if page_number % PROGRESS_LOG_EVERY_PAGES == 0:
            logger.info(
                "Commune %s: page %d, %d mutations so far", code_insee, page_number, len(rows_by_id)
            )

    rows = list(rows_by_id.values())
    if rows_seen != len(rows):
        logger.warning("Commune %s: %d duplicate rows dropped", code_insee, rows_seen - len(rows))
    if len(rows) != expected_count:
        raise IncompleteFetchError(
            f"Commune {code_insee}: the API reports {expected_count} mutations but "
            f"{len(rows)} distinct ones were fetched ({page_number} pages)"
        )
    return rows
