"""

    mtg.lib.scrape.wayback
    ~~~~~~~~~~~~~~~~~~~~~~
    Utilities for scraping Wayback Machine pages.

    @author: mazz3rr

"""
import logging

import backoff
from bs4 import BeautifulSoup
from wayback import WaybackClient
from wayback.exceptions import MementoPlaybackError, WaybackException, WaybackRetryError

from mtg.lib.time import timed


_log = logging.getLogger(__name__)
_SEARCH_RESULTS_LIMIT = 5


def _wayback_predicate(soup: BeautifulSoup | None) -> bool:
    if soup and "Error connecting to database" in str(soup):
        _log.warning(
            "Problems with connecting to Internet Archive's database. Re-trying with backoff...")
        return True
    return False


@timed("fetching wayback soup")
@backoff.on_predicate(
    backoff.expo,
    predicate=_wayback_predicate,
    jitter=None,
    max_tries=7
)
def fetch_wayback_soup(url: str) -> BeautifulSoup | None:
    """Fetch a BeautifulSoup object (or None) for a URL from Wayback Machine.
    """
    try:
        client = WaybackClient()
        _log.info(f"Searching for {url!r} in Wayback Machine...")
        results = client.search(url, limit=_SEARCH_RESULTS_LIMIT)
        for i, memento in enumerate(results, start=1):
            try:
                response = client.get_memento(memento, exact=False)
                return BeautifulSoup(response.text, "lxml")
            except MementoPlaybackError:
                continue  # try next one if this snapshot is broken
        _log.warning(f"Wayback Machine memento for {url!r} could not be retrieved")
        return None
    except (WaybackException, WaybackRetryError) as e:
        _log.warning(f"Wayback Machine failed with: {e!r}")
        return None
